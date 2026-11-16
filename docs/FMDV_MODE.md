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
- `annotations/<safe>.gbk` and `.gff`: each genome's GenBank record and GFF3
  from NCBI, fetched once by `fmdv_reflib.py annotations --lib <lib>` (set
  `NCBI_EMAIL` or pass `--email`; NCBI asks for a contact address on
  scripted requests) and copied into a reference folder when it is made or
  next touched. This is what the stock reference import does for every
  other reference, and it is what fills the annotation row of a Step 2
  table and the gene track in IGV. One detail matters: vsnp3 keys the
  GenBank record by its versioned identifier, `KM268899.1`, while the FASTA
  header and every VCF carry the bare `KM268899`, so the VERSION line and
  the GFF's first column are rewritten to the bare accession at fetch time.
  A file copied in by hand without that change is read and never matches.
  Every one of the 79 records as fetched annotates a single polyprotein
  CDS with no `/gene` qualifier, so a SNP would read `CTT->CAT, unlisted
  gene:L65H, polyprotein, nonsynonymous` in polyprotein numbering. The
  `annotations` step therefore hands each record to `bin/fmdv_proteins.py`,
  which writes the 12 mature proteins in as CDS features (next section), so
  the same call reads `2B:H23Q, 2B (polyprotein 962-1115), nonsynonymous`.
  The fetched files are kept under `annotations/raw/`.

The library path is `fmdv_reflib` in the user config, default `~/fmdv_refs`.

### Mature protein features

FMDV work is done in mature proteins and residue numbers within them, VP1
position 45 or 2B position 24, and the residue in the polyprotein is the
wrong unit. vsnp3's annotation module reads CDS, tRNA, rRNA, repeat_region,
mobile_element and ncRNA features and nothing else, so the `mat_peptide`
features that 11 of the 79 records carry are invisible to it, and where
coding features overlap it keeps the longest, so mature peptides added as
CDS beside the polyprotein would lose to it every time. The polyprotein
CDS therefore stops being a CDS. `bin/fmdv_proteins.py annotate` rewrites
each `.gbk` so the polyprotein is a `misc_feature` with its location,
product and protein_id, and 12 CDS features follow it, L, VP4, VP2, VP3,
VP1, 2A, 2B, 2C, 3A, 3B, 3C and 3D, each with `/gene` and `/product` set
to the protein name, `/transl_table=1`, and a `/translation` sliced from
the polyprotein translation. `/product` also carries the protein's residue
range in the polyprotein, `VP1 (polyprotein 725-943)`, so a table row
still says where a residue sits in the older numbering. 3D runs to the
polyprotein's stop codon so no position in the ORF falls outside every
coding feature; that adds the terminator as its last residue and changes
no numbering. The `.gff` gets the same 12 lines, first column the bare
accession, so the IGV gene track shows 12 labelled blocks. Exactly one
coding feature covers any coding position, so vsnp3 reports it without an
overlap note.

Where the boundaries come from. Ten records carry a complete set of 12
`mat_peptide` features under eight spellings (`Lab`, `LAB`, `L`, `Lab
peptide; leader peptide`, `3B1/3B2/3B3 peptide`, `VPg1` to `VPg3`, which
merge into 3B, and so on), and those are used as given: one A, three Asia
1, one C, four O and one SAT1 (`SAT1-X_MN275121`). `SAT2-II_AF540910`
carries only three and is treated like the unannotated 68. For each of
those 69 the polyprotein translation is aligned (global, BLOSUM62, gap
open 11, extend 1, free end gaps) against every donor of the same serotype,
or every donor when the serotype has none, and the donor with the highest
identity supplies the boundaries: each protein starts at the first target
residue aligned at or after the donor residue that opens it. Fifty-four
genomes came from a same-serotype donor at 86 to 99 percent identity; the
nine SAT2 and six SAT3 genomes came from the SAT1 donor at 82 to 83
percent. Every transfer is checked before anything is written: the 12
proteins must be in order, start at residue 1, end at the last residue,
tile the polyprotein with no gap or overlap (which also puts each in
frame), and each must be within 8 percent or three residues of its donor's
length, whichever is larger. A genome that fails is reported and left as
fetched; none of the 79 failed. The resulting table is
`annotations/protein_coords.json`, and `fmdv_proteins.py table --lib <lib>
--vp1-crosscheck` prints it beside the `VP1_coords.json` that
`fmdv_consensus.py` derives independently with minimap2 from the VP1 panel:
on the five references that had one, every VP1 start agreed, and three
ends agreed exactly while two differed by six bases, the panel record for
those two genomes being two residues short at the 3' end.

