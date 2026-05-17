# Red-team findings — PIPELINES_PACKAGE design review

**Process**: 3-round adversarial review, 8 R1 angles (architecture, ops, bioinformatics, failure-modes, UX, Lingling, Dev, Tod) + 6 R2 cross-examinations + 1 R3 synthesis. The synthesizer's authority is binning, not arbitration — UNRESOLVED findings preserve disagreement verbatim for human decision.

**R1 attack vector count**: 54 vectors across 8 files.
**UNRESOLVED**: 2
**CONFIRMED-BLOCKER / ESCALATED**: 12
**CONFIRMED-MAJOR**: 9
**NARROWED**: 5
**REFUTED**: 10
**TRADEOFF**: 3

---

## Decision-needed (UNRESOLVED) — read first

These are the findings where R2 reviewers genuinely disagreed. The synthesizer has not picked a side.

---

### UNRESOLVED-1: `ensure_assembly()` race — blocker now or future-state concern?

- R1 sources: ARCH-AV4 (blocker), OPS-AV2 (blocker), FM-AV2 (major), BIO-AV4 (major) — 4 angles, all convergent finding #2
- R1 severity as filed: blocker (ops, arch), major (failure-modes, bio)
- R2 verdicts:

  - **R2-ops-reviews-failure (FM-AV2)**: ESCALATE — "R1 calls this major; it is a blocker. The entire §3 shared-artifact design is predicated on `ensure_assembly()` being safe to call concurrently across cards. It is not. Two OOD cards (AMR + MLST) launched seconds apart on the same project both pass the `Path.exists()` check before either SPAdes creates the file. Two concurrent SPAdes invocations on a 47-sample Brucella project can saturate RAM and produce a last-writer-wins corrupted assembly."

  - **R2-bio-reviews-arch (ARCH-AV4)**: UNRESOLVED — "Argument (bio side supporting R1): The race is real in principle. SPAdes writes intermediate files to its output directory during assembly; reading a partially-assembled FASTA would produce a truncated sequence that passes FASTA parsing but fails downstream tools silently. AMRFinderPlus takes 5 seconds per genome while SPAdes takes 15-45 minutes per genome. Any two cards launched within 45 minutes of each other hit this window. // Argument (bio side against R1): The lab workflow is single-user, sequential-card. The vsnp_gui OOD session runs one job at a time per project (the JobManager serializes runs). The kraken_gui is currently electron and not yet OOD-deployed. The concurrent-card scenario requires the kraken_gui OOD deployment (Step 6, ~3 hr) to be complete AND both cards running simultaneously on the same sample. This is a future state, not today's deployment."

  - **R2-ux-reviews-ops+dev (OPS-AV2)**: REBUT — "Wgs3 is a single-lab installation where concurrent users opening the same sample simultaneously is not a realistic near-term scenario. The user-facing outcome (corrupt assembly) is severe if it fires, but the prior is low enough that this does not rise to user-facing P0. This deserves a code fix (fcntl.flock) but not a UX escalation. Rebut for UX priority; keep as backend P2."

  - **R2-steelman (convergent finding 2)**: PARTIAL-REBUT — "The actual OOD usage model on wgs3 today is one authenticated user (vxk1) running sequential pipeline sessions... The surviving valid kernel: `ensure_assembly()` as sketched has no locking stub, no sentinel file, no comment directing an implementer to add one. The fix is one `fcntl.flock` call and a per-sample lockfile. The design should either include it or explicitly annotate `ensure_assembly()` with a `# MUST HOLD <sample>.assemble.lock` comment."

- **Decision required**: Is `ensure_assembly()` locking a pre-T-27 blocker (fix the design now before any implementation begins) or a P2 backend hardening task (add the `fcntl.flock` annotation as a code comment and defer the implementation until Step 6 OOD concurrency materializes)?

---

### UNRESOLVED-2: Provenance regression vs. T-07 — schema gap or sketch shortcut?

