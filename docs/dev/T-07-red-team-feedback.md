# T-07 V1 Schema Diff (against §4a of red-team brief)

> Returned by the red-team review on 2026-05-10. Concrete additions, removals, and changes to the proposed `run_metadata.json` schema, plus a new pipeline-run record that addresses the missing unit-of-provenance.
>
> This file preserves the red-team input verbatim. See [`T-07-implementation-plan.md`](T-07-implementation-plan.md) for the synthesis: which items are adopted as-is, modified, deferred, or pushed back on.

Conventions: `+` added, `-` removed, `~` changed, `!` semantics changed without field rename.

---

## Top-level structure

```jsonc
{
  "schema_version": 2,                         // ~ bumped; V1 of brief had no reader, ship reader with V2 number
  "step": "step1" | "step2",
  "run_id": "<uuid4>",
  "pipeline_run_id": "<uuid4> | null",         // + ties step1 batch and step2 together; see §pipeline_run below
  "parent_run_ids": ["<uuid4>", ...],          // + step2 references the step1 run_ids whose output it consumed

  "started_at": "2026-05-09T20:17:07Z",
  "finished_at": "2026-05-09T20:27:08Z",
  "duration_seconds": 601.99,
  "status": "ok" | "running" | "failed" | "unknown_terminated",  // ~ added unknown_terminated for janitor
  "exit_code": 0,

  "trust_scope": {                             // + explicit, blocks misuse for forensics without reading docs
    "timestamps": "local_ntp",                 //   "local_ntp" | "signed_tsa" (V2)
    "actor_authentication": "ood_session_uuid",//   only as strong as OOD session log retention
    "tamper_resistance": "append_only_advisory", // "advisory" | "merkle_chain" (V2) | "object_lock" (V3)
    "sample_chain_of_custody": "filename_only" //   "filename_only" | "lims_linked" (T-12b)
  },
```

`schema_version` jumps to 2: the brief's V1 had no reader strategy, so we treat this redesign as a fresh start with the reader library shipped alongside. Avoids the V1-with-no-parser trap P flagged.

`pipeline_run_id` and `parent_run_ids` are the missing unit of provenance. See pipeline-run section below.

`trust_scope` is new and load-bearing. Forces honest declaration of what the metadata can and cannot support. R's "two regimes" framing collapses cleanly into a single schema with these toggles.

---

## actor block

```jsonc
  "actor": {
    "user": "vxk1",                            //   asserted by launching process; see trust_scope
    "uid": 12047,                              // + numeric uid as cross-check
    "hostname": "kapurlab-wgs3",
    "ood_session_id": "<batch_connect uuid>",
-   "client_ip": "100.88.108.53"               // - cut; meaningless behind OOD reverse proxy, minor PII
  },
```

Cut `client_ip` per S's and R's analysis. The OOD session UUID is the auth anchor; OOD's own logs resolve session to IP if forensics ever need it.

Add `uid` as a cheap cross-check; if `actor.user` and the resolved uid disagree from the running process, that's a signal worth catching.

---

## vsnp_gui block

```jsonc
  "vsnp_gui": {
-   "git_sha": "79aae04...",                   // ! semantics changed: capture from running process, not deploy path
+   "git_sha": "79aae04...",                   //   from /proc/<uvicorn_pid>/cwd or startup manifest
    "git_branch": "web",
    "git_dirty": false,
    "deploy_path": "/srv/kapurlab/tools/vsnp_gui",
+   "uvicorn_pid": 84412,                      // + so you can verify the source of git_sha post-hoc
+   "uvicorn_started_at": "2026-05-09T08:01:17Z" // + catches stale sessions across deploys
  },
```

