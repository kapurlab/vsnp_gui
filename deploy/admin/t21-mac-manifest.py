#!/usr/bin/env python3
"""
T-21 Mac project migration — Phase 1: manifest only

Walks /Users/vivekkapur/vsnp3/projects/ (or any directory passed as -r),
classifies each subdirectory as KEEP / SKIP / UNSURE based on heuristics,
and writes a TSV you review + edit before Phase 2 actually rsyncs anything.

Usage
-----
    python3 t21-mac-manifest.py
    python3 t21-mac-manifest.py -r /Users/vivekkapur/vsnp3/projects -o /tmp/t21-manifest.tsv

After running, open the TSV in Excel/Numbers, change the `decision` column
to KEEP or DROP per row, and pass the edited TSV back to the migration
step (T-21 Phase 2).

Classification heuristics (initial guess; you override in the TSV)
-----------------------------------------------------------------
KEEP    : has step1/<sample>/.../_zc.vcf — Step 1 ran successfully on real data.
UNSURE  : has FASTQ but no Step 1 outputs, OR has Step 2 outputs but no Step 1.
SKIP    : empty directory, no FASTQ, or name screams "throwaway"
          (test, test*, throwaway, demo).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_ROOT = Path("/Users/vivekkapur/vsnp3/projects")

THROWAWAY_PATTERNS = [
    re.compile(r"^test\d*$", re.IGNORECASE),
    re.compile(r"^throwaway", re.IGNORECASE),
    re.compile(r"^demo", re.IGNORECASE),
    re.compile(r"^scratch", re.IGNORECASE),
    re.compile(r"^foo|^bar|^baz", re.IGNORECASE),
    re.compile(r"^tmp", re.IGNORECASE),
]


def looks_throwaway(name: str) -> bool:
    return any(p.match(name) for p in THROWAWAY_PATTERNS)


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def safe_iter(path: Path):
    try:
        return path.iterdir()
    except (PermissionError, OSError):
        return iter([])


def total_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda _e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def count_files(path: Path, glob_pat: str) -> int:
    try:
        return sum(1 for _ in path.glob(glob_pat))
    except (PermissionError, OSError):
        return 0


def find_reference(project: Path) -> Optional[str]:
    meta = project / "project.json"
    if meta.exists():
        try:
            data = json.loads(meta.read_text())
            ref = data.get("reference")
            if ref:
                return ref
        except (json.JSONDecodeError, OSError):
            pass
    # Fall back: peek at the first zc.vcf header for ##reference=
    for vcf in project.glob("step1/*/alignment_*/*_zc.vcf"):
        try:
            with vcf.open() as f:
                for _ in range(50):
                    line = f.readline()
                    if not line:
                        break
                    if line.startswith("##reference="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            pass
        break
    return None


def classify(project: Path) -> Tuple[str, str]:
    """Return (decision, notes)."""
    name = project.name
    if looks_throwaway(name):
        return "SKIP", "name matches throwaway pattern"

    download = project / "download"
    step1 = project / "step1"
    step2 = project / "step2"

    fastq_count = count_files(download, "**/*.fastq.gz") if download.exists() else 0
    zc_vcf_count = count_files(step1, "**/*_zc.vcf") if step1.exists() else 0
    step2_html = count_files(step2, "*.html") if step2.exists() else 0

    if zc_vcf_count > 0:
        return "KEEP", f"{zc_vcf_count} zc.vcf in step1"

    if fastq_count > 0:
        return "UNSURE", f"{fastq_count} fastq.gz, no Step 1 outputs"

    if step2_html > 0:
        return "UNSURE", f"Step 2 outputs but no Step 1; orphaned?"

    return "SKIP", "empty (no fastq, no step1, no step2)"


def scan(root: Path) -> List[Dict]:
    rows: List[Dict] = []
    if not root.exists():
        sys.exit(f"error: {root} does not exist")
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith("."):
            continue
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        except OSError:
            mtime = "?"

        size = total_size(p)
        fastq = count_files(p / "download", "**/*.fastq.gz") if (p / "download").exists() else 0
        step1_samples = sum(1 for d in safe_iter(p / "step1") if d.is_dir()) if (p / "step1").exists() else 0
        zc_vcf = count_files(p / "step1", "**/*_zc.vcf") if (p / "step1").exists() else 0
        step2_html = count_files(p / "step2", "*.html") if (p / "step2").exists() else 0
        ref = find_reference(p)
        decision, notes = classify(p)

        rows.append({
            "decision": decision,
            "project": p.name,
            "mtime": mtime,
            "size": human_bytes(size),
            "size_bytes": size,
            "fastq": fastq,
            "step1_samples": step1_samples,
            "zc_vcf": zc_vcf,
            "step2_html": step2_html,
            "reference": ref or "?",
            "notes": notes,
            "path": str(p),
        })
    return rows


def write_tsv(rows: List[Dict], out: Path) -> None:
    fields = [
        "decision", "project", "mtime", "size", "size_bytes",
        "fastq", "step1_samples", "zc_vcf", "step2_html",
        "reference", "notes", "path",
    ]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def print_summary(rows: List[Dict]) -> None:
    counts: Dict[str, int] = {}
    total_bytes = 0
    keep_bytes = 0
    for r in rows:
        counts[r["decision"]] = counts.get(r["decision"], 0) + 1
        total_bytes += r["size_bytes"]
        if r["decision"] == "KEEP":
            keep_bytes += r["size_bytes"]

    print(f"\nScanned {len(rows)} projects, {human_bytes(total_bytes)} total\n")
    print(f"  Initial classification (you review and edit):")
    for k in ("KEEP", "UNSURE", "SKIP"):
        print(f"    {k:<8} {counts.get(k, 0):>3}")
    print(f"\n  Estimated migration size if you accept all KEEP: {human_bytes(keep_bytes)}\n")


def main() -> int:
    p = argparse.ArgumentParser(description="T-21 Phase 1: scan Mac projects, write a manifest.")
    p.add_argument("-r", "--root", default=str(DEFAULT_ROOT),
                   help=f"Project root (default: {DEFAULT_ROOT})")
    p.add_argument("-o", "--out", default="/tmp/t21-manifest.tsv",
                   help="Output TSV (default: /tmp/t21-manifest.tsv)")
    args = p.parse_args()

    root = Path(args.root)
    out = Path(args.out)

    rows = scan(root)
    write_tsv(rows, out)
    print_summary(rows)

    print(f"Wrote {out}")
    print()
    print("Next steps")
    print("----------")
    print("1. Open the TSV in Excel / Numbers (it's tab-separated):")
    print(f"     open '{out}'")
    print("2. For each row, set the `decision` column to KEEP or DROP.")
    print("   (UNSURE rows are safest to default to DROP unless you recognize them.)")
    print("3. Save back as TSV.")
    print("4. Hand the edited TSV back for Phase 2 — the actual rsync to wgs3.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
