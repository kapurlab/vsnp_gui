# vSNP GUI

A local web interface for running [vSNP3](https://github.com/USDA-VS/vSNP3) SNP analysis pipelines.

## Two-Repo Setup

This project consists of two separate repositories:

1. **vSNP3** (the analysis pipeline) — installed separately via conda/git. This is the bioinformatics engine that performs alignment, SNP calling, and phylogenetic analysis.
2. **vsnp_gui** (this repo) — provides a graphical interface that calls vSNP3 commands. It connects to vSNP3 via the paths you configure in Settings.

### Typical directory layout

```
~/miniconda3/envs/vsnp3/     # Conda environment with Python + dependencies
~/vsnp3/                      # vSNP3 pipeline repo (cloned from GitHub)
~/vsnp_gui/                   # This GUI repo (cloned from GitHub)
~/vsnp3/projects/             # Project data (configurable)
```

## Requirements

- macOS (tested) or Linux
- Python 3.9+
- Node.js 18+
- vSNP3 installed via conda

## Recommended Conda/Mamba Environment

Create a single environment with vSNP3 and all dependencies:

```bash
mamba create -n vsnp3 -c conda-forge -c bioconda vsnp3 bcftools nodejs
```

Notes:
- The environment name can be anything. Set the full path in **vSNP3 path** in Settings (e.g. `~/miniconda3/envs/vsnp3`).
- `nodejs` is required for running the Electron app in dev mode; browser-only usage can omit it.
- `bcftools` is required for the VCF edit workflow.

## Quick Start (5 minutes)

### 1. Clone and launch

**Browser version:**
```bash
git clone https://github.com/vkapur/vsnp_gui.git
cd vsnp_gui
./start_gui.sh
```
Opens at http://localhost:5173

**Desktop app (Electron):**
```bash
./start_electron.sh
```
Native file dialogs for easier path selection

On startup, the script will auto-detect your conda installation and print the detected paths:
```
================================================
  vSNP GUI — Detected Paths
================================================
  GUI root:        /Users/you/vsnp_gui
  vSNP3 path:      /Users/you/miniconda3/envs/vsnp3
================================================
```
Copy the vSNP3 path into the Settings panel.

### 2. Configure (Settings panel)

On first launch, a yellow banner appears and all actions are disabled until you configure:

| Setting | Example | Description |
|---------|---------|-------------|
| vSNP3 path | `~/miniconda3/envs/vsnp3` | Path to the vSNP3 conda environment (contains scripts, references, and runtime) |
| Projects root | `~/vsnp3/projects` | Where project data is stored |

Click **Save** → **Preflight** (banner disappears, buttons enable)

### 3. Try the demo (VCF Lite Pack)

No FASTQ files needed - jump straight to Step 2:

1. Create a project (e.g., "demo")
2. **Step 2** → Select "Use custom VCF set"
3. **Preset** → "VCF Lite Pack (repo)"
4. **Build VCF set** → **Run**

You'll get a phylogenetic tree and SNP matrices in ~30 seconds.

## Full Workflow

```
FASTQ → Step 1 (align + call) → QC Review → Step 2 (tree + matrix)
```

1. **Create project** - Organizes your analysis
2. **Add inputs** - Link local FASTQ or download from SRA
3. **Step 1 Setup** - Creates sample directories
4. **Step 1 Run** - Aligns reads, calls SNPs (per sample)
5. **QC Summary** - Review metrics, exclude bad samples
6. **Step 2 Setup/Build** - Gather VCFs for comparison
7. **Step 2 Run** - Build phylogenetic tree and SNP matrices

## Documentation

- [docs/DOCUMENTATION_INDEX.md](docs/DOCUMENTATION_INDEX.md) - Start here
- [docs/QUICKSTART.md](docs/QUICKSTART.md) - Step-by-step tutorial with screenshots
- [docs/USER_GUIDE_COMPREHENSIVE.md](docs/USER_GUIDE_COMPREHENSIVE.md) - Full documentation
- [docs/QUICK_REFERENCE.md](docs/QUICK_REFERENCE.md) - One-page cheat sheet
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) - Common issues and solutions

## Sample Data

The `sample_data/vcf_lite/` folder contains 8 pre-processed VCF files for testing Step 2 without running Step 1.

## Development

```bash
# Backend (FastAPI)
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# Frontend (React + Vite)
cd frontend && npm install && npm run dev

# Electron (desktop app)
cd electron && npm install && VITE_DEV_SERVER_URL="http://localhost:5173" npm run dev
```

## Browser vs Electron

| Feature | Browser (`start_gui.sh`) | Electron (`start_electron.sh`) |
|---------|-------------------------|-------------------------------|
| Path selection | Manual text entry | Native folder picker |
| Access | Any browser on network | Desktop app only |
| Setup | Simpler | Requires Electron |

Both versions use the same backend and frontend code. The Electron version adds native file dialogs via `window.vsnp.selectPath()`.

## Changelog (Unreleased)

> **Alpha 0.01**: This is an early alpha release. It is intended for local, controlled deployments and internal testing only.

### Post-hoc Step 1 (feature/posthoc-step1)

- Adds a Post-hoc tab in Step 1 Results to merge QC summaries across multiple Step 1 folders.
- Folder picker defaults to projects root; toggle to include the current project's Step 1.
- Post-hoc table supports Open Folder, IGV, and Stats links using original paths (no duplication).
- Step 1 list height/scrolling aligned with Step 2 panel; clearer "scroll for more" UX.

### VCF Edit Workflow (feature/vcf-edit)

- Adds per-sample VCF editing with audit trail (patched VCF + JSONL log).
- Edit modal can fetch current REF/ALT, auto-fills ALT, and requires an edit reason.
- Step 1 + Post-hoc tables show Edited badges and Edit Log links.
- Step 2 prefers patched VCFs; warns when edits exist and writes `edited_samples.json`.
- Robust handling of corrupt patched VCFs with automatic rebuild from source.
- Settings include `bcftools` path for edits; text logs open in TextEdit on macOS.

## Citation

Hicks J, Stuber T, Lantz K, Torchetti M, Robbe-Austerman S. vSNP: a SNP pipeline for the generation of transparent SNP matrices and phylogenetic trees from whole genome sequencing data sets. *BMC Genomics*. 2024;25:545. PMID: 38822271
