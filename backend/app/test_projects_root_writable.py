"""A Projects root this account cannot write never stops the app from starting,
and Settings never saves one.

On USDA SciComp (2026-11-12) a user set the Projects root to a group folder
they could only read. Creating a project failed, and from then on every start
died at import with PermissionError making <root>/.jobs; the dashboard said
"vsnp_gui stopped while starting" and the only way out was editing
~/.config/vsnp_gui/config.json by hand.

Pinned here:
  - with config.json naming an unwritable root, the backend imports, job logs
    go to the per-user folder beside config.json (and a job really logs
    there), and GET /api/config names the folder and why it cannot be used;
  - a Save that changes the Projects root to a folder this account cannot
    write, or cannot create, or that is a file or not a full path, is refused
    with HTTP 400 naming the folder, and config.json is not touched;
  - a Save that leaves the saved (unwritable) root as it is still saves the
    other settings;
  - a writable root is saved, created when missing, and new job logs follow it
    at once; a root made writable outside the app is used on the next read;
  - the write check leaves nothing behind; the folder picker flags a folder
    this account cannot write; creating a project there is a 403 that says
    why, not a 500.

Run directly:  PYTHONPATH=backend <conda>/bin/python backend/app/test_projects_root_writable.py
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


def wait_for(job_manager, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = job_manager.get_job(job_id) or {}
        if job.get("status") not in ("queued", "running"):
            return job
        time.sleep(0.05)
    return job_manager.get_job(job_id) or {}


def main() -> int:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        print("SKIP: running as root — permission bits do not refuse root")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="projects_root_writable_")).resolve()
    try:
        return run(tmp)
    finally:
        for d, _dirs, _files in os.walk(tmp):
            try:
                os.chmod(d, stat.S_IRWXU)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def run(tmp: Path) -> int:
    # The launch context, set before the backend is imported: config.py reads
    # it once, at import, exactly as a real start does.
    for k in ("BDTOOLS_SITE_ROOT", "BDTOOLS_TOOLS_ROOT", "BDTOOLS_DB_ROOT"):
        os.environ.pop(k, None)
    os.environ["VSNP_GUI_SITE_ROOT"] = str(tmp / "site")
    os.environ["VSNP_GUI_SHARED_PROJECTS_ROOT"] = ""
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")
    (tmp / "site").mkdir()

    group = tmp / "group"                 # a folder this account can only read
    readonly_root = group / "projects"
    readonly_root.mkdir(parents=True)
    readonly_other = tmp / "other_ro"
    readonly_other.mkdir()
    writable = tmp / "mine"
    writable.mkdir()
    for d in (readonly_root, group, readonly_other):
        os.chmod(d, 0o555)

    cfg_path = tmp / "xdg" / "vsnp_gui" / "config.json"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(json.dumps({"projects_root": str(readonly_root)}), encoding="utf-8")

    print("[start with an unwritable Projects root]")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    try:
        from app import main as M
    except Exception as exc:  # the bug: PermissionError on <root>/.jobs
        check(f"the backend starts (import raised {type(exc).__name__}: {exc})", False)
        return 1
    from fastapi import HTTPException
    check("the backend starts", True)
    fallback = cfg_path.parent / "jobs"
    check("job logs go to the per-user folder beside config.json",
          M.FALLBACK_JOBS_DIR == fallback and M.job_manager.jobs_dir == fallback)
    check("nothing was made in the unwritable root", list(readonly_root.iterdir()) == [])

    got = M.get_config()
    problem = got.get("projects_root_problem", "")
    check("GET /api/config names the folder and says it is not writable",
          str(readonly_root) in problem and "not writable" in problem)
    check("...and where job logs are going", got.get("jobs_dir") == str(fallback))
    check("...and the Projects root is not marked valid", got["_validation"]["projects_root"] is False)

    job = wait_for(M.job_manager, M.job_manager.start_job("probe", "echo logged"))
    log = Path(job.get("log_path", ""))
    check("a job runs and writes its log in the per-user folder",
          job.get("exit_code") == 0 and log.parent == fallback
          and "logged" in log.read_text(encoding="utf-8"))

    print("[a Save that leaves the saved root alone still saves the rest]")
    out = M.update_config(M.ConfigUpdate(projects_root=str(readonly_root), step1_max_parallel=5))
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    check("the other setting is saved", saved.get("step1_max_parallel") == 5)
    check("...and the response still reports the root's problem",
          str(readonly_root) in out.get("projects_root_problem", ""))

    print("[Settings refuses a root this account cannot write]")
    before = cfg_path.read_bytes()
    cases = [
        ("an existing folder it cannot write", str(readonly_other), str(readonly_other), "not writable"),
        ("a missing folder under one it cannot write", str(readonly_root / "new"), str(readonly_root / "new"),
         "cannot create it"),
        ("a file", str(tmp / "a_file"), str(tmp / "a_file"), "not a folder"),
        ("a relative path", "projects", "projects", "full path"),
    ]
    (tmp / "a_file").write_text("x", encoding="utf-8")
    for label, value, named, why in cases:
        try:
            M.update_config(M.ConfigUpdate(projects_root=value, step1_max_parallel=7))
            check(f"{label}: refused", False)
        except HTTPException as exc:
            detail = str(exc.detail)
            check(f"{label}: refused with 400 naming the folder and the reason",
                  exc.status_code == 400 and named in detail and why in detail)
    check("config.json was not touched by any refused save", cfg_path.read_bytes() == before)
    check("job logs still go to the per-user folder", M.job_manager.jobs_dir == fallback)

    print("[Settings accepts a root this account can write]")
    listing = sorted(os.listdir(writable))
    check("the write check leaves nothing behind",
          M.projects_root_problem(str(writable)) == "" and sorted(os.listdir(writable)) == listing)
    new_root = writable / "projects"
    out = M.update_config(M.ConfigUpdate(projects_root=str(new_root)))
    saved = json.loads(cfg_path.read_text(encoding="utf-8"))
    check("it is saved", saved.get("projects_root") == str(new_root))
    check("it is created", new_root.is_dir())
    check("the response reports no problem", out.get("projects_root_problem") == "")
    check("new job logs follow it at once",
          M.job_manager.jobs_dir == new_root / ".jobs" and out.get("jobs_dir") == str(new_root / ".jobs"))
    job = wait_for(M.job_manager, M.job_manager.start_job("probe2", "echo here"))
    check("a job started now logs under the new root",
          Path(job.get("log_path", "")).parent == new_root / ".jobs" and job.get("exit_code") == 0)
    check("the earlier job keeps its log where it was", log.is_file() and log.parent == fallback)
    got = M.get_config()
    check("GET /api/config reports no problem and marks the root valid",
          got["projects_root_problem"] == "" and got["_validation"]["projects_root"] is True)

    print("[a root made writable outside the app is used without a restart]")
    cfg_path.write_text(json.dumps({**saved, "projects_root": str(readonly_other)}), encoding="utf-8")
    check("while it is read-only, logs go to the per-user folder",
          M.get_config()["projects_root_problem"] != "" and M.job_manager.jobs_dir == fallback)
    os.chmod(readonly_other, 0o755)
    check("after a chmod, the next read finds no problem and logs follow the root",
          M.get_config()["projects_root_problem"] == ""
          and M.job_manager.jobs_dir == readonly_other / ".jobs")
    os.chmod(readonly_other, 0o555)

    print("[the folder picker and project creation]")
    check("the picker flags a folder this account cannot write",
          M.browse_dirs(path=str(readonly_root))["writable"] is False)
    check("...and not one it can", M.browse_dirs(path=str(writable))["writable"] is True)
    cfg_path.write_text(json.dumps({**saved, "projects_root": str(readonly_root)}), encoding="utf-8")
    try:
        M.project_create(M.ProjectCreate(name="P1"))
        check("creating a project in an unwritable root is refused", False)
    except HTTPException as exc:
        check("creating a project in an unwritable root is a 403 that says why",
              exc.status_code == 403 and "Permission denied" in str(exc.detail))

    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll Projects root write tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
