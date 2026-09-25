"""Step 1 samples finished outside this GUI read as Complete.

A run from the command line leaves no .provenance/exit_code sentinel, so the
GUI reads completion off the outputs. It used to look for the annotated VCF
(*_filtered_hapall_annotated.vcf) beside the de-duplicated BAM — but vsnp3
writes that VCF only when a GenBank annotation is supplied. A run against an
unannotated reference (the Ames modified_NL_not_annotated project, 24,000
samples of it) leaves *_zc.vcf and *_nodup.bam and nothing else, and every
such sample showed as Not Started: pending in the Samples list, picked up and
re-aligned by a plain Run, never collected into the VCF database.

The zero-coverage VCF is the completion artifact — vsnp3 writes it last of
the alignment outputs, and it is what VCF collection and Step 2 consume — so
its presence is what these tests pin, in all three places the rule applies:
the status list, the dispatch plan and VCF collection. They must agree, or
the list promises a run that either re-aligns finished work or never happens.

Run from anywhere with the checkout's python:

    env/bin/python backend/app/test_step1_command_line_complete.py
"""
from __future__ import annotations

import gzip
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as m  # noqa: E402

FAILURES: list[str] = []

REF = "Modified_NL_isolate"
VCF_TEXT = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
LEGACY_NOTE = "aligned before this GUI (legacy alignment/ layout)"


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  OK    {label}")


def write_fastq(path: Path, size: int) -> None:
    path.write_bytes(gzip.compress(os.urandom(size)))


def reads(d: Path) -> None:
    write_fastq(d / f"{d.name}_S81_L001_R1_001.fastq.gz", 200_000)
    write_fastq(d / f"{d.name}_S81_L001_R2_001.fastq.gz", 200_000)


def alignment(d: Path, *, annotated: bool = False, bam: bool = True,
              zc: str | None = "zc.vcf", legacy: bool = False) -> Path:
    """What vsnp3 moves into alignment_<reference>/ when it finishes (or the
    vSNP2-era plain alignment/): BAM + index, the zc VCF, the reference, and
    the annotated VCF only when annotation was supplied."""
    name = d.name
    a = d / ("alignment" if legacy else f"alignment_{REF}")
    a.mkdir(parents=True)
    if bam:
        (a / f"{name}_nodup.bam").write_bytes(b"BAM\x01")
        (a / f"{name}_nodup.bam.bai").write_bytes(b"BAI\x01")
    if annotated:
        (a / f"{name}_filtered_hapall_annotated.vcf").write_text(VCF_TEXT, encoding="utf-8")
    if zc:
        z = a / f"{name}_{zc}"
        if zc.endswith(".gz"):
            z.write_bytes(gzip.compress(VCF_TEXT.encode("utf-8")))
        else:
            z.write_text(VCF_TEXT, encoding="utf-8")
    (a / f"{REF}.fasta").write_text(">chr\nACGT\n", encoding="utf-8")
    (a / f"{REF}.fasta.fai").write_text("chr\t4\t5\t4\t5\n", encoding="utf-8")
    (a / "unmapped_reads").mkdir()
    return a


