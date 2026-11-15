# FMDV mode: handoff for review and trial

Branch `fmdv-mode` of `kapurlab/vsnp_gui`, written 14 and 15 November 2026
for Tod Stuber and anyone else who will try it, review it, or decide what,
if anything, goes into `main`. `FMDV_MODE.md` in this folder is the design
and the measurements. This file is the state: what the branch is, how to
run it, what is verified, what is not, and the questions that need an
answer before it is merged.

## Background: NanoFMDV, and the problem it solves

AHI's sequencing protocol is NanoFMDV (Shaw et al. 2025, BMC Genomics,
doi 10.1186/s12864-025-11938-7; code at `github.com/rjorton/nanofmdv`):
tiled amplicons over the FMDV genome from a 33-primer scheme, barcoded,
sequenced on a MinION, and a per-barcode bash pipeline that turns each
barcode's reads into one typed consensus genome. The pipeline has no SNP
calling, no comparison between samples and no tree. Its unit of output is
one consensus FASTA and one row of statistics per barcode.

Per barcode, `nanofmdv_single_barcode.sh` runs:

1. **Reference choice by VP1 vote.** minimap2 (`map-ont`) maps the reads to
   `fmdv_vp1.fasta`, the VP1 sequences of 79 near-complete genomes drawn
   from FMDbase at roughly one per lineage. The VP1 with the most mapped
   reads names the reference; its full genome is pulled from `fmdv.fasta`
   as `top_ref.fasta`. The reads are also mapped to the 79 complete genomes
   for statistics.
2. **Alignment to the chosen genome** with minimap2, secondary and
   supplementary alignments dropped.
3. **Primer clipping.** `goprime_comp.py` finds each primer's best binding
   window on the chosen reference and `goprime_bed.py` writes a BED;
   `samtools ampliconclip --hard-clip --both-ends --tolerance 50
   --filter-len 50` clips the alignments, and `samtools fastq` writes the
   clipped reads back out.
4. **Draft consensus.** `samtools mpileup` over the clipped BAM, then
   `VSENSUS.jar` calls a draft at a minimum depth of 10.
5. **Polished consensus.** `medaka_variant` on the clipped reads against
   the reference, then `medaka sequence --min_depth 10 --fill_char N`, so
   positions under ten reads are N.
6. **Typing.** `blastn` of the consensus against `fmdv-gbbase.fasta`, their
   collection of 11,153 GenBank FMDV sequences whose headers carry
   serotype, topotype, country, year and host; the top hits give the
   serotype and topotype call.
7. **Statistics** (`depth_charge.py`, `fmdvstats.sh`): depth, coverage,
   read counts and the BLAST call, one CSV row per barcode.

We built and ran it on 14 November 2026 before writing any code of our
own. The published install fails in five places (an environment file named
wrongly, no Java for VSENSUS so the typing column goes silently blank, and
so on); with a corrected environment the pipeline reproduces the paper's
own test dataset exactly (`SRR34371563` assembles to `PV524918` at every
called position). That build and its fixes are documented in the lab's
`NanoFMDV_test_report_2026-11-14.md`, outside this repository.

What NanoFMDV does not do is the question AHI will ask once more than one
sample exists: are these viruses the same, and how do they relate to each
other and to what GenBank holds? That is a reference-mapped SNP question,
which is what vsnp3 does. So the design is NanoFMDV's per-sample front end
where it is right, vsnp3 for everything from alignment on, and the two
joined by a rule vsnp3 did not need for MTBC: the reference is chosen per
sample, before Step 1, because FMDV lineages are too far apart for one
reference to serve (section "Why" in `FMDV_MODE.md`).

## What was adopted, what was changed, what was left out