- R1 sources: R1-user-tod TOD-PR1, also touched by R1-dev DEV-F6
- R1 severity as filed: major (Tod frames as handoff blocker for regulatory submissions)
- R2 verdicts:

  - **R2-ops-reviews-failure (TOD-PR1)**: RATIFY (empirically confirmed) — "T-07 captures: `conda_env_yaml_sha256`, content-addressed shared-store path, `pip_freeze_sha256`, per-binary version strings for samtools/bcftools/bwa/mafft/raxml/iqtree, `python_version`, `platform` (provenance_writer.py lines 476–487, confirmed by direct read). The §6 schema for new primitives captures only `\"env\": \"conda:amrfinder\"` — the name string. This is not a sketch approximation; it is the literal canonical schema in the doc. Any developer implementing new primitives from §6 alone ships a two-tier provenance system with no justification."

  - **R2-ux-reviews-ops+dev (DEV-F6)**: REBUT — "Dev's verdict is correct ('good enough for this lab's use case'). Not a UX issue. The missing `conda list --export` snapshot is invisible to the user at runtime and only matters for future reproducibility audits by external parties."

- **Decision required**: Should the §6 provenance schema be updated now to require `conda_env_snapshot` via `capture_env_snapshot()` from `provenance_writer.py` (porting an afternoon of work), or is the current env-name-only schema acceptable for the primitives being built in T-27, with T-07-quality provenance treated as a later hardening task?

---

## Confirmed blockers (CONFIRMED-BLOCKER + ESCALATED)

These must be addressed before T-27 (`pipelines/common/`) implementation begins.

---

### BLOCKER-1: OOD session timeout orphans SPAdes — partial FASTA silently produces confident wrong badge

- R1 sources: OPS-AV6 (major)
- R2 verdict: ESCALATE (R2-ux OPS-AV6)
- The bug: When an OOD session times out, Apptainer sends SIGKILL to the container process tree. SPAdes dies with no cleanup hook, leaving a partial FASTA in `assembly/<sample>/`. `ensure_assembly()` finds the directory, returns the partial path, AMRFinder runs to exit-code 0, and the badge renders a confident scientific verdict derived from a truncated sequence. The provenance `exit_code` sentinel is never written.
- Evidence: CLAUDE.md §5 (Apptainer `--pid` namespace, SIGKILL on timeout); PIPELINES_PACKAGE.md §3 rule 2; R2-ux OPS-AV6 escalation
- Fix scope: `ensure_assembly()` must validate FASTA completeness before returning the path. Incomplete assemblies must be deleted and re-run, not silently reused.

### BLOCKER-2: `applicable()` TOCTOU + undefined `PrimitiveError` — uncaught exceptions produce no badge

- R1 sources: FM-AV1 (blocker), ARCH-AV3 (major)
- R2 verdict: RATIFY (R2-ops FM-AV1), ESCALATE (R2-bio ARCH-AV3)
- The bug: `AMRFinder.applicable()` at §5 line 336 calls `Path(...).exists()`; if the file is deleted between check and `.run()`, an uncaught `FileNotFoundError` propagates to uvicorn as a 500. `PrimitiveError` is an open question per §11.6; the §8 call-site sketch has no `except` block.
- Evidence: amrfinder.py §5 line 336; PIPELINES_PACKAGE.md §11.6; §8 sketch (bare `primitive.run()`); R2-ops FM-AV1
- Fix scope: Define `PrimitiveError` and `PrimitiveSetupError` in `common/contract.py` before any primitive is implemented. Wrap every `.run()` call site.

### BLOCKER-3: `run_in_conda_env` activation undefined — silently fails at exit_code=127 inside Apptainer

- R1 sources: OPS-AV1 (blocker)
- R2 verdict: RATIFY (R2-ux OPS-AV1)
- The bug: Inside Apptainer, `$CONDA_PREFIX` may be unset and `conda` may not be on `$PATH`. The design claims `runners.py` is a "single-file change" for Apptainer migration but never shows the implementation. exit_code=127 renders silently as `verdict=fail` with no distinction from a scientific negative.
- Evidence: PIPELINES_PACKAGE.md §7; CLAUDE.md §5; R2-ux OPS-AV1
- Fix scope: Write and test `runners.py` inside the actual Apptainer container on wgs3 before T-27 begins. Do not accept the "single-file change" claim as a design given.

### BLOCKER-4: Kraken → Bracken → Krona partial failure permanently skips Bracken/Krona

