# Ticket Tracking — `web` branch

Source of truth for ticket scope: [`vsnp_gui_architecture_roadmap.docx`](vsnp_gui_architecture_roadmap.docx) (original P0–P2 set) and ad-hoc decisions captured below for the multi-user/multi-app additions.

Multi-user / multi-app architecture (storage, groups, refs, MHC approval, …): see [`MULTIUSER.md`](MULTIUSER.md). That document is the design source of truth — this file is execution status only.

Legend: ✅ done · 🚧 in progress · ⏳ pending · 🪝 follow-up

---

## Done so far (chronological)

- **T-01 Stats & Open Folder via downloads** — ✅
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

### T-07 Run provenance (`run_metadata.json`) — ⏳ (was P1, promoted to P0)

Must land before any production run. Captures per Step 1 / Step 2 invocation:
- `vsnp3 --version`
- contents of `reference_options_paths.txt`
- all CLI flags
- env vars: PATH, VSNP3_BOOTSTRAP, etc.
- input file paths + sizes (skip SHA on big BAMs for speed)
- `git rev-parse HEAD` of vsnp_gui
- user, hostname, timestamps

Written to `<project>/<step>/run_metadata.json` and mirrored to `/srv/kapurlab/audit/runs.jsonl` (append-only).

**End of Milestone A: Tod logs in, runs vSNP on a real project, output is reproducible.**

---

## Milestone B — Lab-friendly experience (polish + onboarding)

### T-16 KapurLab landing page — ⏳ (3 phases)

Visual rebuild of the OOD dashboard per the layout mockup (`kapurlab_landing_mockup_v2.html` layout A2 — three-pane: Data | Pipelines + Active work | System).

- **Phase 1**: announcement frontmatter fix (the leaking `type: info`), brand bg/title polish, locale overrides, footer cleanup. Pure cosmetics + bug. ~2 h.
- **Phase 2**: custom `welcome.html.erb` partial rendering A2 layout with mocked data (read from `wgs_pipelines.yml`). Validates the OOD Rails view-override path before backend wiring. ~½ day.
- **Phase 3**: live data — group filtering on `/srv/kapurlab/projects/`, real `df`/`/proc/loadavg`, jobs from vsnp_gui's `JobManager` (no Slurm on this box), composite status pill. ~1 day.

Replaces the standalone "Open vSNP GUI" button as the home. Carries the foundation for adding kraken/MHC entries.

### T-09 Sample QC badges — ⏳

Pass / review / fail badges in the Step 1 sample table based on configurable thresholds (coverage, mapping rate, contamination flag from sourmash).

### T-05 Real-time Step 1 log streaming (SSE) — ✅

`/api/jobs/<id>/events` multiplexes the batch log and every per-sample `run_step1.log` for step1 jobs. Each line carries an `[batch] ` or `[<sample>] ` prefix so the unified stream stays parseable. Late-arriving samples (the ones queued behind `step1_max_parallel`) get discovered each poll cycle. Final flush before the `[job:status]` terminator catches anything written between the last poll and process exit.

Behavior unchanged for step2 / SRA / genome-download jobs (no prefix, single-log tail) — backward-compatible with existing log consumers.

Smoke test: `backend/app/test_step1_sse_smoke.py` (8/8 assertions against the deployed env).

Future polish (separate ticket if it ever surfaces): frontend could parse the prefix and render per-sample collapsible panels with live tail per sample, instead of the current unified stream. The unified stream is the V1 win; per-panel UI is the V2 ask.

**End of Milestone B: Lingling / Dev / Dee can be onboarded with project-scoped access and the experience feels intentional, not bolted-on.**

---

## Milestone C — Second app + cross-project flows (scale-out)

### T-15 + T-08 (merged) Multi-app deployment template + install script — ⏳

`deploy/install_app.py <app_name>` scaffolds a new OOD app dir under `deploy/ood/<app>/` from a template, with the same uvicorn-on-FastAPI + byte-range serve + lazy-loaded React routes pattern. Folds in T-08 (the per-app install command we run by hand today).

### T-18 Kraken DB layout — ⏳

`/srv/kapurlab/refs/kraken/<db>/` for shared databases. Compatibility check that the installed kraken2 mmaps from there. First post-vSNP app uses this template.

### T-13 Cross-project VCF index — ⏳

Index every `*_zc.vcf` across projects + users (scoped by T-12a) into a queryable surface (`/api/vcfs?ref=…&project=…&user=…`). Lets users build custom Step 2 bundles by picking samples from arbitrary projects.

### T-17 MHC approval chain — ⏳

`pending/` → `<panel>_current/` flow with admin CLI (`kapurlab-mhc review <id>`) and append-only ledger at `/srv/kapurlab/audit/mhc-approvals.jsonl`. See [`MULTIUSER.md`](MULTIUSER.md) for the full design.

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

---

## Branching / source of truth

`web` is the OOD/FastAPI rewrite branch off `main`. The pre-Apr-10 Electron history on `main` is preserved but is **not** the path forward for OOD deployment. Daily work happens on wgs3 against this branch. The Mac copy (`/Users/vivekkapur/vsnp_gui/`) is a viewer; do not run a local uvicorn there or it will shadow the OOD backend in the browser (see the `API_BASE` note above).

`t03-spike` retained as the A/B record for phylotree.js vs phylocanvas.gl.
