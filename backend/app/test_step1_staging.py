"""Step 1's two read passes: Grab stages, Run trims.

The invariants that matter:
  * Grab copies and never trims — it only stages samples not already in Step 1,
    under any name (a trimmed Run renames the folder to <sample>-trimN, and a
    later Grab must recognise that as the same sample);
  * a trimmed pair keeps the SAME record count on both sides, so every header
    stays matched — the whole reason trimming takes the head of the file;
  * the trim mark lands in the sample PREFIX of the FASTQ name and on the folder
    (vSNP3 splits the sample name at the first '_'), so the alignment, its VCF
    and the tree label all say the reads were trimmed;
  * a single-file input (ONT long reads, single-end Illumina) trims as a group
    of one and never cuts a read in half;
  * a sample under the cap is left exactly as it is.

Run from anywhere with the per-site conda python:

    <conda>/bin/python backend/app/test_step1_staging.py
"""
from __future__ import annotations
import gzip
import io
import random
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.step1_staging import (  # noqa: E402
    group_key, plan_trim, read_id, read_number, stage, staged_samples,
    trim_staged_samples, trimmed_name,
)

MB = 1024 * 1024


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label}")


def check(cond, label):
    if not cond:
        raise AssertionError(label)
    print(f"  OK  {label}")


def write_fastq(path: Path, count: int, length: int, tag: str, seed: int,
                mate: int = 0) -> None:
    """A FASTQ whose records are realistic enough not to compress to nothing."""
    rng = random.Random(seed)
    suffix = f"/{mate}" if mate else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb", compresslevel=1) as fh:
        for i in range(count):
            seq = "".join(rng.choice("ACGT") for _ in range(length))
            qual = "".join(rng.choice("FGHIJ:,#") for _ in range(length))
            fh.write(
                f"@{tag}:{i}{suffix} {mate}:N:0:ATCACG\n{seq}\n+\n{qual}\n"
                .encode()
            )


def records(path: Path):
    with gzip.open(path, "rt") as fh:
        lines = fh.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) % 4:
        raise AssertionError(f"{path.name}: {len(lines)} lines is not whole records")
    return [tuple(lines[i:i + 4]) for i in range(0, len(lines), 4)]


