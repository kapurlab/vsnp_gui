# FMDV mode

A project whose `project.json` carries `"mode": "fmdv"` runs the ordinary
vsnp3 step1 and step2, with one difference: the reference genome is chosen
**per sample**, not once per project.

## Why

Foot-and-mouth disease virus is too diverse for one reference. Measured on
14 November 2026 against NanoFMDV's 79 lineage genomes (Shaw et al. 2025):

- across serotypes only the non-structural half of the genome aligns at all,
  about 4,300 of 8,200 nt at roughly 83%; the capsid, including VP1, does not;
- within a serotype, topotypes differ by up to 21% (SAT1/X vs SAT1/I: 79.2%);
- nanopore reads stop mapping somewhere past 10 to 15% divergence. Lineage
  genomes 15 to 20% from a reference lost 1,000 to 4,000 of 8,156 positions
  to zero coverage; the reference itself, as a control, lost 5.

MTBC lineages differ by under 0.1%, which is why vsnp3's normal pattern (one
generic reference, lineages sorted afterwards by defining SNPs) works there
and cannot here. The pattern holds one level down: choose the lineage
reference per sample, and within a lineage (above 95% identity) vsnp3
behaves exactly as it does for MTBC. On the one public SAT1 dataset, vsnp3
called 6 SNPs against its lineage reference, exactly the 6 real differences
in the medaka consensus validated against GenBank `PV524918`.

## The reference library

`fmdv_reflib.py build --refs Refs/fmdv.fasta --lib ~/fmdv_refs` turns the 79
genomes into:

- `lineages.json`: the hierarchy FMDV > serotype > topotype/lineage > genome
  (A 19, O 25, SAT1 9, SAT2 9, SAT3 6, Asia1 5, C 6), which the picker shows;
- `panels/vp1.fasta` and `panels/genomes.fasta`: the 79 VP1 sequences and
  the 79 complete genomes, headers = safe names, built with
  `fmdv_reflib.py panels --lib <lib> --vp1 Refs/fmdv_vp1.fasta` (NanoFMDV
  ships the VP1 panel with the same headers as its genome file). The chooser
  maps reads to these;
- `signatures/lineages.sig`: sourmash signatures, `k=21, scaled=10, abund`,
  kept for comparison only. The sourmash chooser was replaced on 14 November
  2026 (see "What happens in Step 1");
- `reference_options/<safe>/`: vsnp3 reference folders, **built lazily** the
  first time a lineage is chosen. Nobody is shown 79 folders; a single-
  serotype outbreak lab ends up with one to three.

The library path is `fmdv_reflib` in the user config, default `~/fmdv_refs`.

## The scope

The project's reference field holds a **scope**, not a folder name:

| scope | meaning |
|---|---|
| `FMDV` | closest lineage anywhere (the default) |
| `SAT1` | closest lineage within SAT1, per sample |
| `SAT1/I` | closest within that lineage (usually one genome) |
| `genome:SAT1-I_KM268899` | pinned: no search, that genome for every sample |

Picking a node above a genome means "closest available within this node,
chosen per sample". That is what makes "SAT1" safe where a single fixed SAT1
genome is not.

## What happens in Step 1

Inside the batch, per sample directory, before `vsnp3_step1.py`:

```
REF_DIR=$(python3 bin/fmdv_choose_reference.py --reads "$R1" --lib <lib> --scope <scope> --sample-dir "$(pwd)")
RUN_ARGS+=(-t "$REF_DIR")
```

The chooser is competitive minimap2, after NanoFMDV (Shaw et al. 2025):

1. The first 20,000 reads are mapped (`-c --secondary=no`, preset `map-ont`,
   or `sr` when the mean read length is under 300) to the 79 VP1 sequences.
   Primary alignments with MAPQ 10 or more and at least 100 aligned bases
   count. The in-scope genome whose VP1 collects the most aligned bases is
   the reference, provided it has at least 20 reads; otherwise the same vote
   is taken on the complete genomes; otherwise no reference is chosen and
   the sample is skipped with a message in the log.
