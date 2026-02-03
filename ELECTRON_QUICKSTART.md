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
- Conda env or env path

Click **Save** and **Preflight**.

## 4) Troubleshooting
- If the Electron window is blank, check that the frontend dev server is running.
- If Preflight fails, install dependencies in the selected conda env:
  - `conda install -n <env> pandas biopython pysam`
