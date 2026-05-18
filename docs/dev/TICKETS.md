# Ticket Tracking — `web` branch

Source of truth for ticket scope: [`vsnp_gui_architecture_roadmap.docx`](vsnp_gui_architecture_roadmap.docx) (original P0–P2 set) and ad-hoc decisions captured below for the multi-user/multi-app additions.

Multi-user / multi-app architecture (storage, groups, refs, MHC approval, …): see [`MULTIUSER.md`](MULTIUSER.md). That document is the design source of truth — this file is execution status only.

Legend: ✅ done · 🚧 in progress · ⏳ pending · 🪝 follow-up

---

## Done so far (chronological)

- **T-01 Stats & Open Folder via downloads** — ✅ (initial scope: sample-row Open Folder + Stats. Broader sweep of `_open_path`/`xdg-open`-shelling buttons completed 2026-05-17 across two commits — see T-39/T-40 in this list for the full retirement of the legacy desktop-app launcher pattern across Reference Editor xlsx files, Edit Log buttons, Step 2 vcf_source, mismatch report, edited samples list)
- **T-39 Reference-file re-upload route** — ✅ (commit `b7554fd`. `POST /api/references/{ref}/upload-file` with filename whitelist, 10 MB cap, atomic write, old-version archive under `<ref>/.history/`, audit log to `/srv/kapurlab/audit/reference-changes.jsonl`. Frontend "Replace" button next to View/Download. Minimum-viable replace flow; T-17a will layer proposal+admin-review on top later — audit format is forward-compatible.)
- **T-40 Retire dead xdg-open endpoints** — ✅ (commit `50210a7`. Deleted backend `POST /api/projects/{p}/open`, `POST /api/posthoc/open`, `POST /api/references/{ref}/open-file`, the `_open_path()` helper, and the `OpenRequest`/`RefOpenFileRequest` Pydantic models. Frontend callers all migrated in `8d74fe1` and `7c7351d`.)
- **Edit Log JSONL inline view** — ✅ (commit `50210a7`. `.jsonl`/`.ndjson` added to the project download-file MIME map as `text/plain`; defensive fallback added so any inline view of an unknown suffix renders as text rather than triggering an octet-stream download. Frontend `fileViewMode()` regex also updated.)
- **T-02 igv.js replaces desktop IGV** — ✅
- **T-02 follow-up: retire desktop IGV backend** — ✅
- **T-02 follow-up: drop OOD virtual desktop (Xvfb/x11vnc/websockify) + noVNC backend proxy** — ✅
- **T-03 Browser tree viewer (phylotree.js)** — ✅
- **T-03 follow-up: phantom "root" suppression + strip _zc.vcf toggle** — ✅
- **T-03 follow-up: retire FigTree backend launcher** — ✅
- **Portal cosmetic pass: "Kapur Lab Pipelines" title + welcome banner + pinned apps** — ✅
- **Step 2 bootstrap support (`VSNP3_BOOTSTRAP` env var + RAxML `-f a`)** — ✅
- **deploy/ood/ tracked in repo (app + portal config)** — ✅
- **T-19 Storage layout** — ✅ (`/home` on nvme2n1, `/srv/kapurlab` on sda, `/srv/kapurlab/backup` on sdb, swap on nvme1n1)
- **T-04 Remove hardcoded user paths** — ✅ (per-user `~/.config/vsnp_gui/config.json`)
- **T-11 Shared refs at `/srv/kapurlab/refs/`** — ✅ (vsnp3 install at `/srv/kapurlab/tools/vsnp3`, refs at `/srv/kapurlab/refs/vsnp3/reference_options/`, multi-user vsnp_gui clone at `/srv/kapurlab/tools/vsnp_gui`)
- **T-12a Multi-user projects (symlink-based)** — ✅ (groups, `kapurlab-setup-project.sh`, `kapurlab-add-user.sh`, `shared_projects_root`, "shared" badge)
- **mtbc0_v1.1 reference install** — ✅ (copied from Mac into `/srv/kapurlab/refs/vsnp3/reference_options/`)
- **VCF DB v2: auto-discovered, reference-scoped, per-user opt-out, dropdown UI** — ✅ (2-level layout `<root>/<reference>/<db_name>/`, `vcf_db_folders_root` config, `disabled_vcf_db_paths` for per-user opt-out, frontend dropdown trigger with name + sample count + scope badge + `[from-assembly]` marker)
- **MTBC VCF DBs installed at `/srv/kapurlab/refs/vsnp3/vcf_db_folders/mtbc0_v1.1/`** — ✅ (`representative` n=57, `minimum_tree` n=17, `synthetic` n=16, `canetti` n=1)
- **GUI upload UX: explicit Choose Files button + XHR progress** — ✅ (replaces flaky bare `<input type="file">`, real per-byte progress with elapsed time and MB/s, fixes FileList live-collection bug)
- **vsnp3 SyntaxWarning fixes (step1.py, step2.py)** — ✅ (patched in `deploy/vsnp3-patches/v3.16-kapurlab.patch`; `apply.sh` now handles partial-state via `patch -N`)
- **vsnp3 markupsafe DeprecationWarning suppression** — ✅ (`PYTHONWARNINGS` exported via `wrap_cmd`, scoped to `markupsafe` module so other DeprecationWarnings still surface)
- **T-05 Real-time Step 1 log streaming via SSE** — ✅ (`/api/jobs/<id>/events` now multiplexes batch log + per-sample `run_step1.log` files for step1 jobs, prefixing lines with `[batch]`/`[<sample>]`; discovers late-arriving samples mid-stream; smoke test in `backend/app/test_step1_sse_smoke.py` covers the multiplex behavior)
- **T-09 Sample QC badges** — ✅ (three-tier pass/review/fail chip in Step 1 + post-hoc tables, computed backend-side from `*_stats.xlsx` against thresholds in `config.DEFAULTS["qc_thresholds"]`; merge order is module defaults < user config < project.json override; reasons surfaced on hover)
- **T-16 KapurLab landing page** — ✅ (custom `dashboard/index.html.erb` override at `/etc/ood/config/apps/dashboard/views/`; A2 three-pane layout Data | Pipelines + Active work | System; Phase 3 live data sources cpu/mem/storage from `/proc` + `df`, active sessions from `BatchConnect::Session.all`, data list from group-aware filesystem walk, account role from group membership; `wgs_pipelines.yml` carries the declarative pipeline cards + footer + onboarding)
- **T-07 Run provenance** — ✅ (per-step `run_metadata.json` with dispatch→finalize drift detection, vsnp_gui git + vsnp3 patch + env snapshot + reference manifest + inputs + outputs all hashed; writer at `backend/app/provenance_writer.py`, reader/indexer at `backend/app/vsnp_provenance/`; JobManager `finalize_callback` indexes inline with soft-fail to `metadata_failures.jsonl`; SQLite indexer at `/srv/kapurlab/audit/runs.sqlite` with hourly gc + nightly crawl/export cron at `/etc/cron.d/vsnp_gui-provenance`; `deploy/admin/{verify_provenance.py,kapurlab-rename-project.sh,provenance-cron.sh,vsnp_gui-provenance.cron}`; verified end-to-end on `/home/vxk1/projects/quick2` — 89 PASS, 0 WARN)

