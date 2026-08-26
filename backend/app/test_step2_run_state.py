"""Unit tests for Step 2 run-folder state: the shape scan, the state names,
and the resolver that decides which comparison the results pane opens on.

These cover the failure that made an 8,000-VCF influenza project report "No
Step 2 outputs found yet." while holding 294 finished comparisons. step2/run
creates a folder, copies every comparison VCF into it, and only then launches
vsnp3 — which consumes those copies and writes group folders in their place. So
a folder full of loose VCFs with no group folders is what every run looks like
mid-staging, and what a run whose job never started looks like forever. The
resolver used to open the newest folder by name unconditionally, so it landed on
exactly that folder and the pane went blank.

What has to stay pinned down here:

  * _dir_holds_a_file agrees with what step2_outputs will actually render for a
    group (files at the top, or under posthoc/). If it says yes where the pane
    says no, the resolver picks a folder the pane draws empty — the same bug in
    a new place.
  * The resolver skips output-less folders but still yields to a LIVE run, and
    still honours an explicit run_id even when that folder has nothing.
  * _step2_read_run_metadata survives every shape of damaged run_metadata.json.
    Five of them used to return a 500 for the whole listing, which the frontend
    swallowed silently: no dropdown, no comparisons, no error.
  * The shape scan does not stat its way through a staging folder.

Run directly:  python test_step2_run_state.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import (
    _dir_holds_a_file,
    _resolve_step2_output_dir,
    _step2_read_run_metadata,
    _step2_run_dirs,
    _step2_run_shape,
    _step2_run_state,
    _step2_runs_newest_first,
    _step2_split_run_name,
)
import app.main as main_mod


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(cond, label):
    if not cond:
        raise AssertionError(f"{label}: expected truthy")
    print(f"  OK  {label}")


def make_finished_run(step2: Path, ts: str, groups: int = 3) -> Path:
    run = step2 / ts
    for i in range(groups):
        g = run / f"HPAI_D1-1_Group-{i}"
        g.mkdir(parents=True, exist_ok=True)
        (g / "alignment.fasta").write_text(">root\nACGT\n>s1\nACGT\n")
        (g / "tree.tre").write_text("(s1,root);")
    run.mkdir(parents=True, exist_ok=True)
    return run


def make_staged_run(step2: Path, ts: str, vcfs: int = 25) -> Path:
    """A run that staged its VCFs and never started — the failure case."""
    run = step2 / ts
    run.mkdir(parents=True, exist_ok=True)
    for i in range(vcfs):
        (run / f"sample-{i:04d}_zc.vcf").write_text("##fileformat=VCFv4.2\n")
    (run / main_mod._STEP2_GUI_CMD_FILE).write_text("vsnp3_step2.py -wd . -t ref\n")
    return run


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="step2_run_state_"))
    try:
        print("\n[labelled comparison names are recognised and split]")
        # Every one of these is a real folder name from the Ames influenza
        # projects, and every one was invisible to the GUI: the old rule required
        # the name to be EXACTLY a stamp, so 203 folders across three projects —
        # precisely the ones someone had cared enough to name — never appeared in
        # the Comparison list at all.
        for name, stamp, label in [
            ("2026-06-05_13-11-21", "2026-06-05_13-11-21", ""),
            ("2026-04-01_08-21-32_owl_subset", "2026-04-01_08-21-32", "owl_subset"),
            ("2026-02-24_13-52-45_all_vcf", "2026-02-24_13-52-45", "all_vcf"),
            ("2026-05-28_14-55-36_HPAI_D1-1_AZ-dairy", "2026-05-28_14-55-36", "HPAI_D1-1_AZ-dairy"),
            ("2025-05-08_13-21-49-milk-product", "2025-05-08_13-21-49", "milk-product"),
            ("2026-04-24_D1-1_Group-2d2b", "2026-04-24", "D1-1_Group-2d2b"),
            ("2025-04-25", "2025-04-25", ""),
            ("2025-08-19_16-02-32]", "2025-08-19_16-02-32", "]"),
        ]:
            got = _step2_split_run_name(name)
            assert_true(got is not None, f"recognised: {name}")
            assert_eq((got["stamp"], got["label"]), (stamp, label), f"split: {name}")

        # A leading date is still required, so step2/ housekeeping is not swept in.
        for name in ("vcf_database", "vcf_starting_files", "test", "runs",
                     "step2_is_running__individual_folders_may_be_complete"):
            assert_eq(_step2_split_run_name(name), None, f"not a comparison: {name}")

        print("\n[chronological order, which raw name order gets wrong]")
        # Lexicographically, 2026-04-24_D1-1_Group-2d2b (no time at all) sorts
        # AHEAD of 2026-04-24_23-59-59, while a bare 2025-04-25 sorts BEHIND
        # every timed folder of its own day — two opposite conventions in one
        # list. "Newest with results" picks the default comparison, so this has
        # to come from the parsed stamp, not the string.
        same_day = [
            "2026-04-24_09-33-17",
            "2026-04-24_09-33-17_D1-1_Group-2d2b",
            "2026-04-24_D1-1_Group-2d2b",
            "2026-04-24_23-59-59",
        ]
        assert_eq(_step2_runs_newest_first(same_day)[0], "2026-04-24_23-59-59",
                  "23:59 is the newest of its day, not the untimed folder")
        assert_eq(_step2_runs_newest_first(same_day)[-1], "2026-04-24_D1-1_Group-2d2b",
                  "an untimed folder sorts at the START of its day (00-00-00)")
        assert_eq(_step2_runs_newest_first(["2025-04-25", "2025-04-25_09-00-00"]),
                  ["2025-04-25_09-00-00", "2025-04-25"],
                  "bare date is older than the same day's timed run")
        assert_eq(_step2_runs_newest_first(
                      ["2025-12-31_23-00-00", "2026-01-01_00-00-01", "2025-06-01_12-00-00"]),
                  ["2026-01-01_00-00-01", "2025-12-31_23-00-00", "2025-06-01_12-00-00"],
                  "ordinary chronology still holds across years")
        # A legacy step2/runs/<name> that parses as nothing must not break the sort.
        assert_eq(_step2_runs_newest_first(["2026-01-01_00-00-00", "legacy-run"]),
                  ["2026-01-01_00-00-00", "legacy-run"],
                  "unparseable names sort last instead of raising")

        print("\n[the two patterns keep their separate jobs]")
        # The permissive rule identifies comparisons. The strict one is used where
        # the question is "is this a nested run folder rather than a results
        # group" — using the permissive rule there would HIDE a group legitimately
        # named with a leading date, which is the same bug in reverse.
        assert_true(main_mod._STEP2_COMPARISON_RE.match("2026-04-24_D1-1_Group-2d2b"),
                    "permissive rule accepts a labelled comparison")
        assert_eq(bool(main_mod._STEP2_STAMP_ONLY_RE.match("2026-04-24_D1-1_Group-2d2b")),
                  False, "strict rule rejects it, so such a GROUP stays visible")
        assert_true(main_mod._STEP2_STAMP_ONLY_RE.match("2026-04-24_09-33-17"),
                    "strict rule still catches a real nested run folder")

        print("\n[labelled folders reach the Comparison list and the resolver]")
        lab = tmp / "labelled" / "step2"
        lab.mkdir(parents=True)
        make_finished_run(lab, "2026-04-01_08-21-32_owl_subset", groups=2)
        make_finished_run(lab, "2026-03-01_08-00-00", groups=2)
        make_staged_run(lab, "2026-04-24_D1-1_Group-2d2b", vcfs=5)
        (lab / "vcf_database").mkdir()
        found = _step2_run_dirs(lab)
        assert_eq(sorted(found.keys()),
                  ["2026-03-01_08-00-00", "2026-04-01_08-21-32_owl_subset",
                   "2026-04-24_D1-1_Group-2d2b"],
                  "all three found, vcf_database excluded")
        # The untimed labelled folder has no results AND sorts to 2026-04-24
        # 00:00:00, so the newest WITH results is the labelled owl_subset run.
        assert_eq(_resolve_step2_output_dir(lab, None).name,
                  "2026-04-01_08-21-32_owl_subset",
                  "a labelled comparison can be the default the pane opens")

        print("\n[_dir_holds_a_file agrees with what the pane renders]")
        d = tmp / "probe"
        (d / "empty_group").mkdir(parents=True)
        assert_eq(_dir_holds_a_file(d / "empty_group"), False,
                  "a group with nothing in it is not content")
        (d / "flat_group").mkdir()
        (d / "flat_group" / "x.tre").write_text("()")
        assert_eq(_dir_holds_a_file(d / "flat_group"), True, "a file at the top counts")
        # step2_outputs folds posthoc/ files into the group's file list, so a
        # group whose only content is there still renders and must count here.
        (d / "posthoc_only" / "posthoc").mkdir(parents=True)
        (d / "posthoc_only" / "posthoc" / "snp_matrix.csv").write_text("a,b\n")
        assert_eq(_dir_holds_a_file(d / "posthoc_only"), True,
                  "posthoc-only group counts, matching the pane")
        assert_eq(_dir_holds_a_file(d / "does_not_exist"), False, "missing dir is not content")

        print("\n[_step2_run_shape]")
        step2 = tmp / "proj" / "step2"
        step2.mkdir(parents=True)
        finished = make_finished_run(step2, "2026-08-19_14-08-32", groups=4)
        staged = make_staged_run(step2, "2026-08-25_06-20-43", vcfs=30)

        s_fin = _step2_run_shape(finished)
        assert_eq(s_fin["groups"], 4, "finished run: group folders counted")
        assert_eq(s_fin["has_results"], True, "finished run: has results")
        assert_eq(s_fin["staged_vcfs"], 0, "finished run: no loose VCFs left")

        s_stg = _step2_run_shape(staged)
        assert_eq(s_stg["groups"], 0, "staged run: no group folders")
        assert_eq(s_stg["staged_vcfs"], 30, "staged run: loose VCFs counted")
        assert_eq(s_stg["has_results"], False, "staged run: nothing to render")
        assert_eq(s_stg["has_gui_cmd"], True, "staged run: recognised as GUI-launched")

        # A run whose only output is the top-level summary still has results.
        toponly = step2 / "2026-08-20_09-00-00"
        toponly.mkdir()
        (toponly / "summary.html").write_text("<html></html>")
        assert_eq(_step2_run_shape(toponly)["has_results"], True,
                  "top-level html alone counts as results")

        # Group folders that exist but are empty are what vsnp3 leaves for
        # TOO_FEW_SAMPLES; the pane skips them, so they are not results.
        degenerate = step2 / "2026-08-21_09-00-00"
        (degenerate / "Group-1").mkdir(parents=True)
        (degenerate / "Group-2").mkdir(parents=True)
        s_deg = _step2_run_shape(degenerate)
        assert_eq(s_deg["groups"], 2, "degenerate run: folders counted")
        assert_eq(s_deg["has_results"], False,
                  "degenerate run: empty groups are not results")

        print("\n[the shape scan does not type-probe a staging folder]")
        # The cost that produced an 89-second run listing was one filesystem
        # type probe per entry: Path.is_dir() stats, and on a filesystem whose
        # readdir carries no d_type even scandir's is_dir() falls back to lstat.
        # So what must be pinned is that is_dir() is not REACHED for entries a
        # name already settles. Counting os.stat would not show this — scandir's
        # probe happens below the os.stat name.
        big = make_staged_run(step2, "2026-08-24_00-00-00", vcfs=400)
        probes = {"n": 0}
        real_scandir = os.scandir

        class CountingEntry:
            def __init__(self, e):
                self._e = e
                self.name = e.name
                self.path = e.path

            def is_dir(self, *a, **k):
                probes["n"] += 1
                return self._e.is_dir(*a, **k)

            def is_file(self, *a, **k):
                probes["n"] += 1
                return self._e.is_file(*a, **k)

        class CountingScandir:
            def __init__(self, path):
                self._it = real_scandir(path)

            def __enter__(self):
                return (CountingEntry(e) for e in self._it)

            def __exit__(self, *exc):
                return self._it.__exit__(*exc)

        os.scandir = CountingScandir
        try:
            shape_big = _step2_run_shape(big)
        finally:
            os.scandir = real_scandir
        assert_eq(shape_big["staged_vcfs"], 400, "all 400 staged VCFs seen")
        assert_eq(probes["n"], 0,
                  "400 staged VCFs settled by name, with zero type probes")
        shutil.rmtree(big)

        print("\n[_step2_run_state]")
        assert_eq(_step2_run_state(s_fin, False), "results", "has output -> results")
        assert_eq(_step2_run_state(s_stg, True), "running", "live job -> running")
        assert_eq(_step2_run_state(s_stg, False), "staged", "staged, no job -> staged")
        marked = dict(s_stg, running_marker=True)
        assert_eq(_step2_run_state(marked, False), "interrupted",
                  "vsnp3 marker with no live job -> interrupted, not running")
        assert_eq(_step2_run_state(marked, True), "running",
                  "liveness outranks the marker")
        assert_eq(_step2_run_state(dict(s_stg, staged_vcfs=0), False), "empty",
                  "nothing at all -> empty")
        assert_eq(_step2_run_state({"readable": False}, False), "unreadable",
                  "unreadable folder is named, not crashed")

        print("\n[_resolve_step2_output_dir skips output-less runs]")
        # Newest by name is the staged folder; the resolver must step over it.
        newest = sorted(_step2_run_dirs_names(step2), reverse=True)[0]
        assert_eq(newest, "2026-08-25_06-20-43", "staged run really is newest by name")
        # Newest-first the folders are: 2026-08-25 (staged, no output),
        # 2026-08-21 (group folders that are all empty -> the pane renders
        # nothing), 2026-08-20 (top-level html). Both output-less folders must
        # be stepped over, which is why the answer is the third one down.
        chosen = _resolve_step2_output_dir(step2, None)
        assert_eq(chosen.name, "2026-08-20_09-00-00",
                  "skips the staged run AND the empty-group run")

        # An explicit choice is honoured even when it shows nothing.
        assert_eq(_resolve_step2_output_dir(step2, "2026-08-25_06-20-43").name,
                  "2026-08-25_06-20-43", "explicit run_id always wins")

        # .current_run pointing at an output-less run is ignored unless live.
        (step2 / ".current_run").write_text("2026-08-25_06-20-43")
        assert_eq(_resolve_step2_output_dir(step2, None).name, "2026-08-20_09-00-00",
                  ".current_run on a stalled run is stepped over")

        # ...but a LIVE run wins, so a comparison you just launched is watchable.
        real_live = main_mod._step2_live_run_id
        main_mod._step2_live_run_id = lambda _s: "2026-08-25_06-20-43"
        try:
            assert_eq(_resolve_step2_output_dir(step2, None).name, "2026-08-25_06-20-43",
                      "a live run is shown even with no output yet")
        finally:
            main_mod._step2_live_run_id = real_live

        # Nothing anywhere has output -> newest, so the pane can explain it.
        bare = tmp / "bare" / "step2"
        (bare / "2026-01-01_00-00-00").mkdir(parents=True)
        (bare / "2026-02-02_00-00-00").mkdir(parents=True)
        assert_eq(_resolve_step2_output_dir(bare, None).name, "2026-02-02_00-00-00",
                  "no run has output -> newest, not the flat legacy layout")

        print("\n[_step2_read_run_metadata survives every damaged file]")
        victim = step2 / "2026-08-19_14-08-32"
        meta = victim / "run_metadata.json"

        meta.write_text(json.dumps({
            "started_at": "2026-08-19T14:08:32", "status": "ok",
            "dispatch_state": {"reference": {"name": "owl_25-003495-001"}},
        }))
        got = _step2_read_run_metadata(victim)
        assert_eq(got["status"], "ok", "well-formed: status")
        assert_eq(got["reference"], "owl_25-003495-001", "well-formed: reference")

        meta.write_text(json.dumps({"status": "ok", "dispatch_state": None}))
        assert_eq(_step2_read_run_metadata(victim)["reference"], "",
                  "dispatch_state present but null")

        meta.write_text(json.dumps({"dispatch_state": {"reference": "owl_25-003495-001"}}))
        assert_eq(_step2_read_run_metadata(victim)["reference"], "owl_25-003495-001",
                  "older schema stored reference as a bare string")

        meta.write_text("null")
        assert_eq(_step2_read_run_metadata(victim)["status"], "unknown",
                  "file contains bare null")

        meta.write_text("[1, 2, 3]")
        assert_eq(_step2_read_run_metadata(victim)["status"], "unknown",
                  "file contains a list")

        meta.write_bytes(b'{"status": "ok", "note": "caf\xe9"}')
        assert_eq(_step2_read_run_metadata(victim)["status"], "unknown",
                  "non-UTF8 byte (UnicodeDecodeError is not an OSError)")

        meta.write_text("{not json")
        assert_eq(_step2_read_run_metadata(victim)["status"], "unknown",
                  "unparseable JSON")

        meta.unlink()
        assert_eq(_step2_read_run_metadata(victim)["status"], "unknown",
                  "absent file is 'unknown', never 'running'")

        if os.geteuid() != 0:
            os.chmod(victim, 0o000)
            try:
                assert_eq(_step2_read_run_metadata(victim)["status"], "unknown",
                          "unreadable folder")
                assert_eq(_step2_run_shape(victim)["readable"], False,
                          "unreadable folder reported, not raised")
            finally:
                os.chmod(victim, 0o755)

        print("\n[step2_outputs explains an empty comparison]")
        # The contract the results pane reads. Without empty_reason it can only
        # fall back to "No Step 2 outputs found yet.", which is what it used to
        # print over a folder holding 8,017 staged VCFs and no run.
        proj_root = tmp / "roots"
        proj = proj_root / "owl_test"
        (proj / "step1").mkdir(parents=True)
        (proj / "project.json").write_text(json.dumps({"reference": "owl_ref"}))
        p2 = proj / "step2"
        p2.mkdir()
        make_finished_run(p2, "2026-07-01_09-00-00", groups=2)
        make_staged_run(p2, "2026-08-25_06-20-43", vcfs=17)

        real_load_config = main_mod.load_config
        main_mod.load_config = lambda: {"projects_root": str(proj_root)}
        try:
            default = main_mod.step2_outputs("owl_test", None)
            assert_eq(default["run_id"], "2026-07-01_09-00-00",
                      "default lands on the run with results, not the staged one")
            assert_eq(default["empty_reason"], None, "a run with results has no reason block")
            assert_eq(len(default["groups"]), 2, "and its groups are rendered")

            explicit = main_mod.step2_outputs("owl_test", "2026-08-25_06-20-43")
            assert_eq(explicit["run_id"], "2026-08-25_06-20-43", "explicit choice honoured")
            assert_eq(explicit["groups"], [], "with nothing to render")
            reason = explicit["empty_reason"]
            assert_true(reason is not None, "and a reason block explaining why")
            assert_eq(reason["state"], "staged", "reason: staged, never ran")
            assert_eq(reason["staged_vcfs"], 17, "reason: how many VCFs are waiting")
            assert_eq(reason["can_resume"], True, "reason: it can be run from here")
            assert_eq(reason["newest_with_results"], "2026-07-01_09-00-00",
                      "reason: where the user's results actually are")
        finally:
            main_mod.load_config = real_load_config

        print("\nAll Step 2 run-state tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _step2_run_dirs_names(step2: Path):
    return [d.name for d in step2.iterdir()
            if d.is_dir() and main_mod._STEP2_STAMP_ONLY_RE.match(d.name)]


if __name__ == "__main__":
    sys.exit(main())
