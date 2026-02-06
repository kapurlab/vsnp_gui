# vSNP GUI - Comprehensive User Guide

**Complete guide for whole genome SNP analysis using vSNP3**

Based on: Stuber TP, et al. (2024). "vSNP: a SNP pipeline for the generation of transparent SNP matrices and phylogenetic trees." BMC Genomics. [Read the paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC11143592/)

---

## Documentation Index

This comprehensive guide covers all aspects of using the vSNP GUI. For quick reference:

- **First time users:** Start here with [Quick Start](#quick-start)
- **Experienced users:** See [QUICK_REFERENCE.md](QUICK_REFERENCE.md)
- **Workflow overview:** See [WORKFLOW_DIAGRAM.md](WORKFLOW_DIAGRAM.md)
- **Having problems?:** See [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- **Alpha testers:** See [ALPHA_TESTING.md](ALPHA_TESTING.md)

---

## Table of Contents

1. [Introduction & Background](#1-introduction--background)
2. [Installation & Setup](#2-installation--setup)
3. [Project Management](#3-project-management)
4. [Input Data Methods](#4-input-data-methods)
5. [Step 1: Alignment & Variant Calling](#5-step-1-alignment--variant-calling)
6. [Quality Control & Metrics](#6-quality-control--metrics)
7. [Step 2: SNP Matrices & Trees](#7-step-2-snp-matrices--trees)
8. [Interpreting Results](#8-interpreting-results)
9. [Best Practices & Guidelines](#9-best-practices--guidelines)
10. [Advanced Topics](#10-advanced-topics)

---

## 1. Introduction & Background

### What is vSNP?

**vSNP (validate SNPs)** is a bioinformatics pipeline developed by the USDA for high-resolution single nucleotide polymorphism (SNP) analysis from whole genome sequencing data.

**Key Features:**
- ISO 17025 accredited by NVSL (2017)
- Transparent SNP calling with manual review capability
- Two-step modular workflow (Alignment → Phylogeny)
- Designed for diagnostic laboratories and outbreak investigation
- Works with minimal computational resources (4 cores, 8GB RAM)

**Applications:**
- Disease outbreak investigations
- Bacterial/viral typing and surveillance
- Phylogenetic analysis of pathogens
- SNP-based epidemiology
- Contact tracing and transmission studies

**Supported Organisms:**
- Mycobacterium bovis & M. tuberculosis complex
- Brucella species (all biovars)
- SARS-CoV-2 and other viruses
- African swine fever virus
- Highly pathogenic avian influenza
- Newcastle disease virus
- Any organism with a reference genome

### Why Use the vSNP GUI?

The GUI provides:
- ✅ Easy-to-use interface (no command line required)
- ✅ Visual project management
- ✅ Real-time progress monitoring
- ✅ Interactive quality control review
- ✅ One-click SRA data download
- ✅ Integrated result visualization

---

## 2. Installation & Setup

### Prerequisites

Before first use, ensure you have:

**1. vSNP3 Software:**
```bash
conda create -n vsnp3 python=3.9
conda activate vsnp3
conda install vsnp3 -c conda-forge -c bioconda
```

**2. Required Python packages:**
```bash
conda install -n vsnp3 pandas biopython -c conda-forge
```

**Important:** vSNP3 requires pandas <2.0. If you have pandas >=2.0:
```bash
conda install -n vsnp3 pandas biopython -c conda-forge
```

**3. Reference Genomes:**
- vSNP3 includes some references by default
- Additional references can be added via `vsnp3_path_adder.py`
- References are stored in `vsnp3/dependencies/`

### First-Time Configuration

**Step 1: Launch the GUI**
```bash
cd vsnp_gui
./start_gui.sh
```
Or double-click `vSNP.app` (macOS)

**Step 2: Configure Settings**

Navigate to the **Settings** panel and enter:

| Setting | Example | Description |
|---------|---------|-------------|
| **vSNP3 path** | `/Users/yourname/vsnp3` | Directory containing `bin/vsnp3_step1.py` |
| **Projects root** | `/Users/yourname/vsnp3/projects` | Where project folders will be created |
| **vSNP3 path** | `~/miniconda3/envs/vsnp3` | Conda environment directory containing vSNP3 scripts and Python runtime |

Click **Save Settings**

**Step 3: Run Preflight Check**

1. Click the **Preflight** button
2. Verify output shows:
   ```
   Checked: pandas, Bio | Missing: none
   ```
3. If packages are missing, install them (see Prerequisites above)
4. ✅ Green "All good" indicator means you're ready!

**Troubleshooting Setup:**
- If preflight fails, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- For alpha testing setup, see [ALPHA_TESTING.md](ALPHA_TESTING.md)

---

## 3. Project Management

### Creating a New Project

**What is a project?**
A project is a self-contained analysis workspace containing:
- Input FASTQ files
- Step 1 outputs (alignments, VCFs, QC metrics)
- Step 2 outputs (SNP matrices, phylogenetic trees)

**How to create:**
1. Navigate to **Projects** panel
2. Enter a descriptive project name
3. Click **Create**

**Naming Guidelines:**
- ✅ Use descriptive names: `TB_Outbreak_Hospital_Feb2026`
- ✅ Include organism: `Brucella_Farm_Survey_2026`
- ✅ Use underscores (not spaces): `My_Project` not `My Project`
- ❌ Avoid special characters: `@#$%` etc.

### Project Directory Structure

Each project automatically creates:

```
ProjectName/
├── download/          # Raw FASTQ files
├── step1/            # Per-sample analysis
│   ├── Sample1/      # Individual sample folder
│   │   ├── *.fastq.gz (symlinks to download/)
│   │   ├── *_zc.vcf (zero-coverage VCF)
│   │   ├── *_stats.xlsx (QC metrics)
│   │   ├── alignment_*/ (BAM files)
│   │   └── run_step1.log
│   └── Sample2/
└── step2/            # Multi-sample analysis
    ├── vcf_source/   # VCF files from Step 1
    ├── *.xlsx        # SNP matrices
    ├── *.tre         # Phylogenetic trees
    └── *.html        # Summary reports
```

### Managing Projects

**Archive a Project:**
1. Click **Archive** next to project name
2. Project renamed to `{ProjectName}_archived`
3. Frees up space in active projects list
4. Data preserved for long-term storage

**Delete a Project:**
1. Click **Delete** next to project name
2. ⚠️ **Warning:** This is permanent! Cannot be undone!
3. All files in the project directory are removed
4. Consider archiving first

**Best Practices:**
- Archive completed projects regularly
- Export important results before deleting
- Keep one project per organism/reference
- Document project purpose in project name

---

## 4. Input Data Methods

The GUI supports three methods for adding FASTQ sequencing data:

### Method 1: Link Local Files

**When to use:** You have FASTQ files already on your computer/server

**Steps:**
1. Navigate to **Inputs** → **Bring Your Own FASTQ**
2. Enter the **full path** to your FASTQ directory
   - Example: `/Users/yourname/sequencing_data/run_2026_02/`
   - ⚠️ Use absolute paths, not relative (`~/` may not work)
3. Click **Link Local Files**

**What happens:**
- GUI creates **symbolic links** to `*.fastq.gz` files
- Original files remain in place (not copied)
- Saves disk space
- Fast (no file copying)

**File Requirements:**
- Must be gzip-compressed: `*.fastq.gz`
- Paired-end reads must follow naming convention:
  - `SampleName_R1.fastq.gz` + `SampleName_R2.fastq.gz`
  - OR `SampleName_1.fastq.gz` + `SampleName_2.fastq.gz`
- Sample name = everything before `_R1`/`_R2` or `_1`/`_2`

**Example file names:**
```
✅ Good:
   Sample_A_R1.fastq.gz + Sample_A_R2.fastq.gz
   ERR123456_1.fastq.gz + ERR123456_2.fastq.gz

❌ Bad:
   Sample_A_forward.fq (not gzipped, wrong suffix)
   SampleB.R1.fastq.gz (period instead of underscore)
```

### Method 2: Upload / Drag & Drop

**When to use:** You want to copy files into the project

**Steps:**
1. Navigate to **Inputs** → **Upload / Drag & Drop**
2. Either:
   - **Click** "Choose Files" and select FASTQ files
   - **Drag** files from Finder directly onto the dropzone

**What happens:**
- Files are **copied** to `download/` directory
- Original files remain unchanged
- Uses more disk space (files duplicated)
- Slower for large files

**When to prefer this method:**
- Source files on removable drive
- Want project to be self-contained
- Source location may change/disappear

### Method 3: SRA Download

**When to use:** Your data is in NCBI Sequence Read Archive

**Steps:**
1. Navigate to **Inputs** → **SRA Download**
2. Enter SRA accessions, **one per line**:
   ```
   SRR10321138
   SRR10321141
   SRR10321143
   ```
3. (Optional) Enter subfolder name:
   - Example: `2026-02-01_batch1`
   - Organizes downloads by date/batch
4. Click **Download**

**Supported Accession Types:**
- **Run** (SRR, ERR, DRR): Downloads individual sequencing run
- **Experiment** (SRX, ERX, DRX): Expands to all runs in experiment
- **Sample** (SRS, ERS, DRS): Expands to all runs from that sample

**Download Process:**
1. GUI attempts **SRA Toolkit** (`fasterq-dump`) first
2. Falls back to **ENA FTP/HTTPS** if SRA Toolkit fails
3. Progress shown in **Live Logs** panel
4. Job status indicator shows when complete

**Troubleshooting Downloads:**
- Check accession numbers are correct (copy from NCBI website)
- Verify internet connection
- Large files may take 10-30 minutes
- Check Live Logs for specific error messages

---

## 5. Step 1: Alignment & Variant Calling

### What Step 1 Does

Step 1 processes **each sample individually** to:

1. **Align** reads to reference genome using BWA-MEM
2. **Call variants** using FreeBayes
3. **Filter** SNPs (quality score QUAL ≥ 20)
4. **Generate VCF files:**
   - Standard VCF with all variants
   - **Zero-coverage VCF** (positions with no reads + SNPs)
5. **Calculate QC metrics** for sequencing and alignment
6. **Annotate SNPs** with gene/position information

### Step 1a: Setup

**Purpose:** Organize FASTQ files into sample-specific folders

**Steps:**
1. Click **Setup** button in Step 1 panel
2. GUI creates directory structure:
   ```
   step1/
   ├── Sample1/
   │   ├── Sample1_R1.fastq.gz (symlink)
   │   └── Sample1_R2.fastq.gz (symlink)
   └── Sample2/
       ├── Sample2_R1.fastq.gz
       └── Sample2_R2.fastq.gz
   ```

**File Pairing Logic:**
- Pairs R1/R2 files by matching prefix
- Example: `ERR123456_R1.fastq.gz` pairs with `ERR123456_R2.fastq.gz`
- Sample name = `ERR123456` (everything before `_R1` or `_R2`)

**Verification:**
- Check **Step 1 Status** → **Samples** list
- Should show all expected samples
- If samples missing, check FASTQ file naming

### Step 1b: Reference Selection

**Critical Decision:** Reference genome must be closely related to your samples!

**Option 1: Auto-detect (Recommended)**
- Select **"Auto-detect (best match)"** from dropdown
- vSNP uses **Sourmash** k-mer matching
- Automatically selects closest reference
- Best for unknown/mixed samples
- Fastest option

**Option 2: Manual Selection**
- Choose specific reference from dropdown
- Examples:
  - `Mycobacterium_AF2122` - M. bovis AF2122/97
  - `mtbc0_v1.1` - M. tuberculosis complex
  - `NC_045512_wuhan-hu-1` - SARS-CoV-2 Wuhan
  - `Brucella_abortus1` - B. abortus biovar 1

**Reference Selection Guidelines:**

| SNP Distance | Alignment Quality | Recommendation |
|--------------|-------------------|----------------|
| <500 SNPs | Excellent | ✅ Optimal |
| 500-1,000 SNPs | Good | ✅ Acceptable |
| 1,000-5,000 SNPs | Poor | ⚠️ Try closer reference |
| >5,000 SNPs | Very Poor | ❌ Wrong reference/organism |

**For bacterial genomes (~4-5 Mb):**
- SNPs >10% of genome size (>400,000 bp) = problematic
- Typically aim for <1,000 SNPs

**What if reference is wrong?**
- No problem! Re-run Step 1 with different reference
- Original FASTQ files unchanged
- Step 1 is independent for each sample

### Step 1c: Run

**Steps:**
1. Verify reference is selected
2. (Optional) Enable **Debug mode:**
   - ☑ Check "Debug (keep intermediates, skip cleanup)"
   - Keeps all intermediate files
   - Useful for troubleshooting
   - Uses more disk space
3. Click **Run** button

**What Happens:**
```
For each sample:
  1. BWA-MEM alignment → BAM file
  2. Remove PCR duplicates → *_nodup.bam
  3. FreeBayes variant calling → VCF files
  4. Filter SNPs (QUAL ≥ 20)
  5. Identify zero-coverage regions
  6. Annotate SNPs with gene info
  7. Calculate QC statistics → *_stats.xlsx
  8. Generate final VCF files
```

**Monitoring Progress:**
- Watch **Live Logs** panel for real-time output
- Check **Step 1 Status** → **Samples** for per-sample status:
  - 🟢 **Complete**: Finished successfully
  - 🟡 **Running**: Currently processing
  - 🔴 **Error**: Failed (click **View log**)
  - ⚪ **Not started**: Waiting in queue

**Typical Processing Times:**
| Genome Type | Size | Coverage | Time/Sample |
|-------------|------|----------|-------------|
| Bacterial | ~4 Mb | 50X | 5-15 min |
| Large bacterial | >8 Mb | 100X | 15-30 min |
| Viral | <50 Kb | 1000X | 2-5 min |

*Times assume 4 CPU cores, SSD storage*

### Step 1 Outputs

For each sample, you get:

**1. Zero-Coverage VCF (`*_zc.vcf`)** ⭐ Most important
- Contains SNP positions + zero-coverage positions
- **This file is used in Step 2!**
- Format: VCF with positions having no reads marked

**2. Quality Metrics (`*_stats.xlsx`)**
- Excel file with comprehensive QC data
- Used for QC Summary table
- Contains ~30 metrics

**3. BAM Files (`*_nodup.bam` + `.bai`)**
- Aligned reads (duplicates removed)
- Can view in IGV (Integrated Genomics Viewer)
- Inspect alignment quality, coverage, SNP positions

**4. Annotated VCF (`*_filtered_hapall_annotated.vcf`)**
- All SNPs with gene annotations
- QUAL ≥ 20 filter applied
- Human-readable variant descriptions

**5. Log File (`run_step1.log`)**
- Complete processing log
- Useful for debugging
- Contains all command outputs

**Optional Outputs (if enabled):**
- `unmapped_reads/` - Reads that didn't align
- `sourmash/` - Reference matching results

---

## 6. Quality Control & Metrics

### QC Summary Table

After Step 1 completes, the **QC Summary** panel displays metrics for all samples.

**How to access:**
1. Step 1 must be complete (all samples green status)
2. Click **Refresh** in QC Summary panel
3. Table populates with metrics from `*_stats.xlsx` files

### Critical Quality Metrics

#### 1. Average Depth of Coverage

**What it measures:** Average number of reads covering each position in the reference genome

**Thresholds:**
- ✅ **Good:** ≥40X for bacteria, ≥100X for viruses
- ⚠️ **Low:** 20-40X (marginal, may miss some SNPs)
- ❌ **Poor:** <20X (insufficient for confident calling)

**Interpretation:**
- Higher depth = more confident SNP calls
- Low depth → may miss true SNPs (false negatives)
- Very high depth (>200X) usually fine, but check for duplicates

**Action if low:**
- Accept lower confidence
- Note in report/metadata
- Consider re-sequencing if critical sample

#### 2. Percent Reference with Zero Coverage

**What it measures:** Percentage of reference genome with no aligned reads

**Thresholds:**
- ✅ **Good:** <5%
- ⚠️ **Moderate:** 5-10% (may be acceptable for distant references)
- ❌ **High:** >10% (poor alignment, likely wrong reference)

**Interpretation:**
- Low zero coverage → good alignment to reference
- High zero coverage → reference too distant OR poor sequencing

**Actionif high:**
1. Check SNP count - if also very high, wrong reference
2. Try auto-detect or closer reference
3. Re-run Step 1 with new reference
4. Consider reference-free phylogeny (Mashtree, kSNP)

#### 3. Duplicate Percent of Mapped Reads

**What it measures:** PCR or optical duplicates (removed from analysis)

**Thresholds:**
- ✅ **Good:** <50%
- ⚠️ **Moderate:** 50-80% (library complexity issues)
- ❌ **High:** >80% (poor library quality)

**Interpretation:**
- Duplicates are automatically removed
- High duplicates → limited unique DNA molecules
- Usually due to low input DNA or excessive PCR cycles

**Action if high:**
- Usually acceptable (duplicates already removed)
- Note for sequencing facility feedback
- If >90%, check actual depth after duplicate removal

#### 4. R1/R2 Passing Q20

**What it measures:** Percentage of bases with quality score ≥20 (99% accuracy)

**Thresholds:**
- ✅ **Good:** >50% for both R1 and R2
- ⚠️ **Moderate:** 30-50% (marginal quality)
- ❌ **Poor:** <30% (poor sequencing run)

**Interpretation:**
- Q20 = 1% error rate
- R2 typically lower than R1 (normal)
- Poor Q20 → many bases filtered out

**Action if low:**
- Check if sequencing run had issues
- Consider re-sequencing
- Check for adapter contamination (trim if needed)

#### 5. Quality SNPs

**What it measures:** Number of SNPs called with QUAL ≥ 20

**Expected Range:**
- **Varies widely** by organism and reference
- Closely related samples: 0-100 SNPs
- Moderately related: 100-1,000 SNPs
- Distantly related: >1,000 SNPs

**Rule of Thumb for Bacteria:**
- SNPs >10% of genome size = problematic
- Example: M. bovis (~4.3 Mb genome)
  - 10% = 430,000 bases
  - Anything >5,000 SNPs is getting distant
  - >10,000 SNPs = wrong reference

**Action if very high:**
1. Check zero coverage% - if also high, confirms wrong reference
2. Try closer reference
3. Verify sample identity (mix-up?)
4. Run Kraken to check for contamination

### Using the QC Summary Interface

**Filter Flagged Samples:**
1. Check ☑ **Show only flagged samples**
2. Table shows only samples failing QC thresholds
3. Review why each sample is flagged

**Exclude Poor Quality Samples:**
1. Check boxes in **Exclude** column for samples to remove
2. Click **Save Exclusions**
3. Creates `step2/remove_from_analysis.xlsx`
4. These samples won't be included in Step 2 analysis

**Download QC Data:**
- Click **Download CSV** button
- Exports complete QC table
- Use for reports, publications, or further analysis in Excel/R

**View Sample-Specific Logs:**
- Click **View log** button next to any sample
- Opens detailed `run_step1.log` in viewer
- Review processing steps and any warnings/errors

---

## 7. Step 2: SNP Matrices & Trees

### What Step 2 Does

Step 2 performs **multi-sample comparative analysis**:

1. **Collects** zero-coverage VCF files from all Step 1 samples
2. **Identifies** SNP positions where allele count (AC) = 1
3. **Compiles** SNP matrix with all samples × all SNP positions
4. **Converts** mixed SNPs to IUPAC ambiguity codes
5. **Builds** phylogenetic tree using RAxML (GTR-CATI model)
6. **Generates** three differently-sorted SNP tables
7. **Groups** samples by defining SNPs (if applicable)
8. **Annotates** SNPs with gene information

**Key Advantage:** Step 2 can be re-run multiple times without re-running Step 1!
- Modify exclusions
- Adjust quality thresholds
- Filter problematic SNP positions
- No need to re-align reads

### Step 2a: Setup

**Purpose:** Link VCF files from Step 1 to Step 2 workspace (Step 1 only mode)

### Step 2a (Alternative): Build a Custom VCF Set

Use this when you want to compare against external VCFs (e.g., reference panels).

**Steps:**
1. In **VCF Sources**, paste one or more folders (one per line)
2. Choose a reference (must match VCFs)
3. Click **Build VCF set**
4. Then **Run** Step 2

#### VCF Lite Pack (built‑in)
A small demo pack is included at `sample_data/vcf_lite/`.
Use preset **“VCF Lite Pack (repo)”** to auto‑fill sources and reference.

**Steps:**
1. Click **Setup** button in Step 2 panel
2. GUI links `*_zc.vcf` files to `step2/vcf_source/`:
   ```
   step2/vcf_source/
   ├── Sample1_zc.vcf (symlink)
   ├── Sample2_zc.vcf (symlink)
   └── Sample3_zc.vcf (symlink)
   ```
3. Status message shows: `VCFs ready for Step 2: N (linked M)`

**Reference Lock Check:**

vSNP Step 2 requires all samples use the **same reference genome**.

- ✅ **Single reference detected:** Ready to proceed
  - Message: "Detected reference from Step 1: Mycobacterium_AF2122"
- ❌ **Mixed references detected:** Cannot run Step 2
  - Error: "Mixed references detected: Reference1, Reference2"
  - **Solution:** Create separate projects for each reference

**Why is this required?**
- SNP positions are relative to reference coordinates
- Different references = incompatible coordinate systems
- Tree building requires consistent alignment

### Step 2b: Run

**Steps:**
1. Verify reference matches Step 1 (auto-populated)
2. Click **Run** button
3. Monitor progress in **Live Logs**

**Processing Steps:**
```
1. Read all *_zc.vcf files
2. Identify positions with AC=1 (allele count = 1)
3. Build SNP position list
4. Extract calls for each sample at each SNP position
5. Handle mixed calls (convert to IUPAC codes)
6. Generate 3 SNP matrices (different sorting)
7. Build phylogenetic tree (RAxML)
8. Annotate SNPs with gene information
9. Group samples if defining SNPs present
10. Create HTML summary report
11. Archive VCF files for reproducibility
```

**Typical Processing Time:**
| Sample Count | SNP Positions | Time |
|--------------|---------------|------|
| 5-10 samples | 100-500 SNPs | 1-2 min |
| 10-50 samples | 500-2000 SNPs | 2-5 min |
| 50-100 samples | 2000-5000 SNPs | 5-15 min |

### Step 2 Outputs

**SNP Matrices (3 versions, same data, different sorting):**

**1. Cascading Matrix (`*_sort_table.xlsx`, `*_sort_table.txt`)**
- **Sorting:** Most common SNPs first → rare SNPs → unique SNPs
- **Purpose:** Reveals evolutionary patterns and population structure
- **Best for:**
  - Understanding relatedness
  - Identifying defining SNPs
  - Seeing selection pressure
  - Publication figures

**Example:**
```
SNP Position  Sample1  Sample2  Sample3  Sample4  Count
100          A        A        A        A        0 (all ref)
250          T        T        T        A        3 (common)
500          G        G        A        A        2 (moderate)
750          C        A        A        A        1 (unique)
```

**2. Alternative Cascading Matrix (`*_alt_sort_table.txt`)**
- **Sorting:** Designed to highlight problematic samples
- **Purpose:** Quality control, outlier detection
- **Best for:**
  - Identifying contamination
  - Finding co-infections (many mixed calls)
  - Spotting sequencing errors

**3. Position-Sorted Matrix (`*_position_sort_table.txt`)**
- **Sorting:** By reference genome position
- **Purpose:** Regional analysis
- **Best for:**
  - Inspecting specific genome regions
  - Hotspot identification
  - Gene-specific SNP analysis

**Phylogenetic Tree:**
- **File:** `*_tree.tre` (Newick format)
- View in: FigTree, iTOL, Dendroscope, R (ape package)
- Shows evolutionary relationships
- Branch lengths = SNP distances

**HTML Summary:**
- **File:** `step2_summary.html`
- Interactive visual report
- Open in any web browser
- Contains:
  - Sample summary table
  - SNP statistics
  - Quality metrics
  - Links to output files

**Group Folders (if defining SNPs exist):**
- Samples grouped by shared defining SNPs
- Each group gets folder with:
  - Group-specific SNP tables
  - FASTA alignment files
  - Statistics and summaries

**Reproducibility Archive:**
- **File:** `vcf_starting_files.zip`
- Contains:
  - All input VCF files
  - Parameters used
  - vSNP version info
- Enables exact replication of analysis

---

## 8. Interpreting Results

### Reading SNP Matrices

SNP matrices show nucleotide calls at variable positions:

**Matrix Structure:**
- **Rows:** Samples (one per sequencing run)
- **Columns:** SNP positions (genomic coordinates)
- **Cells:** Nucleotide calls or special codes

**Cell Values:**

| Symbol | Meaning | Interpretation |
|--------|---------|----------------|
| A, C, G, T | Nucleotide call | High-confidence base call |
| R | A or G (purine) | Mixed call ~50/50 |
| Y | C or T (pyrimidine) | Mixed call ~50/50 |
| M | A or C (amino) | Mixed call ~50/50 |
| K | G or T (keto) | Mixed call ~50/50 |
| S | C or G (strong) | Mixed call ~50/50 |
| W | A or T (weak) | Mixed call ~50/50 |
| N | Low quality or indel | Call below threshold |
| - | Zero coverage | No reads at position |

**Example Matrix:**
```
Position     100    250    500    750
Reference    A      C      G      T
───────────────────────────────────────
Sample1      A      C      G      T    (identical to reference)
Sample2      T      C      G      T    (SNP at position 100)
Sample3      T      Y      G      T    (SNP at 100, mixed at 250)
Sample4      T      C      -      T    (no coverage at 500)
Sample5      A      C      N      T    (low quality at 500)
```

**Interpretation:**
- **Sample 2 & 3** share SNP at position 100 (closely related)
- **Sample 3** has mixed call at 250 (possible co-infection)
- **Sample 4** missing data at position 500 (can't determine relationship there)
- **Sample 5** low confidence at 500 (treat as missing data)

### Understanding Mixed Calls (IUPAC Codes)

**What causes mixed calls?**

**Biological:**
- **Co-infection** - Two strains in same sample (bacterial/viral)
- **Within-host diversity** - Multiple variants circulating
- **Contamination** - Sample混杂 during processing

**Technical:**
- **Low coverage** - Insufficient reads to confidently call
- **Alignment artifacts** - Repetitive regions
- **Sequencing errors** - Random errors creating "mixture"

**How to interpret:**

**Few mixed calls (<5 per sample):**
- ✅ Likely sequencing noise or low coverage
- Usually safe to proceed
- Check if at same positions across samples (artifact)

**Moderate mixed calls (5-20 per sample):**
- ⚠️ Borderline - investigate
- Check coverage at those positions
- Review for pattern (random vs. clustered)

**Many mixed calls (>20 per sample):**
- ❌ Likely biological mixture or contamination
- Check cascading matrix for patterns
- Run Kraken for contamination check
- Consider excluding from phylogenetic tree
- Document in report

**Quality Thresholds:**
- ~95% alternate allele → Called as pure (A, C, G, or T)
- ~90% alternate allele → Called as mixed (IUPAC code)
- <threshold → Called as "N"
- Adjustable in Step 2 with `--qual` parameter

### Phylogenetic Tree Interpretation

**Tree Components:**

```
        ┌── Sample1
    ┌───┤
    │   └── Sample2  (2 SNPs apart)
────┤
    │       ┌── Sample3
    └───────┤
            └── Sample4  (15 SNPs from Sample3)
```

**Branch Lengths:**
- Length = number of SNPs
- Longer branch = more mutations
- Short branches = closely related

**Clades/Clusters:**
- Samples grouping together = recent common ancestor
- Outbreak clusters = very short branches
- Endemic diversity = longer branches

**Root:**
- Usually reference or outgroup
- Basal sample = earliest divergence

**Important Limitations:**

⚠️ **Trees cannot easily show mixed SNP calls!**
- IUPAC codes are ambiguous for tree building
- Mixed samples may have misleading placement
- **Always check SNP matrix** for samples with many mixed calls

**Best Practices:**
- Include outgroup for proper rooting
- Samples with >20 mixed calls → review carefully
- Compare tree topology with SNP matrix patterns
- Bootstrap values indicate node support (if calculated)

### Gene Annotations

SNP tables include annotation columns:

**Key Annotation Fields:**
- **Position:** Nucleotide position in reference genome
- **Gene:** Gene name (if in coding region)
- **Product:** Protein function/description
- **Effect:** Synonymous vs. non-synonymous
  - **Synonymous:** Silent mutation (same amino acid)
  - **Non-synonymous:** Changes amino acid
- **Map Quality:** Alignment quality score at position

**Interpretation:**
- **Non-synonymous SNPs:** May affect protein function
- **SNPs in essential genes:** May indicate selection pressure
- **Hotspot regions:** Many SNPs in same gene
- **Intergenic SNPs:** Between genes (regulatory?)

---

## 9. Best Practices & Guidelines

### Reference Selection Strategy

**Initial Unknown Samples:**
1. Use **Auto-detect** for 2-3 representative samples
2. Review QC metrics (zero coverage, SNP count)
3. If good (low zero coverage, reasonable SNPs), use for all
4. If poor, try alternative reference

**Ongoing Surveillance:**
- Use same reference for consistency
- Update only if new closer reference becomes available
- Document reference changes in metadata

**Multiple Organism Types:**
- Create separate projects per organism
- Don't mix M. bovis and M. tuberculosis in same run
- Different references = different projects

### Quality Control Workflow

**Step-by-Step QC:**

1. **After Step 1 Setup:**
   - Verify all expected samples present
   - Check R1/R2 pairing is correct

2. **During Step 1 Run:**
   - Monitor Live Logs for errors
   - Check sample status indicators

3. **After Step 1 Complete:**
   - Review QC Summary table
   - Filter flagged samples
   - Investigate outliers:
     - Very high/low SNP count
     - High zero coverage
     - Poor Q20 scores
   - Decide on exclusions
   - Document reasons for excluding samples

4. **Before Step 2:**
   - Verify reference lock (single reference)
   - Confirm VCF count matches expectations
   - Save exclusions if any

5. **After Step 2:**
   - Review SNP matrix for patterns
   - Check for samples with many mixed calls
   - Verify tree topology makes biological sense
   - Export results

### Data Management

**Organization:**
- One project per study/outbreak
- Descriptive names with dates
- Archive completed projects monthly
- Keep raw FASTQ in central location (link, don't copy)

**Backup Strategy:**
```bash
# Export important results before archiving
cp step2/*.xlsx results/
cp step2/*.tre results/
cp step2/*.html results/

# Then archive project
# Click "Archive" in GUI

# Periodic backup of all projects
tar -czf vsnp_projects_backup_$(date +%Y%m%d).tar.gz projects/
```

**Disk Space Management:**
- Use symbolic links for FASTQ (don't copy)
- Delete intermediate files if space limited:
  ```bash
  # Safe to delete after QC review:
  rm -rf step1/*/alignment_*/unmapped.bam
  rm -rf step1/*/sourmash/
  ```
- Archive old projects
- Compress FASTQ files (already .gz)

### Reproducibility & Documentation

**What to record:**
- vSNP3 version: `conda list vsnp3`
- Reference genome used
- QC thresholds applied
- Samples excluded (and why)
- Date of analysis
- Any manual filtering/curation

**For Publications:**
- SNP matrix (Excel) → Supplementary Table
- Tree file (.tre) → Figure (via FigTree)
- QC summary CSV → Methods section
- Document in Materials & Methods:
  ```
  "SNP analysis was performed using vSNP3 (Stuber et al. 2024).
   Reads were aligned to [Reference] using BWA-MEM, and variants
   called with FreeBayes (QUAL ≥ 20). Samples with >10% zero
   coverage or <40X average depth were excluded. Phylogenetic
   tree was built using RAxML with GTR-CATI model."
  ```

---

## 10. Advanced Topics

### Re-running Analyses

**Re-run Step 1 with different reference:**
```bash
# Original FASTQ files unchanged
# Just click new reference and Run again
# Previous results overwritten
```

**Re-run Step 2 with different parameters:**
```bash
# VCF files unchanged
# Can modify:
# - Exclusion list
# - Quality thresholds
# - Filters

# No need to re-run Step 1!
```

### Custom Filtering

**Exclude specific SNP positions:**
1. Open SNP matrix in Excel
2. Identify problematic positions (e.g., repetitive regions)
3. Delete those columns
4. Save as new file
5. No need to re-run vSNP - just use filtered table

**Filter by gene/region:**
1. Use position-sorted matrix
2. Extract SNPs in region of interest
3. Build focused tree for that region

### Integration with Other Tools

**View alignments in IGV:**
```bash
# Open IGV
# Genome → Load Genome from File → reference.fasta
# File → Load from File → step1/Sample1/alignment_*/Sample1_nodup.bam
# Navigate to SNP positions
# Inspect coverage, quality, mapping
```

**Build reference-free trees:**
```bash
# For very distant samples
# Use Mashtree or kSNP3

# Mashtree (fast):
conda activate mashtree
mashtree --numcpus 4 download/*.fastq.gz > tree.tre

# kSNP3 (slower, more accurate):
conda activate ksnp
# See vSNP3 docs/instructions/additional_tools.md
```

**Check for contamination:**
```bash
# Kraken2 + Krona
conda activate kraken
kraken2 --db /path/to/db \
        --threads 4 \
        --paired Sample_R1.fastq.gz Sample_R2.fastq.gz \
        --report kraken_report.txt

# Creates HTML with taxonomic breakdown
```

### Troubleshooting Complex Issues

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed solutions to:
- GUI startup failures
- Step 1/2 errors
- Poor alignment quality
- Reference selection problems
- Performance issues

### Getting Help

**Resources:**
- This guide: Comprehensive usage
- [QUICK_REFERENCE.md](QUICK_REFERENCE.md): One-page cheat sheet
- [WORKFLOW_DIAGRAM.md](WORKFLOW_DIAGRAM.md): Visual workflow
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md): Problem solving
- [ALPHA_TESTING.md](ALPHA_TESTING.md): Setup for testers

**External Resources:**
- vSNP3 Paper: https://pmc.ncbi.nlm.nih.gov/articles/PMC11143592/
- GitHub: https://github.com/USDA-VS/vSNP3
- Original vSNP: https://github.com/USDA-VS/vSNP/blob/master/docs/detailed_usage.md

---

## Appendix: Complete Workflow Checklist

**Initial Setup (Once):**
- [ ] Install vSNP3 in conda environment
- [ ] Install pandas, biopython
- [ ] Configure GUI settings
- [ ] Run preflight check (all good)

**Per-Project Workflow:**
- [ ] Create new project
- [ ] Add FASTQ files (link/upload/SRA)
- [ ] Run Step 1 Setup
- [ ] Select reference (auto-detect or manual)
- [ ] Run Step 1
- [ ] Review QC Summary
- [ ] Exclude poor quality samples
- [ ] Run Step 2 Setup
- [ ] Verify reference lock (single reference)
- [ ] Run Step 2
- [ ] Review SNP matrices
- [ ] Inspect phylogenetic tree
- [ ] Export results
- [ ] Document analysis parameters
- [ ] Archive project

**Quality Checks:**
- [ ] Average depth ≥40X
- [ ] Zero coverage <5%
- [ ] R1/R2 Q20 >50%
- [ ] SNP count reasonable for organism
- [ ] No samples with excessive mixed calls (>20)
- [ ] Tree topology makes biological sense

**Before Publication:**
- [ ] Export QC summary CSV
- [ ] Export SNP matrix (Excel)
- [ ] Export tree file (.tre)
- [ ] Create publication-quality tree figure
- [ ] Document methods (vSNP version, reference, QC thresholds)
- [ ] Archive analysis for reproducibility

---

**End of Comprehensive User Guide**

*For questions or issues not covered here, please consult the troubleshooting guide or create a GitHub issue.*

**Citation:**
Hicks, Stuber, Lantz, Torchetti, Robbe-Austerman. vSNP: a SNP pipeline for the generation of transparent SNP matrices and phylogenetic trees from whole genome sequencing data sets. BMC Genomics. 2024 Jun 1;25(1):548. PMID: 38822271
