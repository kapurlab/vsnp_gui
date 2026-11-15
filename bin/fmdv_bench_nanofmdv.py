#!/usr/bin/env python3
"""Compare this fork's per-sample results with a NanoFMDV run on the same reads.

For each sample present in both: the reference each chose, NanoFMDV's BLAST
lineage of its polished consensus, and a pairwise comparison of the two
consensus sequences (mafft, two sequences): positions both call as a base
that differ, and NanoFMDV's insertions and deletions relative to ours (ours
has none by construction). The same over the VP1 window, taken from our
fmdv_consensus.json coordinates and carried through the alignment columns.

Usage:
  fmdv_bench_nanofmdv.py --project <dir> --nanofmdv <compare/analysis dir> [--out table.tsv]

NanoFMDV's folder layout per sample: <s>/<s>_fmdv_consensus.fasta,
<s>_fmdv_stats.csv (SelectedRef, TopConsensusBLAST), top_ref.fasta.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def read_fasta_one(path: Path) -> str:
    seq = []
    with open(path) as fh:
        for line in fh:
            if not line.startswith(">"):
                seq.append(line.strip())
    return "".join(seq).upper()


def align_pair(a: str, b: str, mafft: str) -> Tuple[str, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".fasta", delete=False) as tmp:
        tmp.write(f">a\n{a}\n>b\n{b}\n")
        path = tmp.name
    p = subprocess.run([mafft, "--quiet", "--auto", path], capture_output=True, text=True)
    Path(path).unlink(missing_ok=True)
    seqs: Dict[str, List[str]] = {}
    cur = ""
    for line in p.stdout.splitlines():
        if line.startswith(">"):
            cur = line[1:].strip()
            seqs[cur] = []
        else:
            seqs[cur].append(line.strip())
    return "".join(seqs.get("a", [])).upper(), "".join(seqs.get("b", [])).upper()


def compare(ours: str, theirs: str, mafft: str, window: Optional[Tuple[int, int]] = None) -> Dict:
    """Differences between two consensus sequences over an optional window
    given in OUR coordinates (1-based, closed)."""
    a, b = align_pair(ours, theirs, mafft)
    pos = 0
    both = diff = ins = dele = theirs_only = ours_only = 0
    for ca, cb in zip(a, b):
        if ca != "-":
            pos += 1
        inside = window is None or (ca != "-" and window[0] <= pos <= window[1])
        if not inside:
            continue
        if ca == "-":
            if cb not in "-N":
                ins += 1            # NanoFMDV has a base we do not
            continue
        if cb == "-":
            if ca != "N":
                dele += 1           # NanoFMDV lacks a base we have
            continue
        if ca == "N" and cb != "N":
            theirs_only += 1
        elif cb == "N" and ca != "N":
            ours_only += 1
        elif ca != "N" and cb != "N":
            both += 1
            if ca != cb:
                diff += 1
    return {"both_called": both, "substitution_diff": diff, "nanofmdv_insertions": ins,
            "nanofmdv_deletions": dele, "called_only_by_nanofmdv": theirs_only, "called_only_by_us": ours_only}


def nanofmdv_stats(d: Path, sample: str) -> Dict[str, str]:
    p = d / f"{sample}_fmdv_stats.csv"
    if not p.is_file():
        return {}
    rows = list(csv.DictReader(open(p)))
    return rows[0] if rows else {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--nanofmdv", required=True, help="folder holding one NanoFMDV output folder per sample")
    ap.add_argument("--out")
    a = ap.parse_args()
    mafft = shutil.which("mafft")
    if not mafft:
        print("mafft not on PATH (the fork's env)", file=sys.stderr)
        return 2
    project, nano = Path(a.project).expanduser(), Path(a.nanofmdv).expanduser()
    rows: List[Dict] = []
    for d in sorted((project / "step1").iterdir()):
        s = d.name
        ours_fa, ours_js, ref_js = d / f"{s}_consensus.fasta", d / "fmdv_consensus.json", d / "fmdv_reference.json"
        nd = nano / s
        theirs_fa = nd / f"{s}_fmdv_consensus.fasta"
        if not (ours_fa.is_file() and theirs_fa.is_file()):
            continue
        meta = json.loads(ours_js.read_text()) if ours_js.is_file() else {}
        ref = json.loads(ref_js.read_text()) if ref_js.is_file() else {}
        st = nanofmdv_stats(nd, s)
        ours, theirs = read_fasta_one(ours_fa), read_fasta_one(theirs_fa)
        genome = compare(ours, theirs, mafft)
        vp1 = compare(ours, theirs, mafft, (meta["vp1_start"], meta["vp1_end"])) if meta.get("vp1_start") else {}
        their_ref = (st.get("SelectedRef") or "").split("_")[0:2]
        row = {
            "sample": s,
            "our_ref": meta.get("reference", ""),
            "our_identity": ref.get("identity", ""),
            "nanofmdv_ref": st.get("SelectedRef", ""),
            "same_ref": ("yes" if st.get("SelectedRef", "").split("_")[1:2] == meta.get("reference", "").split("_")[1:2] else "no") if st else "",
            "nanofmdv_blast": (st.get("TopConsensusBLAST") or "").split("|")[0],
            "our_called_pct": meta.get("called_percent", ""),
            "nanofmdv_len": len(theirs), "nanofmdv_N": theirs.count("N"),
            "nanofmdv_called_pct": round(100.0 * (len(theirs) - theirs.count("N")) / len(theirs), 2) if theirs else "",
            **{f"g_{k}": v for k, v in genome.items()},
            "vp1_called_pct_ours": meta.get("vp1_called_percent", ""),
            **{f"vp1_{k}": v for k, v in vp1.items()},
        }
        rows.append(row)
    if not rows:
        print("no sample present in both", file=sys.stderr)
        return 1
    cols = list(rows[0].keys())
    out = open(a.out, "w") if a.out else sys.stdout
    w = csv.DictWriter(out, fieldnames=cols, delimiter="\t")
    w.writeheader()
    w.writerows(rows)
    if a.out:
        out.close()
        print(a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
