# Importing sequences into FMDV mode

Design note. The FMDV mode takes reads (FASTQ from a MinKNOW run, a local
file or SRA). Two other inputs come up in outbreak work and are not covered:
a FASTA somebody already has (a consensus genome or a VP1 sequence from
another lab or pipeline), and GenBank accessions for context sequences or
for published isolates from the same outbreak. This note says how both
should enter a project so that typing, the VP1 tree, the neighborhood
tables, the site tables and the reports treat them like any other sample,
and what happens when NCBI is out of reach. Built on 18 November 2026 as
described below; what the build left out is listed at the end. The user's
description is the "Importing sequences that are not reads" section of
`FMDV_MODE.md`.

## The one decision that shapes the rest: sample or context

An imported sequence plays one of two roles, and the rest of the design
follows from which.

A **sample** is something the project is about: a consensus from another
lab, or a published isolate from the outbreak under study. It is typed, it
gets a neighborhood, it is highlighted in the tree, and it gets a sample
report. It lives in the project, under `step1/<sample>/`, beside the
samples that came from reads.

A **context sequence** is a comparator: a GenBank record that should stand
in the tree with the library's 9,177 VP1 records, labelled
`lineage|country|year|accession` like them, and be a candidate when a
sample is typed. It is not reported on. It lives in the project, under
`fmdv_context/`, so the project carries its own comparators and the shared
context sets in the library at `~/fmdv_refs` are never changed by an
import (the library keeps only a cache of fetched GenBank records, below).

The import panel asks for the role once per import. FASTA files default to
sample; accessions default to context. Either can be switched per row in
the preview. A published isolate from the outbreak is an accession imported
as a sample; that is the case the default does not guess.

## Where a sample without reads lives

`step1/<sample>/` is already the unit every downstream script reads: the
typer wants `<sample>_VP1.fasta`, the tree wants that plus
`fmdv_consensus.json`, the reports want `fmdv_reference.json` and
`fmdv_vp1_type.json`. Grab and the Step 1 batch both recognize a sample
folder by the presence of a `*.fastq.gz`, so a folder without reads is
skipped by both, and the Results table lists only folders that hold a stats
workbook. A sequence-only sample can therefore sit in `step1/` today
without being run or shown, which is exactly the starting point.

The importer writes, per sample:

| file | content |
|---|---|
| `<sample>_imported.fasta` | the record as given, header kept |
| `<sample>_VP1.fasta` | VP1, the whole record when it is VP1 length, else the slice |
| `fmdv_import.json` | provenance: source file or accession, original header, length, N count, class (vp1, P1, genome, other), slice coordinates, template record and identity, the metadata as entered, the sha256 of the input, who imported it and when |
| `fmdv_consensus.json` | the compatibility record the tree reads: `source: imported`, `serotype`, `reference: ""`, `vp1_length`, `vp1_called_percent` (non-N fraction of the slice), `length`, `called_percent` |
| `fmdv_vp1_type.json` | written by `fmdv_vp1_type.py --sample-dir`, run at import |

No `fmdv_reference.json`, no alignment folder, no stats workbook. The
import is a background job in the ONT importer's shape: preview, edit,
commit, poll.

The `fmdv_consensus.json` written here carries a `serotype` key that the
reads path does not; the tree's `collect_samples` reads it before falling
back to the reference name. That is the one change the tree needs for
samples. The typer needs none.

### Whole genome or VP1 only

The importer classifies each record by length, as `fmdv_vp1_context.py`
does for the collection: 600 to 700 nt is VP1, 1,200 to 2,500 is P1,
7,000 or more is a genome, anything else over 700 is other. A VP1 record is
checked against the panel VP1s by blastn (own serotype, 70 percent of the
record aligned) and taken whole. A longer record has VP1 cut out by its
best blastn hit against the checked VP1 set of its serotype, extended to
the template's ends, kept when the slice is VP1 length and at least 80
percent of the template aligned. This is the slicing already proven on
2,980 collection records, so the code moves out of `fmdv_vp1_context.py`
into a small shared module rather than being written twice. minimap2 is
not used for the cut: it missed most records below 87 percent identity in
the SAT1 trial.

