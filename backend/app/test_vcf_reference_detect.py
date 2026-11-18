"""The reference a VCF header names, when a library genome's name is also a
folder on the path.

A reference database built by calling genomes against one reference (the FMDV
add-on's panel builds) keeps each VCF under a folder named for the genome, which
is itself a known reference:
panel_build/SAT1-I_KM268899/SAT1-III_AY593843/SAT1-I_KM268899.fasta.
Two known names sit on that path. The FASTA stem is the file the calls were made
against, so it settles the tie; the first name in dict order used to win, and an
import of the panel without an explicit reference was refused as mixed.

Run directly:  python test_vcf_reference_detect.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import _normalize_reference  # noqa: E402


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def main() -> int:
    aliases = {
        "SAT1-III_AY593843": "SAT1-III_AY593843",
        "SAT1-I_KM268899": "SAT1-I_KM268899",
        "NC_006932-NC_006933": "Brucella_abortus1",
    }
    print("[_normalize_reference]")
    assert_eq(
        _normalize_reference("/x/panel_build/SAT1-I_KM268899/SAT1-III_AY593843/SAT1-I_KM268899.fasta", aliases),
        "SAT1-I_KM268899", "genome folder on the path, FASTA stem decides")
    assert_eq(
        _normalize_reference("/x/panel_build/SAT1-I_KM268899/SAT1-III_AY593843/SAT1-III_AY593843.fasta", aliases),
        "SAT1-III_AY593843", "the other way round")
    assert_eq(
        _normalize_reference("file:///home/u/projects/P/step1/S1/SAT1-I_KM268899.fasta", aliases),
        "SAT1-I_KM268899", "one name on the path, as before")
    assert_eq(
        _normalize_reference("/srv/refs/Brucella_abortus1/NC_006932-NC_006933.fasta", aliases),
        "Brucella_abortus1", "folder name wins when the stem is an alias of it")
    assert_eq(
        _normalize_reference("/tmp/NC_006932-NC_006933.fasta", aliases),
        "Brucella_abortus1", "no folder match, stem alias")
    assert_eq(_normalize_reference("/tmp/other ref.fasta", {}), "other_ref", "unknown stem, spaces to underscores")
    print("\nAll reference-detection tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
