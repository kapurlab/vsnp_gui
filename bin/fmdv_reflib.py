#!/usr/bin/env python3
"""The FMDV reference library: the 79 NanoFMDV lineage genomes as (1) a
hierarchy the picker shows, (2) a sourmash signature database the chooser
searches, and (3) the source for vsnp3 reference folders built on demand.

Nobody is shown 79 folders. A folder exists only once a lineage has been
chosen for some sample; at a single-serotype outbreak lab that is one to
three folders.

Layout of a library directory:
    lineages.json                 hierarchy FMDV > serotype > lineage > genome
    genomes/<safe>.fasta          one record per genome, header = <safe>
    signatures/lineages.sig       sourmash, k=21 scaled=10 abund, one per genome
    panels/vp1.fasta              the 79 VP1 sequences, header = <safe> (chooser vote)
    panels/genomes.fasta          the 79 genomes in one file, header = <safe>
    reference_options/<safe>/     vsnp3 reference folders, created lazily

Why k=21, scaled=10: vsnp3's shipped sourmash recipe (k=31, scaled=1000) is
built for 4 Mb bacteria and leaves an 8 kb virus with a handful of hashes.
Measured 14 November 2026 on SRR34371563: the shipped settings matched 3 kbp
with a tie; these settings matched 99.3% of the right genome with the next
candidate at 21.9%.

Usage:
    fmdv_reflib.py build --refs <Refs/fmdv.fasta> --lib <dir> [--vp1 <Refs/fmdv_vp1.fasta>]
    fmdv_reflib.py panels --lib <dir> --vp1 <Refs/fmdv_vp1.fasta>
    fmdv_reflib.py list  --lib <dir>
    fmdv_reflib.py materialize --lib <dir> --genome <safe>
    fmdv_reflib.py annotations --lib <dir> [--email you@example.org] [--force] [--no-proteins]
    fmdv_reflib.py add --lib <dir> --accession OQ557397 --serotype SAT2 [--lineage <topotype>]
    fmdv_reflib.py relabel --lib <dir> --accession OQ557397 --lineage XIV [--source <where from>]

Adding a genome: `add` puts one more complete genome into the library, for a
lineage NanoFMDV's 79 do not cover. Measured 22 November 2026 on 67 Ethiopian
SAT2 VP1 records: at 75 to 81% identity to every SAT2 genome in the panel
the chooser had no vote. 44 of them sit at 99% to OQ557397 (ETH/2/2022);
the 1991 records are 84% from it and take OQ557396 (ETH/2/91) instead. Both
are topotype XIV in the WRL sequence metadata, and the typing set holds no
XIV record, so `add` cannot name that topotype from the library alone: the
genome goes in unassigned, and `relabel` moves the label in once a reference
laboratory names it. The other 23 are topotype XIII (the 2009 Ethiopian
outbreak), for which no complete genome exists in GenBank or in the WRL set,
and those stay on the least distant library genome, flagged distant and
ambiguous. Leave --lineage out unless the topotype is established: the label
is what the picker shows. The
genome is fetched from GenBank (or read from --fasta), written to genomes/,
entered in lineages.json and panels/genomes.fasta, and its VP1 is cut and
appended to panels/vp1.fasta: from --vp1-fasta when given, else by exact
match to the typing set's record of the same accession
(vp1_context/_typing/all_vp1.fasta), else by blastn against the panel VP1s
of its serotype (BLAST+ on PATH). With --lineage, the typing set's own
copies of that accession are labelled too, for the reason `relabel` gives.
The GenBank annotation is fetched as for `annotations`. The sourmash signature database is not extended: the chooser
stopped using it for minimap2. A Step 2 context database for the new
reference is built as for any other (fmdv_panel_build.sh, then
fmdv_panel_register.py), and context databases built earlier for other
references do not contain the new genome until rebuilt.

Relabelling: `relabel` gives a genome entered as unassigned the topotype a
reference laboratory has since named, by accession. The safe name carries
the topotype, so the genome's FASTA, its annotation files, the panel
entries, the hierarchy the picker shows and the typing set's own copies of
that accession all move together. The typing report quotes the best hit's
label, and the typing set's slice of the same genome scores as well as the
panel entry, so a label left behind there would still report a sample
without a topotype. --source records where the topotype came from. The Step
2 context database is keyed by the safe name, so it has to be built and
registered again under the new one.

Annotation: `annotations` fetches each genome's GenBank record and GFF3
from NCBI once, into annotations/<safe>.gbk and .gff, and materialize
copies them into the reference folder. vsnp3 reads any .gbk in the folder
(vsnp3_reference_options.py) and keys records by their versioned
identifier, KM268899.1, while the FASTA header and every VCF use the bare
accession, so the VERSION line and the GFF's first column are rewritten to
the bare accession at fetch time. Without that rewrite the file is read and
never matches. Fetching at build time keeps a site offline afterwards.

An FMDV record carries one coding feature, the polyprotein, so a Step 2
table would name every call `unlisted gene:H984Q, polyprotein`. After
fetching, `annotations` hands the files to fmdv_proteins.py, which writes
the 12 mature proteins into each record as CDS features so a call reads
`2B:H23Q` instead. Pass --no-proteins to keep the records as fetched.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# NanoFMDV header convention: SAT1/I_KM268899_2012_TAN/22/2012  or, with no
# lineage, SAT1_MF678824_2015_NIG/2/15.
_HEADER = re.compile(r"^([A-Za-z0-9]+)(?:/([^_]+))?_(\w+?)_(\d{4})_(.+)$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _read_fasta(path: Path):
    name, seq = None, []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(seq)
                name, seq = line[1:].strip(), []
            else:
                seq.append(line.strip())
    if name is not None:
        yield name, "".join(seq)


def _write_fasta(path: Path, name: str, seq: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f">{name}\n")
        for i in range(0, len(seq), 80):
            fh.write(seq[i:i + 80] + "\n")


def parse_header(header: str) -> Dict[str, str]:
    m = _HEADER.match(header.split()[0])
    if m:
        sero, lin, acc, year, iso = m.groups()
    else:
        sero, lin, acc, year, iso = header.split("_")[0], None, "NA", "", header
    sero = sero.upper()
    lin = lin or ""
    safe = _UNSAFE.sub("-", f"{sero}-{lin or 'NA'}_{acc}").strip("-")
    return {"id": header, "serotype": sero, "lineage": lin, "accession": acc,
            "year": year, "isolate": iso, "safe": safe}


def load(lib: Path) -> Dict:
    return json.loads((lib / "lineages.json").read_text())


def genomes_in_scope(lib_index: Dict, scope: str) -> List[Dict]:
    """Scope tokens: 'FMDV' (everything), '<SEROTYPE>', '<SEROTYPE>/<lineage>',
    or 'genome:<safe>'. Picking a node above a genome means 'closest within
    this node', so a serotype scope is safe where a fixed genome is not."""
    g = lib_index["genomes"]
    s = (scope or "FMDV").strip()
    if s.upper() == "FMDV":
        return g
    if s.startswith("genome:"):
        return [x for x in g if x["safe"] == s[len("genome:"):]]
    if "/" in s:
        sero, lin = s.split("/", 1)
        return [x for x in g if x["serotype"] == sero.upper() and x["lineage"] == lin]
    return [x for x in g if x["serotype"] == s.upper()]