A record under 600 nt, or one whose slice fails the VP1 check, is refused
in the preview with the reason. A record of another virus fails the check
and reads as such.

A genome does not stop at the slice. Vivek's question on 18 November 2026,
after the first trial: why not make perfect reads from the record, mark
them as such, and run them through the regular workflow? That is what the
GenBank genome databases already do with `vsnp3_step1.py -f`, and it is
simpler than a parallel sequence-only path: the chooser, vsnp3, the
consensus, typing, the sample report and the whole-genome Step 2 all run
unchanged, and most of the special casing for a sample without reads goes
away. So a genome-class record becomes simulated paired reads in
`download/`, with two provisos. The simulator is ours, not vsnp3's:
`vsnp3_fasta_to_fastq.py` replaces every N and ambiguity code with a
random base, so a masked consensus would come out as SNP clusters; ours
cuts at every non-ACGT base and simulates nothing across the cut. And the
provenance has to travel: every read header says `SIMULATED`,
`fmdv_import.json` says `reads: simulated`, the Results row is badged and
its QC reads "n/a", because depth, mapping rate and coverage describe the
simulation. The MQ 54 and pass-every-filter caveats of the genome databases
apply here too.

A record shorter than a genome (VP1, P1, a fragment) stays sequence-only:
simulated reads from 639 nucleotides would give a sample with 8 percent of
the genome covered and nothing for the whole-genome workflow to do, while
typing and the tree need only the VP1.

## Context sequences in the project

The library's context sets are `~/fmdv_refs/vp1_context/<SEROTYPE>.fasta`
and `.tsv`, built once from NanoFMDV's collection and shared by every
project on the server. A project's own additions go under
`<project>/fmdv_context/<SEROTYPE>.fasta` and `<SEROTYPE>.tsv`, same record
names (`<SEROTYPE>-<topotype or NA>_<accession>`), same TSV columns, with
`region_source` set to `imported_vp1` or `imported_sliced_from_genome` and
a `source` column naming the accession fetch or the file. The folder sits
beside `step1/` and `step2/` and travels with the project.

The tree and the tables read the library set by name and gain the project
set: `fmdv_vp1_tree.py` already takes `--project`, so it reads
`<project>/fmdv_context/` without a new flag, merges the records with the
library's for the serotype, and never prunes them from the backbone, since
whoever added them wants them in the tree. `fmdv_vp1_tables.py` reads the
project TSV beside the library TSVs so the neighborhood table and the leaf
labels carry the metadata. The typer gains `--project-context <dir>`: the
project set is small, so it is made into its own BLAST database at import
(under `fmdv_context/_typing/`, rebuilt when a set is newer), the sample is
searched against both databases and the hits are merged by bitscore. A
project's context is then a candidate when its samples are typed, and the
library's typing database is untouched.

An accession already in the library (the 79 panel genomes, the 9,177
context records, the `genomes_context` set) or in the project's own set is
not added as context again: it is in the tree and the typing database
already. The TSVs carry the accession column that makes this a lookup. It
can still be imported as a sample. The first trial showed why: EU448369,
the published isolate beside the short-read test samples, is in the O
context set, and the user wants it typed and highlighted, not merely
present among 4,930 comparators.

The alternative, writing additions into the library beside the collection
as `<SEROTYPE>.added.fasta`, would share a deposit across projects and
enter the typing database with no code change, since the typer globs the
directory. It was rejected (Vivek, 18 November 2026): the library is the
site's shared, versioned asset, built once and carried to AHI on a drive,
and an import from one project should not change what every other project
sees. Promoting a project's context into the library is a deliberate,
separate step and is out of scope here.

## Accessions, and what happens offline

