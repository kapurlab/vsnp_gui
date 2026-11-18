"""A Run never fails because the shared env-snapshot store is out of reach.

SciComp, 2026-11-18: an ordinary user's Step 1 Run returned a bare HTTP 500.
At dispatch the run record captures the software environment and writes it to
the site's shared store, <audit_root>/env_snapshots/, which is admin-only
there. tempfile.mkstemp raised PermissionError, and _step1_dispatch caught only
DispatchFailed, so the error escaped the endpoint before the batch started.
It worked for the admin, who can write the store.

These tests pin, as an account that cannot write or read the store:
  - the Step 1 and Step 2 writers still record the run, and each run's
    _provenance/ still holds conda_env.yaml and pip_freeze.txt, written from
    the capture, with the sha256 the record states;
  - a snapshot already in the store that this account cannot read is the
    same case;
  - a writable store keeps working as before (shared file + per-run copy);
  - _step1_dispatch starts the batch with a provenance_warning when the record
    raises a raw OSError, and step2_run handles any exception the same way.

Run from anywhere with the checkout's python:

    env/bin/python backend/app/test_provenance_unwritable_store.py
"""
from __future__ import annotations

import ast
import hashlib
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import provenance_writer as pw  # noqa: E402
from test_provenance_writer import (  # noqa: E402
    make_fake_reference_folder,
    make_fake_vsnp3,
    make_fake_vsnp_gui,
    make_sample_dir,
)

FAILURES: list[str] = []
CONDA_YAML = "name: vsnp3\ndependencies:\n  - vsnp3=3.37\n"
PIP_FREEZE = "pandas==2.2.3\n"


def check(cond, label):
    if cond:
        print(f"  OK    {label}")
    else:
        FAILURES.append(label)
        print(f"  FAIL  {label}")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def fixed_capture():
    """Deterministic env capture: no conda/pip subprocesses, known content."""
    pw._capture_conda_env_yaml = lambda: CONDA_YAML
    pw._capture_pip_freeze = lambda: PIP_FREEZE
    pw._capture_system_packages = lambda: {}
    pw._capture_install_bin_versions = lambda _p: {}


def site(tmp: Path, name: str):
    """A fresh fake site + project; returns (cfg, project_dir, samples)."""
    pw._env_snapshot_cache.clear()
    root = tmp / name
    audit_root = root / "audit"
    audit_root.mkdir(parents=True)
    refs_root = root / "refs"
    refs_root.mkdir()
    make_fake_reference_folder(refs_root, "mtbc0_v1.1")
    vsnp3_root = make_fake_vsnp3(root / "tools" / "vsnp3")
    vsnp_gui_root = make_fake_vsnp_gui(root / "tools" / "vsnp_gui")
    projects = root / "projects"
    project_dir = projects / "TB_intro"
    (project_dir / "step1").mkdir(parents=True)
    (project_dir / "step2").mkdir()
    samples = ["S001", "S002"]
    for s in samples:
        make_sample_dir(project_dir / "step1", s)
    cfg = {
        "vsnp3_path": str(vsnp3_root),
        "vsnp_gui_deploy_path": str(vsnp_gui_root),
        "vsnp3_reference_options_root": str(refs_root),
        "audit_root": str(audit_root),
        "projects_root": str(projects),
        "shared_projects_root": str(projects),
        "provenance": {"hash_max_bytes": 256 * 1024 * 1024},
    }
    return cfg, project_dir, samples


def check_run_copies(prov_dir: Path, label: str):
    yaml = prov_dir / "conda_env.yaml"
    pip = prov_dir / "pip_freeze.txt"
    check(yaml.is_file() and sha(yaml.read_text()) == sha(CONDA_YAML),
          f"{label}: conda_env.yaml in the run, sha256 matches")
    check(pip.is_file() and pip.read_text() == PIP_FREEZE,
          f"{label}: pip_freeze.txt in the run")


def check_record(meta: Path, label: str):
    import json
    env = json.loads(meta.read_text())["environment"]
    check(env.get("conda_env_yaml_sha256") == sha(CONDA_YAML),
          f"{label}: record states the snapshot's sha256")
    check(env.get("conda_env_yaml_path") == str(meta.parent / "_provenance" / "conda_env.yaml"),
          f"{label}: record points at the run's own copy")
    check(not any(k.startswith("_") for k in env),
          f"{label}: no internal keys serialized")


def test_store_unwritable(tmp: Path):
    print("\n[shared store exists but this account cannot write it]")
    cfg, project_dir, samples = site(tmp, "unwritable")
    store = Path(cfg["audit_root"]) / "env_snapshots"
    store.mkdir()
    store.chmod(0o555)
    try:
        _batch, _ids = pw.dispatch_step1_batch(
            cfg, project_dir, samples, "mtbc0_v1.1", user="reed", ood_session_id=None)
        for s in samples:
            d = project_dir / "step1" / s
            check_run_copies(d / "_provenance", f"step1 {s}")
            check_record(d / "run_metadata.json", f"step1 {s}")
        pw.dispatch_step2(
            cfg, project_dir, "mtbc0_v1.1", "vsnp3_step2.py", [],
            user="reed", ood_session_id=None, is_shared=False,
            resolved_vcf_db_folders=[])
        check_run_copies(project_dir / "step2" / "_provenance", "step2")
        check(list(store.iterdir()) == [], "nothing left in the store")
    finally:
        store.chmod(0o755)