- R1 sources: BIO-AV2 (major)
- R2 verdict: ESCALATE (R2-failure BIO-AV2)
- The bug: Kraken succeeds, writes top-species to `samples.json["kraken"]`. Bracken then fails. The next run finds `"kraken"` present in `samples.json` and skips Kraken — but Bracken has no output. System enters a permanent state where Kraken findings are committed but Bracken/Krona are absent, with no structured error surfaced.
- Evidence: PIPELINES_PACKAGE.md §3 (shared `kraken/<sample>/`, shared conda env); §3 rule 3 (samples.json accumulation); R2-failure BIO-AV2 escalation
- Fix scope: Model Kraken+Bracken+Krona as one `KrakenPipeline` primitive, or define an explicit inter-primitive dependency mechanism where Bracken's `applicable()` consults Bracken output existence on disk, not Kraken finding presence in `samples.json`.

### BLOCKER-5: TSV column rename → silent false-negative "AMR: none / pass" badge

- R1 sources: BIO-AV5 (major)
- R2 verdict: ESCALATE (R2-failure BIO-AV5)
- The bug: `amrfinder.py` run() accesses `row["Gene symbol"]`, `row["Class"]`, etc. by exact string key with no except block. A column rename causes `KeyError` → `findings=[]` → `tsv.exists()=True` → `exit_code=0` → `Badge(label="AMR: none", verdict="pass")`. A false-negative on a sample carrying `mecA1`.
- Evidence: amrfinder.py sketch run() findings parsing; badge() "if not f: return Badge(..., verdict='pass')"; PIPELINES_PACKAGE.md §4; R2-failure BIO-AV5 escalation
- Fix scope: Wrap TSV parsing in try/except raising `PrimitiveError` on missing columns. Validate expected headers before parsing. Also: store `self._cmd` and serialize it in `provenance()` to fix DEV-F2 simultaneously.

### BLOCKER-6: Stateful `assert self.result` prevents badge rendering from cached findings

- R1 sources: ARCH-AV6 (minor)
- R2 verdict: RATIFY+escalated (R2-bio ARCH-AV6)
- The bug: The §8 badge endpoint calls `load_completed_primitives()` then `.badge()`. A primitive that ran hours ago cannot render a badge without re-running the tool or an unspecified `PrimitiveResult` deserializer. Neither mechanism is specified.
- Evidence: PIPELINES_PACKAGE.md §5 lines 386, 396 (`assert self.result`); §3 (`record_finding` writes findings to samples.json); §8 badge endpoint; R2-bio ARCH-AV6
- Fix scope: Add `@classmethod badge_from_findings(cls, findings: list[dict]) -> Badge`. Badge endpoint uses this classmethod with findings from `samples.json`. No re-run required.

### BLOCKER-7: `web()` untyped dict — "no per-tool special-casing in the GUI" claim is false

- R1 sources: UX-AV4 (major)
- R2 verdict: RATIFY (R2-arch UX-AV4), surviving kernel confirmed (R2-steelman finding 5)
- The bug: The design claims "no per-tool special-casing in the GUI" (§8) while `.web()` returns `dict[str, Any]`. AMR returns gene tables with class colors; Kraken would return taxonomy trees. These require different React components regardless of the Python contract.
- Evidence: PIPELINES_PACKAGE.md §4 `.web()` return type; §8 "no per-tool special-casing"; amrfinder.py lines 384–393
- Fix scope: Add a required `kind` field to `.web()` output and enforce it in `common/contract.py`. Define one React component per `kind`. Fix must be in the contract before any `.web()` implementation is written.

### BLOCKER-8: `samples.json` metadata/findings coupling — dual-role anti-pattern with no invalidation sweep

- R1 sources: UX-AV5 (major)
- R2 verdict: ESCALATE (R2-arch UX-AV5)
- The bug: `samples.json` serves two incompatible roles: user-authoritative metadata (`organism`, `host`, `isolation_source`) and tool-authoritative computed findings (`step1`, `kraken`, `amr`). When a user corrects `organism`, all previously computed `applicable()` results and findings become stale with no invalidation sweep. No schema version counter exists.
- Evidence: PIPELINES_PACKAGE.md §3 `samples.json` example; §11 (metadata-findings coupling not listed as open question); R2-arch UX-AV5
- Fix scope: Either (a) split into `metadata.json` and `findings.json`, or (b) add a `metadata_version` counter on the metadata block that each primitive records at run time for staleness detection.

