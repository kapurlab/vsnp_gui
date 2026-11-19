# FMDV mode: the training video script

A scene-by-scene script for a how-to video of FMDV mode in vsnp_gui: what
is on screen, what the presenter clicks, and the words the narrator says.
It is written so that a person can record it with QuickTime or OBS, or so
that the narration can be rendered by a speech engine and laid over screen
recordings made from the same script. Public and synthetic data only.

Two projects on the demonstration server, so the video never waits:

- `FMDV_demo_inputs` starts empty and is where the inputs are entered live.
- `FMDV_demo_results` holds the same inputs already run through Step 1,
  the VP1 trees and a whole-genome Step 2. Scenes 9 onward cut to it.

The inputs, all public:

| input | what it is | how it enters |
|---|---|---|
| `20261114_SAT1_run1` | a MinKNOW-style run folder of three barcodes built from the SRR34371563 nanopore reads, with a sample sheet | MinKNOW run tab |
| `SRR18685705` | an Illumina pair from the Kenya 2016 study, in a folder on the server | FASTQ files tab |
| `EU448369` | a GenBank genome, O1/BFS 1860/UK/67 | Sequences tab, by accession |
| `lab_consensus_O.fasta` | a complete genome FASTA standing in for a consensus from another laboratory (GenBank HM625673) | Sequences tab, dropped as a file |

Nothing in the sample names or the narration says what the AHI trial
samples will be. The run folder's names come from its sample sheet.

Conventions. One scene is one recording. "Say" is the narration, in plain
words, one idea a sentence. "Show" is what the camera holds on. "Do" is the
click sequence. "Cut" marks where a wait is removed or sped up, with a
timer caption. Timings are a guide; the narration sets the pace.

## Scene 1. What this is (20 s)

Show: the app at `localhost:8201`, the project list, no project selected.

Say: This is vsnp_gui in FMDV mode. It takes sequencing reads from a
foot-and-mouth disease virus sample and answers three questions. Which
serotype and lineage is this. How does it compare with what GenBank
already holds. And, for samples that share a lineage, how far apart are
they, base by base.

## Scene 2. A project in FMDV mode (30 s)

Do: type `FMDV_demo_inputs` is already there; click it. Point at the
Step 1 panel's reference block, which reads "FMDV mode: choose the
lineage reference per sample", and at the scope picker.

Say: A project is a folder. This one is in FMDV mode, which changes one
thing from the ordinary workflow: the reference genome is chosen for each
sample, not once for the project, because FMDV is too diverse for one
reference. The scope says where to look. Auto means any serotype; a
serotype or a lineage narrows the choice.

## Scene 3. The Inputs panel (20 s)

Show: the Inputs panel with its four tabs.

Say: Inputs come in four ways, and the panel shows one at a time. A run
folder straight from the sequencer. Read files you already have. A
sequence rather than reads: a FASTA, a GenBank record, or an accession.
And public reads from the Sequence Read Archive. Whatever the source, the
reads land on the right, under Ready to run.

## Scene 4. A MinKNOW run (45 s)

Do: MinKNOW run tab. Paste `/home/vxk1/fmdv_run_test/20261114_SAT1_run1`.
Preview. Hold on the table of three barcodes with their names. Import.
Wait for "Imported". Open the "?" once, briefly, then close it.

Say: The common case. Point at the run folder MinKNOW wrote. The preview
lists each barcode, how many chunk files it has, and the name the run's
sample sheet gives it. Underscores become dashes, because the pipeline
cuts a name at the first underscore. Nothing is written until Import.
Each barcode becomes one FASTQ in the project. The question mark holds the
details for anyone who needs them.

## Scene 5. FASTQ files (30 s)

Do: FASTQ files tab. Pick a folder on the server, Custom path, paste
`/home/vxk1/fmdv_run_test/demo_fastq`, Link. Point at the pair appearing
under Ready to run.

Say: Reads you already have are linked or uploaded. Here a paired Illumina
sample from a folder on the server. R1 and R2 are matched by name. The
files stay where they are; the project keeps a link.

## Scene 6. A GenBank accession (45 s)

Do: Sequences tab. Type `EU448369` in the accession box. Preview. Hold on
the row: country, year, host, isolate filled from GenBank, VP1 cut at its
coordinates, the note that the library already holds it. Import.

Say: Sometimes the sequence is published and there are no reads. Type the
accession. The record is fetched from NCBI, or from the library's cache
when the server is offline, and its metadata is read: country, year,
host, isolate. VP1 is cut out of the genome by blastn. A genome is
imported as simulated reads and goes through the same Step 1 as everything
else, badged so that nobody mistakes the numbers for a sample's. Every
field can be edited before import.

## Scene 7. A FASTA from another laboratory (30 s)

Do: still on Sequences. Drop `lab_consensus_O.fasta` on the drop zone.
Preview. Change the name to `lab-consensus-O`. Import.

Say: A consensus genome from another laboratory works the same way. Drop
the file, check what the preview found, give it a name, import. A VP1
sequence on its own is accepted too; it is typed and placed on the tree
without reads.

