# T-07 Implementation Plan (post red-team)

> Synthesizes the original red-team brief and the returned schema diff into
> a concrete V1 scope. Where I disagree with the red-team or think something
> is V2-not-V1, that's flagged below with rationale; user adjudicates.
>
> Source artifacts:
> - [`T-07-red-team-brief.md`](T-07-red-team-brief.md) — original ask
> - [`T-07-red-team-feedback.md`](T-07-red-team-feedback.md) — schema diff returned
> - [`backend/app/provenance.py`](../../backend/app/provenance.py) — reader scaffold (saved, not wired)

---

## Headline

The red-team did real work. The single most important catches:

1. **`pipeline_run_id` linking step1+step2.** The brief treated each step as an independent provenance unit; the actual unit-of-truth ("which run made Figure 3") is the pipeline. Adopt.
2. **Reference is a folder, not just a fasta.** `define_filter.xlsx` drives the SNP grouping; hashing only the fasta is a meaningful gap. Adopt.
3. **Environment capture (conda env yaml + pip freeze + system tool versions).** Without this, "reproducible" isn't really reproducible — it's "re-runnable on whatever the install looks like today." Adopt.
4. **`trust_scope` block.** Forces honest declaration of what the metadata actually supports, so future-you doesn't misuse it for forensics it can't underwrite. Adopt.
5. **SQLite cross-project index instead of JSONL.** Right tool for the queryable surface. Adopt.

---

## Adopt as proposed

- `pipeline_run_id` + `parent_run_ids` + the new `pipeline_runs/<id>.json` file at project root.
- `trust_scope` block with the proposed enums; ship `local_ntp` / `ood_session_uuid` / `append_only_advisory` / `filename_only` as V1 defaults.
- Cut `client_ip` from `actor` (meaningless behind the OOD reverse proxy + minor PII).
- Add `actor.uid` cross-check.
- `vsnp_gui.{uvicorn_pid, uvicorn_started_at}` so `git_sha` provenance is verifiable post-hoc.
- `vsnp3.{subprocess_pid, subprocess_exe_realpath}` same reason.
- `applied_patches` as objects with `patch_sha256` (not just label strings).
- **Reference folder manifest** with per-file hashes + rolled-up `folder_manifest_sha256` + `resolved_via_symlink`. Hash format as proposed: sorted by relpath, `relpath\0sha256\n` concat, SHA-256 of that.
- **Environment block** with conda env yaml + pip freeze (content-hashed, captured copy stored to `_provenance/`) + filtered `dpkg -l` for tools vsnp3 actually shells out to.
- VCF DB: add `folder_manifest_sha256` per selection.
- `vcf_db_inventory_at_dispatch` (distinguishes "user opted out" from "didn't exist yet" — both are real states V2 already records, this just persists them).
- `edited_samples_at_run_time` becomes `[{sample, edit_record_refs: [{audit_log, line_number, record_sha256}]}]`.
- CLI env_vars allow-list (with explicit `null` for "policy says capture this but it's unset"); `env_capture_policy: "allowlist_v1"` for diffing future versions.
- Outputs: drop hashes (gold-plating); keep `path`/`exists`/`mtime`.
- **SQLite cross-project index** at `/srv/kapurlab/audit/runs.sqlite` with the proposed schema. Nightly cron exports to JSONL for archive/grep.
- **Janitor cron** to mark `running` records older than 48h as `unknown_terminated` and ping operators.

---

## Adopt with modifications

- **`status` enum** — adopt as proposed but add `aborted` for user-cancelled runs (the GUI has a kill flow; want to distinguish from `failed`).
- **Schema version naming.** Skip the V1→V2 leapfrog: just call this V1, with the reader library as a hard requirement of shipping. The "V1-with-no-parser trap" is solved by treating the reader as load-bearing, not by inflating the version number. (Bikeshed; happy to adopt the V2 numbering if you prefer the explicit signal.)
- **Reader library scope for V1.** Save the file as scaffold (done — `backend/app/provenance.py`), but only wire `RunMetadataV2.model_validate()` into the writer's pre-persist check. Defer `iter_run_metadata`, `diff_dispatch_vs_final`, `reconstruct_pipeline_run_from_step2` until there's a consumer (the SQLite indexer or an audit UI). Avoids shipping 600 lines of code with no caller.

---

## Push back