Checked on 15 November 2026 on `FMDV_Demo_Synthetic2` against
`SAT1-I_KM268899`: position 3957, which read `unlisted gene:H984Q,
polyprotein`, reads `2B:H23Q, 2B (polyprotein 962-1115)` (2B spans
polyprotein residues 962 to 1115, so 984 is its residue 23); the 2,711
annotated columns of the sorted table name all 12 proteins; the Step 2 log
carries no annotation self-check warning, because vsnp3's check translates
each CDS with `cds=True`, which a mature protein fails for want of a start
or stop codon and is counted as uncheckable; and IGV on `SYN-01_nanopore`
draws the 12 blocks between the two UTRs.

Two things to know. Numbering in the tables is now within the protein, so
notes written against polyprotein numbering stop matching, and a project
has to re-run Step 2 to see the change; the `/product` text gives the
polyprotein range for the conversion. And vsnp3 applies the translation
table's initiator rule to a CDS's first codon, so where a mature protein
happens to begin with ATG, CTG or TTG, a variant in that codon reports the
mutant residue as M and the type as `start codon lost` (position 3835 on
KM268899, the CTG that opens 2A, is one). The protein, the residue number
and the reference residue are still right. Declaring `/transl_table=1`
holds this to 23 of the library's 869 internal first codons; under the
table 11 vsnp3 assumes when a CDS says nothing, it would be 101.

The step is idempotent: a record that already carries `/gene="VP1"` is
left alone, `--force` rewrites from `annotations/raw/`, and the rewritten
pair is copied over any stale copy in a reference folder. A library built
before this step gains it with `fmdv_reflib.py annotations --lib <lib>`;
`--no-proteins` keeps the records as fetched.

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

