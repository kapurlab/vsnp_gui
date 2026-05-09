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

Side fixes shipped along the way: `API_BASE` localhost fallback, `step2_setup` manifest-write bug, reference alias map last-writer-wins, `step1_vcfs` count fix, vsnp3 column[0] pandas-2 patch, IGV Google OAuth nag.

---

## Milestone A — wgs3 multi-user-ready (foundation)

The unblocker tier. Until A is done, only `vxk1` can use the system. Everything in B/C depends on this.

### T-19 Storage layout — ⏳ (runbook drafted)

Mount the four idle disks (~21 TB) and migrate `/home`. End state:
- `/home` on nvme2n1 (3.7 TB NVMe, XFS w/ usrquota+prjquota)
- `/srv/kapurlab` on sda (10.9 TB HDD, XFS w/ prjquota)
- `/srv/kapurlab/backup` on sdb (5.5 TB HDD, XFS w/ prjquota)
- swap on nvme1n1 (953 GB, replaces /swapfile)

Runbook: [`runbooks/T-19-storage-layout.md`](runbooks/T-19-storage-layout.md). ~60–90 min downtime window. Schedule when nobody else is using OOD.

**Prerequisite for T-04 / T-11 / T-12a / T-21.**

### T-04 Remove hardcoded user paths — ⏳ (was P1, promoted to P0)

`/home/vxk1/` literals scattered through `backend/app/main.py`, `backend/app/config.py`, the OOD `script.sh.erb` template, the bootstrap script, and a handful of helper functions. Each becomes config-driven (read `$HOME` or a config key). Trivial individually; tedious in aggregate. Must land before any user other than vxk1 logs in.

### T-11 Shared refs at `/srv/kapurlab/refs/` — ⏳

- Move `/home/vxk1/vSNP_reference_options/` → `/srv/kapurlab/refs/vsnp3/reference_options/`.
- Install vsnp3 once into `/srv/kapurlab/tools/vsnp3/`. PATH points at `<install>/bin/`.
- `vsnp3_path_adder.py -d /srv/kapurlab/refs/vsnp3/reference_options` writes to the install's shared `reference_options_paths.txt`.
- Set group ownership: `:kapurlab-members 0755`. `kapurlab-admins` rwx via ACL or sudo.
- vsnp_gui's `vcf_db_folders` config field accepts `/srv/kapurlab/refs/vsnp3/vcf_db_folders/*`.

### T-12a Multi-user projects (symlink-based) — ⏳

- Create groups: `kapurlab-members`, `kapurlab-admins`.
- Add admin script `/usr/local/sbin/kapurlab-setup-project.sh <project> <user>...` that creates `proj-<name>` group, makes `/srv/kapurlab/projects/<name>/` setgid, adds users, sets XFS prjquota.
- vsnp_gui aware of `/srv/kapurlab/projects/` (config-driven via T-04).
- Cross-project sample sharing via symlinks (deferred flat-store is **T-12b**).

### T-21 Migrate Vivek's Mac Electron projects — ⏳

One-shot script that walks `/Users/vivekkapur/vsnp3/projects/` (Mac), identifies real projects vs throwaways, rsyncs raw fastq/bam/vcf into the new `/srv/kapurlab/projects/<name>/` shape, and emits a manifest of what moved where.

Pre-req: T-12a done so the destination structure exists.

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

### T-05 Real-time Step 1 log streaming (SSE) — ⏳

Currently Step 1 logs are batched at completion. Stream live via SSE to the GUI so a multi-hour run feels alive.

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

## Branching / source of truth

`web` is the OOD/FastAPI rewrite branch off `main`. The pre-Apr-10 Electron history on `main` is preserved but is **not** the path forward for OOD deployment. Daily work happens on wgs3 against this branch. The Mac copy (`/Users/vivekkapur/vsnp_gui/`) is a viewer; do not run a local uvicorn there or it will shadow the OOD backend in the browser (see the `API_BASE` note above).

`t03-spike` retained as the A/B record for phylotree.js vs phylocanvas.gl.