Three places I'd argue against the red-team's choice. The user adjudicates.

### 1. `dispatch_metadata.json` as a separate write-once file

Their argument: write-once is more tamper-resistant; explicit forensic primitive for "did the world change between dispatch and execution?"

My counter: a `dispatch_state` *sub-block* inside the same `run_metadata.json` (frozen at dispatch, never modified at finalize) gets you the same forensic primitive with one file instead of two. The finalize pathway uses `tempfile + os.replace` so atomicity is the same; the writer just preserves the existing `dispatch_state` block verbatim. Fewer files, fewer places metadata can drift, simpler reader API.

The red-team's "rewrite at finalize is a tamper surface" is technically correct but misframed: the user owns the file either way. If we're worried about user tampering, neither approach helps without filesystem-level immutability (object lock, S3-style WORM). For *honest mistakes* — the realistic failure mode — `tempfile + os.replace` of a single file is enough.

**Recommendation: single file with frozen `dispatch_state` sub-block.** Reader provides `diff_dispatch_vs_final()` against that sub-block. Same forensic primitive, half the files.

### 2. `staged_path` / `identity_method: staged_readonly`

Their argument: hashing multi-GB fastq is expensive; staging to a read-only location is identity-by-construction.

My counter: this introduces a substantial new subsystem to the upload flow — a staging dir per project (or per run), GC policy for stale stages, atomic move semantics from `download/` to `_staging/<run_id>/`, copy-vs-symlink-vs-hardlink decision, disk pressure handling. That's a separate ticket-sized piece of work. Without it, the field is unimplemented; with it, V1 ships way later.

**Recommendation for V1: drop `staged_path` entirely. Use `identity_method: "size_mtime_path"` for files >256 MB, `"sha256"` for ≤256 MB.** Document the limitation. Defer staging to T-07-followup or T-12b (which is already about a flat sample-store and overlaps directly).

### 3. Capturing conda env yaml + pip freeze on every run

Their argument: needed for actual reproducibility.

My partial counter: yes, but capturing on *every* run dumps `~5 KB × 16 samples × N reruns` of duplicated yaml into `_provenance/` dirs. The env doesn't change between runs unless someone runs `mamba install`.

**Recommendation: hash and dedup.** Capture once per (env path + content-hash) pair into a shared store at `/srv/kapurlab/audit/env_snapshots/<sha256>.{yaml,txt}`. Per-run metadata references by hash + symlinks the snapshot file into `_provenance/` if the run wants a local copy. One-time capture cost, queryable history of env changes, no duplication.

---

## Defer to V2 (with rationale)

| Item | Why V2 |
|---|---|
| `signed_tsa` timestamps | V1 is single-server, single-clock; signed timestamps are a real ask only when we federate across institutions or hit a dispute. |
| `merkle_chain` / `object_lock` tamper-resistance | Same — `append_only_advisory` is honest and matches what we can actually enforce today. |
| `lims_linked` chain of custody | Pre-T-12b (flat sample store); we don't have stable sample IDs across projects yet. |
| Reader's `reconstruct_pipeline_run_from_step2` | No legacy data to reconstruct from; everything from T-07 onward will have the metadata natively. |
| Reader's bulk indexing helpers | Will be written *with* the SQLite indexer, which is Phase 3 below. |
| `staged_path` upload flow rework | See pushback #2. |
| Per-output hashing for canonicalized trees | Belongs with a tree-canonicalization spec, separate ticket. |
| Container/snapshot-based reproducibility | V2 path that subsumes most of the env capture work. |

---

## Concrete V1 scope

- Two new files per pipeline:
  - `<project>/<step>/[<sample>/]run_metadata.json` (rewritten at finalize via `tempfile + os.replace`; contains a frozen `dispatch_state` sub-block).
  - `<project>/_provenance/pipeline_runs/<pipeline_run_id>.json` (created by step2 dispatch via implicit walk of step1 dirs; explicit creation by future CLI deferred).
- Per-step `_provenance/` dir holds:
  - Symlink to env snapshot at `/srv/kapurlab/audit/env_snapshots/<sha256>.yaml` (and `.txt` for pip freeze).
  - Existing `run_step1.log` keeps its place.
