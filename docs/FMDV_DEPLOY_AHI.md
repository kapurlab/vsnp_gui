# Deploying FMDV mode at AHI: the plan

For the Animal Health Institute, Sebeta, Ethiopia: one GPU workstation,
Ubuntu 24.04 x86_64, no reliable internet, SAT1 outbreak samples first.
This plan takes the local-first principle from
`AHI_local_first_deployment_2026-11-14.md` (data and software stay on site,
drives carry software in, the network carries support and small results)
and applies it to the fork as it stands on 16 November 2026. It replaces
that note's Phase 1 line about a standalone NanoFMDV install: the FMDV path
now lives inside vsnp_gui, and the standalone pipeline is not deployed.

## What AHI runs

| Piece | Size on WGS3 | Where it comes from | Why |
|---|---|---|---|
| bdtools suite at `/srv/ahi` | about 21 GB, all nine tools | built at Penn State in an Apptainer sandbox bound to `/srv/ahi` | the dashboard, the installs, the tests, the updates |
| vsnp3 env (vsnp3 3.37 with the site patches) | 2.1 GB | bdtools, from bioconda | Step 1, Step 2, minimap2, samtools, bcftools, raxmlHPC, openpyxl, and weasyprint, which the vsnp3 package pulls in, so the reports need nothing more |
| vsnp_gui on the `fmdv-mode` branch | the checkout | bdtools, pinned to a tag cut from the branch | the GUI; FMDV mode is a project flag, stock projects run unchanged |
| the fork's own env `fmdv_gui` | 96 MB | `conda_setup/environment.yml`: mafft, iqtree, seqkit, BLAST+ | VP1 alignment, trees when raxmlHPC is absent, VP1 context slicing and typing |
| the FMDV reference library | 5.0 GB, of which the `panel_build/` alignments are 4.4 GB | built at Penn State from a NanoFMDV checkout (40 MB) and NCBI, once | 79 lineage genomes with GenBank annotations, VP1 panel, per-serotype GenBank VP1 sets (9,177 records), the typing database, the Step 2 reference databases per lineage reference, the defining-SNP sheets |
| test data with answer keys | SYN2 189 MB; the Pirbright MinKNOW folder 6.6 GB; the SAT1 SRA reads | WGS3 | `bdtools test` for the suite, and an FMDV smoke test that proves the install on site, offline |

Everything above fits beside the rest of the drive the local-first note
already plans (under 100 GB in all). The 4.4 GB of library alignments are
what IGV shows for a database genome; they can be left off a first drive
and the feature degrades to "no reads for library genomes".

## Where it runs

Two jobs, two machines. Basecalling (MinKNOW, Dorado) needs the GPU and
sits with the MinION, on the desktop that also drives the Illumina
instrument, or a MinION laptop if one exists; which it is was still being
confirmed on 16 November 2026. Analysis (the GUI, vsnp3, the chooser, the
trees, the reports) is CPU work and lives on the Supermicro server, which
Abdi is installing with Ubuntu and giving Penn State sudo on, over
Tailscale, in the week of 16 November. Basecalled runs reach the server as
a folder, by a share from the basecalling machine, an rsync, or a drive;
the importer reads the MinKNOW layout wherever it lands. Projects, the
library and the reports stay on the server. Nothing in the analysis path
needs a GPU.

The first session on the server, with Asamenew or Abdi watching:

1. `lsb_release -a`, `nproc`, `free -g`, `lsblk`, `df -h`, `nvidia-smi`
   if present: the platform, the cores, the memory, the disks and whether
   the server has a GPU at all (it decides whether re-basecalling can run
   there too).
2. Tailscale up and a speed test both ways against WGS3 with a 1 GB file
   and `rsync --partial`: the number that decides whether the install is
   pushed over the link or waits for the drive.
3. The accounts: `vkapur`, `tstuber`, `lli` with sudo, SSH keys, password
   SSH off after the first login, as the setup plan says.
4. `/srv/ahi` on the large disk, owned by a `kapurlab` group, and
   `/data` or equivalent for runs and projects, with the sizes in mind:
   one SUP-basecalled MinION run is 50 to 150 GB of POD5 and a few GB of
   FASTQ.
5. The written log of the session starts here and becomes AHI's guide.

If the speed test allows it, the install goes onto the server over the
link in the same week: bdtools from GitHub, the two envs from conda, and
the library and test data pushed from WGS3 with `rsync --partial`; about
10 GB in all, the library's IGV alignments left for the drive. If it does
not, the drive image is built as below and the server waits for January,
with reads analysed at Penn State in between.

## Decisions to take first

1. **One GUI or two.** Recommended: AHI's `vsnp_gui` entry in `tools.yml`
   points at a tag cut from `fmdv-mode` (for example `v0.5.0-fmdv.1`).
   One install, one dashboard card, one projects folder; the MTBC and MHC
   work runs on the same GUI with FMDV mode off. The alternative, a second
   tool named `fmdv_gui`, doubles the env and the projects root for no
   gain. Tod's merge decision changes nothing for AHI either way: AHI pins
   a tag, and later moves to a `main` tag if the mode is merged.
