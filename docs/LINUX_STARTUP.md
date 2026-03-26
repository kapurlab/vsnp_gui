# Starting vSNP GUI on the Linux Boxes

## Prerequisites

Both `kapurlab-wgs1` and `kapurlab-wgs2` already have vsnp_gui installed at `~/vsnp_gui` with `node_modules` and conda envs in place. You should not need to reinstall anything unless the repo has been updated.

Required software (already installed on both machines):

- Miniforge3 with a `vsnp3` conda environment at `~/miniforge3/envs/vsnp3`
- Node.js / npm

---

## Option 1: At the Machine (Headed/Local)

### Browser mode

Open a terminal on the machine and run:

```bash
cd ~/vsnp_gui
./start_gui_linux.sh
```

This starts the FastAPI backend on port 8000 and the Vite frontend on port 5173. After a few seconds your default browser will open to `http://localhost:5173`. If it doesn't, open it manually.

### Electron mode

```bash
cd ~/vsnp_gui
./start_electron.sh
```

This starts backend + frontend, waits for Vite to be ready, then launches the Electron desktop app. It auto-detects a free frontend port so there's no conflict if something else is using 5173.

### Desktop launcher

Both machines have a `.desktop` entry registered. Search for "vSNP GUI" in your application menu and click to launch. This runs `start_electron.sh` under the hood.

---

## Option 2: Remote Access from Your Mac

SSH into the machine and start the GUI:

```bash
ssh kapurlab-wgs1
cd ~/vsnp_gui
./start_gui_linux.sh
```

Then in a separate terminal on your Mac, create an SSH tunnel:

```bash
ssh -L 5173:localhost:5173 -L 8000:localhost:8000 kapurlab-wgs1
```

Open http://localhost:5173 in your Mac's browser. Replace `kapurlab-wgs1` with `kapurlab-wgs2` as needed.

Alternatively, since `start_gui_linux.sh` binds to `0.0.0.0`, if you're on the same network you can skip the tunnel and go directly to `http://kapurlab-wgs1:5173`.

## Stopping

`Ctrl+C` in the terminal where you started it. The trap handler kills both backend and frontend processes.

## Troubleshooting

**"Cannot find vsnp3 conda env"** -- The script looks for `~/miniforge3/envs/vsnp3` or `~/miniconda3/envs/vsnp3`. Verify with `conda env list`.

**"npm not found"** -- Run `sudo apt install nodejs npm` or check that nvm/fnm is sourced in your shell.

**Port already in use** -- A previous session may not have cleaned up. Kill orphan processes:

```bash
lsof -ti :8000 | xargs kill -9
lsof -ti :5173 | xargs kill -9
```

**Backend deps out of date** -- The script runs `pip install -r requirements.txt` on every start, but if something is broken:

```bash
cd ~/vsnp_gui/backend
~/miniforge3/envs/vsnp3/bin/python -m pip install -r requirements.txt
```
