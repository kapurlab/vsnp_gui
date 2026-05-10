# JobManager finalize-callback patch

Surface change to `backend/app/jobs.py` for T-07 writer integration. Three
edits, all additive. The existing `start_job` callers that don't pass
`finalize_callback` see no behavior change.

## Edit 1 — imports (top of file)

Add to existing imports:

```python
import json
import sys
import traceback
from datetime import datetime, timezone
```

## Edit 2 — `start_job` signature + storage

Add `finalize_callback` parameter, store on the per-job entry. The callback
signature is `(job_id, exit_code, started_at, finished_at) -> None`.

```python
def start_job(
    self,
    name: str,
    command: str,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    finalize_callback: Optional[Callable[[str, int, datetime, datetime], None]] = None,
) -> str:
    job_id = str(uuid.uuid4())  # or whatever the existing scheme is
    self._jobs[job_id] = {
        "name": name,
        "status": "running",
        "started_at": None,      # populated by _run
        "finished_at": None,     # populated by _run
        "exit_code": None,       # populated by _run
        "_finalize_callback": finalize_callback,  # NEW
    }
    thread = threading.Thread(
        target=self._run,
        args=(job_id, command, cwd, env),
        daemon=True,
    )
    thread.start()
    return job_id
```

`Callable` and `datetime` come from the imports above. If the existing file
already has its own typing aliases, follow that convention.

## Edit 3 — `_run` body, after `process.wait()`

Replace the existing exit-handling block with one that fires the callback
inside a try/except and records failures to
`/srv/kapurlab/audit/metadata_failures.jsonl` per the locked soft-fail-at-finalize
policy. The subprocess result is preserved either way; the metadata gap is logged.

```python
def _run(self, job_id: str, command: str, cwd: Optional[Path], env: Optional[Dict[str, str]]) -> None:
    job = self._jobs[job_id]
    started_at = datetime.now(tz=timezone.utc)
    job["started_at"] = started_at.isoformat()

    try:
        process = subprocess.Popen(
            command, shell=True, cwd=cwd, env=env,
            # ... existing stdout/stderr handling ...
        )
        exit_code = process.wait()
    except Exception as e:
        exit_code = -1
        logger.exception("subprocess launch failed for %s", job_id)

    finished_at = datetime.now(tz=timezone.utc)
    job["finished_at"] = finished_at.isoformat()
    job["exit_code"] = exit_code
    job["status"] = "ok" if exit_code == 0 else "failed"

    # NEW: fire finalize_callback (soft fail; never masks subprocess result)
    callback = job.get("_finalize_callback")
    if callback is not None:
        try:
            callback(job_id, exit_code, started_at, finished_at)
        except Exception:
            logger.exception("finalize_callback failed for %s", job_id)
            self._record_metadata_failure(job_id, sys.exc_info())
```

## Edit 4 — new `_record_metadata_failure` method on JobManager

Append-only JSONL log at `/srv/kapurlab/audit/metadata_failures.jsonl`.
No `chattr +a` in V1: this is a forensic trail, not a tamper-evident ledger.
Janitor catches metadata gaps via the 48h timeout in
`vsnp_provenance.index.gc_running` and `Indexer.mark_orphaned_running`.

```python
def _record_metadata_failure(self, job_id: str, exc_info: tuple) -> None:
    """Log a finalize-callback failure to the audit trail.

    Best-effort: logging failures here are themselves logged but do not raise.
    """
    audit_dir = Path("/srv/kapurlab/audit")
    audit_dir.mkdir(parents=True, exist_ok=True)
    log_path = audit_dir / "metadata_failures.jsonl"

    exc_type, exc_value, exc_tb = exc_info
    entry = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "job_id": job_id,
        "exception_type": exc_type.__name__ if exc_type else "Unknown",
        "exception_message": str(exc_value) if exc_value else "",
        "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    }
    try:
        with log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        logger.exception("failed to record metadata failure for %s", job_id)
```

## Edit 5 — call site changes in main.py

The writer integration in `main.py:step1_run` and `main.py:step2_run` calls
the writer's dispatch helpers BEFORE `start_job`, then passes a finalize
callback that closes over the dispatch return values. Sketch:

```python
# step1_run
batch_run_id, sample_run_ids = provenance_writer.dispatch_step1_batch(
    cfg, project_dir, samples, payload.reference,
    user=current_user, ood_session_id=ood_session_id,
)

def finalize_step1_cb(job_id: str, exit_code: int, started_at: datetime, finished_at: datetime) -> None:
    provenance_writer.finalize_step1_batch(
        project_dir, batch_run_id, exit_code, started_at, finished_at,
    )

job_id = job_manager.start_job(
    name="step1",
    command=wrap_cmd(cfg, f"bash {script_path}"),
    cwd=step1_dir,
    env=build_env(cfg),
    finalize_callback=finalize_step1_cb,  # NEW
)
```

```python
# step2_run
try:
    step2_run_id, pipeline_run_id = provenance_writer.dispatch_step2(
        cfg, project_dir, payload.reference, cmd, flags_list,
        user=current_user, ood_session_id=ood_session_id,
        is_shared=_is_shared_project(cfg, project_dir),
        resolved_vcf_db_folders=_resolved_vcf_db_folders(cfg),
    )
except provenance_writer.Step2DispatchBlocked as e:
    raise HTTPException(
        status_code=409,
        detail=str(e),
    )

def finalize_step2_cb(job_id, exit_code, started_at, finished_at):
    provenance_writer.finalize_step2(
        project_dir, step2_run_id, exit_code, started_at, finished_at,
    )

job_id = job_manager.start_job(
    name="step2",
    command=wrap_cmd(cfg, cmd),
    cwd=step2_dir,
    env=step2_env,
    finalize_callback=finalize_step2_cb,
)
```

For step1, the bash batch script generation needs the sentinel wrapper.
Where the existing `run_sample()` invokes `vsnp3_step1.py`, replace the
single line with the writer's wrapper output:

```python
# in the script template
"run_sample() {",
"  local d=\"$1\"",
"  cd \"$d\" || return 1",
# ... existing R1/R2 detection ...
*provenance_writer.step1_sample_command_with_sentinels(
    f"{cfg['vsnp3_path']}/bin/vsnp3_step1.py -r1 \"$R1\" -r2 \"$R2\" -t {payload.reference}"
).splitlines(),
"}",
```

The writer's `step1_sample_command_with_sentinels()` returns multi-line
bash; splitlines() folds it into the existing list-of-strings template.

## What this patch does NOT do

- Persistence of job state across uvicorn restart. The existing JobManager
  is in-memory; this patch does not change that. Restart loses the
  finalize_callback registration along with the job entry. A run dispatched
  before restart and not finalized before restart will end up as a stuck
  `running` record in run_metadata.json; the disk-side janitor
  (`gc_running` in `vsnp_provenance.index`) marks it `unknown_terminated`
  after the configured timeout. Honest scope for V1.

- Any change to the existing `get_job()` API. Polling continues to work
  unchanged.

- chattr +a on metadata_failures.jsonl. Per the locked discussion, this
  is a forensic trail not a tamper-evident ledger; +a is the wrong tool
  for this access pattern.
