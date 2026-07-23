import json
import logging
import os
import signal
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

from app.config import SITE_ROOT

logger = logging.getLogger(__name__)

# Where finalize-callback failures get logged. Per the locked T-07 policy:
# hard fail at dispatch (no metadata, no run); soft fail at finalize (run
# completes, metadata gap is logged here, janitor catches stuck records via
# the 48h timeout in vsnp_provenance.index.gc_running). This is a forensic
# trail, not a tamper-evident ledger — no chattr +a in V1.
_METADATA_FAILURE_LOG = SITE_ROOT / "audit" / "metadata_failures.jsonl"

# Callback signature: (job_id, exit_code, started_at, finished_at) -> None
FinalizeCallback = Callable[[str, int, datetime, datetime], None]
# Called with (job_id, pid) the instant the subprocess is spawned, so a caller
# can record the OS pid / process group for a restart-proof external liveness
# check (see step2's .step2_pgid lock in main.py).
StartCallback = Callable[[str, int], None]

# Exit code recorded for a job cancelled before its subprocess ever started
# (stopped while still queued). Mirrors a SIGTERM so finalizers treat it as a
# non-clean termination rather than a success.
_CANCELLED_EXIT_CODE = -int(signal.SIGTERM)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class JobManager:
    # After a stop request we SIGTERM the whole process group, then give the
    # tree this long to exit cleanly (let vsnp3 flush logs / remove temp files)
    # before escalating to SIGKILL.
    _STOP_GRACE_SECONDS = 10

    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict] = {}
        # Per-category concurrency gates. A job dispatched with a category +
        # max_concurrent runs at most `max_concurrent` at a time within that
        # category; the rest wait (status "queued") in their own worker threads
        # until a slot frees, then start automatically. Uncapped jobs (no
        # category) are unaffected and start immediately, exactly as before.
        self._sems: Dict[str, threading.Semaphore] = {}
        self._sem_limits: Dict[str, int] = {}

    def _get_semaphore(
        self, category: Optional[str], max_concurrent: Optional[int]
    ) -> Optional[threading.Semaphore]:
        if not category or not max_concurrent or max_concurrent < 1:
            return None
        with self._lock:
            sem = self._sems.get(category)
            if sem is None:
                sem = threading.Semaphore(max_concurrent)
                self._sems[category] = sem
                self._sem_limits[category] = max_concurrent
            # The limit is fixed on first use for the life of the process; a
            # later dispatch with a different max_concurrent keeps the original.
            return sem

    def start_job(
        self,
        name: str,
        command: str,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        finalize_callback: Optional[FinalizeCallback] = None,
        category: Optional[str] = None,
        max_concurrent: Optional[int] = None,
        on_start: Optional[StartCallback] = None,
    ) -> str:
        """Start a job. If finalize_callback is provided it will be invoked
        after the subprocess exits, inside a try/except — its failure
        does NOT mask the subprocess exit code in get_job() and does NOT
        raise into JobManager's loop. Failures are logged to
        /srv/kapurlab/audit/metadata_failures.jsonl per the T-07 soft-fail
        policy. The callback runs synchronously in the same thread that
        ran the subprocess; keep it cheap.

        category + max_concurrent gate how many jobs of that category run at
        once; excess dispatches wait in status "queued" and start when a slot
        frees. on_start(job_id, pid) fires the moment the subprocess spawns."""
        job_id = uuid.uuid4().hex
        log_path = self.jobs_dir / f"{job_id}.log"
        created_at = datetime.now(timezone.utc)
        semaphore = self._get_semaphore(category, max_concurrent)
        job = {
            "id": job_id,
            "name": name,
            "category": category,
            "command": command,
            "cwd": str(cwd) if cwd else None,
            # Capped jobs start "queued" and flip to "running" once they hold a
            # slot; uncapped jobs run immediately (status "running").
            "status": "queued" if semaphore is not None else "running",
            "exit_code": None,
            "log_path": str(log_path),
            "queued_at": created_at.isoformat(),
            # None until the subprocess actually starts (may sit queued first).
            "started_at": None if semaphore is not None else created_at.isoformat(),
            "finished_at": None,
            "duration_seconds": None,
            # Internal-only fields (leading underscore) — stripped from get_job.
            "_finalize_callback": finalize_callback,
            "_on_start": on_start,
        }
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(
            target=self._run,
            args=(job_id, command, cwd, env, log_path, semaphore),
            daemon=True,
        )
        thread.start()
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            # Strip internal-only fields from the returned snapshot.
            return {k: v for k, v in job.items() if not k.startswith("_")}

    def list_jobs(self) -> list:
        """Return public snapshots for dashboard lifecycle/readiness checks."""
        with self._lock:
            return [
                {k: v for k, v in job.items() if not k.startswith("_")}
                for job in self._jobs.values()
            ]

    def stop_job(self, job_id: str) -> bool:
        """Request cancellation of a running OR queued job.

        Marks the job stop-requested and signals its process group (SIGTERM,
        then SIGKILL after a grace period). A job still queued (no subprocess
        yet) is cancelled before it ever starts. Returns True if a running/
        queued job was found and signaled, False if the job is unknown or
        already terminal. Idempotent — a second call on an already-stopping job
        is a no-op that still returns True.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.get("status") not in ("running", "queued"):
                return False
            already = job.get("_stop_requested", False)
            job["_stop_requested"] = True
            process = job.get("_process")
        if already:
            return True
        if process is None:
            # Worker thread hasn't spawned the subprocess yet — it's either
            # still queued or between acquiring a slot and Popen. _run() checks
            # the _stop_requested flag at both points and will skip/terminate it.
            return True
        self._terminate_group(process)
        return True

    def _terminate_group(self, process: subprocess.Popen) -> None:
        """SIGTERM the job's process group, then SIGKILL survivors after a grace
        period. The grace/escalation runs in a daemon thread so callers (the API
        handler) return immediately. Falls back to signaling just the process if
        the group is already gone."""
        def _signal(sig: int) -> None:
            try:
                os.killpg(os.getpgid(process.pid), sig)
            except (ProcessLookupError, PermissionError):
                # Group already reaped, or we can't signal it — try the leader
                # directly as a best effort.
                try:
                    process.send_signal(sig)
                except (ProcessLookupError, ValueError):
                    pass

        _signal(signal.SIGTERM)

        def _escalate() -> None:
            try:
                process.wait(timeout=self._STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _signal(signal.SIGKILL)

        threading.Thread(target=_escalate, daemon=True).start()

    def _finalize(
        self,
        job_id: str,
        exit_code: int,
        started_at: datetime,
        finished_at: datetime,
    ) -> None:
        """Record a job's terminal state and fire its finalize callback. Shared
        by every terminal path (normal exit, cancelled-while-running, and
        cancelled-while-queued) so provenance is always written and a run never
        gets stuck in 'running'/'queued'."""
        callback: Optional[FinalizeCallback] = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                duration = (finished_at - started_at).total_seconds()
                job["exit_code"] = exit_code
                # A stopped job exits non-zero from the SIGTERM/SIGKILL (or never
                # ran at all); report it as "cancelled" rather than "failed" so
                # the GUI can tell a user-requested stop from a pipeline error.
                if job.get("_stop_requested"):
                    job["status"] = "cancelled"
                else:
                    job["status"] = "succeeded" if exit_code == 0 else "failed"
                job["started_at"] = started_at.isoformat()
                job["finished_at"] = finished_at.isoformat()
                job["duration_seconds"] = duration
                callback = job.get("_finalize_callback")
        # Fire the callback OUTSIDE the lock — it can be slow (writes multiple
        # json files, may hit the indexer's SQLite). Soft-fail per the T-07
        # policy: log + record to metadata_failures.jsonl, never raise into the
        # worker loop, never mask the subprocess result.
        if callback is not None:
            try:
                callback(job_id, exit_code, started_at, finished_at)
            except Exception:
                logger.exception("finalize_callback failed for %s", job_id)
                self._record_metadata_failure(job_id, sys.exc_info())

    def _cancel_before_start(self, job_id: str) -> None:
        """Terminal path for a job stopped while still queued (never spawned)."""
        now = datetime.now(timezone.utc)
        with self._lock:
            job = self._jobs.get(job_id)
            started = job.get("queued_at") if job else None
        started_dt = _parse_iso(started) or now
        self._finalize(job_id, _CANCELLED_EXIT_CODE, started_dt, now)

    def _run(
        self,
        job_id: str,
        command: str,
        cwd: Optional[Path],
        env: Optional[Dict[str, str]],
        log_path: Path,
        semaphore: Optional[threading.Semaphore],
    ) -> None:
        acquired = False
        # --- Concurrency gate -------------------------------------------------
        # Wait for a free slot in this category, but stay cancellable while
        # queued: poll the semaphore so a stop request doesn't sit blocked.
        if semaphore is not None:
            while not semaphore.acquire(timeout=0.5):
                with self._lock:
                    job = self._jobs.get(job_id)
                    should_stop = job is None or job.get("_stop_requested", False)
                if should_stop:
                    if job is not None:
                        self._cancel_before_start(job_id)
                    return
            acquired = True
        try:
            # A stop may have landed between acquiring the slot and starting.
            with self._lock:
                job = self._jobs.get(job_id)
                should_stop = job is None or job.get("_stop_requested", False)
                on_start = job.get("_on_start") if job else None
            if should_stop:
                if job is not None:
                    self._cancel_before_start(job_id)
                return

            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as log:
                started_at = datetime.now(timezone.utc)
                # Flip to "running" and stamp the real start now that we hold a
                # slot and are about to spawn.
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job["status"] = "running"
                        job["started_at"] = started_at.isoformat()
                log.write(f"# started_at_utc: {started_at.isoformat()}\n")
                log.write(f"$ {command}\n\n")
                log.flush()
                process = subprocess.Popen(
                    command,
                    cwd=str(cwd) if cwd else None,
                    env={**os.environ, **(env or {})},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=True,
                    text=True,
                    # Own session/process group so stop_job() can signal the
                    # whole tree (the wrapping shell, the bash batch, and every
                    # vsnp3 worker it spawns) with a single killpg — not just the
                    # top shell, which would orphan the running vsnp3 children.
                    start_new_session=True,
                )
                # Publish the handle so stop_job() can reach the group. If a stop
                # request beat us here, honor it now instead of letting the batch
                # run unkillable.
                stop_requested = False
                with self._lock:
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job["_process"] = process
                        stop_requested = job.get("_stop_requested", False)
                # Report the real OS pid so the caller can record a restart-proof
                # external lock (its process group == pid, via start_new_session).
                if on_start is not None:
                    try:
                        on_start(job_id, process.pid)
                    except Exception:
                        logger.exception("on_start hook failed for %s", job_id)
                if stop_requested:
                    self._terminate_group(process)
                exit_code = process.wait()
                finished_at = datetime.now(timezone.utc)
                duration = (finished_at - started_at).total_seconds()
                log.write("\n")
                log.write(f"# finished_at_utc: {finished_at.isoformat()}\n")
                log.write(f"# duration_seconds: {duration:.2f}\n")
            self._finalize(job_id, exit_code, started_at, finished_at)
        finally:
            # Always release the slot so the next queued job in this category
            # can start — even if the run threw before finalize.
            if acquired and semaphore is not None:
                semaphore.release()

    def _record_metadata_failure(self, job_id: str, exc_info: tuple) -> None:
        """Append one JSONL line per finalize-callback failure. Best-effort —
        a logging failure here is itself logged but does not raise."""
        exc_type, exc_value, exc_tb = exc_info
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "exception_type": exc_type.__name__ if exc_type else "Unknown",
            "exception_message": str(exc_value) if exc_value else "",
            "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        }
        try:
            _METADATA_FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with _METADATA_FAILURE_LOG.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            logger.exception("failed to record metadata failure for %s", job_id)
