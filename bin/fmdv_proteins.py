#!/usr/bin/env python3
"""Per-protein coding features for the FMDV reference library.

An FMDV GenBank record carries one coding feature, the polyprotein, so every
annotated call in a vsnp3 Step 2 table reads `unlisted gene:H984Q,
polyprotein`. FMDV work is done in mature proteins and residue numbers within
them, VP1 position 45 or 2B position 24, so the polyprotein residue is the
wrong unit.

This module writes the mature proteins into the reference records as coding
features. For each genome it derives the 12 boundaries L, VP4, VP2, VP3, VP1,
2A, 2B, 2C, 3A, 3B, 3C, 3D as 1-based closed intervals on the genome, then
rewrites the .gbk so the polyprotein CDS becomes 12 CDS features, one per
protein, each with /gene, /product and a /translation taken from the
polyprotein translation. The polyprotein stays in the file as a misc_feature,
a type vsnp3's annotation module does not read, so the ORF is still visible
and still exactly one coding feature covers any position. The same 12 features
go into the .gff, so the IGV gene track shows 12 blocks instead of one.

Where the boundaries come from
    11 of the 79 library records carry mat_peptide features and those are used
    as given. The rest are derived by aligning the genome's polyprotein
    translation against an annotated one, preferring the same serotype, and
    carrying the boundaries across in protein coordinates. The cleavage sites
    are conserved and the proteins are colinear, so this transfers directly,
    but every transfer is checked: the 12 intervals must tile the coding
    sequence with no gap or overlap, each must start in frame, and each must be
    within a tolerance of its donor's length. A genome that fails any check is
    reported and left alone.

vsnp3 details this depends on
    vsnp3_annotation.py reads CDS, tRNA, rRNA, repeat_region, mobile_element
    and ncRNA, and ignores mat_peptide, so mature peptides have to be CDS to be
    seen at all. Where coding features overlap it picks the longest, so mature
    peptides added beside the polyprotein CDS would lose to it every time; the
    polyprotein CDS has to stop being a CDS. Its validate_reference translates
    each CDS with cds=True, which a mature peptide fails for want of a start or
    stop codon, so these features count as uncheckable and print nothing.

    One artifact survives. vsnp3 applies the translation table's initiator
    rule to a CDS's first codon, so where a mature protein happens to begin
    with a codon that also serves as an initiator, a variant in that codon
    reports the mutant residue as M and the type as "start codon lost". The
    reference residue, the protein and the residue number are still right.
    Writing /transl_table=1 holds this to 23 of the library's 869 internal
    first codons, down from 101 under the table 11 vsnp3 assumes.

Usage:
    fmdv_proteins.py annotate --lib <dir> [--genome <safe>] [--force] [--dry-run]
    fmdv_proteins.py table --lib <dir>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# The order is the order along the genome, which is also the cleavage order.
PROTEINS = ("L", "VP4", "VP2", "VP3", "VP1", "2A", "2B", "2C", "3A", "3B", "3C", "3D")

# Written into every rewritten record so a second run can tell the file has
# already been rewritten without parsing the features.
MARKER = "mature proteins written as CDS features by fmdv_proteins.py"

# /product carries the polyprotein residue range as well as the name, so a
# table row says which protein and, for anyone holding older notes, where that
# protein sits in the polyprotein. Set False for a bare name.
PRODUCT_WITH_OFFSET = True

# A transferred protein may differ from its donor in length by this much, as a
# fraction of the donor's length or an absolute number of residues, whichever
# is larger. 2A is 18 residues in every FMDV serotype and 3A varies most.
LENGTH_TOLERANCE_FRAC = 0.08
LENGTH_TOLERANCE_MIN = 3


# --------------------------------------------------------------- protein names

_PEPTIDE_SUFFIX = re.compile(r"\s*peptide\s*$", re.I)


def canonical_protein(product: str) -> Optional[str]:
    """One of PROTEINS for a GenBank mat_peptide /product, else None.

    The 11 annotated records spell the same 12 proteins eight different ways:
    "Lab", "LAB", "L", "Lab peptide", "Lab peptide; leader peptide",
    "leader proteinase", "3B1-3B3", "3B1/3B2/3B3 peptide", "VPg1",
    "pol peptide; 3D peptide".
    """
    for token in str(product).split(";"):
        t = _PEPTIDE_SUFFIX.sub("", token.strip()).strip()
        if not t:
            continue
        u = t.upper()
        if u in ("L", "LAB", "LB") or u.startswith("LEADER") or u.startswith("LAB "):
            return "L"
        if u.startswith("VPG") or u.startswith("3B"):
            return "3B"
        m = re.match(r"^(VP[1-4]|2[ABC]|3[ACD])\b", u)
        if m:
            return m.group(1)
        if u in ("POL", "POLYMERASE"):
            return "3D"
        if u in ("PRO", "PROTEINASE", "PROTEASE"):
            return "3C"
    return None


# ------------------------------------------------------------- reading a record


class Record:
    """The one polyprotein CDS of an FMDV GenBank record, and its sequence."""

    def __init__(self, path: Path):
        from Bio import SeqIO

        with open(path, encoding="utf-8", errors="replace") as fh:
            records = list(SeqIO.parse(fh, "genbank"))
        if len(records) != 1:
            raise ValueError(f"{path.name}: expected one GenBank record, found {len(records)}")
        self.path = path
        self.record = records[0]
        self.id = records[0].id
        cds = [f for f in records[0].features if f.type == "CDS"]
        if len(cds) != 1:
            raise ValueError(f"{path.name}: expected one CDS, found {len(cds)}")
        f = cds[0]
        if len(f.location.parts) != 1 or f.location.strand != 1:
            raise ValueError(f"{path.name}: polyprotein CDS is not a single forward span")
        self.cds_start = int(f.location.start) + 1      # 1-based inclusive
        self.cds_end = int(f.location.end)              # 1-based inclusive
        if (self.cds_end - self.cds_start + 1) % 3:
            raise ValueError(f"{path.name}: polyprotein CDS length is not a multiple of 3")
        self.cds = f
        self.protein = f.qualifiers.get("translation", [None])[0]
        if not self.protein:
            from Bio.Seq import Seq

            self.protein = str(Seq(str(f.extract(records[0].seq))).translate()).rstrip("*")

    def mat_peptides(self) -> Optional[List[Tuple[str, int, int]]]:
        """The record's own mat_peptide features as (name, start, end), 1-based
        closed, merged where a record splits 3B into VPg1, VPg2 and VPg3, or
        None when the record does not carry a usable set of all 12."""
        found: List[Tuple[str, int, int]] = []
        for f in self.record.features:
            if f.type != "mat_peptide" or len(f.location.parts) != 1:
                continue
            name = canonical_protein(f.qualifiers.get("product", [""])[0])
            if name is None:
                continue
            found.append((name, int(f.location.start) + 1, int(f.location.end)))
        if not found:
            return None
        found.sort(key=lambda x: x[1])
        merged: List[Tuple[str, int, int]] = []
        for name, start, end in found:
            if merged and merged[-1][0] == name and merged[-1][2] + 1 == start:
                merged[-1] = (name, merged[-1][1], end)
            else:
                merged.append((name, start, end))
        if [m[0] for m in merged] != list(PROTEINS):
            return None
        return merged


def residues(intervals: Sequence[Tuple[str, int, int]], cds_start: int) -> List[Tuple[str, int, int]]:
    """Genome intervals as 0-based half-open residue ranges in the polyprotein."""
    out = []
    for name, start, end in intervals:
        out.append((name, (start - cds_start) // 3, (end - cds_start + 1) // 3))
    return out


# ---------------------------------------------------------------- the transfer


def _aligner():
    from Bio import Align

    a = Align.PairwiseAligner()
    a.mode = "global"
    a.substitution_matrix = Align.substitution_matrices.load("BLOSUM62")
    a.open_gap_score = -11
    a.extend_gap_score = -1
    a.end_gap_score = 0.0
    return a


def _scorable(protein: str) -> str:
    """BLOSUM62 has no column for J, which GenBank uses for a Leu/Ile
    ambiguity, so anything outside the matrix aligns as X."""
    from Bio import Align

    alphabet = set(Align.substitution_matrices.load("BLOSUM62").alphabet)
    return "".join(c if c in alphabet else "X" for c in protein)


def transfer(donor_protein: str, donor_res: Sequence[Tuple[str, int, int]],
             target_protein: str) -> Tuple[List[Tuple[str, int, int]], float]:
    """Carry a donor's 12 residue boundaries onto a target polyprotein.

    Returns the target's residue ranges and the alignment's percent identity.
    A boundary is the first target residue aligned at or after the donor
    residue that opens the protein, so an insertion or deletion at a cleavage
    site moves the boundary rather than shifting every protein after it.
    """
    aln = _aligner().align(_scorable(donor_protein), _scorable(target_protein))[0]
    donor_to_target: Dict[int, int] = {}
    identities = 0
    # Biopython hands back numpy integers here; everything downstream is
    # written to JSON, so they become plain ints at the boundary.
    for (ds, de), (ts, te) in zip(*aln.aligned):
        ds, de, ts = int(ds), int(de), int(ts)
        for k in range(de - ds):
            donor_to_target[ds + k] = ts + k
            if donor_protein[ds + k] == target_protein[ts + k]:
                identities += 1
    mapped = sorted(donor_to_target)

    def forward(d: int) -> int:
        for m in mapped:
            if m >= d:
                return donor_to_target[m]
        return len(target_protein)

    cuts = [0]
    for _, start, _ in donor_res[1:]:
        cuts.append(forward(start))
    cuts.append(len(target_protein))
    out = [(donor_res[i][0], cuts[i], cuts[i + 1]) for i in range(len(donor_res))]
    identity = 100.0 * identities / max(1, min(len(donor_protein), len(target_protein)))
    return out, float(identity)


def check(target_res: Sequence[Tuple[str, int, int]], donor_res: Sequence[Tuple[str, int, int]],
          protein_length: int) -> List[str]:
    """Everything that is wrong with a transferred boundary set."""
    problems = []
    if [t[0] for t in target_res] != list(PROTEINS):
        problems.append("protein names are not the 12 expected names in order")
        return problems
    if target_res[0][1] != 0:
        problems.append(f"L does not start at residue 1 (starts at {target_res[0][1] + 1})")
    if target_res[-1][2] != protein_length:
        problems.append(f"3D ends at residue {target_res[-1][2]}, polyprotein has {protein_length}")
    for i, (name, start, end) in enumerate(target_res):
        if end <= start:
            problems.append(f"{name} is empty or inverted ({start}..{end})")
            continue
        if i and target_res[i - 1][2] != start:
            problems.append(f"{name} starts at {start}, {target_res[i - 1][0]} ends at {target_res[i - 1][2]}")
        d = donor_res[i][2] - donor_res[i][1]
        tol = max(LENGTH_TOLERANCE_MIN, LENGTH_TOLERANCE_FRAC * d)
        if abs((end - start) - d) > tol:
            problems.append(f"{name} is {end - start} residues, donor has {d}")
    return problems


def genome_intervals(res: Sequence[Tuple[str, int, int]], cds_start: int) -> List[Tuple[str, int, int]]:
    """Residue ranges back to 1-based closed intervals on the genome."""
    return [(name, cds_start + 3 * s, cds_start + 3 * e - 1) for name, s, e in res]


# ------------------------------------------------------------------- rewriting


def _qualifier_lines(key: str, value: str, quoted: bool = True) -> List[str]:
    """One qualifier wrapped the way NCBI wraps it: 21 spaces of indent and no
    line past column 79. A /translation breaks anywhere, because Biopython
    joins its continuation lines with nothing; every other qualifier breaks at
    a space, because Biopython joins those with a space."""
    text = f"/{key}=" + (f'"{value}"' if quoted else value)
    pad, width = " " * 21, 79 - 21
    out: List[str] = []
    if key == "translation":
        while len(text) > width:
            out.append(pad + text[:width])
            text = text[width:]
        out.append(pad + text)
        return out
    words = text.split(" ")
    line = words.pop(0)
    for word in words:
        if len(line) + 1 + len(word) > width:
            out.append(pad + line)
            line = word
        else:
            line += " " + word
    out.append(pad + line)
    return out


def _feature_block(kind: str, start: int, end: int, quals: Sequence[Tuple[str, str, bool]]) -> List[str]:
    lines = ["     " + kind.ljust(16) + f"{start}..{end}"]
    for key, value, quoted in quals:
        lines.extend(_qualifier_lines(key, value, quoted))
    return lines


def _product_text(name: str, res_start: int, res_end: int) -> str:
    if not PRODUCT_WITH_OFFSET:
        return name
    return f"{name} (polyprotein {res_start + 1}-{res_end})"


def rewrite_gbk(text: str, intervals: Sequence[Tuple[str, int, int]],
                res: Sequence[Tuple[str, int, int]], protein: str,
                cds_start: int, cds_end: int) -> str:
    """Replace the polyprotein CDS block with a misc_feature carrying the same
    location and 12 CDS blocks, one per mature protein."""
    lines = text.splitlines()
    head = re.compile(r"^     (\S+)\s+(\S.*)$")
    start_i = end_i = None
    for i, line in enumerate(lines):
        m = head.match(line)
        if m and m.group(1) == "CDS" and m.group(2).strip() == f"{cds_start}..{cds_end}":
            start_i = i
            for j in range(i + 1, len(lines)):
                if head.match(lines[j]) or not lines[j].startswith(" " * 21):
                    end_i = j
                    break
            else:
                end_i = len(lines)
            break
    if start_i is None:
        raise ValueError(f"no CDS {cds_start}..{cds_end} line in the GenBank text")

    old = lines[start_i:end_i]
    poly = ["     " + "misc_feature".ljust(16) + f"{cds_start}..{cds_end}"]
    for line in old[1:]:
        # The polyprotein keeps its qualifiers except /translation, which
        # belongs to the CDS features that now carry the coding call, and
        # /codon_start, which has no meaning on a misc_feature.
        poly.append(line)
    poly = [poly[0]] + _trim_quals(poly[1:], drop=("translation", "codon_start"))
    poly.extend(_qualifier_lines(
        "note", f"{MARKER}; this feature keeps the ORF visible and its "
                "translation is the 12 CDS translations in order"))

    block: List[str] = list(poly)
    last = len(intervals) - 1
    for i, (name, start, end) in enumerate(intervals):
        res_start, res_end = res[i][1], res[i][2]
        # 3D runs to the polyprotein's stop codon so no position in the ORF is
        # left outside every coding feature. Residue numbering is unchanged by
        # this, since it counts from the protein's own 5' end.
        feature_end = cds_end if i == last else end
        block.extend(_feature_block(
            "CDS", start, feature_end,
            [("gene", name, True),
             ("codon_start", "1", False),
             # The standard code, which a picornavirus uses. vsnp3 assumes
             # table 11 when a CDS does not say, and table 11 counts seven
             # codons as initiators against table 1's three, so saying so
             # keeps its initiator rule off most first codons. See the note on
             # first codons in this module's docstring.
             ("transl_table", "1", False),
             ("product", _product_text(name, res_start, res_end), True),
             ("note", f"mature protein {name}, polyprotein residues "
                      f"{res_start + 1}-{res_end}", True),
             ("translation", protein[res_start:res_end], True)]))
    return "\n".join(lines[:start_i] + block + lines[end_i:]) + "\n"


def _trim_quals(lines: Sequence[str], drop: Sequence[str]) -> List[str]:
    out, skipping = [], False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("/"):
            skipping = stripped.split("=", 1)[0][1:] in drop
        if not skipping:
            out.append(line)
    return out


def rewrite_gff(text: str, acc: str, intervals: Sequence[Tuple[str, int, int]],
                res: Sequence[Tuple[str, int, int]], cds_start: int, cds_end: int) -> str:
    """Replace the polyprotein CDS line with 12 CDS lines. The first column
    stays the bare accession, which is what igv.js draws the track on."""
    out: List[str] = []
    replaced = False
    last = len(intervals) - 1
    for line in text.splitlines():
        cols = line.split("\t")
        if (not line.startswith("#") and len(cols) >= 9 and cols[2] == "CDS"
                and cols[3] == str(cds_start) and cols[4] == str(cds_end)):
            for i, (name, start, end) in enumerate(intervals):
                feature_end = cds_end if i == last else end
                attrs = (f"ID=cds-{acc}-{name};Name={name};gene={name};"
                         f"product={_product_text(name, res[i][1], res[i][2])};gbkey=CDS")
                out.append("\t".join([cols[0], cols[1], "CDS", str(start), str(feature_end),
                                      ".", "+", "0", attrs]))
            replaced = True
            continue
        out.append(line)
    if not replaced:
        raise ValueError(f"no CDS {cds_start}..{cds_end} line in the GFF")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------- the step


def boundaries(lib: Path, only: Optional[str] = None) -> Dict:
    """Derive the 12 mature protein intervals for every library genome.

    Returns {"genomes": {safe: {...}}, "failed": {safe: [reason, ...]}}. A
    genome is derived from its own mat_peptide features where it has them and
    otherwise transferred from the annotated genome of the same serotype with
    the highest identity, falling back to the best donor of any serotype.
    """
    index = json.loads((lib / "lineages.json").read_text())
    annotations = lib / "annotations"
    genomes = [g for g in index["genomes"] if only in (None, g["safe"])]
    if only and not genomes:
        raise SystemExit(f"unknown genome {only!r}")

    records: Dict[str, Record] = {}
    donors: Dict[str, Dict] = {}
    failed: Dict[str, List[str]] = {}
    for g in index["genomes"]:
        gbk = annotations / f"{g['safe']}.gbk"
        if not gbk.is_file():
            continue
        src = _raw(annotations, g["safe"], ".gbk") or gbk
        try:
            rec = Record(src)
        except (ValueError, OSError) as e:
            failed[g["safe"]] = [str(e)]
            continue
        records[g["safe"]] = rec
        own = rec.mat_peptides()
        if own is not None:
            res = residues(own, rec.cds_start)
            problems = check(res, res, len(rec.protein))
            if problems:
                failed[g["safe"]] = ["own mat_peptide features: " + p for p in problems]
            else:
                donors[g["safe"]] = {"serotype": g["serotype"], "record": rec, "res": res}

    out: Dict[str, Dict] = {}
    for g in genomes:
        safe = g["safe"]
        rec = records.get(safe)
        if rec is None:
            continue
        if safe in donors:
            res = donors[safe]["res"]
            out[safe] = _entry(g, rec, res, source="mat_peptide features of this record",
                               donor=safe, identity=100.0)
            continue
        same = [d for d in donors.values() if d["serotype"] == g["serotype"]]
        pool = same or list(donors.values())
        if not pool:
            failed.setdefault(safe, []).append("no annotated donor genome in the library")
            continue
        best = None
        for d in pool:
            res, identity = transfer(d["record"].protein, d["res"], rec.protein)
            if best is None or identity > best[1]:
                best = (res, identity, d)
        res, identity, d = best
        problems = check(res, d["res"], len(rec.protein))
        if problems:
            failed.setdefault(safe, []).extend(
                [f"transfer from {d['record'].id}: {p}" for p in problems])
            continue
        out[safe] = _entry(g, rec, res,
                           source=("transferred from the same serotype" if same
                                   else "transferred across serotypes"),
                           donor=d["record"].id, identity=identity)
    return {"genomes": out, "failed": failed,
            "donors": sorted(donors), "n_genomes": len(out)}


def _entry(g: Dict, rec: Record, res: Sequence[Tuple[str, int, int]],
           source: str, donor: str, identity: float) -> Dict:
    intervals = genome_intervals(res, rec.cds_start)
    return {
        "safe": g["safe"], "accession": rec.id, "serotype": g["serotype"],
        "cds": [rec.cds_start, rec.cds_end], "polyprotein_length": len(rec.protein),
        "source": source, "donor": donor, "donor_identity": round(identity, 1),
        "proteins": [{"name": name, "start": start, "end": end,
                      "polyprotein_start": res[i][1] + 1, "polyprotein_end": res[i][2],
                      "length_aa": res[i][2] - res[i][1]}
                     for i, (name, start, end) in enumerate(intervals)],
    }


def _raw(annotations: Path, safe: str, ext: str) -> Optional[Path]:
    """The untouched download, kept when a file is first rewritten so the
    rewrite can be redone from the original rather than from its own output."""
    p = annotations / "raw" / f"{safe}{ext}"
    return p if p.is_file() else None


def vp1_crosscheck(lib: Path, table: Dict) -> List[str]:
    """Compare the VP1 interval against reference_options/<safe>/VP1_coords.json,
    which fmdv_consensus.py writes by mapping the VP1 panel record with
    minimap2. The two are derived independently and should agree."""
    notes = []
    for safe, entry in sorted(table["genomes"].items()):
        cache = lib / "reference_options" / safe / "VP1_coords.json"
        if not cache.is_file():
            continue
        try:
            c = json.loads(cache.read_text())
        except (OSError, ValueError):
            continue
        vp1 = next(p for p in entry["proteins"] if p["name"] == "VP1")
        ds, de = int(c["start"]) - vp1["start"], int(c["end"]) - vp1["end"]
        notes.append(f"{safe}: VP1 {vp1['start']}..{vp1['end']}, "
                     f"VP1_coords.json {c['start']}..{c['end']} (start {ds:+d}, end {de:+d})")
    return notes


def annotate(lib: Path, only: Optional[str] = None, force: bool = False,
             dry_run: bool = False) -> Dict:
    """Write the mature proteins into the library's annotation files and into
    every reference folder that holds a copy. Idempotent: a file already
    rewritten is left alone unless --force, and the untouched download is kept
    under annotations/raw/ so a rewrite always starts from the original."""
    annotations = lib / "annotations"
    table = boundaries(lib, only)
    counts = {"rewritten": 0, "present": 0, "failed": len(table["failed"]), "copied": 0}
    for safe, entry in sorted(table["genomes"].items()):
        gbk, gff = annotations / f"{safe}.gbk", annotations / f"{safe}.gff"
        # A record already rewritten carries a /gene qualifier per protein,
        # which no fetched FMDV record has. The note holding MARKER wraps
        # across lines, so it is not a string to search the file for.
        done = gbk.is_file() and '/gene="VP1"' in gbk.read_text()
        if done and not force:
            counts["present"] += 1
        elif dry_run:
            counts["rewritten"] += 1
        else:
            raw_dir = annotations / "raw"
            raw_dir.mkdir(exist_ok=True)
            for src, ext in ((gbk, ".gbk"), (gff, ".gff")):
                raw = raw_dir / f"{safe}{ext}"
                if src.is_file() and not raw.is_file():
                    raw.write_text(src.read_text())
            cds_start, cds_end = entry["cds"]
            res = [(p["name"], p["polyprotein_start"] - 1, p["polyprotein_end"])
                   for p in entry["proteins"]]
            intervals = [(p["name"], p["start"], p["end"]) for p in entry["proteins"]]
            rec = Record(_raw(annotations, safe, ".gbk") or gbk)
            gbk.write_text(rewrite_gbk(
                (_raw(annotations, safe, ".gbk") or gbk).read_text(),
                intervals, res, rec.protein, cds_start, cds_end))
            raw_gff = _raw(annotations, safe, ".gff") or gff
            if raw_gff.is_file():
                gff.write_text(rewrite_gff(raw_gff.read_text(), entry["accession"],
                                           intervals, res, cds_start, cds_end))
            counts["rewritten"] += 1
        folder = lib / "reference_options" / safe
        if folder.is_dir() and not dry_run:
            for ext in (".gbk", ".gff"):
                src, dst = annotations / f"{safe}{ext}", folder / f"{safe}{ext}"
                if src.is_file() and (not dst.is_file() or dst.read_text() != src.read_text()):
                    dst.write_text(src.read_text())
                    counts["copied"] += 1
    if not dry_run and only is None:
        (annotations / "protein_coords.json").write_text(json.dumps(table, indent=1))
    for safe, problems in sorted(table["failed"].items()):
        for p in problems:
            print(f"proteins: {safe}: {p}", file=sys.stderr)
    print(f"proteins: {counts}", file=sys.stderr)
    return {"counts": counts, "table": table}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    an = sub.add_parser("annotate")
    an.add_argument("--lib", required=True)
    an.add_argument("--genome", default=None, help="one genome's safe name")
    an.add_argument("--force", action="store_true", help="rewrite files already rewritten")
    an.add_argument("--dry-run", action="store_true", help="derive and check, write nothing")
    tb = sub.add_parser("table")
    tb.add_argument("--lib", required=True)
    tb.add_argument("--genome", default=None)
    tb.add_argument("--vp1-crosscheck", action="store_true")
    a = ap.parse_args()
    lib = Path(a.lib).expanduser().resolve()
    if a.cmd == "annotate":
        annotate(lib, a.genome, a.force, a.dry_run)
        return 0
    table = boundaries(lib, a.genome)
    for safe, entry in sorted(table["genomes"].items()):
        print(f"{safe}  {entry['accession']}  CDS {entry['cds'][0]}..{entry['cds'][1]}  "
              f"{entry['polyprotein_length']} aa  {entry['source']} "
              f"({entry['donor']}, {entry['donor_identity']}% identity)")
        for p in entry["proteins"]:
            print(f"    {p['name']:<4} {p['start']:>5}..{p['end']:<5} "
                  f"{p['length_aa']:>5} aa  polyprotein {p['polyprotein_start']}-{p['polyprotein_end']}")
    for safe, problems in sorted(table["failed"].items()):
        for p in problems:
            print(f"FAILED {safe}: {p}")
    if a.vp1_crosscheck:
        for note in vp1_crosscheck(lib, table):
            print(note)
    print(f"{len(table['genomes'])} genomes, {len(table['failed'])} failed, "
          f"donors: {', '.join(table['donors'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
