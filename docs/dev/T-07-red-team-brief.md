# T-07 Run Provenance — Red-Team Brief

> Self-contained context for an external reviewer. The reviewer is **not**
> expected to have prior knowledge of the codebase, the lab, or vSNP. Goal of
> this review: pressure-test the proposed scope before we build it, surface
> what's missing / gold-plated / unsafe, before code lands. Length target for
> response: a punch list of objections + recommendations, not a full design.

---

## 1. What is this system, and who's affected?

**vSNP** is a phylogenomic SNP-calling pipeline for whole-genome sequencing of
veterinary / One Health pathogens — *M. tuberculosis* complex (incl. *M. bovis*
and other animal-host species), *Brucella spp.*, *M. avium subsp.
paratuberculosis*, SARS-CoV-2.

**vsnp_gui** is a FastAPI + React GUI wrapped around the pipeline, deployed
through Open OnDemand on a single shared lab server (`wgs3`). The current
production user count is 1 (the lab head). Within months, ~5 lab members
plus outside collaborators will be onboarded; another year out, the surveillance
ag/vet community may use the same deployment. So this is a single-server,
multi-user, primarily-trusted-but-growing user base.

**What outputs feed where**: the SNP trees and metadata become input to:
- Lab-internal manuscripts and conference figures.
- Veterinary surveillance reports (USDA, state programs).
- Possibly regulatory submissions where outbreak attribution matters.

A wrong tree that nobody notices for 6 months is a real risk. A *correct* tree
nobody can reproduce 18 months later is also a real risk — referees and
regulators ask.

## 2. The problem we're trying to solve

**Today there is essentially zero structured provenance.** Step 1 (per-sample
read mapping → VCF) and Step 2 (defining-SNP grouping → tree) write their
output files and a small html summary. Nothing on disk reliably answers:

- Which exact reference fasta was this tree built against? (Filename present;
  file content / version / SHA — no.)
- Which version of vsnp3 did this run? Which patches were applied?
- Which VCF databases were included as context samples? Were any toggled off
  for this particular run?
- Were any VCFs edited by hand between Step 1 and Step 2? By whom, when,
  for what reason?
- Which user kicked the run off, from which OOD session?
- What were all the CLI flags?
- What was the exact set of input fastq files (paths *and* sizes / hashes)?

There is one append-only audit file today (`/srv/kapurlab/audit/edits.jsonl`)
that captures one specific class of action (VCF edits inside a project). That's
not enough.

### Concrete scenarios that fail today

1. **"Reproduce this tree from October."** No way to know exactly what went into
   it. We can re-run the pipeline today, but the reference, the VCF DB, the
   vsnp3 version, the user-applied edits may all have changed.
2. **"Why does sample Mg220 cluster differently this month than last month?"**
   Currently we'd have to manually diff filesystem state. Nothing tells us
   *what changed* between the two runs.
3. **"This tree looks wrong — are we sure all 16 samples actually went in?"**
   The tree leaf list is the only record. No prior count, no record of which
   samples might've been excluded.
4. **"A reviewer asks for the exact version of `mtbc0_v1.1` reference."** We
   know the folder name; we don't have a hash, version tag, or upstream
   citation in the run record.
5. **Sample mix-up forensics.** If a fastq pair was misnamed at upload time,
   there's no immutable record of which file went into which sample slot.

## 3. Current architecture (just enough)

