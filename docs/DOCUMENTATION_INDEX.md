# vSNP GUI Documentation Index

**Complete documentation suite for the vSNP GUI**

---

## 📚 Documentation Overview

This project includes comprehensive documentation based on the vSNP3 pipeline (Stuber et al. 2024, BMC Genomics) and USDA vSNP3 repository.

**Total Documentation:** 60+ pages across 7 guides

---

## 🎯 Which Guide Should I Read?

### **For First-Time Users**
👉 Start with: **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md)**
- Complete walkthrough from installation to results
- 75+ pages covering all features
- Based on official vSNP3 publication
- Includes quality control guidelines and best practices

### **For Alpha Testers / Collaborators**
👉 Start with: **[ALPHA_TESTING.md](ALPHA_TESTING.md)**
- Quick setup guide for collaborators
- Installation requirements
- How to report issues
- Essential configuration steps

### **For Experienced Users**
👉 Use: **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)**
- One-page cheat sheet
- QC metric thresholds
- Common commands
- Keyboard shortcuts
- Decision matrices

### **For Visual Learners**
👉 See: **[WORKFLOW_DIAGRAM.md](WORKFLOW_DIAGRAM.md)**
- Complete ASCII workflow diagrams
- Data flow visualization
- File structure maps
- Decision trees

### **Having Problems?**
👉 Check: **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**
- Common error messages
- Step-by-step solutions
- Performance optimization
- Emergency recovery procedures

---

## 📖 Complete Guide List

| Guide | Pages | Purpose | Best For |
|-------|-------|---------|----------|
| **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md)** | ~75 | Complete reference manual | First-time users, comprehensive learning |
| **[QUICKSTART.md](QUICKSTART.md)** | ~8 | Step-by-step tutorial | First-time users, quick onboarding |
| **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** | ~5 | One-page cheat sheet | Experienced users, quick lookup |
| **[WORKFLOW_DIAGRAM.md](WORKFLOW_DIAGRAM.md)** | ~8 | Visual workflow guide | Understanding data flow |
| **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** | ~13 | Problem solving | Debugging errors |
| **[ALPHA_TESTING.md](ALPHA_TESTING.md)** | ~2 | Quickstart for testers | Collaborators, setup |
| **[APP_ICON_README.md](APP_ICON_README.md)** | ~3 | Icon creation guide | Developers |
| **[ICONS_README.md](ICONS_README.md)** | ~3 | Icon options | Customization |
| **[sample_data/vcf_lite/README.md](sample_data/vcf_lite/README.md)** | ~1 | Built‑in VCF Lite Pack | Step 2 demo/testing |

**Total:** ~110 pages of documentation

---

## 📋 Detailed Guide Descriptions

### 1. USER_GUIDE_COMPREHENSIVE.md
**The Complete Reference Manual**

**Contents:**
- Introduction & Background (what is vSNP?)
- Installation & Setup
- Project Management
- Input Data Methods (3 options)
- Step 1: Alignment & Variant Calling
  - Setup, Reference Selection, Run
  - Understanding outputs
- Quality Control & Metrics
  - 6 critical metrics explained
  - Thresholds and interpretation
  - Filtering strategies
- Step 2: SNP Matrices & Trees
  - 3 matrix types explained
  - Phylogenetic tree interpretation
- Interpreting Results
  - Reading SNP matrices
  - Mixed calls (IUPAC codes)
  - Gene annotations
- Best Practices & Guidelines
  - Reference selection strategies
  - QC workflow
  - Data management
  - Reproducibility
- Advanced Topics
  - Re-running analyses
  - Custom filtering
  - Integration with IGV, Mashtree, Kraken

**When to use:** First time using vSNP GUI, need detailed explanations, preparing for publication

---

### 2. QUICK_REFERENCE.md
**One-Page Cheat Sheet**

**Contents:**
- Workflow overview (one-liner)
- Initial setup checklist
- Project setup steps
- Step 1 quick guide
- QC metrics decision table
- Step 2 quick guide
- SNP matrix legend
- Common issues & solutions
- Best practices summary
- File naming requirements
- Typical processing times
- Keyboard shortcuts
- Power user tips

**When to use:** Quick lookup, experienced user, forgot a specific threshold, need reminder of file formats

---

### 3. WORKFLOW_DIAGRAM.md
**Visual Workflow Guide**

**Contents:**
- Complete workflow diagram (ASCII art)
- Step-by-step visual flow
- Data flow summary
- File structure after analysis
- Decision tree for reference selection
- Input/Processing/Output diagrams

**When to use:** Understanding big picture, explaining workflow to others, visual learning, presentations

---

### 4. TROUBLESHOOTING.md
**Comprehensive Problem Solving**

**Contents:**
- GUI Won't Start
  - Backend/frontend failures
  - Port conflicts
  - Dependency issues
- Configuration Issues
  - Preflight failures
  - Missing packages
  - Reference problems
- Step 1 Issues
  - Setup failures
  - Run errors
  - Low mapping rate
- Quality Control Issues
  - High zero coverage
  - High SNP counts
  - Mixed calls
- Step 2 Issues
  - Mixed references error
  - Tree building failures
- File & Data Issues
  - FASTQ linking problems
  - SRA download failures
  - Disk space
- Browser/UI Issues
- Performance Issues
- Common Error Messages
- Emergency Recovery

**When to use:** Something's broken, error messages, performance problems, data corruption

---

### 5. ALPHA_TESTING.md
**Quickstart for Collaborators**

**Contents:**
- System requirements
- Installation steps
- Configuration guide
- Starting the GUI
- Basic usage overview
- How to report issues

