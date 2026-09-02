"""Unit + end-to-end tests for app.step1_staging (Grab's staging/trim pass).

The invariants that matter for a trimmed Grab:
  * a pair keeps the SAME record count on both sides, so every header stays
    matched — this is the whole reason trimming takes the head of the file;
  * the trim mark lands in the sample PREFIX (vSNP3 splits the sample name at
    the first '_'), so the alignment is identifiably from trimmed reads;
  * a single-file input (ONT long reads, single-end Illumina) trims as a group
    of one and never cuts a read in half;
  * a sample already under the cap is staged byte-for-byte under its own name.

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
    group_key, read_id, read_number, stage, trimmed_name,
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


def run_stage(download: Path, step1: Path, trim_mb: int):
    """stage() logs to stdout; capture it so the test output stays readable and
    the log itself can be asserted on."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        summary = stage(download, step1, trim_mb)
    return summary, buf.getvalue()


def test_name_helpers() -> None:
    print("[name helpers]")
    # R1/R2 of one library share a key; the read number comes off the marker.
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
    check(group_key("A_R1.fastq.gz") != group_key("B_R1.fastq.gz"),
          "different samples get different keys")

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


def test_trimmed_pair_stays_matched(tmp: Path) -> None:
    print("[trim: paired Illumina]")
    download, step1 = tmp / "download", tmp / "step1"
    # Two oversized mates + one small pair that must pass through untouched.
    write_fastq(download / "TB_1234_R1.fastq.gz", 30000, 150, "TB", 1, mate=1)
    write_fastq(download / "TB_1234_R2.fastq.gz", 30000, 150, "TB", 1, mate=2)
    write_fastq(download / "SM-9_R1.fastq.gz", 400, 150, "SM", 2, mate=1)
    write_fastq(download / "SM-9_R2.fastq.gz", 400, 150, "SM", 2, mate=2)

    summary, log = run_stage(download, step1, trim_mb=1)

    # The underscored sample was dashed on the way in, then tagged.
    sample_dir = step1 / "TB-1234-trim1"
    check(sample_dir.is_dir(), "trimmed sample dir is named <sample>-trim<MB>")
    r1 = sample_dir / "TB-1234-trim1_R1.fastq.gz"
    r2 = sample_dir / "TB-1234-trim1_R2.fastq.gz"
    check(r1.is_file() and r2.is_file(), "both mates staged under the tagged name")

    rec1, rec2 = records(r1), records(r2)
    assert_eq(len(rec1), len(rec2), "R1 and R2 kept the same number of records")
    check(len(rec1) > 0, "the trim kept some reads")
    check(all(read_id(a[0].encode()) == read_id(b[0].encode())
              for a, b in zip(rec1, rec2)),
          "every kept header is still matched with its mate")
    src1 = records(download / "TB-1234_R1.fastq.gz")
    assert_eq(rec1, src1[:len(rec1)], "kept records are the head of the source, verbatim")
    check(r1.stat().st_size <= 1 * MB * 1.10 and r2.stat().st_size <= 1 * MB * 1.10,
          "each trimmed mate lands at roughly the cap")
    check(len(rec1) < len(src1), "the source really was larger than the cap")

    # Under the cap: original name, original bytes, no tag anywhere.
    check((step1 / "SM-9").is_dir(), "under-cap sample keeps its plain name")
    check(not (step1 / "SM-9-trim1").exists(), "under-cap sample is not tagged")
    assert_eq(records(step1 / "SM-9" / "SM-9_R1.fastq.gz"),
              records(download / "SM-9_R1.fastq.gz"),
              "under-cap sample is staged unchanged")
    assert_eq(summary["trimmed"], 1, "one sample reported as trimmed")
    assert_eq(summary["unchanged"], 1, "one sample reported as under the cap")
    assert_eq(summary["renamed"], 2, "both underscored mates were dashed in download/")
    check("over the cap, trimming" in log, "the log says which sample is being trimmed")
    check("[OK]" in log and "kept the first" in log,
          "the log reports how many reads were kept")

    print("[trim: re-run is a no-op]")
    summary2, _ = run_stage(download, step1, trim_mb=1)
    assert_eq(summary2["created"], 0, "a second Grab stages nothing new")
    assert_eq(summary2["skipped"], 2, "both samples are recognised as already staged")


def test_single_file_ont(tmp: Path) -> None:
    print("[trim: single-file ONT]")
    download, step1 = tmp / "download", tmp / "step1"
    # Long reads, no _R1/_R2 marker — the shape an ONT run arrives in.
    write_fastq(download / "ONT-barcode07.fastq.gz", 900, 5000, "ONT", 3)
    summary, log = run_stage(download, step1, trim_mb=1)

    out = step1 / "ONT-barcode07-trim1" / "ONT-barcode07-trim1.fastq.gz"
    check(out.is_file(), "single-file input trims as a group of one")
    kept = records(out)
    src = records(download / "ONT-barcode07.fastq.gz")
    check(0 < len(kept) < len(src), "some, but not all, long reads were kept")
    assert_eq(kept, src[:len(kept)], "kept long reads are whole and verbatim")
    check(all(len(r[1]) == 5000 for r in kept), "no long read was cut in half")
    assert_eq(summary["trimmed"], 1, "the ONT sample is reported as trimmed")
    check("reads so far" in log or "kept the first" in log,
          "the log counts reads, not pairs, for a single-file input")


def test_untrimmed_grab_is_unchanged(tmp: Path) -> None:
    print("[no trim: staged as-is]")
    download, step1 = tmp / "download", tmp / "step1"
    write_fastq(download / "Big_7_R1.fastq.gz", 12000, 150, "BIG", 4, mate=1)
    write_fastq(download / "Big_7_R2.fastq.gz", 12000, 150, "BIG", 4, mate=2)
    summary, log = run_stage(download, step1, trim_mb=0)

    check((step1 / "Big-7").is_dir(), "untrimmed Grab keeps the plain sample name")
    check(not any(p.name.startswith("Big-7-trim") for p in step1.iterdir()),
          "nothing is tagged when trimming is off")
    assert_eq(records(step1 / "Big-7" / "Big-7_R1.fastq.gz"),
              records(download / "Big-7_R1.fastq.gz"),
              "an untrimmed Grab stages the reads byte-for-byte")
    assert_eq(summary["trimmed"], 0, "nothing reported as trimmed")
    assert_eq(summary["created"], 2, "both mates staged")
    check("trim:     off" in log, "the log states that trimming is off")


def main() -> int:
    test_name_helpers()
    for fn in (test_trimmed_pair_stays_matched, test_single_file_ont,
               test_untrimmed_grab_is_unchanged):
        tmp = Path(tempfile.mkdtemp(prefix="step1stage-"))
        try:
            fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
