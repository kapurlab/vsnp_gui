# vSNP GUI — Claude Code Context

> Read this file before touching any code. It contains deployment-critical
> constraints that will cause silent breakage if ignored.

## What This Is

A web-based interface for **vSNP3** — a phylogenomic SNP pipeline for
whole-genome sequencing of veterinary/One Health pathogens (M. bovis,
Brucella spp., M. avium subsp. paratuberculosis, SARS-CoV-2).

The GUI is a **FastAPI backend + React (Vite) SPA** deployed as an
**Open OnDemand (OOD) batch_connect interactive application** on a
Linux server. Each authenticated user gets an isolated session with its
own ports and process tree.

Architecture document and ticket backlog:
`/home/vxk1/vsnp_gui/docs/dev/vsnp_gui_architecture_roadmap.docx`

If the file is missing, transfer it from Vivek's Mac:
```bash
scp "/Users/vivekkapur/Desktop/INLEAD Folder/Surveillance and Monitoring/vsnp_gui_architecture_roadmap.docx" \
    vxk1@kapurlab-wgs3.tailf38ff4.ts.net:/home/vxk1/vsnp_gui/docs/dev/
```

---

## Server: kapurlab-wgs3

| Property | Value |
|---|---|
| Hostname | `kapurlab-wgs3.tailf38ff4.ts.net` |
| IP (Tailscale) | `100.68.171.59` |
| OS | Ubuntu 22.04 |
| CPU | AMD Threadripper PRO 7985WX, 64 cores |
| RAM | 503 GB |
| SSH user | `vxk1` |
| SSH key | Your `~/.ssh/id_ed25519` (Tailscale must be connected) |

SSH: `ssh vxk1@kapurlab-wgs3.tailf38ff4.ts.net`

---

## Repository Layout

```
/home/vxk1/vsnp_gui/           ← SOURCE REPO (work here)
  backend/
    app/
      main.py                   ← FastAPI app, 2800+ lines — all routes
      config.py                 ← load_config() / save_config()
      jobs.py                   ← JobManager, background run tracking
      projects.py               ← project CRUD
      refs.py                   ← reference option listing
      sra.py                    ← SRA accession helpers
    data/
      config.json               ← runtime config (paths, settings)
    requirements.txt
  frontend/
    src/
      App.jsx                   ← main React component (all UI state)
      main.jsx                  ← entry point
    vite.config.js              ← base: "./" — DO NOT CHANGE
    package.json
    dist/                       ← compiled output (gitignored; rebuild after edits)
  docs/
    dev/
      vsnp_gui_architecture_roadmap.docx  ← full design doc + tickets

/var/www/ood/apps/sys/vsnp_gui/ ← OOD DEPLOYMENT (separate from source)
  manifest.yml
  form.yml
  submit.yml.erb
  view.html.erb                 ← Connect button template (ERB)
  template/
    before.sh                   ← runs in OOD parent process (port allocation)
    script.sh.erb               ← runs in session container (starts all services)
```

OOD files are **not** automatically synced from the source repo.
After editing OOD files, copy them manually (requires sudo):
```bash
sudo cp /home/vxk1/vsnp_gui/template/before.sh \
        /var/www/ood/apps/sys/vsnp_gui/template/before.sh
# etc.
```

---

## ⚠️ CRITICAL CONSTRAINTS — READ BEFORE WRITING ANY CODE

### 1. All frontend URLs must be relative — no exceptions

OOD proxies the app via Apache mod_proxy at:
`/rnode/<host>/<port>/<path>`

Apache strips the prefix and forwards `/<path>` to uvicorn.
The browser's `window.location` origin is the OOD server, not the app server.

**If you hardcode a host, port, or absolute URL in the frontend, it will
404 under the proxy.** This includes WebSocket URLs.

```js
// ✅ CORRECT
const res = await fetch('./api/projects');
const ws  = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${location.pathname}novnc-ws`);

