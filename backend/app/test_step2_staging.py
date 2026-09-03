"""Unit tests for step2_staging — the skip-at-copy staging of Step 2 runs.

The predicate must mirror vsnp3's Remove_From_Analysis EXACTLY (a listed
name N removes basenames N, N.vcf and N_zc.vcf; nothing else — notably a
.vcf.gz basename can never match).

This file used to assert that an unremovable .vcf.gz "must not be skipped
either (identical analysis set)". That premise was false and it is the bug
this suite now guards: vsnp3 discovers its inputs with glob('*vcf'), so a
staged .vcf.gz is not analysed at all. It was copied, counted as compared,
and silently absent from the matrix. Staging now decompresses instead, and
the assertions below say so.

Run directly:  python test_step2_staging.py
"""

from __future__ import annotations

import gzip
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.step2_staging import removals_that_bite, stage_step2_vcfs, vsnp3_would_remove


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
    # vsnp3's candidates never end in .gz. That is why a gzipped entry must be
    # decompressed on the way into the run folder rather than copied: no tier
    # could ever remove it, and vsnp3 would never read it.
    assert_eq(vsnp3_would_remove("Y_zc.vcf.gz", {"Y"}), False, ".vcf.gz never matches")

    print("\n[stage_step2_vcfs]")
    tmp = Path(tempfile.mkdtemp(prefix="step2_staging_"))
    try:
        src = tmp / "vcf_database"
        run = tmp / "run"
        src.mkdir()
        run.mkdir()
        for name in ("a_zc.vcf", "b_zc.vcf", "c.vcf"):
            (src / name).write_text(f"##{name}\n")
        with gzip.open(src / "d_zc.vcf.gz", "wt") as fh:
            fh.write("##d_zc.vcf.gz\n")
        # Legacy denylist path (an older frontend that sends no allow-list).
        copied, skipped, staged = stage_step2_vcfs(src, run, ["b", "d", "not_in_db"])
        assert_eq(copied, 3, "copied everything the run keeps")
        assert_eq(skipped, 1, "skipped exactly the excluded .vcf")
        assert_eq(staged, {"a_zc.vcf", "c.vcf", "d_zc.vcf"},
                  "staged basenames — d arrives DECOMPRESSED, under the name vsnp3 globs")
        assert_eq(sorted(p.name for p in run.iterdir()), sorted(staged),
                  "run dir holds exactly the staged files, none of them gzipped")
        assert_eq((run / "d_zc.vcf").read_text(), "##d_zc.vcf.gz\n",
                  "and holds the real decompressed content")
        assert_eq(sorted(p.name for p in src.iterdir()),
                  ["a_zc.vcf", "b_zc.vcf", "c.vcf", "d_zc.vcf.gz"],
                  "database untouched")

        print("\n[stage_step2_vcfs — allow-list]")
        run2 = tmp / "run2"
        run2.mkdir()
        # The reported bug: c.vcf carries no _zc marker, so the selection UI
        # could not name it and the denylist above could never exclude it.
        copied, skipped, staged = stage_step2_vcfs(src, run2, include_samples=["a"])
        assert_eq(copied, 1, "only the named sample is staged")
        assert_eq(staged, {"a_zc.vcf"}, "and it is the one that was named")
        assert_eq(sorted(p.name for p in run2.iterdir()), ["a_zc.vcf"],
                  "the unnameable c.vcf does not join the run")
        print("\n[removals_that_bite — a run folder's removal list is its own]")
        # The database-wide set an allow-list run computes: everything the user
        # did not tick. Written out verbatim it named every other isolate in
        # the project, inside a folder comparing one.
        database_wide = ["b", "c", "d", "zz-not-in-this-project"]
        assert_eq(removals_that_bite(["a_zc.vcf"], database_wide), [],
                  "nothing in the database-wide list can touch this run — so nothing is kept")
        assert_eq(removals_that_bite(["a_zc.vcf", "b_zc.vcf", "c.vcf"], database_wide),
                  ["b", "c"],
                  "only names that can drop a staged file survive")
        assert_eq(removals_that_bite(["b_zc.vcf"], ["b_zc.vcf", "b_zc", "b"]),
                  ["b", "b_zc", "b_zc.vcf"],
                  "every spelling vsnp3 would match is kept, and no other")
        # The narrowing must not change what vsnp3 does: a name that survives
        # removes, a name that was dropped could not have removed anything.
        for name in database_wide:
            assert_eq(vsnp3_would_remove("a_zc.vcf", {name}), False,
                      f"{name} was correctly discarded — it cannot drop a_zc.vcf")

        print("\nAll step2 staging tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