In FMDV mode the Step 1 Results table is laid out for long reads. After
Reference come Serotype and Lineage (the chooser's answer, from
`fmdv_reference.json`), Identity (reads to the chosen reference over the
VP1 panel vote, marked when under 0.90 as distant), Reads, Avg Depth,
Mapping %, Error rate (mismatches and indels per aligned base from samtools
stats: about 2% on a clean sample, 6% or more on a mixed sample or a distant
reference), Zero Cov %, Genome Cov (vsnp3's figure, any read), Genome called
and VP1 called (from `fmdv_consensus.json`: positions with depth 10 or more
and an unambiguous call), Quality SNPs, Ambiguous SNPs (vsnp3's AC=1 calls;
hundreds on one sample against single digits on the rest is a mixed
infection), Thin calls (calls raised to the N floor by the long-read model)
and Groups last. Dup %, Avg Read Len, R1 Q20 and R2 Q20 are not shown in
FMDV mode: for long reads they read "not marked", R2 does not exist, and Q20
sits near 80% on every sample. A mixed-serotype sample carries a mark on its
serotype with the shares in the tooltip. Every column sorts; the three
parsed figures (`fmdv_reads`, `fmdv_error_rate`, `fmdv_thin_calls`) and the
rest of the `fmdv_` fields are in the CSV and XLSX exports. On the synthetic
set the three faults each show in one glance: the mixed sample at 6.25%
error, 610 ambiguous and 40 thin calls; the VP1 dropout at 68% genome and
0% VP1 called; the distant reference at 0.893 identity and 9.5% error.

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

**Same-serotype genomes from GenBank** (`bin/fmdv_genome_db.py --source
<fmdv-gbbase.fasta> --lib <lib> --ref <ref> [--topotype I] [--register]`):
one VCF per topotype places a sample among lineages but says nothing about
its relatives within one. This adds the complete genomes of the reference's
serotype from NanoFMDV's collection, taking the records that
`fmdv_vp1_context.py` sliced VP1 from (so each passed the VP1 check and
carries the same name as its VP1 slice in the tree), writing them to
`<lib>/genomes_context/`, calling each against the reference with vsnp3
step1 in FASTA mode and copying the VCFs to
`<lib>/vcf_db_folders/<ref>/<SEROTYPE>_genbank/` with a `SOURCE.json`
listing every genome. Simulated reads are perfect and uniform, so these
VCFs pass every quality filter by construction and carry no mixed
positions; the folder name and the JSON say so. `--register` appends the
database to the fork's config disabled, for the user to tick. For
SAT1-I_KM268899 that is 39 genomes (topotypes I 5, II 3, III 7, IV 2, VII
1, X 3, 18 without a topotype in their header) in 25 seconds with eight in
parallel. Against the reference they carry 7 to 840 SNPs; MT602086.1
(TAN/27/2012), the genome the walkthrough samples match on VP1, differs at
7 positions genome-wide. The 1,471 genomes across serotypes mean an O
reference would get about 900.

Run through vsnp3 step2 directly (run `2026-11-15_07-36-54_SNP-SAT1-genbank`
in the AHI walkthrough: the three SAT1 samples, the eight panel VCFs and
the 39 GenBank VCFs, the same command the GUI issues), the 50 VCFs gave a
51-taxon alignment of 2,707 positions in ten seconds. Run from the GUI
instead, with `SAT1_genbank` ticked under Reference databases, then
Build and Run, the pane stages the same 50 VCFs and vsnp3 forms the same
groups with the same members (run `2026-11-15_09-06-36`); the six samples
mapped to other serotypes are left out as called against another
reference. The samples sit 0 to
1 SNP from MT602086.1 and 6 to 7 from the reference; the next genome is
383 SNPs away (SAT1-XI_MH053323), then the Cameroon topotype X genomes at
about 405. Two things the run showed. The three test samples and
MT602086.1 are the same virus to within sequencing error, which is what a
within-lineage comparison is for and what the nine-genome database could
not say. And the defining SNP signatures, derived from one genome per
topotype, caught two of the 39 GenBank genomes (OP105164.1 into SAT1-III,
LC770123.1 into SAT1-VII) and none of the seven genomes labelled topotype
III, so they are that one genome's private SNPs, as the Defining SNPs
paragraph predicted. They should be rederived from the larger set before
anyone reads a group tree.

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

Rederived on 15 November 2026 from the 78 panel VCFs plus the 39 GenBank
SAT1 genomes (`fmdv_define_snps.py` reads both; `--panel-only` restores
the old behaviour). Three changes came with the larger set. The passing
rule now mirrors vsnp3's exactly (QUAL over 150, AC 2, single-base REF and
ALT, MQ 56 or more): reads simulated from a genome sit near MQ 54 at some
positions, and a QUAL-only rule had chosen positions vsnp3 then ignored, so
the first rederived workbook formed no SAT1-X group although four genomes
carried the SNP. A member uncovered at a position abstains instead of
blocking it, since a topotype 15% from the reference loses a third of the
genome; and a second signature position is added only when every member
captured so far carries it, so positions sharpen a group and never shrink
it. Genomes whose header gives no topotype are neither members nor
counter-examples. The reference's own topotype still gets no group. Result
for SAT1-I_KM268899, with the captured count being what vsnp3 then put in
each group on the 50-VCF run `2026-11-15_07-51-35_SNP-SAT1-genbank-v2`:

| group | labelled genomes | captured | signature |
|---|---|---|---|
| SAT1-II | 4 | 2 | 1390, 1207 |
| SAT1-III | 8 | 4 | 5838 |
| SAT1-IV | 3 | 2 | 2334 |
| SAT1-VI | 1 | 1 | 3008, 452, 2874 |
| SAT1-VII | 2 | 2 (+1 unlabelled) | 1813, 2788 |
| SAT1-X | 4 | 4 (+4 unlabelled) | 1145 |
| SAT1-XI | 1 | 1 | 3504, 3444, 3450 |