### BLOCKER-9: No bulk run action — per-sample trigger model does not scale to panel workflows

- R1 sources: R1-user-lingling LL-F3
- R2 verdict: ESCALATE (R2-arch LL-F3)
- The bug: The per-sample URL scheme implies one subprocess per trigger. For a 16-sample panel there is no described bulk run path. A bulk run requires a new batch API endpoint and `JobManager` generalization to arbitrary primitives — neither is described.
- Evidence: PIPELINES_PACKAGE.md §3 rule 5; CLAUDE.md `backend/app/jobs.py`; R2-arch LL-F3
- Fix scope: Define `POST /api/projects/{project}/run-all?primitive=amr` before Step 2 ships. Specify how `JobManager` tracks multi-sample primitive runs.

### BLOCKER-10: `posthoc/snp_analysis.py` writes non-T-07 provenance — format drift exists on day one

- R1 sources: R1-user-dev DEV-F8
- R2 verdict: RATIFY (R2-ux DEV-F8)
- The bug: `snp_analysis.py` is in production use and writes `stats.json` in its own schema, not the T-07 `record.json` schema the design mandates. The design's stated goal (eliminate format drift) has already failed for the one substantial analysis module in the repo.
- Evidence: `posthoc/snp_analysis.py` `write_stats()`; PIPELINES_PACKAGE.md §6; R2-ux DEV-F8
- Fix scope: Before any new primitive is implemented, migrate `snp_analysis.py`'s `write_stats()` to use `common/provenance.py`, or explicitly mark it as legacy-excluded with a FIXME.

### BLOCKER-11: `snp_analysis.py` NaN-fill emits `status: ok` on corrupted distance matrix

- R1 sources: FM-AV5 (major)
- R2 verdict: RATIFY (R2-ops FM-AV5) — confirmed production code
- The bug: `sanitize_matrix()` lines 181–186 fills NaN cells with 0, records `nan_filled: N`, leaves `status="ok"`. KDP and closest-neighbor plots render unconditionally on corrupted distances. A corrupted grouping renders as confidently as a correct one.
- Evidence: `snp_analysis.py` lines 181–186, 340–341; R2-ops FM-AV5
- Fix scope: Add a `degraded` verdict to `Badge.verdict`. When `nan_filled > 0`, set `status = "degraded"`, suppress plots, render a yellow badge with NaN count in detail.

### BLOCKER-12: Cross-card navigation is architecturally non-bidirectional