Same fix applies to vsnp3 (B's Round 2 addition):

```jsonc
  "vsnp3": {
    "version": "3.16",
    "install_path": "/srv/kapurlab/tools/vsnp3",
+   "subprocess_pid": 91204,                   // + captured at dispatch
+   "subprocess_exe_realpath": "/srv/kapurlab/tools/vsnp3/bin/vsnp3_step1.py",  // + resolved via /proc
-   "applied_patches": ["v3.16-kapurlab.patch:column_iloc", ...],  // ~ now objects, not strings
+   "applied_patches": [
+     {
+       "name": "column_iloc",
+       "patch_file": "deploy/vsnp3-patches/v3.16-kapurlab.patch",
+       "patch_sha256": "1c44a8...",           // + so a renamed/edited patch is detectable
+       "applied_at": "2026-04-22T11:03:00Z"
+     }
+     // ...
+   ]
  },
```

P's point: patch *names* are labels that point to files that can change. Hash the patch contents.

---

## environment block (new)

```jsonc
+ "environment": {
+   "conda_env_name": "vsnp3-3.16",
+   "conda_env_yaml_sha256": "9f2c1a...",      // + content hashed
+   "conda_env_yaml_path": "<project>/step1/<sample>/_provenance/conda_env.yaml",  // captured copy
+   "pip_freeze_sha256": "82de00...",
+   "pip_freeze_path": "<project>/step1/<sample>/_provenance/pip_freeze.txt",
+   "system_packages": {                       // + dpkg -l filtered to tools vsnp3 actually shells out to
+     "samtools": "1.17-2",
+     "bcftools": "1.17-3",
+     "bwa": "0.7.17-7",
+     "mafft": "7.520-1",
+     "raxml": "8.2.12+dfsg-4",
+     "iqtree": "2.2.2.7-1"
+   },
+   "python_version": "3.11.6",
+   "platform": "Linux 5.15.0-105-generic x86_64"
+ },
```

This is the single biggest gap P identified. Without it, the metadata cannot reconstruct a run; it can only check whether re-running with the install-as-it-stands-today produces the same answer. Capturing conda env yaml plus pip freeze plus dpkg state for the specific tools vsnp3 invokes is the V1 path. Containerization is the V2 path that subsumes most of this.

The yaml and pip freeze files are written to `_provenance/` next to the run metadata; the JSON stores the hash plus a relative path. This keeps the JSON small and makes the env files inspectable.

---

## reference block

```jsonc
  "reference": {
    "name": "mtbc0_v1.1",
    "path": "/srv/kapurlab/refs/vsnp3/reference_options/mtbc0_v1.1",
-   "fasta_sha256": "abcd1234...",
-   "fasta_size": 4491269,
+   "folder_manifest_sha256": "ee01...",       // + recursive manifest hash; format below
+   "files": [
+     {"relpath": "NC_002945.4.fasta", "sha256": "abcd1234...", "size": 4491269},
+     {"relpath": "NC_002945.4.gbk",   "sha256": "5571ab...",   "size": 5128401},
+     {"relpath": "define_filter.xlsx","sha256": "9091cd...",   "size": 31872}
+   ],
+   "resolved_via_symlink": false              // + true if any path component was a symlink at dispatch
  },
```

P's catch: `define_filter.xlsx` drives SNP grouping. The brief hashed only the fasta. Recursive folder manifest with per-file hash plus a single rolled-up `folder_manifest_sha256` (sorted by relpath, hash of `relpath\0sha256\n` concatenated, SHA-256 of that) gives both granular and quick-compare options.

`resolved_via_symlink` plus the per-file paths address Q7 (reference symlinks/replacements between dispatch and execution): we resolve and snapshot at dispatch.

---

## inputs block

```jsonc
  "inputs": [
    {
      "role": "fastq",
      "sample": "Mg220",
      "filename": "Mg220_R1.fastq.gz",
      "abs_path": "/home/vxk1/projects/nagalingam_test/download/Mg220_R1.fastq.gz",
+     "staged_path": "/srv/kapurlab/projects/<p>/_staging/<run_id>/Mg220_R1.fastq.gz",  // + read-only stage
      "size_bytes": 345533011,
      "sha256": null,                          // ! null when staged; identity is location+immutability
+     "identity_method": "staged_readonly",    // + "sha256" | "staged_readonly" | "size_mtime_path"
      "mtime": "2026-05-09T15:31:06Z"
    }
  ],
```

P's reframe of Q1: hash the small canonical things (reference, env, patches) where collision matters and cost is trivial; for multi-GB fastq, stage to a read-only location at dispatch and let location plus immutability serve as identity. `identity_method` makes the choice explicit per file rather than implicit from a size threshold.

If staging isn't feasible (disk pressure, user veto), fall back to `size_mtime_path` and flag it. Hashing remains an option for deliberate use.

---

## VCF DB selections (step2)

```jsonc
  "vcf_db_selections": [
    {
      "path": "/srv/kapurlab/refs/vsnp3/vcf_db_folders/mtbc0_v1.1/representative",
      "scope": "shared",
      "enabled": true,
      "sample_count": 57,
+     "folder_manifest_sha256": "771a..."      // + so you can detect post-hoc changes to the DB
    }
  ],
+ "vcf_db_inventory_at_dispatch": [           // + R's Round 1 ask: distinguish "user opted out" from "didn't exist yet"
+   {"path": "...", "scope": "shared", "sample_count": 57, "present": true},
+   {"path": "...", "scope": "user",   "sample_count": 12, "present": true}
+ ],
```

Captures both "what the user selected" and "what was on disk for them to select." Without the inventory, you can't later tell whether a missing DB was opted out of or simply didn't exist yet.

---

## edited_samples_at_run_time

```jsonc
- "edited_samples_at_run_time": ["Mg220"],
+ "edited_samples_at_run_time": [
+   {
+     "sample": "Mg220",
+     "edit_record_refs": [                    // + reference specific edits.jsonl entries
+       {
+         "audit_log": "/srv/kapurlab/projects/<p>/audit/edits.jsonl",
+         "line_number": 482,
+         "record_sha256": "3a91cc..."         //   hash of the edit record itself; survives line renumbering
+       }
+     ]
+   }
+ ],
```

R's forensics ask. A sample edited twice has multiple records; "Mg220" alone tells you nothing about which edit state was active. Hashing the edit record makes the link survive log compaction or rewrites that the chattr+a pattern doesn't actually prevent.

---

## cli block

```jsonc
  "cli": {
    "command": "vsnp3_step2.py -wd ... -a -t mtbc0_v1.1",
    "flags": ["-a"],
-   "env_vars": {                              // ~ allow-list, not freeform dict
-     "PATH": "...",
-     "VSNP3_BOOTSTRAP": "0",
-     "PYTHONWARNINGS": "..."
-   }
+   "env_vars": {                              //   allow-listed; expansion is one-line PR
+     "PATH": "/srv/kapurlab/tools/vsnp3/bin:...",
+     "LD_LIBRARY_PATH": null,
+     "CONDA_DEFAULT_ENV": "vsnp3-3.16",
+     "CONDA_PREFIX": "/opt/conda/envs/vsnp3-3.16",
+     "PYTHONPATH": null,
+     "PYTHONWARNINGS": "ignore::SyntaxWarning,...",
+     "VSNP3_BOOTSTRAP": "0",
+     "TMPDIR": "/tmp",
+     "OMP_NUM_THREADS": "8"
+   },
+   "env_capture_policy": "allowlist_v1"       // + so future versions can be diffed; cite the policy file
  },
```

Live disagreement noted in chair synthesis: allow-list for V1, expand as needed. Fields explicitly set to `null` distinguish "not set" from "not captured" (avoids the ambiguity of a missing key).

---

## outputs block

```jsonc
  "outputs": [
    {
      "path": "step2/Lineage-04/Lineage-04_2026-05-09_20-18-46_labeled.tre",
-     "size_bytes": 387,                       // - cut: derived from inputs+toolchain
-     "sha256": "..."                          // - cut: see chair synthesis
+     "exists": true,                          // + minimal: did the output land where expected?
+     "mtime": "2026-05-09T20:27:01Z"
    }
  ],
```

Per chair synthesis: per-output hashes are gold-plating for derived artifacts. Track existence and mtime only. result_hash for trees is deferred to a separate ticket pending canonicalization spec.

---

## qc block

Unchanged from brief.

---

## Pipeline-run record (new file)

The brief treats step1 and step2 as independent units of provenance. This breaks the "reproduce Figure 3" use case, which is fundamentally pipeline-level: a tree was generated by some step2 invocation that consumed the output of some specific set of step1 runs. There is currently no record that ties them together.

Three places this matters:

1. Step2 references the *current contents* of `step1/<sample>/` directories. If a sample was re-run between step2 invocations, the step2 metadata captures the inputs at *its* dispatch but doesn't pin which step1 run produced those inputs. Two different step1 runs could have populated the same files at different times.

2. The "parent_run_ids" field on step2 metadata answers "which step1 runs am I built from" symmetrically: step1 metadata gets a `child_run_ids` field updated when a step2 references it (this requires either rewrite or a separate index).

3. A pipeline-run record at project root gives a single anchor for "this is the run that produced the figure," which is what reviewers and regulators ask about.

Proposed file: `<project>/_provenance/pipeline_runs/<pipeline_run_id>.json`

```jsonc
{
  "schema_version": 2,
  "kind": "pipeline_run",
  "pipeline_run_id": "<uuid4>",
  "created_at": "2026-05-09T20:17:00Z",
  "created_by": "vxk1",
  "label": "nagalingam_2026_q2_figure3",       // optional human label

  "step1_runs": [
    {
      "run_id": "<uuid4>",
      "sample": "Mg220",
      "metadata_path": "step1/Mg220/run_metadata.json",
      "status": "ok",
      "vsnp3_version": "3.16",
      "reference_name": "mtbc0_v1.1",
      "reference_folder_manifest_sha256": "ee01..."
    }
  ],

  "step2_runs": [
    {
      "run_id": "<uuid4>",
      "metadata_path": "step2/run_metadata.json",
      "status": "ok",
      "consumed_step1_run_ids": ["<uuid4>", "<uuid4>", ...],
      "consumed_step1_run_ids_complete": true,  // false if any sample dir lacked a run_metadata.json
      "tree_outputs": ["step2/Lineage-04/...labeled.tre", ...]
    }
  ],

  "consistency": {
    "all_step1_same_reference": true,
    "all_step1_same_vsnp3_version": true,
    "all_step1_same_environment_hash": true,
    "warnings": []
  },

  "trust_scope": { /* mirrors per-run trust_scope */ }
}
```

Two creation patterns:

**Implicit:** Step2 dispatch creates the pipeline_run record by walking `step1/<sample>/` directories, reading their `run_metadata.json`, collecting run_ids, generating `pipeline_run_id`, and writing the consolidated record. Step2's own metadata then carries `pipeline_run_id` and `parent_run_ids`. This is automatic and covers the common case.

**Explicit:** A user wanting to label a pipeline run for a specific manuscript figure can call a `vsnp_provenance pipeline-run --label <name>` CLI to create the record before step2 dispatch, or to retro-create one for a historical run by walking existing metadata files. This handles the "reviewer asks about Figure 3 and the original step2 didn't carry a label" case.

The `consistency` block runs cheap cross-checks: did all step1 runs use the same reference? same vsnp3 version? same environment hash? Mismatches don't block the pipeline-run record (you might *want* to combine runs across reference versions for a comparison) but do raise warnings that surface to the user.

`consumed_step1_run_ids_complete: false` is the failure mode for sample directories that exist but have no metadata, which is the default state for samples produced before T-07 lands. Worth surfacing rather than silently ignoring.

---

## dispatch_metadata.json (new file, per R's Round 2 point)

```jsonc
// <project>/<step>/[<sample>/]dispatch_metadata.json
// Written once at dispatch, never modified.
{
  "schema_version": 2,
  "run_id": "<uuid4>",
  "pipeline_run_id": "<uuid4>",
  "dispatched_at": "2026-05-09T20:17:07Z",
  "dispatch_state": {
    // Full snapshot of: actor, vsnp_gui, vsnp3, reference, inputs, environment, vcf_db_inventory_at_dispatch
    // Same shape as run_metadata.json minus finalize-time fields
  }
}
```

Diffing `dispatch_metadata.json` against the finalized `run_metadata.json` answers "did anything change between dispatch and execution?" This is a forensic primitive that the single-file design loses.

`run_metadata.json` continues to be rewritten at finalize via `tempfile + os.replace`. `dispatch_metadata.json` is write-once; if it already exists at dispatch, the run aborts (run_id collision is a bug worth catching loudly).

---

## Cross-project log (§4b) replacement

Replace `/srv/kapurlab/audit/runs.jsonl` with `/srv/kapurlab/audit/runs.sqlite`:

```sql
CREATE TABLE runs (
  run_id              TEXT PRIMARY KEY,
  pipeline_run_id     TEXT,
  step                TEXT NOT NULL,                -- 'step1' | 'step2'
  project_path        TEXT NOT NULL,
  project_name        TEXT NOT NULL,                -- snapshot at finalize; rename-resilient via run_id
  user                TEXT NOT NULL,
  ood_session_id      TEXT,
  reference_name      TEXT,
  reference_folder_manifest_sha256 TEXT,
  vsnp3_version       TEXT,
  vsnp_gui_git_sha    TEXT,
  environment_hash    TEXT,                         -- hash of conda_env_yaml + pip_freeze + system_packages
  started_at          TEXT NOT NULL,
  finished_at         TEXT,
  duration_seconds    REAL,
  status              TEXT NOT NULL,
  exit_code           INTEGER,
  metadata_path       TEXT NOT NULL,                -- absolute path to run_metadata.json
  schema_version      INTEGER NOT NULL,
  inserted_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX idx_runs_reference   ON runs(reference_name);
CREATE INDEX idx_runs_started     ON runs(started_at);
CREATE INDEX idx_runs_user        ON runs(user);
CREATE INDEX idx_runs_pipeline    ON runs(pipeline_run_id);
CREATE INDEX idx_runs_step_status ON runs(step, status);

CREATE TABLE pipeline_runs (
  pipeline_run_id     TEXT PRIMARY KEY,
  project_path        TEXT NOT NULL,
  project_name        TEXT NOT NULL,
  label               TEXT,
  created_at          TEXT NOT NULL,
  created_by          TEXT NOT NULL,
  metadata_path       TEXT NOT NULL,
  inserted_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE project_renames (              -- audit trail for renames; addresses Q5
  rename_id           INTEGER PRIMARY KEY AUTOINCREMENT,
  old_path            TEXT NOT NULL,
  new_path            TEXT NOT NULL,
  renamed_at          TEXT NOT NULL,
  renamed_by          TEXT NOT NULL
);
```

The cross-project log per the brief was a queryable index pretending to be an audit log. SQLite handles the queryable part properly; the per-project JSONL files (and the dispatch_metadata.json files) remain the immutable record of truth.

A nightly cron exports SQLite to JSONL for archive grep-ability and offsite backup. SQLite is the index; JSONL files plus per-project audit logs are the source of truth.

---

## Janitor cron

`status: "running"` records older than configured `max_runtime_hours` (default 48h) get marked `status: "unknown_terminated"` with `finished_at` set to the cron run time and a note in the metadata. Operators get an email or Slack ping for any record marked this way.

---

## File layout summary

```
<project>/
  _provenance/
    pipeline_runs/
      <pipeline_run_id>.json
  step1/
    <sample>/
      run_metadata.json          # finalized; rewritten at finalize
      dispatch_metadata.json     # write-once at dispatch
      _provenance/
        conda_env.yaml
        pip_freeze.txt
        run_step1.log            # existing
    run_metadata.json            # roll-up; finalized at last sample completion
    dispatch_metadata.json       # roll-up; written at batch dispatch
  step2/
    run_metadata.json
    dispatch_metadata.json
    _provenance/
      conda_env.yaml
      pip_freeze.txt
  audit/
    edits.jsonl                  # existing, unchanged

/srv/kapurlab/audit/
  runs.sqlite                    # cross-project index
  runs.sqlite.jsonl              # nightly export
```