Side fixes shipped along the way: `API_BASE` localhost fallback, `step2_setup` manifest-write bug, reference alias map last-writer-wins, `step1_vcfs` count fix, vsnp3 column[0] pandas-2 patch, IGV Google OAuth nag.

---

## Milestone A — wgs3 multi-user-ready (foundation)

The unblocker tier. Until A is done, only `vxk1` can use the system. Everything in B/C depends on this.

### T-19 Storage layout — ✅

Mount the four idle disks (~21 TB) and migrate `/home`. End state:
- `/home` on nvme2n1 (3.7 TB NVMe, XFS w/ usrquota+prjquota)
- `/srv/kapurlab` on sda (10.9 TB HDD, XFS w/ prjquota)
- `/srv/kapurlab/backup` on sdb (5.5 TB HDD, XFS w/ prjquota)
- swap on nvme1n1 (953 GB, replaces /swapfile)

Runbook: [`runbooks/T-19-storage-layout.md`](runbooks/T-19-storage-layout.md). ~60–90 min downtime window. Schedule when nobody else is using OOD.

**Prerequisite for T-04 / T-11 / T-12a / T-21.**

### T-04 Remove hardcoded user paths — ✅

`/home/vxk1/` literals scattered through `backend/app/main.py`, `backend/app/config.py`, the OOD `script.sh.erb` template, the bootstrap script, and a handful of helper functions. Each becomes config-driven (read `$HOME` or a config key). Trivial individually; tedious in aggregate. Must land before any user other than vxk1 logs in.

### T-11 Shared refs at `/srv/kapurlab/refs/` — ✅

- Move `/home/vxk1/vSNP_reference_options/` → `/srv/kapurlab/refs/vsnp3/reference_options/`.
- Install vsnp3 once into `/srv/kapurlab/tools/vsnp3/`. PATH points at `<install>/bin/`.
- `vsnp3_path_adder.py -d /srv/kapurlab/refs/vsnp3/reference_options` writes to the install's shared `reference_options_paths.txt`.
- Set group ownership: `:kapurlab-members 0755`. `kapurlab-admins` rwx via ACL or sudo.
- vsnp_gui's `vcf_db_folders` config field accepts `/srv/kapurlab/refs/vsnp3/vcf_db_folders/*`.

### T-12a Multi-user projects (symlink-based) — ✅

- Create groups: `kapurlab-members`, `kapurlab-admins`.
- Add admin script `/usr/local/sbin/kapurlab-setup-project.sh <project> <user>...` that creates `proj-<name>` group, makes `/srv/kapurlab/projects/<name>/` setgid, adds users, sets XFS prjquota.
- vsnp_gui aware of `/srv/kapurlab/projects/` (config-driven via T-04).
- Cross-project sample sharing via symlinks (deferred flat-store is **T-12b**).

### T-21 Migrate Vivek's Mac Electron projects — 🪝 deferred (superseded by ad-hoc upload)

