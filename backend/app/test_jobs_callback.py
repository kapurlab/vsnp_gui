"""
Smoke test for the JobManager finalize_callback change (T-07).

Verifies:
- Existing call sites without finalize_callback work unchanged.
- Callback fires with the right arguments after subprocess exits.
- Callback exception does NOT raise out of JobManager and does NOT mask
  the subprocess exit code in get_job().
- A failure entry lands in metadata_failures.jsonl when the callback raises.
- get_job() does not expose the internal _finalize_callback field.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Make `from app import jobs` work whether invoked from anywhere — backend/
# (the parent of app/) needs to be on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import jobs as jobs_mod  # noqa: E402  (import after sys.path mutation)


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(condition, label):
    if not condition:
        raise AssertionError(f"{label}: expected truthy")
    print(f"  OK  {label}")


def wait_for_job(jm, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = jm.get_job(job_id)
        if j and j["status"] in {"succeeded", "failed"}:
            return j
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="t07_jobs_smoke_"))
    print(f"workspace: {tmp}")

    # Redirect the metadata-failure log to a tempdir so we don't pollute the
    # real /srv/kapurlab/audit/ during the test.
    original_log_path = jobs_mod._METADATA_FAILURE_LOG
    jobs_mod._METADATA_FAILURE_LOG = tmp / "metadata_failures.jsonl"

    try:
        jm = jobs_mod.JobManager(tmp / "jobs")

        # ----- Test 1: no callback, behavior unchanged -----
        print("\n[no callback]")
        job_id = jm.start_job(name="t1", command="echo hello")
        j = wait_for_job(jm, job_id)
        assert_eq(j["status"], "succeeded", "status without callback")
        assert_eq(j["exit_code"], 0, "exit_code without callback")
        assert_true("_finalize_callback" not in j, "get_job hides _finalize_callback")

        # ----- Test 2: success path with callback -----
        print("\n[callback fires on success]")
        captured = {}

        def cb_ok(job_id, exit_code, started_at, finished_at):
            captured["job_id"] = job_id
            captured["exit_code"] = exit_code
            captured["started_at"] = started_at
            captured["finished_at"] = finished_at

        job_id = jm.start_job(name="t2", command="echo cb-ok", finalize_callback=cb_ok)
        j = wait_for_job(jm, job_id)
        # Give the callback a moment after status flips
        time.sleep(0.1)
        assert_eq(j["status"], "succeeded", "status with success callback")
        assert_eq(captured.get("job_id"), job_id, "callback got correct job_id")
        assert_eq(captured.get("exit_code"), 0, "callback got correct exit_code")
        assert_true(captured.get("started_at") is not None, "callback got started_at")
        assert_true(captured.get("finished_at") is not None, "callback got finished_at")

        # ----- Test 3: subprocess fails, callback still fires -----
        print("\n[callback fires on failure]")
        captured.clear()
        job_id = jm.start_job(name="t3", command="exit 7", finalize_callback=cb_ok)
        j = wait_for_job(jm, job_id)
        time.sleep(0.1)
        assert_eq(j["status"], "failed", "status with failure callback")
        assert_eq(j["exit_code"], 7, "exit_code preserved on failure")
        assert_eq(captured.get("exit_code"), 7, "callback got non-zero exit_code")

        # ----- Test 4: callback raises — soft fail, exit_code preserved -----
        print("\n[callback raises soft-fails]")

        def cb_explodes(job_id, exit_code, started_at, finished_at):
            raise RuntimeError("synthetic provenance writer failure")

        job_id = jm.start_job(name="t4", command="echo will-be-fine", finalize_callback=cb_explodes)
        j = wait_for_job(jm, job_id)
        time.sleep(0.1)
        # Subprocess succeeded; callback raised; subprocess result must be preserved.
        assert_eq(j["status"], "succeeded", "subprocess result preserved despite callback raise")
        assert_eq(j["exit_code"], 0, "exit_code preserved despite callback raise")

        # Verify a failure entry was written.
        assert_true(
            jobs_mod._METADATA_FAILURE_LOG.is_file(),
            "metadata_failures.jsonl created on callback raise",
        )
        lines = jobs_mod._METADATA_FAILURE_LOG.read_text().strip().splitlines()
        assert_eq(len(lines), 1, "one failure entry written")
        entry = json.loads(lines[0])
        assert_eq(entry["job_id"], job_id, "failure entry has correct job_id")
        assert_eq(entry["exception_type"], "RuntimeError", "failure entry has correct exception_type")
        assert_true(
            "synthetic provenance writer failure" in entry["exception_message"],
            "failure entry includes exception_message",
        )
        assert_true(
            "synthetic provenance writer failure" in entry["traceback"],
            "failure entry includes traceback",
        )

        # ----- Test 5: get_job hides _finalize_callback even when set -----
        print("\n[get_job sanitization]")
        job_id = jm.start_job(name="t5", command="echo hide", finalize_callback=cb_ok)
        wait_for_job(jm, job_id)
        time.sleep(0.1)
        public = jm.get_job(job_id)
        assert_true("_finalize_callback" not in public, "get_job sanitizes _finalize_callback when set")

        print("\nAll JobManager finalize_callback smoke assertions passed.")
        return 0

    finally:
        jobs_mod._METADATA_FAILURE_LOG = original_log_path
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