| NanoFMDV stage | In FMDV mode | Why |
|---|---|---|
| 79 lineage genomes and their VP1 panel | adopted as the reference library, read from a NanoFMDV checkout at build time | one genome per lineage is the right granularity for a nanopore reference |
| Reference by VP1 vote, most reads wins | adopted and tightened: first 20,000 reads, primary alignments only, MAPQ 10 or more and 100 aligned bases, the vote counted in aligned bases, a 20-read minimum, whole genomes as fallback, restricted to the project's scope | the same answer on all ten trial samples, in under 1.5 s, with a record of the runner-up, the read identity and the serotype composition |
| (none) | added: `distant` and `mixed` flags from the vote, both review and never refusal | a sample from a lineage with no close genome must still run; crosstalk and co-infection must be visible |
| Primer binding sites by the goprime rule, BED per sample | adopted, reimplemented; computed once per reference and primer set and cached; a primer whose best fit is under 0.8 is left out and listed | an unconditional BED clips a random stretch where a serotype-specific primer does not bind |
| `samtools ampliconclip`, same settings | adopted; unmapped reads pass through | vsnp3's mapping rate must still describe the sample |
| minimap2 alignment to the chosen genome | replaced by `vsnp3_step1.py -t <lineage folder>` | vsnp3's alignment and calling produce the `_zc.vcf` that Step 2, the groups and the trees need |
| VSENSUS draft, medaka polish, depth mask 10 | replaced by a consensus built from vsnp3's own calls with a pileup check and the same depth-10 mask; no indels, coordinates stay the reference's | the consensus and the SNP calls then agree by construction; measured identical to medaka's on SAT1 and 99.8% on strains 10 to 14% from their reference. An indel-aware polish for GenBank submission is a later, separate step |
| blastn typing of the consensus | not adopted; serotype and lineage come from the chooser's VP1 composition | typing is decided before mapping, which is what makes the mapping work. The GenBank collection is used instead for the VP1 context tree and for the same-serotype genome databases |
| Statistics CSV | replaced by vsnp3's stats and an FMDV QC verdict (genome covered, depth, mapping rate, distant, mixed) | the stock verdict judged an FMDV field sample by bacterial-isolate rules |
| Shell loop over barcode folders | replaced by a MinKNOW run import into vsnp_gui's one-file-per-sample layout, with a sample sheet | the barcode-to-sample step belongs in the GUI, and it is useful in stock mode too |
| (none) | added: Step 2 per reference with lineage and GenBank genome databases, defining-SNP groups per topotype, and a VP1 tree with a GenBank context set | the between-sample questions NanoFMDV does not ask |

## Schematic

Per sample. NanoFMDV's pipeline on the left, FMDV mode on the right; the
dashed line marks where NanoFMDV's part ends and vsnp3 begins.

```mermaid
flowchart LR
  subgraph N["NanoFMDV, per barcode"]
    direction TB
    n1["reads of one barcode"] --> n2["minimap2 to 79 VP1s<br/>most reads wins"]
    n2 --> n3["top_ref.fasta"]
    n3 --> n4["minimap2 to top_ref"]
    n4 --> n5["goprime BED on top_ref<br/>samtools ampliconclip"]
    n5 --> n6["mpileup, VSENSUS draft"]
    n6 --> n7["medaka_variant polish<br/>depth under 10 to N"]
    n7 --> n8["blastn vs GenBank collection<br/>serotype and topotype"]
    n8 --> n9["consensus FASTA<br/>stats row"]
  end
  subgraph F["FMDV mode, per sample"]
    direction TB
    f1["one FASTQ per sample<br/>from the MinKNOW import"] --> f2["fmdv_choose_reference.py<br/>minimap2 to 79 VP1s, aligned bases vote,<br/>within the project's scope"]
    f2 --> f3["fmdv_reference.json<br/>lineage, identity, serotype share,<br/>distant and mixed flags"]
    f2 --> f4["reference_options/&lt;lineage&gt;/<br/>made on first use"]
    f4 --> f5["fmdv_clip_primers.py<br/>goprime rule, cached BED,<br/>samtools ampliconclip"]
    f5 --> f6["vsnp3_step1.py -t &lt;lineage folder&gt;<br/>stock alignment and calling"]
    f6 --> f7["sample_zc.vcf<br/>as in every vsnp3 project"]
    f6 --> f8["fmdv_consensus.py<br/>vsnp3 calls plus pileup check,<br/>depth under 10 to N, VP1 slice"]
    f8 --> f9["consensus FASTA, VP1 FASTA<br/>fmdv_consensus.json"]
    f7 --> f10["QC verdict with FMDV thresholds"]
    f3 --> f10
  end
  N -. "adopted: VP1 vote, primer BED rule, ampliconclip, depth mask" .-> F
```

