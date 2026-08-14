"""Unit tests for the project-card counts (app.projects._project_counts).

These numbers drive the Projects list badges, and the scan behind them is the
most expensive thing the backend does on a big project: /api/projects is
re-fetched after most actions in the GUI, and on the 9,364-sample Ames project
the original four-traversals-per-project version stalled the whole app for
minutes at a time. The rewrite is a single traversal, so what has to be pinned
down here is that the NUMBERS did not change — especially the awkward cases:
reads that appear in both download/ and step1/ via symlink (counted once),
the legacy suffix-less alignment/ layout, and the dirs that must be skipped.

Run directly:  python test_project_counts.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import projects
from projects import _project_counts


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def make_project(root: Path) -> Path:
    """A project exercising every counting rule at once."""
    proj = root / "proj"
    download = proj / "download"
    step1 = proj / "step1"
    vcfdb = proj / "step2" / "vcf_database"
    for d in (download, step1, vcfdb):
        d.mkdir(parents=True)

    # Sample A: modern layout, reads in step1, VCF under alignment_<ref>/.
    a = step1 / "A"
    (a / "alignment_MTBC0_v1").mkdir(parents=True)
    (a / "A_R1.fastq.gz").write_text("r1")
    (a / "A_R2.fastq.gz").write_text("r2")
    (a / "alignment_MTBC0_v1" / "A_zc.vcf").write_text("##vcf")
    # vSNP3's unmapped subset is NOT an input read set.
    (a / "A_unmapped_R1.fastq.gz").write_text("junk")

    # Sample B: legacy pre-GUI layout — suffix-less alignment/.
    b = step1 / "B"
    (b / "alignment").mkdir(parents=True)
    (b / "B_R1.fastq.gz").write_text("r1")
    (b / "alignment" / "B_zc.vcf").write_text("##vcf")

    # The provenance writer's sibling and dot-dirs are not samples.
    (step1 / "_provenance").mkdir()
    (step1 / ".hidden").mkdir()

    # download/ holds one real read plus a symlink to sample A's read: the
    # symlinked one is the SAME file and must not be counted twice.
    (download / "C_R1.fastq.gz").write_text("r1")
    (download / "A_R1.fastq.gz").symlink_to(a / "A_R1.fastq.gz")
    # …including one nested a level down (download/ is scanned recursively).
    (download / "batch1").mkdir()
    (download / "batch1" / "D_R1.fastq.gz").write_text("r1")

    # The cumulative VCF store: plain .vcf and gzipped collected VCFs.
    (vcfdb / "A_zc.vcf").write_text("##vcf")
    (vcfdb / "B_zc.vcf").write_text("##vcf")
    (vcfdb / "C_zc.vcf.gz").write_bytes(b"\x1f\x8b")
    (vcfdb / ".vcf_source_manifest.csv").write_text("filename\n")
    return proj


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="project_counts_"))
    try:
        proj = make_project(tmp)

        print("\n[counts on a project using every layout rule]")
        c = _project_counts(proj)
        # A_R1, A_R2, B_R1 (step1) + C_R1, D_R1 (download) = 5 distinct reads.
        # download/A_R1 is a symlink to step1/A/A_R1 -> counted once, and
        # A_unmapped_R1 is excluded entirely.
        assert_eq(c["fastq_count"], 5, "distinct reads (symlink deduped)")
        assert_eq(c["step1_samples"], 2, "sample dirs (_provenance/.hidden skipped)")
        # No step1_vcfs. Producing it meant one extra directory read per sample
        # — 24,000 of them on an influenza project, every time the GUI
        # re-fetched the list — for a number the card could never display: the
        # badge reads `vcfs_count ?? step1_vcfs`, and vcfs_count is set on every
        # path including the error path, so the fallback could not fire.
        assert_eq("step1_vcfs" in c, False, "no per-sample alignment descent")
        assert_eq(c["step2_vcfs"], 2, "plain .vcf in the database")
        assert_eq(c["vcfs_count"], 3, "collected _zc.vcf + _zc.vcf.gz")
        assert_eq(c["step2_html"], 0, "no reports yet")

        print("\n[the burst coalescer returns equal values and a fresh dict]")
        again = _project_counts(proj)
        assert_eq(again, c, "coalesced counts equal the computed ones")
        again["fastq_count"] = -1
        assert_eq(_project_counts(proj)["fastq_count"], 5,
                  "mutating a returned dict cannot poison the cache")

        print("\n[once the burst window passes, changes are picked up]")
        # The window is only a few seconds because no cheap signature can see a
        # VCF written deep inside step1/<sample>/alignment_<ref>/ — it bumps no
        # parent mtime. Simulating expiry (rather than sleeping) keeps the test
        # fast while pinning the behaviour that matters: the counts are re-read,
        # not served stale.
        newd = proj / "step1" / "C"
        (newd / "alignment_MTBC0_v1").mkdir(parents=True)
        (newd / "C_R1.fastq.gz").write_text("r1")
        (newd / "alignment_MTBC0_v1" / "C_zc.vcf").write_text("##vcf")
        # A VCF appearing inside an EXISTING sample — the case no mtime signature
        # can detect — must also reappear once the window passes.
        (proj / "step1" / "B" / "alignment" / "B2_zc.vcf").write_text("##vcf")
        original_ttl = projects._COUNTS_TTL_SECONDS
        projects._COUNTS_TTL_SECONDS = 0.0
        try:
            c2 = _project_counts(proj)
        finally:
            projects._COUNTS_TTL_SECONDS = original_ttl
        assert_eq(c2["step1_samples"], 3, "new sample counted")
        assert_eq(c2["fastq_count"], 6, "new sample's read counted")

        print("\n[an empty / missing project counts as zero, never an error]")
        empty = tmp / "empty"
        empty.mkdir()
        c3 = _project_counts(empty)
        assert_eq(c3, {"fastq_count": 0, "step1_samples": 0,
                       "step2_html": 0, "step2_vcfs": 0, "vcfs_count": 0},
                  "all zeros")

        print("\n[one unreadable project does not take the list down]")
        # pathlib's exists() propagates EACCES rather than returning False, so a
        # single project whose project.json this user cannot reach used to raise
        # straight out of /api/projects — and the frontend's catch replaced the
        # whole list with an empty one. Every readable project vanished because
        # of one that was not.
        root = tmp / "root"
        (root / "good").mkdir(parents=True)
        (root / "good" / "project.json").write_text('{"reference": "ok"}')
        bad = root / "locked"
        bad.mkdir()
        (bad / "project.json").write_text("{}")
        os.chmod(bad, 0o000)
        try:
            listed = projects.list_projects([("personal", root)])
            names = sorted(p["name"] for p in listed)
            assert_eq(names, ["good", "locked"], "both projects still listed")
            good = next(p for p in listed if p["name"] == "good")
            assert_eq(good.get("reference"), "ok", "the readable one is intact")
            locked = next(p for p in listed if p["name"] == "locked")
            assert_eq(locked.get("counts_unreadable"), True,
                      "the unreadable one is flagged, not omitted")
            assert_eq(locked["fastq_count"], 0, "unreadable counts are zero")
        finally:
            os.chmod(bad, 0o755)

        print("\n[a root that cannot be read is skipped, not fatal]")
        assert_eq(projects.list_projects([("personal", tmp / "nonexistent")]), [],
                  "missing root yields an empty list")

        print("\nAll project-count tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