**When to use:** Setting up GUI for first time, sharing with collaborators, testing setup

---

### 6. APP_ICON_README.md & ICONS_README.md
**Icon Creation & Customization**

**Contents:**
- Icon designs created
- How to switch icons
- Technical details
- File formats

**When to use:** Customizing appearance, understanding icon assets

---

## 🔍 Quick Navigation by Topic

### Installation & Setup
- **[ALPHA_TESTING.md](ALPHA_TESTING.md)** - Quick setup
- **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md)** - Detailed setup
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - Setup problems

### Running Analysis
- **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md)** - Complete walkthrough
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - Quick steps
- **[WORKFLOW_DIAGRAM.md](WORKFLOW_DIAGRAM.md)** - Visual guide

### Quality Control
- **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md)** - Section 6 (detailed)
- **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** - QC table
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - QC issues

### Understanding Results
- **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md)** - Section 8
- **[WORKFLOW_DIAGRAM.md](WORKFLOW_DIAGRAM.md)** - Output structure

### Problems & Errors
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** - First stop
- **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md)** - Best practices

---

## 📊 Documentation Quality

**Based on authoritative sources:**
- ✅ vSNP3 official publication (Stuber et al. 2024, BMC Genomics)
- ✅ USDA vSNP3 GitHub repository
- ✅ vSNP v2 detailed usage documentation
- ✅ Hands-on testing with actual GUI

**Coverage:**
- ✅ Every GUI feature documented
- ✅ All quality metrics explained
- ✅ Common errors with solutions
- ✅ Best practices from publication
- ✅ Visual aids and examples

**Accessibility:**
- ✅ Multiple difficulty levels (beginner → advanced)
- ✅ Quick reference for fast lookup
- ✅ Visual diagrams for workflow
- ✅ Troubleshooting index
- ✅ Cross-referenced between guides

---

## 🎓 Learning Path

**Recommended reading order for new users:**

1. **[ALPHA_TESTING.md](ALPHA_TESTING.md)** (5 min)
   - Get system set up and running

2. **[WORKFLOW_DIAGRAM.md](WORKFLOW_DIAGRAM.md)** (10 min)
   - Understand the big picture

3. **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md) - Sections 1-4** (30 min)
   - Learn basics: setup, projects, inputs

4. **Run your first analysis** (hands-on)
   - Create test project
   - Run Step 1 on a few samples

5. **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md) - Sections 5-6** (30 min)
   - Learn Step 1 and QC in detail

6. **[USER_GUIDE_COMPREHENSIVE.md](USER_GUIDE_COMPREHENSIVE.md) - Sections 7-8** (30 min)
   - Learn Step 2 and results interpretation

7. **[QUICK_REFERENCE.md](QUICK_REFERENCE.md)** (bookmark)
   - Use as ongoing reference

8. **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** (as needed)
   - Consult when issues arise

**Total learning time:** ~2-3 hours to full proficiency

---

## 📚 External Resources

### vSNP3 Official Resources
- **Publication:** [vSNP: a SNP pipeline for the generation of transparent SNP matrices and phylogenetic trees from whole genome sequencing data sets](https://pmc.ncbi.nlm.nih.gov/articles/PMC11143592/)
  - Stuber TP, et al. BMC Genomics. 2024 Jun 1;25(1):548
- **GitHub:** [USDA-VS/vSNP3](https://github.com/USDA-VS/vSNP3)
- **vSNP v2:** [Detailed Usage Guide](https://github.com/USDA-VS/vSNP/blob/master/docs/detailed_usage.md)

### Related Tools
- **IGV:** [Integrated Genomics Viewer](https://software.broadinstitute.org/software/igv/)
- **FigTree:** [Tree Visualization](http://tree.bio.ed.ac.uk/software/figtree/)
- **iTOL:** [Interactive Tree of Life](https://itol.embl.de/)
- **Mashtree:** [Reference-free phylogeny](https://github.com/lskatz/mashtree)
- **Kraken2:** [Taxonomic classification](https://ccb.jhu.edu/software/kraken2/)

---

## 🔄 Documentation Updates

**Version:** 1.0 (February 2026)

**Changelog:**
- Initial release with comprehensive documentation suite
- Based on vSNP3 v3.10 and GUI alpha version
- Covers all features of February 2026 GUI release

**Future Updates:**
- Will track GUI feature additions
- Updated for new vSNP3 releases
- Additional examples and case studies

---

## 💬 Feedback

**Found an error in documentation?**
- Create GitHub issue
- Tag with `documentation` label

**Have suggestions?**
- Create GitHub issue with `enhancement` label

**Documentation is unclear?**
- Let us know which section
- Suggest improvements

---

## 📝 Documentation Standards

All guides follow:
- **Clear headings** - Easy navigation
- **Code examples** - Copy-paste ready
- **Decision tables** - Quick reference
- **Cross-references** - Link related topics
- **Visual aids** - Diagrams where helpful
- **Troubleshooting** - Common issues addressed

---

## ✅ Documentation Completeness Checklist

- [x] Installation and setup
- [x] All GUI features documented
- [x] Quality control thresholds defined
- [x] Output file formats explained
- [x] Best practices from literature
- [x] Common errors with solutions
- [x] Visual workflow diagrams
- [x] Quick reference card
- [x] Alpha testing guide
- [x] Troubleshooting guide
- [x] Cross-references between guides
- [x] External resource links
- [x] Citation information

---

*Complete documentation suite for professional whole genome SNP analysis with vSNP3*
