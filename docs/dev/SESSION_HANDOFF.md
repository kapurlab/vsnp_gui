# Session handover — May 12 2026

Continuation notes for the next Claude session. Read this first, then
`docs/dev/TICKETS.md` for the broader plan.

## TL;DR

The day was about **getting *Mammaliicoccus sciuri* up as a working
reference and validating the full vSNP3 pipeline on a real-data panel** —
turning up and fixing a chain of integration bugs that had never been
exercised by the MTBC / SARS-CoV-2 work to date. Sixteen commits landed.

Two test projects are live on wgs3, both pinned to the new
**`Mammaliicoccus_sciuri_6942A_MS`** reference (cattle isolate, much
better centroid than the type strain NCTC12103):

- **`Mscuiri_test_6942AMS`** — 8 SRA-derived isolates, step1+step2 ✅,
  tree rendered, NOT yet re-run after the alias-map fix on `4320772`.
  Worth a final Setup+Run on step2 to confirm everything is clean.
- **`Shivasharanappa_panel`** — 7 paired Indian bovine mastitis samples
  from the user's collaborator, **downsampled locally to ~200× target
  coverage** before transfer (12 GB → 2.3 GB), pinned to the same
  cattle reference. **Awaiting first Setup+Run.**

`web` branch HEAD: `4320772` _reference_alias_map: handle vsnp3's
NCBI-version stem strip. wgs3 deploy clone in sync. Frontend dist rebuilt
where needed.

## State to verify when picking up

```bash
# Branch + deploy clone match origin/web
ssh wgs3 'sudo git -C /srv/kapurlab/tools/vsnp_gui log --oneline -1'
# expect: 4320772 _reference_alias_map: handle vsnp3's NCBI-version stem strip

# Latest dist bundle on wgs3
ssh wgs3 'sudo ls /srv/kapurlab/tools/vsnp_gui/frontend/dist/assets/index-*.js'

# The two M. sciuri reference dirs exist with full file set
ssh wgs3 'ls /srv/kapurlab/refs/vsnp3/reference_options/Mammaliicoccus_sciuri_NCTC12103/ && echo --- && ls /srv/kapurlab/refs/vsnp3/reference_options/Mammaliicoccus_sciuri_6942A_MS/'

# Both M. sciuri projects exist; Shivasharanappa_panel has the downsampled fastqs
ssh wgs3 'ls /home/vxk1/projects/Mscuiri_test_6942AMS/ /home/vxk1/projects/Shivasharanappa_panel/download/ | head -25'

# Smoke tests still green (T-07 indexer + writer + jobs callback + SSE)
ssh wgs3 'cd /srv/kapurlab/tools/vsnp_gui/backend/app && \
    /srv/kapurlab/tools/vsnp3/bin/python test_provenance_indexer.py 2>&1 | tail -2 && \
    /srv/kapurlab/tools/vsnp3/bin/python test_provenance_writer.py 2>&1 | tail -2 && \
    /srv/kapurlab/tools/vsnp3/bin/python test_jobs_callback.py 2>&1 | tail -2'
```

## What landed this session (May 12 chronological highlights)

Sixteen commits, every one shipped + synced. In rough order:

**Browser xlsx preview (T-09 follow-up wave)**
- `db06542` — initial xlsx → HTML preview endpoint + "View" buttons on step2 outputs. Uses openpyxl-based renderer that walks cells and emits inline-styled `<table>`. Was a placeholder formatting pass.
- `6ddc685` — rotation 90/180 from `cell.alignment.text_rotation` mapped to CSS `writing-mode: vertical-rl`. Auditing the step2 Open buttons: `.tre/.nwk` get the existing tree-view button; html/fasta/nexus/vcf/txt/tsv/csv/log/json/yaml/md/pdf/png/jpg/svg get a new browser-native "View" via `?inline=1` on /download-file; zip/bam/binary drop the Open entirely.
- `672086b` — fix column widths: openpyxl stores column-range widths under a single anchor key with `.min`/`.max` spanning the range. The previous lookup was per-letter and missed the implicit range, so cols got the 80px fallback instead of Excel's narrow 20px. Plus `table-layout: fixed` so colgroup widths are honored.
- `9d9de16` — MIME types filled in on /download-file so inline=1 actually renders (.json → application/json, .svg → image/svg+xml, image types). The provenance "View" button on .json files was downloading instead of rendering.
- `cd10d96` — fix missing app-logo icon in the OOD reverse-proxy context (origin-rooted `<img src="/...">` was 404'ing because OOD's main nginx doesn't serve from the per-session uvicorn's dist).

