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
- [x] Retire desktop IGV: `step1_igv_session`, `posthoc/igv_session`, `_open_igv` and helpers, `igv_app_path` config field, frontend handlers and settings UI (commit follows)
- [ ] After T-03 lands, remove Xvfb / x11vnc / websockify from OOD `script.sh.erb`
- [ ] Posthoc-tab IGV smoke test (needs a 2nd project; sufficient by symlinking `test/step1`)

## P1

### T-03 — Browser tree viewer — 🚧 (on `t03-spike` branch)

A/B spike comparing **phylotree.js** vs **phylocanvas.gl** is on the `t03-spike` branch.

Outcome: **phylotree.js wins** for vSNP's screening workflow.
- Trees < 2k leaves; Phylocanvas's WebGL scale advantage doesn't apply.
- SVG output is publication-grade and native in phylotree.
- Reroot, tip search, bootstrap labels, midpoint root all first-class.
- Phylocanvas.gl 1.62 also has a "Attempted to assign to readonly property" failure under modern Vite/Safari that we couldn't unblock; not worth fighting.

Spike code lives at:
- `frontend/src/TreePtStandalone.jsx` (winner — promote into a drawer next)
- `frontend/src/TreePcStandalone.jsx` (loser — delete on promotion)
- Backend `GET /api/projects/{p}/step2/trees` — keep
- npm: `phylotree` (keep), `@phylocanvas/phylocanvas.gl` (delete on promotion)

**Bootstrap shipped on the spike**:
- vsnp3 upstream doesn't generate bootstrap values — RAxML invocation in `bin/vsnp3_fasta_to_snps_table.py` is hard-coded as best-tree only. Patched the conda env to read a `VSNP3_BOOTSTRAP` env var; if > 0, RAxML runs `-f a -x 7777 -N <n>` and the pipeline picks up `RAxML_bipartitions.raxml`.
- New "Bootstrap (replicates)" field in Step 2 Options. Default 0 (off). Backend sets the env var when > 0.
- Filed upstream: [USDA-VS/vSNP3#23](https://github.com/USDA-VS/vSNP3/issues/23).
- End-to-end smoke on `test` project: 50 replicates run in ~2s on 7 SARS-CoV-2 deer samples; `.tre` carries support values.

**Pending before merge to `web`**:
- [ ] Promote `TreePtStandalone` into a resizable + fullscreen drawer in `App.jsx` (mirror IGV drawer's UX). Currently spike-only via `?view=tree-pt` tab.
- [ ] Wire a "View" button next to `*.tre` rows in Step 2 outputs that opens the drawer (replaces the spike `phylotree`/`phylocanvas` buttons).
- [ ] Delete `TreePcStandalone.jsx` and `npm uninstall @phylocanvas/phylocanvas.gl`.
- [ ] Cherry-pick or merge `t03-spike` → `web`.
- [ ] Retire FigTree backend launcher (`figtree_app_path` config field, `_open_path` xdg-open on .tre).
- [ ] Remove Xvfb / x11vnc / websockify from OOD `script.sh.erb` (closes T-02 acceptance criterion 6).

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
