"""Import sequences that are not reads: a FASTA or GenBank record as a sample.

Two inputs the reads path does not cover: a FASTA somebody already has (a
consensus genome from another lab or pipeline, an assembly, a published
genome) and GenBank accessions.

IN ANY PROJECT a record long enough to make reads from becomes simulated
paired reads in <project>/download/<name>_R1/_R2.fastq.gz and a sample folder
step1/<name>/, and goes through Grab and Step 1 like any sample: alignment,
VCF, and Step 2 beside the project's own samples. That is how a published
genome joins a comparison. The reads are perfect and uniform, skip every run
of N (vsnp3's own simulator turns N into random bases, which a masked
consensus must not do), are made at `depth` fold (100 by default, the caller's
to change), and are marked simulated in every read header and in
step1/<name>/imported_sequence.json, so the Results table badges the row and
QC does not apply to it.

IN AN FMDV PROJECT the same import also types the record, and a record plays
one of two roles (docs/FMDV_SEQ_IMPORT.md):

  sample   a GENOME (7,000 nt or more) becomes simulated paired reads in
           <project>/download/<name>_R1/_R2.fastq.gz and goes through Grab and
           Step 1 like any sample: chooser, alignment, VCF, consensus, typing,
           report, whole-genome Step 2. The reads are perfect and uniform, skip
           every run of N (vsnp3's own simulator turns N into random bases,
           which the FMDV doc forbids for a masked consensus), and are marked
           simulated in every header, plus the typing and the VP1 tree.
           A shorter record (VP1, P1, a fragment) has nothing for the
           whole-genome workflow and lands sequence-only in step1/<name>/ with
           <name>_imported.fasta, <name>_VP1.fasta, imported_sequence.json and
           fmdv_consensus.json (what the tree reads): no reads, no alignment,
           Grab and the batch skip the folder, the typer and the tree take it.
  context  lands in <project>/fmdv_context/<SEROTYPE>.fasta and .tsv, named
           and laid out like the library's vp1_context sets, so the tree, the
           tables and the typer read it beside them. Never pruned from a tree.

VP1 is found the way fmdv_vp1_context.py finds it for the collection: a
VP1-length record (600 to 700 nt) is checked by blastn against the 79 panel
VP1s; a longer record has VP1 cut out by its best blastn hit to the checked
VP1 set of its serotype, extended to the template's ends. The serotype of an
unlabelled record is the serotype of its best panel hit.

Accessions are fetched with the same plain HTTPS efetch fmdv_reflib.py uses,
in batches, cached under <lib>/genbank_cache/<accession>.gb before anything
else happens. When NCBI cannot be reached the answer is a clear error per
accession, never a queue. A GenBank flat file downloaded elsewhere is parsed
the same way, so a server with no link imports the file instead.

Two phases, as in ont_import: ``preview`` parses, finds VP1 and proposes one
editable row per record, stashing the sequences under <project>/.seq_import/;
``run_commit`` (``python -m app.seq_import commit <plan.json>``) writes the
files and types the samples, as a background job.
"""

from __future__ import annotations

import collections
import csv
import gzip
import hashlib
import io
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

_BIN = Path(__file__).resolve().parent.parent.parent / "bin"
if str(_BIN) not in sys.path:
    sys.path.insert(0, str(_BIN))
import fmdv_vp1_context as ctx  # noqa: E402  (stdlib only; the shared VP1 check and slice)

from app.ont_import import safe_sample_name  # noqa: E402

VP1_MIN, VP1_MAX = ctx.VP1_MIN, ctx.VP1_MAX
MIN_RECORD = 600            # under this a record is refused: partial VP1 makes gappy alignments
MIN_VP1_COVERAGE = ctx.MIN_VP1_COVERAGE
# The per-sample record of an import, in step1/<name>/. Written under the
# neutral name since v0.4.155, when importing stopped being FMDV-only; the
# legacy name is still read, so projects imported on v0.4.154 keep working.
IMPORT_JSON = "imported_sequence.json"
LEGACY_IMPORT_JSON = "fmdv_import.json"
# The simulated reads made from an imported sequence. 100-fold is the default
# depth; a caller may ask for another (MIN_DEPTH..MAX_DEPTH). Perfect reads at
# any depth are still perfect: depth changes how much evidence each position
# carries, not whether it is right, so it matters only where a tool has a
# minimum-depth rule (vsnp3 calls a position N under 10).
READ_LEN = 250
FRAGMENT = 400
SIM_DEPTH = 100
MIN_DEPTH, MAX_DEPTH = 1, 1000
COMPLEMENT = str.maketrans("ACGT", "TGCA")


def clamp_depth(value, default: int = SIM_DEPTH) -> int:
    """A depth a caller asked for, held to MIN_DEPTH..MAX_DEPTH. Anything that
    is not a number is the default: a bad box in the browser must not decide
    how much evidence a sample appears to carry."""
    try:
        d = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(MIN_DEPTH, min(MAX_DEPTH, d))
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
EUTILS_HOST = "eutils.ncbi.nlm.nih.gov"
FETCH_BATCH = 50            # efetch takes up to 200 ids; 50 keeps a batch small on a slow link
FETCH_DELAY = 0.4           # seconds between batches (NCBI asks for 3 requests a second without a key)
FETCH_TIMEOUT = 60
FETCH_RETRIES = 3
ACCESSION_RE = re.compile(r"^[A-Z]{1,2}_?\d{5,8}(\.\d+)?$")
META_FIELDS = ("serotype", "topotype", "country", "year", "host", "accession", "isolate")
# The TSV the library's context sets carry, plus where the record came from.
TSV_COLUMNS = list(ctx.TSV_COLUMNS) + ["source"]
SIDECAR_COLUMNS = ("sample", "role", "serotype", "lineage", "topotype", "country", "year", "host", "accession", "isolate")
SIDECAR_TEMPLATE = (
    "sample\trole\tserotype\tlineage\tcountry\tyear\thost\taccession\tisolate\n"
    "SAMPLE-1\tsample\tSAT1\tI\tEthiopia\t2026\tbovine\t\tETH/1/2026\n"
    "SAMPLE-2\tcontext\tO\tEA-3\tKenya\t2019\tbovine\tMN123456\tK/12/2019\n"
)