**SRA download flow** (the big chain — went from "broken silently" to "fast and informative")
- `a44d38a` — SRA expander: rate-limit (process-local 0.4s gap, 0.11s with NCBI_API_KEY), retry-on-429 with exponential backoff, narrow exception catch + SRAExpansionError so failures surface instead of silently falling back to the unexpanded literal accession. Endpoint translates to 502 with `detail`.
- `3061b4d` — frontend: check res.ok and surface backend errors in the SRA Download status line. Was silently swallowing 502s and leaving the spinner stuck forever.
- `7e89493` → `ebe40ee` — DNS in the OOD Singularity container was broken: host /etc/resolv.conf is a symlink to /run/systemd/resolve/stub-resolv.conf, but /run isn't in the cluster's singularity_bindpath. The first attempt at a `source:dest` file bind got rejected by Singularity (file bind requires destination to exist in image). Switched to a directory bind of `/run/systemd/resolve`. Required killing the dashboard Passenger PUN to force config reload (OOD's `~/ondemand/restart_pun` sentinel didn't fire — see polish item below).
- `5b21d15` — parallelize the bash download script via `xargs -P 4`. Was a strict for-loop; with 18 accessions that's serial network + serial fasterq-dump. Sweet spot is 4 parallel workers (past ~6 NCBI throttles per-IP). Also: installed `sra-toolkit` apt package on wgs3 so Method 1 (S3 + fasterq-dump) becomes the primary download path, not ENA-curl-only.

**File-list UX**
- `ea5a9fe` — collapse paired R1+R2 fastqs into a single sample row in the "Files in download/" panel. SRA-naming (`_1`/`_2`) and Illumina-naming (`_R1`/`_R2[_001]`) both detected via one regex.

**Step1 reliability**
- `5936c6e` — provenance_writer._build_step1_inputs accepts SRA `_1`/`_2` fastq naming. The bash step1 batch script already did; the writer only matched literal `R1`/`R2`. Every dispatch on an SRA-named panel was returning DispatchFailed before any job ran. Plus frontend's `step1Run` now surfaces 500 details via alert() (was silently swallowing them, same pattern as the SRA Download fix).
- `83ea531` — step1 status uses T-07's `.provenance/exit_code` sentinel as the authoritative per-sample terminal signal. Drops the log-substring heuristic that was false-positiving on vsnp3's verbose intermediate output and making every sample flicker through "Error" before transitioning to "Complete".
- `45a5592` — bash step1 batch loop: use `wait -n` (rolling pool, true throughput-limited parallelism) instead of `wait $pids[0]` (FIFO replacement, capped by slowest in the head batch). And: 409 guard at the top of /step1/run rejecting a second click while an existing step1 job is `running`. Two concurrent batches racing over the same per-sample dir was the actual cause of the seqkit "FileNotFoundError on temp_fastq_seqkit_stats.txt" we hit.
- `80f59e5` — Run button frontend: disable + relabel "Running…" while step1JobStatus === "running". 5-second polling of /step1/status while the batch is in flight so the disable accurately re-enables after a page reload that lost SSE jobId tracking.

**Reference alias resolution**
- `4320772` — `_reference_alias_map` indexes both the literal FASTA stem and the NCBI-version-stripped stem. vsnp3_step1.py copies the reference FASTA into each sample's alignment dir and renames it to drop the version suffix (`NZ_LS483305.1.fasta` → `NZ_LS483305.fasta`), so the resulting VCF's `##reference=` points at the renamed file. step2's setup was string-matching on the original stem and pushing every VCF into mismatch_report.csv, leaving vcf_source/ empty and crashing vsnp3_step2.py with "After sample filter: 0".

## Today's biology arc

