# vSNP GUI Workflow Diagram

## Complete Analysis Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                         INITIAL SETUP (One-Time)                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  1. Configure Settings                                               │
│     ├─ vSNP3 path                                                   │
│     ├─ Projects root                                                │
│     ├─ Conda environment                                            │
│     └─ Save                                                          │
│                                                                       │
│  2. Run Preflight Check                                             │
│     └─ Verify: pandas, biopython installed                           │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         CREATE PROJECT                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Enter project name → Create                                         │
│                                                                       │
│  Creates directory structure:                                        │
│     ProjectName/                                                     │
│     ├─ download/     (FASTQ files)                                  │
│     ├─ step1/        (Alignments + VCFs)                            │
│     └─ step2/        (SNP matrices + trees)                         │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         ADD INPUT FILES                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Choose ONE of three methods:                                        │
│                                                                       │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐ │
│  │  Link Local      │  │  Upload/Drag     │  │  SRA Download    │ │
│  │  Files           │  │  & Drop          │  │                  │ │
│  ├──────────────────┤  ├──────────────────┤  ├──────────────────┤ │
│  │ Enter path to    │  │ Select or drag   │  │ Enter accessions │ │
│  │ FASTQ directory  │  │ FASTQ.GZ files   │  │ (SRR/ERR/DRR)   │ │
│  │                  │  │                  │  │                  │ │
│  │ Creates symlinks │  │ Copies files     │  │ Downloads from   │ │
│  │ (saves space)    │  │ to project       │  │ NCBI/ENA        │ │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘ │
│                                                                       │
│  Result: FASTQ files in ProjectName/download/                       │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    STEP 1: ALIGNMENT & SNP CALLING                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │  1a. SETUP                                               │        │
│  │                                                           │        │
│  │  Click "Setup" → Creates sample directories:             │        │
│  │                                                           │        │
│  │  step1/                                                  │        │
│  │  ├─ Sample1/                                             │        │
│  │  │  ├─ Sample1_R1.fastq.gz (symlink)                    │        │
│  │  │  └─ Sample1_R2.fastq.gz (symlink)                    │        │
│  │  ├─ Sample2/                                             │        │
│  │  │  ├─ Sample2_R1.fastq.gz                               │        │
│  │  │  └─ Sample2_R2.fastq.gz                               │        │
│  │  └─ ...                                                  │        │
│  └─────────────────────────────────────────────────────────┘        │
│                             │                                         │
│                             ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │  1b. SELECT REFERENCE                                    │        │
│  │                                                           │        │
│  │  Options:                                                │        │
│  │  ┌──────────────────────────────────────────┐           │        │
│  │  │ ○ Auto-detect (best match)               │           │        │
│  │  │   └─ Uses Sourmash                       │           │        │
│  │  │                                           │           │        │
│  │  │ ○ Manual selection:                      │           │        │
│  │  │   ├─ Mycobacterium_AF2122               │           │        │
│  │  │   ├─ mtbc0_v1.1                          │           │        │
│  │  │   ├─ NC_045512_wuhan-hu-1                │           │        │
│  │  │   ├─ Brucella_abortus1                   │           │        │
│  │  │   └─ ...                                 │           │        │
│  │  └──────────────────────────────────────────┘           │        │
│  └─────────────────────────────────────────────────────────┘        │
│                             │                                         │
│                             ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │  1c. RUN                                                 │        │
│  │                                                           │        │
│  │  For each sample:                                        │        │
│  │  1. Align reads (BWA-MEM)                               │        │
│  │  2. Call variants (FreeBayes, QUAL≥20)                  │        │
│  │  3. Generate VCF files                                  │        │
│  │  4. Calculate QC metrics                                │        │
│  │  5. Annotate SNPs                                        │        │
│  │                                                           │        │
│  │  Options:                                                │        │
│  │  ☐ Debug mode (keep intermediates)                      │        │
│  │                                                           │        │
│  │  Monitor: Live Logs panel                               │        │
│  └─────────────────────────────────────────────────────────┘        │
│                             │                                         │
│                             ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │  OUTPUTS (per sample):                                   │        │
│  │                                                           │        │
│  │  step1/Sample1/                                          │        │
│  │  ├─ Sample1_zc.vcf                   ← For Step 2       │        │
│  │  ├─ Sample1_filtered_hapall.vcf                          │        │
│  │  ├─ Sample1_stats.xlsx               ← QC metrics       │        │
│  │  ├─ alignment_*/                                         │        │
│  │  │  ├─ Sample1_nodup.bam             ← For IGV          │        │
│  │  │  └─ Sample1_nodup.bam.bai                            │        │
│  │  └─ run_step1.log                    ← Debug log        │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         QUALITY CONTROL REVIEW                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  QC Summary Table:                                                   │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Sample  │ AvgDepth │ ZeroCov │ Dup% │ R1Q20 │ R2Q20 │ SNPs│   │
│  ├─────────┼──────────┼─────────┼──────┼───────┼───────┼─────┤   │
│  │ Sample1 │   45X    │  2.1%   │ 15%  │  92%  │  88%  │ 234 │✓  │
│  │ Sample2 │   38X ⚠  │  3.4%   │ 22%  │  85%  │  82%  │ 245 │✓  │
│  │ Sample3 │   52X    │  1.8%   │ 18%  │  94%  │  91%  │ 238 │✓  │
│  │ Sample4 │   28X ⚠  │ 12.3% ⚠ │ 85%⚠ │  45%⚠ │  43%⚠ │ 489⚠│✗  │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  Decision Matrix:                                                    │
│  ┌──────────────────────────────────────────────┐                   │
│  │  Metric             Good      ⚠️ Flag        │                   │
│  ├──────────────────────────────────────────────┤                   │
│  │  Average Depth      ≥40X      <40X          │                   │
│  │  Zero Coverage      <5%       >10%          │                   │
│  │  Duplicates         <50%      >80%          │                   │
│  │  R1/R2 Q20          >50%      <50%          │                   │
│  │  Quality SNPs       Varies    >10% genome   │                   │
│  └──────────────────────────────────────────────┘                   │
│                                                                       │
│  Actions:                                                            │
│  ┌────────────────────────────────────────────────────────┐         │
│  │ ☑ Show only flagged samples                            │         │
│  │ ☐ Sample4  [Exclude from Step 2]                       │         │
│  │                                                          │         │
│  │ [Download CSV] [Save Exclusions] [Refresh]             │         │
│  └────────────────────────────────────────────────────────┘         │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                STEP 2: SNP MATRIX & PHYLOGENETIC TREES               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │  2a. SETUP                                               │        │
│  │                                                           │        │
│  │  Links *_zc.vcf files to step2/vcf_source/:             │        │
│  │                                                           │        │
│  │  step2/vcf_source/                                       │        │
│  │  ├─ Sample1_zc.vcf (symlink)                            │        │
│  │  ├─ Sample2_zc.vcf (symlink)                            │        │
│  │  ├─ Sample3_zc.vcf (symlink)                            │        │
│  │  └─ ...                                                  │        │
│  │                                                           │        │
│  │  ⚠️  Reference Lock Check:                              │        │
│  │  ✓ Single reference: Ready                              │        │
│  │  ✗ Mixed references: Must split projects                │        │
│  └─────────────────────────────────────────────────────────┘        │
│                             │                                         │
│                             ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │  2b. RUN                                                 │        │
│  │                                                           │        │
│  │  Processing steps:                                       │        │
│  │  1. Compile VCF files                                   │        │
│  │  2. Identify SNP positions (AC=1)                       │        │
│  │  3. Build SNP matrices (3 versions)                     │        │
│  │  4. Generate phylogenetic tree (RAxML)                  │        │
│  │  5. Annotate SNPs with genes                            │        │
│  │  6. Group samples (if defining SNPs)                    │        │
│  │                                                           │        │
│  │  Note: Can re-run without re-running Step 1!            │        │
│  └─────────────────────────────────────────────────────────┘        │
│                             │                                         │
│                             ▼                                         │
│  ┌─────────────────────────────────────────────────────────┐        │
│  │  OUTPUTS:                                                │        │
│  │                                                           │        │
│  │  step2/                                                  │        │
│  │  ├─ SNP_MATRICES (3 versions):                          │        │
│  │  │  ├─ *_sort_table.xlsx         (Cascading)            │        │
│  │  │  ├─ *_alt_sort_table.txt      (Alt cascading)        │        │
│  │  │  └─ *_position_sort_table.txt (Position-sorted)      │        │
│  │  │                                                        │        │
│  │  ├─ PHYLOGENETIC TREE:                                  │        │
│  │  │  └─ *_tree.tre                (Newick format)        │        │
│  │  │                                                        │        │
│  │  ├─ VISUALIZATIONS:                                     │        │
│  │  │  └─ step2_summary.html        (Interactive)          │        │
│  │  │                                                        │        │
│  │  ├─ GROUPS (if defining SNPs):                          │        │
│  │  │  ├─ Group1/                                          │        │
│  │  │  │  ├─ SNP tables                                    │        │
│  │  │  │  └─ FASTA alignments                              │        │
│  │  │  └─ Group2/                                          │        │
│  │  │                                                        │        │
│  │  └─ ARCHIVE:                                            │        │
│  │     └─ *.zip                      (Reproducibility)     │        │
│  └─────────────────────────────────────────────────────────┘        │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         INTERPRET RESULTS                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  SNP MATRIX INTERPRETATION                                   │   │
│  │                                                               │   │
│  │  Position:  123    456    789    ...                         │   │
│  │  ─────────────────────────────────────                       │   │
│  │  Sample1     A      C      G                                 │   │
│  │  Sample2     T      C      G     ← SNP at 123               │   │
│  │  Sample3     A      Y      G     ← Mixed call at 456        │   │
│  │  Sample4     A      -      G     ← No coverage at 456       │   │
│  │                                                               │   │
│  │  Legend:                                                      │   │
│  │  A,C,G,T = Nucleotides                                      │   │
│  │  R,Y,M,K,S,W = Mixed calls (IUPAC)                          │   │
│  │  N = Low quality/indel                                       │   │
│  │  - = Zero coverage                                           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  PHYLOGENETIC TREE INTERPRETATION                            │   │
│  │                                                               │   │
│  │              ┌── Sample1                                     │   │
│  │         ┌────┤                                               │   │
│  │         │    └── Sample2  (2 SNPs apart)                    │   │
│  │    ─────┤                                                    │   │
│  │         │    ┌── Sample3                                     │   │
│  │         └────┤                                               │   │
│  │              └── Sample4  (15 SNPs from Sample3)            │   │
│  │                                                               │   │
│  │  Branch length = SNP distance                                │   │
│  │  Clusters = closely related isolates                         │   │
│  │                                                               │   │
│  │  ⚠️  Mixed calls not shown in tree - check SNP matrix!      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         EXPORT & PUBLISH                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Publication-Ready Outputs:                                          │
│                                                                       │
│  ├─ QC Summary CSV → Supplementary Table 1                          │
│  ├─ SNP Matrix Excel → Supplementary Table 2                        │
│  ├─ Tree file (.tre) → FigTree → Figure 1                          │
│  ├─ Step 2 HTML → Internal review                                   │
│  └─ Metadata + Exclusions → Methods section                         │
│                                                                       │
│  External Tools:                                                     │
│  ├─ FigTree / iTOL → Tree visualization                            │
│  ├─ IGV → Alignment inspection                                      │
│  └─ Excel/R → SNP matrix analysis                                   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Data Flow Summary