# The function the VP1 search runs; tests replace it.
blast_best_hits: Callable = ctx.blast_best_hits


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- parsing --

def read_fasta_text(text: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    name, buf = None, []
    for line in text.splitlines():
        if line.startswith(">"):
            if name is not None:
                out.append((name, "".join(buf)))
            name, buf = line[1:].strip(), []
        elif name is not None:
            buf.append(re.sub(r"[^A-Za-z*-]", "", line.strip()))
    if name is not None:
        out.append((name, "".join(buf)))
    return [(n, s.upper().replace("U", "T")) for n, s in out if s]


def is_genbank_text(text: str) -> bool:
    head = text.lstrip()[:200]
    return head.startswith("LOCUS")


def _qualifiers(lines: List[str]) -> Dict[str, str]:
    """/key="value" pairs of one feature, values joined across continuation lines."""
    quals: Dict[str, str] = {}
    key, buf = None, []

    def flush():
        if key is not None:
            quals.setdefault(key, " ".join(buf).strip().strip('"'))
    for ln in lines:
        s = ln.strip()
        m = re.match(r'^/([A-Za-z_]+)(?:=(.*))?$', s)
        if m:
            flush()
            key, buf = m.group(1), [m.group(2) or ""]
        elif key is not None:
            buf.append(s)
    flush()
    return quals


def parse_genbank_text(text: str) -> List[Dict]:
    """The records of a GenBank flat file: accession, version, length, definition,
    the source feature's qualifiers, and the sequence. No Biopython: the backend
    env does not carry it and these six fields are all the import needs."""
    records: List[Dict] = []
    for chunk in re.split(r"^//\s*$", text, flags=re.M):
        if "LOCUS" not in chunk:
            continue
        rec: Dict = {"accession": "", "version": "", "length": 0, "definition": "", "organism": "",
                     "source": {}, "sequence": "", "raw": chunk.strip() + "\n//\n"}
        lines = chunk.splitlines()
        i = 0
        while i < len(lines):
            ln = lines[i]
            if ln.startswith("LOCUS"):
                f = ln.split()
                rec["locus"] = f[1] if len(f) > 1 else ""
                for j, tok in enumerate(f):
                    if tok == "bp" and j > 0 and f[j - 1].isdigit():
                        rec["length"] = int(f[j - 1])
            elif ln.startswith("DEFINITION"):
                parts = [ln[12:].strip()]
                while i + 1 < len(lines) and lines[i + 1].startswith(" " * 12):
                    i += 1
                    parts.append(lines[i].strip())
                rec["definition"] = " ".join(parts)
            elif ln.startswith("VERSION"):
                rec["version"] = ln.split()[1] if len(ln.split()) > 1 else ""
            elif ln.startswith("ACCESSION"):
                f = ln.split()
                rec["accession"] = f[1] if len(f) > 1 else ""
            elif ln.startswith("FEATURES"):
                i += 1
                feats: List[Tuple[str, List[str]]] = []
                while i < len(lines) and not lines[i].startswith(("ORIGIN", "CONTIG", "BASE COUNT")):
                    m = re.match(r"^ {5}(\S+)\s+(\S.*)$", lines[i])
                    if m:
                        feats.append((m.group(1), [m.group(2)]))
                    elif feats and lines[i].startswith(" " * 21):
                        feats[-1][1].append(lines[i])
                    i += 1
                for kind, body in feats:
                    if kind == "source":
                        rec["source"] = _qualifiers(body[1:])
                        rec["organism"] = rec["source"].get("organism", "")
                        break
                continue
            elif ln.startswith("ORIGIN"):
                seq = []
                i += 1
                while i < len(lines) and not lines[i].startswith("//"):
                    seq.append(re.sub(r"[^A-Za-z]", "", lines[i]))
                    i += 1
                rec["sequence"] = "".join(seq).upper()
                continue
            i += 1
        if not rec["accession"]:
            rec["accession"] = (rec.get("locus") or rec["version"].split(".")[0])
        if not rec["version"] and rec["accession"]:
            rec["version"] = rec["accession"]
        if rec["sequence"]:
            records.append(rec)
    return records


def genbank_metadata(rec: Dict) -> Dict[str, str]:
    """The import's metadata fields from a GenBank record's source feature.
    Country is the part of /geo_loc_name (or the older /country) before the
    first colon; year the four-digit year in /collection_date. Lineage is not
    a structured GenBank field and is left NA unless the user fills it."""
    src = rec.get("source") or {}
    geo = src.get("geo_loc_name") or src.get("country") or ""
    country = geo.split(":", 1)[0].strip()
    m = re.search(r"(1[89]\d\d|20\d\d)", src.get("collection_date") or "")
    year = m.group(1) if m else ""
    sero = (src.get("serotype") or "").strip().upper()
    if not sero:
        m2 = re.search(r"\b(serotype|type)\s+(SAT\s?[123]|Asia\s?1|[AOC])\b", rec.get("definition", ""), re.I)
        if m2:
            sero = m2.group(2).upper().replace(" ", "")
    sero = {"SAT 1": "SAT1", "SAT 2": "SAT2", "SAT 3": "SAT3", "ASIA 1": "ASIA1"}.get(sero, sero)
    return {"serotype": sero, "topotype": "", "country": country, "year": year,
            "host": (src.get("host") or "").strip(),
            "accession": ctx.bare_accession(rec.get("version") or rec.get("accession") or ""),
            "isolate": (src.get("isolate") or src.get("strain") or "").strip(),
            "definition": rec.get("definition", "")}


def read_sidecar_tsv(text: str) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
    """sample -> metadata from a sidecar sheet (TSV or CSV) with a `sample`
    column and any of role, serotype, lineage (or topotype), country, year,
    host, accession, isolate. Problems are reported, not silently dropped."""
    problems: List[str] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}, ["sheet is empty"]
    delim = "\t" if lines[0].count("\t") >= lines[0].count(",") else ","
    rows = list(csv.reader(lines, delimiter=delim))
    header = [h.strip().lower() for h in rows[0]]
    if "sample" not in header:
        return {}, [f"sheet header must contain `sample` (found: {', '.join(header)})"]
    si = header.index("sample")
    out: Dict[str, Dict[str, str]] = {}
    for n, row in enumerate(rows[1:], start=2):
        if len(row) <= si or not row[si].strip():
            problems.append(f"line {n}: no sample")
            continue
        meta = {}
        for j, h in enumerate(header):
            if h in SIDECAR_COLUMNS and h != "sample" and j < len(row):
                meta[h] = row[j].strip()
        if "topotype" in meta and "lineage" not in meta:
            meta["lineage"] = meta.pop("topotype")
        meta.pop("topotype", None)
        key = safe_sample_name(row[si])
        if key in out:
            problems.append(f"line {n}: sample {row[si]} listed twice")
            continue
        out[key] = meta
    return out, problems