// ❌ BREAKS UNDER PROXY
const res = await fetch('http://localhost:8000/api/projects');
const ws  = new WebSocket('ws://localhost:15000/');
```

`vite.config.js` has `base: "./"` — **never change this**.

### 2. FastAPI serves the React frontend — do not add a separate static server

`main.py` mounts the compiled `frontend/dist/` as StaticFiles and serves
`index.html` at `/`. Adding a separate nginx/caddy for static files will
break the single-port OOD session model.

### 3. Rebuild the frontend after any frontend edit

```bash
cd /home/vxk1/vsnp_gui/frontend
/home/vxk1/miniforge3/envs/vsnp3/bin/node_modules/.bin/npm run build
# or use the system npm if node is on PATH:
npm run build
```

The built output at `frontend/dist/` is what uvicorn serves. Dev server
(`npm run dev`) is for local development only — it does not work through OOD.

### 4. Use the vsnp3 conda env for all Python

```
/home/vxk1/miniforge3/envs/vsnp3/bin/python
```

Do not use system Python or the base conda Python. All backend dependencies
(fastapi, uvicorn, websockets, etc.) are installed here.

### 5. OOD session execution model

- `before.sh` runs **in the OOD parent process** (as the authenticated user).
  It calls `find_port` (OOD helper) to allocate ports and exports them.
  This is the only place `find_port` can be called.
- `script.sh.erb` runs **inside an Apptainer/Singularity container** via tmux,
  started by SSHing to localhost. It starts Xvfb, x11vnc, websockify, uvicorn.
- ERB syntax (`<%= variable %>`) is substituted by OOD before the script runs.
  Shell variables (`$HOME`, `$USER`, `$port`) work normally after substitution.
- The container uses `--pid` namespace — processes inside are **not** visible
  to the host via `ps aux` or `pgrep`.

### 6. sudo is required to edit OOD app files

`/var/www/ood/apps/sys/vsnp_gui/` is owned by root.
Use `sudo tee` or `sudo cp` to write files there.
The vxk1 user has passwordless sudo for specific paths only — check
`/etc/sudoers.d/` if you get permission errors.

---

## Development Workflow

### Backend change (no frontend change)

1. Edit `backend/app/main.py` (or other backend files)
2. Kill and restart uvicorn inside the active OOD session, OR start a new OOD session
3. To restart uvicorn without a full session restart, find the process (it runs
   inside apptainer — check `ss -tlnp` for the listening port, then kill by port):
   ```bash
   # Find the OOD session port from connection.yml or session logs
   fuser -k <port>/tcp   # kills the process holding that port
   # OOD will mark the session as failed; start a new session instead
   ```
   **Recommended: just start a new OOD session** — it takes ~10 seconds.

### Frontend change

1. Edit files in `frontend/src/`
2. Rebuild: `cd /home/vxk1/vsnp_gui/frontend && npm run build`
3. Start a new OOD session (uvicorn serves the new dist/ on startup)

### OOD template change (before.sh, script.sh.erb, view.html.erb)

1. Edit the file in `/home/vxk1/vsnp_gui/` (or directly with sudo in `/var/www/`)
2. Copy to OOD location:
   ```bash
   sudo cp /home/vxk1/vsnp_gui/template/before.sh \
           /var/www/ood/apps/sys/vsnp_gui/template/before.sh
   ```
3. Start a new OOD session to test

---

## Environment & Key Paths

| Item | Path |
|---|---|
| Python (use this) | `/home/vxk1/miniforge3/envs/vsnp3/bin/python` |
| conda env | `/home/vxk1/miniforge3/envs/vsnp3/` |
| vsnp3 CLI scripts | `/home/vxk1/miniforge3/envs/vsnp3/bin/vsnp3_*` |
| IGV binary | `/home/vxk1/miniforge3/envs/vsnp3/bin/igv` |
| FigTree binary | `/home/vxk1/miniforge3/envs/vsnp3/bin/figtree` |
| Reference options | `/home/vxk1/vSNP_reference_options/` (23 sets) |
| Reference path config | `/home/vxk1/miniforge3/envs/vsnp3/dependencies/reference_options_paths.txt` |
| Projects root | `/home/vxk1/projects/` |
| IGV prefs | `/home/vxk1/.igv/prefs.properties` (PORT_ENABLED=true) |
| noVNC static files | `/usr/share/novnc/` |
| OOD config | `/etc/ood/config/ood_portal.yml` and `clusters.d/wgs3.yml` |
| OOD app (deployed) | `/var/www/ood/apps/sys/vsnp_gui/` |
| OOD app (source) | `/home/vxk1/vsnp_gui/` ← edit here |

---

## Reference Option Structure

Each reference set lives at `/home/vxk1/vSNP_reference_options/<name>/` and contains:

```
NC_000962.fasta              ← reference genome (FASTA)
NC_000962.gbk                ← GenBank annotation
NC_000962.gff                ← GFF3 annotation
H37_define_filter.xlsx       ← defining SNP positions (Step 2 grouping)
H37_remove_from_analysis.xlsx ← positions to exclude
best_reference.txt           ← for auto-detection (sourmash)
```

The GUI discovers references by reading `reference_options_paths.txt`,
then listing subdirectories of each listed path.

---

## Active Ticket Backlog (priority order)

See the architecture doc for full specs. Summary:

| ID | Title | Priority |
|---|---|---|
| T-01 | Fix Stats & Open Folder (download-based) | P0 |
| T-02 | Replace desktop IGV with igv.js | P0 |
| T-03 | Browser tree viewer (Phylocanvas.gl) | P1 |
| T-04 | Remove hardcoded user paths ($HOME-relative) | P1 |
| T-05 | Real-time Step 1 log streaming (SSE) | P1 |
| T-06 | Project export ZIP download | P1 |
| T-07 | Run provenance (run_metadata.json) | P1 |
| T-08 | Install script (automated OOD deployment) | P2 |
| T-09 | Sample QC badges (pass/review/fail) | P2 |
| T-10 | Docker Compose deployment path | P2 |

Start with T-01 (small, high impact, fixes a visible bug) then T-02 (igv.js,
largest architectural change).

---

## What Is Currently Working

- OOD batch_connect session launches and allocates ports correctly
- FastAPI backend serves all Step 1 and Step 2 pipeline routes
- React frontend loads through OOD rnode proxy (relative URLs work)
- noVNC virtual desktop connects via WebSocket through uvicorn proxy
- IGV launches in virtual desktop (prefs set, binary found, non-blocking)
- FigTree launches in virtual desktop
- All 23 reference sets visible and selectable in the GUI
- Step 1 ran successfully (verified with SARS-CoV-2 deer samples)

## What Is Currently Broken / Incomplete

- Stats button — attempts OS-native file open, fails in web context (T-01)
- Open Folder button — same issue (T-01)
- IGV is virtual desktop only — fragile, requires noVNC open (T-02)
- No real-time progress during Step 1 runs (T-05)
- Hardcoded /home/vxk1/ paths in config.json defaults (T-04)
- No run provenance recorded (T-07)