def quiet(fn, *args, **kwargs):
    """These log to stdout; capture it so the test output stays readable and the
    log itself can be asserted on."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


def test_name_helpers() -> None:
    print("[name helpers]")
    assert_eq(group_key("Mg-280_R1.fastq.gz"), group_key("Mg-280_R2.fastq.gz"),
              "R1 and R2 share a group key")
    assert_eq(group_key("Mg-280_R1_001.fastq.gz"),
              group_key("Mg-280_R2_001.fastq.gz"), "lane suffix keeps the pair together")
    assert_eq(group_key("SRR12_1.fastq.gz"), group_key("SRR12_2.fastq.gz"),
              "bare SRA _1/_2 share a group key")
    assert_eq(read_number("Mg-280_R2.fastq.gz"), 2, "R2 is read 2")
    assert_eq(read_number("SRR12_1.fastq.gz"), 1, "bare _1 is read 1")
    # An ONT run has no read marker at all — its own group, of one.
    assert_eq(read_number("ONT-barcode07.fastq.gz"), 0, "unmarked file is single-end")
    assert_eq(group_key("ONT-barcode07.fastq.gz"), "ONT-barcode07.fastq.gz",
              "unmarked file keys on its own name")

    # The tag has to land in the sample prefix, before the first '_', or vSNP3
    # never sees it — and it must not introduce an underscore of its own.
    assert_eq(trimmed_name("Mg-280_R1.fastq.gz", "Mg-280", "-trim200"),
              "Mg-280-trim200_R1.fastq.gz", "tag goes in the sample prefix")
    assert_eq(trimmed_name("Mg-280_R1_001.fastq.gz", "Mg-280", "-trim200"),
              "Mg-280-trim200_R1_001.fastq.gz", "tag preserves the lane suffix")
    assert_eq(trimmed_name("ONT-b07.fastq.gz", "ONT-b07", "-trim200"),
              "ONT-b07-trim200.fastq.gz", "unmarked file gets the tag too")
    check("_" not in trimmed_name("X_R1.fastq.gz", "X", "-trim200").split("_")[0],
          "the tagged sample prefix stays underscore-free")

    print("[read_id]")
    assert_eq(read_id(b"@A00123:1:HXX:1:1101:1:0 1:N:0:ATCACG"), "A00123:1:HXX:1:1101:1:0",
              "Illumina description dropped")
    assert_eq(read_id(b"@SRR1.7/1"), read_id(b"@SRR1.7/2"), "/1 and /2 reduce to one id")


def test_grab_stages_without_trimming(tmp: Path) -> None:
    print("[Grab: copies, never trims]")
    download, step1 = tmp / "download", tmp / "step1"
    write_fastq(download / "TB_1234_R1.fastq.gz", 20000, 150, "TB", 1, mate=1)
    write_fastq(download / "TB_1234_R2.fastq.gz", 20000, 150, "TB", 1, mate=2)
    write_fastq(download / "ONT-b07.fastq.gz", 500, 5000, "ONT", 3)

    summary, log = quiet(stage, download, step1)

    # The underscored sample was dashed on the way in; nothing is tagged.
    check((step1 / "TB-1234").is_dir(), "underscored sample staged as TB-1234")
    check((step1 / "ONT-b07").is_dir(), "single-file sample staged on its own")
    check(not any("trim" in p.name for p in step1.iterdir()),
          "Grab tags nothing, however large the reads are")
    assert_eq(records(step1 / "TB-1234" / "TB-1234_R1.fastq.gz"),
              records(download / "TB-1234_R1.fastq.gz"),
              "reads are staged byte-for-byte")
    assert_eq(summary["renamed"], 2, "both underscored mates dashed in download/")
    assert_eq(summary["created"], 3, "three files staged")
    check("trim" not in log.lower(), "the Grab log never mentions trimming")

    print("[Grab: re-run is a no-op]")
    summary2, _ = quiet(stage, download, step1)
    assert_eq(summary2["created"], 0, "a second Grab stages nothing new")
    assert_eq(summary2["skipped"], 2, "both samples recognised as already staged")


def test_trimmed_pair_stays_matched(tmp: Path) -> None:
    print("[Run trim: paired Illumina]")
    download, step1 = tmp / "download", tmp / "step1"
    write_fastq(download / "TB-1234_R1.fastq.gz", 30000, 150, "TB", 1, mate=1)
    write_fastq(download / "TB-1234_R2.fastq.gz", 30000, 150, "TB", 1, mate=2)
    write_fastq(download / "SM-9_R1.fastq.gz", 400, 150, "SM", 2, mate=1)
    write_fastq(download / "SM-9_R2.fastq.gz", 400, 150, "SM", 2, mate=2)
    quiet(stage, download, step1)

    final, log = quiet(trim_staged_samples, step1, ["SM-9", "TB-1234"], 1)
    assert_eq(final, ["SM-9", "TB-1234-trim1"],
              "the run iterates the folder names the trim produced")

    sample_dir = step1 / "TB-1234-trim1"
    check(sample_dir.is_dir(), "the trimmed sample's folder carries the mark")
    check(not (step1 / "TB-1234").exists(), "its untrimmed folder is gone, not duplicated")
    r1 = sample_dir / "TB-1234-trim1_R1.fastq.gz"
    r2 = sample_dir / "TB-1234-trim1_R2.fastq.gz"
    check(r1.is_file() and r2.is_file(), "both mates carry the mark in the FASTQ name")
    check(not (sample_dir / "TB-1234_R1.fastq.gz").exists(),
          "the untrimmed read it replaced is gone, so the batch can't pick it up")

    rec1, rec2 = records(r1), records(r2)
    assert_eq(len(rec1), len(rec2), "R1 and R2 kept the same number of records")
    check(len(rec1) > 0, "the trim kept some reads")
    check(all(read_id(a[0].encode()) == read_id(b[0].encode())
              for a, b in zip(rec1, rec2)),
          "every kept header is still matched with its mate")
    src1 = records(download / "TB-1234_R1.fastq.gz")
    assert_eq(rec1, src1[:len(rec1)], "kept records are the head of the source, verbatim")
    check(len(rec1) < len(src1), "the source really was larger than the cap")
    check(r1.stat().st_size <= 1 * MB * 1.10, "the trimmed mate lands at roughly the cap")

    # The originals are untouched where they live.
    assert_eq(records(download / "TB-1234_R1.fastq.gz"), src1,
              "download/ still holds the full-size original")

    # Under the cap: untouched, unrenamed.
    check((step1 / "SM-9").is_dir(), "under-cap sample keeps its plain name")
    assert_eq(records(step1 / "SM-9" / "SM-9_R1.fastq.gz"),
              records(download / "SM-9_R1.fastq.gz"),
              "under-cap sample is left exactly as it is")
    check("under the cap, left as it is" in log, "the log says why it skipped that one")
    check("kept the first" in log and "[OK]" in log,
          "the log reports how many reads were kept")


def test_single_file_ont(tmp: Path) -> None:
    print("[Run trim: single-file ONT]")
    download, step1 = tmp / "download", tmp / "step1"
    # Long reads, no _R1/_R2 marker — the shape an ONT run arrives in.
    write_fastq(download / "ONT-b07.fastq.gz", 900, 5000, "ONT", 3)
    quiet(stage, download, step1)

    final, log = quiet(trim_staged_samples, step1, ["ONT-b07"], 1)
    assert_eq(final, ["ONT-b07-trim1"], "single-file sample trims as a group of one")
    out = step1 / "ONT-b07-trim1" / "ONT-b07-trim1.fastq.gz"
    check(out.is_file(), "the trimmed long-read file carries the mark")
    kept = records(out)
    src = records(download / "ONT-b07.fastq.gz")
    check(0 < len(kept) < len(src), "some, but not all, long reads were kept")
    assert_eq(kept, src[:len(kept)], "kept long reads are whole and verbatim")
    check(all(len(r[1]) == 5000 for r in kept), "no long read was cut in half")
    check("reads so far" in log or "kept the first" in log,
          "the log counts reads, not pairs, for a single-file input")


def test_trim_is_idempotent_and_never_compounds(tmp: Path) -> None:
    print("[Run trim: repeat runs]")
    download, step1 = tmp / "download", tmp / "step1"
    write_fastq(download / "TB-1_R1.fastq.gz", 20000, 150, "TB", 4, mate=1)
    write_fastq(download / "TB-1_R2.fastq.gz", 20000, 150, "TB", 4, mate=2)
    quiet(stage, download, step1)
    quiet(trim_staged_samples, step1, ["TB-1"], 1)
    kept_once = len(records(step1 / "TB-1-trim1" / "TB-1-trim1_R1.fastq.gz"))

    # Same size again: nothing to do, and above all no second cut.
    final, log = quiet(trim_staged_samples, step1, ["TB-1-trim1"], 1)
    assert_eq(final, ["TB-1-trim1"], "an already-trimmed sample keeps its name")
    assert_eq(len(records(step1 / "TB-1-trim1" / "TB-1-trim1_R1.fastq.gz")), kept_once,
              "its reads are untouched")
    check("already trimmed" in log, "the log says it is already trimmed")

    # A DIFFERENT size must not cut the cut — the reads to do it from are the
    # originals back in download/, so this is refused, not compounded.
    final2, log2 = quiet(trim_staged_samples, step1, ["TB-1-trim1"], 2)
    assert_eq(final2, ["TB-1-trim1"], "a different size leaves the sample alone")
    check(not (step1 / "TB-1-trim1-trim2").exists(), "no trim-of-a-trim folder appears")
    assert_eq(len(records(step1 / "TB-1-trim1" / "TB-1-trim1_R1.fastq.gz")), kept_once,
              "its reads are still the first trim's")
    check("Remove it and Grab it again" in log2, "the log says how to redo it")

    # And a later Grab must not re-stage TB-1 just because the folder is now
    # named TB-1-trim1 — that was the whole ready-to-run bug.
    print("[Grab after a trimmed Run]")
    assert_eq(staged_samples(step1).get("TB-1"), "TB-1-trim1",
              "the trimmed folder registers under its untrimmed base name")
    summary, log3 = quiet(stage, download, step1)
    assert_eq(summary["created"], 0, "Grab stages nothing new")
    check(not (step1 / "TB-1").exists(), "no untrimmed twin is stood up beside it")
    check("already in Step 1 as TB-1-trim1" in log3, "the log names what it skipped")


def test_plan_matches_what_the_trim_does(tmp: Path) -> None:
    """The dispatcher names the run's samples from plan_trim before the pass has
    run — provenance and the results table depend on the two agreeing."""
    print("[plan_trim predicts the run's sample names]")
    download, step1 = tmp / "download", tmp / "step1"
    write_fastq(download / "Big-1_R1.fastq.gz", 20000, 150, "B", 5, mate=1)
    write_fastq(download / "Big-1_R2.fastq.gz", 20000, 150, "B", 5, mate=2)
    write_fastq(download / "Small-2_R1.fastq.gz", 300, 150, "S", 6, mate=1)
    quiet(stage, download, step1)

    names = ["Big-1", "Small-2"]
    plan, _ = quiet(plan_trim, step1, names, 1)
    predicted = [e["new_sample"] for e in plan]
    assert_eq(predicted, ["Big-1-trim1", "Small-2"], "plan names both samples")
    assert_eq([e["action"] for e in plan], ["trim", "under"], "and says what it will do")

    actual, _ = quiet(trim_staged_samples, step1, names, 1)
    assert_eq(actual, predicted, "the trim pass produces exactly the planned names")

    # With the box unticked the plan is a no-op that renames nothing.
    plan_off, _ = quiet(plan_trim, step1, ["Big-1-trim1"], 0)
    assert_eq([e["new_sample"] for e in plan_off], ["Big-1-trim1"],
              "trim off leaves every name alone")
    assert_eq([e["action"] for e in plan_off], ["off"], "and reports itself as off")


def main() -> int:
    test_name_helpers()
    for fn in (test_grab_stages_without_trimming,
               test_trimmed_pair_stays_matched,
               test_single_file_ont,
               test_trim_is_idempotent_and_never_compounds,
               test_plan_matches_what_the_trim_does):
        tmp = Path(tempfile.mkdtemp(prefix="step1stage-"))
        try:
            fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
