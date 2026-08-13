"""Step 2 run-folder staging.

Each Step 2 run works on COPIES of the cumulative step2/vcf_database in a
dated run folder (vsnp3 deletes every VCF out of its -wd, so the database
itself must never be handed to it). Staging used to copy the WHOLE database
and rely on vsnp3's -remove_by_name to drop the excluded samples — but
vsnp3_step2.py parses every VCF in its -wd into a dataframe BEFORE applying
the removal list, so a 10-sample comparison against a 9,372-VCF database
copied and parsed all 9,372 files. Staging now skips any file the removal
list would drop anyway.

vsnp3's matching rule (vsnp3_step2.py + Remove_From_Analysis, read from the
deployed install): dataframes are keyed by VCF basename, and a listed name N
removes the keys ``N``, ``N.vcf`` and ``N_zc.vcf`` — nothing else (a .vcf.gz
key can never match). vsnp3_would_remove() mirrors that EXACTLY, and the run
still passes -remove_by_name with the full list, so if the mirror ever
disagreed with vsnp3 the stray file would still be removed by vsnp3 itself:
a mismatch can only cost time, never correctness.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable, Set, Tuple


def vsnp3_would_remove(vcf_basename: str, removal_names: Set[str]) -> bool:
    """True when vsnp3's -remove_by_name would drop this staged file."""
    if vcf_basename in removal_names:
        return True
    if vcf_basename.endswith(".vcf"):
        stem = vcf_basename[: -len(".vcf")]
        if stem in removal_names:
            return True
        if stem.endswith("_zc") and stem[: -len("_zc")] in removal_names:
            return True
    return False


def stage_step2_vcfs(
    vcf_source_dir: Path,
    run_dir: Path,
    removal_names: Iterable[str],
) -> Tuple[int, int, Set[str]]:
    """Copy the VCFs this run will analyze from the database into run_dir.

    Returns (copied, skipped_excluded, staged_basenames). copy2 follows
    symlinks, so the real VCF content (the DB entries are symlinks into
    step1) lands in the run folder as regular files. Raises OSError with the
    failing filename attached for the endpoint to surface.
    """
    removal_set = set(removal_names)
    copied = 0
    skipped = 0
    staged: Set[str] = set()
    for src in sorted([*vcf_source_dir.glob("*.vcf"), *vcf_source_dir.glob("*.vcf.gz")]):
        if vsnp3_would_remove(src.name, removal_set):
            skipped += 1
            continue
        shutil.copy2(src, run_dir / src.name)
        copied += 1
        staged.add(src.name)
    return copied, skipped, staged