Per project. Step 2 is stock vsnp3 run once per lineage reference; what is
new is what sits beside the samples in the comparison, and the VP1 tree.

```mermaid
flowchart LR
  subgraph L["Reference library, built once per site"]
    direction TB
    l1["NanoFMDV Refs:<br/>79 genomes, 79 VP1s,<br/>GenBank collection, primers"] --> l2["fmdv_reflib.py build<br/>lineages.json, panels, genomes"]
    l1 --> l3["fmdv_vp1_context.py<br/>VP1 per serotype from the collection,<br/>checked and sliced"]
    l2 --> l4["fmdv_panel_build.sh &lt;ref&gt;<br/>78 other lineage genomes<br/>as vsnp3 FASTA-mode VCFs"]
    l1 --> l5["fmdv_genome_db.py --ref<br/>every complete genome of the serotype<br/>as vsnp3 FASTA-mode VCFs"]
    l4 --> l6["fmdv_define_snps.py<br/>one group per topotype into<br/>define_filter.xlsx"]
    l5 --> l6
  end
  subgraph P["Project"]
    direction TB
    p1["samples on lineage reference R<br/>sample_zc.vcf each"] --> p2["Step 2 on R: Build ticks the<br/>databases, Run is stock vsnp3"]
    p2 --> p3["all-samples tree and matrix,<br/>one tree per topotype group"]
    p1 --> p4["fmdv_vp1_tree.py<br/>samples' VP1 plus the panel plus<br/>a context set chosen by genetic spread"]
    p4 --> p5["VP1 tree in the Step 2 run list"]
    p6["samples on another reference"] --> p7["their own Step 2 on that reference"]
  end
  l4 --> p2
  l5 --> p2
  l6 --> p2
  l3 --> p4
  l2 --> p4
```

The lab's `FMDV_mode_workflow.html` draws the same two levels with the
divergence scale that motivates them; it is outside this repository.

## What the branch is

vsnp_gui gains a project mode, `"mode": "fmdv"` in `project.json`, in which
the reference genome is chosen per sample before Step 1 instead of once per
project. Everything after that choice is stock vsnp3: the same step1, the
same `_zc.vcf`, the same Step 2, groups, trees and viewer. The reason is in
`FMDV_MODE.md`: lineages of one FMDV serotype differ by up to 21% and
nanopore reads stop mapping past 10 to 15% divergence, so one reference per
project loses the capsid, and with it the typing signal, for most samples.

Against `main` at `2b5d1bf` (v0.4.139) the branch is 15 commits: 19 new
files and edits to four stock files, 4,900 lines added and 43 removed. The
branch also carries a rebuilt `frontend/dist/` so a checkout serves the
FMDV interface without a build.

New, FMDV only, nothing in stock mode calls them:

| File | What it does |
|---|---|
| `bin/fmdv_reflib.py` | the reference library: the 79 NanoFMDV lineage genomes as a hierarchy, panels, and vsnp3 reference folders made on demand |
| `bin/fmdv_choose_reference.py` | the per-sample chooser: competitive minimap2 on the VP1 panel, whole genomes as fallback; writes `fmdv_reference.json` |
| `bin/fmdv_clip_primers.py` | amplicon primer clipping on the chosen reference, `samtools ampliconclip` |
| `bin/fmdv_consensus.py` | depth-masked consensus from vsnp3's own calls plus a pileup check, and the VP1 slice |
| `bin/fmdv_vp1_tree.py` | VP1 tree of the project's samples with the lineage panel and a GenBank context set, mafft plus raxmlHPC |
| `bin/fmdv_vp1_context.py` | builds the per-serotype VP1 context sets offline from NanoFMDV's GenBank collection, with a VP1 identity check and slicing from long records |
| `bin/fmdv_panel_build.sh` | calls the 78 other lineage genomes against one reference (vsnp3 step1 FASTA mode) as a Step 2 reference database |
| `bin/fmdv_genome_db.py` | the same for every complete genome of the reference's serotype in the collection |
| `bin/fmdv_define_snps.py` | derives one defining-SNP group per topotype into the reference's `define_filter.xlsx` |
| `bin/fmdv_bench_nanofmdv.py` | compares our consensus with NanoFMDV's on the same reads |
| `backend/app/ont_import.py`, `frontend/src/OntRunImport.jsx` | MinKNOW run import: barcode folders to one FASTQ per sample. Not FMDV specific; works in stock mode |
| `frontend/src/FmdvScopePicker.jsx` | the scope picker (serotype, topotype or lineage, genome, each with Auto) |
| `backend/app/test_fmdv_*.py`, `backend/app/test_ont_import.py` | tests in the stock script style |
| `conda_setup/environment.yml` | the fork's own tools beside the vsnp3 env: mafft, iqtree, seqkit, BLAST+ |
| `docs/FMDV_MODE.md`, this file | design, measurements, state |

