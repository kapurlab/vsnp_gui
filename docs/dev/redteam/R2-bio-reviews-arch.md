# R2 — Bioinformatics cross-examination of Architecture (R1)

---

### ARCH-AV1: `snp_analysis.py` is panel-shaped and cannot implement `AnalysisPrimitive`

- Verdict: **RATIFY** (with scope correction)
- Argument: The empirical claim holds. `snp_analysis.py:run()` (line 291) takes `(group_dir, group_name, out_dir, snp_dists_path, scope)` — there is no `sample` parameter anywhere in the file. Its unit of work is a multi-sequence FASTA (a concatenated panel produced by vSNP Step 2), not a per-sample BAM or FASTQ. `snp-dists` operates on that whole-panel FASTA and returns an N×N pairwise matrix. The R1 claim that this is panel-shaped is verified against the actual code. The domain argument R1 missed: this is not an edge case. In vSNP workflows, every population-level statistic — SNP density plots, KDE, closest-neighbor histograms, phylogenetic trees — is inherently cross-sample. `badge()` and `excel()` as per-sample methods have no coherent meaning for a pairwise distance matrix; the "sample" field would have to be the group name, which breaks badge aggregation in the GUI. R1's `GroupPrimitive` recommendation is sound. The scope correction: R1 frames this as a "blocker" implying the design is broken today. The design doc never explicitly claims `snp_analysis.py` implements `AnalysisPrimitive`; it lives in `backend/app/posthoc/` as a standalone module. The real gap is that the design does not acknowledge group-scoped primitives exist at all, leaving the next engineer to either force a fake-sample mapping or discover this independently.
- Evidence: `snp_analysis.py:291` (function signature), `snp_analysis.py:309` (`run_snp_dists` on whole-panel FASTA), `PIPELINES_PACKAGE.md §4` (`sample: str` on `PrimitiveResult`)

---

### ARCH-AV2: `run()` entangles execution with provenance write — retries silently corrupt the audit trail

- Verdict: **REBUT**
- Argument: The double-write concern is real but overstated for this lab's actual failure modes. In bioinformatics production, the dominant failure mode for a tool like AMRFinder is not a transient exception that triggers a retry loop — it is a missing binary, a corrupt FASTA, or an out-of-date DB, all of which are fatal and non-retriable. The R1 analysis assumes a retry framework exists or will be built; the design doc contains no retry layer. If `run_in_conda_env` raises before `self.result` is set, `write_provenance` is never reached (the call is after `self.result =`, line 381), so the "two provenance records" scenario requires the caller to explicitly catch and re-call `.run()` — an unusual pattern. The legitimate residue: `write_provenance` inside `.run()` does block dry-run and testing (you cannot call `.run()` in CI without side effects). That is a real friction, but it is a testing ergonomics problem, not an audit corruption hazard in practice.
- Evidence: `AMRFinder.run()` in `PIPELINES_PACKAGE.md §5` (lines 381-382, provenance called after result is fully set); R1-architecture.md §2 ("If `run_in_conda_env` raises, no provenance is written" — correct but the alternative scenario of two records requires an active retry wrapper that does not exist)

---

### ARCH-AV3: `applicable()` has filesystem I/O and its schema is undefined

- Verdict: **ESCALATE**
- Argument: R1 is correct on both sub-claims, but the domain consequence is worse than stated. `AMRFinder.applicable()` calls `Path(...).exists()` (design doc §5 line 336). In a vSNP workflow, whether an assembly exists is genuinely a runtime filesystem question — the design is correct that assembly presence cannot be inferred from `samples.json` alone, because assembly runs asynchronously across OOD cards and the FASTA may be written after `samples.json` was last read. However, the fix does not require lifting the `.exists()` call into `Project.sample_context()` — that just moves the I/O, it does not eliminate it. The real escalation: `SampleContext` being undefined means every primitive author independently decides what keys to probe. For tools like MLST and SeqSero2, `applicable()` needs to check organism against a scheme list (a static lookup, no I/O). For Kraken, there is no precondition and `applicable()` always returns True. For AMR, it needs assembly presence (I/O). For Sourmash, it needs fastqs (I/O). The heterogeneous I/O requirement means a single `SampleContext` struct cannot cleanly separate "pure applicability" from "filesystem state" — a `SampleContext` that is correct at construction time is stale 30 seconds later in a concurrent-card scenario. The right fix is two-tier: `applicable_static(organism, has_assembly_flag)` using a pre-resolved bool, plus `applicable_live(project, sample)` for anything requiring current filesystem state. R1 proposes one tier; the domain requires acknowledging both.
- Evidence: `PIPELINES_PACKAGE.md §4` (`applicable()` docstring), `§5` line 336 (`Path(...).exists()`), `§11 Q3` (schema deferred), `§3 Rule 2` (assembly written asynchronously)

---

### ARCH-AV4: `Project.ensure_assembly()` is a shared-mutable-state race condition