2. The same reads are mapped to the 79 complete genomes for the serotype
   composition and the identity of the reads to the chosen genome.

VP1 decides because it is the serotype- and topotype-determining region:
only reads carrying discriminating sequence vote, and the conserved 3' half
of the genome, which splits almost at random between serotypes, does not.
The serotype share is also read off VP1, because FMDV recombines in the
non-structural region and a serotype A strain's 3' half can sit closer to
an O genome than to the library's A genome (the Pirbright A samples read as
21% "O" over whole genomes and 1% on VP1).

`fmdv_reference.json` in the sample directory records the choice, `vote`
(`vp1` or `genome`), `f_match` (now the chosen genome's share of the
in-scope vote, 0 to 1), `vote_reads`, the runner-up, `identity`,
`serotype_share` (VP1) and `serotype_share_genome`, `mixed`, and the top
five hits on each panel. Two flags, both **warnings, never refusals**:

- `distant`: identity of the reads to the chosen genome under 0.90. This
  mixes read error with divergence: on the Pirbright R9 era reads the
  correct Asia1 lineage sits at 0.91 and lineages 10% or more away at 0.86
  to 0.89; the SAT1 reads sit at 0.98 on their own lineage. R10 reads will
  move everything up. A sample from a lineage with no complete genome in
  GenBank (SAT1 topotype IX is one such lineage) has no close genome
  anywhere and must still run.
- `mixed`: a second serotype holds 5% or more of the VP1 reads, with at
  least 50 reads. Barcode crosstalk in the Pirbright runs sat at 1 to 2%.

The folder is handed to vsnp3 as `-t <folder>`, never `-r fasta`: with a
bare `-r`, vsnp3 drops `reference_type` and the Reference and Groups cells
go blank.

**Why not sourmash.** The first chooser sketched the reads (`k=21,
scaled=10, abund`) and ranked `sourmash gather` hits by `f_match`, the
fraction of the reference's k-mers found in the reads. It was right on
SAT1 (0.99 against 0.22 for the runner-up) and wrong on three of six
Pirbright samples on 14 November 2026: both serotype A samples and the O
epithelium sample went to Asia1 Sindh-08. Their true lineages were 15% or
more from the nearest library genome, where exact 21-mers mostly fail, so
the breadth score of the right genome fell to 0.15 to 0.28, while a 1 to 2%
trace of Asia1 crosstalk with a near-identical library genome scored 0.29
to 0.36. Ranking by abundance-weighted overlap would have fixed these
three cases, but alignment tolerates the divergence outright and gives the
composition for free. Kraken and Bracken share sourmash's weakness here
(exact k-mers, lowest common ancestor collapsing to species on the
conserved half) and are the tool for the unmapped reads, not for this.

## What happens in Step 2

Nothing new in vsnp3. The existing mixed-reference veto is correct for FMDV;
a project with samples on several lineage references runs one comparison per
reference. To see where samples land among lineages, the same-serotype
lineage genomes can be added as synthetic samples (`vsnp3_step1.py -f`; they
carry no `N`, so the N-to-random-base behaviour of that path does not bite).

## Do not

- Do not feed a medaka consensus through `vsnp3_step1.py -f`:
  `vsnp3_fasta_to_fastq.py` maps `N` to a random base, so masked regions
  become SNP clusters. Give vsnp3 the reads and the chosen reference.
- Do not register the FMDV folders with `vsnp3_path_adder.py`; pass the
  folder path with `-t`, as vsnp_gui already does for every reference.

## Importing a MinKNOW run

vsnp3 takes one read file per sample and the Inputs page lists `download/`
one file per sample. MinKNOW writes `fastq_pass/barcodeNN/` with each
barcode's reads split into chunk files of about 4,000 reads, so a raw run
folder used to give nothing, or one "sample" per chunk. The barcode-to-sample
step has always been done upstream of vsnp3; the lab's implementation is in
mhc_gui, and `backend/app/ont_import.py` lifts its shape without the MHC
amplicon column. It is not FMDV specific and works in stock mode too.