- New cross-project SQLite at `/srv/kapurlab/audit/runs.sqlite` with the proposed schema (incl. `project_renames` audit table).
- Nightly `runs.sqlite.jsonl` export cron.
- Janitor cron for `running` records older than 48h.
- Backend writer:
  - Hooks into `step1_run`, `step2_run`, and per-sample step1 finalize paths.
  - Validates against `RunMetadataV2.model_validate()` before persisting.
  - Hard-fail at dispatch if metadata write fails (run won't start).
  - Soft-warn if finalize-side write fails (run completed, log goes to stderr; janitor will mark it stale eventually).
- Reader scaffold saved as `backend/app/provenance.py` with the schema models actually used by the writer.

### Explicit non-goals for V1 (carry over from brief)

- No web UI for browsing provenance — files on disk + SQLite + ad-hoc grep.
- No "replay this run" button.
- No PROV-O export.
- No fingerprint-based sample-mix-up detection.

---

## Implementation phases

**Phase 1 — Capture (writer + scaffold).** ~1–2 days.
- Wire models from `provenance.py` into a `provenance_writer.py` module.
- Implement helpers: `compute_folder_manifest()`, `current_env_snapshot()`, `current_vsnp3_state()`, `current_vsnp_gui_state()`, `vcf_db_inventory()`.
- Hook into step1 dispatch (per-sample + roll-up), step1 finalize, step2 dispatch (with implicit pipeline_run creation), step2 finalize.
- `dispatch_state` sub-block frozen at dispatch, preserved at finalize.
- Smoke test: run nagalingam_test step1+step2 end-to-end, inspect produced JSON.

**Phase 2 — Cross-project ledger.** ~1 day.
- Create `runs.sqlite` schema + bootstrap script.
- On finalize, append/update SQLite row.
- `project_renames` table populated by an admin CLI (`kapurlab-rename-project`).
- Read endpoints (`/api/audit/runs?...`) optional V1.

**Phase 3 — Operational glue.** ~½ day.
- Janitor cron (`/etc/cron.d/vsnp_gui-janitor`).
- Nightly SQLite-to-JSONL export.
- Operator notification on `unknown_terminated` (stderr → systemd journal → email if you want; defer if you don't have an MTA).

**Phase 4 (later) — Reader/UI.** Out of scope for T-07 itself.
- Wire the rest of `provenance.py`'s helpers into the SQLite indexer.
- Build a "Run history" tab in the GUI per the existing T-13 / cross-project surface ticket.

---

## Decisions locked in (2026-05-10)

| # | Decision | Notes |
|---|---|---|
| 1 | **`dispatch_state` sub-block in `run_metadata.json`** (single file) | Frozen at dispatch, preserved verbatim through finalize via `tempfile + os.replace`. Reader provides `diff_dispatch_vs_final()` that compares the sub-block against the rest of the record. |
| 2 | **Defer `staged_path` to V2.** V1 uses a 256 MB hash threshold; configurable via a backend setting (`provenance.hash_max_bytes`, default `268435456`). Files above the threshold get `identity_method: "size_mtime_path"`; files at or below get `"sha256"`. Limitation documented in the rendered metadata via the `trust_scope.sample_chain_of_custody` field. |
| 3 | **Belt-and-braces env capture.** Compute the normalized content hash; write the env yaml + pip freeze to the shared store at `/srv/kapurlab/audit/env_snapshots/<sha256>.{yaml,txt}` if not already present; AND copy a verbatim snapshot into the per-run `_provenance/` dir. Per-run metadata references both. Storage is negligible (<5 KB per run); resilience to shared-store loss/GC/corruption is real. |
| 4 | **Schema version `2`** (revised 2026-05-10 after Opus delivered the indexer at V2). Reader, indexer, and indexer smoke-test all already commit to V2; the integer is bikeshed-level so keeping V2 avoids churning the well-tested code. The "V1-with-no-parser trap" the red team flagged is solved by the reader being load-bearing, not by the integer. |
| 5 | **Wire the full reader API in V1** — `load`, `load_dispatch` (now returns the sub-block from a `RunMetadataV1`), `load_pipeline_run`, `iter_run_metadata`, `diff_dispatch_vs_final`, `reconstruct_pipeline_run_from_step2`. Marginal cost is small; the indexer needs `iter_run_metadata` and reimplementing record loading there would be wasteful; `reconstruct_*` is needed within the first quarter of operational use for any retro-labeling of historical runs. |

For Phase 1, the dispatch model collapses: `DispatchMetadataV2` becomes a property accessor on `RunMetadataV2` returning the `dispatch_state` sub-block (or a sibling `diff_dispatch_vs_final_subblock(metadata_path)` helper that operates on the single file). Reader scaffold is now at `backend/app/vsnp_provenance/__init__.py` (package, alongside `index.py` for the SQLite indexer + janitor).

---

## Concurrency / latency model (raised in the red-team's follow-up, missing from my draft)

The original brief assumed metadata writes happen synchronously at dispatch. For step1's multi-sample batch (16 samples × env capture × reference manifest computation × input identity), naive sequential dispatch-side writes could add 30+ seconds of latency *before any vsnp3 subprocess runs*. Worth being explicit.

**Where the work actually lives:**

| Work | When | Cost | Cacheable? |
|---|---|---|---|
| Reference folder manifest hash | Once per (reference, run batch) | ~50–200 ms (small fasta + gbk + xlsx) | Yes — cache by reference path + mtime |
| Env snapshot hash + capture | Once per uvicorn process lifetime (until install changes) | First call ~1–5 s (conda env export + pip freeze + dpkg); subsequent <50 ms | Yes — process-memory cache, invalidate on install path mtime change |
| Input identity (sha256 ≤256 MB; size+mtime above) | Per input file | ~200 ms / 100 MB on this hardware | No |
| `vcf_db_inventory_at_dispatch` snapshot | Once per step2 dispatch | <50 ms (filesystem walk) | Per-process cache OK but not necessary |
| `run_metadata.json` write | Per run (per sample for step1) | <10 ms | n/a |

**Plan:**

- **step1 batch dispatch**: writes the *batch-level* `run_metadata.json` (under `<project>/step1/`) with `status: "running"` and the dispatch_state sub-block. Per-sample metadata writes happen *inside each worker*, after the worker acquires its parallelism slot from `step1_max_parallel: 8` and immediately before forking the vsnp3 subprocess. Per-worker metadata work runs in parallel with up to 7 other workers; user-visible latency is "first worker's metadata write before first subprocess starts", not "16 workers' metadata writes before any subprocess starts." On this hardware that's ~300 ms after caches warm.
- **step2 dispatch**: single `run_metadata.json` plus the implicit `pipeline_run` record creation (walk step1 dirs, collect run_ids). All sequential, but only once per step2 invocation. Total dispatch-side latency budget: ~500 ms.
- **finalize writes**: per-sample (step1) on subprocess exit; once (step2) on subprocess exit. Atomic via `tempfile + os.replace`. Async-fire-and-forget if write latency on the hot exit path is ever a problem (it shouldn't be — kilobyte JSON write).

This makes the dispatch-side latency O(per-sample-work) bounded by `step1_max_parallel`, not O(total-samples).

### Risk to flag: conda env export

`conda env export -n vsnp3-3.16` is the unknown:
- Sometimes slow on large envs (the vsnp3 env has ~150 packages — mid-range; rough expectation 1–5 s first call).
- Output is non-deterministic in two ways we need to normalize past: (a) `prefix:` line includes the absolute install path, (b) dependency ordering varies between calls.
- The `--no-builds` flag drops build hashes (which is what we want for reproducibility-by-spec), but the resulting yaml is still input-order-sensitive.

**Normalization**: parse the yaml, drop `prefix:`, sort `dependencies:` (split into conda + pip sub-groups, sort each lexicographically, preserve grouping), dump canonical form, hash that. Cache by `(env install path, install path mtime)` in the uvicorn process. Capture cost amortizes to near-zero after the first run.

**If env capture turns out to be slow or stubbornly non-deterministic past the normalization above**, that's where Phase 1 schedule pressure shows up. Fallback: capture only `pip freeze` + `dpkg -l <tools>` + the `vsnp3 --version`, skip the conda env yaml capture for V1, document the gap. Less reproducible but unblocks shipping.

---

## Notes / verifications already done

- Pydantic v2 confirmed in vsnp3 env: `2.13.4`. Reader scaffold imports cleanly.
- Existing audit pattern (`audit/edits.jsonl` with chattr +a) generalizes to the new `runs.sqlite` neighbor.
- Existing `wrap_cmd` already exports `PYTHONWARNINGS`; new env capture will read from the actual subprocess env, not our wrapper, so the captured env will reflect what vsnp3 actually saw.