def materialize(lib: Path, safe: str) -> Path:
    """Create reference_options/<safe>/ in vsnp3's format if absent: the
    FASTA (header = accession), a minimal define_filter.xlsx (sheet 'all',
    A2 'name-All': one catch-all group, no positions filtered), an empty
    remove_from_analysis.xlsx and, when `annotations` has been run, the
    GenBank and GFF files vsnp3 and IGV annotate from. Idempotent, and a
    folder made before the annotations existed gains them on the next
    call."""
    idx = load(lib)
    rec = next((x for x in idx["genomes"] if x["safe"] == safe), None)
    if rec is None:
        raise SystemExit(f"unknown genome {safe!r}")
    folder = lib / "reference_options" / safe
    acc = rec["accession"]
    # File stems are the SAFE name, not the accession. vsnp3 names each
    # sample's alignment dir after the FASTA stem and the stats sheet records
    # the -t folder's basename, so with one token everywhere
    # (alignment_SAT1-I_KM268899, Reference "SAT1-I_KM268899", project
    # reference "SAT1-I_KM268899") vsnp_gui's Build filter and Step 2 veto
    # match exactly. The sequence header stays the accession, so the VCF
    # CHROM and IGV show KM268899.
    fasta = folder / f"{safe}.fasta"
    if fasta.is_file() and (folder / f"{safe}_define_filter.xlsx").is_file():
        _copy_annotations(lib, safe, folder)
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    name, seq = next(_read_fasta(lib / "genomes" / f"{safe}.fasta"))
    _write_fasta(fasta, acc, seq)
    import openpyxl  # in the vsnp3 env
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "all"; ws["A2"] = "name-All"
    wb.save(folder / f"{safe}_define_filter.xlsx")
    wb2 = openpyxl.Workbook(); wb2.active.title = "Sheet1"
    wb2.save(folder / f"{safe}_remove_from_analysis.xlsx")
    (folder / "SOURCE.txt").write_text(
        f"{rec['id']}\n{rec.get('source') or 'from NanoFMDV Refs/fmdv.fasta'}; "
        "folder generated by fmdv_reflib.py\n")
    _copy_annotations(lib, safe, folder)
    return folder


