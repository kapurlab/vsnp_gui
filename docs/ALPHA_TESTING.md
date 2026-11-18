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
git clone https://github.com/kapurlab/vsnp_gui.git
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
- **vSNP3 path**: the vSNP3 conda environment directory, the one holding `bin/vsnp3_step1.py` (e.g. `~/miniconda3/envs/vsnp3`)
- **Projects root**: where projects live (e.g. `~/vsnp3/projects`)

Then click **Save & Refresh** and check for a ✓ beside each path.

## 4b) Sample data (bundled VCFs)
For Step 2 testing without FASTQs, use the 8 MTBC VCFs in `sample_data/vcf_lite/` (needs the `mtbc0_v1.1` reference):
- Projects → create a project with reference **mtbc0_v1.1** and select it
- Step 2 → **Build the comparison set** tab → box 2 → **More options** → enter the full path to `sample_data/vcf_lite` → **Add**
- **vcf_lite (8 samples)** appears in box 2, ticked → **Build comparison set** → **Run**
- Afterwards, click the **x** next to `vcf_lite` so it isn't added to other `mtbc0_v1.1` projects

Full walkthrough: [QUICKSTART.md](QUICKSTART.md), Part 3.

## Conda path (recommended if you already use conda)
Create a conda environment and install dependencies:
```
conda create -n vsnp3 python=3.9 -y
conda install -n vsnp3 -c conda-forge pandas biopython -y
```
Set **vSNP3 path** in the GUI to the environment directory (e.g. `~/miniconda3/envs/vsnp3`) and click **Save & Refresh**.

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
2. Add input files (Inputs → Add local FASTQ, upload, or SRA Download)
3. Step 1: Grab ready-to-run samples → pick a reference → Run
4. Step 1 Results: review and optionally exclude
5. Step 2: Build comparison set → Run
6. Step 2 Results: open outputs

## Reporting issues / feedback
Please report:
- Exact error messages
- Which step and button (Grab ready-to-run samples, Run, Build comparison set, Save & Refresh)
- The project name and reference used
- Screenshots of the panel where it failed

You can open an issue or send notes directly to the dev team.

## Notes
- The GUI stores projects under the **Projects root** path.
- The **Projects** panel has **Archive**, which moves a project into `projects_archive/`. There is no Delete button; deleting a project is done from the command line.
- If you change paths, click **Save & Refresh** and check for a ✓ beside each path.
