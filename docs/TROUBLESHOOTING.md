# vSNP GUI Troubleshooting Guide

**Quick solutions to common issues**

---

## GUI Won't Start

### Issue: Backend fails to start

**Error:** `Address already in use` or `Port 8000 is in use`

**Solution:**
```bash
# Find what's using port 8000
lsof -i :8000

# Kill the process
kill -9 <PID>

# Or restart the GUI
./start_gui.sh
```

### Issue: Frontend fails to start

**Error:** `Port 5173 is in use`

**Solution:**
```bash
# Find what's using port 5173
lsof -i :5173

# Kill the process
kill -9 <PID>

# Or let Vite use another port (automatic)
```

### Issue: Missing dependencies

**Error:** `ModuleNotFoundError: No module named 'fastapi'`

**Solution:**
```bash
cd vsnp_gui/backend
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration Issues

### Issue: Preflight fails with missing packages

**Error:** `Missing: pandas` or `Missing: Bio`

**Solution:**
```bash
conda activate vsnp3  # or your environment name
conda install -n vsnp3 pandas biopython -c conda-forge
```

**Alternative (if not using conda):**
```bash
pip install pandas biopython
```

### Issue: vSNP3 scripts not found

**Error:** `vsnp3_step1.py: command not found`

**Possible causes:**
1. vSNP3 not installed
2. Wrong conda environment
3. Incorrect vSNP3 path in settings

**Solution:**
```bash
# Check if vSNP3 is installed
conda activate vsnp3
which vsnp3_step1.py

# If not found, install it:
conda install vsnp3 -c conda-forge -c bioconda

# Update GUI settings with correct path
# vSNP3 path should be the directory containing bin/vsnp3_step1.py
```

### Issue: References not showing up

**Symptoms:** Reference dropdown is empty or missing expected references

**Solution:**
1. Check vSNP3 path points to correct directory
2. Verify `dependencies/` folder exists in vSNP3 path
3. Check for `reference_options_paths.txt`:
   ```bash
   cat /path/to/vsnp3/dependencies/reference_options_paths.txt
   ```
4. Ensure reference folders exist at paths listed in file

---

## Step 1 Issues

### Issue: No samples created after Setup

**Symptoms:** Step 1 Setup runs but no sample folders appear

**Possible causes:**
1. No FASTQ files in download directory
2. Wrong file naming (not `*R1*.fastq.gz` / `*R2*.fastq.gz`)
3. Files not gzip compressed

**Solution:**
```bash
# Check download directory
ls -la download/

# Verify FASTQ file naming
# Good: Sample1_R1.fastq.gz, Sample1_R2.fastq.gz
# Good: Sample1_1.fastq.gz, Sample1_2.fastq.gz
# Bad: Sample1.R1.fq, Sample1_forward.fastq

# Re-compress if needed
gzip *.fastq
```

### Issue: Step 1 Run fails immediately

**Error in logs:** `Reference not found` or `Cannot open reference`

**Solution:**
1. Verify reference exists:
   ```bash
   ls /path/to/vsnp3/dependencies/Mycobacterium_AF2122/
   ```
2. Check reference name matches exactly (case-sensitive!)
3. Try auto-detect instead of manual selection

### Issue: FreeBayes fails or hangs

**Symptoms:** Step 1 runs for hours without completing

**Possible causes:**
1. Very large FASTQ files (>10GB each)
2. Insufficient memory (<8GB)
3. Corrupted BAM file

**Solution:**
```bash
# Check sample directory for errors
cd step1/SampleName
tail -100 run_step1.log

# Look for specific error messages
# Common: "out of memory", "segmentation fault"

# If memory issue, close other applications
# If persistent, try on machine with more RAM
```

### Issue: Low mapping rate

**QC shows:** `<50% reads mapped`

**Possible causes:**
1. Wrong organism/reference
2. Contamination
3. Poor sequencing quality
4. Adapter sequences not removed

**Solution:**
```bash
# Run Kraken to identify reads
conda activate kraken
kraken2 --db /path/to/database \
        --threads 4 \
        --paired Sample_R1.fastq.gz Sample_R2.fastq.gz \
        --output kraken_output.txt \
        --report kraken_report.txt

# View results
cat kraken_report.txt | head -20

# If wrong organism, select correct reference
# If contaminated, clean FASTQ files first
```

---

## Quality Control Issues

### Issue: High zero coverage (>10%)

**Interpretation:** Poor alignment to reference

**Possible causes:**
1. Reference too distant
2. Wrong organism
3. Missing regions in sequencing

**Solution:**
1. Check SNP count - if very high, try closer reference
2. Run with auto-detect to find best reference
3. If still high, consider:
   - Sample quality (check Q20 scores)
   - Sequencing depth (check average depth)
   - Reference-free phylogeny (Mashtree/kSNP)

### Issue: Very high SNP count (>1000 for bacteria)

**Interpretation:** Sample too distant from reference

**Rule of thumb:** SNPs >10% of genome size = problematic

**Solution:**
```bash
# Calculate acceptable SNP threshold
# E.g., M. bovis genome ~4.3 Mb
# 10% = 430,000 bp = 43,000 SNPs (way too high!)
# Aim for <1,000 SNPs for bacterial genomes