def _copy_annotations(lib: Path, safe: str, folder: Path) -> None:
    """Put annotations/<safe>.gbk and .gff beside the FASTA when they exist
    and the folder lacks them. The stems match the FASTA stem, which is what
    vsnp_gui's GFF-for-FASTA lookup scores highest."""
    for ext in (".gbk", ".gff"):
        src = lib / "annotations" / f"{safe}{ext}"
        dst = folder / f"{safe}{ext}"
        if src.is_file() and not dst.is_file():
            shutil.copyfile(src, dst)


def _bare_gbk(text: str, acc: str) -> str:
    """VERSION KM268899.1 -> VERSION KM268899, so Biopython's record.id, which
    vsnp3 keys annotation by, equals the FASTA header and the VCF CHROM."""
    return re.sub(r"^(VERSION\s+)\S+", lambda m: m.group(1) + acc, text, count=1, flags=re.M)


def _bare_gff(text: str, acc: str) -> str:
    """First column KM268899.1 -> KM268899, so igv.js draws the track on the
    FASTA's sequence name."""
    out = []
    for line in text.splitlines():
        if line and not line.startswith("#"):
            cols = line.split("\t", 1)
            if len(cols) == 2 and cols[0].split(".")[0] == acc:
                line = acc + "\t" + cols[1]
        out.append(line)
    return "\n".join(out) + "\n"


