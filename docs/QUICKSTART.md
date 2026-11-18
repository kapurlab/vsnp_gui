# vSNP GUI Quickstart

A step-by-step guide to get running in under 10 minutes.

---

## Prerequisites

Before starting, ensure you have:

| Requirement | Check | Install |
|-------------|-------|---------|
| macOS or Linux | - | - |
| Python 3.9+ | `python3 --version` | [python.org](https://python.org) |
| Node.js 18+ | `node --version` | `brew install node` |
| Conda | `conda --version` | [miniconda](https://docs.conda.io/en/latest/miniconda.html) |
| vSNP3 | `conda list -n vsnp3` | See below |

### Recommended Conda/Mamba Environment

```bash
mamba create -n vsnp3 -c conda-forge -c bioconda \
  python=3.10 pandas biopython samtools bcftools nodejs
```

Notes:
- Point the **vSNP3 path** setting to this environment directory (e.g. `~/miniconda3/envs/vsnp3`).
- `nodejs` is required for Electron dev; browser-only usage can omit it.
- `bcftools` is required for the VCF edit workflow.

### Install vSNP3 (if needed)

```bash
conda create -c conda-forge -c bioconda -n vsnp3 vsnp3 -y
conda activate vsnp3
vsnp3_step1.py -h  # verify installation
```

---

## Part 1: Launch the GUI

### Step 1.1: Clone the repository

```bash
git clone https://github.com/kapurlab/vsnp_gui.git
cd vsnp_gui
```

### Step 1.2: Choose your launch mode

**Option A: Browser version** (recommended for first-time users)
```bash
./start_gui.sh
```
Opens at http://localhost:5173. Paths are entered manually as text.

**Option B: Desktop app (Electron)**
```bash
./start_electron.sh
```
Runs as a native desktop app with folder picker dialogs for path selection.

Both versions are functionally identical. The Electron version provides a more polished experience with native file dialogs.

### Step 1.3: What happens on launch

The script will:
- Create a Python virtual environment for the backend
- Install backend dependencies (FastAPI, etc.)
- Install frontend dependencies (React, Vite)
- (Electron only) Install Electron and open the desktop app

**First launch takes 1-2 minutes** (subsequent launches are faster).

### Step 1.4: Verify startup

You should see:
- Terminal output showing backend on port 8000
- Terminal output showing frontend on port 5173
- Browser opens (or Electron window appears)

---

## Part 2: Configure Settings

On first launch, you'll see a yellow banner: **"Setup required: Set vSNP3 path, projects root, and conda env in Settings, then click Save & Refresh + Preflight."**

All action buttons (Grab ready-to-run samples, Run, Build comparison set, etc.) remain disabled until these three settings are configured. The banner disappears once you've saved valid settings.

### Step 2.1: Fill in Settings

In the **Settings** panel (top-left):

| Field | Value | Notes |
|-------|-------|-------|
| **vSNP3 path** | `~/vsnp3` or wherever vSNP3 is installed | Contains `dependencies/` folder |
| **Projects root** | `~/vsnp3/projects` | Where your analyses will be saved |
| **vSNP3 path** | `~/miniconda3/envs/vsnp3` | Conda environment directory containing vSNP3 scripts and Python runtime |

**Electron users**: Click the folder icon next to each path field to open a native folder picker dialog.

### Step 2.2: Save and verify

1. Click **Save**
2. Click **Preflight**

You should see: `Checked: pandas, Bio | Missing: none`

If you see missing dependencies:
```bash
conda install -n vsnp3 pandas biopython -y
```

---

## Part 3: Demo Run (bundled VCFs)

Let's verify everything works using the included sample data. No FASTQ files needed!

The repo ships 8 MTBC VCFs in `sample_data/vcf_lite/` (see its [README](../sample_data/vcf_lite/README.md)). They were called against MTBC0, so the demo needs the `mtbc0_v1.1` reference. If `mtbc0_v1.1` is not in the reference list in Step 3.1, your vSNP3 install doesn't have that reference and this demo won't run; go on to Part 4.

### Step 3.1: Create a project

1. In the **Projects** panel, type `demo` in the text field
2. In the **-- reference (optional) --** list next to it, pick `mtbc0_v1.1`
3. Click **Create**
4. Click on `demo` to select it

### Step 3.2: Add the demo VCFs and build the comparison set

1. Scroll to the **Step 2** section and stay on the **Build the comparison set** tab
2. Box 2 (**Reference databases to compare against**) shows `Reference: mtbc0_v1.1`. Under it, open **More options — add your own folder, copy / link, duplicate handling**
3. Enter the full path to the `sample_data/vcf_lite` folder, e.g. `/Users/you/vsnp_gui/sample_data/vcf_lite`, and click **Add**
   - **Electron users**: click **Browse** and pick the folder instead
   - The path is read on the machine running the backend, so on a server use the server's path to this repo
4. Box 2 now lists **vcf_lite (8 samples)**, ticked
   - If your site has shared databases for `mtbc0_v1.1` (they are tagged **shared**), untick them for the quickest run
   - Leave box 1 (`vcf_database`) as it is. It is empty in a new project, which is fine
5. In box 3, click **Build comparison set**

You should see: `Imported 8 | Ref: mtbc0_v1.1`, and below it `This run compares 8 of the 8 VCFs in the set — ready to Run`

### Step 3.3: Run Step 2

1. Verify **Step 2 Options** shows **Reference:** `mtbc0_v1.1`
2. Click **Run**
3. Watch the **Live Logs** panel for progress

**Expected time**: 30-60 seconds

### Step 3.4: View results

When complete, the **Step 2 Results** panel shows the new comparison with:
- `vSNP_step2_summary-*.html` - Interactive summary (click **View**, or **DL** to download)
- `vcf_starting_files.zip` - The VCFs that went into the comparison (**DL**)
- Group folders (`MTBC0-All` plus lineage groups such as `Lineage-02`) with SNP matrices and tree files

**Congratulations!** You've verified the GUI works.

### Step 3.5: Remove the demo folder

In box 2, click the **x** next to `vcf_lite`. An added folder stays in your list and stays ticked for every `mtbc0_v1.1` project, so leaving it there would add the 8 demo samples to your real comparisons. Removing it deletes no files.

---

## Part 4: Full Workflow (with FASTQ)

Now let's run a complete analysis from raw reads.

### Step 4.1: Create a new project

1. Type `mtb_analysis` in Projects
2. Click **Create** → Select it

### Step 4.2: Add FASTQ files

**Option A: Link local files**
1. In **Inputs** → "Bring Your Own FASTQ"
2. Enter the path to a folder containing `*.fastq.gz` files
3. Click **Link Local Files**

**Option B: Download from SRA**
1. In **Inputs** → "SRA Download"
2. Paste accession numbers (one per line):
   ```
   SRR5642711
   SRR7617662
   SRR1791695
   ```
3. Click **Download**
4. Monitor progress in **Logs**

### Step 4.3: Run Step 1

1. In **Step 1** panel, click **Grab ready-to-run samples**
   - Copies the downloaded FASTQs into Step 1, one folder per sample; they appear as Not Started
2. Select a **Reference** (or choose "Auto-detect")
3. Click **Run**

**Expected time**: 5-15 minutes per sample

Monitor progress:
- **Logs** panel shows real-time output
- **Samples** list shows status badges (running → complete)

### Step 4.4: Review QC

After Step 1 completes:

1. Click **Refresh** in QC Summary
2. Review metrics:
   - **Avg Depth** - Target >40X (flagged if <40)
   - **Dup %** - Target <80%
   - **R1/R2 Q20** - Target >50%
3. Check **Exclude** box for poor samples
4. Click **Save Exclusions**

### Step 4.5: Run Step 2

**All of this project's samples (the normal path):**
1. Open the **Build the comparison set** tab
2. Keep this project's **vcf_database** ticked
3. Optionally tick reference databases to compare against
4. Click **Build comparison set** → **Run**

**Only some samples:**
1. Open the **Compare a list of samples** tab
2. Paste the sample names
3. Optionally tick a reference database to compare against
4. Click **Run**

### Step 4.6: Explore outputs

Results appear in **Step 2 Results**:

| Output | Description |
|--------|-------------|
| `all_vcf_*.html` | Interactive summary |
| `*_cascade.xlsx` | SNP matrix (evolutionary sorting) |
| `*_tree.tre` | Newick tree file |
| Group folders | Per-clade breakdowns (if defining SNPs exist) |

Click **Open** to view any file in your default application.

---

## Troubleshooting

### "Preflight failed"
- Verify vSNP3 path points to the correct conda environment directory
- Run: `conda install -n vsnp3 pandas biopython`

### "No FASTQ files found"
- Check your path contains `*.fastq.gz` files
- Files must have `_R1` and `_R2` in names

### "Mixed references detected"
- All samples in one Step 2 run must use the same reference
- Create separate projects for different organisms

### Step 1 stuck or errored
- Click **View log** for the specific sample
- Check for disk space, memory issues
- Re-run just the failed samples

### GUI won't start
```bash
# Kill any stuck processes
pkill -f uvicorn
pkill -f "npm run dev"
pkill -f electron

# Restart
./start_gui.sh  # or ./start_electron.sh
```

### Electron window blank or won't load
- Ensure backend (port 8000) and frontend (port 5173) are running
- Check terminal for errors from any of the three processes
- Try the browser version first to verify backend/frontend work

### Electron folder picker not working
- The native folder picker requires the app to be focused
- If dialogs don't appear, check for windows behind the main app

---

## Next Steps

- Read the [USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md) for detailed explanations
- See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for more solutions
- Use the [QUICK_REFERENCE.md](QUICK_REFERENCE.md) as a cheat sheet

---

*Last updated: February 2026*