- Set up **`Mammaliicoccus_sciuri_NCTC12103`** as a new reference (type strain, NCTC12103 = ATCC 29062 = DSM 20345, Sanger Centre 2018, chromosome `NZ_LS483305.1`, 2.81 Mb complete circular). Manual setup: download fasta/gbk/gff via `vsnp3_download_fasta_gbk_gff_by_acc.py`, faidx, .genome, best_reference marker, citation.
- Search SRA directly for *M. sciuri* Illumina bovine WGS runs → 205 hits across major bovine studies. Curated to 18 representative SRRs, dropped down to 9 after QC review.
- Ran step1+step2 on the 9-sample panel → tree showed huge branch lengths (~0.40 sub/site of the SNP alignment), suspicious. Investigated and found:
  - Branch lengths are real, not a bug — high *M. sciuri* intra-species diversity.
  - **NCTC12103 (squirrel skin, 1960s) is a poor reference for modern cattle isolates.** Most cattle samples are ~0.40 sub/site away on the SNP alignment.
- Set up **`Mammaliicoccus_sciuri_6942A_MS`** as a second reference (USA cow strain, chromosome `CP099817.1`, 2.77 Mb, SUNY Albany 2022).
- Re-ran on the cattle reference → root moves INTO the cattle clade (sister to the Swiss pair), sister-taxa structure preserved (WI pair, Swiss pair), but **per-sample branch lengths to the rest of the panel are unchanged** because the distances are intrinsic to the samples, not the reference choice. Confirms the panel has real population structure but the high SNP counts are not a methodological artifact.
- One sample (`SRR37536882`, Bangladesh "buffalo mastitic milk") was deep outgroup in both trees. Ran sourmash containment as definitive species check:
  - Cattle control SRR32134413 (known *M. sciuri* from tree): **80.1%** of 6942A_MS reference k-mers present in sample → clean same-species signal.
  - SRR37536882: **47.5%** k-mer overlap → sibling-species territory (probably *M. lentus*, *M. fleurettii*, or *M. vitulinus*). Removed from both projects.
- Confirmed Shivasharanappa's collaborator sent **clean replacement fastqs** for the 7 Indian bovine mastitis samples (the original delivery had been corrupted — wrapped/truncated/quality-scrubbed). Downsampled locally to ~200× target before transfer (12 GB → 2.3 GB, ~5× shrink).

## Live system state (May 12 EOD)

- **Branch**: `web` at `4320772`. Both `origin/web` and the wgs3 deploy clone (`/srv/kapurlab/tools/vsnp_gui/`).
- **Frontend dist**: rebuilt across multiple commits today; check the live bundle hash if anything looks stale.
- **vSNP3 install**: `/srv/kapurlab/tools/vsnp3/` v3.16. Patched. New addition this session: **`sra-toolkit` apt package** (`/usr/bin/fasterq-dump`, `/usr/bin/prefetch`) — gives the SRA download script its primary path (S3 + fasterq-dump) instead of ENA-curl-only fallback.
- **OOD cluster config**: `/etc/ood/config/clusters.d/wgs3.yml` has the updated `singularity_bindpath` including `/run/systemd/resolve` so in-container DNS works.
- **References installed**:
  - `Mammaliicoccus_sciuri_NCTC12103` (type strain — kept for posterity / type comparisons)
  - `Mammaliicoccus_sciuri_6942A_MS` (cattle isolate — recommended default for cattle work)
  - All sibling refs from prior sessions (MTBC, Brucella, NC_045512, Mycobacterium AF2122, etc.)
- **Test projects on wgs3**:
  - `/home/vxk1/projects/Mscuiri_test/` — 8 SRA-derived isolates, step1+step2 done against NCTC12103. After dropping SRR37536882.
  - `/home/vxk1/projects/Mscuiri_test_6942AMS/` — same 8 isolates as symlinks to `Mscuiri_test/download/`, pinned to 6942A_MS reference, step1+step2 done. (Note: step2 was run before the `4320772` alias-map fix; *should* still be correct because alias map used a different fallback path, but a re-run would confirm.)
  - `/home/vxk1/projects/Shivasharanappa_panel/` — 7 Indian bovine mastitis samples downsampled to ~200× target, pinned to 6942A_MS reference. **Awaiting first Setup+Run on step1.**
