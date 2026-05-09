# Ticket Tracking — `web` branch

Source of truth for ticket scope: [`vsnp_gui_architecture_roadmap.docx`](vsnp_gui_architecture_roadmap.docx).
This file tracks **status** as we work the backlog. Update as tickets land.

Legend: ✅ done · 🚧 in progress · ⏳ pending · 🪝 follow-up after a done ticket

## P0

### T-01 — Fix Stats & Open Folder (download-based) — ✅
Landed 2026-05-08.
- Stats button → per-sample XLSX download via `Content-Disposition: attachment`
- Open Folder → modal listing files in the sample dir, with a Download button per file
- Endpoints: `GET /api/projects/{p}/step1/samples/{s}/stats/download`, `GET /api/projects/{p}/step1/samples/{s}/files`
- Frontend: `downloadStep1Stats`, `openStep1FolderModal`, `formatBytes`, modal in `App.jsx`

### T-02 — Replace desktop IGV with igv.js — ✅ (with follow-ups)
Landed 2026-05-08.
- igv.js panel renders inline (bottom drawer, resizable + fullscreen)
- BAM + BAI loaded against the project reference FASTA
- Multi-track stacking on second click (comparison mode)
- Pop-out tab via `?view=igv&project=…&samples=…` (`IgvStandalone.jsx`)
- Subsequent IGV clicks routed to the live popout via `postMessage` (additive)
- Backend: `GET /api/projects/{p}/serve` with HTTP byte-range support (206 + `Content-Range`)
- npm: `igv@^3.8.0`

🪝 Follow-ups:
- [x] Retire desktop IGV: `step1_igv_session`, `posthoc/igv_session`, `_open_igv` and helpers, `igv_app_path` config field, frontend handlers and settings UI.
- [x] Remove Xvfb / x11vnc / websockify from OOD `script.sh.erb` and the FastAPI noVNC WebSocket proxy. Closes T-02 acceptance criterion 6.
- [ ] Posthoc-tab IGV smoke test (needs a 2nd project; sufficient by symlinking `test/step1`).

## P1

### T-03 — Browser tree viewer — ✅ (with follow-ups)

In-browser tree viewer using **phylotree.js**. Picked over phylocanvas.gl after an A/B spike (preserved in branch `t03-spike`).

Layout: **new tab** (not a drawer). Rectangular trees + long tip labels (`hCoV-19-deer-USA-IA-XXXX-EPI_zc.vcf` ~50 chars) benefit more from full viewport than from a constrained drawer; screening workflow doesn't need to keep the project view in context.

What shipped:
- `frontend/src/TreeStandalone.jsx` — full-viewport viewer at `?view=tree&project=…&path=…`. Controls: tip search highlight, Bootstrap labels toggle, Reroot mode (click branch), Midpoint root, Reset, Download `.tre` (handoff to iTOL/FigTree).
- `frontend/src/main.jsx` — lazy-loaded behind `React.lazy` + `Suspense` + `ErrorBoundary` (introduced during the spike to keep the main App alive if a viewer chunk crashes; pattern kept).
- `App.jsx` — `*.tre` rows in Step 2 outputs show a **View tree** button that opens the standalone in a new tab.
- Backend `GET /api/projects/{p}/step2/trees` — lists latest `.tre` per group.
- npm: `phylotree`.