- **Sample sheet**: `sample_sheet.csv` (or `.tsv`) in the run folder, two
  required columns `barcode,sample_id`; other columns are ignored. Barcodes
  may be written `barcode07`, `NB07`, `BC7` or `7`. A template is served at
  `/api/ont/sample_sheet_template`. Without a sheet the barcode is the name.
- **Names**: underscores in a `sample_id` become dashes at import
  (`ETH_SAT1_001` → `ETH-SAT1-001`), because vsnp3 names a sample from
  everything before the first underscore of its read file. The preview shows
  both spellings. The Step 1 batch now also passes `-n "$(basename "$d")"`,
  so the folder name is the sample name whatever the file is called.
- **Layouts**: `<run>/fastq_pass/barcodeNN/` (also nested one to three
  levels down, as MinKNOW writes experiment/sample/run_id/fastq_pass),
  `<run>/barcodeNN/`, or a flat folder of `*barcodeNN*.fastq.gz`.
  `unclassified` is listed separately and skipped unless ticked.
- **Preview then commit**: `POST /api/projects/{p}/import_run/preview`
  with `{"run_dir": ...}` returns one row per barcode (chunk count, bytes,
  proposed name, whether that name is already in `download/`) and any sheet
  problems. `POST /api/projects/{p}/import_run` with the edited rows
  concatenates each barcode's gzip chunks into `download/<sample>.fastq.gz`
  as a background job (`.part` file, renamed when complete; no read is
  decompressed). A name already present refuses the whole import.
- **UI**: "Import ONT run" on the Inputs page, left column, between the
  local-FASTQ block and SRA Download (`frontend/src/OntRunImport.jsx`).
  After import the files are ordinary inputs: Grab, then Run.

Tested on WGS3 on 14 November 2026 with a synthetic run built from the
SRR34371563 reads (three barcodes of 19,690 reads in five chunks each, an
unclassified folder, a sheet with `barcode01`, `NB02` and `3` as labels and
two underscored names). Preview, import, Grab and Step 1 in scope `SAT1`
gave three samples named as the sheet said, each choosing SAT1/I at
`f_match` 0.98 to 0.99 (sourmash chooser).

## QC in FMDV mode

Stock vsnp_gui judges a sample on depth and mapping rate, which is right for
a bacterial isolate and wrong for an FMDV field sample, where host reads
dominate: an epithelium sample with 28% FMDV reads and 580x depth is a good
sample. Switching FMDV mode on writes a `qc_thresholds` layer into
project.json (`FMDV_QC_THRESHOLDS` in `main.py`, editable per project
afterwards, removed when the mode is switched off):

| Signal | Source | Pass | Review | Fail |
|---|---|---|---|---|
| Genome covered | Genome with Coverage | >= 90% | >= 70% | < 70% |
| Depth | Average Depth | >= 100x | >= 20x | < 20x |
| Mapping rate | 1 - Unmapped % | >= 20% | < 20% | never |
| Distant reference | chooser identity < 0.90 | | review | |
| Mixed serotypes | chooser, second serotype >= 5% of VP1 reads | | review | |

Genome covered comes first because the consensus and the SNP calls are only
as good as the covered fraction; 90% is what a GenBank submission needs, and
under 70% a sample cannot give a submittable consensus or a trustworthy
whole-genome SNP set, which is what a fail should mean (Vivek, 14 November
2026, keeping 70% after seeing the test set). A low mapping rate describes
the sample, not the result, so it reviews and never fails. The two chooser
flags are not faults of the sample: "distant" says the consensus step and a
closer reference are needed before the SNPs are trusted (a lineage with no
genome in the library),
"mixed" says a second serotype is present above the 1 to 2% of barcode
crosstalk. Both reach the verdict engine through its contamination hook.

