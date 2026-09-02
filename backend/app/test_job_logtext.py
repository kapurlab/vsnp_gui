#!/usr/bin/env python3
"""
Offline test for /api/jobs/<id>/logtext — the POLLED replacement for the SSE
stream in job_events. Behind an Open OnDemand /rnode reverse proxy a held-open
SSE connection corrupts concurrent sibling GETs (a status poll comes back as log
text), so the UI polls one plain GET returning status + the log text instead.

What is pinned here:
  * a plain job returns its log unprefixed, status and exit_code passed through;
  * a step1 batch job merges the batch log ("[batch] ") with every per-sample
    run_step1.log ("[<sample>] "), samples ordered by LAST WRITE so the tail the
    pane shows is the sample actually running — and a sample dir with no log yet
    is skipped, not an error;
  * the text is tail-truncated under the proxy's ~43.5 KB ceiling with a marker,
    and the payload says so;
  * a missing log file is an empty log, not an exception; an unknown job is 404.
Run from anywhere: sys.path is derived from __file__.
"""
from __future__ import annotations
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import main as backend_main  # noqa: E402
from fastapi import HTTPException  # noqa: E402


class FakeJobManager:
    def __init__(self, jobs):
        self._jobs = jobs

    def get_job(self, job_id):
        return self._jobs.get(job_id)


def check(cond, msg):
    if not cond:
        print(f"FAIL: {msg}")
        sys.exit(1)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="logtext_test_"))
    try:
        # --- a plain (non-step1) job -------------------------------------
        plain_log = tmp / "step2.log"
        plain_log.write_text("line one\nline two\n", encoding="utf-8")
        plain = {"name": "step2", "cwd": str(tmp), "log_path": str(plain_log),
                 "status": "succeeded", "exit_code": 0}
        body = backend_main._job_logtext_payload(plain)
        check(body["log"] == "line one\nline two", f"plain log text: {body['log']!r}")
        check(body["status"] == "succeeded" and body["exit_code"] == 0, "status/exit_code passthrough")
        check(body["truncated"] is False, "plain job not truncated")

        # --- a step1 batch job: batch log + per-sample logs ---------------
        step1_dir = tmp / "step1"; step1_dir.mkdir()
        batch_log = tmp / "batch.log"
        batch_log.write_text("== Running step1 ==\nAll samples queued\n", encoding="utf-8")
        older = step1_dir / "Mg220"; older.mkdir()
        (older / "run_step1.log").write_text("Start: Mg220\ndone Mg220\n", encoding="utf-8")
        newer = step1_dir / "Mg111"; newer.mkdir()          # sorts FIRST by name...
        (newer / "run_step1.log").write_text("Start: Mg111\n[M::bwa] still going\n", encoding="utf-8")
        (step1_dir / "Mg333").mkdir()                        # no log yet: skipped
        (step1_dir / "stray.txt").write_text("not a sample dir\n", encoding="utf-8")
        now = time.time()
        os.utime(older / "run_step1.log", (now - 600, now - 600))   # ...but was written 10 min ago
        os.utime(newer / "run_step1.log", (now, now))               # this one is live
        step1 = {"name": "step1", "cwd": str(step1_dir), "log_path": str(batch_log),
                 "status": "running", "exit_code": None}
        body = backend_main._job_logtext_payload(step1)
        lines = body["log"].split("\n")
        check(lines[0] == "[batch] == Running step1 ==" and lines[1] == "[batch] All samples queued",
              f"batch prefix: {lines[:2]}")
        check(lines[2].startswith("[Mg220] ") and lines[4].startswith("[Mg111] "),
              f"samples ordered by last write, live one last: {lines[2:]}")
        check(lines[-1] == "[Mg111] [M::bwa] still going", f"tail is the live sample: {lines[-1]!r}")
        check(not any("Mg333" in l or "stray" in l for l in lines), "no-log dir and stray file skipped")
        check(body["status"] == "running" and body["truncated"] is False, "running, untruncated")

        # --- truncation --------------------------------------------------
        big = tmp / "big.log"
        big.write_text("".join(f"row {i:07d}\n" for i in range(6000)), encoding="utf-8")  # ~72 KB
        body = backend_main._job_logtext_payload({"name": "step2", "log_path": str(big), "status": "running"})
        check(body["truncated"] is True, "big log reports truncated")
        check(body["log"].startswith("...(earlier log truncated)...\n"), "truncation marker leads")
        check(body["log"].rstrip("\n").endswith("row 0005999"), "tail preserved after truncation")
        check(len(body["log"]) <= backend_main._LOGTEXT_MAX_CHARS + 40, f"under the ceiling: {len(body['log'])}")

        # --- missing log file, unknown job -------------------------------
        body = backend_main._job_logtext_payload({"name": "step2", "log_path": str(tmp / "nope.log"), "status": "queued"})
        check(body["log"] == "" and body["status"] == "queued", "missing log file -> empty log")
        backend_main.job_manager = FakeJobManager({"j1": plain})
        route_body = backend_main.job_logtext("j1")
        check(route_body["log"] == "line one\nline two", "route returns the payload")
        try:
            backend_main.job_logtext("nope")
            check(False, "unknown job must 404")
        except HTTPException as e:
            check(e.status_code == 404, f"unknown job -> 404, got {e.status_code}")

        print("OK: /logtext payload — plain, step1 merge (batch + samples by last write), truncation, 404")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
