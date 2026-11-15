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

So no database query is involved: the records are read, filtered to VP1
length, renamed to the fork's safe form and written per serotype, with a
provenance TSV beside each file carrying every field as the source states
it. Nothing is inferred and no accession is invented.

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
import sys
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

VP1_MIN, VP1_MAX = 600, 700
SAFE = re.compile(r"[^A-Za-z0-9._-]+")
# GenBank nucleotide accession, with or without a version: KC440883, LC770123.1, NC_039210.1
ACCESSION = re.compile(r"^([A-Z]{1,2}_?\d{5,8})(\.\d+)?$")

FIELDS = ["serotype", "topotype", "country", "year", "host", "accession", "length", "isolate"]


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


def panel_accessions(panel: Path) -> set:
    """Accessions in the lineage panel, from the _<accession> tail of each safe name."""
    out = set()
    if not panel.is_file():
        return out
    for header, _ in read_records(panel):
        name = header.split()[0]
        if "_" in name:
            out.add(bare_accession(name.rsplit("_", 1)[1]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="NanoFMDV Refs/BLAST/fmdv-gbbase.fasta")
    ap.add_argument("--lib", required=True, help="FMDV reference library (writes vp1_context/ under it)")
    ap.add_argument("--panel", help="lineage panel whose accessions are skipped (default lib/panels/vp1.fasta)")
    ap.add_argument("--serotype", action="append", help="limit to these serotypes (repeatable)")
    ap.add_argument("--max-per-lineage", type=int, default=0,
                    help="cap sequences per topotype, longest first (0 = keep all). "
                         "O and A hold thousands; a tree does not need them all.")
    ap.add_argument("--min-length", type=int, default=VP1_MIN)
    ap.add_argument("--max-length", type=int, default=VP1_MAX)
    a = ap.parse_args()

    source, lib = Path(a.source).expanduser(), Path(a.lib).expanduser()
    if not source.is_file():
        _err(f"vp1_context: no such file: {source}")
        return 2
    panel = Path(a.panel).expanduser() if a.panel else lib / "panels" / "vp1.fasta"
    in_panel = panel_accessions(panel)
    want = {s.upper() for s in (a.serotype or [])}

    by_sero: Dict[str, List[Tuple[Dict[str, str], str]]] = collections.defaultdict(list)
    skipped: collections.Counter = collections.Counter()
    header_forms: collections.Counter = collections.Counter()
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
        if not (a.min_length <= len(seq) <= a.max_length):
            skipped["not VP1 length"] += 1
            continue
        header_forms[meta["header_form"]] += 1
        by_sero[meta["serotype"]].append((meta, seq.upper()))

    out_dir = lib / "vp1_context"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Dict] = {}
    seen: Dict[str, str] = {}  # safe name -> serotype file, across every file

    for sero, items in sorted(by_sero.items()):
        if a.max_per_lineage:
            kept: List[Tuple[Dict[str, str], str]] = []
            per: Dict[str, List[Tuple[Dict[str, str], str]]] = collections.defaultdict(list)
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
            fasta_lines.append(f">{name}\n{seq}\n")
            rows.append([name, meta["serotype"], meta["topotype"], meta["country"],
                         meta["year"], meta["host"], meta["accession"], str(len(seq)), meta["isolate"]])

        (out_dir / f"{sero}.fasta").write_text("".join(fasta_lines))
        with open(out_dir / f"{sero}.tsv", "w") as fh:
            fh.write("name\tserotype\ttopotype\tcountry\tyear\thost\taccession\tlength\tisolate\n")
            for r in rows:
                fh.write("\t".join(r) + "\n")
        topos = collections.Counter(r[2] or "NA" for r in rows)
        summary[sero] = {"sequences": len(rows), "topotypes": dict(sorted(topos.items())),
                         "countries": len({r[3] for r in rows if r[3]})}
        _err(f"vp1_context: {sero:6s} {len(rows):5d} sequences, {len(topos)} topotypes, "
             f"{summary[sero]['countries']} countries")

    (out_dir / "SOURCE.json").write_text(json.dumps({
        "source_file": str(source),
        "source": "NanoFMDV (Shaw et al. 2025) GenBank-derived FMDV collection, shipped with the pipeline",
        "selection": f"VP1-length records ({a.min_length} to {a.max_length} nt), serotype from the record header; "
                     f"accessions already in {panel.name} skipped",
        "panel": str(panel),
        "panel_accessions": len(in_panel),
        "max_per_lineage": a.max_per_lineage or "all",
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source_records": total,
        "kept": sum(s["sequences"] for s in summary.values()),
        "kept_by_header_form": dict(header_forms),
        "serotypes": summary,
        "skipped": dict(skipped),
    }, indent=1))
    _err(f"vp1_context: {total} source records, {sum(s['sequences'] for s in summary.values())} kept "
         f"({dict(header_forms)}), written to {out_dir}")
    _err(f"vp1_context: skipped {dict(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
