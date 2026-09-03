"""The gate that makes a wrong comparison impossible to publish silently.

Requested / staged / vsnp3-visible must be the same set of samples. Every bug
this exists for produced a run that started cleanly, finished cleanly and
analysed the wrong samples — the pane said 25, the folder held 26, vsnp3
reported 26, and nothing compared those numbers to each other.

  cd backend/app && ../../env/bin/python test_step2_reconcile.py
"""

import gzip
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.step2_reconcile import reconcile, staged_samples, vsnp3_visible  # noqa: E402

FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")


def _run_dir(tmp, names):
    d = Path(tmp) / "run"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        if n.endswith(".gz"):
            with gzip.open(d / n, "wt") as fh:
                fh.write("##fileformat=VCFv4.2\n")
        else:
            (d / n).write_text("##fileformat=VCFv4.2\n")
    return d


def test_agreement_is_silent():
    with tempfile.TemporaryDirectory() as tmp:
        d = _run_dir(tmp, ["A_zc.vcf", "B_zc.vcf"])
        r = reconcile(d, ["A", "B"], [])
        check(r["ok"], True, f"a matching run passes: {r['problems']}")
        check(r["counts"], {"requested": 2, "staged": 2, "analyzable": 2}, "all three agree")


def test_refuses_a_stowaway():
    """The Ames case: a file joined the run that nobody selected."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _run_dir(tmp, ["A_zc.vcf", "16-014020-020_Bovine_Maiduguri-NG.vcf"])
        r = reconcile(d, ["A"], [])
        check(r["ok"], False, "a staged-but-unrequested file is refused")
        joined = " ".join(r["problems"])
        check("16-014020-020_Bovine_Maiduguri-NG" in joined, True,
              f"and the culprit is named: {joined}")


def test_refuses_a_staged_file_vsnp3_cannot_read():
    """A .vcf.gz in the run folder is counted by us and ignored by vsnp3."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _run_dir(tmp, ["A_zc.vcf", "B_zc.vcf.gz"])
        r = reconcile(d, ["A", "B"], [])
        check(r["ok"], False, "a gzipped staged file is refused")
        check("unreadable by vsnp3" in " ".join(r["problems"]), True,
              f"named as such: {r['problems']}")
        check(r["counts"], {"requested": 2, "staged": 2, "analyzable": 1},
              "staged says 2, vsnp3 would read 1 — exactly the silent overcount")


def test_refuses_when_remove_by_name_would_drop_a_requested_sample():
    """Asking for A while the removal workbook still names it."""
    with tempfile.TemporaryDirectory() as tmp:
        d = _run_dir(tmp, ["A_zc.vcf", "B_zc.vcf"])
        r = reconcile(d, ["A", "B"], ["A"])
        check(r["ok"], False, "the run would be one sample short and says so")
        check(r["counts"]["analyzable"], 1, "vsnp3 would compare only B")


def test_refuses_when_a_requested_file_never_arrived():
    with tempfile.TemporaryDirectory() as tmp:
        d = _run_dir(tmp, ["A_zc.vcf"])
        r = reconcile(d, ["A", "B"], [])
        check(r["ok"], False, "a requested sample missing from the folder is refused")
        check("requested but not staged" in " ".join(r["problems"]), True,
              f"named as such: {r['problems']}")


def test_visibility_uses_vsnp3s_own_pattern():
    with tempfile.TemporaryDirectory() as tmp:
        d = _run_dir(tmp, ["A_zc.vcf", "B_zc.vcf.gz", "C.vcf"])
        check(vsnp3_visible(d, []), {"A", "C"}, "glob('*vcf') skips the .gz")
        check(staged_samples(d), {"A", "B", "C"}, "the folder holds all three")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"FAIL — {len(FAILS)} assertion(s)")
    for f in FAILS:
        print("  " + f)
    sys.exit(1)
print("ok — step2 dispatch reconciliation")
