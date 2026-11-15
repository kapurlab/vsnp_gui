#!/usr/bin/env python3
"""Derive defining-SNP groups for one FMDV lineage reference, the MTBC way.

vsnp3 sorts samples into groups with a define_filter workbook: row 1 holds a
group's defining signature, row 2 its name. A signature is one or more
positions ("CHROM:pos, CHROM:pos") that a sample must all carry as SNPs, and
positions suffixed "!" that it must not carry (vsnp3_group_on_defining_snps.py,
group_selection). With `-a` vsnp3 builds the all-samples tree and one tree
per group, so one Step 2 run gives both the global placement and the fine
structure, which is how the MTBC references are set up.

Here the signatures are read off the VCFs under fmdv_refs/panel_build/<ref>/:
every other library genome called against this reference, and, when
fmdv_genome_db.py has run, the GenBank complete genomes of the reference's
serotype (topotype from lib/vp1_context/<SEROTYPE>.tsv). Two tiers:

  serotype groups   one per serotype other than the reference's own, over
                    all 78 genomes: positions where every genome of that
                    serotype carries the SNP and no covered genome of
                    another serotype does;
  topotype groups   one per topotype within the reference's serotype:
                    positions where every genome of that topotype carries
                    the SNP and no other covered genome, of any serotype,
                    does. Genomes whose header gives no topotype are
                    neither members nor counter-examples here (they may
                    belong to the group); they still count for the
                    serotype tier and appear in the all-samples tree.

The reference's own topotype gets no group: a sample of it has no SNP that
others lack, and vsnp3 defines groups by presence. It shows in the
all-samples tree and in the serotype view.

Caveats, written down so nobody mistakes this for a validated scheme: with
one genome per topotype a "topotype" signature is that genome's private
SNPs (a 15 November 2026 Step 2 run with 39 GenBank genomes showed the
panel-only signatures caught 2 of them and none of the seven labelled
topotype III); with several genomes per topotype it is what they share
and nothing else carries, which is the right question but still only as
good as the labels and the sample of genomes. Cross-serotype calls against
an 8 kb reference cover only 38 to 50% of positions (measured 14 November
2026 against SAT1-I), so a serotype signature lives in the conserved half.
Real samples will refine both; the simulation benchmark should score them.

Usage:
    fmdv_define_snps.py --lib ~/fmdv_refs --ref SAT1-I_KM268899 [--max-positions 3] [--panel-only] [--dry-run]
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

ALT_QUAL = 150        # vsnp3's passing-SNP rule (vsnp3_group_on_defining_snps._passing):
MIN_MQ = 56           # QUAL > 150, AC == 2, one-base REF and ALT, MQ >= 56
MIN_SIGNATURE = 1
MAX_POSITIONS = 3


def read_calls(vcf: Path) -> Tuple[str, Set[int], Set[int]]:
    """(chrom, positions that pass vsnp3's SNP rule, positions with any coverage).

    The rule is the one vsnp3 applies when it sorts samples into groups, so a
    signature position here is one vsnp3 will see in the sample: QUAL over
    150, AC equal to 2, single-base REF and ALT, MQ of 56 or more. Reads
    simulated from a genome sit near MQ 54 at some positions, which is why
    a QUAL-only rule chose positions vsnp3 then ignored (15 November 2026).
    """
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
            info: Dict[str, str] = {}
            for kv in f[7].split(";"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    info[k] = v
            try:
                dp = int(info.get("DP", "0"))
            except ValueError:
                dp = 0
            if dp > 0:
                covered.add(pos)
            if f[4] in (".", "") or len(f[3]) != 1 or len(f[4]) != 1:
                continue
            try:
                qual = float(f[5]); ac = int(info.get("AC", "0")); mq = float(info.get("MQ", "nan"))
            except ValueError:
                continue
            if qual > ALT_QUAL and ac == 2 and mq >= MIN_MQ:
                alt.add(pos)
    return chrom, alt, covered


def signature(members: List[str], pool: List[str], alt: Dict[str, Set[int]],
              cov: Dict[str, Set[int]], max_positions: int) -> Tuple[List[int], int]:
    """Signature positions and how many members carry all of them.

    A position qualifies when every member covered there carries the SNP, at
    least half the members (and at least one) carry it, and no covered
    non-member of the pool does. Members with no coverage at a position
    abstain: against a lineage reference a topotype 15% away loses a third
    of the genome, and demanding every member be covered left SAT1-II, -III
    and -IV with no signature at all (15 November 2026). The cost is that a
    member uncovered at a chosen position falls out of the group in vsnp3,
    which requires the SNP to be present; the captured count says how many
    members pass. Ranked by members carrying it, then by non-members covered
    there (a position nobody else covers proves nothing)."""
    if not members:
        return [], 0
    others = [g for g in pool if g not in members]
    need = max(1, (len(members) + 1) // 2)
    candidates = set.union(*(alt[m] for m in members))
    scored = []
    for p in candidates:
        n_alt = sum(1 for m in members if p in alt[m])
        if n_alt < need or any(p in cov[m] and p not in alt[m] for m in members):
            continue
        if any(p in alt[o] for o in others):
            continue
        n_cov = sum(1 for o in others if p in cov[o])
        if n_cov == 0:
            continue
        scored.append((n_alt, n_cov, p))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    # vsnp3 requires every signature position, so a second position may only
    # be added when every member captured so far carries it too: positions
    # strengthen the signature, they never shrink the group.
    chosen: List[int] = []
    captured_set: Set[str] = set()
    for _a, _c, p in scored:
        if not chosen:
            chosen, captured_set = [p], {m for m in members if p in alt[m]}
        elif all(p in alt[m] for m in captured_set):
            chosen.append(p)
        if len(chosen) >= max_positions:
            break
    return chosen, len(captured_set)


def group_name(serotype: str, topotype: str) -> str:
    """The group label vsnp3 will accept for one topotype.

    vsnp3 makes a directory named after each group and writes that group's
    FASTA and tree into it, so a group name is a path component. FMDV
    topotypes carry slashes outside the SAT serotypes, ASIA/G-IX and
    ME-SA/PanAsia-2 among them, and vsnp3 then tries to open
    "ASIA1-ASIA/G-IX/ASIA1-ASIA/G-IX-<stamp>.fasta" in a directory that does
    not exist and the whole Step 2 run dies after the trees are built. Slashes
    become dashes here; the topotype is still readable and the label is still
    unique.
    """
    return f"{serotype}-{topotype}".replace("/", "-")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True)
    ap.add_argument("--ref", required=True, help="safe name of the lineage reference")
    ap.add_argument("--max-positions", type=int, default=MAX_POSITIONS)
    ap.add_argument("--panel-only", action="store_true", help="ignore the GenBank genome VCFs")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    lib = Path(a.lib).expanduser().resolve()
    idx = fmdv_reflib.load(lib)
    by_safe = {g["safe"]: g for g in idx["genomes"]}
    if a.ref not in by_safe:
        raise SystemExit(f"{a.ref} is not in the library")
    ref_sero = by_safe[a.ref]["serotype"]

    # serotype and topotype of every genome that may have a VCF here: the
    # panel from lineages.json, the GenBank genomes from the context TSV
    # (empty topotype = unlabelled).
    meta: Dict[str, Tuple[str, str]] = {g: (d["serotype"], d["lineage"] or "") for g, d in by_safe.items()}
    genbank: Set[str] = set()
    tsv = lib / "vp1_context" / f"{ref_sero}.tsv"
    if not a.panel_only and tsv.is_file():
        with open(tsv) as fh:
            header = fh.readline().rstrip("\n").split("\t")
            for line in fh:
                r = dict(zip(header, line.rstrip("\n").split("\t")))
                if r.get("region_source") == "sliced_from_genome" and r["name"] not in meta:
                    meta[r["name"]] = (ref_sero, r.get("topotype", ""))
                    genbank.add(r["name"])

    alt: Dict[str, Set[int]] = {}
    cov: Dict[str, Set[int]] = {}
    chrom = ""
    for v in sorted(glob.glob(str(lib / "panel_build" / a.ref / "*" / f"alignment_{a.ref}" / "*_zc.vcf"))):
        g = Path(v).name[: -len("_zc.vcf")]
        if g not in meta:
            continue
        chrom, alt[g], cov[g] = read_calls(Path(v))
    if len(alt) < 10:
        raise SystemExit(f"only {len(alt)} VCFs under panel_build/{a.ref}; build them first")
    pool_all = sorted(alt)
    n_genbank = sum(1 for g in pool_all if g in genbank)
    print(f"{len(pool_all)} genomes with VCFs: {len(pool_all) - n_genbank} panel, {n_genbank} GenBank ({ref_sero})")
    groups: List[Tuple[str, List[int], int, int]] = []   # name, positions, members, captured

    # Serotype groups over everything.
    for sero in sorted({meta[g][0] for g in pool_all}):
        if sero == ref_sero:
            continue
        members = [g for g in pool_all if meta[g][0] == sero]
        sig, captured = signature(members, pool_all, alt, cov, a.max_positions)
        groups.append((sero, sig, len(members), captured))

    # Topotype groups within the reference's serotype. Unlabelled genomes
    # are left out of the pool here: they are not members, and they must not
    # count as counter-examples either, since they may belong to the group.
    same = [g for g in pool_all if meta[g][0] == ref_sero]
    by_topo: Dict[str, List[str]] = defaultdict(list)
    for g in same:
        if meta[g][1]:
            by_topo[meta[g][1]].append(g)
    unlabelled = [g for g in same if not meta[g][1]]
    pool_labelled = [g for g in pool_all if g not in unlabelled]
    # Uniqueness is checked against ALL panel genomes, not just the serotype:
    # a topotype's private SNPs within SAT1 turned out to be shared with SAT2
    # and SAT3 genomes in the conserved half (14 November 2026: SAT1-II, -IV
    # and -XI each captured one foreign genome in a 78-genome run).
    ref_topo = meta[a.ref][1]
    for topo in sorted(by_topo):
        if topo == ref_topo:
            # Presence-based grouping cannot define the reference's own
            # topotype: a sample close to the reference carries no SNP that
            # others lack, and a signature from the other genomes of the
            # topotype would leave such a sample out of its own group.
            print(f"{group_name(ref_sero, topo)} is the reference's own topotype ({len(by_topo[topo])} other genomes): no group; "
                  f"it shows in the all-samples tree")
            continue
        members = by_topo[topo]
        sig, captured = signature(members, pool_labelled, alt, cov, a.max_positions)
        groups.append((group_name(ref_sero, topo), sig, len(members), captured))
    if unlabelled:
        print(f"{len(unlabelled)} {ref_sero} genomes without a topotype label, used for the serotype tier only")

    print(f"{'group':<14} {'members':>7} {'captured':>8}  signature")
    kept = []
    for name, sig, n, captured in groups:
        ok = len(sig) >= MIN_SIGNATURE
        print(f"{name:<14} {n:7d} {captured:8d}  " + (", ".join(f"{chrom}:{p}" for p in sig) if sig else "(none found)")
              + ("" if ok else "  SKIPPED"))
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
        {"reference": a.ref, "chrom": chrom, "alt_qual": ALT_QUAL, "min_mq": MIN_MQ, "max_positions": a.max_positions,
         "groups": [{"name": n, "positions": s,
                     "members": next(m for gname, _s, m, _c in groups if gname == n),
                     "members_captured": next(c for gname, _s, _m, c in groups if gname == n)}
                    for n, s in kept],
         "genomes_with_vcfs": len(alt), "panel_genomes": len(alt) - n_genbank, "genbank_genomes": n_genbank,
         "unlabelled_same_serotype": unlabelled}, indent=1))
    print(f"wrote {out} with {len(kept)} groups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