- R1 sources: UX-AV1 (blocker)
- R2 verdict: ESCALATE (R2-arch UX-AV1)
- The bug: OOD sessions are ephemeral port-bound objects with no stable URL identity. The `?project=X&sample=Y` URL has no `source_card` or `source_session_id`. A breadcrumb "arrived from vSNP" cannot link back to the correct session. The problem is a URL scheme gap, not a missing UI component.
- Evidence: PIPELINES_PACKAGE.md §3 rule 5; CLAUDE.md "OOD session execution model"; R2-arch UX-AV1
- Fix scope: Add `source_card` and `source_session_id` parameters to the cross-card URL scheme. Originating card embeds its session ID (from OOD's `$SESSION_ID`). Receiving card header renders "Back to [source_card] session [id]."

---

## Confirmed majors (CONFIRMED-MAJOR)

Address during T-27, document if deferred.

### MAJOR-1: `provenance()["command"]` is a hardcoded stub — recorded command diverges from actual argv
- R1 sources: R1-user-dev DEV-F2; R2 verdict: ESCALATE (R2-ux DEV-F2)
- Bug: `provenance()` returns `"command": "amrfinder -n ... --plus"` — a literal stub with `...`, not the actual `cmd` list.
- Fix: Store `self._cmd` after building it in `.run()`; serialize as `" ".join(self._cmd)` in `provenance()`. Two-line fix.

### MAJOR-2: Three badge-absence states (n/a / not-run / failed) are visually indistinguishable
- R1 sources: UX-AV3 (major); R2 verdict: RATIFY (R2-arch UX-AV3)
- Bug: `applicable() == False`, "not yet run," and `PrimitiveError` all produce the same badge-column absence.
- Fix: Add `n/a`, `pending`, `fail` as explicit `Badge.verdict` values with distinct UI states.

### MAJOR-3: No cross-sample matrix view — panel-level question cannot be answered from badge layer
- R1 sources: R1-user-lingling LL-F5; R2 verdict: ESCALATE (R2-arch LL-F5)
- Bug: `samples.json` enables a trivial server-side matrix query but no endpoint or UI exposes it.
- Fix: Add `GET /api/projects/{project}/matrix?primitive=amr&field=class` returning a per-sample × per-value grid.

### MAJOR-4: `ensure_assembly()` cache key is path existence, not assembler identity — silent wrong-answer propagation
- R1 sources: BIO-AV3 (major); R2 verdict: ESCALATE (R2-failure BIO-AV3)
- Bug: All assembly-dependent primitives complete with exit-code 0 and valid provenance SHA256s even when the cached FASTA was produced by the wrong assembler. Audit trail is complete but undetectable as wrong.
- Fix: `ensure_assembly()` reads `assembly/<sample>/.provenance/record.json` and raises `AssemblyMismatchError` if assembler or params differ.

### MAJOR-5: AMRFinder `-O` organism flag has no defined resolution path — every caller reimplements independently
- R1 sources: BIO-AV4 (major); R2 verdict: RATIFY (R2-failure BIO-AV4)
- Bug: `-O` resolution deferred to `build_kwargs(ctx)` — a method the contract does not define. Every front-end independently resolves organism-to-`-O` and implementations will drift.
- Fix: Define `build_kwargs(sample_context)` as an abstract classmethod in `AnalysisPrimitive`.

### MAJOR-6: Assembly provenance schema unspecified — SPAdes parameters not captured
- R1 sources: R1-user-tod TOD-PG3; R2 verdict: RATIFY (R2-ops TOD-PG3)
- Bug: `assembly/<sample>/.provenance/record.json` schema is never defined. SPAdes version, k-mer list, randomization seed not guaranteed to be recorded.
- Fix: Define the assembly primitive provenance schema in §6 alongside AMRFinder's.

### MAJOR-7: `samples.json` findings are last-write-wins — collaborator re-run destroys original findings
- R1 sources: R1-user-tod TOD-PG2; R2 verdict: RATIFY (R2-ops TOD-PG2)
- Bug: `record_finding()` unconditionally overwrites `samples.json[sample][primitive]`. A collaborator re-run destroys original findings.
- Fix: Index findings by `run_id` within each primitive key. Latest run is default view; history preserved.

### MAJOR-8: No export workflow for handoff — absolute paths in provenance break on import
- R1 sources: R1-user-tod TOD-HG1; R2 verdict: RATIFY (R2-ops TOD-HG1)
- Bug: Every `record.json` embeds absolute paths (`str(self.FASTA)` at amrfinder.py line 434). `verify_provenance.py` SHA256-resolves check fails immediately on import.
- Fix: Add `Project.export_package(dest_dir, include_fastqs=False)` that rewrites absolute paths to project-relative and generates a `MANIFEST.json`.

### MAJOR-9: AMR badge detail lists gene names, not drug classes — advisor question unanswerable
- R1 sources: R1-user-lingling LL-F4; R2 verdict: RATIFY (R2-arch LL-F4)
- Bug: `Badge.detail` for AMR lists gene names only. A user who doesn't know that `blaTEM` is a beta-lactamase cannot answer their advisor's question from the badge.
- Fix: Change `badge()` detail to `"2 BETA-LACTAM genes: blaTEM-1, blaOXA-48"`. One-line change.

---

## Narrowed (R1 right, R1's prescribed fix too heavyweight)

### NARROWED-1: Sample-shaped vs panel-shaped contract
- R1 recommended fix: Introduce `GroupPrimitive(samples: list[str])` from day one
- R2 narrower fix (steelman): Add a documentation note in §4 that group-scoped tools require a separate subcontract; defer `GroupPrimitive` until the first group-scoped tool is actually being migrated
- Surviving kernel: A developer implementing SNP distances today has no guidance and will reach for the wrong base class. The documentation gap is real and must be closed in §4 before T-27.

### NARROWED-2: `applicable()` expressiveness
- R1 recommended fix: Eliminate all filesystem I/O from `applicable()` via a typed `SampleContext` dataclass
- R2 narrower fix (bio + steelman): Add a named `pre_run_check(project, sample) -> None` hook for environment validation (raises `PrimitiveSetupError`). `applicable()` retains its triage role. `SampleContext` still needs to be defined (§11 Q3 is load-bearing), but need not pre-resolve all filesystem state.
- Surviving kernel: The binary `bool` return of `applicable()` cannot express "will run but with organism-validity caveats." The contract needs either a third return value or `pre_run_check()` to surface warnings.

### NARROWED-3: `samples.json` write atomicity
- R1 recommended fix: Redesign `record_finding()` atomicity from scratch
- R2 narrower fix (steelman): Port `_atomic_json_write()` from `provenance_writer.py` line 120 into `common/provenance.py`. Fix `snp_analysis.py`'s bare `path.write_text()` first — that is the real existing non-atomic path.
- Surviving kernel: No `JSONDecodeError` recovery specified. Lock mechanism must be `fcntl.flock`, not PID-file advisory lock.

### NARROWED-4: `run()` entangles execution with provenance write
- R1 recommended fix: Separate `run()` from `write_provenance()` with explicit post-run hook and transaction boundary
- R2 narrower fix (bio): No retry framework exists or is planned. Make `write_provenance()` conditional on a `record_provenance=True` parameter so tests can pass `False`.
- Surviving kernel: Testing ergonomics problem, not an audit corruption hazard in the lab's actual failure modes.

### NARROWED-5: Relative-import debt from `backend/app/pipelines/` initial home
- R1 recommended fix: Start as a proper package from day one
- R2 narrower fix (bio): At Step 1, only ~4 files use relative imports. Step 3 extraction is a one-sed-per-file rename. The deferral is correct given single-consumer state.
- Surviving kernel: The design doc needs an explicit commitment that Step 3 extraction is mandatory, not optional, or the import debt compounds indefinitely.

---

## Refuted (R2 successfully rebutted R1)

**REFUTED-1: `run()` + `write_provenance()` double-write on retry** — R1 claim: retry produces two provenance records. R2 rebuttal (bio): no retry framework exists; write_provenance only reachable after self.result is fully set; clean failure if run_in_conda_env raises before that. (R2-bio ARCH-AV2)

**REFUTED-2: Exit-code dual-representation (JSON + sentinel file)** — R1 claim: two writes create two truth sources. R2 rebuttal (ops): provenance_writer.py lines 120–131 implement a single atomic JSON write; no sentinel file in T-07 implementation; §6's "exit_code text file" is design-doc shorthand, not reality. (R2-ops FM-AV7)

**REFUTED-3: `record_finding()` + `write_provenance()` leave findings and provenance out of sync** — R1 claim: if `write_provenance()` fails after `record_finding()` succeeds, they diverge. R2 rebuttal (ops): `record_finding()` is caller responsibility per §8, not in `.run()`; `write_provenance()` is last call in `.run()`; ValueError from TSV fires before write_provenance — clean failure. (R2-ops FM-AV3)

**REFUTED-4: Badge density crosses meaningful threshold at 16 samples × 5 primitives** — R1 claim: 80 badge chips at realistic panel size. R2 rebuttal (arch): `applicable()` gates badge rendering; for MTBC panel, realistically 2–3 badge columns render, not 5. (R2-arch UX-AV2)

**REFUTED-5: First-time user mental model destroyed, not migrated** — R1 claim: card proliferation requires relearning. R2 rebuttal (arch): Step 2 delivers in the existing vSNP step1 page; dashboard proliferation only at Step 6 (later); treating Step 6 UX as launch blocker misprioritizes. (R2-arch UX-AV7)

**REFUTED-6: `pip install -e` breaks under concurrent OOD sessions** — R1 claim: two sessions + git pull = incompatible versions. R2 rebuttal (ux): requires concurrent sessions + live deploy — not normal single-lab workflow. (R2-ux OPS-AV5)

**REFUTED-7: CLI `__main__` block not shown; provenance may not fire on CLI runs** — R1 claim: CLI may be provenance-dark. R2 rebuttal (ux): GUI path calls `.run()` explicitly; CLI provenance is a dev-experience concern, not a user-facing failure. (R2-ux DEV-F1)

**REFUTED-8: Re-run overwrites successful outputs** — R1 claim: no `ensure_amr()` equivalent. R2 rebuttal (ux): for AMRFinder (5s/genome), harmless; expensive case handled by `ensure_assembly()`. (R2-ux DEV-F5)

**REFUTED-9: Sourmash sketch/gather split into different reuse profiles** — R1 claim: one primitive forces re-sketching on every DB update. R2 rebuttal (failure): no wrong answer, no corrupt state; theoretical overhead until lab actually changes DB sources. (R2-failure BIO-AV6)

**REFUTED-10: Provenance schema inadequate for publication-grade reproducibility** — R1 claim (Dev): missing `conda list --export` snapshot. R2 rebuttal (ux): Dev's own verdict — "good enough for this lab's use case." Invisible at runtime. (R2-ux DEV-F6)

---

## Tradeoffs (decision but not a bug)

**TRADEOFF-1: AMRFinder DB version not pinned; monthly updates silently change gene calls** — Provenance records `db_version` but GUI never alerts user to a version change between runs. Decision: either pin DB by path hash in `config.json` and refuse to run on mismatch, or document that re-running after `amrfinder --update` is a known workflow that produces a new provenance record with a different `db_version`. (OPS-AV7, RATIFY R2-ux)

**TRADEOFF-2: Conda-env wrappers replace NAHLN_AMR Apptainer containers** — For MLST, SeqSero2, ABRicate in regulatory submissions, a conda env snapshot is documentation; an Apptainer image is a reproducibility guarantee. Decision: evaluate whether Apptainer images should be maintained alongside conda envs for tools appearing in regulatory submissions. The `"container": null` field in §6 accommodates this. (TOD-RG1, RATIFY R2-ops)

**TRADEOFF-3: `verify_provenance.py` validates data integrity, not environment integrity** — No automated flag when a re-running collaborator's tool version or DB version diverges. Decision: add a warning-only function that checks installed vs. recorded tool version, or accept that version comparison is a manual step. Pairs with TRADEOFF-1. (TOD-PG1, RATIFY R2-ops)

---

## Net delta vs original design doc

- Convergent findings (3+ R1 angles): 5 (ensure_assembly race; sample vs panel contract; samples.json corruption; applicable() expressiveness; schemaless findings/web())
- Findings that survived R2: 34 of 54
- Findings R2 refuted: 10
- New escalations found in R2: 10
- Unresolved disagreements: 2

---

## Recommended scope-freeze for T-27

Based on CONFIRMED-BLOCKER + UNRESOLVED requiring resolution, T-27 (`pipelines/common/`) should not begin implementation until the following are decided/fixed:

1. **[UNRESOLVED-1]** Decide `ensure_assembly()` locking posture: pre-T-27 blocker or P2 annotation. If blocker: the `fcntl.flock` call and sentinel-file pattern must be in the `ensure_assembly()` sketch. If P2: add mandatory `# MUST HOLD <sample>.assemble.lock` annotation.

2. **[UNRESOLVED-2]** Decide provenance schema scope: port `capture_env_snapshot()` from `provenance_writer.py` into §6 now, or document env-name-only as a known reduction from T-07 with a forward reference.

3. **[BLOCKER-2]** Define `PrimitiveError` and `PrimitiveSetupError` in `common/contract.py` before any `.run()` call site is written.

4. **[BLOCKER-5 + BLOCKER-6]** Add `badge_from_findings()` classmethod and wrap TSV parsing in `PrimitiveError` — minimum viable badge layer that does not produce false-negative pass badges.

5. **[BLOCKER-3]** Write and test `runners.py` inside the actual Apptainer container on wgs3. Do not accept "single-file change" as a design given.

6. **[BLOCKER-7]** Add `kind` discriminator to `.web()` contract and enforce it in `common/contract.py` before any `.web()` implementation is written.

7. **[BLOCKER-8]** Decide `samples.json` metadata/findings architecture (split files or version counter) before `Project.record_finding()` is implemented.

8. **[BLOCKER-10]** Migrate `posthoc/snp_analysis.py`'s `write_stats()` to `common/provenance.py`, or mark it explicitly as legacy-excluded with a FIXME.

9. **[BLOCKER-11]** Fix `snp_analysis.py` NaN-fill `status: ok` bug — confirmed production code, silent wrong answer.

10. **[NARROWED-2]** Define `SampleContext` in `common/contract.py` (§11 Q3 is load-bearing; every `applicable()` implementation will invent its own keys until resolved).