`fmdv_reflib.py annotations` already fetches GenBank records with a plain
HTTPS `efetch` through `urllib`, with `NCBI_EMAIL` and a 0.4 s delay. The
sequence import uses the same helper, moved into a backend module so the
two share it: `rettype=gbwithparts`, `retmode=text`, ids joined by comma
in batches of 50 (efetch takes up to 200, and 50 keeps a batch under the
size that fails on a slow link), `NCBI_API_KEY` honored when set, 60 s
timeout per batch, three retries on 429 and 5xx with doubling delays.
Biopython's `Entrez` is not used: the backend env does not have Biopython,
the plain fetch already works for the annotations, and the offline behavior
is the same either way. The flat file is parsed by a small parser for the
fields the import needs (LOCUS length, the `source` feature's
`/organism`, `/serotype`, `/strain`, `/isolate`, `/country` or
`/geo_loc_name`, `/collection_date`, `/host`, `/note`, and ORIGIN). The
year is the four-digit year of `/collection_date`; the country is the part
of `/geo_loc_name` before the first colon.

Every fetched record is cached as `~/fmdv_refs/genbank_cache/<accession>.gb`
before anything else happens, so a second project, or a retry after a
failed import, reads from disk. The cache is also how a record arrives on
a server with no link: a GenBank flat file downloaded elsewhere and dropped
into the import panel is parsed the same way, and the import panel accepts
`.gb` and `.gbk` beside `.fasta`.

When NCBI is unreachable the answer is a clear error, not a queue. Before
the fetch job starts, the backend probes `eutils.ncbi.nlm.nih.gov` with a
5 s timeout. If the probe fails, the response lists the accessions that
are in the cache or the library (those import now) and the ones that are
not, with one instruction: fetch the GenBank record on a connected machine
and import the file, or import the FASTA with the metadata filled in by
hand. A queue that fires when the link returns was considered and rejected:
on a server where the link is down more than up, a queued import is the
normal state, nobody watches it, and a project whose context arrives a week
later is worse than one whose user knows today what is missing. Partial
success inside a batch is reported per accession in the same shape as the
SRA download report (fetched, cached, failed), and never fails the
accessions that did come through.

## Metadata

The tree labels context leaves `lineage|country|year|accession`, the
neighborhood table shows lineage, country, year, host and isolate, and the
reports quote the same fields. Samples are labelled by their sample name.
So a context sequence needs the four fields filled, and a sample needs a
name and benefits from the rest.

For an accession the fields come from the GenBank `source` feature. The
lineage is the one field GenBank does not carry in a structured way: it is
in the title or the `/note` for some deposits and absent for most. The
import leaves it `NA` unless the user fills it, which is what the
collection does for one record in six, and the typer and the tree already
treat `NA` as unannotated.

For a FASTA the preview shows one editable row per record: sample name
(proposed from the header's first token, underscores to dashes, as the ONT
importer does), role, serotype (from the blastn check, overridable),
lineage, country, year, host, isolate, accession. Two shortcuts fill the
row: a NanoFMDV-style header
`SEROTYPE|TOPOTYPE|COUNTRY|YEAR|HOST|ACCESSION|LENGTH|ISOLATE` is parsed
as the collection's are, and a sidecar TSV next to the FASTA with a
`sample` column and any of the other columns fills matching rows. A
template for the sidecar is served the way the ONT sample sheet template
is. Nothing is inferred from a name that does not parse; blank fields stay
blank and the label drops them, as the tables do today.

A name already present in `step1/` or `download/` refuses the import, as
the ONT importer does.

## The user interface

One block on the Inputs page, "Import sequences", under "Import ONT run"
and above "SRA Download", shown in FMDV mode only. It has a file chooser
and drop zone for `.fasta`, `.fa`, `.gb` and `.gbk` files, and a text area
for accessions, one per line. Both lead to the same preview table: one row
per record with the fields above, a role switch, a tick box, the slice
result (coordinates, template, identity, or the refusal), and whether the
name or accession already exists. Import runs as a job; the block polls it
and reports per row, as the ONT importer does. A third tab inside the
existing panel was considered; the Inputs page has no tabbed panel, the
ONT importer is a block, and a block beside it is what a user will look
for.

