# Session handover — May 16 2026

Continuation notes for the next Claude session. Read this first, then
[`docs/dev/PIPELINES_PACKAGE.md`](PIPELINES_PACKAGE.md) for the design
direction and [`docs/dev/TICKETS.md`](TICKETS.md) for the broader plan.

## TL;DR

The day was about three things, in order:

1. **Diagnosing the Shivasharanappa panel** — kraken2 + bracken showed
   all 7 samples are heavily polymicrobial (4–38% *M. sciuri*, dominant
   *Proteus mirabilis* / *Enterococcus* / *Paraclostridium*). Source is
   almost certainly direct-from-clinical-specimen (mastitis milk), not
   colony picks — even though the collaborator's published methods
   describe a single-isolate workflow.

2. **Reconstructing the panel from the collaborator's NCBI submissions** —
   pulled the 8 *M. sciuri* WGS assemblies they uploaded to BioProject
   PRJNA1358531 (one strain not in our fastq set: HW110, a water isolate),
   synthesized 50× paired Illumina reads from each, and re-ran step1
   alongside the raw fastqs. Combined tree confirms their *M. sciuri*
   contig bins faithfully match what BWA recovers from the raw
   polymicrobial fastqs — every NivediXXX synthetic clusters tightly with
   its real-fastq sibling.

3. **Installing AMRFinderPlus on wgs3 and running it on the 8 NivediXXX
   assemblies** — established the canonical fixture for the future shared
   pipelines package. Found *mecA1* and *sal(A)* universal; arsB / arsC /
   cadD scattered; nothing else of clinical concern.

The architectural payoff: a 632-line design doc
**[`PIPELINES_PACKAGE.md`](PIPELINES_PACKAGE.md)** committed to `web` as
`51ae6df`. Specifies the `AnalysisPrimitive` contract and a shared
**project filesystem workspace** that every future OOD card (vSNP /
Kraken / AMR / Sourmash / MLST) reads-and-writes against. Today's AMR
run becomes the regression fixture for the first concrete primitive
(`pipelines/amrfinder.py`).

## Commits landed today

**On `web`** (1 unpushed commit at HEAD):
- `51ae6df` docs: PIPELINES_PACKAGE design — shared analysis primitives
  + project workspace

**On `codex/snp-analysis`** (1 unpushed commit at HEAD):
- `b6c474d` step1 concurrency hardening + openpyxl dep
  (committed the 3 uncommitted edits that had been sitting in the main
  worktree: `_wrapper_process_alive()` pgrep fallback in `main.py`,
  step1 "already running" guard in `App.jsx`, `openpyxl==3.1.5` for
  posthoc `_stats.xlsx` reading)

Both branches are local-only. **Nothing was pushed today.** Push when
the next session has reviewed the handoff and decided how to proceed.

## State on wgs3

**`/home/vxk1/projects/Shivasharanappa_panel/`** is the working dataset:

```
download/
  IN{47,50,107,108,109,185,240}_R{1,2}.fastq.gz          ← real polymicrobial fastqs, downsampled to 200× target
  NivediHW110_R{1,2}.fastq.gz, NivediIN{47,...}_R{1,2}.fastq.gz ← synthetic 50× from NCBI assemblies
step1/<sample>/                                          ← already populated for the 7 real samples
step2/                                                   ← tree from the combined run (15 samples)
synthetic_from_assembly/
  fasta/{HW110,IN47,...,IN240}.fasta                     ← 8 NCBI-deposited M. sciuri assemblies
  fastq/                                                 ← wgsim-simulated paired reads (50× depth, 251bp, seed 42)
kraken/                                                  ← kraken2 + bracken + krona + per-sample bracken pies + panel_kraken_summary.xlsx
amrfinder/                                               ← 8 .amr.tsv files — THE FIXTURE for pipelines/amrfinder.py regression test
```

**Conda envs on wgs3** (under `~/miniforge3/envs/`):
- `vsnp3` — pre-existing, vsnp3 + samtools/bcftools/wgsim
- `kraken_report` — kraken2 2.17.1, bracken 3.1, krona, krakentools, matplotlib, pandas, openpyxl
- `amrfinder` — ncbi-amrfinderplus 4.2.7 with DB 2026-03-24.1 at
  `~/miniforge3/envs/amrfinder/share/amrfinderplus/data/2026-03-24.1/`

**Shared refs**:
- Kraken Standard-8: `/srv/kapurlab/refs/kraken/standard-8_20250402/`

