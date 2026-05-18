# Welcome to the Kapur Lab vSNP Pipeline

Hi Tod —

This is a quick orientation note for the lab's vSNP3 pipeline GUI. The
short version: everything runs in a web browser through OnDemand. You
shouldn't need to ssh anywhere or run commands by hand for normal work.

## Getting in

Open this URL in any browser on a machine that's on the lab's Tailscale
network:

> https://kapurlab-wgs3.tailf38ff4.ts.net

Log in with your lab credentials. From the OnDemand dashboard, find
**vSNP GUI** under Interactive Apps and click **Launch**. After about
~10 seconds you'll get a "Connect to vSNP GUI" button — that opens the
pipeline in a new browser tab.

If you don't see vSNP GUI listed, or the login fails, ping Vivek — your
account may still be in the process of getting set up.

## What's there to do

The GUI is organized roughly as a left-to-right workflow:

1. **Create a project** (top-left). Use underscores not spaces — the GUI
   will normalize for you, but it's cleaner to type it that way. A few
   trial projects may already be pre-populated for you to poke at.
2. **Bring in data**: drag-and-drop fastq.gz files into the Inputs panel,
   *or* paste SRA/SRS/SRX/DRS accessions into the SRA Download box.
   The GUI handles the SRS→SRR translation for you and writes a
   crosswalk file you can view via "View crosswalk" in the Inputs panel.
3. **Pick a reference** in the Reference Selection panel. If you need to
   add a new GenBank-accessioned reference, use Download New Reference
   and give it a human-readable display name (e.g.
   `LSDV_Neethling_2490` is better than just `AF325528.1`).
4. **Step 1** — alignment + variant calling per sample. Click Setup, then
   Run. Per-sample status appears in the table; you can View Log on any
   sample, or open IGV in-browser to inspect alignments.
5. **Step 2** — comparative SNP analysis across samples. You can exclude
   samples from analysis via the QC table checkboxes (e.g., low coverage,
   high contamination); exclusions take effect at Setup time, not just
   at run time.

## What's actively being built / known gaps

The pipeline is under active development. A few things to know:

- **SRA download status reporting** has a known bug — the job log
  sometimes reports successful runs as `[FAILED]`. Don't trust the
  failure-count summary; check `download/` for the actual fastqs.
  This is tracked as T-42.
- **Single-end and very tiny (<1 MB) fastqs are auto-skipped** at Step 1
  dispatch with a list-of-what-got-skipped alert. If you submit SRS
  accessions that resolve to single-end runs, expect them to be
  excluded — the GUI will tell you which. Real single-end Illumina
  support is in flight (T-46 Phase 2).
- **Renaming a project** is non-trivial and best done via the admin
  script (`/srv/kapurlab/tools/vsnp_gui/deploy/admin/kapurlab-rename-project.sh`)
  rather than `mv` directly. Ping Vivek if you need this.
- **Depth cap on mapping** (e.g., "cap at 300× mapped depth") is *not*
  yet implemented. For very deep sequencing on small viral genomes you
  may see longer-than-needed runtimes. Vivek is thinking about how to
  add this without penalizing low-yield samples.

## Where things live

| What | Path |
|---|---|
| Your projects | `/home/<you>/projects/<project>/` once you create them |
| Shared (lab) projects | `/srv/kapurlab/projects/<project>/` |
| Shared reference dir | `/srv/kapurlab/refs/vsnp3/reference_options/<ref>/` |
| Audit logs (provenance) | `/srv/kapurlab/audit/` |

You shouldn't normally need to touch these directly — the GUI handles it
— but if you ever do (e.g., to inspect a fastq the GUI doesn't show, or
to copy a file off-system) ssh access works as usual.

## Where to get help

- **Vivek** for anything pipeline / lab / scientific
- **Lingling / Dev** if Vivek isn't around — they've been using the GUI
  for a few weeks and have good intuition for the common gotchas
- **Project documentation** lives at
  `/srv/kapurlab/tools/vsnp_gui/docs/dev/` if you want the gory detail
  (architecture, ticket backlog, design red-team review). Not required
  reading.

## A specific note on reproducibility

If you're handing results off to a collaborator or submission, the
pipeline writes a `_provenance/` directory inside each step1 sample dir
and a top-level `run_metadata.json` per Step 1 batch. These capture
tool versions, reference SHA256s, vsnp3 patch state, env snapshots — a
full reproducibility manifest. **If anything looks off in your results,
preserve the project directory before re-running**: re-running rewrites
the provenance and you lose the historical state. Vivek can help with
post-hoc audits via the `verify_provenance.py` admin tool.

---

If something doesn't work the way this note describes, please report it.
The GUI is currently averaging a few small fixes per session — your
feedback closes that loop.

— Vivek (via Claude, May 18 2026)
