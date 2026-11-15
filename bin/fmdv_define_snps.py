#!/usr/bin/env python3
"""Derive defining-SNP groups for one FMDV lineage reference, the MTBC way.

vsnp3 sorts samples into groups with a define_filter workbook: row 1 holds a
group's defining signature, row 2 its name. A signature is one or more
positions ("CHROM:pos, CHROM:pos") that a sample must all carry as SNPs, and
positions suffixed "!" that it must not carry (vsnp3_group_on_defining_snps.py,
group_selection). With `-a` vsnp3 builds the all-samples tree and one tree
per group, so one Step 2 run gives both the global placement and the fine
structure, which is how the MTBC references are set up.

Here the signatures are read off the panel VCFs: every other library genome
called against this reference (fmdv_refs/panel_build/<ref>/). Two tiers:

  serotype groups   one per serotype other than the reference's own, over
                    all 78 genomes: positions where every genome of that
                    serotype carries the SNP and no covered genome of
                    another serotype does;
  topotype groups   one per topotype within the reference's serotype:
                    positions where the topotype's genome carries the SNP and
                    no other covered genome, of any serotype, does.

The reference's own topotype gets no group: a sample of it has no SNP that
others lack, and vsnp3 defines groups by presence. It shows in the
all-samples tree and in the serotype view.

Caveats, written down so nobody mistakes this for a validated scheme: one
genome per topotype means a "topotype" signature is really that genome's
private SNPs, and cross-serotype calls against an 8 kb reference cover only
38 to 50% of positions (measured 14 November 2026 against SAT1-I), so a
serotype signature lives in the conserved half. Real samples will refine
both; the simulation benchmark should score them.

Usage:
    fmdv_define_snps.py --lib ~/fmdv_refs --ref SAT1-I_KM268899 [--max-positions 3] [--dry-run]
Writes reference_options/<ref>/<ref>_define_filter.xlsx (sheet "all",
column A "name-All") and prints the groups.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fmdv_reflib  # noqa: E402

ALT_QUAL = 150        # vsnp3's ALT rule: QUAL > 150 and not AC=1
MIN_SIGNATURE = 1
MAX_POSITIONS = 3


def read_calls(vcf: Path) -> Tuple[str, Set[int], Set[int]]:
    """(chrom, positions called ALT, positions with any coverage)."""
    alt: Set[int] = set()
    covered: Set[int] = set()
    chrom = ""
    with open(vcf) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            f = line.rstrip("\n").split("\t")
            if len(f) < 8:
                continue
            chrom = f[0]
            pos = int(f[1])
            info = f[7]
            dp = 0
            for kv in info.split(";"):
                if kv.startswith("DP="):
                    try:
                        dp = int(kv[3:])
                    except ValueError:
                        dp = 0
            if dp > 0:
                covered.add(pos)
            if f[4] not in (".", "") and "AC=1;" not in info + ";" and float(f[5] or 0) > ALT_QUAL:
                alt.add(pos)
    return chrom, alt, covered


def signature(members: List[str], pool: List[str], alt: Dict[str, Set[int]],
              cov: Dict[str, Set[int]], max_positions: int) -> List[int]:
    """Positions ALT in every member and in no covered non-member of the
    pool, ranked by how many non-members are covered there (a position
    nobody else covers proves nothing)."""
    others = [g for g in pool if g not in members]
    shared = set.intersection(*(alt[m] for m in members)) if members else set()
    scored = []
    for p in shared:
        if any(p in alt[o] for o in others):
            continue
        n_cov = sum(1 for o in others if p in cov[o])
        if n_cov == 0:
            continue
        scored.append((n_cov, p))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [p for _n, p in scored[:max_positions]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--ref", required=True, help="safe name of the lineage reference")
    ap.add_argument("--max-positions", type=int, default=MAX_POSITIONS)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    lib = Path(a.lib).expanduser().resolve()
    idx = fmdv_reflib.load(lib)
    by_safe = {g["safe"]: g for g in idx["genomes"]}
    if a.ref not in by_safe:
        raise SystemExit(f"{a.ref} is not in the library")
    ref_sero = by_safe[a.ref]["serotype"]

    alt: Dict[str, Set[int]] = {}
    cov: Dict[str, Set[int]] = {}
    chrom = ""
    for v in sorted(glob.glob(str(lib / "panel_build" / a.ref / "*" / f"alignment_{a.ref}" / "*_zc.vcf"))):
        g = Path(v).name[: -len("_zc.vcf")]
        if g not in by_safe:
            continue
        chrom, alt[g], cov[g] = read_calls(Path(v))
    if len(alt) < 10:
        raise SystemExit(f"only {len(alt)} panel VCFs under panel_build/{a.ref}; build them first")
    pool_all = sorted(alt)
    groups: List[Tuple[str, List[int], int]] = []   # name, positions, members

    # Serotype groups over everything.
    for sero in sorted({by_safe[g]["serotype"] for g in pool_all}):
        if sero == ref_sero:
            continue
        members = [g for g in pool_all if by_safe[g]["serotype"] == sero]
        sig = signature(members, pool_all, alt, cov, a.max_positions)
        groups.append((sero, sig, len(members)))

    # Topotype groups within the reference's serotype.
    same = [g for g in pool_all if by_safe[g]["serotype"] == ref_sero]
    by_topo: Dict[str, List[str]] = defaultdict(list)
    for g in same:
        by_topo[by_safe[g]["lineage"] or "NA"].append(g)
    # Uniqueness is checked against ALL panel genomes, not just the serotype:
    # a topotype's private SNPs within SAT1 turned out to be shared with SAT2
    # and SAT3 genomes in the conserved half (14 November 2026: SAT1-II, -IV
    # and -XI each captured one foreign genome in a 78-genome run).
    for topo in sorted(by_topo):
        members = by_topo[topo]
        sig = signature(members, pool_all, alt, cov, a.max_positions)
        groups.append((f"{ref_sero}-{topo}", sig, len(members)))

    print(f"{'group':<14} {'members':>7}  signature")
    kept = []
    for name, sig, n in groups:
        ok = len(sig) >= MIN_SIGNATURE
        print(f"{name:<14} {n:7d}  " + (", ".join(f"{chrom}:{p}" for p in sig) if sig else "(none found)") + ("" if ok else "  SKIPPED"))
        if ok:
            kept.append((name, sig))
    if a.dry_run:
        return 0

    folder = fmdv_reflib.materialize(lib, a.ref)
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "all"
    ws.cell(row=2, column=1, value="name-All")
    for i, (name, sig) in enumerate(kept, start=2):
        ws.cell(row=1, column=i, value=", ".join(f"{chrom}:{p}" for p in sig))
        ws.cell(row=2, column=i, value=name)
    out = folder / f"{a.ref}_define_filter.xlsx"
    wb.save(out)
    (folder / "DEFINING_SNPS.json").write_text(json.dumps(
        {"reference": a.ref, "chrom": chrom, "alt_qual": ALT_QUAL, "max_positions": a.max_positions,
         "groups": [{"name": n, "positions": s} for n, s in kept],
         "panel_genomes": len(alt)}, indent=1))
    print(f"wrote {out} with {len(kept)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