Edits to stock files, every one gated on the mode flag or on an FMDV
library reference, so stock projects run the code they ran before:

- `backend/app/main.py` (416 lines added). The Step 1 dispatch
  (`_step1_dispatch`) runs the chooser, the clipper and the consensus per
  sample around `vsnp3_step1.py -t <lineage folder>`; `Step1Request` and
  the status routes carry the mode, the scope and the chooser's result;
  `_resolve_qc_thresholds` and the QC summary read a per-project threshold
  layer; Step 2 (`step2_run`, `step2_panels_get`, `_step2_reference_audit`,
  `_reference_panel_accessions`, `_reference_panels_by_name`,
  `step2_vcf_count`, `project_vcfs_list`) resolves a library reference to
  its folder on demand and filters databases by the comparison reference;
  `project_link_local` and the `/fmdv` routes switch the mode and scope.
- `frontend/src/App.jsx` (210 lines added). The FMDV checkbox and scope
  picker in Step 1, the "Compare on reference" select and the database
  tick boxes in the Step 2 Build tab, and the QC criteria panel in FMDV
  mode. Build now asks the backend which databases are ticked before it
  imports, which fixes a stale-list race that exists in stock too.
- `backend/app/qc_verdict.py` (35 lines). Two extra signals through the
  existing contamination hook: a distant reference and a mixed serotype,
  both review, never fail.
- `backend/app/step2_inventory.py` (7 lines). A library reference's
  folder counts as a reference location.

Nothing from NanoFMDV is in the repository. The 79 genomes, the VP1 panel,
the GenBank collection and the primer scheme are read from a NanoFMDV
checkout at library build time (NanoFMDV, Shaw et al. 2025,
`github.com/rjorton/nanofmdv`, which carries no licence file, so its data
is not redistributed here).

## How to run it

Prerequisites beyond a working vsnp_gui:

1. The vsnp3 env on PATH when the backend starts, as for stock. The FMDV
   scripts use its `minimap2`, `samtools`, `bcftools` and `raxmlHPC`.
2. The fork's own env for mafft, iqtree, seqkit and BLAST+:
   `mamba env create -f conda_setup/environment.yml`, then put its `bin` on
   PATH as well. (BLAST+ was added to the file on 15 November and that
   install has not been exercised; on the development server BLAST+ came
   from the system.)
3. A NanoFMDV checkout for its `pipeline_exec/Refs/fmdv.fasta` (79 lineage
   genomes), `Refs/fmdv_vp1.fasta` (their VP1 sequences, same headers),
   `Refs/BLAST/fmdv-gbbase.fasta` (11,153 GenBank records with
   serotype|topotype|country|year|host|accession headers) and the L primer
   scheme FASTA.
4. Node 20 or later to rebuild the frontend if you change it (Vite 8 does
   not run on Node 18). The branch ships a built `frontend/dist/`.

Build the library once (default location `~/fmdv_refs`; the config key
`fmdv_reflib` moves it):

```bash
python bin/fmdv_reflib.py build --refs <nanofmdv>/pipeline_exec/Refs/fmdv.fasta --lib ~/fmdv_refs --vp1 <nanofmdv>/pipeline_exec/Refs/fmdv_vp1.fasta
mkdir -p ~/fmdv_refs/primers && cp <nanofmdv L primers>.fasta ~/fmdv_refs/primers/nanofmdv_L.fasta
python bin/fmdv_vp1_context.py --source <nanofmdv>/pipeline_exec/Refs/BLAST/fmdv-gbbase.fasta --lib ~/fmdv_refs --threads 8
NCBI_EMAIL=you@example.org python bin/fmdv_reflib.py annotations --lib ~/fmdv_refs
```

