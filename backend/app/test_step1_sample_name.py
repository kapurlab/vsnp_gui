"""Unit tests for app.main._sanitized_sample_and_name (pure function).

vSNP3 derives the sample name from the FASTQ filename by splitting at the
FIRST '_'. An underscore inside the sample prefix collapses distinct samples
to a shared prefix (Mg_280, Mg_281, … -> all "Mg"), silently merging them in
Step 2. step1_setup dashes the prefix (Mg_280 -> Mg-280) before staging.

Run from anywhere with the per-site conda python:

    <conda>/bin/python backend/app/test_step1_sample_name.py
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import _sanitized_sample_and_name  # noqa: E402


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label}")


def main() -> int:
    print("[_sanitized_sample_and_name]")
    # Underscored prefixes get dashed; _R1/_R2 indicator preserved.
    assert_eq(_sanitized_sample_and_name("Mg_280_R1.fastq.gz"),
              ("Mg-280", "Mg-280_R1.fastq.gz"), "Mg_280 R1 dashed")
    assert_eq(_sanitized_sample_and_name("Mg_280_R2.fastq.gz"),
              ("Mg-280", "Mg-280_R2.fastq.gz"), "Mg_280 R2 dashed")
    # Lane suffix (_001) preserved.
    assert_eq(_sanitized_sample_and_name("Mg_280_R1_001.fastq.gz"),
              ("Mg-280", "Mg-280_R1_001.fastq.gz"), "lane suffix preserved")
    # Multiple internal underscores (GISAID-style names) all dashed.
    assert_eq(
        _sanitized_sample_and_name(
            "hCoV-19-deer-USA-IA-200443-2021-EPI_ISL_5804774-2021-01-09_R1.fastq.gz"),
        ("hCoV-19-deer-USA-IA-200443-2021-EPI-ISL-5804774-2021-01-09",
         "hCoV-19-deer-USA-IA-200443-2021-EPI-ISL-5804774-2021-01-09_R1.fastq.gz"),
        "GISAID EPI_ISL dashed")

    # Sample IDs that END in _1 / _2: the greedy prefix must bind the read
    # marker to the RIGHTMOST _R1/_R2, not the trailing _2 in the ID. (Regression
    # for the non-greedy bug that collapsed Mg_2 back to "Mg".)
    assert_eq(_sanitized_sample_and_name("Mg_2_R1.fastq.gz"),
              ("Mg-2", "Mg-2_R1.fastq.gz"), "ID ending in _2 + _R1 dashed to Mg-2")
    assert_eq(_sanitized_sample_and_name("Mg_1_R2.fastq.gz"),
              ("Mg-1", "Mg-1_R2.fastq.gz"), "ID ending in _1 + _R2 dashed to Mg-1")
    assert_eq(_sanitized_sample_and_name("Mg_3_R1.fastq.gz"),
              ("Mg-3", "Mg-3_R1.fastq.gz"), "ID ending in _3 stays consistent")
    # Bare SRA-style _1/_2 IS the read marker (no _R), so the sample is the
    # prefix before it — Mg_1.fastq.gz is read 1 of sample "Mg", not "Mg_1".
    assert_eq(_sanitized_sample_and_name("Mg_1.fastq.gz"),
              ("Mg", "Mg_1.fastq.gz"), "bare _1 is the read marker, sample=Mg")

    # No-underscore prefixes are returned UNCHANGED (idempotent / no churn).
    for name in ("Mg282_R1.fastq.gz", "Mg220_R1.fastq.gz", "SRR1234567_1.fastq.gz"):
        sample, out = _sanitized_sample_and_name(name)
        assert_eq(out, name, f"{name} untouched")
        if "_" in sample:
            raise AssertionError(f"{name}: sample {sample!r} still has '_'")

    # Invariant across all: the returned sample prefix never contains '_'.
    for name in ("Mg_280_R1.fastq.gz", "a_b_c_R2.fastq.gz", "x_1.fastq.gz"):
        sample, _out = _sanitized_sample_and_name(name)
        if "_" in sample:
            raise AssertionError(f"{name}: sample {sample!r} still has '_'")
    print("  OK  no returned sample prefix contains '_'")

    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