def test_store_file_unreadable(tmp: Path):
    print("\n[snapshot already in the store, unreadable to this account]")
    cfg, project_dir, samples = site(tmp, "unreadable")
    store = Path(cfg["audit_root"]) / "env_snapshots"
    store.mkdir()
    held = store / f"{sha(CONDA_YAML)}.yaml"
    held.write_text(CONDA_YAML)
    held.chmod(0)
    try:
        pw.dispatch_step1_batch(
            cfg, project_dir, samples, "mtbc0_v1.1", user="reed", ood_session_id=None)
        for s in samples:
            check_run_copies(project_dir / "step1" / s / "_provenance", f"step1 {s}")
        check(stat.S_IMODE(held.stat().st_mode) == 0, "the store's file is left as it was")
    finally:
        held.chmod(0o640)


def test_audit_root_unwritable(tmp: Path):
    print("\n[store cannot even be created]")
    cfg, project_dir, samples = site(tmp, "nocreate")
    audit_root = Path(cfg["audit_root"])
    audit_root.chmod(0o555)
    try:
        pw.dispatch_step1_batch(
            cfg, project_dir, samples, "mtbc0_v1.1", user="reed", ood_session_id=None)
        for s in samples:
            check_run_copies(project_dir / "step1" / s / "_provenance", f"step1 {s}")
    finally:
        audit_root.chmod(0o755)


def test_store_writable(tmp: Path):
    print("\n[writable store, as for an admin: unchanged behaviour]")
    cfg, project_dir, samples = site(tmp, "writable")
    pw.dispatch_step1_batch(
        cfg, project_dir, samples, "mtbc0_v1.1", user="admin", ood_session_id=None)
    store = Path(cfg["audit_root"]) / "env_snapshots"
    check((store / f"{sha(CONDA_YAML)}.yaml").is_file(), "snapshot written to the store")
    check((store / f"{sha(PIP_FREEZE)}.txt").is_file(), "pip freeze written to the store")
    for s in samples:
        check_run_copies(project_dir / "step1" / s / "_provenance", f"step1 {s}")


def test_step1_dispatch_survives_raw_oserror(tmp: Path):
    print("\n[_step1_dispatch: a raw OSError from the record is a warning]")
    from app import main as m

    cfg, project_dir, samples = site(tmp, "dispatch")
    cfg["step1_min_fastq_bytes"] = 1
    started = []

    def boom(*_a, **_k):
        raise PermissionError(13, "Permission denied",
                              "/site/audit/env_snapshots/.x.yaml.tmp")

    saved = (m.provenance_writer.dispatch_step1_batch, m._vsnp3_reference_arg,
             m.job_manager.start_job, m.build_env, m.wrap_cmd)
    m.provenance_writer.dispatch_step1_batch = boom
    m._vsnp3_reference_arg = lambda _cfg, ref: f"-t /refs/{ref}"
    m.job_manager.start_job = lambda **kw: started.append(kw) or "job-1"
    m.build_env = lambda _cfg: {}
    m.wrap_cmd = lambda _cfg, cmd: cmd
    try:
        payload = m.Step1Request(reference="mtbc0_v1.1")
        out = m._step1_dispatch("TB_intro", payload, cfg, project_dir,
                                project_dir / "step1")
    except Exception as e:  # noqa: BLE001
        out = None
        check(False, f"dispatch returned instead of raising ({type(e).__name__}: {e})")
    finally:
        (m.provenance_writer.dispatch_step1_batch, m._vsnp3_reference_arg,
         m.job_manager.start_job, m.build_env, m.wrap_cmd) = saved
    if out is not None:
        check(out.get("job_id") == "job-1", "the batch job was started")
        check(len(started) == 1 and started[0].get("finalize_callback") is None,
              "no provenance finalize without a record")
        check("Permission denied" in out.get("provenance_warning", ""),
              "the warning names the error")


def test_step2_run_catches_everything():
    print("\n[step2_run: the record's try ends in a catch-all]")
    src = (HERE / "main.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "step2_run")
    tries = [t for t in ast.walk(fn) if isinstance(t, ast.Try)
             and "dispatch_step2" in ast.unparse(t.body[0])]
    check(len(tries) == 1, "one try around dispatch_step2")
    if tries:
        names = [ast.unparse(h.type) if h.type else "" for h in tries[0].handlers]
        check(names[-1] == "Exception", f"last handler is Exception (have {names})")
        check(names[0].endswith("Step2DispatchBlocked"),
              "Step2DispatchBlocked is still caught first (409)")


def main() -> int:
    if os.geteuid() == 0:
        print("SKIP: root ignores the permission bits these tests depend on")
        return 0
    fixed_capture()
    tmp = Path(tempfile.mkdtemp(prefix="vsnp_prov_store_"))
    try:
        test_store_unwritable(tmp)
        test_store_file_unreadable(tmp)
        test_audit_root_unwritable(tmp)
        test_store_writable(tmp)
        test_step1_dispatch_survives_raw_oserror(tmp)
        test_step2_run_catches_everything()
    finally:
        for p in tmp.rglob("*"):
            try:
                p.chmod(0o755)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nAll provenance store tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