def _efetch(acc: str, rettype: str, email: str) -> str:
    q = {"db": "nuccore", "id": acc, "rettype": rettype, "retmode": "text", "tool": "fmdv_gui"}
    if email:
        q["email"] = email
    req = urllib.request.Request(EUTILS + "?" + urllib.parse.urlencode(q),
                                 headers={"User-Agent": "fmdv_gui reference library"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def fetch_annotations(lib: Path, email: str = "", force: bool = False, delay: float = 0.4,
                      proteins: bool = True) -> Dict[str, int]:
    """Fetch every library genome's GenBank record (gbwithparts) and GFF3
    into lib/annotations/, identifiers rewritten to the bare accession, and
    copy them into any reference folder already materialized. NCBI asks for
    a contact address on scripted requests: --email or NCBI_EMAIL."""
    idx = load(lib)
    out = lib / "annotations"
    out.mkdir(exist_ok=True)
    email = email or os.environ.get("NCBI_EMAIL", "").strip()
    counts = {"fetched": 0, "present": 0, "failed": 0, "no_cds": 0}
    for rec in idx["genomes"]:
        safe, acc = rec["safe"], rec["accession"]
        gbk, gff = out / f"{safe}.gbk", out / f"{safe}.gff"
        if gbk.is_file() and gff.is_file() and not force:
            counts["present"] += 1
        else:
            try:
                g = _efetch(acc, "gbwithparts", email)
                time.sleep(delay)
                f = _efetch(acc, "gff3", email)
                time.sleep(delay)
            except Exception as e:  # noqa: BLE001 - report and carry on
                print(f"annotations: {safe} ({acc}) failed: {e}", file=sys.stderr)
                counts["failed"] += 1
                continue
            if not g.startswith("LOCUS"):
                print(f"annotations: {safe} ({acc}) no GenBank record returned", file=sys.stderr)
                counts["failed"] += 1
                continue
            gbk.write_text(_bare_gbk(g, acc))
            gff.write_text(_bare_gff(f, acc))
            counts["fetched"] += 1
        if "     CDS             " not in gbk.read_text():
            counts["no_cds"] += 1
        folder = lib / "reference_options" / safe
        if folder.is_dir():
            _copy_annotations(lib, safe, folder)
    (out / "SOURCE.txt").write_text(
        "GenBank records (rettype gbwithparts) and GFF3 from NCBI E-utilities for the library accessions, "
        "fetched by fmdv_reflib.py annotations. VERSION lines and GFF seqids are rewritten to the bare "
        "accession to match the FASTA headers and VCF CHROM values.\n"
        f"fetched {time.strftime('%Y-%m-%d')}\n")
    print(f"annotations: {counts}", file=sys.stderr)
    if proteins:
        # The mature proteins go in after every record is on disk, because a
        # genome with no mat_peptide features of its own takes its boundaries
        # from one that has them. The step keeps the fetched file under
        # annotations/raw/ and copies the rewritten pair into every reference
        # folder, so it corrects a stale copy the loop above left behind.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import fmdv_proteins
        fmdv_proteins.annotate(lib, force=force)
    return counts


def build_panels(lib: Path, vp1_src: Optional[Path]) -> None:
    """Write panels/genomes.fasta (every library genome, header = safe) and,
    given NanoFMDV's Refs/fmdv_vp1.fasta, panels/vp1.fasta with the same safe
    names. The chooser votes on VP1 (the serotype- and topotype-determining
    region) and reads composition off the genome panel; see
    fmdv_choose_reference.py."""
    idx = load(lib)
    panels = lib / "panels"
    panels.mkdir(exist_ok=True)
    with open(panels / "genomes.fasta", "w", encoding="utf-8") as out:
        for rec in idx["genomes"]:
            name, seq = next(_read_fasta(lib / "genomes" / f"{rec['safe']}.fasta"))
            out.write(f">{rec['safe']}\n{seq}\n")
    if vp1_src is None:
        return
    known = {r["safe"] for r in idx["genomes"]}
    n = 0
    with open(panels / "vp1.fasta", "w", encoding="utf-8") as out:
        for header, seq in _read_fasta(vp1_src):
            safe = parse_header(header)["safe"]
            if safe not in known:
                print(f"vp1 panel: {header} has no genome in the library, skipped", file=sys.stderr)
                continue
            out.write(f">{safe}\n{seq}\n"); n += 1
    print(f"panels written: {len(idx['genomes'])} genomes, {n} VP1 sequences")


def build(refs: Path, lib: Path, vp1: Optional[Path] = None) -> None:
    sourmash = shutil.which("sourmash")
    if not sourmash:
        raise SystemExit("sourmash not on PATH (it is in the vsnp3 env)")
    (lib / "genomes").mkdir(parents=True, exist_ok=True)
    (lib / "signatures").mkdir(exist_ok=True)
    (lib / "reference_options").mkdir(exist_ok=True)

    genomes: List[Dict] = []
    combined = lib / "signatures" / "_all_genomes.fasta"
    with open(combined, "w", encoding="utf-8") as out:
        for header, seq in _read_fasta(refs):
            rec = parse_header(header)
            rec["length"] = len(seq)
            rec["n_count"] = seq.upper().count("N")
            genomes.append(rec)
            _write_fasta(lib / "genomes" / f"{rec['safe']}.fasta", rec["safe"], seq)
            out.write(f">{rec['safe']}\n{seq}\n")

    tree: Dict[str, Dict[str, List[str]]] = {}
    for r in genomes:
        tree.setdefault(r["serotype"], {}).setdefault(r["lineage"] or "(unassigned)", []).append(r["safe"])
    index = {"source": str(refs), "sketch": "k=21,scaled=10,abund",
             "n_genomes": len(genomes), "genomes": genomes, "tree": tree}
    (lib / "lineages.json").write_text(json.dumps(index, indent=1))

    sig = lib / "signatures" / "lineages.sig"
    subprocess.run([sourmash, "sketch", "dna", "-p", "k=21,scaled=10,abund",
                    "--singleton", "-o", str(sig), str(combined)],
                   check=True, capture_output=True, text=True)
    combined.unlink()
    print(f"library built at {lib}: {len(genomes)} genomes, "
          f"{len(tree)} serotypes, signatures in {sig.name}")
    build_panels(lib, vp1)


VP1_LENGTH = (600, 700)   # nt; FMDV VP1 is 630 to 660


def _gb_field(gb: str, qualifier: str) -> str:
    m = re.search(r'/' + qualifier + r'="([^"]*)"', gb)
    return m.group(1).strip() if m else ""


def _vp1_from_typing_set(lib: Path, acc: str, genome: str) -> Optional[Tuple[str, str]]:
    """The typing set's VP1 of this accession, when it is a substring of the
    genome (it was sliced from this very record): (sequence, how)."""
    typing = lib / "vp1_context" / "_typing" / "all_vp1.fasta"
    if not typing.is_file():
        return None
    for header, seq in _read_fasta(typing):
        head_acc = header.split()[0].rsplit("_", 1)[-1].split(".")[0]
        if head_acc != acc:
            continue
        if seq.upper() in genome.upper():
            return seq.upper(), f"typing set record {header.split()[0]}"
    return None


def _vp1_by_blast(lib: Path, serotype: str, genome: str) -> Optional[Tuple[str, str]]:
    """VP1 cut from the genome by its best blastn hit to a panel VP1 of the
    same serotype (every panel VP1 when the serotype has none), the hit
    extended by the template's unaligned ends, as fmdv_vp1_context slices
    long records. None without BLAST+ or without a hit of VP1 length."""
    blastn = shutil.which("blastn")
    if not blastn or not Path(blastn).with_name("makeblastdb").exists():
        return None
    panel = lib / "panels" / "vp1.fasta"
    if not panel.is_file():
        return None
    templates = [(h, q) for h, q in _read_fasta(panel) if h.split("-")[0] == serotype.upper()]
    templates = templates or list(_read_fasta(panel))
    import fmdv_vp1_context  # noqa: E402  (beside this file)
    best = fmdv_vp1_context.blast_best_hits(blastn, [("g", genome)], templates, 4, "add")
    f = (best or {}).get("g")
    if f is None:
        return None
    qstart, qend, sstart, send, qlen, slen = int(f[4]), int(f[5]), int(f[6]), int(f[7]), int(f[8]), int(f[9])
    if sstart <= send:
        start, end = qstart - 1 - (sstart - 1), qend + (slen - send)
    else:
        return None  # a reverse-strand genome record; submit it forward
    start, end = max(0, start), min(qlen, end)
    piece = genome[start:end].upper()
    if not (VP1_LENGTH[0] <= len(piece) <= VP1_LENGTH[1]):
        return None
    return piece, f"blastn to panel VP1 {f[1]} at {float(f[2]):.1f}% identity, {start + 1}-{end}"


def add_genome(lib: Path, accession: str, serotype: str, lineage: str, isolate: str = "",
               year: str = "", fasta: Optional[Path] = None, vp1_fasta: Optional[Path] = None,
               email: str = "", annotations: bool = True) -> Dict:
    """Add one complete genome to the library (see the module docstring)."""
    idx = load(lib)
    acc = accession.split(".")[0].strip()
    serotype = serotype.upper().strip()
    lineage = lineage.strip()
    safe = _safe(serotype, lineage, acc)
    if any(g["safe"] == safe or g["accession"] == acc for g in idx["genomes"]):
        raise SystemExit(f"{acc} is already in the library")

    email = email or os.environ.get("NCBI_EMAIL", "").strip()
    if fasta is not None:
        header, seq = next(_read_fasta(fasta))
    else:
        text = _efetch(acc, "fasta", email)
        time.sleep(0.4)  # NCBI allows three requests a second; the record and its annotation follow
        if not text.startswith(">"):
            raise SystemExit(f"GenBank returned no FASTA for {acc}")
        header, seq = _split_fasta_text(text)
    seq = seq.upper()
    if len(seq) < 7000:
        raise SystemExit(f"{acc} is {len(seq)} nt; a library genome must be a complete genome (7,000 nt or more)")
    if not (isolate and year):
        try:
            gb = _efetch(acc, "gb", email)
            time.sleep(0.4)
        except Exception:  # noqa: BLE001 - metadata only
            gb = ""
        isolate = isolate or _gb_field(gb, "isolate") or _gb_field(gb, "strain") or acc
        m = re.search(r"\b(\d{4})\b", _gb_field(gb, "collection_date"))
        year = year or (m.group(1) if m else "")

    if vp1_fasta is not None:
        vp1 = (next(_read_fasta(vp1_fasta))[1].upper(), f"{vp1_fasta}")
    else:
        vp1 = _vp1_from_typing_set(lib, acc, seq) or _vp1_by_blast(lib, serotype, seq)
    if vp1 is None:
        raise SystemExit(f"could not locate VP1 in {acc}: not in the typing set, and no blastn hit of VP1 length "
                         f"(is BLAST+ on PATH?). Pass --vp1-fasta with the VP1 sequence.")
    if not (VP1_LENGTH[0] <= len(vp1[0]) <= VP1_LENGTH[1]):
        raise SystemExit(f"the VP1 for {acc} is {len(vp1[0])} nt, outside {VP1_LENGTH[0]}-{VP1_LENGTH[1]}")

    rec = {"id": f"{serotype}{'/' + lineage if lineage else ''}_{acc}_{year}_{isolate}",
           "serotype": serotype, "lineage": lineage, "accession": acc, "year": year,
           "isolate": isolate, "safe": safe, "length": len(seq), "n_count": seq.count("N"),
           "source": f"GenBank {acc}, added {time.strftime('%Y-%m-%d')} by fmdv_reflib.py add",
           "vp1_source": vp1[1]}
    _write_fasta(lib / "genomes" / f"{safe}.fasta", safe, seq)
    idx["genomes"].append(rec)
    idx["n_genomes"] = len(idx["genomes"])
    idx["tree"].setdefault(serotype, {}).setdefault(lineage or "(unassigned)", []).append(safe)
    (lib / "lineages.json").write_text(json.dumps(idx, indent=1))
    build_panels(lib, None)
    with open(lib / "panels" / "vp1.fasta", "a", encoding="utf-8") as out:
        out.write(f">{safe}\n{vp1[0]}\n")
    print(f"added {safe}: {len(seq)} nt, VP1 {len(vp1[0])} nt from {vp1[1]}", file=sys.stderr)
    if lineage:
        # The typing set's own slice of this genome carries no topotype, and the
        # typing report quotes the best hit's label, so a sample of this lineage
        # would come back without one although the library now names it.
        moved = _relabel_typing_set(lib, serotype, acc, lineage)
        if moved:
            print(f"typing set: {moved} record(s) of {acc} labelled {lineage}", file=sys.stderr)
    if annotations:
        fetch_annotations(lib, email)
    print(f"next: fmdv_panel_build.sh {safe} && fmdv_panel_register.py {safe}  (Step 2 context database)",
          file=sys.stderr)
    return rec


def _safe(serotype: str, lineage: str, acc_token: str) -> str:
    return _UNSAFE.sub("-", f"{serotype}-{lineage or 'NA'}_{acc_token}").strip("-")


def _relabel_typing_set(lib: Path, serotype: str, acc: str, lineage: str) -> int:
    """Give the typing set's own copies of `acc` the same topotype.

    The context sets come from NanoFMDV's GenBank collection, whose headers
    carry no topotype for a record its curators had not placed, so the VP1
    slice taken from this genome sits there as <SERO>-NA_<acc>. The typing
    report quotes the best hit's label, and that copy scores the same as the
    panel entry on an identical sequence, so the label has to move in both
    places or a typed sample still comes back without a topotype."""
    ctx = lib / "vp1_context"
    changed = 0
    fasta = ctx / f"{serotype}.fasta"
    if fasta.is_file():
        lines = fasta.read_text(encoding="utf-8").splitlines(True)
        for i, line in enumerate(lines):
            if not line.startswith(">"):
                continue
            token = line[1:].split()[0].rsplit("_", 1)[-1]
            if token.split(".")[0] == acc:
                lines[i] = f">{_safe(serotype, lineage, token)}\n"
                changed += 1
        fasta.write_text("".join(lines), encoding="utf-8")
    tsv = ctx / f"{serotype}.tsv"
    if tsv.is_file():
        rows = tsv.read_text(encoding="utf-8").splitlines(True)
        head = rows[0].rstrip("\n").split("\t") if rows else []
        for i, row in enumerate(rows[1:], start=1):
            cells = row.rstrip("\n").split("\t")
            if len(cells) < len(head) or cells[head.index("accession")].split(".")[0] != acc:
                continue
            token = cells[0].rsplit("_", 1)[-1]
            cells[0] = _safe(serotype, lineage, token)
            cells[head.index("topotype")] = lineage
            rows[i] = "\t".join(cells) + "\n"
        tsv.write_text("".join(rows), encoding="utf-8")
    return changed


def relabel_genome(lib: Path, accession: str, lineage: str, source: str = "") -> Dict:
    """Give a library genome the topotype it was entered without.

    `add` lists a genome as unassigned when no annotated VP1 in the typing
    set is near enough to name its topotype, which is the honest state for a
    lineage the typing set does not hold. Once a reference laboratory names
    it, this moves the label in: the safe name carries the topotype, so the
    genome's files, its panel entries, the typing set's copies of the same
    accession and the hierarchy the picker shows all follow.

    The Step 2 context database is keyed by the safe name, so an existing one
    belongs to the old name and has to be built and registered again; the
    caller is told so. Nothing is inferred: `source` is recorded on the
    record as where the topotype came from."""
    idx = load(lib)
    acc = accession.split(".")[0].strip()
    lineage = lineage.strip()
    rec = next((g for g in idx["genomes"] if g["accession"].split(".")[0] == acc), None)
    if rec is None:
        raise SystemExit(f"{acc} is not in the library")
    sero, old = rec["serotype"], rec["safe"]
    new = _safe(sero, lineage, acc)
    if new == old:
        raise SystemExit(f"{acc} is already {rec['lineage'] or 'unassigned'}")
    if any(g["safe"] == new for g in idx["genomes"]):
        raise SystemExit(f"{new} is already in the library")

    genome = lib / "genomes" / f"{old}.fasta"
    if genome.is_file():
        name, seq = next(_read_fasta(genome))
        _write_fasta(lib / "genomes" / f"{new}.fasta", new, seq)
        genome.unlink()
    for ext in (".gbk", ".gff"):
        src = lib / "annotations" / f"{old}{ext}"
        if src.is_file():
            src.rename(lib / "annotations" / f"{new}{ext}")
    coords = lib / "annotations" / "protein_coords.json"
    if coords.is_file():
        table = json.loads(coords.read_text())
        if old in table:
            table[new] = table.pop(old)
            coords.write_text(json.dumps(table, indent=1))
    folder = lib / "reference_options" / old
    had_folder = folder.is_dir()
    if had_folder:
        shutil.rmtree(folder)           # materialize rebuilds it under the new name

    old_lineage = rec["lineage"]
    rec["lineage"] = lineage
    rec["safe"] = new
    rec["id"] = f"{sero}{'/' + lineage if lineage else ''}_{acc}_{rec.get('year', '')}_{rec.get('isolate', '')}"
    note = f"topotype {lineage} assigned {time.strftime('%Y-%m-%d')} by fmdv_reflib.py relabel"
    rec["source"] = f"{rec.get('source', '')}; {note}{f' ({source})' if source else ''}".lstrip("; ")
    tree = idx["tree"].setdefault(sero, {})
    bucket = tree.get(old_lineage or "(unassigned)", [])
    if old in bucket:
        bucket.remove(old)
    if not bucket:
        tree.pop(old_lineage or "(unassigned)", None)
    tree.setdefault(lineage or "(unassigned)", []).append(new)
    (lib / "lineages.json").write_text(json.dumps(idx, indent=1))

    build_panels(lib, None)
    panel = lib / "panels" / "vp1.fasta"
    if panel.is_file():
        text = panel.read_text(encoding="utf-8")
        panel.write_text(text.replace(f">{old}\n", f">{new}\n"), encoding="utf-8")
    moved = _relabel_typing_set(lib, sero, acc, lineage)
    if had_folder:
        materialize(lib, new)
    print(f"relabelled {old} -> {new} ({moved} typing-set record(s) relabelled)", file=sys.stderr)
    print(f"next: fmdv_panel_build.sh {new} && fmdv_panel_register.py {new}  (Step 2 context database); "
          f"the entry registered for {old} is stale", file=sys.stderr)
    return rec

def _split_fasta_text(text: str) -> Tuple[str, str]:
    lines = text.strip().splitlines()
    return lines[0][1:].strip(), "".join(l.strip() for l in lines[1:] if not l.startswith(">"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--refs", required=True); b.add_argument("--lib", required=True)
    b.add_argument("--vp1", help="NanoFMDV Refs/fmdv_vp1.fasta, for the chooser's VP1 panel")
    pn = sub.add_parser("panels"); pn.add_argument("--lib", required=True); pn.add_argument("--vp1", required=True)
    l = sub.add_parser("list"); l.add_argument("--lib", required=True)
    m = sub.add_parser("materialize"); m.add_argument("--lib", required=True); m.add_argument("--genome", required=True)
    an = sub.add_parser("annotations"); an.add_argument("--lib", required=True)
    an.add_argument("--email", default="", help="contact address for NCBI (default: NCBI_EMAIL)")
    an.add_argument("--force", action="store_true", help="fetch again even when present")
    an.add_argument("--no-proteins", action="store_true",
                    help="leave the records as fetched, one polyprotein CDS")
    ad = sub.add_parser("add", help="add one complete genome from GenBank (see the module docstring)")
    ad.add_argument("--lib", required=True)
    ad.add_argument("--accession", required=True, help="GenBank accession of a complete genome, e.g. OQ557397")
    ad.add_argument("--serotype", required=True, help="O, A, C, Asia1, SAT1, SAT2, SAT3")
    ad.add_argument("--lineage", default="", help="topotype/lineage as the picker should show it, e.g. EA-3; "
                    "leave out when not established (listed as unassigned)")
    ad.add_argument("--isolate", default="", help="isolate name (default: from the GenBank record)")
    ad.add_argument("--year", default="", help="collection year (default: from the GenBank record)")
    ad.add_argument("--fasta", help="read the genome from this FASTA instead of fetching it")
    ad.add_argument("--vp1-fasta", help="the genome's VP1 sequence, when it cannot be located automatically")
    ad.add_argument("--email", default="", help="contact address for NCBI (default: NCBI_EMAIL)")
    ad.add_argument("--no-annotations", action="store_true", help="skip fetching the GenBank annotation")
    rl = sub.add_parser("relabel", help="give a genome entered as unassigned its topotype")
    rl.add_argument("--lib", required=True)
    rl.add_argument("--accession", required=True, help="GenBank accession of a genome already in the library")
    rl.add_argument("--lineage", required=True, help="topotype/lineage as the picker should show it, e.g. XIV")
    rl.add_argument("--source", default="", help="where the topotype comes from, recorded on the record, "
                    "e.g. 'WRL Pirbright sequence metadata'")
    a = ap.parse_args()
    lib = Path(a.lib).expanduser().resolve()
    if a.cmd == "add":
        add_genome(lib, a.accession, a.serotype, a.lineage, a.isolate, a.year,
                   Path(a.fasta).expanduser().resolve() if a.fasta else None,
                   Path(a.vp1_fasta).expanduser().resolve() if a.vp1_fasta else None,
                   a.email, annotations=not a.no_annotations)
    elif a.cmd == "relabel":
        relabel_genome(lib, a.accession, a.lineage, a.source)
    elif a.cmd == "build":
        build(Path(a.refs).expanduser().resolve(), lib,
              Path(a.vp1).expanduser().resolve() if a.vp1 else None)
    elif a.cmd == "panels":
        build_panels(lib, Path(a.vp1).expanduser().resolve())
    elif a.cmd == "annotations":
        fetch_annotations(lib, a.email, a.force, proteins=not a.no_proteins)
    elif a.cmd == "list":
        idx = load(lib)
        for sero in sorted(idx["tree"]):
            print(sero)
            for lin, safes in sorted(idx["tree"][sero].items()):
                print(f"  {lin:<22} {', '.join(safes)}")
    else:
        print(materialize(lib, a.genome))
    return 0


if __name__ == "__main__":
    sys.exit(main())
