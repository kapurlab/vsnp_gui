"""ONT samples under vsnp3 3.37: the VCF is <sample>_nanopore_zc.vcf.

What these pin (from PR 14, FMDV mode, where the bug surfaced; the bug is a
stock one and affects every ONT project on vsnp3 3.37):
  * the sample derived from <sample>_nanopore_zc.vcf is <sample>, not
    <sample>_nanopore, so Step 2 no longer treats every ONT VCF as foreign;
  * a Step 2 table's label <sample>_nanopore still finds step1/<sample> for
    the IGV page, the file lists, the stats preview and the variant lookup;
  * a sample that really is called ..._nanopore keeps its own name.

    env/bin/python backend/app/test_nanopore_label.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="nanopore_label_")).resolve()
os.environ["XDG_CONFIG_HOME"] = str(_TMP / "xdg")
os.environ["VSNP_GUI_SITE_ROOT"] = str(_TMP / "site")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.step2_inventory import import_tail, sample_of  # noqa: E402
from app.main import _resolve_sample_dir, _step1_file_stem  # noqa: E402

FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")
    else:
        print(f"  OK  {what}")


def test_ont_vcf_names_derive_the_sample():
    check(sample_of("ETH-SAT1-001_nanopore_zc.vcf"), "ETH-SAT1-001", "ONT suffix stripped whole")
    check(sample_of("ETH-SAT1-001_nanopore_zc.vcf.gz"), "ETH-SAT1-001", "gzipped ONT suffix too")
    check(sample_of("ETH-SAT1-001_zc.vcf"), "ETH-SAT1-001", "Illumina suffix unchanged")
    check(sample_of("nanopore_zc.vcf"), "nanopore", "without the leading underscore only _zc.vcf is a suffix")
    check(import_tail("S_nanopore_zc.vcf"), ("S", "_nanopore_zc.vcf"), "import_tail keeps the full ONT suffix")


def test_step2_label_resolves_the_step1_folder():
    step1 = _TMP / "step1"
    (step1 / "SYN-05").mkdir(parents=True)
    (step1 / "nanopore").mkdir()
    (step1 / "X_nanopore").mkdir()
    check(_resolve_sample_dir(step1, "SYN-05_nanopore"), step1 / "SYN-05", "_nanopore label finds the folder")
    check(_resolve_sample_dir(step1, "SYN-05"), step1 / "SYN-05", "plain name still exact")
    check(_resolve_sample_dir(step1, "nanopore"), step1 / "nanopore", "a sample called nanopore is itself")
    check(_resolve_sample_dir(step1, "X_nanopore"), step1 / "X_nanopore", "a real _nanopore folder wins")
    check(_resolve_sample_dir(step1, "SYN-99_nanopore"), None, "a missing sample is still a miss")
    check(_step1_file_stem(step1 / "SYN-05", "SYN-05_nanopore"), "SYN-05", "files are named after the folder")
    check(_step1_file_stem(step1 / "SYN-05", "SYN-05"), "SYN-05", "plain name unchanged")
    check(_step1_file_stem(step1 / "X_nanopore", "X_nanopore"), "X_nanopore", "a real _nanopore folder keeps its name")


def main() -> int:
    try:
        test_ont_vcf_names_derive_the_sample()
        test_step2_label_resolves_the_step1_folder()
    finally:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
    for f in FAILS:
        print("  FAIL " + f)
    print(f"\n{len(FAILS)} FAILURE(S)" if FAILS else "\nAll nanopore label tests passed.")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
