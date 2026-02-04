# vSNP GUI

A local web interface for running [vSNP3](https://github.com/USDA-VS/vSNP3) SNP analysis pipelines.

## Requirements

- macOS (tested) or Linux
- Python 3.9+
- Node.js 18+
- vSNP3 installed via conda

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

### 2. Configure (Settings panel)

On first launch, a yellow banner appears and all actions are disabled until you configure:

| Setting | Example |
|---------|---------|
| vSNP3 path | `~/vsnp3` |
| Projects root | `~/vsnp3/projects` |
| Conda env | `vsnp3` |

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

## Citation

Hicks J, Stuber T, Lantz K, Torchetti M, Robbe-Austerman S. vSNP: a SNP pipeline for the generation of transparent SNP matrices and phylogenetic trees from whole genome sequencing data sets. *BMC Genomics*. 2024;25:545. PMID: 38822271