```
                 INPUT                PROCESSING               OUTPUT

FASTQ Files  ───────────▶  Step 1      ───────────▶  VCF Files
(Download,                 (Per Sample)              QC Metrics
 Link, or                  - Align                   BAM Files
 Upload)                   - Call SNPs
                          - Annotate
                              │
                              │
                              ▼
                          QC Review   ───────────▶  Pass/Fail
                          (User)                    Exclusions
                              │
                              │
                              ▼
VCF Files    ───────────▶  Step 2      ───────────▶  SNP Matrices
(from Step 1)              (All Samples)            Phylo Trees
                          - Compile                 HTML Summary
                          - Matrix                  Groups
                          - Tree
```

---

## File Structure After Complete Analysis

```
ProjectName/
│
├── download/
│   ├── Sample1_R1.fastq.gz
│   ├── Sample1_R2.fastq.gz
│   ├── Sample2_R1.fastq.gz
│   └── Sample2_R2.fastq.gz
│
├── step1/
│   ├── Sample1/
│   │   ├── Sample1_R1.fastq.gz (symlink)
│   │   ├── Sample1_R2.fastq.gz (symlink)
│   │   ├── Sample1_zc.vcf ────────────┐
│   │   ├── Sample1_stats.xlsx         │
│   │   ├── alignment_*/                │
│   │   │   ├── Sample1_nodup.bam      │
│   │   │   └── Sample1_nodup.bam.bai  │
│   │   └── run_step1.log              │
│   │                                    │
│   └── Sample2/                        │
│       ├── Sample2_zc.vcf ────────────┤
│       └── ...                         │
│                                        │
└── step2/                               │
    ├── vcf_source/                      │
    │   ├── Sample1_zc.vcf (symlink)◄───┘
    │   └── Sample2_zc.vcf (symlink)
    │
    ├── ProjectName_sort_table.xlsx           ← Primary SNP matrix
    ├── ProjectName_alt_sort_table.txt
    ├── ProjectName_position_sort_table.txt
    ├── ProjectName_tree.tre                  ← Phylogenetic tree
    ├── step2_summary.html                    ← Interactive report
    │
    ├── Group1/                               ← If defining SNPs exist
    │   ├── SNP tables
    │   └── FASTA files
    │
    └── vcf_starting_files.zip                ← Reproducibility archive
```

---

## Decision Tree: Reference Selection

```
                    Start
                      │
                      ▼
            Do you know the organism?
                 /         \
               YES          NO
                │            │
                │            ▼
                │     Use AUTO-DETECT
                │            │
                ▼            │
      Is it in vSNP3         │
      reference list?        │
         /        \          │
       YES        NO         │
        │          │         │
        │          ▼         │
        │    Use Mashtree/   │
        │    kSNP to find    │
        │    closest ref     │
        │          │         │
        ▼          │         │
    Select         │         │
    Specific   ◄───┴─────────┘
    Reference
        │
        ▼
    Run Step 1
        │
        ▼
    Check QC Metrics
        │
        ▼
    Zero Coverage <5%?
       /          \
     YES          NO
      │            │
      │            ▼
      │      Try Different
      │      Reference
      │            │
      │            ▼
      │      Re-run Step 1
      │            │
      ▼            ▼
   Proceed to
   Step 2
```

---

*Visual guide for the complete vSNP GUI analysis workflow*