**Bootstrap support shipped at the same time**:
- vsnp3 upstream's RAxML call is hard-coded as best-tree-only. Patched the conda env to read a `VSNP3_BOOTSTRAP` env var; if > 0, RAxML runs `-f a -x 7777 -N <n>` and the pipeline picks up `RAxML_bipartitions.raxml`.
- New "Bootstrap (replicates)" field in Step 2 Options. Default 0 (off). Backend threads it through as the env var.
- Filed upstream: [USDA-VS/vSNP3#23](https://github.com/USDA-VS/vSNP3/issues/23). Same conda-update caveat as the column[0] patch.
- Smoke (test project, 7 SARS-CoV-2 deer samples): 50 replicates → 2.17 s; `.tre` carries support values on all 5 internal branches.

🪝 Follow-ups:
- [x] **Phantom "root"**: suppressed in the `node-styler` — any internal node whose `data.name === "root"` has its label blanked at draw time, regardless of source.
- [x] **Strip `_zc.vcf`** toggle for tip labels (defaults on, off via header checkbox).
- [x] Retire FigTree backend launcher: dropped `figtree_app_path` from `ConfigUpdate`, `get_config`, `update_config`, `config.py` defaults; removed the FigTree branch in `_open_path`; dropped the FigTree settings field from the Settings UI.
- [x] Remove Xvfb / x11vnc / websockify from OOD `script.sh.erb` and drop the FastAPI `/novnc-ws` WebSocket proxy + `/novnc/` static mount. OOD templates now tracked in [`deploy/ood/`](../../deploy/ood/) so the deployment is reviewable. Closes T-02 acceptance criterion 6.

### T-04 — Remove hardcoded user paths (`$HOME`-relative) — ⏳
### T-05 — Real-time Step 1 log streaming (SSE) — ⏳
### T-06 — Project export ZIP download — ⏳
### T-07 — Run provenance (`run_metadata.json`) — ⏳

## P2

### T-08 — Install script (automated OOD deployment) — ⏳
### T-09 — Sample QC badges (pass/review/fail) — ⏳
### T-10 — Docker Compose deployment path — ⏳

---

## Bonus fixes shipped on the `web` branch

These were uncovered while working T-01 / T-02 and weren't separate tickets, but are worth noting so we don't re-debug them:

- **`API_BASE` localhost fallback** — `frontend/src/App.jsx` was defaulting to `http://localhost:8000` if `VITE_API_URL` wasn't set. From any browser whose machine had a local uvicorn (typical: dev Mac), every fetch silently went there instead of through the OOD rnode proxy. Changed to `"."` (relative). Symptom: GUI showed Mac-side projects when loading from the OOD URL.

- **`step2_setup` manifest write outside `with` block** — `manifest_path.open()` `with` block closed the file after the header line; the loop then wrote to a closed handle. Fixed by indenting the loop into the `with`.

- **Reference alias map: last-writer-wins** — `_reference_alias_map` overwrote a stem→name mapping when multiple reference sets contained the same fasta stem (e.g. a stray `NC_045512.fasta` inside `tb_reference/` overrode the canonical `NC_045512_wuhan-hu-1`). Now prefers the mapping where the reference-set name contains the fasta stem.

- **vsnp3 upstream `column[0]` → pandas 2 KeyError** — patched the conda-installed `vsnp3_fasta_to_snps_table.py` (3 sites) to use `.iloc[0]`. Filed [USDA-VS/vSNP3#22](https://github.com/USDA-VS/vSNP3/issues/22). The patch lives only in the conda env; will be overwritten on `conda update vsnp3`.

- **vsnp3 has no bootstrap support** — patched the same file to read `VSNP3_BOOTSTRAP` env var and run RAxML rapid bootstrap (`-f a`) when set. Backup at `vsnp3_fasta_to_snps_table.py.bak.bootstrap`. Filed [USDA-VS/vSNP3#23](https://github.com/USDA-VS/vSNP3/issues/23) for an upstream `--bootstrap N` flag. Same conda-update caveat as the column[0] patch.

- **IGV "Error loading Google oAuth properties" nag** — set `ENABLE_GOOGLE_MENU=false` and cleared `PROVISIONING_URL` in `~/.igv/prefs.properties`. Pre-T-02 mitigation; mostly moot once desktop IGV is retired.

---

## Branching / source of truth

`web` is the OOD/FastAPI rewrite branch off `main`. The pre-Apr-10 Electron history on `main` is preserved but is **not** the path forward for OOD deployment. Daily work happens on wgs3 against this branch. The Mac copy (`/Users/vivekkapur/vsnp_gui/`) is a viewer; do not run a local uvicorn there or it will shadow the OOD backend in the browser (see the API_BASE note above).
