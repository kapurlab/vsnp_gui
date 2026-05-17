# R1 — Architecture (adversarial)

## Attack vectors

### 1. `snp_analysis.py` is not a primitive and cannot be made one without gutting the contract

- Evidence: `snp_analysis.py:run()` (line 291) takes `(group_dir, group_name, out_dir, snp_dists_path, scope)` — group-scoped, not sample-scoped. It runs `snp-dists` across a multi-sample FASTA and produces a pairwise matrix. `AnalysisPrimitive` is sample-scoped by construction (`sample: str` on both the class and `PrimitiveResult`). There is no seam.
- Severity: blocker
- Falsification: show a concrete `SNPAnalysis(AnalysisPrimitive)` implementation where `.run()` returns a valid `PrimitiveResult` with a meaningful `sample` field. Any answer that requires `sample = group_name` exposes the mismatch.
- Detail: The design's entire reporting stack (`.latex(tex)`, `.excel(d)`, `.badge()`) is per-sample. SNP distance analysis is cross-sample by definition. Forcing it into `AnalysisPrimitive` either produces a fake "sample" that is really a group identifier, or requires a second contract (`GroupPrimitive`) the design never acknowledges. `snp_analysis.py` already ships in the codebase as a post-hoc step. It is the hardest case, and the contract fails it.

---

### 2. `run()` entangles execution with provenance write — retries silently corrupt the audit trail

- Evidence: Design §4 docstring: *"Execute the tool, parse outputs, write provenance, return findings."* `AMRFinder.run()` (§5) calls `write_provenance(self.out_dir, self.provenance())` inside the method body. `provenance()` reads `self.result.exit_code`, which is only set if `run_in_conda_env` completed.
- Severity: major
- Falsification: show a retry wrapper that calls `.run()` twice on a transient failure and produces exactly one provenance record with the correct exit code and duration.
- Detail: If `run_in_conda_env` raises, no provenance is written. If the caller retries, two provenance records land with different timestamps. Idempotency checks read stale data. A dry-run mode requires special-casing inside every primitive. The design conflates "run the tool" with "record that we ran it" — these need separate lifecycle hooks or an explicit transaction boundary.

---

### 3. `applicable(sample_context: Mapping)` is untestable and its schema is deferred

- Evidence: §11 Q3: *"What's the canonical dict passed to `applicable(sample_context)`? Needs at minimum: `assembly_fasta`, `fastqs`, `organism`, `coverage`, `kraken_top_species`. Define this in `common/contract.py` as a dataclass — call it `SampleContext`."* This is listed as an open question, not a resolved design decision.
- Severity: major
- Falsification: show that `AMRFinder.applicable()` (§5, line 336) can be unit-tested in CI without a real project tree. The implementation calls `Path(sample_context["assembly_fasta"]).exists()` — a filesystem side-effect inside a classmethod advertised as a cheap applicability check.
- Detail: `applicable()` is called by composers to decide which primitives to offer. If it requires live filesystem state, it cannot be meaningfully unit-tested and will silently return `False` in any environment where the project tree doesn't exist (CI, dry-run, staging). The schema being undefined means every primitive author invents their own keys, and the first integration test failure will expose N incompatible conventions. Deferring this to §11 makes it load-bearing technical debt on day one.

---

### 4. `Project.ensure_assembly()` is a hidden shared-mutable-state race condition

- Evidence: §3: *"First card that needs `<sample>.fasta` runs SPAdes/Shovill... AMR, MLST, Abricate... all check `assembly/<sample>/<sample>.fasta` and reuse if present."* The helper signature is `Project.ensure_assembly(sample, *, threads=8) -> Path` — no locking parameter.
- Severity: major
- Falsification: show the locking mechanism that prevents two OOD cards calling `ensure_assembly()` simultaneously on the same sample and running two SPAdes processes that both write to `assembly/<sample>/`.
- Detail: The design treats concurrent OOD cards as a feature (§3). Each card runs in its own process with no shared lock. `ensure_assembly` has no lock file or atomic rename pattern. SPAdes writes partial output to its final output directory. Concurrent invocations produce a corrupted assembly or a silent race where one process reads a partially-written FASTA.

