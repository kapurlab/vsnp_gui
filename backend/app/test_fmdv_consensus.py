"""The reference-anchored consensus rules (bin/fmdv_consensus.py).

What these pin:
  * a vsnp3 ALT above QUAL 150 is applied; one at or under it becomes N, not
    the reference base (five such sites at depth 9 to 16 were printed as
    reference before the NanoFMDV comparison caught it);
  * AC=1 (vsnp3's ambiguous call) and a zero-coverage REF=N record both mask;
  * indel records never move coordinates;
  * the pileup pass reads mpileup text correctly (indel runs and read marks
    skipped) and returns the plurality base with its count and depth;
  * VP1 coordinates come from the per-reference cache when it exists.

    <conda>/bin/python backend/app/test_fmdv_consensus.py
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("fmdv_consensus", ROOT / "bin" / "fmdv_consensus.py")
fc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fc)

FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")


VCF = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
    "KM268899\t1\t.\tN\t.\t.\t.\t.\tGT\t./.\n"            # zero coverage
    "KM268899\t10\t.\tA\tG\t900\t.\tDP=200;AC=2\tGT\t1/1\n"  # trusted ALT
    "KM268899\t20\t.\tC\tT\t50\t.\tDP=10;AC=2\tGT\t1/1\n"    # sub-threshold ALT
    "KM268899\t30\t.\tG\tA\t700\t.\tDP=100;AC=1;AN=2\tGT\t0/1\n"  # ambiguous
    "KM268899\t40\t.\tAT\tA\t800\t.\tDP=100;AC=2\tGT\t1/1\n"  # deletion: skipped
    "KM268899\t50\t.\tT\t.\t.\t.\tDP=5\tGT\t0/0\n"            # no call
)


def test_vcf_rules():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "s_nanopore_zc.vcf"
        p.write_text(VCF)
        subs, zero, ambiguous = fc.vcf_calls(p, 150)
        check(zero, [1], "REF=N record is a zero-coverage position")
        check(subs.get(10), "G", "QUAL 900 ALT applied")
        check(subs.get(20), "N", "QUAL 50 ALT becomes N, never the reference base")
        check(subs.get(30), "N", "AC=1 becomes N")
        check(40 in subs, False, "an indel record leaves coordinates alone")
        check(50 in subs, False, "a no-call record is ignored")
        check(ambiguous, 2, "both masked ALTs counted as ambiguous")


def test_fasta_round_trip():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.fasta"
        fc.write_fasta(p, "KM268899 ref=SAT1-I", "ACGT" * 40, width=70)
        name, seq = fc.read_fasta(p)
        check(name, "KM268899", "header keeps the first token")
        check(seq, "ACGT" * 40, "sequence intact across line wrapping")


def test_pileup_parsing(monkeypatch=None):
    mpileup = (
        "KM268899\t1\tA\t10\t..........\tIIIIIIIIII\n"               # all reference
        "KM268899\t2\tC\t12\t,,,.TTTTTTT$T\tIIIIIIIIIIII\n"          # 8 T of 12: plurality 0.67
        "KM268899\t3\tG\t10\t^].+2AT..-1C.......\tIIIIIIIIII\n"      # indel runs and start mark skipped
        "KM268899\t4\tT\t3\t..,\tIII\n"                              # under depth
        "KM268899\t5\tA\t20\tGGGGGGGGGGGGGGGGGGGG\tIIIIIIIIIIIIIIIIIIII\n"  # 100% G
    )
    real = subprocess.run

    class R:
        stdout = mpileup
        returncode = 0

    subprocess.run = lambda *a, **k: R()
    try:
        out = fc.pileup_majority("samtools", Path("x.bam"), Path("x.fasta"), 10, 0.5)
    finally:
        subprocess.run = real
    check(out.get(1), ("A", 10, 10), "reference-only column counts as reference")
    check(out.get(2), ("T", 8, 12), "plurality T with count and depth, $ marks ignored")
    check(out.get(3), ("G", 10, 10), "insertion and deletion runs and ^ marks do not count as bases")
    check(4 in out, False, "a column under min depth is left out")
    check(out.get(5), ("G", 20, 20), "a unanimous non-reference base")


def test_vp1_coords_cache_is_read_first():
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        cache = lib / "reference_options" / "SAT1-I_X" / "VP1_coords.json"
        cache.parent.mkdir(parents=True)
        cache.write_text(json.dumps({"reference": "SAT1-I_X", "start": 3178, "end": 3834}))
        got = fc.vp1_coords(lib, "SAT1-I_X", Path(td) / "ref.fasta", Path(td))
        check((got["start"], got["end"]), (3178, 3834), "cached coordinates returned without minimap2")
        got = fc.vp1_coords(lib, "SAT1-II_Y", Path(td) / "ref.fasta", Path(td))
        check(got, None, "no cache and no panel: VP1 skipped, not an error")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"FAIL — {len(FAILS)} assertion(s)")
    for f in FAILS:
        print("  " + f)
    sys.exit(1)
print("ok — fmdv consensus rules")