def parse_accessions(text: str) -> Tuple[List[str], List[str]]:
    """Accessions one per line or separated by commas or spaces, versions
    dropped, duplicates collapsed; the tokens that are not accessions."""
    good: List[str] = []
    bad: List[str] = []
    for tok in re.split(r"[\s,;]+", text or ""):
        t = tok.strip().upper()
        if not t:
            continue
        if ACCESSION_RE.match(t):
            acc = ctx.bare_accession(t)
            if acc not in good:
                good.append(acc)
        else:
            bad.append(tok)
    return good, bad


# ------------------------------------------------------------- the library --

def library_accessions(lib: Path) -> Dict[str, str]:
    """accession -> where it already is (a panel genome, a context record or a
    context genome), so an import never adds a record the library holds."""
    where: Dict[str, str] = {}
    idx = lib / "lineages.json"
    if idx.is_file():
        try:
            for g in json.loads(idx.read_text()).get("genomes", []):
                if g.get("accession"):
                    where[ctx.bare_accession(g["accession"])] = f"panel genome {g.get('safe')}"
        except (OSError, ValueError):
            pass
    for tsv in sorted((lib / "vp1_context").glob("*.tsv")):
        for row in _read_tsv(tsv):
            acc = ctx.bare_accession(row.get("accession", ""))
            if acc and acc not in where:
                where[acc] = f"library context {row.get('name', '')}"
    return where


def project_context_accessions(project_dir: Path) -> Dict[str, str]:
    where: Dict[str, str] = {}
    for tsv in sorted((project_dir / "fmdv_context").glob("*.tsv")):
        for row in _read_tsv(tsv):
            acc = ctx.bare_accession(row.get("accession", ""))
            if acc:
                where[acc] = f"project context {row.get('name', '')}"
    return where