- **`/srv/kapurlab/audit/`**: T-07 provenance index (`runs.sqlite`) auto-populated from today's runs. env_snapshots dir continues to dedupe.
- **Cron**: `/etc/cron.d/vsnp_gui-provenance` unchanged (hourly gc, nightly crawl/export).

## What to do first when you pick up

1. Verify the state-checks above pass (~2 min).
2. **Pick up the M. sciuri analysis**:
   - The user's most likely next move is **Setup + Run step1 + step2 on `Shivasharanappa_panel`**. ~15 min wall time. Compare the tree to `Mscuiri_test_6942AMS`'s tree — see whether the Indian bovine isolates land inside the cattle clade (good — same lineage as the WI/CH/Belgium samples) or break out as their own sublineage (interesting — publishable observation about Indian cattle).
   - The *M. sciuri* `define_filter.xlsx` is still empty. Cascade tables won't have lineage-defining-SNP coloring until that's populated. Not a blocker but an obvious follow-up.
3. **Land the queued polish features** (see list below) in whatever order suits the next session's priority.

## Polish features queued (deferred from this session, in rough priority)

1. **OOD `restart_pun` reliability** — touching `~/ondemand/restart_pun` did not reliably trigger the dashboard PUN restart on config changes. Workaround was a direct kill of the Passenger RubyApp process for the dashboard. Worth: (a) investigating why the sentinel didn't fire on this OOD version, (b) documenting the kill recipe in `deploy/ood/README.md`, (c) adding a small admin script that does the kill cleanly.
2. **Auto-housekeeping for Reference downloads.** Backend should run a post-download step that parses the FASTA defline, derives a friendly `Genus_species_strain` directory name (with optional GUI override), runs `samtools faidx`, generates `.genome`, touches `best_reference.txt`, writes `citation.txt`, and renames the dir. We did this manually for both M. sciuri references today; the GUI's "Download New Reference" form should do it automatically. ~1 hr of work; design notes:
   - Defline parsing: `>ACC <Genus> <species> [strain <name>] [chromosome|complete|genome|plasmid…]`. Strip "strain" / "isolate" filler words. Sanitize strain name (keep alnum/_/-).
   - Frontend: optional "Reference name (auto if blank)" text input.
   - Backend: JobManager finalize_callback on the existing download job runs the housekeeping. Mirror the T-07 pattern.
3. **Downsampling at Step 1 Setup time.** Project-level config `step1_max_coverage` (default 0 = no downsample). Setup uses `seqkit stats` to estimate per-sample coverage as `total_bases × map_rate / ref_size`, then `seqkit sample -p <frac> -s <seed>` with the same seed for R1 and R2 to preserve pair sync. Original fastqs stay in `download/`; downsampled go into the sample dir, the existing symlink points at them. Add a "downsampled to Nx" indicator in the QC table. ~2 hr.
4. **Coverage breadth as a T-09 QC signal.** Currently T-09 verdicts depend on depth (`Average Depth`) + mapping rate (`100 − Unmapped Percent`) + contamination flag. Add `Genome with Coverage %` (column already in the *_stats.xlsx output) with a reasonable threshold (≥80% pass / ≥60% review / <60% fail). Would have flagged SRR21002857 (33%) / SRR37251701 (49%) / ERR3358320 (63%) cleanly — these were the cases mapping-rate alone missed. ~30 min.
5. **Sourmash species check at Setup or Step 1 time.** vsnp3 ships sourmash already, and we used it successfully today to confirm the Bangladesh sample wasn't *M. sciuri*. Could integrate as a Step 1 Setup-time check: sketch each sample's fastqs, compare containment against the chosen reference's sketch, flag samples with <70% containment as likely-wrong-species. Surfaces as a third badge state on the Step 1 sample row. ~1.5 hr (mostly the GUI plumbing).
6. **Better per-sample step1 progress UI.** The "Running…" disabled Run button is fine but tells you nothing about *which* sample is on what stage (BWA / fixmate / markdup / variant calling). Parsing `run_step1.log` for the standard vsnp3 phase markers and surfacing them in the sample table would be a nice touch. ~2-3 hr.

## Known minor follow-ups (deferred, not blocking)