On the 14 November test set: the four SAT1 samples pass (99.7 to 99.9%
covered, 2,000x to 6,600x); PIR-O-epithelium is review (75% covered,
distant); the other five Pirbright samples fail on genome covered at 52 to
68%, because their amplicon scheme does not reach a third of the genome,
with distant and mixed added as reasons where they apply.

## Step 2 context: the lineage genomes as reference databases

Four samples of one virus give Step 2 nothing to say. What places a sample
is the other lineage genomes, so for each lineage reference in use the 78
other library genomes are called against it once (`bin/fmdv_panel_build.sh
<ref>`: vsnp3 step1 in FASTA mode, about 20 s per genome, 8 in parallel;
output in `<lib>/panel_build/<ref>/`) and the VCFs become vsnp_gui
reference databases under `<lib>/vcf_db_folders/<ref>/`, registered in the
fork's config as `vcf_db_folders` entries with `reference` set:

- `<SEROTYPE>/`: the reference's own serotype, 8 genomes for SAT1-I. This is
  the one to tick. Against SAT1-I the SAT1 topotypes cover 50 to 87% of the
  genome with 390 to 530 SNPs each; a Step 2 run of the four SAT1 samples
  plus these eight gave a 1,353-position alignment and a tree with the
  samples at the root (0.004 from KM268899) and the topotypes ordered II,
  III, IV, VI, XI, X, NA, VII outward.
- `FMDV_all/`: all 78. Built because the MTBC pattern is one global tree
  on one reference, and measured so nobody trusts it for that: against a
  single reference the other serotypes cover 38 to 50% of positions, all in
  the conserved non-structural half, where FMDV recombines freely. The
  resulting 82-taxon tree had no serotype as a clade (A's smallest clade
  held 42 non-A genomes; SAT1, SAT2 and SAT3 were fully interleaved). It is
  a tree of the 3' half, not of serotype, which is a capsid property.
  Serotype placement is the chooser's job (VP1 composition); Step 2 earns
  its keep within a serotype.

**Defining SNPs** (`bin/fmdv_define_snps.py --lib <lib> --ref <ref>`): the
MTBC grouping mechanism carried over. vsnp3 reads row 1 of the
define_filter workbook as a group's signature, one or more positions that
must all be present as SNPs (and `pos!` for ones that must be absent), and
with `-a` builds the all-samples tree plus one tree per group. The script
derives one group per topotype of the reference's serotype from the panel
VCFs: positions ALT in that topotype's genome and in no other covered
genome of any serotype, up to three, ranked by how many genomes are covered
there. For SAT1-I that gave signatures for II, III, IV, NA, VI and VII; X and
XI had none once uniqueness was checked against all 78 genomes, and I is the reference's own topotype, which presence-based
grouping cannot define. Serotype-level groups do not exist against a
single reference and are not needed, since other serotypes are never
mapped to it. With one genome per topotype these signatures are that
genome's private SNPs; they will be refined as real samples accumulate,
and the simulation benchmark should score them. Written into
`reference_options/<ref>/<ref>_define_filter.xlsx`; vsnp3 recognised the
groups in both the GUI run and a direct run.

## Chooser test set

`~/fmdv_run_test` on WGS3 also holds `20261114_FMDV_run2` and
`20261114_FMDV_run3`: MinKNOW-style folders built from the only other ONT
FMDV data in SRA, Pirbright's BioProject PRJNA693428 (serotypes A, O and
Asia1 from cell culture and epithelium, 40,000 reads per barcode in five
chunks, a sample sheet, an unclassified folder in run2). Their reads are 15%
or more from the nearest library genome, carry 1 to 5% crosstalk, and only
15 to 55% of them are FMDV, which is what field samples will look like. The
minimap2 chooser picks the right serotype and lineage for all six in under
1.5 s each. Any change to the chooser should be run against these and the
SAT1 run before it ships; a simulation benchmark (leave-one-out over the 79
genomes, crosstalk and co-infection mixes, depth and error rate) is the
planned next step and is not built yet.

## Consensus and VP1

