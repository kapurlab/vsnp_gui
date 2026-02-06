# vSNP GUI (Electron) Quickstart

This runs the GUI as a standalone macOS app (Electron) with native folder pickers.

## 1) Install prerequisites
- Node.js (brew install node)
- Python 3.9+
- Conda (recommended if you already use it)

## 2) Start the Electron app
From the repo root:
```
./start_electron.sh
```
This starts:
- FastAPI backend on port 8000
- Vite frontend dev server on port 5173
- Electron app pointing at the dev server

## 3) First run settings
In **Settings**, use **Choose** buttons to pick:
- vSNP3 path
- Projects root
- vSNP3 path (conda environment directory, e.g. `~/miniconda3/envs/vsnp3`)

Click **Save** and **Preflight**.

## 4) Try the VCF Lite Pack
For a quick Step 2 demo:
- Step 2 → VCF Sources → Preset **“VCF Lite Pack (repo)”**
- Build VCF set → Run

## 4) Troubleshooting
- If the Electron window is blank, check that the frontend dev server is running.
- If Preflight fails, install dependencies in the vSNP3 environment:
  - `conda install -n vsnp3 pandas biopython`