**Local pulled artifacts** (on the Mac):
- `~/Downloads/Shivasharanappa_kraken_reports/` — krona HTMLs, bracken pies, xlsx, `Nivedi_panel_metadata.csv`
- `~/Downloads/Shivasharanappa_kraken_reports/Nivedi_panel_metadata.csv` — BioSample-derived host / source / village / date / collector
- `~/Downloads/Shivasharanappa_panel_kraken.zip` — email-ready bundle for the collaborator
- `~/Downloads/Shivasharanappa_amrfinder/{*.amr.tsv,amr_matrix.csv}`
- `~/Downloads/Shivasharanappa_assemblies/*.fasta` — 8 NivediXXX FASTAs

## Open items for the next session

### Priority 1 — codex/snp-analysis → web merge (deferred today)

Attempted in this session, **aborted** because the conflicts span months
of independent evolution in shared code regions that include T-07
provenance plumbing. Don't auto-resolve.

`codex/snp-analysis` has 5 unmerged commits (4 from March/April + 1
today: `b6c474d`). The branch adds a complete `backend/app/posthoc/`
subsystem that **`web` is already calling into** (web's `main.py` /
`App.jsx` / `styles.css` reference posthoc routes and state, but the
implementation directory doesn't exist on web). The merge fills in the
missing subsystem.

**Conflict regions in `main.py`** (5+):

| Region | Web (HEAD) has | codex has | Resolution hint |
|---|---|---|---|
| imports | `SRAExpansionError` | `posthoc_*` registry imports | take both |
| helper block at L61 | `_project_roots`, `_project_dir_for`, `_resolve_qc_thresholds`, `_annotate_qc_rows`, `_path_under_any_project_root` | `_wrapper_process_alive`, `_script_bin_dir`, `_tool_bin_dir` | take both — orthogonal |
| `wrap_cmd` at L231 | PYTHONWARNINGS prefix + vsnp3 PATH | tool_bin + script_bin PATH builder | merge: PYTHONWARNINGS prefix + codex's PATH builder logic |
| step2 dispatch at L1714 | full T-07 `provenance_writer.dispatch_step2` + `Step2DispatchBlocked` + finalize callback | `shlex.quote` on paths | keep web's T-07 dispatch wholesale; apply codex's shlex.quote to the cmd construction |
| step2 outputs at L2561 | labeled-tre suppression logic | (different change) | inspect — likely keep web |

**Conflict in `requirements.txt`**: codex adds aiofiles, numpy, pandas,
matplotlib, scipy, openpyxl. Take all (they're needed by the posthoc
SNP analysis).

**Conflict in `App.jsx`**: UI overlap in the posthoc tab/state region.
Web has more recent UI state (posthocFolders, posthocRows, posthocFilteredRows
memo, fetch calls to /api/posthoc/{step1/scan,step1/resolve_samples,open}).
Codex's frontend additions are an earlier generation of that UI plus a
step1JobStatus guard. Take web's UI but graft codex's step1JobStatus
guard into `step1Run()`.

**Web has its own implementations of `/api/posthoc/step1/scan` and
`/api/posthoc/step1/resolve_samples` at L1930/1995** — codex has them at
L1478/1540. These may have diverged. Read both before deciding which to
keep — web's are likely newer (post-T-07), but verify they're not
regressing capability codex had.

**`/api/posthoc/tools` and `/api/projects/{p}/posthoc/run`** exist only on
codex side — take wholesale.

**`/api/posthoc/open`** exists only on web side — keep.

Recommend a **focused merge session**: open both branches' main.py
side-by-side, resolve each hunk with intent visible, run the test suite
(`backend/app/test_*.py`), smoke-test the posthoc dropdown end-to-end.
~30–60 min of careful work.

### Priority 2 — GitHub-side branch rename (user does this in browser)

After the codex/snp-analysis merge lands cleanly, promote `web` → `main`:

```
1. Push web to origin (the 51ae6df doc commit + any merge commits)
2. GitHub UI → repo Settings → Branches:
   a. Rename `main` → `main-electron-archive`
   b. Rename `web` → `main`
   c. Change default branch to `main` (the new one)
3. Update any branch protections / required checks pointed at "main"
4. Locally: git fetch + reconcile
   git branch -d main             # delete stale local main
   git branch -m web main          # rename local web
   git branch --set-upstream-to=origin/main main
```

The Mac copy of the repo is a viewer (do NOT run a local uvicorn there
— it shadows the OOD backend in the browser, see `API_BASE` note in
`TICKETS.md`).

### Priority 3 — Stale branch cleanup (after rename)

**Deleted today**: `codex/collab-latest` (local) — confirmed 0 unique
commits vs web, 74 behind.

**Still to clean up**:
- `claude/charming-sanderson-f6840e` (local + remote) — 0 unique commits
  vs web after today's commit landed there
- `claude/happy-haslett-9b4f56` (local + remote) — 0 unique commits vs
  web after `PIPELINES_PACKAGE.md` was copied to charming-sanderson
- `codex/collab-review` — check if unique commits vs web; likely stale
- `codex/browser-folder-picker` — check
- `baseline-test` — old experimental branch; user should decide
- Worktrees: `git worktree remove .claude/worktrees/{charming-sanderson-f6840e,happy-haslett-9b4f56}`
  AFTER confirming nothing important left in them. happy-haslett's only
  notable content was `docs/dev/PIPELINES_PACKAGE.md`, which is now
  committed on web.

Keep: `t03-spike` (A/B record), `feature/posthoc-step1` /
`feature/vcf-edit` (live feature branches), `codex/snp-analysis`
(active work).

### Priority 4 — Red team the PIPELINES_PACKAGE design

Before implementing Phase 1 (`pipelines/amrfinder.py`), spawn parallel
sub-agents to red-team the design from four angles: software
architecture, ops/deployment, bioinformatics workflow, code-review
hygiene. The PIPELINES_PACKAGE doc is at a natural point where outside
perspectives would catch problems before they get baked in.

Real-human review (Lingling, Tod, Dev, Dee) should also happen for UX
and bioinformatics domain expertise.

### Priority 5 — Tickets to add (Milestone C, post-rename)

The PIPELINES_PACKAGE.md design implies these tickets — add to
`docs/dev/TICKETS.md` Milestone C:

| Ticket | What |
|---|---|
| **T-15+T-08** (existing — promote) | Multi-app deployment template is now load-bearing for pipelines work |
| **T-27** | Implement `pipelines/common/` (`AnalysisPrimitive` contract + `Project` workspace helper + provenance/badge/runner shims). Lives initially inside `vsnp_gui/backend/app/pipelines/`. |
| **T-28** | Implement `pipelines/amrfinder.py` against the contract; regression-test against today's 8 NivediXXX amr_matrix.csv |
| **T-29** | Wire AMR into vsnp_gui: post-step1 hook, badge, "Run AMR" button. First end-to-end card-to-card consumption. |
| **T-30** | Re-deploy kraken_gui as an OOD batch_connect app on wgs3; drop electron build; import `pipelines/kraken.py` from the shared package. |
| **T-31** | Standalone AMR OOD card (sister to vSNP / Kraken). Driven by the same shared module. |
| **T-32** | Sourmash card + `pipelines/sourmash.py` |
| **T-33** | NAHLN_AMR vendored reference: port MLST / Abricate / SeqSero2 wrappers as additional primitives |
| **T-34** | Cross-card navigation protocol (`?project=X&sample=Y` on every card) |
| **T-35** | `samples.json` schema + concurrent-write strategy (atomic tempfile+rename + per-project lockfile) |

## Other observations worth noting (not blocking)

- **Collaborator data caveat**: their methods section claims FastQC →
  fastp → Trimmomatic → Shovill v1.0.9 → CheckM → FastANI → Prokka →
  AMRFinder pipeline. GenBank assembly metadata says SPAdes v4.2.0
  (Shovill v1.0.9 can't wrap SPAdes 4.x — methods are wrong on at least
  that point). No SRA was deposited. They likely binned *M. sciuri*
  contigs from polymicrobial SPAdes output and submitted only those.
  Legitimate workflow but undisclosed in their methods.

- **Collaborator email open**: I helped draft a request for their
  ConFindr / CheckM TSVs to settle whether the read sets they analysed
  are the read sets they sent us. They mentioned using "Kraken,
  Confinder, CheckM" — output never shared. Worth following up.

- **9th BioSample in the project (Nivedi_IN208)** registered at
  SAMN53117686 but with `/strain="Not applicable"` and no WGS assembly
  submitted — likely failed their QC and was dropped. He didn't send
  you that one.

- **OOD `step1_max_parallel` is 8** — on a 128-core / 503 GB box. Was
  the bottleneck today when running 15 samples. Bumping to 16 in the
  user config (or adding it to the GUI's Settings panel) is a 1-line
  change.

- **NAHLN_AMR upstream is gold** — `bin/latex_reporter.py` (58 KB) is a
  direct cousin of the kapurlab fork's `Latex_Report` class.
  `bracken_pie.py` is identical lineage. When porting tools into the
  pipelines package, NAHLN_AMR's existing wrappers cut the work
  substantially.

## Verify when picking up

1. `git log --oneline -5` on `web` — last commit should be
   `51ae6df docs: PIPELINES_PACKAGE design …`
2. `git log --oneline -5` on `codex/snp-analysis` — last commit should
   be `b6c474d step1 concurrency hardening + openpyxl dep`
3. `git status` on both worktrees — should be clean (no uncommitted)
4. Neither branch is pushed yet — `git push` decisions belong to the
   next session
5. `ssh wgs3 'ls /home/vxk1/projects/Shivasharanappa_panel/amrfinder/'` —
   should show 8 `.amr.tsv` files
6. `ssh wgs3 'mamba run -n amrfinder amrfinder --version'` — should
   report 4.2.7