# Try closer reference or use reference-free methods
```

### Issue: Many mixed calls (>50 SNPs per sample)

**Interpretation:** Possible co-infection or contamination

**What it means:**
- Mixed calls (IUPAC codes) = both reference and alternate alleles present
- A few (<5) = sequencing noise (OK)
- Many (>20) = co-infection, contamination, or heterozygosity

**Solution:**
1. Review sample history - known co-infection?
2. Check if pattern affects multiple samples (contamination event?)
3. Run Kraken to identify contaminants
4. Consider excluding sample from Step 2 analysis
5. If biological (co-infection), document in report

### Issue: QC Summary shows "No stats loaded yet"

**Symptoms:** QC table is empty after Step 1 completes

**Possible causes:**
1. Step 1 didn't complete successfully
2. No `*_stats.xlsx` files generated
3. Pandas not installed or wrong version

**Solution:**
```bash
# Check if stats files exist
ls step1/*/*.xlsx

# If missing, check Step 1 logs
cd step1/SampleName
tail run_step1.log

# Verify pandas version
conda list pandas
# vSNP3 requires pandas <2.0

# If pandas >=2.0, downgrade:
conda install pandas -c conda-forge
```

---

## Step 2 Issues

### Issue: Mixed references error

**Error:** `Mixed references detected: Reference1, Reference2. Split into separate runs.`

**Explanation:** Step 2 cannot combine samples aligned to different references

**Solution:**
```bash
# Option 1: Create separate projects
# Project_Ref1 (samples aligned to Reference1)
# Project_Ref2 (samples aligned to Reference2)

# Option 2: Re-run Step 1 with consistent reference
# Select all samples to use same reference
```

### Issue: No VCF files found for Step 2

**Symptoms:** Step 2 Setup shows `VCFs ready: 0`

**Possible causes:**
1. Step 1 not completed
2. No `*_zc.vcf` files in step1 directories
3. Step 2 Setup not run yet
4. Using **custom VCF set** but Build VCF set not run

**Solution:**
```bash
# Check for VCF files
find step1 -name "*_zc.vcf"

# If missing, Step 1 didn't complete
# Check sample statuses in Step 1 panel
# Re-run failed samples
```

**If using a custom VCF set:**
- Step 2 → VCF Sources → **Build VCF set**
- Verify VCFs in `step2/vcf_source/`

### Issue: RAxML fails to build tree

**Error in logs:** `RAxML error` or `Tree building failed`

**Possible causes:**
1. Too few samples (<3)
2. All samples identical (no SNPs)
3. Insufficient memory

**Solution:**
```bash
# Check number of VCF files
ls step2/vcf_source/*.vcf | wc -l

# Need at least 3 samples for tree
# If >=3, check SNP matrix for variation

# View log
cd step2
tail -50 run_step2.log
```

### Issue: Step 2 Results show no outputs

**Symptoms:** "No Step 2 outputs found yet" after successful run

**Solution:**
```bash
# Check if files actually exist
ls step2/*.html step2/*.xlsx step2/*.tre

# If files exist, click Refresh in Step 2 Results
# If no files, check Step 2 logs for errors
```

---

## File & Data Issues

### Issue: FASTQ files not linking

**Symptoms:** Link Local Files runs but no files appear in download/

**Possible causes:**
1. Path incorrect
2. No `*.fastq.gz` files in directory
3. Permission issues

**Solution:**
```bash
# Verify path exists and contains FASTQ
ls /path/to/fastq_dir/*.fastq.gz

# Check permissions
ls -la /path/to/fastq_dir/

# Try absolute path instead of relative
# Use: /Users/name/data/fastqs
# Not: ~/data/fastqs or ./fastqs
```

### Issue: SRA download fails

**Error:** `SRA download failed` or `fasterq-dump: command not found`

**Possible causes:**
1. SRA Toolkit not installed
2. Network connectivity issues
3. Invalid accession number

**Solution:**
```bash
# Install SRA Toolkit
conda install sra-tools -c bioconda

# Test manually
fastq-dump --split-files SRR10321138

# If network issue, GUI will try ENA fallback
# Check Live Logs for progress

# Verify accession is correct:
# Valid: SRR10321138, ERR123456, DRR456789
# Invalid: SRP123456 (project, not run)
```

### Issue: Disk space full

**Error:** `No space left on device`

**Solution:**
```bash
# Check available space
df -h

# Find large files
du -sh */

# Clean up:
# - Delete old projects (use Archive first!)
# - Remove debug files (alignment_*/ if not needed)
# - Delete intermediate files:
rm -rf step1/*/unmapped_reads/
rm -rf step1/*/sourmash/

