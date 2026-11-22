"""Step 1 "misfit" samples: folders that can never be dispatched.

A project's step1/ accumulates directories that are not runnable samples — a
misfiled or scratch folder with no fastqs in it, reads whose download source
was removed, a truncated download. Those used to report as "Not started",
which sorts to the TOP of the Samples list and reads as pending work that the
next Run will pick up. It never was: the dispatcher has always held them back.

These tests pin the two halves together — the status endpoint calls them
"misfit", and the dispatch plan refuses to run exactly the same set — because
the failure mode of them disagreeing is a list that promises a run it won't do.

Run from anywhere with the per-site conda python:

    <conda>/bin/python backend/app/test_step1_misfit.py
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as m  # noqa: E402

FAILURES: list[str] = []


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  OK    {label}")


def write_fastq(path: Path, size: int) -> None:
    """A gzip file of at least `size` bytes on disk. The checks under test read
    st_size only, so the payload is random (incompressible) to make the file on
    disk the size asked for rather than whatever gzip squeezes it to."""
    path.write_bytes(gzip.compress(os.urandom(size)))


def build_step1(root: Path) -> Path:
    """A step1/ holding one of every case the status logic has to separate."""
    step1 = root / "step1"
    step1.mkdir(parents=True)

    # Runnable: a normal paired sample, comfortably over the size floor.
    good = step1 / "GOOD-01"
    good.mkdir()
    write_fastq(good / "GOOD-01_R1.fastq.gz", 200_000)
    write_fastq(good / "GOOD-01_R2.fastq.gz", 200_000)

    # Misfit: a directory that was never a sample (nothing in it at all).
    (step1 / "EMPTY-DIR").mkdir()

    # Misfit: misfiled — holds files, but no reads for vsnp3 to align.
    misfiled = step1 / "MISFILED"
    misfiled.mkdir()
    (misfiled / "notes.txt").write_text("stray folder", encoding="utf-8")

    # Misfit: the reads are a symlink whose source was deleted.
    broken = step1 / "BROKEN-LINK"
    broken.mkdir()
    (broken / "BROKEN-LINK_R1.fastq.gz").symlink_to(root / "gone" / "x_R1.fastq.gz")

    # Misfit: a download that stopped short, well under the 50 KB floor.
    tiny = step1 / "TRUNCATED"
    tiny.mkdir()
    write_fastq(tiny / "TRUNCATED_R1.fastq.gz", 400)
    write_fastq(tiny / "TRUNCATED_R2.fastq.gz", 400)

    # Runnable: reads simulated from an imported sequence (app.seq_import). A
    # 100-fold perfect simulation of an 8 kb FMDV genome gzips to a few KB,
    # far under the floor, and is not a truncated download. Must dispatch.
    sim = step1 / "SIM-IMPORT"
    sim.mkdir()
    write_fastq(sim / "SIM-IMPORT_R1.fastq.gz", 5_000)
    write_fastq(sim / "SIM-IMPORT_R2.fastq.gz", 5_000)
    (sim / "imported_sequence.json").write_text(
        json.dumps({"sample": "SIM-IMPORT", "reads": "simulated"}), encoding="utf-8")

    # Misfit: a simulated import whose reads came out empty. Nothing to align.
    sim0 = step1 / "SIM-EMPTY"
    sim0.mkdir()
    (sim0 / "SIM-EMPTY_R1.fastq.gz").write_bytes(b"")
    (sim0 / "SIM-EMPTY_R2.fastq.gz").write_bytes(b"")
    (sim0 / "imported_sequence.json").write_text(
        json.dumps({"sample": "SIM-EMPTY", "reads": "simulated"}), encoding="utf-8")

    # Complete: a finished run, reads still in place.
    done = step1 / "DONE-01"
    (done / ".provenance").mkdir(parents=True)
    (done / ".provenance" / "exit_code").write_text("0\n", encoding="utf-8")
    write_fastq(done / "DONE-01_R1.fastq.gz", 200_000)

    # Complete, but its reads have since been cleared out. The input check
    # would flag it; completion is the truth and wins.
    done2 = step1 / "DONE-NO-READS"
    (done2 / ".provenance").mkdir(parents=True)
    (done2 / ".provenance" / "exit_code").write_text("0\n", encoding="utf-8")

    # Errored: it ran and failed. "Error" is the more useful state — there is
    # a log to open — so it is not re-labelled by the input check either.
    bad = step1 / "FAILED-01"
    (bad / ".provenance").mkdir(parents=True)
    (bad / ".provenance" / "exit_code").write_text("1\n", encoding="utf-8")
    (bad / "run_step1.log").write_text("boom\n", encoding="utf-8")
    write_fastq(bad / "FAILED-01_R1.fastq.gz", 200_000)

    # Errored because the FMDV chooser found no reference (exit code 3 and a
    # chooser JSON with nothing chosen). Shown as an error that says so, and
    # retried by a plain Run: the fix is a genome added to the library.
    noref = step1 / "NO-REF"
    (noref / ".provenance").mkdir(parents=True)
    (noref / ".provenance" / "exit_code").write_text("3\n", encoding="utf-8")
    (noref / "run_step1.log").write_text("FMDV: no reference could be chosen\n", encoding="utf-8")
    (noref / "fmdv_reference.json").write_text(json.dumps({
        "scope": "FMDV", "chosen": None, "error": "no in-scope match", "fmdv_reads": 0,
        "top_vp1": [{"safe": "O-EA-4_X", "reads": 16}], "top_genome": []}), encoding="utf-8")
    write_fastq(noref / "NO-REF_R1.fastq.gz", 5_000)
    (noref / "imported_sequence.json").write_text(
        json.dumps({"sample": "NO-REF", "reads": "simulated"}), encoding="utf-8")

    # Exit code 3 from something other than the chooser (a reference was
    # chosen; vsnp3 failed later): an ordinary error, not retried.
    bad3 = step1 / "FAILED-03"
    (bad3 / ".provenance").mkdir(parents=True)
    (bad3 / ".provenance" / "exit_code").write_text("3\n", encoding="utf-8")
    (bad3 / "fmdv_reference.json").write_text(json.dumps({"chosen": {"safe": "O-EA-4_X"}}), encoding="utf-8")
    write_fastq(bad3 / "FAILED-03_R1.fastq.gz", 200_000)

    # Scaffolding, not a sample: stays out of the list entirely.
    (step1 / "_provenance").mkdir()
    return step1


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

    check(sorted(by_sample), [
        "BROKEN-LINK", "DONE-01", "DONE-NO-READS", "EMPTY-DIR", "FAILED-01", "FAILED-03",
        "GOOD-01", "MISFILED", "NO-REF", "SIM-EMPTY", "SIM-IMPORT", "TRUNCATED",
    ], "scaffolding (_provenance) is not listed as a sample")

    check(by_sample["NO-REF"]["status"], "error", "a sample with no FMDV reference is an error")
    check(by_sample["NO-REF"]["reason"].startswith("No FMDV reference could be chosen"), True,
          "and the reason says so")
    check("best 16" in by_sample["NO-REF"]["reason"], True, "with the chooser's best vote")
    check(by_sample["FAILED-03"]["status"], "error", "exit code 3 with a reference chosen is an error")
    check(by_sample["FAILED-03"]["reason"], "Failed during Step 1 — open View log for the error.",
          "with the generic wording")

    check(by_sample["SIM-IMPORT"]["status"], "not_started",
          "tiny simulated-read import is runnable, not a misfit")
    check(by_sample["SIM-IMPORT"]["reason"], "", "and carries no complaint")
    check(by_sample["SIM-EMPTY"]["status"], "misfit", "empty simulated reads are a misfit")
    check("empty" in by_sample["SIM-EMPTY"]["reason"], True, "and say so")

    check(by_sample["GOOD-01"]["status"], "not_started",
          "a runnable sample is still Not started")
    check(by_sample["GOOD-01"]["reason"], "", "and carries no complaint")

    for name in ("EMPTY-DIR", "MISFILED", "BROKEN-LINK", "TRUNCATED", "SIM-EMPTY"):
        check(by_sample[name]["status"], "misfit", f"{name} is a misfit")
        check(bool(by_sample[name]["reason"]), True, f"{name} says why")

    check(by_sample["EMPTY-DIR"]["reason"],
          "no fastq files in the sample directory", "the no-reads wording is kept")

    # A sample that ran keeps its own state — even with its reads gone.
    check(by_sample["DONE-01"]["status"], "complete", "a finished sample is not a misfit")
    check(by_sample["DONE-NO-READS"]["status"], "complete",
          "a finished sample whose reads were cleared out is still complete")
    check(by_sample["FAILED-01"]["status"], "error", "a failed sample is not a misfit")
    return by_sample


def test_dispatch_agrees(step1: Path, by_sample: dict) -> None:
    print("\n[dispatch plan]")
    to_run, skipped = m._step1_dispatch_plan(step1)
    check(to_run, ["GOOD-01", "NO-REF", "SIM-IMPORT"],
          "the runnable samples and the no-reference retry are dispatched")
    check("FAILED-03" in {s["sample"] for s in skipped}, True,
          "an ordinary exit-3 failure is held back like any error")

    misfits = {n for n, s in by_sample.items() if s["status"] == "misfit"}
    check(misfits & set(to_run), set(), "no misfit is ever dispatched")

    pending = {n for n, s in by_sample.items() if s["status"] == "not_started"}
    check(pending - set(to_run), set(),
          "every sample the list calls Not started is one a Run picks up")

    skipped_names = {s["sample"] for s in skipped}
    check(misfits <= skipped_names, True, "each misfit is reported as held out")


def test_force_rerun_does_not_resurrect(step1: Path) -> None:
    print("\n[force re-run]")
    to_run, _ = m._step1_dispatch_plan(step1, force_rerun=True)
    # Force re-run overrides "already completed" / "errored before" — it does
    # NOT override a missing input, because there is nothing to align.
    # DONE-NO-READS is complete but has no reads left, so it stays out too.
    check(sorted(to_run), ["DONE-01", "FAILED-01", "FAILED-03", "GOOD-01", "NO-REF", "SIM-IMPORT"],
          "Force re-run picks up finished and failed samples, never misfits")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="step1_misfit_"))
    try:
        project_dir = tmp / "projects" / "proj"
        step1 = build_step1(project_dir)
        by_sample = test_status(project_dir)
        test_dispatch_agrees(step1, by_sample)
        test_force_rerun_does_not_resurrect(step1)
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
