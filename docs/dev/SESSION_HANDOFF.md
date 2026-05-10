# Session handover — May 10 2026

Continuation notes for the next Claude session. Read this first, then
`docs/dev/TICKETS.md` for the broader plan.

## TL;DR

**Milestone A is ~95% done.** T-19, T-04, T-11, T-12a all ✅. T-21 deferred
🪝 (Vivek's call — bulk Mac→wgs3 migration superseded by per-project ad-hoc
upload). T-07 Phase 1 is code-complete, integrated into main.py, and
verified end-to-end against a real run (`quick_test_NC_045512_wuhan-hu-1`)
through six rounds of bug-fixing. **What's left: ~65 minutes of focused work
to call Milestone A done** — re-run end-to-end, build Phase 3 ops glue,
update TICKETS.md.

**Then Milestone B starts.** Today already shipped T-05 (real-time SSE
log streaming) as bonus work — the biggest UX pain Vivek hit during the
session. T-09 (QC badges) and T-16 (KapurLab landing page, 3 phases)
remain.

`web` branch HEAD: `f04fa7a` Step 2 import: kill textarea write-back
feedback loop. wgs3 deploy clone in sync. Frontend dist rebuilt.

## State to verify when picking up

```bash
# Branch + deploy clone match origin/web
ssh wgs3 'sudo git -C /srv/kapurlab/tools/vsnp_gui log --oneline -1'
# expect: f04fa7a Step 2 import: kill textarea write-back feedback loop

# Latest dist bundle is built from current source
ssh wgs3 'sudo ls -la /srv/kapurlab/tools/vsnp_gui/frontend/dist/assets/index-*.js'

# Indexer + writer + verifier all import cleanly
ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui/backend && \
    PYTHONPATH=. /srv/kapurlab/tools/vsnp3/bin/python -c \
    "from app.main import step1_run, step2_run; \
     from app import provenance_writer; \
     from app.vsnp_provenance.index import Indexer; \
     print(\"all imports OK\")"'

# Component-level smoke tests should still pass
ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui/backend/app && \
    /srv/kapurlab/tools/vsnp3/bin/python test_provenance_indexer.py 2>&1 | tail -3 && \
    /srv/kapurlab/tools/vsnp3/bin/python test_provenance_writer.py 2>&1 | tail -3 && \
    /srv/kapurlab/tools/vsnp3/bin/python test_jobs_callback.py 2>&1 | tail -3 && \
    /srv/kapurlab/tools/vsnp3/bin/python test_step1_sse_smoke.py 2>&1 | tail -3'
```

## What landed this session (May 10 chronological highlights)

**Foundational T-07 work** (the headline):
- Reader package at `backend/app/vsnp_provenance/{__init__.py, index.py}` — Opus-drafted, 30/30 indexer assertions pass.
- Writer module at `backend/app/provenance_writer.py` — Opus-drafted against the writer-context doc, 35+ assertions pass. Captures vsnp_gui git, vsnp3 patches, env snapshot, reference folder manifest, edit record refs, VCF DB selections + inventory, all under a frozen `dispatch_state` sub-block per the locked design.
- JobManager finalize_callback at `backend/app/jobs.py` — 17/17 assertions including soft-fail-with-metadata_failures.jsonl.
- main.py wiring: `step1_run` and `step2_run` call `provenance_writer.dispatch_*()` before `start_job` and pass a `finalize_callback`. Bash batch script writes `.provenance/{started_at,finished_at,exit_code}` sentinels per sample.
- Verifier CLI at `deploy/admin/verify_provenance.py` — color-coded PASS/WARN/FAIL punch list; cheaper than eyeballing JSON.

**T-07 design + handoff docs** (substantive):
- `docs/dev/T-07-red-team-brief.md` — original ask sent to Opus
- `docs/dev/T-07-red-team-feedback.md` — verbatim schema diff Opus returned
- `docs/dev/T-07-implementation-plan.md` — synthesis with 5 locked decisions
- `docs/dev/T-07-writer-context-for-opus.md` — JobManager + main.py shape doc Opus coded against
- `docs/dev/T-07-jobs-patch.md` — JobManager patch spec (now applied)

