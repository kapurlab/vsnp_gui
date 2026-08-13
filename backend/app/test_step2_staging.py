"""Unit tests for step2_staging — the skip-at-copy staging of Step 2 runs.

The predicate must mirror vsnp3's Remove_From_Analysis EXACTLY (a listed
name N removes basenames N, N.vcf and N_zc.vcf; nothing else — notably a
.vcf.gz basename can never match). If the mirror under-skips, vsnp3's own
-remove_by_name still drops the file, so only exactness is asserted here.

Run directly:  python test_step2_staging.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from step2_staging import stage_step2_vcfs, vsnp3_would_remove


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def main() -> int:
    print("\n[vsnp3_would_remove mirrors vsnp3]")
    # A set-sample name (file stem minus _zc.vcf) removes its file.
    assert_eq(vsnp3_would_remove("S1_zc.vcf", {"S1"}), True, "N matches N_zc.vcf")
    assert_eq(vsnp3_would_remove("S1_zc.vcf", {"S1_zc"}), True, "N matches N.vcf form")
    assert_eq(vsnp3_would_remove("S1_zc.vcf", {"S1_zc.vcf"}), True, "N matches basename")
    assert_eq(vsnp3_would_remove("X.vcf", {"X"}), True, "plain .vcf by stem")
    # Exact-stem semantics — vsnp3 does NOT match by leading ID, and neither
    # do we: the GUI resolves leading-ID list entries to full set-sample
    # names before they reach the exclude payload.
    assert_eq(vsnp3_would_remove("ERR036186_parsed_reads_zc.vcf", {"ERR036186_parsed_reads"}),
              True, "full sample name removes its file")
    assert_eq(vsnp3_would_remove("ERR036186_parsed_reads_zc.vcf", {"ERR036186"}),
              False, "leading ID alone does not (matches vsnp3)")
    assert_eq(vsnp3_would_remove("S10_zc.vcf", {"S1"}), False, "no prefix fuzz")
    # vsnp3's candidates never end in .gz — a gzipped VCF is not removable by
    # name, so it must not be skipped either (identical analysis set).
    assert_eq(vsnp3_would_remove("Y_zc.vcf.gz", {"Y"}), False, ".vcf.gz never matches")

    print("\n[stage_step2_vcfs]")
    tmp = Path(tempfile.mkdtemp(prefix="step2_staging_"))
    try:
        src = tmp / "vcf_database"
        run = tmp / "run"
        src.mkdir()
        run.mkdir()
        for name in ("a_zc.vcf", "b_zc.vcf", "c.vcf", "d_zc.vcf.gz"):
            (src / name).write_text(f"##{name}\n")
        copied, skipped, staged = stage_step2_vcfs(src, run, ["b", "d", "not_in_db"])
        assert_eq(copied, 3, "copied everything the run keeps")
        assert_eq(skipped, 1, "skipped exactly the excluded .vcf")
        assert_eq(staged, {"a_zc.vcf", "c.vcf", "d_zc.vcf.gz"}, "staged basenames")
        assert_eq(sorted(p.name for p in run.iterdir()), sorted(staged),
                  "run dir holds exactly the staged files")
        assert_eq(sorted(p.name for p in src.iterdir()),
                  ["a_zc.vcf", "b_zc.vcf", "c.vcf", "d_zc.vcf.gz"],
                  "database untouched")
        print("\nAll step2 staging tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
