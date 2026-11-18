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
In **Settings**, use the **Choose** button beside each field to pick:
- vSNP3 path (the vSNP3 conda environment directory, e.g. `~/miniconda3/envs/vsnp3`)
- Projects root

Click **Save & Refresh** and check for a ✓ beside each path.

## 4) Try the bundled demo VCFs
For a quick Step 2 demo (needs the `mtbc0_v1.1` reference):
- Projects → create a project with reference **mtbc0_v1.1** and select it
- Step 2 → **Build the comparison set** tab → box 2 → **More options** → **Browse** → pick the repo's `sample_data/vcf_lite` folder
- **vcf_lite (8 samples)** appears in box 2, ticked → **Build comparison set** → **Run**
- Afterwards, click the **x** next to `vcf_lite` so it isn't added to other `mtbc0_v1.1` projects

Full walkthrough: [QUICKSTART.md](QUICKSTART.md), Part 3.

## 5) Troubleshooting
- If the Electron window is blank, check that the frontend dev server is running.
- If a run fails because a Python package (pandas or Bio) is missing, install them in the vSNP3 environment:
  - `conda install -n vsnp3 pandas biopython`