The first command takes seconds, the third about four minutes with BLAST+
on eight threads, and the fourth about a minute online: it fetches the 79
GenBank records and GFF3 files that give Step 2 tables their annotation
row and IGV its gene track, the same files the stock reference import
downloads, with their identifiers rewritten to the bare accession so they
match the VCFs. A site that will run offline does this step while it still
has a connection. Everything else in the library is made on demand: a
reference folder the first time a lineage is chosen, VP1 coordinates and
primer BEDs the first time a reference is used.

For Step 2 context on a reference (optional, per lineage reference in use;
`SAT1-I_KM268899` is the one the trials used):

```bash
bash bin/fmdv_panel_build.sh SAT1-I_KM268899
python bin/fmdv_genome_db.py --source <nanofmdv>/pipeline_exec/Refs/BLAST/fmdv-gbbase.fasta --lib ~/fmdv_refs --ref SAT1-I_KM268899 --register
python bin/fmdv_define_snps.py --lib ~/fmdv_refs --ref SAT1-I_KM268899
```

`fmdv_panel_build.sh` still has the development server's paths in it
(vsnp3 at `/srv/kapurlab/tools/vsnp3`, the fork at `~/fmdv_gui`, the
library at `~/fmdv_refs`); edit the first lines. Its output folders become
`vcf_db_folders` entries in the GUI config, with `reference` set, under
Settings or by hand; `fmdv_genome_db.py --register` writes its own entry,
disabled, for the user to tick.

Then in the GUI:

1. Create a project. In the Step 1 pane tick "FMDV mode: choose the
   lineage reference per sample" and pick a scope: FMDV (closest lineage
   anywhere, the default), a serotype, a topotype, or a pinned genome.
   Ticking the mode writes the FMDV QC thresholds into `project.json`.
2. Add reads: local FASTQs, or "Import ONT run" on the Inputs page with a
   MinKNOW run folder and an optional `sample_sheet.csv`
   (`barcode,sample_id`). Tick Nanopore reads. Grab, then Run.
3. Step 1 Results shows the stock table; the chooser's result is in
   `step1/<sample>/fmdv_reference.json`, the consensus and VP1 slice beside
   the alignment folder, and the QC verdict uses the FMDV thresholds.