def build_step1(root: Path) -> Path:
    step1 = root / "step1"
    step1.mkdir(parents=True)

    # The Ames folder, file for file: a vsnp3 3.08 run from 2022 against an
    # unannotated reference (so no annotated VCF; a run_log.txt with no sample
    # prefix and a *_report.out that version left behind), plus what a later
    # GUI Run that was stopped before reaching it left there — _provenance/,
    # run_metadata.json and an empty .provenance/ pre-created at dispatch,
    # its sentinel never written.
    s = step1 / "22-013751-001-original"
    s.mkdir()
    reads(s)
    alignment(s)
    (s / f"{s.name}_2022-08-18_11-06-08_report.out").write_text("report\n", encoding="utf-8")
    (s / f"{s.name}_2022-08-18_11-06-08_report.pdf").write_bytes(b"%PDF-1.4\n")
    (s / f"{s.name}_2022-08-18_11-06-08_stats.xlsx").write_bytes(b"PK\x03\x04")
    (s / "run_log.txt").write_text("vSNP3: 3.08\n", encoding="utf-8")
    (s / "run_metadata.json").write_text("{}", encoding="utf-8")
    (s / "_provenance").mkdir()
    (s / ".provenance").mkdir()

    # The Ames folder that already read as Complete: a current command-line
    # run made with a GenBank annotation — annotated VCF, BAM and zc VCF.
    s = step1 / "24-039012-002-original-tile"
    s.mkdir()
    reads(s)
    alignment(s, annotated=True)
    (s / f"{s.name}_2026-04-30_07-57-46_report.pdf").write_bytes(b"%PDF-1.4\n")
    (s / f"{s.name}_2026-04-30_07-57-46_stats.xlsx").write_bytes(b"PK\x03\x04")
    (s / f"{s.name}_run_log.txt").write_text("vSNP3: 3.36\n", encoding="utf-8")

    # Finished, BAM since cleared out to reclaim space: the VCF is still there.
    s = step1 / "NO-BAM"
    s.mkdir()
    reads(s)
    alignment(s, bam=False)

    # Nanopore: vsnp3 names the zc VCF *_nanopore_zc.vcf.
    s = step1 / "ONT-01"
    s.mkdir()
    reads(s)
    alignment(s, zc="nanopore_zc.vcf")

    # A compressed zc VCF.
    s = step1 / "GZ-01"
    s.mkdir()
    reads(s)
    alignment(s, zc="zc.vcf.gz")

    # vSNP2-era layout: plain alignment/, zc VCF only. Complete, with its note.
    s = step1 / "LEGACY-01"
    s.mkdir()
    reads(s)
    alignment(s, bam=False, legacy=True)

    # Stopped before its VCF: a BAM alone is not a finished run.
    s = step1 / "BAM-ONLY"
    s.mkdir()
    reads(s)
    alignment(s, zc=None)

    # Never run.
    s = step1 / "PENDING-01"
    s.mkdir()
    reads(s)

    # Ran under the GUI and failed: the sentinel wins over whatever is on disk.
    s = step1 / "FAILED-01"
    s.mkdir()
    reads(s)
    alignment(s)
    (s / ".provenance").mkdir()
    (s / ".provenance" / "exit_code").write_text("1\n", encoding="utf-8")
    (s / "run_step1.log").write_text("boom\n", encoding="utf-8")

    # Scaffolding, not a sample.
    (step1 / "_provenance").mkdir()
    return step1


COMPLETE = [
    "22-013751-001-original", "24-039012-002-original-tile",
    "GZ-01", "LEGACY-01", "NO-BAM", "ONT-01",
]
PENDING = ["BAM-ONLY", "PENDING-01"]


def statuses(project_dir: Path) -> dict:
    cfg: dict = {}
    m.load_config = lambda: cfg
    m._project_dir_for = lambda c, p: project_dir
    m._STEP1_STATUS_CACHE.clear()
    out = m.step1_status("proj")
    return {s["sample"]: s for s in out["samples"]}


