# vSNP GUI Quick Reference Card

**One-page guide for experienced users**

---

## 🚀 Workflow Overview

```
FASTQ Files → Step 1 (Align + Call SNPs) → QC Review → Step 2 (Build Trees) → Results
```

---

## ⚙️ Initial Setup (One-Time)

1. **Settings Panel:**
   - vSNP3 path: `/path/to/vsnp3`
   - Projects root: `/path/to/projects`
   - vSNP3 path: `~/miniconda3/envs/vsnp3`
   - Click **Save**

2. **Preflight Check:**
   - Click **Preflight**
   - Should show: `Missing: none`
   - If missing packages: `conda install -n vsnp3 pandas biopython`

---

## 📁 Project Setup

1. **Create:** Enter name → **Create**
2. **Add Files:** Choose one:
   - **Link Local:** Path → **Link Local Files**
   - **Upload:** Drag files to dropzone
   - **SRA:** Accessions (one/line) → **Download**

---

## 🧬 Step 1: Alignment & SNP Calling

### Run Step 1
1. **Setup** → Creates sample folders
2. **Select Reference:**
   - **Auto-detect** (recommended) OR
   - Specific reference (e.g., `Mycobacterium_AF2122`)
3. **Run** → Monitor in Live Logs

### Outputs (per sample)
- `*_zc.vcf` - Zero-coverage VCF (for Step 2)
- `*_stats.xlsx` - QC metrics
- `*.bam` / `*.bai` - Alignments (for IGV)

---

## ✅ QC Review

### Critical Metrics

| Metric | Good | ⚠️ Flag | Action |
|--------|------|---------|--------|
| **Avg Depth** | ≥40X | <40X | Re-sequence or accept |
| **Zero Coverage** | <5% | >10% | Wrong reference |
| **Duplicates** | <50% | >80% | Note, usually OK |
| **R1/R2 Q20** | >50% | <50% | Poor sequencing |
| **Quality SNPs** | Varies | >10% genome | Wrong reference |

### Actions
- ✅ **Download CSV** - Export QC data
- ✅ **Show flagged** - Filter poor samples
- ✅ **Exclude** - Check boxes → **Save Exclusions**
- ✅ **View log** - Sample-specific details

---

## 🌳 Step 2: Trees & SNP Tables

### Run Step 2
1. **Use Step 1 only**: Setup → Links `*_zc.vcf`
2. **Use custom VCF set**: Build VCF set → Run
3. Verify: Single reference (no mixed references!)

### VCF Lite Pack
- Preset: **“VCF Lite Pack (repo)”** in Step 2 → VCF Sources

### Outputs
- **SNP Matrices** (3 versions):
  - Cascading (evolutionary patterns)
  - Alt cascading (problem samples)
  - Position-sorted (genome regions)
- **Phylogenetic Tree** (`*.tre`)
- **HTML Summary** (interactive)
- **Group Folders** (if defining SNPs exist)

---

## 📊 Reading SNP Matrices

**Cell Values:**
- `A, C, G, T` - Nucleotide calls
- `R, Y, M, K, S, W` - Mixed calls (IUPAC)
- `N` - Low quality/indel
- `-` - No coverage

**Mixed Calls (>5 per sample):**
- May indicate co-infection or contamination
- Check cascading matrix for patterns

---

## 🔧 Common Issues

| Problem | Solution |
|---------|----------|
| **High zero coverage (>10%)** | Try different reference |
| **Too many SNPs (>10% genome)** | Reference too distant |
| **Mixed reference error (Step 2)** | Split into separate projects |
| **Job fails** | Check logs, verify vSNP3 path |
| **No samples showing** | Check Step 1 Setup ran |

---

## 💡 Best Practices

### Reference Selection
- ✅ Use **Auto-detect** for unknown samples
- ✅ Keep samples <1,000 SNPs from reference
- ✅ Use basal reference for proper tree rooting
- ⚠️ Avoid distant references (>10% SNPs)

### Data Management
- ✅ Link FASTQ (don't copy) to save space
- ✅ Archive completed projects
- ✅ Export results before deleting
- ✅ Descriptive project names: `Organism_Study_Date`

### Quality Control
- ✅ Review QC metrics before Step 2
- ✅ Exclude poor quality samples
- ✅ Document exclusion criteria
- ✅ Check for mixed calls (co-infection indicator)

---

## 📚 File Naming Requirements

### FASTQ Files
**Paired-end naming:**
```
SampleName_R1.fastq.gz + SampleName_R2.fastq.gz
OR
SampleName_1.fastq.gz + SampleName_2.fastq.gz
```

**Rules:**
- Must be gzip compressed (`.fastq.gz`)
- Sample name = everything before `_R1`/`_R2` or `_1`/`_2`
- Avoid special characters in sample names

---

## 🎯 Typical Analysis Time

| Step | Time (per sample) |
|------|-------------------|
| **SRA Download** | 5-20 min (varies by size) |
| **Step 1 (bacterial)** | 5-15 min |
| **Step 2 (10 samples)** | 1-5 min |

*Times for ~4 Mb bacterial genome at 50X coverage*

---

## 🆘 Quick Troubleshooting

1. **Backend won't start:**
   ```bash
   cd vsnp_gui/backend
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Frontend won't start:**
   ```bash
   cd vsnp_gui/frontend
   npm install
   ```

3. **Conda environment issues:**
   ```bash
   conda activate vsnp3
   conda install pandas biopython -c conda-forge
   ```

4. **Check running processes:**
   ```bash
   lsof -i :8000  # Backend
   lsof -i :5173  # Frontend
   ```

---

## 📖 References

- **Full User Guide:** `USER_GUIDE.md`
- **Alpha Testing:** `ALPHA_TESTING.md`
- **vSNP3 Paper:** https://pmc.ncbi.nlm.nih.gov/articles/PMC11143592/
- **GitHub:** https://github.com/USDA-VS/vSNP3

---

## 🔑 Keyboard Shortcuts

- **Hide/Show sections:** Click section headers
- **Scroll:** Page Up/Down, Arrow keys
- **Refresh QC:** After Step 1 completes

---

## ⚡ Power User Tips

1. **Re-run Step 2 without Step 1:**
   - Modify exclusions
   - Adjust parameters
   - No need to re-align!

2. **Parallel downloads:**
   - Multiple SRA accessions download sequentially
   - Monitor in Live Logs

3. **View in IGV:**
   - Open `*.bam` files from `step1/SampleName/alignment_*/`
   - Inspect coverage and SNP positions

4. **Export for publication:**
   - Trees: `*.tre` → FigTree/iTOL
   - Tables: Excel matrices → figures
   - QC: CSV → supplementary data

---

*Last updated: February 2026*
