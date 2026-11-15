"""The FMDV-mode pieces that live in main.py and step2_inventory.py.

What these pin:
  * vsnp3 3.37 names an ONT VCF <sample>_nanopore_zc.vcf; the sample derived
    from it is <sample>, not <sample>_nanopore (the stock bug that made every
    ONT VCF a foreign one in Step 2);
  * the FMDV scope grammar: FMDV, a serotype, serotype/topotype, or
    genome:<safe name>; anything else is a 400;
  * which primer set a project gets: "none" means none, a named set must
    exist, otherwise default.fasta, otherwise the only set, otherwise none.

    <conda>/bin/python backend/app/test_fmdv_mode_main.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from app.step2_inventory import import_tail, sample_of  # noqa: E402
from app.main import _fmdv_primer_set, _require_fmdv_scope, _resolve_sample_dir, _step1_file_stem  # noqa: E402

FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")


def test_ont_vcf_names_derive_the_sample():
    check(sample_of("ETH-SAT1-001_nanopore_zc.vcf"), "ETH-SAT1-001", "ONT suffix stripped whole")
    check(sample_of("ETH-SAT1-001_nanopore_zc.vcf.gz"), "ETH-SAT1-001", "gzipped ONT suffix too")
    check(sample_of("ETH-SAT1-001_zc.vcf"), "ETH-SAT1-001", "Illumina suffix unchanged")
    check(sample_of("nanopore_zc.vcf"), "nanopore", "without the leading underscore only _zc.vcf is a suffix")
    check(import_tail("S_nanopore_zc.vcf"), ("S", "_nanopore_zc.vcf"), "import_tail keeps the full ONT suffix")


def test_step2_label_resolves_the_step1_folder():
    # A Step 2 table labels an ONT sample by its VCF stem, <sample>_nanopore.
    # The IGV page and the file lists must still find step1/<sample>.
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        step1 = Path(td) / "step1"
        (step1 / "SYN-05").mkdir(parents=True)
        (step1 / "nanopore").mkdir()
        check(_resolve_sample_dir(step1, "SYN-05_nanopore"), step1 / "SYN-05", "_nanopore label finds the folder")
        check(_resolve_sample_dir(step1, "SYN-05"), step1 / "SYN-05", "plain name still exact")
        check(_resolve_sample_dir(step1, "nanopore"), step1 / "nanopore", "a sample called nanopore is itself")
        check(_resolve_sample_dir(step1, "SYN-99_nanopore"), None, "a missing sample is still a miss")
        check(_step1_file_stem(step1 / "SYN-05", "SYN-05_nanopore"), "SYN-05", "files are named after the folder")
        check(_step1_file_stem(step1 / "SYN-05", "SYN-05"), "SYN-05", "plain name unchanged")
        check(_step1_file_stem(step1 / "X_nanopore", "X_nanopore"), "X_nanopore", "a real _nanopore folder keeps its name")


def test_scope_grammar():
    for ok in ("FMDV", "SAT1", "SAT1/IX", "ASIA1", "O/ME-SA", "genome:SAT1-I_KM268899"):
        try:
            check(_require_fmdv_scope(ok), ok, f"scope {ok!r} accepted as written")
        except HTTPException as e:
            FAILS.append(f"scope {ok!r} refused: {e.detail}")
    check(_require_fmdv_scope(None), "FMDV", "empty means FMDV")
    check(_require_fmdv_scope("  SAT1 "), "SAT1", "surrounding space trimmed")
    for bad in ("../x", "SAT1;rm", "a b", "genome:", "SAT1/IX/extra/deep/x" * 10):
        try:
            _require_fmdv_scope(bad)
            FAILS.append(f"scope {bad!r} should have been refused")
        except HTTPException as e:
            check(e.status_code, 400, f"scope {bad!r} is a 400")


def test_primer_set_choice():
    with tempfile.TemporaryDirectory() as td:
        lib = Path(td) / "lib"
        proj = Path(td) / "proj"
        proj.mkdir()
        pj = proj / "project.json"

        def meta(**kw):
            pj.write_text(json.dumps(kw))

        meta()
        check(_fmdv_primer_set(lib, proj), "", "no primers folder: no set")
        (lib / "primers").mkdir(parents=True)
        (lib / "primers" / "nanofmdv_L.fasta").write_text(">p\nACGT\n")
        check(Path(_fmdv_primer_set(lib, proj)).name, "nanofmdv_L.fasta", "a single set is the default")
        (lib / "primers" / "other.fasta").write_text(">q\nACGT\n")
        check(_fmdv_primer_set(lib, proj), "", "two sets and no default.fasta: none chosen")
        (lib / "primers" / "default.fasta").write_text(">d\nACGT\n")
        check(Path(_fmdv_primer_set(lib, proj)).name, "default.fasta", "default.fasta wins when present")
        meta(fmdv_primers="other.fasta")
        check(Path(_fmdv_primer_set(lib, proj)).name, "other.fasta", "the project can name a set")
        meta(fmdv_primers="../../etc/passwd")
        check(_fmdv_primer_set(lib, proj), "", "a path outside the folder is not followed")
        meta(fmdv_primers="none")
        check(_fmdv_primer_set(lib, proj), "", "none means no clipping")
        meta(fmdv_primers="missing.fasta")
        check(_fmdv_primer_set(lib, proj), "", "a named set that does not exist gives none, not a crash")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"FAIL — {len(FAILS)} assertion(s)")
    for f in FAILS:
        print("  " + f)
    sys.exit(1)
print("ok — FMDV mode pieces in main.py")