The unlabelled genomes the signatures caught are informative: the four
Nigerian 2015 genomes (MF678823 to MF678826, the panel's SAT1-NA among
them) fell into SAT1-X with the Cameroon 2016 genomes, which is what their
VP1 distance had suggested, and LC770123.1 fell into SAT1-VII. Half the
genomes of topotypes II, III and IV are still in no group: those topotypes
span 1937 to 2009 and no single SNP passing vsnp3's rule is shared by all
of them and nothing else on this reference. Presence-based grouping on one
reference has that limit; the all-samples tree does not. No serotype-level
group exists, as before.

**IGV for a database genome.** Clicking a variant of a reference-database
genome in a Step 2 table opens IGV on that genome. It has no Step 1 folder
in the project, but the library kept the alignment vsnp3 made when the
database was built (`<lib>/panel_build/<ref>/<genome>/alignment_*/`), and
in FMDV mode the viewer loads that BAM, labelled "reads (simulated from its
genome)", with a note saying what it is: reads simulated from the genome's
own GenBank sequence, uniform in depth, so the base at a variant site is
the genome's own against the reference. The library is added to the
viewer's serve roots for this; the windowed-reads path is not used, since
it looks only under the project's step1 folder.

**The ONT label and the Step 1 folder.** vsnp3 3.37 names an ONT sample's
VCF `<sample>_nanopore_zc.vcf`, so every Step 2 table labels the sample
`<sample>_nanopore` while its Step 1 folder is `<sample>`. Clicking such a
sample in a table used to open IGV saying it had no Step 1 folder. The
sample-folder resolver now strips that suffix as a last try, and the file
lookups inside a resolved folder use the folder's own name, so the stats
workbook, the BAM, the reads ticket, the VCF editor and the variant lookup
all answer for the label the tables use.

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

## VP1 typing

Serotype, topotype and lineage are defined on VP1, so the first answer for a
sample is its VP1 type, as in NanoFMDV. `bin/fmdv_vp1_type.py` searches the
sample's VP1 consensus (`<sample>_VP1.fasta`, at least 50% called) with
blastn against one database built once from every record of
`lib/vp1_context/<SEROTYPE>.fasta` (the NanoFMDV GenBank VP1 set, sliced per
serotype by `fmdv_vp1_context.py`, with serotype, topotype, lineage,
country, year and isolate per record from the TSVs beside them) plus the 79
panel VP1s. The database lives under `lib/vp1_context/_typing/` and is
rebuilt when a set is newer than it.