**Six bugs caught + fixed during end-to-end testing** (in chronological order of discovery):
1. `_provenance/` dir leaking as an "Unknown" sample — bash batch + step1_status both filter `_`/`.` prefixed dirs now.
2. VCF DB filter not matching reference at build time — frontend now filters `vcfDbFolders` by `importReference`.
3. Step2 reference inheritance — *not reproduced*; screenshot showed the dropdown DID auto-populate, suggesting timing not bug. Logged in commit message; needs reproducer if real.
4. Env capture empty — diagnosed: no conda binary, no pip in env, dpkg-query empty for conda-installed tools. Added `conda-meta/*.json` fallback (synthesized yaml manifest from filenames) + `<install>/bin/` version probes for samtools/bcftools/bwa/raxml etc. Now produces a real env fingerprint.
5. Step1 output scan missed `alignment_<ref>/*` files — switched to recursive `**/*` glob, added `_filtered_hapall_annotated.vcf` pattern.
6. Step2 import textarea write-back feedback loop — auto-discovered DB paths were being written into `importSourcesText` after every build, then re-read on next build, bypassing the reference filter. Killed the write-back; clear textarea on project change.

**T-05 (Milestone B item, done as bonus)**: `/api/jobs/<id>/events` SSE endpoint multiplexes the batch log with every per-sample `run_step1.log`, prefixed `[batch]` / `[<sample>]`. Discovers late-arriving samples mid-stream. 8/8 multiplexer assertions pass.

**Step 2 + tree polish**:
- VCF DB v2: auto-discover via `vcf_db_folders_root`, reference-scoped (`<root>/<ref>/<db>/`), per-user opt-out via `disabled_vcf_db_paths`, dropdown UI with sample counts and `[from-assembly]` markers, `shared` badge.
- mtbc0_v1.1 reference + 4 MTBC VCF DBs (`representative`/`minimum_tree`/`canetti`/`synthetic`) installed at `/srv/kapurlab/refs/vsnp3/`.
- step2_outputs hides unlabeled `.tre` files when a `_labeled.tre` sibling exists (per-group file panel was offering both, leading users to click into bare-accession leaves).
- `vsnp3_step2.py` invocations now run quietly: SyntaxWarning fixes patched at source (`deploy/vsnp3-patches/v3.16-kapurlab.patch` extended), markupsafe DeprecationWarning suppressed via `PYTHONWARNINGS` exported by `wrap_cmd`.

**Step 1 / inputs polish**:
- Upload UI: explicit "Choose Files" button (replaces flaky bare `<input type="file">`), XHR with real progress display (% / MB / s / MB/s), Cancel button during upload, FileList live-collection bug fix.
- New "Files in download/" panel under the upload dropzone: lists all files in the project's `download/` with sizes, mtimes, per-file `×` delete. Auto-refreshes after upload completes (success or cancel) and after delete. Backed by new `GET/DELETE /api/projects/<p>/inputs[/<filename>]` endpoints.
- "Bring Your Own FASTQ" Choose Folder: was a `window.prompt()` for a path in web mode; now a dropdown of every sibling project's `download/` (with `(shared)` annotation) plus a `Custom path…` escape hatch.

**QC table polish**:
- `qc_exclude` auto-save on toggle (debounced 400 ms) + hydrate on project load. The old "Save Exclusions" button is now "Force-save Exclusions" — kept as a manual-flush + alert affordance.

## Open items, in priority order (Milestone A close-out)

### 1. Re-run end-to-end on quick_test (or any project) and verify ✅

Fresh OOD session → step1 → step2 → run the verifier:

```bash
ssh wgs3 'sudo -u vxk1 -H /srv/kapurlab/tools/vsnp3/bin/python \
  /srv/kapurlab/tools/vsnp_gui/deploy/admin/verify_provenance.py \
  /home/vxk1/projects/quick_test'
```

Expected after the May 10 fix bundle: ~all PASS, no env-block / outputs WARNs (those were what the May 10 fixes targeted). If anything still WARNs or FAILs, file as a regression — the writer + integration are stable as-of `f04fa7a`.

### 2. Phase 3 ops glue (~45 min)

- **Init the indexer DB on wgs3**:
  ```bash
  sudo -u vxk1 -H /srv/kapurlab/tools/vsnp3/bin/python -m vsnp_provenance.index \
      --db /srv/kapurlab/audit/runs.sqlite init
  ```
  (Wrap with PYTHONPATH=/srv/kapurlab/tools/vsnp_gui/backend so the package is findable.)

- **Pre-create env_snapshots dir** with appropriate group ownership so multi-user writes don't race:
  ```bash
  sudo install -d -o root -g kapurlab-admins -m 2775 /srv/kapurlab/audit/env_snapshots
  ```
  (Writer creates it lazily but explicit ownership is operationally cleaner.)