- Verdict: **UNRESOLVED**
- Argument (bio side supporting R1): The race is real in principle. SPAdes writes intermediate files to its output directory during assembly; reading a partially-assembled FASTA would produce a truncated sequence that passes FASTA parsing but fails downstream tools silently. AMRFinder on a truncated FASTA would return 0 genes — a false negative with no error signal. This is not a theoretical edge case: AMRFinderPlus takes 5 seconds per genome (confirmed in wgs3 smoke test), while SPAdes takes 15-45 minutes per genome. Any two cards that both need assembly and are launched within 45 minutes of each other hit this window.
- Argument (bio side against R1, from R1's own "where I'd be wrong"): The lab workflow is single-user, sequential-card. The vsnp_gui OOD session runs one job at a time per project (the JobManager in `backend/app/jobs.py` serializes runs). The kraken_gui is currently electron and not yet OOD-deployed. The concurrent-card scenario requires the kraken_gui OOD deployment (Step 6 of the migration path, listed as ~3 hr effort) to be complete AND both cards to be running simultaneously on the same sample. This is a future state, not today's deployment. A lockfile warning in the design doc is warranted; treating it as a current blocker is premature.
- Evidence: `PIPELINES_PACKAGE.md §3 Rule 2` (ensure_assembly, no locking mentioned), `§9 Step 6` (kraken OOD not yet deployed), wgs3 smoke test timing (5s AMRFinder, 15-45 min SPAdes), R1-architecture.md §4 "where I'd be wrong"

---

### ARCH-AV5: Relative-import debt from `backend/app/pipelines/` initial home

- Verdict: **REBUT**
- Argument: R1 is technically correct that relative imports require rewriting on extraction, but the magnitude is wrong for this codebase. At Step 1 (only AMRFinder exists), the total set of files using relative imports is: `amrfinder.py`, `common/contract.py`, `common/runners.py`, `common/provenance.py` — approximately 4 files. At Step 3 (sourmash added, extraction triggered), the rename is a one-line sed per file, not a multi-day refactor. The design doc recommends deferral because starting with a proper package requires PyPI infrastructure or an editable pip install that the lab does not yet have set up; with a single tool and no second consumer, this overhead is not justified. The domain argument R1 missed: bioinformatics codebases that start as "proper packages" on day one frequently accumulate packaging machinery (setup.py, pyproject.toml, version bumps, changelog) that adds friction without value until there is actually a second consumer importing the code. The real risk is not import-path debt — it is that the migration never happens because the extraction step gets skipped when Step 3 is "good enough." The design doc's own Step 3 trigger ("when primitive #2 follows the same contract") is a concrete, binary condition, not "someday."
- Evidence: `PIPELINES_PACKAGE.md §11 Q1`, `§9 Steps 1-3`, R1-architecture.md §5

---

### ARCH-AV6: `web()` and `badge()` stateful `assert` breaks load-from-cache use case

- Verdict: **RATIFY**
- Argument: This is a real design gap with direct operational consequence for the intended badge endpoint. The design doc §8 shows `GET /api/projects/{project}/samples/{sample}/badges` calling `load_completed_primitives(project, sample)` and then `.badge()`. In a production vSNP session, AMRFinder ran hours or days ago; no one should re-run it to render a badge. The `assert self.result` pattern forces one of two bad outcomes: re-run the tool (expensive, slow, potentially different result if DB updated) or implement a `PrimitiveResult` deserializer that reconstructs `self.result` from `samples.json`. The domain argument R1 missed: `findings` is already written to `samples.json` (§3 `record_finding`). The badge calculation is a pure function of `self.result.findings`. The fix is trivial — add a `@classmethod badge_from_findings(cls, findings: list[dict]) -> Badge` that contains the verdict logic, and make `badge()` call it. The instance method remains for pipeline use; the classmethod enables stateless rendering from cached data. This is standard practice in bioinformatics report generators where display and compute are separated.
- Evidence: `PIPELINES_PACKAGE.md §5` lines 386, 396 (`assert self.result`), `§3` (`record_finding` writes findings to samples.json), `§8` (badge endpoint design implies cached-result rendering)

---

## Net delta

- **Findings that survived (from bio perspective):**
  - AV1: `snp_analysis.py` is genuinely panel-shaped and does not fit `AnalysisPrimitive`; a `GroupPrimitive` tier is needed
  - AV3: `applicable()` filesystem I/O is real and the schema gap is load-bearing technical debt; escalated to a two-tier applicability model
  - AV6: Stateful `assert self.result` is a real barrier to badge-from-cache; fix is low-effort

- **Findings that failed (from bio perspective):**
  - AV2: Provenance double-write is a testing ergonomics issue, not an audit hazard; no retry framework exists or is planned
  - AV5: Relative-import debt is real but small and bounded; the deferral recommendation is correct given the single-consumer state

- **Escalations:**
  - AV3 → Two-tier applicability: `applicable_static` (organism/flag, pure) vs `applicable_live` (filesystem, async-safe); the design's single-method signature cannot cleanly serve both needs given the asynchronous assembly production model

- **Unresolved:**
  - AV4: `ensure_assembly()` race is real under concurrent OOD cards but the concurrent-card scenario is a future state; a lockfile is warranted in the design doc but treating it as a current blocker is premature

---

## Verdict on the "sample-shaped vs panel-shaped" lead claim

R1's lead claim — that `snp_analysis.py` falsifies the `AnalysisPrimitive` contract — is **empirically correct but architecturally over-scoped**. Reading `snp_analysis.py` directly confirms the function signature at line 291 takes `group_dir` and `group_name`, not a sample; the entire module operates on a concatenated multi-sample FASTA and produces a pairwise distance matrix, KDE plots, and cluster ordering — none of which has a per-sample decomposition. There is no seam where `sample: str` could be meaningfully populated. However, R1 frames this as a current design failure, when the design doc places `snp_analysis.py` in `backend/app/posthoc/` — outside the `pipelines/` hierarchy — and never claims it implements `AnalysisPrimitive`. The real gap is one of omission, not contradiction: the design's contract section (§4) is silent about the class of tools that are inherently cross-sample (SNP distance, krona aggregates, cross-sample AMR matrices), implying either that the authors did not consider them or that they intentionally excluded them without saying so. A `GroupPrimitive(samples: list[str])` contract with its own `web()` and `badge()` semantics (aggregate verdict over the panel, not a per-sample chip) is the correct resolution. The lead claim survives, but its severity is a design omission rather than a contract-breaking contradiction.