## Scene 8. Grab and Run (60 s, with a cut)

Do: Ready to run now lists six samples. Click Grab into Step 1. Watch the
Samples list fill with Not started. Click Run. Scroll to Live Logs and
hold while the chooser line for the first sample appears, then the
alignment. Cut: timer caption "about 6 minutes", speed through the rest
of the log, land on the Results table filling in.

Say: Grab stages the reads into Step 1. Run does, for each sample, what
the ordinary pipeline does, with the FMDV steps around it. First the
reads vote for the closest of 79 lineage genomes, by their VP1. Then
primers are clipped, the reads are aligned, variants are called. Then a
consensus is built, VP1 is cut out of it, and the VP1 is typed against
nine thousand GenBank records. Each sample gets a report as it finishes.
Six samples take a few minutes.

## Scene 9. The Results table (60 s)

Cut to `FMDV_demo_results`. Show: the Step 1 Results table. Hover the
column headings as they are named. Point at the QC chips, the Serotype
and Lineage columns, Topotype (VP1), the Simulated badge on the two
imported genomes with QC n/a.

Say: One row per sample. The QC chip says pass, review or fail, and why,
on lines set for an eight kilobase amplicon genome: how much of the
genome was covered, depth, mapping rate. Serotype and Lineage are the
reads' vote. Topotype VP1 is the typing, which names the nearest GenBank
record's lineage. Identity is how close the reads sit to the chosen
reference. The two imported genomes are badged Simulated and their QC
reads not applicable, because depth and coverage describe the simulation.

## Scene 10. Files and IGV (45 s)

Do: open Files on one nanopore sample. Click Stats, close. Click FMDV
report, scroll it once, close. Click IGV, let it load, zoom into VP1,
point at a variant.

Say: Behind every row are the files. The stats workbook. The one-page
FMDV report: the call, the flags, the QC numbers, the neighbourhood, a
tree, the VP1 differences. And the alignment in IGV, where any variant
can be checked against the reads.

## Scene 11. Step 2, the VP1 tab (60 s)

Do: scroll to Step 2. The VP1 tab is open. Point at the typing table.
Choose SAT1 in the tree selector, click Build VP1 tree. Cut: timer
"about 2 minutes". Step 2 Results refreshes with the new run.

Say: Step 2 opens on VP1, because serotype, topotype and lineage are
defined on VP1. The typing table is the first answer. The tree places the
samples among the lineages: the library's panel, plus a backbone of
GenBank records chosen to span each topotype, plus each sample's nearest
neighbours. One serotype per tree.

## Scene 12. The VP1 tree, neighbours and sites (75 s)

Do: in Step 2 Results, VP1 view. Tree: the neighbourhood tree with the
samples highlighted; switch to the full tree and back. Neighbours: the
table for one sample. Sites: open the nucleotide cascade table, then the
amino acid one. Files: the Newick and alignment.

Say: The neighbourhood tree keeps the samples, their nearest records, and
one representative of every other lineage, so every label can be read.
The full tree is behind the second button. Neighbours lists, for each
sample, the records within three percent of its nearest, with country,
year and host. Sites shows the positions that vary among the samples and
their neighbours, in VP1 order or in cascade, at the nucleotide and the
amino acid level, coloured as vsnp3's tables are.

## Scene 13. Whole-genome SNPs (60 s, with a cut)

Do: Step 2, Whole-genome SNPs tab. The reference select shows
SAT1-I_KM268899. Tick the SAT1 and SAT1_genbank databases. Build. Run.
Cut: timer "under a minute". Step 2 Results: open the SNP tree, then the
sorted table, then the cascade table.

Say: For samples on the same lineage reference, the whole-genome
comparison is the ordinary vsnp3 Step 2. The databases add the other
lineage genomes and GenBank's complete genomes of the serotype, called
against the same reference, so a sample is placed among everything known
for its lineage. The tree and the SNP tables read as they do for any
other organism.

## Scene 14. The run report (30 s)

Do: Step 2 VP1 view, click the run report HTML. Scroll: the summary
sentence, the table of calls, the serotype trees.

Say: The run report is for the outbreak team. One paragraph says which
serotypes and lineages are present and which samples share one. One table
gives the calls with the notes that change how a call is read. The trees
follow. The sample reports hold the detail.

## Scene 15. Close (15 s)

Show: the project list.

Say: That is FMDV mode. Reads or sequences in, a typed and placed sample
out, with the evidence one click away. The documentation in the docs
folder has the design and the measurements behind every step.

## Production notes

- Record at the browser's full width in light theme, 1440 by 900 or
  larger, with the page zoom at 100 percent.
- The narration for each scene is rendered to one audio file per scene
  with the macOS speech engine (voice Evan Enhanced, or a Personal Voice
  when one is built); the scene's video is trimmed or sped up to match.
- Cuts: Step 1 and the VP1 tree are recorded in full and sped up 20 to 40
  fold with a timer caption; everything else plays in real time.
- Before recording the input scenes, reset `FMDV_demo_inputs`: archive it
  and create it again in FMDV mode, so the preview tables show new rows
  and nothing says "already in step1/".
