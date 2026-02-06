# vSNP GUI Alpha Testing Quickstart

This guide is for collaborators who will run the GUI locally on macOS.

## What you need
- macOS
- Git
- Node.js (for the frontend)
- Python 3.9+ (for the backend)
- vSNP3 installed locally

If you already have conda, you can use it. If not, you can use a standard Python venv.

## 1) Clone the GUI repo
```
git clone https://github.com/vkapur/vsnp_gui.git
cd vsnp_gui
```

## 2) Get vSNP3 installed
You need a local vSNP3 checkout and its dependencies (references and scripts).

Example:
```
# Pick a location you control
mkdir -p ~/vsnp3
cd ~/vsnp3
# If you already have vSNP3, skip the clone
# git clone https://github.com/USDA-VS/vsnp3.git
```

You will need the vSNP3 path in the GUI Settings (see below).

## 3) Start the GUI (easy method)
From the GUI repo root:
```
./start_gui.sh
```
This launches the backend and frontend and opens a browser tab.

Alternatively, you can double‑click:
- `Launch_vSNP_GUI.command`

Electron app (native folder pickers):
```
./start_electron.sh
```

## 4) Configure in the GUI (Settings panel)
In the Settings panel:
- **vSNP3 path**: the folder containing `vsnp3_step1.py` (e.g. `~/vsnp3`)
- **Projects root**: where projects live (e.g. `~/vsnp3/projects`)
- **vSNP3 path**: the conda environment directory (e.g. `~/miniconda3/envs/vsnp3`)

Then click **Save** and **Preflight**.

## 4b) Sample data (VCF Lite Pack)
For Step 2 testing, you can use the built‑in **VCF Lite Pack**:
- Step 2 → **VCF Sources**
- Preset: **“VCF Lite Pack (repo)”**
- Build VCF set → Run

## Conda path (recommended if you already use conda)
Create a conda environment and install dependencies:
```
conda create -n vsnp3 python=3.9 -y
conda install -n vsnp3 -c conda-forge pandas biopython -y
```
Set **vSNP3 path** in the GUI to the environment directory (e.g. `~/miniconda3/envs/vsnp3`) and run **Preflight**.

## Venv path (if you do not use conda)
Create a virtual environment and install dependencies:
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```
Make sure the GUI Settings use the correct Python/venv if prompted.

## Using the GUI (high-level)
1. Create a project
2. Add input files (BYO FASTQ or SRA download)
3. Step 1: Setup → Run
4. QC Summary: review and optionally exclude
5. Step 2: Setup → Run
6. Step 2 Results: open outputs

## Reporting issues / feedback
Please report:
- Exact error messages
- Which step (Setup/Run/Preflight)
- The project name and reference used
- Screenshots of the panel where it failed

You can open an issue or send notes directly to the dev team.

## Notes
- The GUI stores projects under the **Projects root** path.
- The **Projects** panel now supports Archive/Delete.
- If you change paths, click **Save** and rerun **Preflight**.