4. Step 2: choose the comparison reference (one lineage at a time; the
   select lists the lineages the project's samples landed on), tick the
   reference databases to compare against, Build, Run. Samples on other
   references are left out and listed.
5. The VP1 tree is a command for now:
   `python bin/fmdv_vp1_tree.py --project <project dir> --lib ~/fmdv_refs --serotype SAT1`.
   Its output folder appears in the Step 2 run list and opens in the tree
   viewer.

Test data, all public: SRA `SRR34371563` (SAT1/I, TAN/27/2012, expected
genome `PV524918`, the one NanoFMDV publishes), and Pirbright's BioProject
`PRJNA693428` (serotypes A, O and Asia1 from cell culture and epithelium,
R9 reads 15% or more from the nearest library genome, 1 to 5% barcode
crosstalk, 15 to 55% FMDV reads). The development server holds MinKNOW
style run folders built from both, with sample sheets, under
`~/fmdv_run_test` (2 GB), and the nine FASTQs of the walkthrough project
(179 MB); ask and they will be copied.

## What is verified

Numbers and methods are in `FMDV_MODE.md`; this is the list.

- Chooser: right serotype and lineage for all ten trial samples (four
  SAT1, six Pirbright) in under 1.5 s each; agrees with NanoFMDV's own
  reference choice on all ten.
- Step 1 in the mode: per-sample `-t <folder>`, `_zc.vcf` named and placed
  as stock; vsnp3 called 6 SNPs on the SAT1 reads against their lineage
  reference, the 6 real differences to the published genome.
- Consensus: identical to NanoFMDV's medaka consensus over the genome and
  over VP1 on the SAT1 samples; 99.8% agreement on the divergent Pirbright
  strains, disagreements split between the pipelines.
- Primer clipping: coordinates equal NanoFMDV's BED on the shared
  reference; no effect on the trial reads, which carry no primers.
- QC: the verdict table on the ten samples matches what a reviewer would
  say by eye (SAT1 pass, Pirbright review or fail on genome covered).
- ONT import: preview, sheet handling, four-level `fastq_pass` layout,
  renamed samples, unclassified skipped.
- Step 2: one comparison per reference from the GUI; the eight-genome
  SAT1 database and the 39-genome GenBank database both run from the pane,
  with the GUI run and a direct vsnp3 run forming the same groups.
  Defining-SNP groups derived from 117 genomes are recognised by vsnp3.
- Step 2 tables carry the annotation row and IGV the gene track, from the
  GenBank and GFF files the library fetches once per genome; a variant of a
  database genome opens IGV on the alignment the library build kept, with
  a note that the reads were simulated from the genome's sequence.
- VP1 tree: samples placed with their lineage on the panel alone and on
  panel plus context (SAT1 167 taxa in 90 s; O 621 taxa in 11 min).
- Tests: the four new scripts pass; the 60 stock test scripts give the
  same result on the branch as on `main` (54 pass, six fail for
  environment reasons on both).

## What is not done

In the order they matter for a site that will run this on outbreak
samples.

1. Step 1 Results still shows the Illumina QC columns. Serotype, lineage,
   `f_match`, identity and the distant and mixed flags are in
   `fmdv_reference.json` and in the verdict, not in the table.
2. No GUI button for the VP1 tree; it is a command.
3. The consensus is substitution-only with a depth mask. A GenBank
   submission needs an indel-aware polish (medaka), which is a separate
   step not built. The consensus and the SNP calls share coordinates by
   design.
4. Amplicon reads with primers present have not been seen by either
   pipeline. The clipper is adopted from NanoFMDV and tested only on
   primer-free reads.
5. The importer does not tick "Nanopore reads"; the user does.
6. Defining SNPs: half the genomes of SAT1 topotypes II, III and IV fall in
   no group, because no single passing SNP is shared by all of a topotype
   that spans 1937 to 2009 and by nothing else. The all-samples tree does
   not have that limit. The reference's own topotype gets no group.
7. Two-level mapping, proposed and not built: after the lineage reference,
   remap a sample to the closest complete genome in the collection when
   that genome is closer on VP1 than the panel reference by a margin to be
   tuned, and run Step 2 against it with the same-topotype genomes. The
   pieces exist (1,471 genomes marked in the context TSVs, FASTA-mode
   databases, the VP1 tree); the second pass and its place in the GUI do
   not.
8. The chooser benchmark (leave-one-out over the 79 genomes, crosstalk and
   co-infection mixes, depth and error rate) is planned, not built. Any
   change to the chooser should at least rerun the ten trial samples.
9. `fmdv_panel_build.sh` carries development-server paths;
   `fmdv_reflib.py`'s docstring still describes the sourmash chooser that
   was replaced (the signatures are built and kept for comparison only).
10. No GenBank genome exists for some lineages (SAT1 topotype IX is one). A
    sample from such a lineage maps to the nearest genome, is flagged
    distant, and runs; its lineage call is provisional by construction.

## Questions for Tod

1. A mode inside vsnp_gui, or a separate tool that hands vsnp3 a reads
   file and a reference folder? The branch is the first. The argument for
   it is that Step 2, the groups, the trees and the viewer come for free
   and the per-sample change is small; the argument against is 450 lines
   in two files you maintain.
2. If a mode: are the four edit points acceptable as placed (the Step 1
   dispatch, the Step 2 reference resolution and database filter, the QC
   threshold layer, the verdict hook)? Each is behind the mode flag or a
   library-reference check.
3. The ONT run import is independent of FMDV and may be worth having in
   stock on its own.
4. Two observations on vsnp3 itself, for information. Reads simulated by
   `vsnp3_fasta_to_fastq.py` sit near MQ 54 at some positions, under the
   56 that `_passing` requires, so a genome run through `-f` can carry a
   real SNP that the defining-SNP logic never sees. And
   `test_step2_perf_caches` fails three times in three on an xfs home on
   `main` as well, because two writes of `project.json` in a row get the
   same mtime.
5. Merge, or keep the branch? Our proposal is to keep `fmdv-mode` open as a
   branch with a draft pull request for the diff view, use it for the AHI
   trial from the branch, and decide on merging once items 1 to 4 above
   have a shape you agree with.
