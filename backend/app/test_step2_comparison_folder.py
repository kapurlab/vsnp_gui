"""What a finished comparison folder is allowed to contain.

A comparison folder gets handed around — attached to an email, copied to a
collaborator, kept beside a published tree. So it must describe ITS OWN
samples and no others. Three files broke that rule, each by scanning the whole
vcf_database rather than the run:

  * comparison_manifest.json listed the entire removal set, which on a
    ten-sample run is "every other isolate in the project".
  * remove_by_name.xlsx was that same set, written to disk as a workbook.
  * figtree_groups.tsv was one row per database entry, and nothing read it.

This drives the real dispatch endpoint against a real project tree, with the
job stubbed out at the last moment, and then reads the folder.

  cd backend && ../env/bin/python -m app.test_step2_comparison_folder
"""

import json
import re
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


# Ten samples the user ticked, and the rest of a database they did not.
PICKED = [f"TX-17-{i:04d}" for i in range(1, 11)]
REST = [f"NM-19-{i:04d}" for i in range(1, 41)]


def build_project(root: Path):
    project = root / "af2122"
    db = project / "step2" / "vcf_database"
    db.mkdir(parents=True)
    edits = project / "step2" / "vcf_edits"
    edits.mkdir()
    for s in PICKED + REST:
        (db / f"{s}_zc.vcf").write_text("##fileformat=VCFv4.2\n")
    # One hand-edited sample in the comparison, one outside it. An edited VCF is
    # a symlink into vcf_edits/ — that is how the scan recognises it.
    for s in (PICKED[0], REST[0]):
        real = edits / f"{s}_zc.vcf"
        real.write_text("##fileformat=VCFv4.2\n##edited\n")
        link = db / f"{s}_zc.vcf"
        link.unlink()
        link.symlink_to(real)
    return project


def dispatch(root: Path, project: Path, **overrides):
    """Run the real endpoint with only the job launch stubbed."""
    started = {}

    def fake_start_job(name, command, cwd=None, **kw):
        started["command"] = command
        return "job-stub"

    fields = dict(
        reference="AF2122",
        include=list(PICKED),
        # The frontend sends the whole rest of the database here: the tiers
        # still travel, they just no longer define the run.
        build_exclude=list(REST),
        step1_exclude=[],
    )
    fields.update(overrides)
    payload = m.Step2Request(**fields)
    saved = {k: getattr(m, k) for k in
             ("load_config", "reference_lock", "_step2_reference_audit", "build_env")}
    saved_start = m.job_manager.start_job
    saved_dispatch = m.provenance_writer.dispatch_step2
    m.load_config = lambda: {"projects_root": str(root)}
    m.reference_lock = lambda p: {"references": ["AF2122"]}
    m._step2_reference_audit = lambda cfg, pd_: {
        "project_reference": "", "recoverable": [], "removable": [], "orphans": [], "mixed": False}
    m.build_env = lambda cfg: {}
    m.job_manager.start_job = fake_start_job
    m.provenance_writer.dispatch_step2 = lambda *a, **k: (None, None)
    try:
        m.step2_run("af2122", payload)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
        m.job_manager.start_job = saved_start
        m.provenance_writer.dispatch_step2 = saved_dispatch
    runs = sorted(d for d in (project / "step2").iterdir()
                  if d.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}_", d.name))
    return runs[-1], started.get("command", "")


def test_the_folder_names_only_its_own_samples():
    print("\n[a ten-sample comparison, in a project of fifty]")
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        project = build_project(root)
        run_dir, command = dispatch(root, project)

        staged = sorted(p.name for p in run_dir.glob("*.vcf"))
        check(len(staged), 10, "ten VCFs staged")

        check((run_dir / "figtree_groups.tsv").exists(), False,
              "figtree_groups.tsv is not written — nothing read it, and it listed everything")
        check((run_dir / "remove_by_name.xlsx").exists(), False,
              "remove_by_name.xlsx is not written — no removal name can touch a staged file")
        check(" -remove_by_name" in command, False,
              "and vsnp3 is not passed one")

        edited = json.loads((run_dir / "edited_samples.json").read_text())
        check(edited["edited_samples"], [PICKED[0]],
              "edited_samples.json names this run's edited sample")
        check(REST[0] in edited["edited_samples"], False,
              "and not the one edited elsewhere in the database")

        manifest = json.loads((run_dir / "comparison_manifest.json").read_text())
        check(manifest["counts"], {"requested": 10, "staged": 10, "analyzable": 10},
              "the manifest counts this comparison")
        check(manifest["requested"], sorted(PICKED), "and names the ten samples in it")
        check(manifest["excluded_from_request"], [], "nothing the request asked for was dropped")
        check("removed_by_name" in manifest, False, "the database-wide removal list is gone")

        # The real test: read every byte of every file the folder holds and
        # look for a sample that is not part of this comparison.
        leaked = set()
        for path in run_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for name in REST:
                if name in text or name in path.name:
                    leaked.add(f"{name} in {path.name}")
        check(sorted(leaked), [], "no file in the folder names a sample outside the comparison")


def test_a_sample_a_removal_tier_dropped_is_still_reported():
    print("\n[a sample you asked for that a tier removed is named — you asked for it]")
    with tempfile.TemporaryDirectory() as t:
        root = Path(t)
        project = build_project(root)
        dropped = PICKED[0]
        run_dir, _cmd = dispatch(root, project, step1_exclude=[dropped])
        manifest = json.loads((run_dir / "comparison_manifest.json").read_text())
        check(manifest["excluded_from_request"], [dropped],
              "the manifest says which requested sample did not make the run")
        check(manifest["counts"]["requested"], 9, "and the run is the other nine")
        check(dropped in manifest["requested"], False, "the dropped sample is not among them")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"\nFAIL — {len(FAILS)} assertion(s)")
    sys.exit(1)
print("\nAll comparison-folder tests passed.")
