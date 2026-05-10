# T-07 Writer Context for Opus

> Hand this to Opus along with the locked-in V1 plan
> (`T-07-implementation-plan.md`) and the schema diff
> (`T-07-red-team-feedback.md`). It surfaces the existing vsnp_gui code
> patterns the writer module has to slot into, plus two architectural
> questions Opus needs to answer before drafting.
>
> Schema reference: `backend/app/vsnp_provenance/__init__.py` (reader) and
> `backend/app/vsnp_provenance/index.py` (indexer + janitor) — both shipped,
> both passing 30/30 smoke-test assertions in the actual vsnp3 env. The
> writer's job is to produce records the existing reader and indexer can
> consume without changes.

---

## 1. Existing dispatch surface

### JobManager (`backend/app/jobs.py`)

```python
class JobManager:
    def start_job(self, name: str, command: str,
                  cwd: Optional[Path] = None,
                  env: Optional[Dict[str, str]] = None) -> str:
        # Spawns a daemon thread that runs subprocess.Popen(command, shell=True).
        # Returns job_id. Updates self._jobs[job_id]["status"] when subprocess
        # exits. NO finalize callback. NO on-completion hook. Job state is
        # polled via get_job(job_id).

    def get_job(self, job_id: str) -> Optional[Dict]:
        # In-memory only. Restart loses state.
```

Per-job log is written to `<jobs_dir>/<job_id>.log` with a header (`# started_at_utc:`) and footer (`# finished_at_utc:`, `# duration_seconds:`).

**This is the single biggest integration question.** The writer needs *some* way to know when the subprocess finished, so the finalize-side metadata write can fire. Three options:

- **(A) Add a `finalize_callback` parameter to `start_job`.** Cleanest: one parameter on JobManager, one call after `process.wait()`. Writer's `finalize_step{1,2}()` becomes the callback. ~10 LoC change to JobManager.
- **(B) Background poller in main.py (or separate thread).** Polls `get_job()` until status transitions, then calls writer. More moving parts; resilient to JobManager restart only if state is persisted (it isn't today).
- **(C) Wrap the subprocess invocation with a shell trap or `; finalize_cmd`** appended after the actual command. Fragile — exit code propagation, no easy way to capture started/finished timestamps with sub-second precision.

**Recommendation: A.** It's a five-line JobManager change. I can do that change as part of the writer integration; Opus can assume it exists in the writer's design.

### step1 dispatch (`main.py:1087`-1213, "step1_run")

Step1 is *not* a per-sample subprocess. It's a **bash batch** generated at dispatch time:

```python
script_path.write_text("\n".join([
    "#!/bin/bash",
    "set -uo pipefail",
    "FAIL=0",
    f"MAX_PARALLEL={max_parallel}",
    "OVERALL_START=$(date +%s)",
    "run_sample() {",
    "  local d=\"$1\"",
    # ... cd into sample dir, ls fastq_R1/R2, invoke vsnp3_step1.py ...
    "}",
    "for d in */; do",
    "  run_sample \"$d\" &",
    "  pids+=(\"$!\")",
    "  if [ ${#pids[@]} -ge \"$MAX_PARALLEL\" ]; then",
    "    wait \"${pids[0]}\" || FAIL=1",
    "    pids=(\"${pids[@]:1}\")",
    "  fi",
    "done",
    # ...
    "exit $FAIL",
]))
job_id = job_manager.start_job(
    name="step1",
    command=wrap_cmd(cfg, f"bash {script_path}"),
    cwd=step1_dir,
    env=build_env(cfg),
)
```

So JobManager only sees the batch-level job. The 16 per-sample `vsnp3_step1.py` invocations happen inside the bash subshells, invisible to Python.

**Per-sample step1 dispatch_metadata.json + run_metadata.json** therefore needs one of:

- **(P1) Have the writer pre-create per-sample `run_metadata.json` files at batch dispatch**, with `status: "running"` and a single shared `dispatch_state` sub-block (the per-sample-specific bits are minimal — input fastq paths). Per-sample finalize then happens via post-batch scan: when the batch JobManager job finalizes, the writer walks per-sample dirs, reads `run_step1.log` for each (which has its own start/end timestamps), and rewrites each per-sample metadata as terminal.
- **(P2) Have the bash script invoke a thin CLI** (`python -m vsnp_provenance.write_step1_per_sample ...`) inline in `run_sample()` to dispatch and finalize per-sample. Maximum precision but adds a Python startup per sample (~150 ms overhead × 16 samples × parallelism = real but not dramatic).

**Recommendation: P1.** The dispatch_state for all 16 samples in a batch is identical except for input filenames; one batch-level snapshot pre-rendered into per-sample files at dispatch + post-batch finalize is the simplest path. Per-sample timing comes from `run_step1.log`'s existing footer. Opus should design `dispatch_step1_batch(...)` and `finalize_step1_batch(...)` as the primary entry points; per-sample writes happen inside those.

### step2 dispatch (`main.py:1324`-1394, "step2_run")

Single subprocess. Vastly simpler than step1.

```python
cmd = f"vsnp3_step2.py -wd {vcf_source_dir} {flags_str} -t {payload.reference}{remove_arg}"
label_script = _build_tree_label_script(step2_dir, cfg, label_style)
if label_script:
    cmd = f"{cmd} && python {label_script}"
step2_env = build_env(cfg)
if payload.bootstrap and payload.bootstrap > 0:
    step2_env["VSNP3_BOOTSTRAP"] = str(int(payload.bootstrap))
job_id = job_manager.start_job(
    name="step2",
    command=wrap_cmd(cfg, cmd),
    cwd=step2_dir,
    env=step2_env,
)
```

Step2 is also where the **pipeline_run record** is created (per V2 schema). At dispatch, the writer walks `<project>/step1/<sample>/run_metadata.json` for every sample, collects `run_id` values into `consumed_step1_run_ids`, generates a fresh `pipeline_run_id`, writes both the per-step2 `run_metadata.json` (carrying that `pipeline_run_id` + `parent_run_ids`) and the standalone `<project>/_provenance/pipeline_runs/<id>.json`.

For step2's pipeline_run creation: handle the case where some step1 samples have no `run_metadata.json` (i.e. they ran before T-07 landed) by setting `consumed_step1_run_ids_complete: false` and surfacing a warning in `consistency.warnings`.

---

## 2. Helpers already available in vsnp_gui

The writer should compose with these rather than reimplement:

| Helper | Lives in | Returns | Notes |
|---|---|---|---|
| `load_config()` | `app.config` | dict | Per-user config; includes `vsnp3_path`, `vcf_db_folders_root`, `disabled_vcf_db_paths`, etc. |
| `_project_dir_for(cfg, project)` | `app.main` | Path | Resolves a project name to its actual directory (handles per-user vs shared roots). |
| `_resolved_vcf_db_folders(cfg)` | `app.main` | list of dicts | The full inventory the writer needs for `vcf_db_inventory_at_dispatch`. Each entry: `{path, reference, name, sample_count, enabled, scope}`. |
| `wrap_cmd(cfg, command)` | `app.main` | str | Already prepends `PYTHONWARNINGS=...` and `PATH=...`. Writer doesn't need to touch this — just consume the env it produces. |
| `build_env(cfg)` | `app.main` | dict | The env passed to `start_job`. Writer should capture from this (or from `os.environ` post-launch) for the `cli.env_vars` block. |

The writer should expose its own helpers in turn:

- `compute_folder_manifest(folder: Path) -> tuple[str, list[ReferenceFile]]` — returns `(rolled_sha256, [files_with_per_file_hashes])`. Hash format per the schema diff: sorted by relpath, `relpath\0sha256\n` concat, SHA-256 of that. Used for both the reference and each VCF DB selection.
- `compute_input_identity(path: Path, threshold_bytes: int) -> tuple[str | None, IdentityMethod]` — returns `(sha256_or_none, identity_method)`. Threshold default 256 MB per the locked decisions; configurable via `cfg.get("provenance", {}).get("hash_max_bytes", 268435456)`.
- `current_env_snapshot(vsnp3_install_path: Path) -> Environment` — does the conda env yaml + pip freeze + `dpkg -l` capture, normalizes (drop `prefix:`, sort dependencies), content-hashes, dedups into `/srv/kapurlab/audit/env_snapshots/<sha256>.{yaml,txt}`, AND copies into the per-run `_provenance/` dir (belt-and-braces per locked decisions). Cached in module-level dict keyed by `(install_path, install_path_mtime)` to keep dispatch latency near zero after the first call.
- `current_vsnp_gui_state(deploy_path: Path) -> VsnpGui` — git sha (from `git -C {deploy_path} rev-parse HEAD`), branch, dirty flag, `os.getpid()` as `uvicorn_pid`, process start time as `uvicorn_started_at` (cache once at module import).
- `current_vsnp3_state(install_path: Path) -> Vsnp3` — `vsnp3 --version` (parse), install_path, `subprocess_pid` left null at dispatch time (we don't have it yet), `subprocess_exe_realpath` resolved from `<install>/bin/vsnp3_step{1,2}.py` via `os.path.realpath`. The `applied_patches` list reads `deploy/vsnp3-patches/v3.16-kapurlab.patch`'s SHA-256 once at import; static for the life of a deploy.
- `read_edit_record_refs(audit_log: Path, sample: str, run_started_at: datetime) -> list[EditRecordRef]` — for the `edited_samples_at_run_time` block. Reads `<project>/audit/edits.jsonl`, finds entries matching the sample whose timestamp ≤ run_started_at, returns the most recent one as `{audit_log, line_number, record_sha256}` (record SHA = SHA-256 of the JSON line itself, so the link survives line renumbering).
- `_atomic_json_write(path: Path, data: dict)` — `tempfile.mkstemp` in same dir + `os.replace`. Used for the finalize-side rewrite.

---

## 3. Sub-block, not separate file (per locked decisions)

The schema-diff doc proposed a separate `dispatch_metadata.json` file. The locked decision is **single file with a frozen `dispatch_state` sub-block** inside `run_metadata.json`. Writer flow:

```python
def dispatch_step2(project_dir, run_id, ...):
    state = build_dispatch_state(...)  # full snapshot of all the dispatch-time fields
    initial_record = {
        "schema_version": 2,
        "step": "step2",
        "run_id": run_id,
        "pipeline_run_id": ...,
        "parent_run_ids": [...],
        "started_at": now_iso(),
        "status": "running",
        # ... everything from `state` flattened into the top level
        # AND a frozen copy stashed under `dispatch_state`:
        "dispatch_state": state,
    }
    _atomic_json_write(project_dir / "step2" / "run_metadata.json", initial_record)

def finalize_step2(project_dir, run_id, exit_code, started_at, finished_at):
    path = project_dir / "step2" / "run_metadata.json"
    rec = _read_json(path)  # round-trip through json, not the pydantic reader
    rec["finished_at"] = finished_at.isoformat()
    rec["duration_seconds"] = (finished_at - started_at).total_seconds()
    rec["status"] = "ok" if exit_code == 0 else "failed"
    rec["exit_code"] = exit_code
    rec["outputs"] = scan_outputs(project_dir / "step2")
    # dispatch_state sub-block is NOT touched
    _atomic_json_write(path, rec)
```

The reader's `diff_dispatch_vs_final()` currently expects a separate dispatch file. Opus should add `diff_dispatch_vs_final_subblock(metadata_path)` to the reader (or refactor the existing function to read the sub-block when present, fall back to separate file otherwise) so the API works against either layout. The reader scaffold already preserves unknown fields via `extra="allow"`, so storing a `dispatch_state` dict on `RunMetadataV2` requires only a model field addition.

---

## 4. Two architectural calls Opus needs to make

These don't have a clean right answer; surfacing for design conversation:

### 4a. step1 per-sample writes: pre-create + post-batch scan, or inline CLI?

P1 (pre-create + post-batch scan, my recommendation):
- Dispatch creates 16 per-sample `run_metadata.json` files all carrying the same `dispatch_state` (modulo per-sample input filenames).
- Bash batch runs as today, writing to per-sample `run_step1.log`.
- After JobManager's finalize callback fires for the batch, the writer walks per-sample dirs, parses each `run_step1.log` for actual start/end timestamps and exit code (the script writes `# duration_seconds: N` and `Error: exit N` lines), and rewrites each per-sample metadata as terminal.
- Plus a roll-up `<project>/step1/run_metadata.json` that aggregates.

P2 (inline CLI in bash):
- Bash script's `run_sample()` calls `python -m vsnp_provenance.writer step1-sample dispatch <sample>` before vsnp3_step1.py and `... finalize <sample> --exit $STATUS` after.
- More accurate per-sample timestamps (no log parsing), but adds ~150ms × 32 calls × parallelism worth of Python startup cost, and the bash script gets noisier.

I lean P1; happy to be talked into P2 if Opus thinks the timestamp precision matters more than I'm crediting.

### 4b. JobManager finalize-callback: A, B, or C from §1?

I'm strongly for **A** (add `finalize_callback` parameter). The other two have downsides that don't pay for themselves.

If Opus agrees, I'll do the JobManager change as a small precursor commit so the writer can assume the API exists.

---

## 5. Concrete function surface to ship in Phase 1

```python
# backend/app/provenance_writer.py

def dispatch_step1_batch(
    cfg: dict,
    project_dir: Path,
    samples: list[str],
    reference_name: str,
    *,
    user: str,
    ood_session_id: str | None,
) -> tuple[str, dict[str, str]]:
    """Returns (batch_run_id, {sample: per_sample_run_id}).

    Writes <project>/step1/run_metadata.json (batch roll-up, status=running,
    with dispatch_state sub-block) and per-sample
    <project>/step1/<sample>/run_metadata.json (each with status=running,
    its own run_id, the same dispatch_state).
    """

def finalize_step1_batch(
    project_dir: Path,
    batch_run_id: str,
    exit_code: int,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Walks per-sample dirs, parses run_step1.log for per-sample
    start/end/exit_code, rewrites each per-sample metadata as terminal,
    and finalizes the batch roll-up."""

def dispatch_step2(
    cfg: dict,
    project_dir: Path,
    reference_name: str,
    cli_command: str,
    cli_flags: list[str],
    *,
    user: str,
    ood_session_id: str | None,
) -> tuple[str, str]:
    """Returns (step2_run_id, pipeline_run_id).

    Writes <project>/step2/run_metadata.json (status=running, with
    dispatch_state sub-block, vcf_db_selections, vcf_db_inventory_at_dispatch,
    edited_samples_at_run_time, parent_run_ids derived from step1 metadata).
    Also writes <project>/_provenance/pipeline_runs/<pipeline_run_id>.json.
    """

def finalize_step2(
    project_dir: Path,
    step2_run_id: str,
    exit_code: int,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Rewrites step2 run_metadata.json with terminal status, scans outputs."""
```

Plus the helper surface listed in §2.

---

## 6. Locked-in design decisions (recap)

From `T-07-implementation-plan.md`:

1. `dispatch_state` sub-block in single `run_metadata.json` file (not separate dispatch_metadata.json).
2. Defer `staged_path`. Use `identity_method = "sha256"` for inputs ≤ 256 MB; `"size_mtime_path"` above. Threshold configurable via `cfg["provenance"]["hash_max_bytes"]`.
3. Belt-and-braces env capture: shared store at `/srv/kapurlab/audit/env_snapshots/<sha256>.{yaml,txt}` AND per-run copy in `_provenance/`.
4. `schema_version: 2` (Opus's reader already commits to this; not flipping for V1 to avoid churning tested code).
5. Wire the full reader API in V1 — `iter_run_metadata`, `diff_dispatch_vs_final`, `reconstruct_pipeline_run_from_step2` all in scope.

---

## 7. Files Opus already has

- `backend/app/vsnp_provenance/__init__.py` — reader (their own draft, unchanged).
- `backend/app/vsnp_provenance/index.py` — indexer + `gc_running` janitor (their own draft, unchanged; smoke test passes 30/30 against vsnp3 env).
- `backend/app/test_provenance_indexer.py` — smoke test runner.

What they need to produce:

- `backend/app/provenance_writer.py` — the writer module with the function surface in §5.
- A small modification to `backend/app/vsnp_provenance/__init__.py` to make `diff_dispatch_vs_final()` work against the sub-block layout (or add a sibling `diff_dispatch_vs_final_subblock()`).
- A short integration patch to `backend/app/main.py` for `step1_run` and `step2_run` (or — preferably — the integration patches stay in *my* hands; Opus produces only the writer module + reader patch, and I wire it into main.py against the JobManager change). Whichever they prefer; I have the existing-code context, they have the writer-design context.

---

## 8. Things that are actually easy that they shouldn't worry about

- Pydantic v2 is installed (`2.13.4`) in the deploy env. Their existing reader scaffold uses v2 syntax and runs.
- The vsnp3 env's `python` is `/srv/kapurlab/tools/vsnp3/bin/python` 3.12.x. Modern syntax fine.
- `git rev-parse HEAD` from inside `/srv/kapurlab/tools/vsnp_gui` returns the deploy SHA; deploy is a regular git clone.
- `os.environ` at uvicorn process startup is a stable enough source for env capture (we set `OOD_*` vars in `script.sh.erb` and they persist).
- The applied-patches set is small and known: 4 hunks total in `deploy/vsnp3-patches/v3.16-kapurlab.patch`. SHA-256 the whole patch file once at import; the per-hunk decomposition is metadata baked into the schema.