def _read_tsv(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    try:
        with open(path) as fh:
            header = fh.readline().rstrip("\n").split("\t")
            for line in fh:
                f = line.rstrip("\n").split("\t")
                if f and f[0]:
                    rows.append(dict(zip(header, f)))
    except OSError:
        pass
    return rows


def panel_vp1(lib: Path) -> List[Tuple[str, str]]:
    return ctx.panel_records(lib / "panels" / "vp1.fasta")


def context_vp1(lib: Path, project_dir: Optional[Path], serotype: str) -> List[Tuple[str, str]]:
    """The checked VP1 set of one serotype: the library's context records, the
    project's own, and the panel VP1s of that serotype. The slice templates."""
    out: List[Tuple[str, str]] = []
    for root in ([lib / "vp1_context"] + ([project_dir / "fmdv_context"] if project_dir else [])):
        fa = root / f"{serotype}.fasta"
        if fa.is_file():
            out.extend((h.split()[0], s) for h, s in ctx.read_records(fa))
    out.extend((n, s) for n, s in panel_vp1(lib) if ctx.serotype_of(n) == serotype)
    return out


# ---------------------------------------------------------------- the fetch --

def ncbi_reachable(timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((EUTILS_HOST, 443), timeout=timeout):
            return True
    except OSError:
        return False


def _efetch(ids: List[str], email: str, api_key: str) -> str:
    q = {"db": "nuccore", "id": ",".join(ids), "rettype": "gbwithparts", "retmode": "text", "tool": "fmdv_gui"}
    if email:
        q["email"] = email
    if api_key:
        q["api_key"] = api_key
    req = urllib.request.Request(EUTILS + "?" + urllib.parse.urlencode(q),
                                 headers={"User-Agent": "fmdv_gui sequence import"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def fetch_genbank(accessions: List[str], cache_dir: Path, email: str = "", api_key: str = "",
                  fetch: Callable[[List[str], str, str], str] = _efetch,
                  delay: float = FETCH_DELAY) -> Dict[str, Dict]:
    """accession -> {"path": cached flat file} or {"error": why}. Records already
    in the cache are not fetched. Every fetched record is written to the cache
    before it is used, so a retry or another project reads it from disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Dict] = {}
    todo: List[str] = []
    for acc in accessions:
        p = cache_dir / f"{acc}.gb"
        if p.is_file() and p.stat().st_size > 0:
            out[acc] = {"path": str(p), "cached": True}
        else:
            todo.append(acc)
    for i in range(0, len(todo), FETCH_BATCH):
        batch = todo[i:i + FETCH_BATCH]
        text, last_err = "", ""
        for attempt in range(FETCH_RETRIES):
            try:
                text = fetch(batch, email, api_key)
                break
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, OSError, socket.timeout) as e:
                last_err = str(getattr(e, "reason", e))
            time.sleep(delay * (2 ** attempt))
        got: Dict[str, Dict] = {}
        if text:
            for rec in parse_genbank_text(text):
                acc = ctx.bare_accession(rec.get("version") or rec.get("accession") or "")
                if acc:
                    got[acc] = rec
        for acc in batch:
            rec = got.get(acc)
            if rec is None:
                out[acc] = {"error": f"not returned by NCBI ({last_err or 'no such record, or not a nucleotide accession'})"}
                continue
            p = cache_dir / f"{acc}.gb"
            p.write_text(rec["raw"])
            out[acc] = {"path": str(p), "cached": False}
        if i + FETCH_BATCH < len(todo):
            time.sleep(delay)
    return out


# ------------------------------------------------------------ the records --

def import_json_path(sample_dir: Path) -> Optional[Path]:
    """The import record of a sample folder, under either name, or None."""
    for name in (IMPORT_JSON, LEGACY_IMPORT_JSON):
        p = sample_dir / name
        if p.is_file():
            return p
    return None


def read_import_json(sample_dir: Path) -> Dict:
    """The import record as a dict; {} when there is none or it is unreadable."""
    p = import_json_path(sample_dir)
    if p is None:
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def import_json_files(step1_dir: Path) -> List[Path]:
    """One import record per sample folder that has one, in sample order."""
    found: Dict[str, Path] = {}
    if not step1_dir.is_dir():
        return []
    for name in (IMPORT_JSON, LEGACY_IMPORT_JSON):
        for p in step1_dir.glob(f"*/{name}"):
            found.setdefault(p.parent.name, p)
    return [found[k] for k in sorted(found)]


def classify(length: int, mode: str = "fmdv") -> str:
    """What kind of record this is. Outside FMDV mode the only question is
    whether reads can be made from it, so there is one class, "sequence"."""
    if mode != "fmdv":
        return "sequence" if length >= READ_LEN else "short"
    if length < MIN_RECORD:
        return "short"
    if VP1_MIN <= length <= VP1_MAX:
        return "vp1"
    if length >= 7000:
        return "genome"
    if 1200 <= length < 2500:
        return "P1"
    return "other"


def simulatable_bases(seq: str, read_len: int = READ_LEN) -> int:
    """Bases simulate_reads would actually sample: the runs of called bases
    at least one read long. Everything else gets no reads and reads back as
    zero coverage, so this is what a depth setting applies to."""
    return sum(len(m.group(0)) for m in re.finditer(r"[ACGT]+", seq.upper()) if len(m.group(0)) >= read_len)


def describe_records(records: List[Dict], mode: str = "fmdv") -> None:
    """class, N count, called percent and the problem that stops a record
    being imported at all. In place, for both modes; locate_vp1 adds the
    FMDV-only answers on top."""
    for r in records:
        seq = r["sequence"]
        r["class"] = classify(len(seq), mode)
        r["n_count"] = sum(1 for c in seq if c not in "ACGT")
        r["called_percent"] = called_percent(seq)
        r.setdefault("problem", "")
        r["vp1"] = None
        r["simulatable"] = simulatable_bases(seq)
        if r["class"] == "short":
            r["problem"] = (f"{len(seq)} nt is under {MIN_RECORD}: partial VP1 makes gappy alignments"
                            if mode == "fmdv" else
                            f"{len(seq)} nt is under {READ_LEN}: too short to make a read from")
        elif mode != "fmdv" and not r["simulatable"]:
            r["problem"] = (f"no run of {READ_LEN} called bases: every part of this record is too short "
                            "or masked, so no read can be made from it")


def called_percent(seq: str) -> float:
    s = seq.upper()
    return round(100.0 * sum(1 for c in s if c in "ACGT") / len(s), 1) if s else 0.0


def records_from_fasta(text: str, source: str, sidecar: Optional[Dict[str, Dict[str, str]]] = None) -> List[Dict]:
    """One import record per FASTA record: the header parsed as a NanoFMDV
    label when it is one, the sidecar row when there is one."""
    out: List[Dict] = []
    for header, seq in read_fasta_text(text):
        first = header.split()[0]
        meta = {k: "" for k in META_FIELDS}
        labelled = ctx.parse_header(header)
        if labelled:
            meta.update({k: labelled.get(k, "") for k in META_FIELDS})
            name = labelled.get("isolate") or labelled.get("accession") or first
        else:
            name = first
            if ACCESSION_RE.match(first.upper()):
                meta["accession"] = ctx.bare_accession(first.upper())
                name = meta["accession"]
        name = safe_sample_name(name.split("|")[0])
        side = (sidecar or {}).get(name) or (sidecar or {}).get(safe_sample_name(first)) or {}
        if side.get("lineage"):
            meta["topotype"] = side["lineage"]
        for k in ("serotype", "country", "year", "host", "accession", "isolate"):
            if side.get(k):
                meta[k] = side[k]
        out.append({"source": source, "kind": "fasta", "header": header, "name": name, "sequence": seq,
                    "meta": meta, "role": side.get("role", "")})
    return out


def records_from_genbank(text: str, source: str) -> List[Dict]:
    out: List[Dict] = []
    for rec in parse_genbank_text(text):
        meta = genbank_metadata(rec)
        name = safe_sample_name(meta["accession"] or rec.get("locus", ""))
        out.append({"source": source, "kind": "genbank", "header": f"{rec.get('version', '')} {rec.get('definition', '')}".strip(),
                    "name": name, "sequence": rec["sequence"], "meta": {k: meta[k] for k in META_FIELDS},
                    "definition": meta["definition"], "organism": rec.get("organism", ""), "role": ""})
    return out


def locate_vp1(records: List[Dict], lib: Path, project_dir: Optional[Path], blastn: Optional[str],
               threads: int = 4) -> None:
    """Fill each record's `serotype` (from its best panel VP1 hit when not
    stated), `vp1` (the whole record or the slice, with coordinates) and
    `problem` (why it cannot be imported). In place."""
    panel = panel_vp1(lib)
    describe_records(records, "fmdv")
    live = [r for r in records if not r["problem"]]
    if not live:
        return
    if not blastn:
        for r in live:
            r["problem"] = "blastn is not on the backend's PATH (BLAST+), so VP1 cannot be checked or cut"
        return
    if not panel:
        for r in live:
            r["problem"] = f"no VP1 panel at {lib / 'panels' / 'vp1.fasta'} (run fmdv_reflib.py panels)"
        return
    best = blast_best_hits(blastn, [(f"r{i}", r["sequence"]) for i, r in enumerate(live)], panel, threads, "import")
    if best is None:
        for r in live:
            r["problem"] = "blastn failed in the VP1 check"
        return
    long_by_sero: Dict[str, List[Dict]] = {}
    for i, r in enumerate(live):
        f = best.get(f"r{i}")
        if f is None:
            r["problem"] = "no hit to any panel VP1: not FMDV VP1, or not FMDV"
            continue
        hit_sero = ctx.serotype_of(f[1])
        stated = (r["meta"].get("serotype") or "").upper()
        if stated and stated != hit_sero:
            r["serotype_note"] = f"header says {stated}, nearest panel VP1 is {hit_sero} ({f[1]})"
        r["meta"]["serotype"] = stated or hit_sero
        r["panel_hit"] = {"name": f[1], "identity": float(f[2]), "aligned": int(f[3])}
        if r["class"] == "vp1":
            cov = int(f[3]) / int(f[8])
            if cov < MIN_VP1_COVERAGE:
                r["problem"] = f"VP1-length record but the nearest panel VP1 covers only {cov:.0%} of it: not VP1"
                continue
            r["vp1"] = {"sequence": r["sequence"], "start": 1, "end": len(r["sequence"]), "strand": "+",
                        "region_source": "imported_vp1", "template": f[1], "identity": float(f[2]),
                        "coverage": round(cov, 2)}
        else:
            long_by_sero.setdefault(r["meta"]["serotype"], []).append(r)
    for sero, group in long_by_sero.items():
        templates = context_vp1(lib, project_dir, sero)
        if not templates:
            for r in group:
                r["problem"] = f"no VP1 set for serotype {sero} to cut VP1 against"
            continue
        skipped: collections.Counter = collections.Counter()
        items = [({"serotype": sero, "topotype": "", "accession": f"q{i}"}, r["sequence"]) for i, r in enumerate(group)]
        sliced = ctx.slice_vp1(sero, items, templates, blastn, threads, VP1_MIN, VP1_MAX, skipped)
        by_acc = {m["accession"]: (m, s) for m, s in sliced}
        for i, r in enumerate(group):
            got = by_acc.get(f"q{i}")
            if got is None:
                r["problem"] = f"no VP1 could be cut from this {r['class']} record (no usable hit to the {sero} VP1 set)"
                continue
            m, piece = got
            start, end = m["slice"].split("(")[0].split("-")
            r["vp1"] = {"sequence": piece, "start": int(start), "end": int(end), "strand": m["slice"][-2],
                        "region_source": "imported_sliced_from_" + m["region_source"].replace("sliced_from_", ""),
                        "template": m["slice_template"], "identity": float(m["slice_identity"]), "coverage": None}


# ------------------------------------------------------------- the preview --

def _stash_dir(project_dir: Path) -> Path:
    d = project_dir / ".seq_import"
    d.mkdir(parents=True, exist_ok=True)
    return d


def existing_names(project_dir: Path) -> Dict[str, str]:
    """name -> where, for every sample name the project already uses."""
    where: Dict[str, str] = {}
    step1 = project_dir / "step1"
    if step1.is_dir():
        for d in step1.iterdir():
            if d.is_dir() and not d.name.startswith((".", "_")):
                where[d.name] = "step1/"
    dl = project_dir / "download"
    if dl.is_dir():
        for p in dl.rglob("*.fastq.gz"):
            where.setdefault(re.split(r"[_.]", p.name, 1)[0], "download/")
    return where


def preview(records: List[Dict], project_dir: Path, lib: Path, default_role: str = "sample",
            blastn: Optional[str] = None, threads: int = 4, mode: str = "fmdv",
            depth: int = SIM_DEPTH) -> Dict:
    """One editable row per record, with the reads it would make (and, in an
    FMDV project, the VP1 found and the metadata read), and the sequences
    stashed under <project>/.seq_import/<token>.json for the commit."""
    fmdv = mode == "fmdv"
    if fmdv:
        locate_vp1(records, lib, project_dir, blastn, threads)
    else:
        # Nothing to look up outside FMDV mode: a record is a sample, and the
        # only question is whether reads can be made from it.
        describe_records(records, mode)
    names = existing_names(project_dir)
    lib_acc = library_accessions(lib) if fmdv else {}
    proj_acc = project_context_accessions(project_dir) if fmdv else {}
    token = time.strftime("%Y%m%d_%H%M%S") + "_" + hashlib.sha1(
        "".join(r["sequence"] for r in records).encode()).hexdigest()[:8]
    rows: List[Dict] = []
    seen: Dict[str, int] = {}
    for i, r in enumerate(records):
        m = r["meta"]
        name = r["name"] or f"seq{i + 1}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name}-{seen[name]}"
        acc = ctx.bare_accession(m.get("accession", "")) if m.get("accession") else ""
        exists = f"name already in {names[name]}" if name in names else ""
        # An accession the library or the project already holds as context
        # cannot be added as context twice; it can still be a SAMPLE (a
        # published isolate to type and highlight beside the project's own).
        in_context = ""
        if acc and acc in lib_acc:
            in_context = f"already in the library as {lib_acc[acc].split(' ', 2)[-1]}"
        elif acc and acc in proj_acc:
            in_context = f"already in this project's context as {proj_acc[acc].split(' ', 2)[-1]}"
        vp1 = r.get("vp1")
        rows.append({
            "id": i, "source": r["source"], "kind": r["kind"], "header": r["header"][:200],
            "name": name, "role": (r.get("role") or default_role) if fmdv else "sample",
            "serotype": m.get("serotype", ""), "lineage": m.get("topotype", ""),
            "country": m.get("country", ""), "year": m.get("year", ""), "host": m.get("host", ""),
            "accession": acc, "isolate": m.get("isolate", ""),
            "definition": r.get("definition", ""),
            "length": len(r["sequence"]), "n_count": r.get("n_count"), "called_percent": r.get("called_percent"),
            "class": r.get("class"),
            # What Step 1 will have to work with: perfect paired reads over
            # the called runs of the record, or nothing (an FMDV record too
            # short for the whole-genome workflow is typed without reads).
            "reads": reads_mode(r),
            "simulated": ({"covered_bases": r.get("simulatable") or 0, "read_length": READ_LEN}
                          if reads_mode(r) == "simulated" else None),
            "vp1": ({k: v for k, v in vp1.items() if k != "sequence"} | {"length": len(vp1["sequence"]),
                    "called_percent": called_percent(vp1["sequence"])}) if vp1 else None,
            "problem": r.get("problem", ""), "serotype_note": r.get("serotype_note", ""),
            "exists": exists, "in_context": in_context,
        })
    stash = _stash_dir(project_dir) / f"{token}.json"
    stash.write_text(json.dumps({"token": token, "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                 "records": [{"id": i, "sequence": r["sequence"], "header": r["header"],
                                              "source": r["source"], "kind": r["kind"],
                                              "vp1": r.get("vp1"), "class": r.get("class"),
                                              "panel_hit": r.get("panel_hit"), "raw": r.get("raw", "")}
                                             for i, r in enumerate(records)]}))
    return {"token": token, "rows": rows, "blastn": bool(blastn), "mode": mode,
            "depth": clamp_depth(depth), "min_depth": MIN_DEPTH, "max_depth": MAX_DEPTH,
            "project_context": project_context_counts(project_dir)}


def project_context_counts(project_dir: Path) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for tsv in sorted((project_dir / "fmdv_context").glob("*.tsv")):
        counts[tsv.stem] = len(_read_tsv(tsv))
    return counts


# -------------------------------------------------------------- the commit --

def build_plan(token: str, rows: List[Dict], project_dir: Path, lib: Path,
               mode: str = "fmdv", depth: int = SIM_DEPTH) -> Tuple[Dict, List[str]]:
    """Resolve the edited rows against the stash. Returns (plan, errors);
    nothing is written."""
    fmdv = mode == "fmdv"
    depth = clamp_depth(depth)
    stash = _stash_dir(project_dir) / f"{Path(token).name}.json"
    if not stash.is_file():
        return {}, ["the preview has expired; run Preview again"]
    data = json.loads(stash.read_text())
    by_id = {r["id"]: r for r in data["records"]}
    names = existing_names(project_dir)
    lib_acc = library_accessions(lib)
    proj_acc = project_context_accessions(project_dir)
    errors: List[str] = []
    entries: List[Dict] = []
    used: Dict[str, str] = {}
    for row in rows:
        rec = by_id.get(int(row.get("id", -1)))
        if rec is None:
            errors.append(f"row {row.get('id')} is not in the preview")
            continue
        # Outside FMDV mode there is one role and no typing: a record is a
        # sample, and what it needs is reads.
        role = (row.get("role") or "sample").strip().lower() if fmdv else "sample"
        if role not in ("sample", "context"):
            errors.append(f"row {row.get('id')}: role must be sample or context")
            continue
        if fmdv and not rec.get("vp1"):
            errors.append(f"{row.get('name') or rec['header'][:40]}: no VP1 was found in the preview")
            continue
        if not fmdv and reads_mode(rec) != "simulated":
            errors.append(f"{row.get('name') or rec['header'][:40]}: no reads can be made from this record")
            continue
        sero = (row.get("serotype") or "").strip().upper()
        if fmdv and not re.fullmatch(r"[A-Z0-9]+", sero or ""):
            errors.append(f"{row.get('name')}: serotype must be letters and digits (SAT1, O, ASIA1)")
            continue
        acc = (row.get("accession") or "").strip().upper()
        acc = ctx.bare_accession(acc) if acc else ""
        lineage = (row.get("lineage") or "").strip()
        meta = {"serotype": sero, "topotype": lineage, "country": (row.get("country") or "").strip(),
                "year": (row.get("year") or "").strip(), "host": (row.get("host") or "").strip(),
                "accession": acc, "isolate": (row.get("isolate") or "").strip()}
        if role == "sample":
            # A sample may repeat a library accession: the library holds it
            # as an unnamed comparator, the project wants it typed and shown.
            name = safe_sample_name(row.get("name") or "")
            if not name:
                errors.append(f"row {row.get('id')}: a sample needs a name")
                continue
            if name in names:
                errors.append(f"{name} is already in {names[name]}")
                continue
        else:
            if not acc:
                # A context record without an accession is named from its sample name.
                acc = safe_sample_name(row.get("name") or "") or f"seq{row.get('id')}"
                meta["accession"] = acc
            if acc in lib_acc:
                errors.append(f"{acc} is already in the library ({lib_acc[acc]})")
                continue
            if acc in proj_acc:
                errors.append(f"{acc} is already in this project's context ({proj_acc[acc]})")
                continue
            name = ctx.safe_name(meta)
        if name in used:
            errors.append(f"{name} is used twice")
            continue
        used[name] = role
        entries.append({"id": rec["id"], "role": role, "name": name, "meta": meta,
                        "source": rec["source"], "kind": rec["kind"], "header": rec["header"],
                        "class": rec.get("class"), "sequence": rec["sequence"], "vp1": rec["vp1"],
                        "panel_hit": rec.get("panel_hit"), "raw": rec.get("raw", "")})
    if not entries and not errors:
        errors.append("nothing selected")
    plan = {"project": str(project_dir), "lib": str(lib), "token": token, "entries": entries,
            "mode": mode, "depth": depth, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return plan, errors


def _write_fasta(path: Path, name: str, seq: str, width: int = 70) -> None:
    with open(path, "w") as fh:
        fh.write(f">{name}\n")
        for i in range(0, len(seq), width):
            fh.write(seq[i:i + width] + "\n")


def simulate_reads(seq: str, name: str, r1: Path, r2: Path, read_len: int = READ_LEN,
                   fragment: int = FRAGMENT, depth: int = SIM_DEPTH, seed: Optional[int] = None) -> Dict:
    """Perfect paired reads from a sequence, skipping every run of N.

    The sequence is cut at any base that is not A, C, G or T; each remaining
    segment of at least `read_len` is sampled with fragments of `fragment`
    bases (or the whole segment when shorter) at `depth` fold, R1 the first
    `read_len` of the fragment and R2 the reverse complement of its last
    `read_len`. Quality is Phred 40 throughout. Masked regions therefore get no
    reads at all: they read as zero coverage in the VCF and as N in the
    consensus, which is what a masked consensus means. Every header carries
    SIMULATED and the record it came from. Deterministic for a given name."""
    rng = random.Random(seed if seed is not None else int(hashlib.sha1(name.encode()).hexdigest()[:8], 16))
    s = seq.upper()
    segments = [m for m in re.finditer(r"[ACGT]+", s)]
    qual = chr(33 + 40) * read_len
    n_pairs = 0
    covered = 0
    skipped = 0
    r1.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(r1, "wt", compresslevel=1) as f1, gzip.open(r2, "wt", compresslevel=1) as f2:
        for m in segments:
            seg = m.group(0)
            L = len(seg)
            if L < read_len:
                skipped += 1
                continue
            covered += L
            frag = min(fragment, L)
            pairs = max(1, round(L * depth / (2 * read_len)))
            for _ in range(pairs):
                start = rng.randint(0, L - frag)
                piece = seg[start:start + frag]
                a = piece[:read_len]
                b = piece[-read_len:].translate(COMPLEMENT)[::-1]
                n_pairs += 1
                head = f"SIMULATED:{name}:{m.start() + start + 1}:{n_pairs}"
                f1.write(f"@{head}/1 simulated from imported sequence, no real reads\n{a}\n+\n{qual[:len(a)]}\n")
                f2.write(f"@{head}/2 simulated from imported sequence, no real reads\n{b}\n+\n{qual[:len(b)]}\n")
    return {"pairs": n_pairs, "read_length": read_len, "fragment": fragment, "depth": depth,
            "covered_bases": covered, "segments": len(segments), "segments_too_short": skipped,
            "r1": str(r1), "r2": str(r2)}


def reads_mode(entry: Dict) -> str:
    """simulated when reads will be made from the record, none when not.

    A whole genome in an FMDV project, and any record long enough outside
    one ("sequence"). An FMDV record shorter than a genome is typed and put
    on the VP1 tree instead, which needs no reads."""
    return "simulated" if entry.get("class") in ("genome", "sequence") else "none"


def write_sample(entry: Dict, project_dir: Path, mode: str = "fmdv", depth: int = SIM_DEPTH) -> Path:
    """The sample folder of one imported record, and its simulated reads in
    <project>/download/ when reads can be made from it."""
    name = entry["name"]
    fmdv = mode == "fmdv"
    d = project_dir / "step1" / name
    d.mkdir(parents=True, exist_ok=True)
    seq, vp1 = entry["sequence"], entry.get("vp1")
    _write_fasta(d / f"{name}_imported.fasta", entry["header"].split()[0] if entry["header"] else name, seq)
    if vp1:
        _write_fasta(d / f"{name}_VP1.fasta", f"{name} VP1 {vp1['start']}-{vp1['end']}({vp1['strand']}) of imported record", vp1["sequence"])
    if entry.get("raw"):
        (d / f"{name}_imported.gb").write_text(entry["raw"])
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta = entry["meta"]
    reads = reads_mode(entry)
    sim = None
    if reads == "simulated":
        dl = project_dir / "download"
        sim = simulate_reads(seq, name, dl / f"{name}_R1.fastq.gz", dl / f"{name}_R2.fastq.gz",
                             depth=clamp_depth(depth))
    record = {
        "sample": name, "role": "sample", "imported": now, "mode": mode,
        # simulated: paired reads in download/, run through Step 1 like any
        # sample; none: sequence-only, typed and placed on the VP1 tree.
        "reads": reads, "simulated_reads": sim,
        "source": entry["source"], "kind": entry["kind"], "header": entry["header"],
        "sha256": hashlib.sha256(seq.encode()).hexdigest(),
        "length": len(seq), "n_count": sum(1 for c in seq if c not in "ACGT"),
        "called_percent": called_percent(seq), "class": entry.get("class"),
        "panel_hit": entry.get("panel_hit"),
        "metadata": meta,
        "note": ("Imported sequence. The reads in download/ are SIMULATED from it (perfect, uniform, none over N, "
                 f"{sim['depth'] if sim else depth}-fold): depth, mapping rate and QC describe the simulation, "
                 "not a sample."
                 if reads == "simulated" else
                 "Imported sequence, no reads. Typed and placed on the VP1 tree like a sample; "
                 "no alignment, QC or whole-genome SNPs."),
    }
    if vp1:
        record["vp1"] = {k: v for k, v in vp1.items() if k != "sequence"} | {"length": len(vp1["sequence"])}
    (d / IMPORT_JSON).write_text(json.dumps(record, indent=1))
    # What the VP1 tree reads (collect_samples): serotype, and how much of
    # VP1 is called. `reference` is empty because nothing was mapped. FMDV
    # mode only: outside it there is no VP1 and no tree to read it.
    if fmdv and vp1:
        (d / "fmdv_consensus.json").write_text(json.dumps({
            "sample": name, "source": "imported", "reference": "", "serotype": meta["serotype"],
            "length": len(seq), "called_percent": called_percent(seq),
            "vp1_start": vp1["start"], "vp1_end": vp1["end"], "vp1_length": len(vp1["sequence"]),
            "vp1_called_percent": called_percent(vp1["sequence"]),
            "written": now,
        }, indent=1))
    return d


def append_context(entry: Dict, project_dir: Path) -> Path:
    """One record appended to <project>/fmdv_context/<SEROTYPE>.fasta and .tsv."""
    meta = entry["meta"]
    sero = meta["serotype"]
    d = project_dir / "fmdv_context"
    d.mkdir(parents=True, exist_ok=True)
    fa, tsv = d / f"{sero}.fasta", d / f"{sero}.tsv"
    name = entry["name"]
    vp1 = entry["vp1"]
    with open(fa, "a") as fh:
        fh.write(f">{name}\n{vp1['sequence']}\n")
    new = not tsv.is_file()
    with open(tsv, "a") as fh:
        if new:
            fh.write("\t".join(TSV_COLUMNS) + "\n")
        row = {c: "" for c in TSV_COLUMNS}
        row.update(name=name, serotype=sero, topotype=meta.get("topotype") or "NA", country=meta.get("country", ""),
                   year=meta.get("year", ""), host=meta.get("host", ""), accession=meta.get("accession", ""),
                   length=str(len(vp1["sequence"])), isolate=meta.get("isolate", ""),
                   region_source=vp1.get("region_source", "imported_vp1"), source_length=str(len(entry["sequence"])),
                   slice=f"{vp1['start']}-{vp1['end']}({vp1['strand']})",
                   slice_identity=f"{vp1['identity']:.1f}" if vp1.get("identity") is not None else "",
                   slice_template=vp1.get("template", ""),
                   vp1_check_panel=(entry.get("panel_hit") or {}).get("name", ""),
                   vp1_check_identity=f"{(entry.get('panel_hit') or {}).get('identity', 0):.1f}" if entry.get("panel_hit") else "",
                   vp1_check_coverage=f"{vp1['coverage']:.2f}" if vp1.get("coverage") is not None else "",
                   source=f"{entry['kind']}:{entry['source']}")
        fh.write("\t".join(row[c].replace("\t", " ") for c in TSV_COLUMNS) + "\n")
    # The typer's project database is rebuilt when a set is newer than it.
    return d


def run_commit(plan_path: Path) -> int:
    plan = json.loads(plan_path.read_text())
    project_dir, lib = Path(plan["project"]), Path(plan["lib"])
    mode = plan.get("mode") or "fmdv"
    depth = clamp_depth(plan.get("depth"))
    failed = 0
    samples: List[str] = []
    for e in plan["entries"]:
        try:
            if e["role"] == "sample":
                d = write_sample(e, project_dir, mode, depth)
                samples.append(e["name"])
                how = (f"simulated paired reads at {depth}x in download/, Grab then Run"
                       if reads_mode(e) == "simulated" else "sequence only")
                where = f", VP1 {e['vp1']['start']}-{e['vp1']['end']}" if e.get("vp1") else ""
                print(f"sample {e['name']}: {e['class']} {len(e['sequence'])} nt{where} -> {d} ({how})", flush=True)
            else:
                d = append_context(e, project_dir)
                print(f"context {e['name']}: {e['class']} {len(e['sequence'])} nt, VP1 {e['vp1']['start']}-{e['vp1']['end']} -> {d / (e['meta']['serotype'] + '.fasta')}", flush=True)
        except (OSError, KeyError, ValueError) as exc:
            failed += 1
            print(f"{e.get('role')} {e.get('name')}: FAILED ({exc})", flush=True)
    # Typing is FMDV's answer to "what is this": outside an FMDV project
    # there is no VP1 database to ask, and the sample is simply reads now.
    typer = _BIN / "fmdv_vp1_type.py"
    if mode == "fmdv" and samples and typer.is_file():
        if shutil.which("blastn"):
            for s in samples:
                cmd = [sys.executable, str(typer), "--sample-dir", str(project_dir / "step1" / s), "--lib", str(lib),
                       "--project-context", str(project_dir / "fmdv_context")]
                rc = subprocess.run(cmd).returncode
                if rc != 0:
                    print(f"typing {s}: fmdv_vp1_type.py exited {rc}", flush=True)
        else:
            print("typing skipped: blastn is not on PATH; use Type VP1 on the Step 2 VP1 tab later", flush=True)
    try:
        (_stash_dir(project_dir) / f"{Path(plan['token']).name}.json").unlink()
    except OSError:
        pass
    print(f"done: {len(plan['entries']) - failed} imported, {failed} failed", flush=True)
    return 1 if failed else 0


def prune_stash(project_dir: Path, max_age_s: float = 7 * 86400) -> None:
    d = project_dir / ".seq_import"
    if not d.is_dir():
        return
    now = time.time()
    for p in d.glob("*.json"):
        try:
            if now - p.stat().st_mtime > max_age_s:
                p.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "commit":
        sys.exit(run_commit(Path(sys.argv[2])))
    print("usage: python -m app.seq_import commit <plan.json>", file=sys.stderr)
    sys.exit(2)
