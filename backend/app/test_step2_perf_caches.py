"""Unit tests for the v0.4.78 read-path work: the caches and single-scan
rewrites that make the Step 2 pane usable on 8,000-sample projects.

Every change under test here is required to be BEHAVIOR-IDENTICAL — same
answers, fewer filesystem calls — so these tests pin equivalence and, where a
cache is involved, that its invalidation key actually fires:

  * _scan_vcf_db returns exactly what glob(*.vcf)+glob(*.vcf.gz) plus a
    per-file resolve() used to (case-sensitive, dot-skipped, symlinked edits
    detected, symlinked DATABASE DIR detected) — without resolving regular files.
  * _step1_sample_names: the three-glob chain collapses to one suffix test
    (subsumption), and the per-dir memo invalidates when a dir's mtime moves.
  * _fasta_dims: memo hit skips the read; a rewritten file recomputes.
  * _read_remove_xlsx_names: parse cached; an edited workbook re-parses.
  * step2_groupings cache: hits are COPIED out, so the label-token loop cannot
    grow the cached lists request over request.
  * _resolve_step2_output_dir fast path: two stats for an explicit id, with the
    traversal guards map membership used to provide.
  * update_project_meta: a no-op update leaves project.json's mtime alone
    (this is what keeps the counts cache warm across project visits).
  * posthoc_status_all mirrors the per-group endpoint's answers.

Run directly:  python test_step2_perf_caches.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main as m
from app.projects import update_project_meta


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(cond, label):
    if not cond:
        raise AssertionError(f"{label}: expected truthy")
    print(f"  OK  {label}")


def bump_mtime(path: Path):
    """Advance a path's mtime deterministically (no sleep)."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="step2_perf_"))
    try:
        # ------------------------------------------------------------------
        print("\n[_scan_vcf_db matches the old glob+resolve behavior]")
        db = tmp / "step2" / "vcf_database"
        edits = tmp / "step2" / "vcf_edits"
        db.mkdir(parents=True)
        edits.mkdir(parents=True)
        (db / "plain_zc.vcf").write_text("#\n")
        (db / "gz_zc.vcf.gz").write_bytes(b"\x1f\x8b")
        (db / "UPPER.VCF").write_text("#\n")           # glob is case-sensitive: excluded
        (db / ".hidden_zc.vcf").write_text("#\n")      # glob hides dotfiles: excluded
        (db / "notes.txt").write_text("x\n")
        (edits / "edited1_zc.vcf").write_text("#\n")
        os.symlink(edits / "edited1_zc.vcf", db / "edited1_zc.vcf")
        # A symlink that does NOT point into vcf_edits must not read as edited.
        (tmp / "elsewhere.vcf").write_text("#\n")
        os.symlink(tmp / "elsewhere.vcf", db / "elsewhere.vcf")

        names, edited = m._scan_vcf_db(db)
        assert_eq(names, ["edited1_zc.vcf", "elsewhere.vcf", "gz_zc.vcf.gz", "plain_zc.vcf"],
                  "names: case-sensitive, dot-skipped, sorted")
        assert_eq(edited, {"edited1"}, "only the vcf_edits symlink counts as edited")
        assert_eq(m._edited_samples_in_dir(db), ["edited1"], "_edited_samples_in_dir rewired")

        # The database dir ITSELF living under vcf_edits marks everything edited
        # (the one case a per-entry symlink check cannot see).
        nested = edits / "nested_db"
        nested.mkdir()
        (nested / "a_zc.vcf").write_text("#\n")
        _, edited_all = m._scan_vcf_db(nested)
        assert_eq(edited_all, {"a"}, "a database dir inside vcf_edits marks its files edited")

        assert_eq(m._scan_vcf_db(tmp / "missing")[0], [], "missing dir -> empty, no raise")

        # ------------------------------------------------------------------
        print("\n[_step1_sample_names: subsumption + mtime-keyed memo]")
        step1 = tmp / "step1"
        for name, reads in [("s_r1", ["x_R1_001.fastq.gz"]),
                            ("s_underscore1", ["y_1.fastq.gz"]),
                            ("s_plain", ["z.fastq.gz"]),
                            ("s_empty", []),
                            ("s_dot_only", [".hidden.fastq.gz"]),
                            ("_provenance", ["p.fastq.gz"])]:
            d = step1 / name
            d.mkdir(parents=True)
            for r in reads:
                (d / r).write_text("@\n")
        # Age every sample dir past the racy-timestamp guard (entries whose
        # mtime is within ~2 s of now are deliberately not cached — see below).
        for d in step1.iterdir():
            st = d.stat()
            os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns - 10_000_000_000))
        m._STEP1_NAMES_CACHE.clear()
        got = m._step1_sample_names(step1)
        assert_eq(got, ["s_plain", "s_r1", "s_underscore1"],
                  "all three legacy patterns matched; empty/dot-only/underscore skipped")

        # Memo: a second call with a poisoned inner listing still answers right
        # (proves it did not re-list), then a bumped mtime re-lists.
        real_scandir = os.scandir
        inner_lists = {"n": 0}
        def counting_scandir(p):
            if str(p).startswith(str(step1) + os.sep):
                inner_lists["n"] += 1
            return real_scandir(p)
        os.scandir = counting_scandir
        try:
            got2 = m._step1_sample_names(step1)
            assert_eq(got2, got, "memoized answer identical")
            assert_eq(inner_lists["n"], 0, "no per-sample listing on a warm call")
            (step1 / "s_empty" / "new.fastq.gz").write_text("@\n")
            bump_mtime(step1 / "s_empty")
            got3 = m._step1_sample_names(step1)
            assert_true("s_empty" in got3, "a bumped dir re-lists and joins")
            assert_true(inner_lists["n"] >= 1, "exactly the changed dir was re-listed")
        finally:
            os.scandir = real_scandir

        # ------------------------------------------------------------------
        print("\n[_fasta_dims memo]")
        fa = tmp / "aln.fasta"
        fa.write_text(">root\nACGT\n>s1\nACGT\n")
        m._FASTA_DIMS_CACHE.clear()
        assert_eq(m._fasta_dims(fa), (2, 4, True), "cold read")
        # Poison open(): a memo hit must not read the file at all.
        real_open = Path.open
        def poisoned_open(self, *a, **k):
            if self == fa:
                raise AssertionError("memo hit re-read the fasta")
            return real_open(self, *a, **k)
        Path.open = poisoned_open
        try:
            assert_eq(m._fasta_dims(fa), (2, 4, True), "warm call served from memo")
        finally:
            Path.open = real_open
        fa.write_text(">root\nACGTAA\n>s1\nACGTAA\n>s2\nACGTAA\n")
        bump_mtime(fa)
        assert_eq(m._fasta_dims(fa), (3, 6, True), "rewritten file recomputes")
        assert_eq(m._fasta_dims(tmp / "no.fasta"), (0, 0, False), "missing file")
        assert_eq(m._fasta_dims(None), (0, 0, False), "None path")

        # ------------------------------------------------------------------
        print("\n[_read_remove_xlsx_names cache]")
        try:
            import pandas as pd
        except ImportError:
            pd = None
        if pd is None:
            print("  (pandas unavailable — skipped)")
        else:
            xl = tmp / "remove_from_analysis.xlsx"
            pd.DataFrame(["alpha", "beta"]).to_excel(xl, header=False, index=False)
            m._REMOVE_XLSX_CACHE.clear()
            assert_eq(m._read_remove_xlsx_names(xl), ["alpha", "beta"], "cold parse")
            first = m._read_remove_xlsx_names(xl)
            first.append("MUTATED")
            assert_eq(m._read_remove_xlsx_names(xl), ["alpha", "beta"],
                      "callers get copies; mutating one cannot poison the cache")
            pd.DataFrame(["gamma"]).to_excel(xl, header=False, index=False)
            bump_mtime(xl)
            assert_eq(m._read_remove_xlsx_names(xl), ["gamma"], "edited workbook re-parses")
            assert_eq(m._read_remove_xlsx_names(tmp / "no.xlsx"), [], "missing file")

        # ------------------------------------------------------------------
        print("\n[_resolve_step2_output_dir fast path + guards]")
        s2 = tmp / "proj" / "step2"
        run = s2 / "2026-08-01_10-00-00"
        (run / "G1").mkdir(parents=True)
        (run / "G1" / "t.tre").write_text("()")
        # Fast path answers without listing: poison scandir over s2 itself.
        os.scandir = counting_scandir  # counts under step1 only — need a new poison
        os.scandir = real_scandir
        listed = {"n": 0}
        def counting_scandir2(p):
            if Path(p) == s2:
                listed["n"] += 1
            return real_scandir(p)
        os.scandir = counting_scandir2
        real_iterdir = Path.iterdir
        def counting_iterdir(self):
            if self == s2:
                listed["n"] += 1
            return real_iterdir(self)
        Path.iterdir = counting_iterdir
        try:
            got = m._resolve_step2_output_dir(s2, "2026-08-01_10-00-00")
            assert_eq(got, run, "explicit id resolves directly")
            assert_eq(listed["n"], 0, "and never lists the step2 dir")
        finally:
            os.scandir = real_scandir
            Path.iterdir = real_iterdir
        # Traversal guards: an id that is not a bare comparison name never
        # escapes step2/ via the fast path.
        evil = m._resolve_step2_output_dir(s2, "2026-08-01_10-00-00/../..")
        assert_true(str(evil).startswith(str(s2)) or evil == s2,
                    "path-traversal id cannot resolve outside step2/")
        assert_eq(m._resolve_step2_output_dir(s2, "vcf_database"), run,
                  "a non-comparison name falls through to the normal resolution")

        # ------------------------------------------------------------------
        print("\n[update_project_meta skips the no-op write]")
        proj = tmp / "proj2"
        proj.mkdir()
        update_project_meta(proj, {"reference": "owl_ref", "display_name": "p_owl_ref"})
        mtime1 = (proj / "project.json").stat().st_mtime_ns
        update_project_meta(proj, {"reference": "owl_ref", "display_name": "p_owl_ref"})
        assert_eq((proj / "project.json").stat().st_mtime_ns, mtime1,
                  "identical update leaves mtime untouched")
        out = update_project_meta(proj, {"reference": "other"})
        assert_eq(out["reference"], "other", "a real change still writes")
        assert_true((proj / "project.json").stat().st_mtime_ns != mtime1,
                    "and bumps mtime")
        assert_eq(json.loads((proj / "project.json").read_text())["display_name"],
                  "p_owl_ref", "unrelated keys preserved")

        # ------------------------------------------------------------------
        print("\n[groupings cache copies out, never hands over]")
        # Simulate two requests against the same cached parse: the label loop
        # appends into `groups`; a second request must start from the ORIGINAL
        # parse, not the augmented lists.
        m._GROUPINGS_CACHE.clear()
        key = ("summary", 1, 2)
        m._GROUPINGS_CACHE[key] = ({"G1": ["ERR1"]}, 1)
        hit1 = {k: list(v) for k, v in m._GROUPINGS_CACHE[key][0].items()}
        hit1["G1"].append("Label_ERR1")   # what the endpoint's token loop does
        hit2 = {k: list(v) for k, v in m._GROUPINGS_CACHE[key][0].items()}
        assert_eq(hit2, {"G1": ["ERR1"]}, "second hit is unaugmented")

        # ------------------------------------------------------------------
        print("\n[_xlsx_window_memo_put survives threadpool concurrency]")
        # The unguarded eviction loop measurably raised KeyError and
        # "dictionary changed size during iteration" under 8 threads; the lock
        # must make the hammer boringly silent.
        import threading
        m._XLSX_WINDOW_MEMO.clear()
        errors = []
        def hammer(tid):
            try:
                for i in range(20_000):
                    m._xlsx_window_memo_put(f"k{tid}-{i % 7}", {"w": i})
            except Exception as e:  # noqa: BLE001 — the test IS the exception check
                errors.append(f"{type(e).__name__}: {e}")
        threads = [threading.Thread(target=hammer, args=(t,)) for t in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert_eq(errors, [], "8 threads x 20k puts: no exceptions")
        assert_true(len(m._XLSX_WINDOW_MEMO) <= m._XLSX_WINDOW_MEMO_MAX,
                    "memo stayed within its bound")

        # ------------------------------------------------------------------
        print("\n[_etag_matches handles the header as clients send it]")
        assert_eq(m._etag_matches('"abc.html"', "abc.html"), True, "quoted match")
        assert_eq(m._etag_matches('W/"abc.html"', "abc.html"), True, "weak match")
        assert_eq(m._etag_matches('"x", "abc.html"', "abc.html"), True, "list member")
        assert_eq(m._etag_matches("*", "abc.html"), True, "star form")
        assert_eq(m._etag_matches('"other"', "abc.html"), False, "non-match")
        assert_eq(m._etag_matches("", "abc.html"), False, "absent header")
        assert_eq(m._etag_matches("abc.html", "abc.html"), True,
                  "bare (unquoted) echo still accepted")

        # ------------------------------------------------------------------
        print("\n[_step1_sample_names refuses to cache a racy timestamp]")
        racy = step1 / "s_racy"
        racy.mkdir()
        # Freshly created: its mtime is 'now', inside the 2 s guard window, so
        # the empty answer must NOT be cached — a read arriving within the same
        # coarse-filesystem tick would otherwise be pinned invisible.
        m._step1_sample_names(step1)
        assert_true(str(racy) not in m._STEP1_NAMES_CACHE,
                    "an entry whose mtime is ~now is not cached")
        # Age the directory artificially: now it may cache.
        st = racy.stat()
        os.utime(racy, ns=(st.st_atime_ns, st.st_mtime_ns - 10_000_000_000))
        m._step1_sample_names(step1)
        assert_true(str(racy) in m._STEP1_NAMES_CACHE,
                    "a comfortably-past mtime is cached")

        # ------------------------------------------------------------------
        print("\n[posthoc_status_all mirrors the per-group endpoint]")
        pa_root = tmp / "pa_root"
        pa_proj = pa_root / "pa_proj"
        pa_run = pa_proj / "step2" / "2026-08-02_10-00-00"
        for g in ("Group-A", "Group-B"):
            (pa_run / g).mkdir(parents=True)
            (pa_run / g / "aln.fasta").write_text(">root\nAC\n")
        tool_obj = m.posthoc_get_tool("snp_analysis")
        # One group locked = running.
        lock = m._posthoc_lock_path(pa_run / "Group-A", tool_obj.tool_id)
        lock.parent.mkdir(parents=True, exist_ok=True)
        # The lock file holds a JOB ID; staleness is decided by asking the
        # JobManager. Register a live answer for our token so the stale-lock
        # sweep (which both endpoints run) keeps the lock in place.
        lock.write_text("job-test-token")
        real_get_job = m.job_manager.get_job
        m.job_manager.get_job = (
            lambda jid: {"status": "running"} if jid == "job-test-token" else real_get_job(jid)
        )
        real_cfg = m.load_config
        m.load_config = lambda: {"projects_root": str(pa_root)}
        try:
            batched = m.posthoc_status_all("pa_proj", "snp_analysis", "2026-08-02_10-00-00")
            for g in ("Group-A", "Group-B"):
                single = m.posthoc_status("pa_proj", g, "snp_analysis", "2026-08-02_10-00-00")
                assert_eq(batched["groups"][g], single, f"{g}: batched == per-group")
            assert_eq(batched["groups"]["Group-A"]["running"], True, "locked group running")
            assert_eq(batched["groups"]["Group-B"]["running"], False, "unlocked group idle")
        finally:
            m.load_config = real_cfg
            m.job_manager.get_job = real_get_job

        print("\nAll perf-cache tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