The nearest record names the serotype and is reported as the nearest
relative. The lineage comes from the nearest ANNOTATED record within 3%
identity of the best hit: a fifth of the O set carries no topotype (whole
genome deposits, often the sample's own study), and on the Illumina trial
three of six samples hit one of those at 100% and would have been typed
"NA". If no annotated record is that close the lineage is left unassigned
and the nearest annotated record is still reported. Agreement, the share of
the top 20 annotated hits carrying the same lineage, is reported so a VP1
between lineages reads as such.

What it gives that the reads vote cannot: the vote names the nearest of 79
complete genomes, the typing names the nearest of about 9,000 VP1 records.
On the Illumina trial the vote said O/ME-SA/Ind-2001/e and the typing said
Ind-2001/D, the depositors' own sublineage; the vote said PanAsia-2/FAR-09
and the typing said PanAsia-2/ANT-10 at 99.8%; the Kenya 2016 SAT1 samples
typed to KEN/PRB GenBank records at 97 to 99% where the nearest library
genome was at 94%.

Step 1 types each sample after its consensus is written (never failing the
sample). `POST /api/projects/<p>/fmdv/vp1_type` retypes a whole project as
a background job, for samples run before typing existed or after the sets
change; the Step 2 VP1 tab has the button. Results are in
`fmdv_vp1_type.json` per sample, in the QC rows as `fmdv_vp1_*` and
`fmdv_topotype`, in the fmdv route as `vp1_typing`, and in the Step 1
Results table as the "Topotype (VP1)" column. A sample whose VP1 serotype
disagrees with the reads vote gets a note: a mixed sample, or a consensus
built on the wrong serotype.

Needs BLAST+ (blastn, makeblastdb) on the backend's PATH; the fork's
`conda_setup/environment.yml` carries it, and the development server has
the system copy.

## Step 2 in FMDV mode leads with VP1

The Step 2 pane in FMDV mode opens on "VP1: typing and tree": the typing
table for the project's samples and the VP1 tree per serotype. The
whole-genome SNP comparison on one lineage reference is the second tab,
"Whole-genome SNPs", unchanged in what it does. The order follows the
question: which serotype and lineage is this, then, for samples that share
a lineage, how far apart are they. The Kenya Illumina trial is the case for
it: the whole-genome Step 2 put two SAT1 samples 384 SNPs apart and 380 to
466 from every library genome, and the VP1 tree put each beside a Kenya 2016
GenBank record as a sister pair.

The Step 2 Results pane carries the same two tabs, bound to the same
state, so the two panes always show the same kind of run. The VP1 view
shows one tree run at a time with sections Typing, Tree, Neighbours,
Sites and Files. `bin/fmdv_vp1_tables.py`, run by the tree script after
the tree, writes the neighbourhoods (per sample, every record within 3%
of its nearest, at most 20 and at least 5, by identity over the compared
bases of the alignment) and four site tables over the samples and the
union of their neighbourhoods in tree order: sorted (VP1 order) and
cascade (sites ordered by how many rows carry the minority state, as
vsnp3's) at the nucleotide and the amino acid level. Site numbering is
the first row's VP1, gaps removed; amino acids come from the alignment
frame, since the panel VP1s are whole ORFs.

The neighbourhood tree. After every tree build the full Newick is pruned
to the samples, their neighbourhoods and one representative of every other
topotype in the run, and written as `VP1_neighbourhood.tre` with context
leaves relabelled `lineage|country|year|accession`. Pruning merges the
dropped branches so distances between the kept leaves are unchanged. The
Tree section opens it by default with the first sample highlighted; the
full tree is the second button. O for three samples: 659 leaves to 59.

The VP1 tree's context backbone now follows the typing: topotypes the
samples were typed to keep the full backbone (20 per topotype or lineage
bucket), every other topotype keeps two for orientation (`--context typed`,
the default; `--context all` is the old behaviour). The O tree for three
samples went from 631 context sequences to 407.

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

In the GUI the same run is the "Build VP1 tree" button under the scope
picker in the Step 1 pane. Its serotype list is the serotypes Step 1 placed
samples on, with counts; one serotype per tree, since VP1 of two serotypes
does not align in a way that means anything. `POST
/api/projects/<p>/fmdv/vp1_tree` starts the script as a background job with
the env's `bin` first on PATH, then the backend's own (raxmlHPC). The
backend finds that env as `<conda base>/envs/fmdv_gui` (CONDA_EXE's base,
then miniforge3, miniconda3, anaconda3, mambaforge under the home
directory), or wherever `FMDV_GUI_TREE_ENV` points; `GET .../fmdv` reports
`vp1_tree.ready` and the builder it will use, and the button says what is
missing when it is disabled. The route refuses with a plain message when
no sample of that serotype has 50% of VP1 called. When the job finishes the
Step 2 Results list refreshes and the new VP1-tree comparison is in it.

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
The tree does not take a context set whole. Identical sequences collapse
to one (`VP1_context_selection.tsv` in the output lists what each kept
sequence stands for). A backbone keeps up to `--per-topotype` (20)
sequences per topotype, chosen to span the topotype's genetic range: the
longest first, then repeatedly the sequence farthest by 11-mer distance
from everything chosen so far. Country and year play no part in the choice
and stay in the selection file for the reader. Each sample's `--neighbours`
(20) nearest context sequences by 11-mer Jaccard are then added from the
whole set. The backbone shows the lineages; the neighbours keep the fine
structure around the sample that the cap would otherwise remove.
`--no-collapse` keeps identical sequences apart, and 0 for either count
turns that part off. The selection file says why each sequence is there
(`backbone`, `neighbour:<sample>:<score>`).

On the AHI walkthrough with eight threads: SAT1, 394 context records, 384
distinct, 136 backbone plus 20 neighbours, 167 sequences in all, 90
seconds; O, 4,930 records, 4,508 distinct, 574 backbone plus 20
neighbours, 621 sequences, eleven minutes. The SAT1 backbone draws most on
Zimbabwe, Nigeria and Uganda, the O backbone on India, Iran, Sri Lanka and
Pakistan, which is where the sequenced diversity of each serotype lies.
The three SAT1 samples are identical on VP1 to SAT1-NA_MT602086.1
(TAN/27/2012, sliced from a complete genome whose header carries no
topotype) and one site in 657 from SAT1-I_KM268899, SAT1-I_MF592618 and
SAT1-I_MF592629. The two O samples are identical to several O/ME-SA/PanAsia
sequences from the United Kingdom. RAxML writes the Newick unrooted, so a
sample can appear as a child of the root; read placement from nearest
neighbours or after rerooting, not from the root split. Before the
selection the full SAT1 set (406 sequences) took 6.5 minutes, and a full O
tree would have run for hours.

## Reports

Two reports, both HTML and PDF (weasyprint, in the vsnp3 env), both
rewritten whenever their inputs change.

The sample report, `bin/fmdv_sample_report.py`, is written at the end of
Step 1 and again when a VP1 tree that includes the sample finishes (a run
restricted to the sample's serotype is preferred over an all-serotype run).
It holds, in order: the call (serotype, lineage, nearest record, the record
that named the lineage, agreement); the flags (QC level, mixed with the
shares, distant with the identity, provisional); the QC numbers and the
lines they were judged against; the reference the reads chose and the
serotype composition; the neighbourhood table; the sample's neighbourhood
tree as an SVG, midpoint-rooted, bootstrap 50 and over; the VP1 amino acid
differences from the record that named the lineage, or from the nearest
record in the alignment when that one is not in it; the VP1 sequence in
print with the consensus genome beside it as a file; versions, the typing
database's build date and the fixed caveats. Written as
`<sample>_FMDV_report.html` and `.pdf` in the sample folder; the Step 2
VP1 view's Typing table links both.

The run report, `bin/fmdv_run_report.py`, is for the outbreak team: a
summary in words (serotypes and lineages present, samples sharing a
lineage, how many calls carry a note), one table of calls with the notes
that change how a call is read (two serotypes in the reads, no close
genome, lineage unassigned, QC fail or review), and the run-level
neighbourhood tree per serotype with the samples highlighted. QC detail
stays in the sample reports. Written as `<project>/FMDV_run_report.html`
and `.pdf` at the end of every Step 1 batch in FMDV mode and after every
VP1 tree; linked from the VP1 view beside the typing table.

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

## Synthetic benchmark

`bin/fmdv_simulate.py` builds a MinKNOW-style run with a known answer key, for FMDV mode. Every file name and every FASTQ header contains SYN. These are simulated reads. They are not a real outbreak and not a real sample, and they must not be cited as one.

Regenerate with the same seed:

```
python3 bin/fmdv_simulate.py \
  --panel <lib>/panels/genomes.fasta \
  --primers <lib>/primers/nanofmdv_L.fasta \
  --collection <NanoFMDV Refs/all_fmdv.fasta> \
  --host-fasta <bovine segment FASTA> \
  --out <run folder> --seed 20261115 --scenario all --depths 20,200,2000
```

`--scenario` is `all`, `clean`, `crosstalk`, `coinfection`, `dropout`, `distant`, or `depths`. The answer key is written beside the run folder as `SYN_truth.json`, `SYN_truth_table.tsv`, and `SYN_snp_table.tsv`.

### Method

Three real complete genomes, none of them invented.

The outbreak founder is panel genome `SAT1-I_KM268899`. The other serotype, used for crosstalk and co-infection, is panel genome `O-EA-2_MH053318`. The distant founder is GenBank `DQ989316` (Asia 1 isolate IND 21-89, from NanoFMDV's complete-genome collection). It was not assumed. Each run aligns it to all 79 panel genomes and keeps it only if the nearest is 15 to 20 percent away. The identity used for that decision is aligned-base identity (matches divided by aligned bases), with terminal gaps ignored, so a short record is not scored as distant just because its ends are missing. Global identity is reported and is not the rule. On 15 November 2026 the nearest panel genome was `ASIA1-NA_MF372126`, at 15.79 percent aligned divergence (aligned identity 0.8421, global identity 0.8411, edit distance 1,315, 48 internal indels). Nothing in the SAT1 context set met the window: the farthest aligned divergence there was under 10 percent, and several apparent 15 percent figures were missing ends rather than substitutions.

A short transmission chain is built from the founder. Each edge is 3 substitutions, inside the required 2 to 10. Positions are drawn from the seed, restricted to the amplicon insert and at least 80 bp from any placed primer, so `samtools ampliconclip --tolerance 50` does not remove them. One substitution on each edge is inside VP1 (founder coordinates 3178 to 3834). `SYN-03` is an exact copy of `SYN-02`. `SYN-05` also loses three nucleotides, `GCT` at founder position 1019, in the L1a and L1b insert.

| sample | parent | substitutions from parent | VP1 site |
|---|---|---|---|
| SYN-01 | founder | 0 | |
| SYN-02 | SYN-01 | 3 (1763 C>A, 3731 G>A, 4621 G>C) | 3731 |
| SYN-03 | SYN-02 | 0, identical genome | 3731 (shared) |
| SYN-04 | SYN-02 | 3 (1199 T>A, 3608 C>A, 6937 G>A) | 3608 |
| SYN-05 | SYN-04 | 3 (2684 T>G, 3629 A>C, 6987 G>C) plus the deletion | 3629 |

Sample ids do not name a serotype or a lineage. Underscores are not used, because import turns them into dashes.

Primers are placed with the clipper's rule (`best_site` in `bin/fmdv_clip_primers.py`, minimum fit 0.8). On `SAT1-I_KM268899` that places the same 25 primers as the cached BED and leaves out the same eight. Alternative oligos at one site collapse to the best fit, so a tile is not sequenced once per oligo. The seven amplicons are S 1-327, L1a 315-1373, L1b 765-2576, L3b 2291-4886, L4 3878-4445, L4 3878-6068, and L5 5159-7292. The oligo sequence stays on the amplicon. IUPAC is resolved to the founder base where the two are compatible. The 3' RT primer (`FMD_UNI_RT`) does not meet fit 0.8 on these GenBank records, which have no polyA tail, so positions 7293-8156 are uncovered on every sample. That is the scheme, not the dropout.

Reads are Badread 0.4.2 with the `nanopore2023` error model and qscore model, which is Badread's R10.4.1 model. The identity distribution is `98,99.5,1.5`. Fragment length is set above the amplicon, so Badread's linear-reference rule returns the whole amplicon and a short product is not cut in half. Adapters, junk, random reads, chimeras, and glitches are off. Depth is per amplicon (`--quantity {depth}x` on that amplicon alone). Overlaps are deeper. Badread's base target plus a few deletion-shortened reads lands a little above the label (about 201 reads for a 200x amplicon).

No bovine read set was in the local reference folder. Host reads are simulated from public `Bos taurus` chromosome 1, `NC_037328.1` positions 1,000,001-1,100,000 (ARS-UCD2.0), at fragment length 1,500 bp. FMDV is 35 percent of reads, inside the requested 20 to 50 percent.

The folder is a MinKNOW layout: `fastq_pass/barcodeNN/SYN_barcodeNN_chunkNNN.fastq.gz` in chunks of 4,000 reads, and `sample_sheet_SYN.csv` with `barcode` and `sample_id`. Import ignores the extra columns.

### Scenarios

| sample | barcode | what it is | depth |
|---|---|---|---|
| SYN-01 | barcode01 | clean founder | 200x |
| SYN-02 | barcode02 | 3 substitutions from the founder | 200x |
| SYN-03 | barcode03 | identical to SYN-02 | 200x |
| SYN-04 | barcode04 | 3 further substitutions | 200x |
| SYN-05 | barcode05 | 3 further substitutions and the 3-nt deletion | 200x |
| SYN-06 | barcode06 | founder plus 3 percent of reads from O-EA-2 | 200x |
| SYN-07 | barcode07 | 70 percent founder, 30 percent O-EA-2 | 200x |
| SYN-08 | barcode08 | founder with L3b and L4 omitted | 200x |
| SYN-09 | barcode09 | distant founder alone | 200x |
| SYN-12 | barcode12 | clean founder | 20x |
| SYN-13 | barcode13 | clean founder | 2000x |

The 20x and 2000x labels are the clean founder only. The other scenarios stay at 200x. `--depths` can add or drop the depth series.

SYN-08 drops the L3b and L4 products. On the founder that opens 2577-5158 (2,582 bp, 31.7 percent). Together with the scheme's uncovered 3' end the sample has 42.3 percent of the genome at zero coverage.

### Expected calls

These are the calls the key predicts. They are not a GUI result. This session could not reach the lab server, so project `FMDV_synthetic_1` was not imported or scored there. Do not treat the table below as a measured score.

Scope `FMDV`. The chooser votes on VP1, so the distant sample's call is the panel genome whose VP1 collects the reads, which should be an Asia 1 genome, with `distant` true if read identity to that genome is under 0.90. The nearest whole genome is `ASIA1-NA_MF372126`.

| sample | expected genome | distant | mixed | coverage note |
|---|---|---|---|---|
| SYN-01, SYN-02, SYN-03, SYN-04, SYN-05, SYN-12, SYN-13 | SAT1-I_KM268899 | no | no | about 89 percent of the reference covered, because of the 3' scheme gap, so the coverage flag is review rather than pass if the threshold is 90 percent |
| SYN-06 | SAT1-I_KM268899 | no | no | 3 percent is under the 5 percent mixed flag, even if the other-serotype read count exceeds 50 |
| SYN-07 | SAT1-I_KM268899 | no | yes | 30 percent O-EA-2 on VP1 |
| SYN-08 | SAT1-I_KM268899 | no | no | 42 percent uncovered, under the 70 percent fail line |
| SYN-09 | nearest Asia 1 genome | yes | no | reads are 15.8 percent from the nearest panel genome before sequencing error |

SNP distances on the founder coordinates should follow the tree: 0 between SYN-02 and SYN-03, 3 between SYN-01 and SYN-02, 3 between SYN-02 and SYN-04, 3 between SYN-04 and SYN-05, 9 between SYN-01 and SYN-05. The VP1 tree should place SYN-02 with SYN-03, then SYN-04, then SYN-05, if Step 2 is run on `SAT1-I_KM268899` and the context includes those samples. Defining-SNP groups should follow the same edges. The 3-nt deletion is not a substitution. The consensus caller is substitution-only, so the deletion is expected to be missing from the consensus. Score that miss. Do not treat a missing deletion as a pipeline success.

Depth: 200x and 2000x on covered bases should clear the depth-100 pass line. 20x sits on the depth-20 line, and partial overlap at amplicon edges can put some positions under it. Mapping rate should clear 20 percent on the SAT1 samples, because FMDV is 35 percent of reads and those reads match the chosen genome. The distant sample may fall under that line if reads stop mapping.

### What it cannot test

- Adapter trimming. Adapters were left off so the reads look like MinKNOW pass reads and the key stays interpretable.
- Junk, random-read, chimera, or glitch filters. Those rates were 0.
- A deletion in the consensus. The caller does not emit one. The key expects a miss at founder position 1019.
- Barcode misclassification beyond the planted 3 percent and 30 percent mixtures.
- The 3' RT amplicon. The primer does not place on a GenBank record with no polyA, which is also what the clipper does.
- Live GUI scores, until the folder is imported on the lab server as project `FMDV_synthetic_1`. Do not touch `AHI_walkthrough` or `FMDV_test_run2`.

The intended server path is `~/fmdv_run_test/synthetic_run1` on WGS3, with the answer key beside it. A copy made off the server is still simulated data and still carries SYN.