**Decision (2026-05-09)**: not pursuing the bulk migration. The original projects remain on the Mac; the GUI's per-project upload UX (now reliable: drag-and-drop or Choose Files with XHR progress) covers the actual need — bring projects over to wgs3 selectively, when there's a reason to. Re-running Step 1+2 from fastq is fast (~10 min for the 16-sample MTBC test). The bulk migration optimized for a use case (one-shot move of *all* Mac projects to wgs3) that isn't actually needed.

**Status of artifacts**:
- Manifest script (`deploy/admin/t21-mac-manifest.py`) and migration script (`deploy/admin/t21-mac-migrate.py --fastq-only`) shipped, but committed without the openrsync-compat fixes that lived only in `/tmp/t21-mac-migrate.py` on the Mac. Those fixes are also lost-by-default unless someone resurrects this work.
- One mid-flight live attempt was killed; partial state (`download/` contents + project shells under `/srv/kapurlab/projects/`) was cleaned up; only `sanity_test` remains there.

**Bugs uncovered during the live run** (kept here so future-you isn't surprised if T-21 is ever resurrected):

1. **Symlink dereferencing.** Script uses `rsync -rltDvh …`; the `-l` preserves symlinks as symlinks, but several Mac source dirs are symlink-only (e.g. `M5_test/download/` is 100% symlinks into `Nagalingam_03242026`; `Nagalingam_02272026/` mixes real fastq with Dropbox-aliased samples). Result: dest dirs filled with broken symlinks pointing at Mac paths that don't exist on Linux. Fix: add `-L` (or `--copy-links`).

2. **`project.json` overwriting.** `--fastq-only` doesn't filter `project.json`, so rsync stomps the clean version `kapurlab-setup-project.sh` writes with the Mac-side `display_name` (e.g. "Linglnig_mtbc0_v1.1"). The GUI then shows confusing legacy display names. Fix: exclude `project.json` from rsync, or re-write it after rsync from setup-project's template.

**Open semantic question** — for projects that on the Mac are pure aliases of another project (M5_test → Nagalingam_03242026), would we (a) migrate as real data via `-L`, (b) drop from the manifest, or (c) recreate the alias on Linux via symlinks under `/srv/kapurlab/projects/`? Permanent question now that the migration is deferred — re-decide if T-21 is ever reopened.

### T-07 Run provenance (`run_metadata.json`) — ✅ (was P1, promoted to P0)

Captures, per Step 1 / Step 2 invocation, everything needed to reproduce a run:
- vsnp_gui git sha + branch + dirty flag
- vsnp3 version + applied-patches list (sha-checked against `deploy/vsnp3-patches/`)
- environment fingerprint: `conda_env.yaml` from `conda-meta/*.json` synthesis, pip freeze where present, `<install>/bin/` version probes for samtools/bcftools/bwa/raxml/mafft/iqtree, hashed and deduped under `/srv/kapurlab/audit/env_snapshots/`
- reference folder manifest (every file under the reference dir, sha256-hashed; one rolled-up `folder_manifest_sha256` per record)
- inputs (sha256-identified, including auto-discovered VCFs) and outputs
- step2: `pipeline_run_id` linking to a `_provenance/pipeline_runs/<uuid>.json` graph that names the step1 records consumed; `vcf_db_selections` + `vcf_db_inventory_at_dispatch` snapshot
- `dispatch_state` sub-block frozen at job-start, compared against the finalize block at write time so any silent drift surfaces in the verifier

**Architecture**:
- Reader/schema: `backend/app/vsnp_provenance/{__init__.py,index.py}` (Pydantic models + SQLite indexer + janitor + CLI; 30+ smoke assertions in `test_provenance_indexer.py`).
- Writer: `backend/app/provenance_writer.py` (35+ smoke assertions in `test_provenance_writer.py`).
- JobManager wiring: `backend/app/jobs.py` `finalize_callback` indexes inline; on failure writes a fingerprinted entry to `/srv/kapurlab/audit/metadata_failures.jsonl` instead of corrupting the run record (17/17 assertions in `test_jobs_callback.py`).
- Ops: `runs.sqlite` indexer, `/etc/cron.d/vsnp_gui-provenance` (hourly gc, nightly 02:00 crawl, nightly 02:05 JSONL export), all driven by `deploy/admin/provenance-cron.sh`.
- Admin tools: `deploy/admin/verify_provenance.py` (color punch-list — collapses the empty-VCF-DB case to PASS for refs without curated DBs), `deploy/admin/kapurlab-rename-project.sh` (atomic mv + indexer rename to keep paths consistent).

**Verified end-to-end** on `/home/vxk1/projects/quick2` (SARS-CoV-2 NC_045512, 7 samples, step1+step2 round-trip): 89 PASS, 0 WARN, 0 FAIL.

**Design corpus** (kept as historical record): `docs/dev/T-07-{red-team-brief,red-team-feedback,implementation-plan,jobs-patch,writer-context-for-opus}.md`.

**End of Milestone A: Tod logs in, runs vSNP on a real project, output is reproducible.** ✅ — true as of 2026-05-10.

---

## Milestone B — Lab-friendly experience (polish + onboarding)

### T-16 KapurLab landing page — ✅

Replaces the OOD-default home (OnDemand logo + generic HPC welcome copy + pinned-apps grid) with a three-pane KapurLab dashboard: **Data | Pipelines + Active work | System**. Brand strip with live host pill + dynamic status pill above; Penn State / WGS3 footer below. Visual system per the v2 mockup — warm cream background, Fraunces serif for headings, IBM Plex Sans for body, IBM Plex Mono for paths and timestamps, terra-cotta accent for CTAs.

**Mechanism**: a Rails view override at `deploy/ood/portal/apps/dashboard/views/dashboard/index.html.erb`, installed at `/etc/ood/config/apps/dashboard/views/dashboard/index.html.erb`. Rails reloads partials on file change (PUN kill required only on the first install). Replaces the OOD-default welcome banner *and* the `dashboard_layout.rows` widget iteration — neither runs.

- **Phase 1** (cosmetics + bug): announcement `type: info` leak fixed earlier in the day; `brand_bg_color` matched to mockup `#1c3754`; `pinned_apps: []` because the override renders its own grid.
- **Phase 2** (Rails override path + mocked layout): partial installed at `/etc/ood/config/apps/dashboard/views/dashboard/index.html.erb`, mocked data sourced from `deploy/ood/portal/wgs_pipelines.yml`. Validated the OOD view-override mechanism before backend wiring.
- **Phase 3** (live data): the partial now computes every section inline. System metrics from `/proc/loadavg` + `/proc/meminfo` + `df -B1 /srv/kapurlab`; active sessions from `BatchConnect::Session.all`; data lists from a group-aware filesystem walk (project visibility gated on `proj-<name>` group membership or `kapurlab-admins`); account role from `kapurlab-admins` / `kapurlab-members` group lookup with project count appended. Brand-strip status pill flips to a beta-coloured "Resource pressure" variant when any system metric crosses its 80–85% warn threshold.

**Declarative bits remaining in `wgs_pipelines.yml`**: pipeline cards (vSNP3 available + Desktop / Kraken / MHC coming-soon), footer copy, onboarding links. Pipeline cards are the right place to evolve as kraken/MHC apps actually land.

The "Open vSNP GUI" standalone button is gone; vSNP3 lives in the Pipelines grid.

### T-09 Sample QC badges — ✅

Three-tier verdict (pass / review / fail) on every Step 1 sample row and post-hoc QC row, computed backend-side from the same `*_stats.xlsx` columns the table already shows. Verdict + reasons + parsed signals ride along on each row as `_qc_verdict`; the frontend renders a colored chip in a new "QC" column with reasons exposed on hover.

**Defaults** (lab-tuned, in `config.DEFAULTS["qc_thresholds"]`):
- Coverage (Average Depth): ≥30× pass / ≥10× review / <10× fail
- Mapping rate (`100 − Unmapped Percent`): ≥90% pass / ≥70% review / <70% fail
- Contamination flag (sourmash output — vsnp3 doesn't emit this today, handled defensively for when it does): any positive value forces verdict to at least `review`

**Threshold layering**: module DEFAULTS < per-user `~/.config/vsnp_gui/config.json` < per-project `project.json["qc_thresholds"]`. The merge is a shallow per-key dict update, so a project that only overrides `coverage.pass_min` still inherits everything else from the user config. There's no UI for editing thresholds yet — admins edit `config.json` / `project.json` by hand. Add a settings panel if/when the hand-edit gets old.

**Verified end-to-end**: real *_stats.xlsx data on `/home/vxk1/projects/quick2` (7/7 pass — clean SARS-CoV-2 deer panel) and `/home/vxk1/projects/nagalingam_test` (11 pass + 2 review for borderline mapping + 2 fail for low depth/mapping — verdicts match what the eye sees in the metrics).

Smoke test: `backend/app/test_qc_verdict.py` covers all three tiers, missing/unparseable fields, escalation order, contamination, and the override merge.

### T-05 Real-time Step 1 log streaming (SSE) — ✅

`/api/jobs/<id>/events` multiplexes the batch log and every per-sample `run_step1.log` for step1 jobs. Each line carries an `[batch] ` or `[<sample>] ` prefix so the unified stream stays parseable. Late-arriving samples (the ones queued behind `step1_max_parallel`) get discovered each poll cycle. Final flush before the `[job:status]` terminator catches anything written between the last poll and process exit.

Behavior unchanged for step2 / SRA / genome-download jobs (no prefix, single-log tail) — backward-compatible with existing log consumers.

Smoke test: `backend/app/test_step1_sse_smoke.py` (8/8 assertions against the deployed env).

Future polish (separate ticket if it ever surfaces): frontend could parse the prefix and render per-sample collapsible panels with live tail per sample, instead of the current unified stream. The unified stream is the V1 win; per-panel UI is the V2 ask.

**End of Milestone B: Lingling / Dev / Dee can be onboarded with project-scoped access and the experience feels intentional, not bolted-on.**

---

## Milestone C — Second app + cross-project flows (scale-out)

The pipelines-package work (T-27 – T-35) is the architectural spine for everything else in this milestone. It implements [`PIPELINES_PACKAGE.md`](PIPELINES_PACKAGE.md) — one `AnalysisPrimitive` contract that vsnp_gui, a re-deployed kraken_gui, and future OOD cards all consume. T-30 / T-31 / T-32 depend on it; T-15 / T-18 / T-13 are orthogonal scaffolding that becomes more valuable once primitives are landing.

### T-27 `pipelines/common/` shared base — ⏳

Shared building blocks for the pipelines package per [`PIPELINES_PACKAGE.md`](PIPELINES_PACKAGE.md): `AnalysisPrimitive` ABC + `PrimitiveResult` / `Badge` / `SampleContext` / `PrimitiveError` dataclasses, `Project` workspace helper (`ensure_assembly`, `record_finding`, `sample_context`, `provenance_dir`), `runners.run_in_conda_env` (single chokepoint for env activation — apptainer swap = one-file change), and thin shims around the existing T-07 provenance writer + T-09 verdict helpers (re-export, do not fork). Initial home: `backend/app/pipelines/`; extract to a standalone `kapurlab-pipelines` repo when a second front-end imports it (design doc §11.1).

**Falsification test before declaring done**: retrofit `backend/app/posthoc/snp_analysis.py` onto `AnalysisPrimitive`. It predates the contract and already works in production — if it doesn't fit cleanly, the contract is wrong, not the prototype.

Blocked on red-team of [`PIPELINES_PACKAGE.md`](PIPELINES_PACKAGE.md). Filed now to surface design questions; freeze scope after red-team.

### T-28 `pipelines/amrfinder.py` — ⏳

First primitive against the T-27 contract. Wraps `ncbi-amrfinderplus 4.2.7` in `~/miniforge3/envs/amrfinder/` (smoke-tested on wgs3 2026-05-12, DB `2026-03-24.1`). Implements all six abstract methods (`run` / `latex` / `excel` / `web` / `badge` / `provenance`) plus the `applicable` classmethod. Runs in generic mode (no `-O`) for *Mammaliicoccus*-like organisms — design doc §5 caveat.

Regression fixture: 8 NivediXXX *M. sciuri* FASTAs at `/home/vxk1/projects/Shivasharanappa_panel/synthetic_from_assembly/fasta/`; expected matrix in design doc §13. Tests assert findings reproduce that matrix byte-for-byte (modulo column order). Depends on T-27.

### T-29 vsnp_gui AMR integration — ⏳

End-to-end consumer of T-27 + T-28 — the proof that the contract works as a real button users press.
1. Post-step1 hook: when step1 finalizes and an assembly FASTA exists (or `Project.ensure_assembly` runs SPAdes/Shovill once), queue AMRFinder in the background; don't block step1 finalize.
2. Manual "Run AMR" button on the step1 result row for the missed-trigger case.
3. Badge column sourced from `AMRFinder.badge()` via a new `/api/projects/{p}/samples/{s}/badges` endpoint that iterates every completed primitive.

This is the first end-to-end test of the pipelines package. Depends on T-27, T-28.

### T-30 kraken_gui re-deploy as OOD batch_connect — ⏳

Drop the electron wrapper. Same FastAPI/React code, new `deploy/ood/kraken/` template alongside vsnp_gui. Imports `pipelines/kraken.py` (ported from `~/kraken/pipeline/bin/` in `kapurlab/kraken_id_parse_gui`) instead of bundling pipeline scripts. Adds a card to the T-16 KapurLab dashboard. Depends on T-27, T-18 (kraken DB layout), T-34; best done after T-15 (`install_app.py` scaffold).

### T-31 standalone AMR OOD card — ⏳

OOD card for users who want AMR-only workflows (no vSNP context). Imports `pipelines/amrfinder.py`; minimal FastAPI/React at `deploy/ood/amr/`. Reads/writes the canonical project tree (`assembly/` + `amr/` + `samples.json`) so vsnp_gui and kraken_gui see its outputs without coordination. Depends on T-27, T-28, T-34.

### T-32 sourmash card + `pipelines/sourmash.py` — ⏳

Port the current ad-hoc sourmash usage out of vsnp3 wrappers into a contract-conformant primitive, then ship a standalone OOD card the way T-31 ships AMR. Project-tree convention: `sourmash/<sample>/`. Depends on T-27.

### T-33 NAHLN_AMR wrappers (MLST / Abricate / SeqSero2) — ⏳

Port USDA-VS's three Nextflow process wrappers ([NAHLN_AMR](https://github.com/USDA-VS/NAHLN_AMR)) into `pipelines/{mlst,abricate,seqsero2}.py`. One conda env each per design doc §7. NAHLN_AMR becomes a vendored reference, not a runtime dependency — we explicitly do not adopt Nextflow as the orchestration layer (§10). Depends on T-27.

### T-34 Cross-card navigation protocol — ⏳

One route handler per OOD card: `GET /open?project=X&sample=Y` (or `?project=X` for project-level landing). Pre-fills the picker; no re-picking when jumping between cards. Cards generate cross-links via `Project.cross_card_url(card, sample)`. Tiny in isolation, but **prerequisite for T-30 / T-31** — the second OOD card is when cross-navigation becomes useful.

### T-35 `samples.json` schema + concurrent-write strategy — ⏳

The shared knowledge base across cards ([`PIPELINES_PACKAGE.md`](PIPELINES_PACKAGE.md) §3 rule 3).
- **Schema**: top-level keys are sample IDs; per-sample dict has `fastqs[]`, `organism`, `host`, `isolation_source`, plus one key per primitive (`step1`, `kraken`, `amr`, `sourmash`, `mlst`, …) holding that primitive's accumulated findings. JSON Schema published at `pipelines/common/schemas/samples.schema.json`.
- **Write strategy**: atomic `tempfile + os.replace`, per-project advisory lockfile (`samples.json.lock`) around the read-modify-write critical section. `Project.record_finding(sample, primitive, finding)` is the sole mutator.
- `Project` validates on read; refuses to write malformed entries.

**Land before the second primitive starts writing into `samples.json`** — half-baked schema with multiple writers is the highest-cost mistake to make in this milestone. T-35 effectively blocks T-29 from going past sample #1.

### T-15 + T-08 (merged) Multi-app deployment template + install script — ⏳

`deploy/install_app.py <app_name>` scaffolds a new OOD app dir under `deploy/ood/<app>/` from a template, with the same uvicorn-on-FastAPI + byte-range serve + lazy-loaded React routes pattern. Folds in T-08 (the per-app install command we run by hand today).

### T-18 Kraken DB layout — ⏳

`/srv/kapurlab/refs/kraken/<db>/` for shared databases. Compatibility check that the installed kraken2 mmaps from there. First post-vSNP app uses this template.

### T-13 Cross-project VCF index — ⏳

Index every `*_zc.vcf` across projects + users (scoped by T-12a) into a queryable surface (`/api/vcfs?ref=…&project=…&user=…`). Lets users build custom Step 2 bundles by picking samples from arbitrary projects.

### T-17 MHC approval chain — ⏳

`pending/` → `<panel>_current/` flow with admin CLI (`kapurlab-mhc review <id>`) and append-only ledger at `/srv/kapurlab/audit/mhc-approvals.jsonl`. See [`MULTIUSER.md`](MULTIUSER.md) for the full design.

### T-17a Reference-file edit + approval chain — ⏳

Reference-file analog of T-17. The vsnp3 reference xlsx files (`*_define_filter.xlsx`, `*_remove_from_analysis.xlsx`) are shared across all users and define analysis correctness — a bad edit silently changes every subsequent run. Builds on the View/Download primitives shipped 2026-05-17 (commit `8d74fe1`).

**Phase 1 (~1 day) — propose + approve queue, no in-browser editor.** Users still edit offline; the *submission* changes:
- Frontend: "Propose Change" button next to View / Download in the Reference Editor.
- Modal: upload edited file + `rationale` (markdown) + `evidence` (free-form: linked project IDs, sample IDs, publications, URLs).
- Backend: `POST /api/references/{ref}/propose-change` writes a proposal to `/srv/kapurlab/refs/vsnp3/reference_options/<ref>/.proposals/<user>/<ts>-<id>/` containing `before.sha256`, `after.xlsx`, `rationale.md`, `evidence.json`.
- New admin page lists pending proposals across all references: semantic diff vs current, rationale, evidence, "projects that last used this reference" (queryable from T-07 runs.sqlite).
- `POST /api/references/proposals/{id}/approve` → atomic `os.replace()` into place + archive old to `<ref>/.history/<ts>/` + append to `/srv/kapurlab/audit/reference-changes.jsonl`.
- `POST /api/references/proposals/{id}/reject` → move to `.rejected/` with reason; kept for audit.

**Permission model.** Approvers: members of `kapurlab-admins`. Self-approve is allowed (lab reality: 1–2 admins) but every approval — including self-approval — records `submitter`, `approver`, and a `self_approved: true` flag in the audit log. Reviewers external to the lab can grep for self-approvals if regulatory questions arise.

**Phase 2 (deferred) — schema-aware in-browser editor.** A React component that knows the xlsx schema (position columns, group blocks, conditional-format-encoded grouping). Edits become semantic ("added position 3,456,789 to TbD1 group") rather than cell-byte changes. Eliminates the download/edit-locally hop for routine adds/removes. Spreadsheet-grid alternative (Univer/AG-Grid) considered and explicitly deferred — schema-aware gives better validation and better admin-review diffs.

**Integration points.**
- T-07: every approve/reject is a `reference-changes.jsonl` entry alongside the existing run-provenance audit stream.
- T-09 / future QC: reference change history exposed via a "this reference was last modified <date> by <user> with rationale '...'" badge in the Reference Editor read view.
- Notification: in-app inbox v1 (poll an unread-proposal count endpoint); email-on-pending later if anyone asks.

**Deferred until red-team decisions settle** ([`redteam/FINDINGS.md`](redteam/FINDINGS.md) UNRESOLVED-1 and UNRESOLVED-2 — T-17a touches the same provenance and locking patterns that the unresolved decisions affect).

### T-20 Staged ingestion flow — ⏳

GUI "Add to project" tab listing `/home/<user>/uploads/*` with a move button (rename(2) when same FS, copy+verify+unlink otherwise). Plus documentation for the sequencer rsync cron.

### T-14 Step 1 cleanup / archival — ⏳

After Step 1, "Archive" button per sample compresses or deletes intermediates (`*_filtered_hapall_annotated.vcf`, `RAxML_*` scratch, `*.reduced`, `unmapped_reads/`) while keeping BAM + `_zc.vcf`. Optional default-on policy. Disk-size deltas tracked in run metadata (T-07).

### T-06 Project export ZIP — ⏳

Bundle a project for off-system handoff. Configurable: with/without raw fastq, with/without intermediates. Writes to `/srv/kapurlab/projects/<name>/exports/` so the user can download or rsync elsewhere.

---

## Milestone D — Later

### T-12b Flat sample-store — ⏳

`/srv/kapurlab/samples/<sample-id>/` as canonical home for fastq/BAM/VCF/edit-log; projects become views. Trigger: when symlinks bite us (provenance gets confusing, deletion gets risky, cross-project queries are clumsy). See [`MULTIUSER.md`](MULTIUSER.md) for the trigger criteria.

### T-10 Docker Compose deployment — ⏳

For non-OOD environments (local dev, demos, eventual public-facing). Unblocks publishing the GUI. Low priority while OOD on wgs3 is the canonical deployment.

---

## Bonus fixes shipped on the `web` branch

These were uncovered while working tickets and weren't separate items, but worth noting so we don't re-debug them:

- **`API_BASE` localhost fallback** — `frontend/src/App.jsx` defaulted to `http://localhost:8000` if `VITE_API_URL` wasn't set; from a browser whose machine had a local uvicorn (typical: dev Mac), every fetch silently hit there instead of through the OOD rnode proxy. Changed to `"."` (relative).
- **`step2_setup` manifest write outside `with` block** — write went to a closed handle. Fixed.
- **Reference alias map: last-writer-wins** — stray `NC_045512.fasta` inside `tb_reference/` was clobbering `NC_045512_wuhan-hu-1`. Now prefers the mapping where the reference name contains the fasta stem.
- **`step1_vcfs` count** — was globbing `**/*.vcf` (counts both `_zc.vcf` and intermediates → 2× actual). Now `_zc.vcf` only.
- **vsnp3 upstream `column[0]` → pandas-2 KeyError** — patched conda env. Filed [USDA-VS/vSNP3#22](https://github.com/USDA-VS/vSNP3/issues/22).
- **vsnp3 has no bootstrap support** — patched conda env to honor `VSNP3_BOOTSTRAP`. Filed [USDA-VS/vSNP3#23](https://github.com/USDA-VS/vSNP3/issues/23).
- **IGV "Google oAuth properties" nag** — set `ENABLE_GOOGLE_MENU=false` in `~/.igv/prefs.properties`. Mostly moot once desktop IGV was retired.
- **Phantom "root" tree label** — phylotree's newick parser wraps the parsed tree in a synthetic outer node named `"root"`; with internal-names on (bootstrap toggle), that label was rendered as a phantom "root" alongside the legit outgroup leaf. Suppressed in node-styler.

---

## Follow-ups noted

🪝 small/medium items that came up during recent work but don't justify their own milestone slot yet:

- **T-22 GUI server-pull ingestion for large fastq.** Browser uploads (either GUI drop-zone or OOD Files) hold up the tab and die if the user closes it; over Tailscale a 5 GB pair is fine, but anything larger or unattended needs a server-side pull. Sketch: per-user `/home/<user>/uploads/` drop-dir + a "Scan inbox" tab in the project view that lists/move-into-project, plus optional rsync-from-Mac cron. Overlaps with **T-20 staged ingestion**; merge them when picked up.
- **T-23 VCF DB v3: per-DB `db.json` metadata.** V2 ships with folder-name + live sample count. V3 adds a tiny `db.json` for friendly display name (e.g. "MTBC representative isolates (Coll et al. 2014)"), short description, citation, kind tag (empirical/synthetic/minimum_tree). Discovery falls back to folder name when missing. Add when we have more than one reference's worth of DBs and bare folder names start being ambiguous.
- **T-24 VCF DB v3 (cont.): per-user opt-out of synthetic DBs by default.** Currently shared DBs default to enabled (including `synthetic`); if usage shows synthetic noise hurts more than it helps for routine runs, flip the default-checked logic for `kind: "synthetic"` so they're available but unchecked.
- **T-25 Upstream vsnp3 PRs.** Two more patches we now carry locally: the SyntaxWarning raw-string fixes in `bin/vsnp3_step1.py` and `bin/vsnp3_step2.py`. File alongside the existing `column[0]` (USDA-VS/vSNP3#22) and bootstrap (#23) issues. Trivial — 4 character changes total.
- **T-26 OOD Files upload size.** `/etc/ood/config/nginx_stage.yml` left at default (10 GB max upload). Confirmed working at 345 MB; haven't stress-tested above 2 GB. Bump or document if anyone hits it.
- **T-36 Post-hoc sample resolver: fuzzy/substring search.** `/api/posthoc/step1/resolve_samples` currently matches only on exact step1 subdirectory name (e.g. typing `200443` fails to find `hCoV-19-deer-USA-IA-200443-2021-EPI_ISL_5804774-2021-01-09`). Placeholder text in the UI says "SRR10321141" which works only for short SRA accessions; long descriptive dir names (deer SARS-CoV-2, MTBC strain labels) are effectively unsearchable. Fix in `backend/app/main.py:2091` — change `candidate = root / sample; if candidate.is_dir()` to also check `any(d.name.contains(sample) for d in root.iterdir() if d.is_dir())`. Return all matches when ambiguous, let UI disambiguate.
- **T-37 Post-hoc sample resolver: search shared root + archives.** Same endpoint hardcodes `Path(cfg["projects_root"]).glob("*/step1")` — ignores `shared_projects_root` and any archive roots. Should reuse `_project_roots(cfg)` (already exists in `main.py`) to iterate both personal + shared. Archives would need a third config key (`archive_projects_root`?) or a convention for archived projects to live under a sibling tree.
- **T-38 Post-hoc folder picker: browser-side picker, not `window.prompt()`.** `addPosthocFolder()` in `frontend/src/App.jsx:1809` uses native folder picker via Electron's `window.vsnp.selectPath`; in the OOD browser it falls back to `window.prompt("Enter Step 1 folder path:")` — user has to know and type the absolute path. Build an inline picker scoped to `projects_root` + `shared_projects_root` (same pattern as the VCF DB folder picker that exists elsewhere). Existing one-click "Current Project" button covers the most common case; this is for cross-project use.
- **T-43 vsnp3 upstream patch: quote paths in seqkit (and any other) subprocess invocations.** Surfaced during the LSDV India Step 1 run (2026-05-17). `vsnp3_fastq_stats_seqkit.py:119` ultimately invokes `seqkit stat <path>` without proper quoting; if the path contains a space (e.g. project named `LSDV India`), the shell splits the path and seqkit reports `[ERRO] stat /home/vxk1/projects/LSDV: no such file or directory`. vsnp3 then tries to parse the error string as a float for `read_quality_average` and fails with `ValueError: could not convert string to float: '\\x1b[31m[ERRO]…'` — every sample dies identically. The kapurlab-side regression has been fixed (project names now auto-convert spaces to underscores at creation time, plus reject other shell-unfriendly chars), so this is no longer urgent — but the upstream bug is real and worth a USDA-VS PR alongside #22 (column[0]) and #23 (bootstrap). Add the fix to `deploy/vsnp3-patches/v3.16-kapurlab.patch` first, then file upstream. Also worth auditing other vsnp3 subprocess calls (bwa, samtools, bcftools) for the same quoting weakness — any of them would produce a similarly cryptic failure on a path with shell metacharacters.
- **T-42 SRA download progress UI + status-writer bug fix.** Surfaced during the LSDV India 41-sample download (2026-05-17). The Downloads box on the project page only shows completed files; in-flight progress is invisible — users have to open the Job Log to see whether 5 or 35 of their batch are done. **Phase 1** (~2 hr): show `<done>/<total>` + a progress bar at the top of the Downloads box, driven off the existing `/api/jobs/<id>/events` SSE stream that already multiplexes per-accession log lines (T-05) — no new backend endpoint needed. **Phase 2** (~half day): per-accession chips showing `queued`/`downloading`/`compressing`/`done`/`failed` with hover-to-see-elapsed; final reconciliation pass at job-end against on-disk fastqs. **Prerequisite — fix the status writer.** Two real bugs surfaced in the same LSDV batch: (a) 3 successfully-downloaded runs (`SRR18028321`, `SRR24474207`, `SRR31397153`) ended with no `.status_<acc>` sentinel despite their fastqs landing correctly; (b) the job log reported 6 accessions as `[FAILED]` but 5 of those actually succeeded (had both paired fastqs AND `ok` sentinels) — looks like a race between the per-accession success path and the final failure-list writer. Without fixing these the progress UI surfaces stale or wrong state, so the writer fix lands before or alongside Phase 1. Likely site: the per-accession completion handler in `backend/app/sra.py` (or wherever the SRS→SRR resolution + download loop lives).

---

## Branching / source of truth

`main` is the canonical branch — OOD/FastAPI rewrite history, daily work on wgs3. The pre-Apr-10 Electron history is preserved on the `main-electron-archive` branch but is **not** the path forward for OOD deployment. The Mac copy (`/Users/vivekkapur/vsnp_gui/`) is a viewer / authoring environment; do not run a local uvicorn there or it will shadow the OOD backend in the browser (see the `API_BASE` note above).

Historical note: the rewrite branch was originally called `web` and got renamed to `main` (with the prior `main` archived). Older session handoffs and commit messages may still reference `web` — substitute `main` mentally.

`t03-spike` retained as the A/B record for phylotree.js vs phylocanvas.gl.
