# vSNP GUI (fresh build)

Local web GUI for vSNP3 with FastAPI + React. Focus: stable workflow, centralized SRA download handling, and clean logs.

## Quick Start

### Backend
```bash
cd /Users/vivekkapur/vsnp_gui/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd /Users/vivekkapur/vsnp_gui/frontend
npm install
npm run dev
```

Open: http://localhost:5173

## Config
The backend writes config to `backend/data/config.json`.
Defaults:
- `vsnp3_path`: `/Users/vivekkapur/vsnp3`
- `projects_root`: `/Users/vivekkapur/vsnp3/projects`
- `conda_env`: `vsnp3`

Ensure vSNP3 is in your PATH:
```bash
which vsnp3_step1.py
which vsnp3_step2.py
```

If vSNP3 is installed in a conda env, set the env name in Settings.

## Notes
- SRA downloads run in `projects/<name>/download` and log to `projects/.jobs/<job_id>.log`.
- References are auto-detected from `vsnp3/dependencies/reference_options_paths.txt`.

## Next Steps
- Add settings UI for `vsnp3_path`, `projects_root`, and SRA flags.
- Add job history per project.
- Add QC summary parsing and results browser.