- **Cron jobs at `/etc/cron.d/vsnp_gui-provenance`**:
  ```cron
  # Hourly: mark stuck `running` records as `unknown_terminated` after 48h,
  # both in the index and on disk
  0 * * * * vxk1 /srv/kapurlab/tools/vsnp3/bin/python -m vsnp_provenance.index \
      --db /srv/kapurlab/audit/runs.sqlite gc --max-hours 48 \
      --projects-root /srv/kapurlab/projects > /dev/null 2>&1

  # Nightly at 02:00: crawl all projects to pick up any records the writer
  # didn't index inline (failed finalize_callback fallback), then export
  # to JSONL for archive grep-ability and offsite backup
  0 2 * * * vxk1 /srv/kapurlab/tools/vsnp3/bin/python -m vsnp_provenance.index \
      --db /srv/kapurlab/audit/runs.sqlite crawl /srv/kapurlab/projects > /dev/null 2>&1
  5 2 * * * vxk1 /srv/kapurlab/tools/vsnp3/bin/python -m vsnp_provenance.index \
      --db /srv/kapurlab/audit/runs.sqlite export \
      --out /srv/kapurlab/audit/runs.sqlite.jsonl > /dev/null 2>&1
  ```
  (Test each command standalone first before installing cron.)

- **`kapurlab-rename-project` admin script** at `deploy/admin/`:
  ```bash
  #!/bin/bash
  set -euo pipefail
  OLD="$1"; NEW="$2"
  PROJECTS_ROOT=${PROJECTS_ROOT:-/srv/kapurlab/projects}
  mv "$PROJECTS_ROOT/$OLD" "$PROJECTS_ROOT/$NEW"
  /srv/kapurlab/tools/vsnp3/bin/python -m vsnp_provenance.index \
      --db /srv/kapurlab/audit/runs.sqlite rename \
      --old "$PROJECTS_ROOT/$OLD" --new "$PROJECTS_ROOT/$NEW" --by "$(whoami)"
  echo "renamed $OLD -> $NEW; indexer updated"
  ```
  Without this, any future `mv` of a project would silently corrupt the indexer's path references.

### 3. TICKETS.md update (~5 min)

Mark T-07 ✅ in the chronological list and the Milestone A section. Note that T-22 (server-pull ingestion), T-23 (db.json metadata), T-24 (synthetic-DB default-off), T-25 (upstream vsnp3 PRs), T-26 (OOD upload size doc) remain as 🪝 follow-ups.

After (1)+(2)+(3): Milestone A done. End-of-Milestone-A definition (per
TICKETS.md): *"Tod logs in, runs vSNP on a real project, output is
reproducible."* Will be honestly true after Phase 3.

## Open items, in priority order (Milestone B)

Per current TICKETS.md, Milestone B is "Lab-friendly experience (polish + onboarding)":

### T-05 Real-time Step 1 log streaming — ✅ done in this session

`/api/jobs/<id>/events` multiplexes batch + per-sample logs with `[batch]` / `[<sample>]` prefixes. Frontend renders the unified stream. A future polish iteration could parse the prefix and render per-sample collapsible panels — noted as separate ticket if anyone asks.

### T-09 Sample QC badges — ⏳

Pass / review / fail badges in the Step 1 sample table based on configurable thresholds (coverage, mapping rate, contamination flag from sourmash). Needs:
- Threshold defaults stored where? (cfg file probably)
- Per-sample QC computation source (the existing `qc_summary` endpoint already returns the metrics; just need threshold logic + UI badges)
- The QC table just got auto-save / hydrate work; touching it again should integrate cleanly without conflicts.

Smaller than T-16; do first.

### T-16 KapurLab landing page — ⏳ (3 phases)

Visual rebuild of the OOD dashboard per the layout mockup (`kapurlab_landing_mockup_v2.html` layout A2 — three-pane: Data | Pipelines + Active work | System).

- **Phase 1**: announcement frontmatter fix (the leaking `type: info`), brand bg/title polish, locale overrides, footer cleanup. Pure cosmetics + bug. ~2 h.
- **Phase 2**: custom `welcome.html.erb` partial rendering A2 layout with mocked data (read from `wgs_pipelines.yml`). Validates the OOD Rails view-override path before backend wiring. ~½ day.
- **Phase 3**: live data — group filtering on `/srv/kapurlab/projects/`, real `df`/`/proc/loadavg`, jobs from vsnp_gui's `JobManager` (no Slurm on this box), composite status pill. ~1 day.