def test_status(project_dir: Path) -> dict:
    print("[step1/status]")
    by_sample = statuses(project_dir)
    check(sorted(by_sample), sorted(COMPLETE + PENDING + ["FAILED-01"]),
          "every sample dir is listed, the scaffolding is not")

    ames = by_sample["22-013751-001-original"]
    check(ames["status"], "complete",
          "a command-line run with a zc VCF and BAM but no annotated VCF is Complete")
    check(ames["has_outputs"], True, "and its outputs count as finished")
    check(ames["has_zc_vcf"], True, "and it has a VCF to collect")
    check(ames["has_log"], False, "and no GUI log to view")
    check(ames["reason"], "", "and carries no note")

    check(by_sample["24-039012-002-original-tile"]["status"], "complete",
          "a command-line run with the annotated VCF is Complete as before")
    check(by_sample["NO-BAM"]["status"], "complete",
          "a finished run whose BAM was cleared out is still Complete")
    check(by_sample["ONT-01"]["status"], "complete",
          "a nanopore run (*_nanopore_zc.vcf) is Complete")
    check(by_sample["GZ-01"]["status"], "complete",
          "a compressed zc VCF counts")
    check(by_sample["LEGACY-01"]["status"], "complete",
          "a vSNP2-era plain alignment/ run is Complete")
    check(by_sample["LEGACY-01"]["reason"], LEGACY_NOTE,
          "and keeps its legacy-layout note")
    for name in COMPLETE:
        check(by_sample[name]["has_outputs"], True, f"{name}: has_outputs")
        check(by_sample[name]["has_zc_vcf"], True, f"{name}: has_zc_vcf")

    check(by_sample["BAM-ONLY"]["status"], "not_started",
          "a BAM with no VCF is a run that did not finish, so it will run")
    check(by_sample["BAM-ONLY"]["has_outputs"], False, "and its outputs are not finished")
    check(by_sample["BAM-ONLY"]["has_zc_vcf"], False, "and it has no VCF")
    check(by_sample["PENDING-01"]["status"], "not_started", "a never-run sample is Not started")
    check(by_sample["FAILED-01"]["status"], "error",
          "a non-zero sentinel is an Error whatever sits under alignment_*/")
    check(by_sample["FAILED-01"]["has_zc_vcf"], True, "though its VCF is still reported")
    return by_sample


def test_dispatch_agrees(step1: Path, by_sample: dict) -> None:
    print("\n[dispatch plan]")
    to_run, skipped = m._step1_dispatch_plan(step1)
    check(to_run, PENDING, "a plain Run dispatches only the unfinished samples")
    pending = {n for n, s in by_sample.items() if s["status"] == "not_started"}
    check(pending, set(to_run),
          "the samples the list calls Not started are exactly the ones a Run picks up")
    reasons = {s["sample"]: s["reason"] for s in skipped}
    for name in COMPLETE:
        check(reasons.get(name),
              "already completed in a previous run (use Force re-run to re-align)",
              f"{name} is held out as already completed")
    check(reasons.get("FAILED-01"), "errored in a previous run — use Force re-run to retry",
          "the failed sample is held out as errored")

    to_run, _ = m._step1_dispatch_plan(step1, force_rerun=True)
    check(to_run, sorted(COMPLETE + PENDING + ["FAILED-01"]),
          "Force re-run picks up every sample, finished ones included")


def test_collect_agrees(project_dir: Path) -> None:
    print("\n[vcfs/collect]")
    out = m.project_vcfs_collect("proj", m.VcfsCollectRequest())
    check(sorted(out["auto_added"]), COMPLETE,
          "every Complete sample's zc VCF is collected, and nothing else")
    check(out["force_added"], [], "nothing needed forcing")
    check(out["no_vcf"], [], "nothing lacked a VCF")
    check(out["total"], len(COMPLETE), "the database holds one VCF per finished sample")
    db = m.vcf_db_dir(project_dir / "step2")
    check(sorted(p.name for p in db.iterdir()), [
        "22-013751-001-original_zc.vcf", "24-039012-002-original-tile_zc.vcf",
        "GZ-01_zc.vcf.gz", "LEGACY-01_zc.vcf", "NO-BAM_zc.vcf", "ONT-01_nanopore_zc.vcf",
    ], "under the names step2 expects")

    # Collect accumulates: a second pass adds nothing and drops nothing.
    again = m.project_vcfs_collect("proj", m.VcfsCollectRequest())
    check(again["auto_added"], [], "a second Collect adds nothing")
    check(sorted(again["already_present"]), COMPLETE, "and finds them all present")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="step1_cli_complete_"))
    try:
        project_dir = tmp / "projects" / "proj"
        step1 = build_step1(project_dir)
        by_sample = test_status(project_dir)
        test_dispatch_agrees(step1, by_sample)
        test_collect_agrees(project_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