After `vsnp3_step1.py` returns 0, the FMDV batch runs
`bin/fmdv_consensus.py --sample-dir <step1/sample> --lib <library>`. It
reads what Step 1 left in `alignment_<ref>/` (the reference FASTA, the
zero-coverage VCF, the deduplicated BAM) and writes beside them:

- `<sample>_consensus.fasta`: the reference with vsnp3's substitutions
  applied (QUAL above 150; an `AC=1` ambiguous call becomes N) and every
  position under 10 reads (`samtools depth`, NanoFMDV's published `-d 10`)
  or in the VCF's zero-coverage records set to N. No indels, so the
  coordinates stay the reference's, the consensus agrees with what Step 2
  compares, and VP1 is a slice. It is not a GenBank genome: real insertions
  and deletions are missing. A submission consensus needs a polisher
  (medaka), a separate step.
- `<sample>_VP1.fasta`: the VP1 slice. VP1 coordinates are found once per
  reference by mapping its record in `panels/vp1.fasta` onto the genome
  with minimap2 and cached in `reference_options/<ref>/VP1_coords.json`
  (SAT1-I_KM268899: 3178 to 3834, 657 nt).
- `fmdv_consensus.json`: length, N count, `called_percent`,
  substitutions, `ambiguous_to_n`, `vp1_start`, `vp1_end`, `vp1_length`,
  `vp1_called_percent`.

On the Test_FMDV project (14 November 2026): the four SAT1 samples give
8,156 nt, 99.7% called, 6 substitutions each, VP1 fully called. The six
Pirbright samples, 14% or more from their library references, give 36 to
40% of the genome called at depth 10 (the chooser's genome-covered figure,
52 to 63%, counts any read; the depth-10 mask is stricter) but VP1 99.4 to
99.8% called, with 70 to 239 substitutions. That is the point of the slice:
VP1 is recoverable where the genome is not.

## The VP1 tree

VP1 is the unit of FMDV epidemiology. Serotypes, topotypes and lineages are
defined on it (Samuel and Knowles 2001, J Gen Virol 82:609, with the 15%
topotype cut-off; SAT3 topotypes at 20% or more, Bastos et al. 2003, Virus
Genes 27:283; the reference laboratories' nomenclature in Knowles et al.
2016, Rev Sci Tech 35:741). Whole-genome SNPs serve within-lineage
transmission (Cottam et al. 2008, Proc Biol Sci 275:887), which is Step 2's
job. Recombination is almost exclusively in the non-structural region
(Ferretti et al. 2020, PLoS Pathog 16:e1008235), which is why the 82-taxon
whole-genome tree on one reference had no serotype as a clade: across
serotypes only the recombining half aligns.

`bin/fmdv_vp1_tree.py --project <dir> --lib <library> [--serotype SAT1]`
collects every sample's `_VP1.fasta` with at least 50% of VP1 called
(`--min-called`), adds the 79-genome VP1 panel (restricted to the serotype
when one is given) and, when present, `lib/vp1_context/<SEROTYPE>.fasta`,
the GenBank comparator set for that serotype (below). It aligns with mafft
and builds the tree with
raxmlHPC (GTRGAMMA, 100 rapid bootstraps; the vsnp3 env has it), falling
back to iqtree then FastTree. Output goes to
`step2/<stamp>_VP1-tree[-SEROTYPE]/VP1/` with `VP1_input.fasta`,
`VP1_aligned.fasta`, `VP1_<stamp>.tre` and `vp1_tree.json`, plus a
`run_metadata.json` with `status: ok`, so the folder appears in the Step 2
run list and the tree viewer shows it like any other group.

mafft is not in the vsnp3 env and that env is not changed. The fork has its
own `conda_setup/environment.yml` (mafft, iqtree, seqkit):
`mamba env create -f conda_setup/environment.yml`, then put that env's
`bin` on PATH before running the script. Without mafft the script stops
with that instruction.

On Test_FMDV, all ten samples plus the 79 panel sequences took under ten
seconds and placed every sample with its reference lineage: the SAT1 four
beside SAT1-I_KM268899, the A pair under A-ASIA-Sea-97, the O pair under
O-ME-SA-PanAsia, the Asia1 pair with ASIA1-NA and Sindh-08. The SAT1-only
tree (four samples, nine panel genomes, 2.6 s) is the report view.

The context sets come from NanoFMDV's own GenBank-derived collection
(Shaw et al. 2025, `pipeline_exec/Refs/BLAST/fmdv-gbbase.fasta`, 11,153
records), reformatted offline by `bin/fmdv_vp1_context.py --source <that
file> --lib <library>`. The script needs BLAST+ (blastn and makeblastdb)
on PATH. It names each sequence `<SEROTYPE>-<topotype or NA>_<accession>`,
skips the 79 accessions already in the lineage panel, and writes a
`<SEROTYPE>.tsv` beside each FASTA with serotype, topotype, country, year,
host, accession, length and isolate as the source header states them, plus
the columns below. Records with a plain GenBank header (one in six) keep
their serotype and accession with topotype NA.

Three checks decide what goes in. First, a record of VP1 length (600 to 700
nt) is not taken on length alone: it must align to a panel VP1 of its own
serotype over at least 70% of itself, because the collection holds
VP1-sized fragments from elsewhere in the genome (the SAT1 AF2834xx series
are 609-nt pieces of the 3C region; 202 records hit no panel VP1 at all,
31 only partly, and 2 ASIA1-labelled records matched serotype A). Second,
VP1 is cut out of every longer record (complete genomes, P1 capsid records,
other fragments) by its best blastn hit to the checked VP1 set of its
serotype, extended to the template's ends and kept when VP1 length; the TSV
column `region_source` says `vp1_record`, `sliced_from_genome`,
`sliced_from_P1` or `sliced_from_other`, with the template, identity and
coordinates beside it. Genome slices all start between positions 2,000 and
3,500, P1 slices mostly at 1,576, which is where VP1 sits. Third, records
under 600 nt stay out, since partial VP1 makes gappy alignments.

The build keeps 9,098 sequences: 6,118 VP1 records and 2,980 slices (1,471
from genomes, 927 from P1, 582 from other fragments). By serotype: O 4,930,
A 2,247, Asia1 678, SAT2 557, SAT1 394, C 197, SAT3 95. Dropped: 1,389
under 600 nt, 235 that failed the VP1 check, 128 long records with no
usable VP1 hit, 224 untyped, 79 in the panel. SAT1 has 13 topotypes, 223
of them topotype I; the three topotype IX sequences (FJ798154 to FJ798156,
Ethiopia 2007) are 77.6% identical to their nearest panel VP1, SAT1-XI, so
that lineage was 22% from anything in the panel. `--max-per-lineage N` caps
each topotype at N sequences, longest first, for a quicker O or A tree.
With the SAT1 set in place the AHI walkthrough tree holds 406 sequences
(nine panel genomes, 394 context sequences, three samples) and takes 6.5
minutes with eight threads. The three samples are identical on VP1 to
SAT1-NA_MT602086.1 (TAN/27/2012, sliced from a complete genome whose header
carries no topotype) and one difference in 657 sites from SAT1-I_KM268899,
SAT1-I_MF592618 and SAT1-I_MF592629; the next nearest are topotype I at
about 1%. RAxML writes the Newick unrooted, so a sample can appear as a
child of the root; read placement from nearest neighbours or after
rerooting, not from the root split.

## Primer clipping

AHI's protocol is amplicon based (NanoFMDV's L primer scheme, 33 primers,
kept as `<lib>/primers/nanofmdv_L.fasta`). Primer-derived bases sit in
every read at the amplicon ends, so without clipping the consensus and the
vsnp3 VCF read as reference at those positions whatever the virus carries.
`bin/fmdv_clip_primers.py` runs in the FMDV batch between the chooser and
`vsnp3_step1.py`, adopted from NanoFMDV with two changes:

- the primer BED is found **on the chosen reference**, once per reference
  and primer set, by the rule goprime uses (best window on either strand by
  the fraction of IUPAC-compatible bases), cached as
  `reference_options/<ref>/primers_<set>_<hash>.bed`. On SAT1-I_KM268899
  the coordinates equal NanoFMDV's `top_ref_bind.bed` for every primer both
  place;
- a primer whose best fit is under 0.8 is left out and listed in
  `fmdv_clip.json` (eight on SAT1-I, all serotype-specific primers for A,
  Asia1 and O whose siblings cover the same loci), instead of clipping a
  random stretch of genome the way an unconditional BED does.

Then minimap2 to the reference, `samtools ampliconclip --hard-clip
--both-ends --tolerance 50 --filter-len 50` (NanoFMDV's settings), and the
reads back out as `fmdv_clip/<sample>_clipped.fastq.gz`. Unmapped reads
pass through untouched so the mapping rate vsnp3 reports still means what
it did. The project key `fmdv_primers` names a set under `<lib>/primers/`
or `none`; with one set in the library it is used by default. On the ten
Test_FMDV samples clipping changed no SNP call and no consensus base: those
reads carry no primers. The effect size on AHI's amplicon reads is
unmeasured until the first real run.

## Benchmark against NanoFMDV

NanoFMDV itself (the authors' scripts at `~/nanofmdv_test/pipeline_exec`
on WGS3, medaka 2.2.1, chemistry-matched models, `-d 10` to match ours)
ran on the same ten FASTQs; `bin/fmdv_bench_nanofmdv.py` aligns each pair
of consensus sequences with mafft and counts positions both call that
differ. Result, 14 November 2026, after the consensus rule fixes below:

| sample | same reference | positions both call | differ | NanoFMDV indels | VP1 differ |
|---|---|---|---|---|---|
| four SAT1 samples | yes | 8,105 to 8,106 | 0 | 0 | 0 of 657 |
| PIR-Asia1 pair | yes | 3,001 to 3,006 | 0 | 0 to 1 | 0 |
| PIR-O pair | yes | 3,015 and 3,284 | 0 and 1 | 2 to 3 | 0 |
| PIR-A pair | yes | 3,003 to 3,004 | 5 and 6 | 1 | 3 and 4 |

Reference choice agreed on all ten, and NanoFMDV's BLAST serotype of its
polished consensus matched ours every time. The remaining A disagreements
are at positions 1105 to 1108 (a deletion-rich stretch where the reads are
split 45 to 55 and neither caller has a basis), 3702 (same), and 3706 and
3707, where NanoFMDV prints a 15 to 20% minority base beside that deletion
and the reads support ours. NanoFMDV also masks 26 well-covered positions
(307 to 332) in every SAT1 sample: medaka's chunk join fails at a primer
site and its log says so. The one O disagreement is a 59% plurality at
depth 17, under our pileup floor.

The benchmark found two defects in our first consensus rule, both fixed:

- an ALT that vsnp3 recorded with QUAL at or under 150 (low depth keeps
  QUAL low even when every read agrees) was printed as the reference base.
  Five such positions at depth 9 to 16 on PIR-O. Now N;
- substitutions the caller never recorded (a 65% A at depth 2,584 on
  PIR-A, position 2399). A pileup pass now checks every position: a
  plurality of 75% or more against the consensus base replaces it, 50 to
  75% makes it N, and each case is listed under `pileup_overrides` in
  `fmdv_consensus.json` with `in_vcf` saying whether vsnp3 saw it. These
  are also the positions Step 2 would miss.

What this does and does not show. On SAT1 reads within 2% of their
reference the two pipelines are identical over the genome and over VP1.
On strains 10 to 14% from the nearest library genome and R9-era reads
they agree at 99.8% of called positions, and the disagreements split
between them. NanoFMDV's medaka consensus carries real indels and ours
does not, by design. Neither pipeline has been tested on amplicon reads
with primers present, which is what AHI will produce.