Replaces the standalone "Open vSNP GUI" button as the home. Carries the foundation for adding kraken/MHC entries.

## Live system state (May 10 EOD)

- **Branch**: `web` at `f04fa7a` — both `origin/web` and the wgs3 deploy clone (`/srv/kapurlab/tools/vsnp_gui/`).
- **Frontend dist**: rebuilt from `f04fa7a` source. Bundle hash visible at `/srv/kapurlab/tools/vsnp_gui/frontend/dist/assets/index-*.js`.
- **Backend uvicorn**: any *running* OOD session has the OLD code in memory (uvicorn doesn't auto-reload). Backend changes only take effect on next session start.
- **OOD apps**: only `vSNP GUI`. Pinned, "Bioinformatics" category.
- **Users**: `vxk1` (admin), `ro_test` (project member, in `proj-sanity_test`). Other accounts in `/home/` are pre-existing system users — not lab users.
- **Test projects on wgs3**:
  - `/home/vxk1/projects/nagalingam_test` (MTBC, 16 samples, fully run through step1+step2 with the OLD code)
  - `/home/vxk1/projects/quick_test` (NC_045512 SARS-CoV-2, 7 samples; this was the Milestone A end-to-end test bed — has provenance metadata from the May 10 runs)
  - `/srv/kapurlab/projects/sanity_test` (MTBC SARS-CoV-2 deer samples, smoke-tested earlier)
- **VCF DBs installed at `/srv/kapurlab/refs/vsnp3/vcf_db_folders/mtbc0_v1.1/`**: representative (n=57), minimum_tree (n=17), synthetic (n=16) [from-assembly], canetti (n=1).
- **No NC_045512 VCF DBs installed** — for SARS-CoV-2 step2, only the project's own step1 outputs go in.
- **vsnp3 install**: `/srv/kapurlab/tools/vsnp3/` v3.16. **Patched** with column[0] + VSNP3_BOOTSTRAP + new SyntaxWarning fixes. `apply.sh` is idempotent and uses `patch -N` so partial-applied installs pick up new hunks correctly.
- **`/srv/kapurlab/audit/`**: contains `t21-migration.jsonl` (historical, abandoned T-21 attempt). Phase 3 will add `runs.sqlite`, `env_snapshots/`, `metadata_failures.jsonl` (the last is created lazily by JobManager when a finalize_callback raises).

## Operational recipes

### Sync deploy clone to origin/web (lesson learned)

`sudo git fetch` from root fails because root's `~/.ssh/known_hosts` doesn't trust github. Always run the fetch as `vxk1` (whose ssh setup includes the GitHub host key from the existing self-loopback bootstrap):

```bash
ssh wgs3 'sudo -u vxk1 -H git -C /srv/kapurlab/tools/vsnp_gui fetch origin && \
          sudo git -C /srv/kapurlab/tools/vsnp_gui reset --hard origin/web'
```

After a frontend file change, also rebuild:

```bash
ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui/frontend && sudo -u vxk1 npm run build'
```

### Verify provenance after a step1+step2 run

```bash
ssh wgs3 'sudo -u vxk1 -H /srv/kapurlab/tools/vsnp3/bin/python \
  /srv/kapurlab/tools/vsnp_gui/deploy/admin/verify_provenance.py \
  /home/vxk1/projects/<project>'
```

Color-coded PASS/WARN/FAIL counts. Use `--strict` to exit 1 on any FAIL.

### Run the smoke test suite

All four are sibling-of-test-target style — invoke from `backend/app/`:

```bash
ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui/backend/app && \
  /srv/kapurlab/tools/vsnp3/bin/python test_provenance_indexer.py && \
  /srv/kapurlab/tools/vsnp3/bin/python test_provenance_writer.py && \
  /srv/kapurlab/tools/vsnp3/bin/python test_jobs_callback.py && \
  /srv/kapurlab/tools/vsnp3/bin/python test_step1_sse_smoke.py'
```

### Pydantic version + Python version

vsnp3 env runs Python 3.14.4, pydantic 2.13.4. The `__future__ import annotations` + `int | None` / `list[str]` syntax in writer/reader is fine.

### Backend changes need OOD session restart

uvicorn runs without `--reload`. Any backend change requires a fresh OOD session to take effect. Frontend changes need a hard refresh (Cmd+Shift+R) once dist is rebuilt.

## Files of note (full list)

- **Source of truth**: `docs/dev/MULTIUSER.md` + `docs/dev/TICKETS.md` on `web` branch.
- **Runbooks**: `docs/dev/runbooks/{T-19-storage-layout.md, ood-debugging.md}`.
- **T-07 design corpus**: `docs/dev/T-07-{red-team-brief,red-team-feedback,implementation-plan,jobs-patch,writer-context-for-opus}.md`.
- **Admin scripts**: `deploy/admin/{kapurlab-setup-project,kapurlab-add-user,t21-mac-manifest,t21-mac-migrate,verify_provenance}.{sh,py}`.
- **OOD deploy artifacts**: `deploy/ood/{template,portal,clusters.d}/`.
- **vsnp3 patches**: `deploy/vsnp3-patches/{v3.16-kapurlab.patch,apply.sh,README.md}` — extended with SyntaxWarning fixes; idempotency uses `patch -N`.
- **T-07 reader package**: `backend/app/vsnp_provenance/__init__.py` (reader) + `backend/app/vsnp_provenance/index.py` (SQLite indexer + janitor + CLI).
- **T-07 writer module**: `backend/app/provenance_writer.py` (NOT yet wired anywhere outside main.py's step1_run/step2_run).
- **JobManager**: `backend/app/jobs.py` (with finalize_callback + soft-fail metadata_failures.jsonl).
- **Smoke tests**: `backend/app/test_{provenance_indexer,provenance_writer,jobs_callback,step1_sse_smoke}.py`.
- **Live working tree on wgs3**: `/home/vxk1/vsnp_gui/` (vxk1's dev clone).
- **Live deploy clone on wgs3**: `/srv/kapurlab/tools/vsnp_gui/` (what every user's session runs from).

## What to do first when you pick up

1. Verify the state-checks above pass (~2 min).
2. Decide: finish Milestone A first (recommended; ~65 min) or pivot straight to Milestone B (T-09 is the natural starting point).
3. If finishing Milestone A:
   - Have Vivek run a fresh end-to-end on `quick_test` (or any project), then run the verifier — expect all PASS now that the May 10 fix bundle is deployed.
   - Phase 3 ops glue: init `runs.sqlite`, pre-create `env_snapshots/`, install cron, write `kapurlab-rename-project`. Test each piece standalone before chaining.
   - Mark T-07 ✅ in TICKETS.md.
4. If pivoting to Milestone B:
   - Start with T-09 (QC badges) since it's smaller and the QC table is freshly modified, so context is current.
   - Save T-16 (landing page rebuild) for its own session — bigger lift, different headspace (frontend polish + OOD Rails view overrides), worth focused time.

## Known minor follow-ups (deferred, not blocking)

- **"VCFs in set: N" UI label** is technically misleading after the textarea fix — it shows `total_found` (imported + skipped + mismatched) when only `imported` actually ends up in `vcf_source/`. Cosmetic; the count is now usually correct in practice because the textarea-bleed bug is gone. Could rename to "VCFs considered" or use `imported` as the headline. ~10 LoC.
- **mafft / iqtree binary probes return None** — they're genuinely not installed in this vsnp3 env (only raxml variants for tree building). Not a probe gap.
- **Bug 3 (step2 reference inheritance)** — Vivek reported "reference not inherited from step1" but the screenshot showed the dropdown DID auto-populate to `NC_045512_wuhan-hu-1`. May be a timing issue (importReference loads after the step2 panel renders). Needs a reproducer if it surfaces again.
- **OOD nginx_file_upload_max** at default (10 GB). Confirmed working at 345 MB; haven't stress-tested above 2 GB. Bump or document if anyone hits it.
- **T-22 server-pull ingestion** — for true fire-and-forget bulk fastq, the per-user `/home/<user>/uploads/` drop-dir + scan-and-import flow. Browser uploads die when the tab closes; this is the long-term answer. Overlaps with T-20 staged ingestion.
- **T-23 VCF DB v3** — per-DB `db.json` metadata for friendly display names + citations. Folder names cover V1; add when ambiguity surfaces.
- **T-25 upstream vsnp3 PRs** — file the new SyntaxWarning fixes alongside the existing column[0] (USDA-VS/vSNP3#22) and bootstrap (#23) issues.

That's the lot. Good luck.