---

### 5. The "initial home: `backend/app/pipelines/`" coupling is not a deferral — it's a trap

- Evidence: §11 Q1: *"Recommend (a) — defer the package extraction until there's a real second consumer."* §9 Step 3: *"When primitive #2 (sourmash) follows the same contract, extract `pipelines/` into its own repo."* The `AMRFinder` sketch in §5 imports from `.common.contract` and `.common.runners` using relative imports.
- Severity: tradeoff (with a concrete cost)
- Falsification: show that after Step 3 extraction, no `import` path inside `vsnp_gui` requires a change.
- Detail: Relative imports (`from .common.contract import ...`) work when the code lives inside `backend/app/pipelines/`. After extraction to a sibling repo, every import becomes `from pipelines.common.contract import ...`. That is not a refactor that "just works" — every primitive file, every test, and every GUI integration that imports by relative path must be updated. The recommended path actively creates import-path debt in every file it touches. The cost of starting as a proper package from day one (`pip install -e .`) is ~30 minutes and avoids the forced rename pass.

---

### 6. `web()` and `badge()` require `self.result` to be set — stateful presentation methods break the presentation/execution boundary

- Evidence: `AMRFinder.web()` (§5, line 386): `assert self.result, "call .run() first"`. Same pattern in `badge()`. `latex()` and `excel()` reference `self.result.findings` with no guard (lines 413, 421).
- Severity: minor
- Falsification: show a front-end use case where a completed primitive's badge is rendered without calling `.run()` in the same process — e.g., loading a badge from a previously cached `PrimitiveResult` stored in `samples.json`.
- Detail: The design's badge endpoint (§8: `GET /api/projects/{project}/samples/{sample}/badges`) is supposed to call `load_completed_primitives(project, sample)` and then `.badge()` on each. This requires re-instantiating primitives and re-calling `.run()` just to get a badge, OR it requires deserializing a `PrimitiveResult` from disk and injecting it into `self.result` before calling `.badge()`. Neither mechanism is specified. The stateful `assert` pattern means you cannot call `.badge()` on a primitive that ran in a previous session without a re-run.

---

## Recommendations

1. **Introduce a `GroupPrimitive` contract alongside `AnalysisPrimitive`.** `snp_analysis.py` is not an outlier — krona, bracken summaries, and cross-sample AMR matrices are all group-scoped. Split: `AnalysisPrimitive(sample: str)` for per-sample tools, `GroupPrimitive(samples: list[str])` for cross-sample analysis. Without this, every group-scoped tool gets a fake `sample = group_name` that poisons badge/excel logic.

2. **Make `applicable()` a pure function over a typed `SampleContext` dataclass — no filesystem I/O.** The `Path(...).exists()` check belongs in `Project.sample_context()`, not inside the classmethod. Define `SampleContext` in `contract.py` with an `assembly_available: bool` field. This makes `applicable()` testable with a dict literal in CI without a real project tree.

3. **Separate `run()` from `write_provenance()` with an explicit post-run hook.** The primitive executes and returns a `PrimitiveResult`. The framework calls `write_provenance()` exactly once on success. This gives retries that don't double-write provenance, a dry-run mode (skip the hook), and badge rendering from a deserialized result without a re-run.

---

## Where I'd be wrong

- If the lab never runs two OOD cards concurrently on the same sample, the `ensure_assembly` race is theoretical. The design is correct for a single active user.
- If `snp_analysis.py` is intentionally excluded from the contract — treated as a standalone script outside `pipelines/` — attack vector 1 is moot. The design doc never says every existing script must implement the contract; that inference came from "consolidating one-off wrappers."
- If the package is extracted to its own repo immediately after Step 1 (i.e., Step 3 is not actually deferred), the relative-import debt in attack vector 5 never accumulates.