In the Step 1 Results table, a sequence-only sample appears as a row with
the read columns blank and a source badge (FASTA or GenBank) where the read
type goes. Serotype, Lineage, Topotype (VP1) and VP1 called are filled from
the typing; Identity, Reads, Avg Depth, Mapping, Error rate, Zero Cov,
Genome Cov, Genome called, Quality SNPs, Ambiguous SNPs and Thin calls are
blank, not zero, and the QC verdict is "not applicable", not pass or fail.
The rows come from `step1/*/fmdv_import.json` and are appended in
`step1_status` beside the workbook rows; the dispatch planner's list of
skipped folders leaves them out so Run does not report them as skipped.
The Step 2 VP1 tab lists them already, because it lists every sample with
a typing result. "Build VP1 tree" includes them because the tree reads
`fmdv_consensus.json`.

The sample and run reports include a sample whose folder has
`fmdv_import.json` and no `fmdv_reference.json`; the chooser and QC
sections read "imported sequence, no reads". That is a change to report
code and is listed, not made, here.

## Out of scope for the first cut

- Importing reads by accession; SRA Download does that.
- Promoting a project's context or samples into the library's context set.
- Editing metadata after import; delete and import again.
- Protein sequences, multi-segment or concatenated records, IUPAC codes
  other than N in the VP1 called figure.
- Fetching anything beyond the nucleotide record (no PubMed, no BioSample).
- Any change to the deployment plan or to the report code.

## Code changes implied

Backend:

- `backend/app/seq_import.py` (new): record parsing (FASTA, GenBank flat
  file, NanoFMDV header, sidecar TSV), classification, the VP1 check and
  slice (shared with `bin/fmdv_vp1_context.py`, which imports it), the
  efetch helper with cache and probe (shared with `bin/fmdv_reflib.py`),
  `preview()`, `build_plan()`, and a `commit` entry point run as a job.
- `backend/app/main.py`: `POST /api/projects/{p}/import_sequences/preview`
  (files or accessions in, rows out, with the offline report),
  `POST /api/projects/{p}/import_sequences` (rows in, job out),
  `GET /api/fmdv/sequence_sheet_template`; `step1_status` rows for
  `fmdv_import.json` folders; `_step1_dispatch_plan` skips them silently;
  `fmdv_project_get` returns `imports` and the project context counts for
  the preview's existence check and the VP1 tab.
- `bin/fmdv_vp1_tree.py`: `collect_samples` reads `serotype` from
  `fmdv_consensus.json` when present; `collect_panel` reads
  `<project>/fmdv_context/<SEROTYPE>.fasta` and `.tsv` and exempts those
  records from pruning.
- `bin/fmdv_vp1_tables.py`: `load_meta` reads the project TSVs too.
- `bin/fmdv_vp1_type.py`: `--project-context <dir>`, a second database
  built from the project set, hits merged by bitscore; the Step 1 batch
  and the retype route pass it.
- `bin/fmdv_sample_report.py`, `bin/fmdv_run_report.py`: no change for a
  genome import, which has a chooser result once Step 1 has run; a
  sequence-only (VP1) import still has no report. Report code, flagged for
  its owner.
- `backend/app/test_seq_import.py` (new): header and flat-file parsing,
  classification and slicing against a small panel, the offline branch of
  the fetch, the preview's collision rules.

Frontend:

- `frontend/src/SeqImport.jsx` (new), mounted in `App.jsx` on the Inputs
  page under `OntRunImport`, FMDV mode only, with `onImported` calling
  `loadAll()`.
- `App.jsx` Results table: a row renderer for `read_type: "sequence"`
  (blank numeric cells, source badge, verdict "n/a").
- Tests in the step1 results test file for the sequence row.

Docs: a section in `FMDV_MODE.md` once built, pointing here.
