#!/usr/bin/env python3
"""Build the per-serotype VP1 context sets from NanoFMDV's reference collection.

The VP1 tree places a sample among lineages. Against the 79-genome panel
alone that placement is weak and partly circular: the panel holds one
genome per lineage, and the sample's reference was chosen from it. The
comparator set this adds is NanoFMDV's own GenBank-derived collection
(Shaw et al. 2025, `Refs/BLAST/fmdv-gbbase.fasta`), whose headers already
carry what the placement needs:

    SAT1|IX|ETHIOPIA|2007|BOVINE|KC440883|639|ETH/3/2007_G001_S001
    serotype topotype country year host accession length isolate

So no database query is involved: the records are read, renamed to the
fork's safe form and written per serotype, with a provenance TSV beside
each file carrying every field as the source states it. Nothing is
inferred and no accession is invented.

Not every VP1-length record in the collection is VP1: the SAT1 AF2834xx
series, for one, are 609-nt polyprotein fragments from the 3C region that
sit on genomes near position 6,000. So every record is checked with blastn
against the 79 panel VP1 sequences and kept only when its best hit is of
its own serotype and covers at least 70% of the record. The check needs
BLAST+ (blastn and makeblastdb) on PATH.

Three kinds of record go in, and the TSV's region_source column says which:

  vp1_record          the record is VP1 itself (600 to 700 nt)
  sliced_from_genome  VP1 cut from a complete genome (7,000 nt or more)
  sliced_from_P1      VP1 cut from a P1 capsid record (1,200 to 2,500 nt)
  sliced_from_other   VP1 cut from any other record over 700 nt

Slicing runs blastn once per serotype: each long record against every
checked VP1 record of that serotype plus its panel genomes' VP1, best hit by bitscore,
extended by the template's unaligned ends, kept when the slice is VP1
length and at least 80% of the template aligned. The TSV records the
template, the identity to it and the coordinates. blastn finds VP1 at 70
to 100% identity; minimap2 missed most records below about 87% in a trial
on SAT1. Records under 600 nt are left out: partial VP1 makes gappy
alignments and unstable placement.

About one record in six carries a plain GenBank header instead, such as
`SAT1|LC770123.1_FOOT-AND-MOUTH_DISEASE_VIRUS_SAT_1_...`. The serotype is
known and the accession is the first token, so these are kept with
topotype NA and the GenBank title as the isolate field.

Any accession already in the 79-genome panel (lib/panels/vp1.fasta, safe
names ending in _<accession>) is skipped so a genome is not in the tree
twice. Safe names are unique within and across the serotype files.

Why it matters for the trial: the panel holds one genome per lineage, so
any sample whose lineage lacks a complete genome in GenBank can only sit
on a long branch beside the nearest panel genome. SAT1 topotype IX is one
such lineage; this collection has three VP1 sequences for it, all
Ethiopian, 2007. Which lineage the trial samples belong to is what the
tree is for, not something assumed in advance.

Usage:
  fmdv_vp1_context.py --source <fmdv-gbbase.fasta> --lib ~/fmdv_refs
  fmdv_vp1_context.py --source ... --lib ... --serotype SAT1 --max-per-lineage 40
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

VP1_MIN, VP1_MAX = 600, 700
MIN_QUERY_ALIGNED = 0.80   # a slice template must align over this much of itself
MIN_VP1_COVERAGE = 0.70    # a record must align to a panel VP1 over this much of itself
SAFE = re.compile(r"[^A-Za-z0-9._-]+")
# GenBank nucleotide accession, with or without a version: KC440883, LC770123.1, NC_039210.1
ACCESSION = re.compile(r"^([A-Z]{1,2}_?\d{5,8})(\.\d+)?$")
COMPLEMENT = str.maketrans("ACGTRYKMBDHVNacgtrykmbdhvn", "TGCAYRMKVHDBNtgcayrmkvhdbn")

FIELDS = ["serotype", "topotype", "country", "year", "host", "accession", "length", "isolate"]
TSV_COLUMNS = ["name", "serotype", "topotype", "country", "year", "host", "accession", "length",
               "isolate", "region_source", "source_length", "slice", "slice_identity", "slice_template",
               "vp1_check_panel", "vp1_check_identity", "vp1_check_coverage"]

Record = Tuple[Dict[str, str], str]


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def read_records(path: Path) -> Iterator[Tuple[str, str]]:
    """(header, sequence) for every record."""
    name, seq = None, []
    with open(path) as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    yield name, "".join(seq)
                name, seq = line[1:].strip(), []
            else:
                seq.append(line.strip())
    if name is not None:
        yield name, "".join(seq)


def parse_header(h: str) -> Optional[Dict[str, str]]:
    """Fields from a labelled header, or from a plain GenBank one; None if neither."""
    parts = [p.strip() for p in h.split("|")]
    if len(parts) == len(FIELDS):
        out = dict(zip(FIELDS, parts))
        out["header_form"] = "labelled"
    elif len(parts) == 2:
        token = parts[1].split("_", 1)
        if not ACCESSION.match(token[0]):
            return None
        out = {k: "" for k in FIELDS}
        out.update(serotype=parts[0], accession=token[0],
                   isolate=token[1].replace("_", " ") if len(token) > 1 else "")
        out["header_form"] = "genbank"
    else:
        return None
    out["serotype"] = out["serotype"].upper()
    out["raw"] = h
    return out


def bare_accession(acc: str) -> str:
    """KC440883.1 -> KC440883, so panel and context compare on the same key."""
    m = ACCESSION.match(acc)
    return m.group(1) if m else acc


def safe_name(meta: Dict[str, str]) -> str:
    sero = SAFE.sub("-", meta["serotype"]).strip("-")
    topo = SAFE.sub("-", meta["topotype"]).strip("-") or "NA"
    acc = SAFE.sub("-", meta["accession"]).strip("-")
    return f"{sero}-{topo}_{acc}"


def serotype_of(safe: str) -> str:
    return safe.split("-", 1)[0].split("_", 1)[0].upper()


def panel_records(panel: Path) -> List[Tuple[str, str]]:
    return [(h.split()[0], s) for h, s in read_records(panel)] if panel.is_file() else []


def revcomp(seq: str) -> str:
    return seq.translate(COMPLEMENT)[::-1]


def source_class(length: int) -> str:
    if length >= 7000:
        return "sliced_from_genome"
    if 1200 <= length < 2500:
        return "sliced_from_P1"
    return "sliced_from_other"


def blast_best_hits(blastn: str, queries: List[Tuple[str, str]], subjects: List[Tuple[str, str]],
                    threads: int, tag: str) -> Optional[Dict[str, List[str]]]:
    """Best blastn hit (by bitscore) of every query against the subject set; None if blast failed."""
    makeblastdb = str(Path(blastn).with_name("makeblastdb"))
    with tempfile.TemporaryDirectory(prefix=f"vp1ctx_{tag}_") as td:
        qry, sub, db, hits = (Path(td) / "q.fa", Path(td) / "s.fa", Path(td) / "db", Path(td) / "hits.tsv")
        qry.write_text("".join(f">{n}\n{seq}\n" for n, seq in queries))
        sub.write_text("".join(f">{n}\n{seq}\n" for n, seq in subjects))
        if subprocess.run([makeblastdb, "-in", str(sub), "-dbtype", "nucl", "-out", str(db)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            return None
        cmd = [blastn, "-task", "blastn", "-query", str(qry), "-db", str(db), "-evalue", "1e-10",
               "-max_target_seqs", "5", "-max_hsps", "1", "-num_threads", str(threads),
               "-outfmt", "6 qseqid sseqid pident length qstart qend sstart send qlen slen bitscore"]
        with open(hits, "w") as fh:
            if subprocess.run(cmd, stdout=fh, stderr=subprocess.DEVNULL).returncode != 0:
                return None
        best: Dict[str, List[str]] = {}
        for line in open(hits):
            f = line.rstrip("\n").split("\t")
            if len(f) >= 11 and (f[0] not in best or float(f[10]) > float(best[f[0]][10])):
                best[f[0]] = f
        return best


def check_vp1(items: List[Record], panel_seqs: List[Tuple[str, str]], blastn: str, threads: int,
              skipped: collections.Counter) -> List[Record]:
    """Keep the records that are VP1 of their stated serotype, judged against the panel's VP1.

    The collection's VP1-length records are not all VP1: the AF2834xx SAT1
    series, for instance, are 609-nt polyprotein fragments from the 3C region.
    A record passes when its best hit among the 79 panel VP1 sequences covers
    at least MIN_VP1_COVERAGE of the record and is of the record's serotype.
    """
    if not items:
        return []
    best = blast_best_hits(blastn, [(f"r{i}", seq) for i, (_, seq) in enumerate(items)], panel_seqs, threads, "check")
    if best is None:
        _err("vp1_context: blastn failed in the VP1 check; nothing kept")
        skipped["VP1 check failed (blast error)"] += len(items)
        return []
    out: List[Record] = []
    for i, (meta, seq) in enumerate(items):
        f = best.get(f"r{i}")
        if f is None:
            skipped["not VP1 (no hit to any panel VP1)"] += 1
            continue
        cov = int(f[3]) / int(f[8])
        if cov < MIN_VP1_COVERAGE:
            skipped[f"not VP1 (panel hit covers under {int(MIN_VP1_COVERAGE * 100)}%)"] += 1
            continue
        if serotype_of(f[1]) != meta["serotype"]:
            skipped["serotype differs from closest panel VP1"] += 1
            continue
        m = dict(meta)
        m.update(vp1_check_panel=f[1], vp1_check_identity=f"{float(f[2]):.1f}", vp1_check_coverage=f"{cov:.2f}")
        out.append((m, seq))
    return out


def slice_vp1(sero: str, long_items: List[Record], vp1_set: List[Tuple[str, str]],
              blastn: str, threads: int, lo: int, hi: int,
              skipped: collections.Counter) -> List[Record]:
    """VP1 cut from each long record by its best blastn hit to the serotype's checked VP1 set.

    blastn (-task blastn) finds VP1 at 70 to 100% identity; minimap2 missed
    most records below about 87% in a trial on SAT1, so it is not used.
    """
    if not long_items or not vp1_set:
        skipped["long record, no VP1 set for serotype"] += len(long_items)
        return []
    best = blast_best_hits(blastn, [(f"q{i}", seq) for i, (_, seq) in enumerate(long_items)], vp1_set, threads, sero)
    if best is None:
        _err(f"vp1_context: blastn failed for {sero}; long records skipped")
        skipped["long record, blast failed"] += len(long_items)
        return []
    out: List[Record] = []
    for i, (meta, seq) in enumerate(long_items):
        f = best.get(f"q{i}")
        if f is None:
            skipped["long record, no VP1 hit"] += 1
            continue
        pident, qstart, qend = float(f[2]), int(f[4]), int(f[5])
        sstart, send, qlen, slen = int(f[6]), int(f[7]), int(f[8]), int(f[9])
        if (abs(send - sstart) + 1) / slen < MIN_QUERY_ALIGNED:
            skipped["long record, VP1 hit too partial"] += 1
            continue
        # Extend the hit by the template's unaligned ends so the slice is the whole gene.
        if sstart <= send:
            strand = "+"
            start, end = qstart - 1 - (sstart - 1), qend + (slen - send)
        else:
            strand = "-"
            start, end = qstart - 1 - (slen - sstart), qend + (send - 1)
        start, end = max(0, start), min(qlen, end)
        piece = seq[start:end]
        if strand == "-":
            piece = revcomp(piece)
        if not (lo <= len(piece) <= hi):
            skipped["long record, slice not VP1 length"] += 1
            continue
        m = dict(meta)
        m.update(region_source=source_class(qlen), source_length=str(qlen),
                 slice=f"{start + 1}-{end}({strand})", slice_identity=f"{pident:.1f}", slice_template=f[1],
                 vp1_check_panel="", vp1_check_identity="", vp1_check_coverage="")
        out.append((m, piece))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="NanoFMDV Refs/BLAST/fmdv-gbbase.fasta")
    ap.add_argument("--lib", required=True, help="FMDV reference library (writes vp1_context/ under it)")
    ap.add_argument("--panel", help="lineage panel whose accessions are skipped and whose VP1s seed "
                                    "the slicing (default lib/panels/vp1.fasta)")
    ap.add_argument("--serotype", action="append", help="limit to these serotypes (repeatable)")
    ap.add_argument("--max-per-lineage", type=int, default=0,
                    help="cap sequences per topotype, longest first (0 = keep all). "
                         "O and A hold thousands; a tree does not need them all.")
    ap.add_argument("--no-slice", action="store_true", help="do not cut VP1 from records over --max-length")
    ap.add_argument("--blastn", help="path to blastn (default: first on PATH; makeblastdb beside it)")
    ap.add_argument("--threads", type=int, default=4, help="blastn threads for the slicing")
    ap.add_argument("--min-length", type=int, default=VP1_MIN)
    ap.add_argument("--max-length", type=int, default=VP1_MAX)
    a = ap.parse_args()

    source, lib = Path(a.source).expanduser(), Path(a.lib).expanduser()
    if not source.is_file():
        _err(f"vp1_context: no such file: {source}")
        return 2
    panel = Path(a.panel).expanduser() if a.panel else lib / "panels" / "vp1.fasta"
    panel_seqs = panel_records(panel)
    in_panel = {bare_accession(n.rsplit("_", 1)[1]) for n, _ in panel_seqs if "_" in n}
    want = {s.upper() for s in (a.serotype or [])}
    blastn = a.blastn or shutil.which("blastn")
    if not blastn or not Path(blastn).with_name("makeblastdb").exists():
        _err("vp1_context: blastn and makeblastdb (BLAST+) are required for the VP1 check; none on PATH")
        return 2
    if not panel_seqs:
        _err(f"vp1_context: no panel VP1 at {panel}; the VP1 check needs it")
        return 2

    by_sero: Dict[str, List[Record]] = collections.defaultdict(list)
    candidates: List[Record] = []
    long_by_sero: Dict[str, List[Record]] = collections.defaultdict(list)
    skipped: collections.Counter = collections.Counter()
    total = 0
    for header, seq in read_records(source):
        total += 1
        meta = parse_header(header)
        if meta is None:
            skipped["unparseable header"] += 1
            continue
        if not meta["serotype"] or meta["serotype"] == "UNTYPED":
            skipped["untyped"] += 1
            continue
        if want and meta["serotype"] not in want:
            skipped["other serotype"] += 1
            continue
        if not meta["accession"]:
            skipped["no accession"] += 1
            continue
        # Before the length filter, so the count covers every panel genome the
        # collection holds, whole genome or VP1 slice.
        if bare_accession(meta["accession"]) in in_panel:
            skipped["already in panel"] += 1
            continue
        if len(seq) < a.min_length:
            skipped["shorter than VP1"] += 1
            continue
        if len(seq) > a.max_length:
            if a.no_slice:
                skipped["longer than VP1, not sliced"] += 1
            else:
                long_by_sero[meta["serotype"]].append((meta, seq.upper()))
            continue
        meta.update(region_source="vp1_record", source_length=str(len(seq)), slice="",
                    slice_identity="", slice_template="")
        candidates.append((meta, seq.upper()))

    # Every VP1-length record must be VP1 of its serotype, judged against the panel.
    checked = check_vp1(candidates, panel_seqs, blastn, a.threads, skipped)
    _err(f"vp1_context: VP1 check kept {len(checked)} of {len(candidates)} VP1-length records")
    for meta, seq in checked:
        by_sero[meta["serotype"]].append((meta, seq))

    # Slice VP1 out of the long records, one blastn run per serotype, against
    # every checked VP1 record of that serotype plus its panel genomes' VP1.
    for sero, long_items in sorted(long_by_sero.items()):
        vp1_set = [(n, s) for n, s in panel_seqs if serotype_of(n) == sero]
        vp1_set += [(safe_name(m), s) for m, s in by_sero.get(sero, [])]
        sliced = slice_vp1(sero, long_items, vp1_set, blastn, a.threads, a.min_length, a.max_length, skipped)
        _err(f"vp1_context: {sero:6s} sliced VP1 from {len(sliced)} of {len(long_items)} long records "
             f"({len(vp1_set)} VP1 templates)")
        by_sero[sero].extend(sliced)

    out_dir = lib / "vp1_context"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Dict] = {}
    seen: Dict[str, str] = {}  # safe name -> serotype file, across every file
    region_counts: collections.Counter = collections.Counter()
    header_forms: collections.Counter = collections.Counter()

    for sero, items in sorted(by_sero.items()):
        if a.max_per_lineage:
            kept: List[Record] = []
            per: Dict[str, List[Record]] = collections.defaultdict(list)
            for meta, seq in items:
                per[meta["topotype"] or "NA"].append((meta, seq))
            for group in per.values():
                group.sort(key=lambda ms: (-len(ms[1]), ms[0]["accession"]))
                kept.extend(group[: a.max_per_lineage])
            items = kept

        fasta_lines: List[str] = []
        rows: List[List[str]] = []
        for meta, seq in sorted(items, key=lambda ms: ms[0]["accession"]):
            name = safe_name(meta)
            if name in seen:
                # Same accession twice: keep the first, count it, never overwrite.
                skipped["duplicate name"] += 1
                continue
            seen[name] = sero
            region_counts[meta["region_source"]] += 1
            header_forms[meta["header_form"]] += 1
            fasta_lines.append(f">{name}\n{seq}\n")
            rows.append([name, meta["serotype"], meta["topotype"], meta["country"], meta["year"],
                         meta["host"], meta["accession"], str(len(seq)), meta["isolate"],
                         meta["region_source"], meta["source_length"], meta["slice"],
                         meta["slice_identity"], meta["slice_template"],
                         meta["vp1_check_panel"], meta["vp1_check_identity"], meta["vp1_check_coverage"]])

        (out_dir / f"{sero}.fasta").write_text("".join(fasta_lines))
        with open(out_dir / f"{sero}.tsv", "w") as fh:
            fh.write("\t".join(TSV_COLUMNS) + "\n")
            for r in rows:
                fh.write("\t".join(r) + "\n")
        topos = collections.Counter(r[2] or "NA" for r in rows)
        regions = collections.Counter(r[9] for r in rows)
        summary[sero] = {"sequences": len(rows), "topotypes": dict(sorted(topos.items())),
                         "region_source": dict(sorted(regions.items())),
                         "countries": len({r[3] for r in rows if r[3]})}
        _err(f"vp1_context: {sero:6s} {len(rows):5d} sequences ({regions['vp1_record']} VP1 records, "
             f"{len(rows) - regions['vp1_record']} slices), {len(topos)} topotypes, "
             f"{summary[sero]['countries']} countries")

    kept_total = sum(s["sequences"] for s in summary.values())
    (out_dir / "SOURCE.json").write_text(json.dumps({
        "source_file": str(source),
        "source": "NanoFMDV (Shaw et al. 2025) GenBank-derived FMDV collection, shipped with the pipeline",
        "selection": f"records of {a.min_length} to {a.max_length} nt that pass the VP1 check, plus VP1 sliced "
                     f"from longer records; serotype from the record header; accessions already in "
                     f"{panel.name} skipped",
        "panel": str(panel),
        "panel_accessions": len(in_panel),
        "max_per_lineage": a.max_per_lineage or "all",
        "vp1_check": f"blastn -task blastn against {panel.name}: best hit of the record's serotype covering "
                     f"at least {int(MIN_VP1_COVERAGE * 100)}% of the record",
        "slicing": "off" if a.no_slice else "blastn -task blastn against the checked VP1 set, best hit per record, "
                                             "extended to the template's ends",
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_records": total,
        "kept": kept_total,
        "kept_by_region_source": dict(region_counts),
        "kept_by_header_form": dict(header_forms),
        "serotypes": summary,
        "skipped": dict(skipped),
    }, indent=1))
    _err(f"vp1_context: {total} source records, {kept_total} kept {dict(region_counts)}, written to {out_dir}")
    _err(f"vp1_context: skipped {dict(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
