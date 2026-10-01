"""The comparison folder the GUI names is the folder the run writes into.

Each Step 2 comparison is written into its own dated folder, step2/<stamp>.
The GUI used to say only "Outputs will be written to: <project>/step2", the
parent of hundreds of them, so a path passed on to someone else did not say
which comparison it meant. step2/run's answer, step2/active (a reload mid-run)
and every step2/runs entry now carry the folder's own path, and the GUI shows
and copies exactly that string. What is pinned here:

  * step2/run's run_dir is the folder the run made: named by the run id, and
    the job's working directory.
  * step2/active names the same folder while the job is live; a pre-2026-06
    step2/runs/<id> folder where that is what the id names; and nothing, never
    a guess, for an id that names no folder or is not a comparison name.
  * step2/runs gives each comparison its folder's path, and the flat legacy
    layout step2/ itself.

This drives the real endpoints against a real project tree, with only the job
launch stubbed.

  cd backend && ../env/bin/python -m app.test_step2_folder_path
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as m  # noqa: E402

FAILS = []


def check(got, want, what):
    if got == want:
        print(f"  OK  {what}")
    else:
        FAILS.append(what)
        print(f"  FAIL {what}\n    got:  {got!r}\n    want: {want!r}")


SAMPLES = [f"TX-17-{i:04d}" for i in range(1, 6)]


def build_project(root: Path) -> Path:
    project = root / "owl_25-003495-001"
    db = project / "step2" / "vcf_database"
    db.mkdir(parents=True)
    for s in SAMPLES:
        (db / f"{s}_zc.vcf").write_text("##fileformat=VCFv4.2\n")
    return project


def stub_config(root: Path):
    # Step 2 runs <vsnp3_path>/bin/vsnp3_step2.py, so the path must resolve;
    # nothing is executed.
    vsnp3 = root / "vsnp3"
    (vsnp3 / "bin").mkdir(parents=True, exist_ok=True)
    (vsnp3 / "bin" / "vsnp3_step2.py").write_text("#!/usr/bin/env python\n", encoding="utf-8")
    m.load_config = lambda: {"projects_root": str(root), "vsnp3_path": str(vsnp3)}


def dispatch(project: Path):
    """The real endpoint, the job launch stubbed. Returns (answer, job cwd)."""
    started = {}

    def fake_start_job(name, command, cwd=None, **kw):
        started["cwd"] = cwd
        return "job-stub"

    saved = {k: getattr(m, k) for k in ("reference_lock", "_step2_reference_audit", "build_env")}
    saved_start = m.job_manager.start_job
    saved_dispatch = m.provenance_writer.dispatch_step2
    m.reference_lock = lambda p: {"references": ["AF2122"]}
    m._step2_reference_audit = lambda cfg, pd_: {
        "project_reference": "", "recoverable": [], "removable": [], "orphans": [], "mixed": False}
    m.build_env = lambda cfg: {}
    m.job_manager.start_job = fake_start_job
    m.provenance_writer.dispatch_step2 = lambda *a, **k: (None, None)
    try:
        answer = m.step2_run(project.name, m.Step2Request(
            reference="AF2122", include=list(SAMPLES), build_exclude=[], step1_exclude=[]))
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
        m.job_manager.start_job = saved_start
        m.provenance_writer.dispatch_step2 = saved_dispatch
    return answer, started.get("cwd")


def active_with(project: Path, current_run: str):
    """step2/active while a job is live and .current_run holds `current_run`."""
    (project / "step2" / ".current_run").write_text(current_run, encoding="utf-8")
    saved_active, saved_get = m._step2_active_job, m.job_manager.get_job
    m._step2_active_job = lambda step2_dir: "job-stub"
    m.job_manager.get_job = lambda job_id: {"status": "running", "started_at": "2026-09-30T14:22:05"}
    try:
        return m.step2_active(project.name)
    finally:
        m._step2_active_job, m.job_manager.get_job = saved_active, saved_get


def test_the_run_names_the_folder_it_writes_into():
    print("\n[step2/run answers with the dated folder it made]")
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        project = build_project(root)
        stub_config(root)
        answer, cwd = dispatch(project)
        run_dir = answer.get("run_dir", "")
        want = str(root / project.name / "step2" / answer["run_id"])
        check(run_dir, want, "run_dir is <projects_root>/<project>/step2/<run_id>")
        # bool() first: Path("") is ".", which is a folder.
        check(bool(run_dir) and Path(run_dir).is_dir(), True, "and that folder exists")
        check(str(cwd), run_dir, "and it is the job's working directory")
        check(Path(run_dir).is_absolute(), True, "an absolute path, to send as it is")

        print("\n[step2/active names the same folder after a reload]")
        live = active_with(project, answer["run_id"])
        check(live["run_dir"], run_dir, "the running comparison's folder")
        check(live["run_id"], answer["run_id"], "beside its id, unchanged")

        print("\n[step2/runs gives each comparison its folder]")
        rows = {r["run_id"]: r for r in m.step2_runs_list(project.name)}
        check(rows[answer["run_id"]]["path"], run_dir, "the new comparison's path")


def test_active_names_a_folder_or_nothing():
    print("\n[step2/active: a pre-2026-06 folder, and ids that name no folder]")
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        project = build_project(root)
        stub_config(root)
        step2 = project / "step2"
        old = step2 / "runs" / "2026-05-01_09-00-00"
        old.mkdir(parents=True)
        check(active_with(project, old.name)["run_dir"], str(old),
              "a resumed step2/runs/<id> folder is found where it is")
        odd = step2 / "runs" / "before-dates"
        odd.mkdir()
        check(active_with(project, odd.name)["run_dir"], str(odd),
              "so is one whose name is no date (the listing's fallback)")
        check(active_with(project, "2026-09-30_14-22-05")["run_dir"], "",
              "an id whose folder is gone names nothing")
        check(active_with(project, "vcf_database")["run_dir"], "",
              "step2/'s own folders are not comparisons")
        check(active_with(project, "2026-01-01_00-00-00/../../..")["run_dir"], "",
              "and an id cannot climb out of step2/")
        check(active_with(project, "")["run_dir"], "", "no .current_run, no folder")

        print("\n[no job, no answer beyond that]")
        check(m.step2_active(project.name), {"job_id": None}, "unchanged when nothing runs")


def test_the_list_gives_every_folder_its_path():
    print("\n[step2/runs: both layouts]")
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        project = build_project(root)
        stub_config(root)
        step2 = project / "step2"
        new = step2 / "2026-09-30_14-22-05_owl_subset"
        old = step2 / "runs" / "2026-05-01_09-00-00"
        new.mkdir()
        old.mkdir(parents=True)
        rows = {r["run_id"]: r["path"] for r in m.step2_runs_list(project.name)}
        check(rows, {new.name: str(new), old.name: str(old)},
              "a labelled comparison and a step2/runs/<id> one, each at its own path")

    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        project = build_project(root)
        stub_config(root)
        step2 = project / "step2"
        (step2 / "Group-1").mkdir()
        rows = m.step2_runs_list(project.name)
        check([(r["run_id"], r["path"]) for r in rows], [("legacy", str(step2))],
              "the flat layout's comparison is step2/ itself")


saved_config = m.load_config
try:
    for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                     key=lambda f: f.__code__.co_firstlineno):
        fn()
finally:
    m.load_config = saved_config

if FAILS:
    print(f"\nFAIL — {len(FAILS)} assertion(s)")
    sys.exit(1)
print("\nAll comparison-folder path tests passed.")