- **Filesystem layout** (ignore the parts that aren't relevant):
  ```
  /srv/kapurlab/
    tools/vsnp3/                  shared vsnp3 install (with local patches)
    tools/vsnp_gui/               shared vsnp_gui clone (git web branch)
    refs/vsnp3/
      reference_options/<ref>/    fasta + gbk + define_filter.xlsx (~25 references)
      vcf_db_folders/<ref>/<db>/  shared VCF databases for context samples
    projects/<project>/           shared projects (group-owned, XFS prj quota)
      step1/<sample>/             read mapping outputs, *_zc.vcf
      step2/                      group dirs (one per SNP group), trees
      audit/edits.jsonl           append-only VCF edit log (chattr +a)
    audit/                        cross-project audit logs (currently just t21-*)
  /home/<user>/projects/<project>/  per-user projects (same shape)
  ```
- **Per-user config**: `~/.config/vsnp_gui/config.json` holds `vsnp3_path`,
  `projects_root`, `vcf_db_folders_root`, `disabled_vcf_db_paths`, etc.
- **OOD session model**: each user gets a containerized session with their own
  uvicorn on an allocated port. Backend code is the same shared install.
- **Job execution**: `JobManager` spawns step1/step2 as subprocesses. Each
  sample's step1 invocation gets its own `run_step1.log`. step2 is a single
  vsnp3 invocation that produces all groups.
- **Existing patches** to vsnp3 are tracked at `deploy/vsnp3-patches/` with an
  idempotent `apply.sh`.

## 4. Proposed V1 scope

Two artifacts per pipeline run:

### 4a. Per-run metadata file

Written **at job start** (`status: "running"`, `started_at`) and **finalized
at completion** (`status: "ok"|"failed"`, `finished_at`, `duration_seconds`,
`exit_code`).

- Step 1: one file per sample at `<project>/step1/<sample>/run_metadata.json`
  *and* a roll-up at `<project>/step1/run_metadata.json`.
- Step 2: one file at `<project>/step2/run_metadata.json`.

Proposed schema (JSON, version-tagged):

```jsonc
{
  "schema_version": 1,
  "step": "step1" | "step2",
  "run_id": "<uuid4>",
  "started_at": "2026-05-09T20:17:07Z",
  "finished_at": "2026-05-09T20:27:08Z",
  "duration_seconds": 601.99,
  "status": "ok" | "running" | "failed",
  "exit_code": 0,

  "actor": {
    "user": "vxk1",
    "hostname": "kapurlab-wgs3",
    "ood_session_id": "<batch_connect uuid>",
    "client_ip": "100.88.108.53"
  },

  "vsnp_gui": {
    "git_sha": "79aae04...",
    "git_branch": "web",
    "git_dirty": false,
    "deploy_path": "/srv/kapurlab/tools/vsnp_gui"
  },

  "vsnp3": {
    "version": "3.16",
    "install_path": "/srv/kapurlab/tools/vsnp3",
    "applied_patches": ["v3.16-kapurlab.patch:column_iloc",
                         "v3.16-kapurlab.patch:VSNP3_BOOTSTRAP",
                         "v3.16-kapurlab.patch:syntaxwarning_step1",
                         "v3.16-kapurlab.patch:syntaxwarning_step2"]
  },

  "reference": {
    "name": "mtbc0_v1.1",
    "path": "/srv/kapurlab/refs/vsnp3/reference_options/mtbc0_v1.1",
    "fasta_sha256": "abcd1234...",
    "fasta_size": 4491269
  },

  "inputs": [
    {
      "role": "fastq",
      "sample": "Mg220",
      "filename": "Mg220_R1.fastq.gz",
      "abs_path": "/home/vxk1/projects/nagalingam_test/download/Mg220_R1.fastq.gz",
      "size_bytes": 345533011,
      "sha256": "<computed if size <= threshold; else null>",
      "mtime": "2026-05-09T15:31:06Z"
    }
    // ...
  ],

  // step2-only
  "vcf_db_selections": [
    {
      "path": "/srv/kapurlab/refs/vsnp3/vcf_db_folders/mtbc0_v1.1/representative",
      "scope": "shared",
      "enabled": true,
      "sample_count": 57
    }
    // shared dbs the user explicitly opted out of are listed with enabled:false
  ],
  "edited_samples_at_run_time": ["Mg220"],   // step1-edits in effect

  "cli": {
    "command": "vsnp3_step2.py -wd ... -a -t mtbc0_v1.1",
    "flags": ["-a"],
    "env_vars": {
      "PATH": "/srv/kapurlab/tools/vsnp3/bin:...",
      "VSNP3_BOOTSTRAP": "0",
      "PYTHONWARNINGS": "ignore::SyntaxWarning,ignore::DeprecationWarning:markupsafe"
    }
  },

  "outputs": [
    {"path": "step2/Lineage-04/Lineage-04_2026-05-09_20-18-46_labeled.tre",
     "size_bytes": 387, "sha256": "..."}
    // ...
  ],

  "qc": {
    "samples_excluded": ["Mg220"],   // from remove_from_analysis.xlsx
    "exclude_source": "step2/remove_from_analysis.xlsx"
  }
}
```

### 4b. Append-only cross-project audit log

`/srv/kapurlab/audit/runs.jsonl` — one line per finalized run, summary subset
of the per-run JSON. Set `chattr +a` so it can be appended to but not
rewritten in place. Same pattern as the existing
`/srv/kapurlab/projects/<p>/audit/edits.jsonl`.

This is the queryable surface for "show me every step2 run against
mtbc0_v1.1 in the last 90 days" without scanning per-project trees.

### 4c. Write semantics

- Backend writes `run_metadata.json` with `status: "running"` synchronously at
  job dispatch, *before* the subprocess is launched. If this fails, the run
  doesn't start.
- On subprocess exit (success or failure), the same file is rewritten with
  finalized fields. Write is atomic (`tempfile + os.replace`).
- A separate finalizer writes the line to the cross-project log.
- Hash strategy: SHA-256 only for inputs ≤ 1 GB (proposed default); larger
  files get `sha256: null` and we accept that in V1.

## 5. Explicit non-goals for V1

- No web UI surfacing (provenance is on disk; viewing comes V2).
- No re-execution / "replay this run" button. (V2 / V3.)
- No PROV-O / W3C provenance export. JSON Lines is the surface; converting
  later is fine.
- No run-to-run diff tool. Possible later as a separate ticket.
- No sample-mix-up detection (e.g. fingerprint matching across runs). Out of
  scope; depends on a separate sample-store ticket (T-12b).
- No edit history beyond the existing `audit/edits.jsonl` for in-GUI VCF edits.

## 6. Open design questions we want pushback on

1. **Hash strategy.** Hashing all inputs SHA-256 is slow on multi-GB fastq.gz
   (~30 s extra at step1 dispatch per sample on this hardware). Threshold-based
   skipping (proposed: only hash files ≤ 1 GB) means the rich-data inputs that
   most need integrity are exactly the ones we don't hash. Alternatives:
   record only `(size, mtime, abs_path)` and treat that as identity? Hash in
   background after the run? Hash *output* only?

2. **Schema evolution.** `schema_version: 1` is in the document, but we have
   no plan for what V2 looks like or how readers handle multiple versions.
   Is there a standard people use here, or does each project reinvent it?

3. **Failure semantics for metadata write.** Hard fail at dispatch (run won't
   start)? Soft fail with a warning logged? If the *finalize* write fails,
   the run record is left as `status: "running"` forever — how do we
   garbage-collect those?

4. **Append-only ledger ergonomics.** `chattr +a` works but is annoying for
   admin maintenance (you have to drop and re-set on bulk operations — we
   already learned this with `kapurlab-setup-project.sh`). Is there a less
   draconian audit pattern that still resists tampering by a logged-in user?

5. **Project moves and renames.** If a project is renamed in the GUI, do we
   rewrite the historical run_metadata files? Leave them with the old name?
   The cross-project log entries — do they reference by current name or
   immutable run_id?

6. **PII / sensitive info in env capture.** PATH and other env vars get
   captured verbatim. Likely fine for this lab (no secrets in PATH), but if a
   user has e.g. AWS keys in `AWS_ACCESS_KEY_ID`, we'd capture that.
   Allow-list approach? Block-list?

7. **Reference fasta hashing.** Computing SHA-256 of a 4 MB fasta on every
   run dispatch is trivial. But if we include hash, we should also handle
   the case where the reference is symlinked or replaced between dispatch
   and execution. Lock?

8. **Multi-user race conditions on shared projects.** Two users could kick
   off step2 against the same shared project simultaneously. Do we serialize?
   Allow concurrent and just write two metadata files? Detect and warn?

9. **VCF DB selection capture for V2 of VCF DBs.** Today the per-user
   `disabled_vcf_db_paths` is read at run time. If the user toggles a shared
   DB off after kicking off the run, the metadata still reflects "as enabled
   at dispatch." Do we want the metadata to be queryable in a way that
   distinguishes "DB was opted out" vs "DB didn't exist yet for this user"?

10. **Storage growth.** Per-step metadata is ~5–20 KB. Per-sample step1
    metadata × N samples × every re-run could add up. Garbage collection
    policy? Attach to project-level retention rules?

## 7. Red-team prompt

Imagine you're the lab admin 18 months from now. A reviewer of a manuscript
asks "exactly how was Figure 3's tree generated?". You have read access to
the filesystem but you didn't run the analysis. Walk through the proposed
metadata.

- What can't you reconstruct?
- What can you reconstruct but only after non-trivial sleuthing?
- What's gold-plated — captured at every run but never actually used?
- Where's the bus factor (single point of failure / single source of
  truth that's brittle)?
- What's the security or PII concern that the existing team is too
  close to see?
- What would you cut to make V1 actually shippable in a couple of days
  rather than weeks?

Punch list of objections + concrete recommendations preferred over prose.