# Use symlinks instead of copying FASTQ files
```

---

## Browser/UI Issues

### Issue: Page not loading (localhost:5173)

**Symptoms:** Browser shows "Can't connect" or blank page

**Solution:**
```bash
# Check if frontend is running
lsof -i :5173

# If not running:
cd vsnp_gui/frontend
npm run dev

# Check browser console (F12) for errors
# Try different browser (Chrome/Firefox/Safari)
```

### Issue: Buttons not working

**Symptoms:** Clicking buttons does nothing

**Solution:**
1. Check browser console (F12) for JavaScript errors
2. Hard refresh page (Cmd+Shift+R on Mac, Ctrl+Shift+R on Windows)
3. Clear browser cache
4. Check if backend is running (API calls failing)

### Issue: Live Logs not updating

**Symptoms:** Job running but logs frozen

**Solution:**
1. Check job status indicator (should show "running")
2. Refresh page
3. Check backend logs for errors:
   ```bash
   tail -f backend/.venv/lib/python*/site-packages/uvicorn.log
   ```

---

## Project Management Issues

### Issue: Cannot delete/archive project

**Error:** `Project not found` or operation fails silently

**Solution:**
```bash
# Check project exists
ls projects/

# Check backend logs for permission errors
# Ensure project directory is not in use

# Manual deletion (as last resort):
rm -rf projects/ProjectName/

# Don't forget to create backup first:
tar -czf ProjectName_backup.tar.gz projects/ProjectName/
```

### Issue: Project shows wrong file counts

**Symptoms:** FASTQ count is wrong, VCF count incorrect

**Solution:**
- Click project again to refresh counts
- Close and reopen GUI
- Counts update automatically, but may lag
- Check actual files:
  ```bash
  find projects/ProjectName/download -name "*.fastq.gz" | wc -l
  ```

---

## Performance Issues

### Issue: GUI is very slow

**Possible causes:**
1. Too many projects
2. Large log files
3. Insufficient system resources

**Solution:**
```bash
# Archive old projects
# GUI manages active projects more efficiently

# Clean up large log files
find . -name "*.log" -size +100M

# Check system resources
top  # or Activity Monitor on Mac

# Close other applications
# Allocate more RAM if running in VM
```

### Issue: Step 1 taking very long (>1 hour per sample)

**Expected times:**
- Bacterial genome (4Mb, 50X): 5-15 min
- Large genome (>50Mb): 30-60 min
- Viral genome (<1Mb): 2-5 min

**If much slower:**
1. Check CPU usage - should be 100% (or close)
2. Check disk I/O - slow disks cause delays
3. Network drive? Copy files locally first
4. Insufficient RAM - check for swapping

---

## Common Error Messages

### `vSNP3 path is not set`

**Solution:** Set the vSNP3 path in Settings to the conda environment directory (e.g. `~/miniconda3/envs/vsnp3`)

### `vsnp3_step1.py not found`

**Solution:** Install vSNP3 or correct path in Settings

### `Reference type is required for Step 2`

**Solution:** Select reference in Step 2 panel (should auto-populate from Step 1)

### `No FASTQ files found`

**Solution:** Check download/ directory has `*.fastq.gz` files

### `VCF file has wrong chromosome`

**Meaning:** Sample VCF doesn't match reference

**Solution:** Re-run Step 1 with correct reference for that sample

---

## Getting More Help

### Enable Debug Mode

For better error messages:
1. Step 1: Check **Debug** checkbox before running
2. Keeps intermediate files
3. More verbose logging

### Collect Diagnostic Information

When reporting issues, include:
```bash
# System info
uname -a
python --version
conda --version

# GUI version
cd vsnp_gui
git log -1 --oneline

# Error logs
cat step1/SampleName/run_step1.log
tail -100 backend/.venv/*/uvicorn.log

# File structure
tree -L 2 projects/ProjectName/
```

### Check Online Resources

- **vSNP3 GitHub Issues:** https://github.com/USDA-VS/vSNP3/issues
- **vSNP Paper:** https://pmc.ncbi.nlm.nih.gov/articles/PMC11143592/
- **User Guide:** `USER_GUIDE.md`
- **Alpha Testing Guide:** `ALPHA_TESTING.md`

---

## Emergency Recovery

### Corrupted Project

If project is corrupted but has valuable data:
```bash
cd projects/ProjectName

# Backup first!
tar -czf ../ProjectName_emergency_backup.tar.gz .

# Try to salvage specific files
cp step1/*/Sample*_zc.vcf ../rescued_vcfs/
cp step2/*.xlsx ../rescued_results/

# Create new project and import files
```

### Complete GUI Reset

If GUI is completely broken:
```bash
cd vsnp_gui

# Backup projects
cp -r projects ~/vsnp_projects_backup

# Reset backend
cd backend
rm -rf .venv data/
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Reset frontend
cd ../frontend
rm -rf node_modules/ dist/
npm install

# Restart
cd ..
./start_gui.sh
```

---

*For issues not covered here, please create a GitHub issue or contact support*