- **vsnp3_download_GCA_fasta_get_metadata.py is broken** (missing `usda_file_setup` import). Standalone fix is a one-line shim; the bulk-fetch flow we discussed is unused for now since we use SRA reads directly.
- **NCBI_API_KEY env var** is not yet set on the backend. With it, eutils rate limit jumps from 3/sec to 10/sec. Free to register. Per the install plan we discussed: put key at `/etc/ood/secrets/ncbi_api_key` (mode 0640, group kapurlab-members) and patch `deploy/ood/template/before.sh` to export it. Today's two transient 502s could have been avoided.
- **define_filter.xlsx for M. sciuri** is empty. Cascade tables don't have lineage-defining SNP coloring. Real work — needs literature review or a defining-SNP discovery analysis on a representative isolate panel.
- **Bangladesh sample species ID** — confirmed it's not *M. sciuri* (47% containment) but didn't nail the exact sibling species. Could pull type-strain assemblies for *M. lentus*, *M. fleurettii*, *M. vitulinus*, *M. stepanovicii* and run a 5-way containment comparison. ~5 min if anyone needs it.
- **The Mscuiri_test_6942AMS step2 re-run.** Step1+step2 was run against the cattle reference earlier today, *before* the alias-map fix landed (`4320772`). The path it took to populate vcf_source/ worked via a different fallback (since the user had already manually re-set up the project after the alias issue surfaced). Worth a final Setup+Run on step2 to confirm clean state, but not strictly required — the tree we got was sensible.
- **Local seqkit on the Mac** — installed via `brew install seqkit` for the downsampling. Documented in this handoff; not a long-term ask of the user but worth noting if anyone else picks this up.

## Files of note (added or substantially modified today)

- `backend/app/sra.py` — rate-limited expander, SRAExpansionError, parallelized download script via xargs -P
- `backend/app/main.py` — _reference_alias_map alias fix, step1_run wait -n loop + duplicate-Run guard, step1_status exit_code sentinel, /download-file MIME table, /preview-xlsx endpoint
- `backend/app/xlsx_html.py` — openpyxl → HTML converter with formatting / CF rules / rotation / column widths
- `backend/app/provenance_writer.py` — _build_step1_inputs accepts SRA _1/_2 fastq naming
- `frontend/src/App.jsx` — paired-file grouping in download list, View buttons for xlsx/inline-renderable types, step1 Run button disabled-while-running with 5s polling, sraDownload + step1Run error surfacing
- `deploy/ood/clusters.d/wgs3.yml` — singularity_bindpath includes /run/systemd/resolve

## Operational recipes

### Re-run step2 after a tree-rooting / reference change

In the same project, switching reference is *not* clean (it'd mix old `alignment_<ref>/` dirs with new ones). Use a new project; symlink the existing `download/` fastqs to avoid re-transfer. Today's `Mscuiri_test_6942AMS` was created exactly this way — 9 symlinks back into `Mscuiri_test/download/`, project.json pinned to the new reference, Setup + Run.

### Force OOD dashboard PUN restart (when `restart_pun` sentinel doesn't fire)

```bash
ssh wgs3 'ps -fu vxk1 | grep "Passenger RubyApp.*dashboard" | grep -v grep | awk "{print \$2}" | xargs -r kill'
```

Next dashboard request spawns a fresh PUN that re-reads cluster.yml / dashboard.yml.

### Downsample fastqs to target coverage

```bash
seqkit sample -p <fraction> -s 42 <input.fastq.gz> -o <output.fastq.gz>
# Same -s for R1 and R2 to preserve pair sync.
# fraction = target_depth × ref_size / (map_rate × total_input_bases)
```

We did this locally on the Mac for the Shivasharanappa fastqs today. The 12 GB → 2.3 GB shrink.

### Sourmash species check (containment direction matters)

```bash
# Sketch the sample's R1+R2 merged
sourmash sketch dna -p k=31,scaled=1000 --merge <sample> R1.fastq.gz R2.fastq.gz -o sample.sig

# Sketch the reference
sourmash sketch dna -p k=31,scaled=1000 --name <ref> ref.fasta -o ref.sig

# CRITICAL: query the reference's k-mers IN the sample's k-mers (not the
# other way round — sample has way more error k-mers).
sourmash search --containment ref.sig sample.sig
# Expected: 75-100% for same species, 30-60% for sibling species, <10% for different genus.
```

That's the lot. Good luck.