2. **Over the link now, or wait for January.** With sudo on the
   Supermicro this is the speed test's call, taken in the first session
   above: about 10 GB over the link installs FMDV mode on the server this
   month; otherwise the drive image below arrives with Asamenew in January
   and reads are analysed at Penn State in between. The image is built
   either way, as the restore copy.
3. **Where the library lives.** `fmdv_reflib` in the GUI config names it.
   Recommended `/srv/ahi/refs/fmdv`, beside the vsnp3 references, owned by
   the site, read by every user.

## Code work before an image is built

Small, and each is a line in the handoff's not-done list until done.

- `fmdv_panel_build.sh` carries development paths; make it read the vsnp3
  path, the fork path and the library from arguments or the config.
- The backend's `_fmdv_tree_env` finds the fork env by name under the
  conda roots it knows; confirm it finds `/srv/ahi/envs/fmdv_gui` as
  bdtools lays it out, or add the site root to its search.
- The library build is four commands in `FMDV_MODE_HANDOFF.md`, "How to
  run it"; wrap them as `bin/fmdv_library_build.sh <nanofmdv checkout>
  <library>` so Penn State and AHI run one thing, and so the step that
  needs NCBI (`annotations`) is clearly the one done at Penn State.
- An FMDV smoke test, `bin/fmdv_smoke_test.sh <project root>`: import
  SYN2, Step 1, Step 2 on SAT1-I, the SAT1 VP1 tree, the reports, then
  compare the calls and the Step 2 distances with the answer key and print
  pass or fail. This is what AHI runs after the restore and after every
  update, offline.
- A tag on `fmdv-mode` for bdtools to pin, and the `tools.yml` entry for
  the AHI site manifest with updates frozen (`updates: report`).

## Building the image at Penn State

On WGS3, which is the same platform as the AHI workstation.

1. `sites/ahi.conf` with `SITE_ROOT=/srv/ahi`, as the local-first note
   says, and the AHI manifest pinning `vsnp_gui` to the fork tag.
2. In the Apptainer sandbox bound to `/srv/ahi`: `bdtools install` for the
   suite, then `mamba env create -p /srv/ahi/envs/fmdv_gui -f
   conda_setup/environment.yml`.
3. The library: `fmdv_library_build.sh` with a NanoFMDV checkout, online
   for the GenBank annotations and the context sets; then
   `fmdv_panel_build.sh` and `fmdv_genome_db.py` for the lineage references
   the trial will use (SAT1-I to start; SAT2, O and A as the context sets
   suggest), and `fmdv_define_snps.py` for each. Register the databases in
   the site config.
4. The GUI config for the site: `fmdv_reflib`, the vcf_db_folders entries,
   the FMDV QC thresholds as defaults, the projects root.
5. `bdtools test all` to fill the test cache, then `fmdv_smoke_test.sh` on
   the sandbox install. Both must pass before the image is written.
6. Write `/srv/ahi` to the drive as a tar archive with checksums, with the
   SYN2 run folder and its key, the Pirbright run folder, and the two
   expected report sets beside it. A second identical drive stays at Penn
   State as the restore image.

## At AHI

1. Restore `/srv/ahi`, verify checksums, `bdtools doctor`.
2. `bdtools test all` against the cached data; then `fmdv_smoke_test.sh`.
   The smoke test's pass line is the acceptance.
3. Start the GUI from the bdtools dashboard with the site root set; open
   it on the workstation, or from another PC on the lab LAN by the
   workstation's address. No Open OnDemand.
4. Walk one real run through with the AHI staff watching: Import ONT run
   from the MinKNOW folder, Grab, Run, the VP1 tab, Build VP1 tree, open
   the sample and run reports, print one. The written log of that session
   becomes AHI's own guide, as the setup plan says.
5. Freeze updates. Penn State builds and tests updates on WGS3, then pushes
   one tool at a time with `rsync --partial` over Tailscale, overnight, and
   AHI reruns the smoke test after each.

## What comes back

Reports (HTML and PDF), consensus and VP1 FASTAs, QC tables: kilobytes to
megabytes, over Tailscale, with AHI's approval per export as the
governance note requires. Raw reads only when a question needs them, by
drive or as a subsample.

## Open questions for AHI

- Which machine the MinION is on and its operating system, and whether the
  Supermicro has a GPU.
- The server's hostname, the sudo accounts, and whether PCs on the LAN
  will open the GUI (they will, since the server has no screen of its
  own: the backend binds to the LAN address, behind the dashboard's
  login).
- Whether the lab wants the sample report in print by default, which sets
  the PDF as the file the dashboard opens.
- Which serotypes beyond SAT1 to prepare Step 2 databases for before the
  drive is written. Each is a few minutes at Penn State and a few hundred
  megabytes; all four SAT and O and A together are under 3 GB.
