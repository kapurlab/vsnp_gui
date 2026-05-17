# R2 — Steelman defense against convergent R1 findings

**Role:** Cross-examination, steelman. Find the strongest defense the design
can make for each convergent finding. Ratify only if no rebuttal holds.

---

## Convergent finding 1: Sample-shaped contract vs panel-shaped tools

- **Verdict: PARTIAL-REBUT**

- **Strongest defense:**
  The design never claims `AnalysisPrimitive` is the only contract in the
  package. Section 10 ("What we explicitly don't do") is a list of
  out-of-scope choices, not a closed taxonomy of contracts. The existing
  `snp_analysis.py` is never described as a primitive anywhere in
  PIPELINES_PACKAGE.md — R1-architecture.md's own "Where I'd be wrong"
  concedes this: "If `snp_analysis.py` is intentionally excluded from the
  contract — treated as a standalone script outside `pipelines/` — attack
  vector 1 is moot."

  The sample-shaped contract is correct for every tool listed in §2's worked
  primitives: AMR, MLST, Kraken, Sourmash, ConFindr, CheckM, SeqSero2,
  ABRicate. All of these are genuinely per-sample operations. The badge
  endpoint (§8) and the `samples.json` knowledge base (§3) are explicitly
  per-sample data structures — they exist because the workload IS
  sample-centric. The design chose the right granularity for the actual
  majority case.

  Introducing `GroupPrimitive` from day one before any group-scoped tool is
  being migrated is premature generalization. §9's migration path is
  deliberately incremental: build the contract that fits 8 of 9 tools, then
  extend when the 9th real case arrives.

- **Surviving valid kernel:**
  The design is silent on how SNP analysis (or any cross-sample aggregation)
  fits the package at all. R1's specific claim — that `PrimitiveResult.sample:
  str` makes it *impossible* to express group tools — is too strong, since a
  `GroupPrimitive` subcontract is compatible with the existing package
  structure. But the design genuinely doesn't acknowledge the gap. As written,
  a developer implementing SNP distances in this package has no guidance and
  will likely reach for the wrong base class. That documentation gap is real.

---

## Convergent finding 2: `ensure_assembly()` race

- **Verdict: PARTIAL-REBUT**

- **Strongest defense:**
  Four R1 angles surface this, but all four acknowledge the same qualifying
  condition: R1-architecture says "The design is correct for a single active
  user." R1-ops says "if wgs3 is used by a single authenticated user at a
  time and OOD sessions are never concurrent, the race conditions do not
  materialize." R1-failure-modes repeats it verbatim. R1-bioinformatics
  confines its race concern to assembler parameter mismatch, not file
  corruption per se.

  The actual OOD usage model on wgs3 today is one authenticated user (vxk1)
  running sequential pipeline sessions. The design says OOD sessions are
  concurrent features, but the real workflow is: finish step1, open AMR card.
  Not: open AMR and MLST simultaneously. SPAdes on clinical isolates takes
  10-60 minutes — a user who wants both AMR and MLST badges waits for one
  card to finish before opening the other, because there's nothing else to do
  in between. The practical race window is essentially zero.

  More importantly: the design's §3 Rule 2 explicitly names the race risk in
  "First card that needs `<sample>.fasta` runs SPAdes/Shovill." It is not
  blind to the issue. Specifying the behavior without specifying the lock
  mechanism is a deferral, not an omission — the open questions in §11 are
  the documented deferral register.

- **Surviving valid kernel:**
  The deferral is not cost-free. `ensure_assembly()` as sketched has no
  locking stub, no sentinel file, no comment directing an implementer to add
  one. If this code is written by a developer who did not read §3 Rule 2
  carefully, the race is real, and on a 503 GB machine with 64 cores, two
  simultaneous SPAdes runs will complete without crashing — producing a
  last-writer-wins silent corruption. The fix is one `fcntl.flock` call and
  a per-sample lockfile. The design should either include it or explicitly
  annotate `ensure_assembly()` with a `# MUST HOLD <sample>.assemble.lock`
  comment. The absence of that annotation is the true surviving gap.

---

## Convergent finding 3: `samples.json` corruption / no stale-lock recovery

- **Verdict: PARTIAL-REBUT**

- **Strongest defense:**
  The R1 critique of `samples.json` atomicity is largely mooted by the
  existing implementation in `provenance_writer.py`. That module's
  `_atomic_json_write()` (line 120) already implements the correct pattern:
  `tempfile.mkstemp()` in the same directory, `os.fdopen()` write,
  `os.replace()` rename, with `os.unlink(tmp_path)` on any exception.
  `os.replace()` on Linux is a POSIX rename — atomic at the filesystem level.
  A mid-write kill leaves a partial tempfile but does NOT corrupt the
  production path, because `os.replace()` only executes after a successful
  write. The next invocation produces a new tempfile and a new atomic rename;
  the partial file is orphaned with a `.{filename}.XXXXXX` name, visible to
  cleanup scans.

  R1-ops claims "partial tempfile remains" and "the next writer... renames it,
  overwriting or racing the partial file." This is wrong: `os.replace()` does
  not overwrite a partial tempfile from a different invocation — each
  `mkstemp` produces a unique suffix. The surviving tempfiles are harmless
  noise, not a corruption vector. `Project.from_path()` can scan and prune
  them with a single glob on startup.

  The stale-lockfile concern is real only if `fcntl.flock` (kernel-managed,
  automatically released on process death) is NOT used and instead a PID-file
  advisory lock is used. The design doesn't specify which — that's the actual
  gap, not the write atomicity.

- **Surviving valid kernel:**
  `samples.json` does not have `JSONDecodeError` recovery specified. If a
  mid-write kill happens before `os.replace()` executes (e.g., during the
  `json.dump()` call itself), the production file is intact but the next
  `json.load()` in the same process may read a stale in-memory state. No
  repair path for a corrupt production file (which cannot happen via
  `os.replace()` but can happen via manual editing or `write_text()` as in
  `snp_analysis.py:288`) is described. That bare `path.write_text()` in the
  already-shipped `write_stats()` is the real existing gap — it's not atomic,
  and it's in production code.

---

## Convergent finding 4: `applicable()` can't express runtime state

- **Verdict: PARTIAL-REBUT**

- **Strongest defense:**
  R1 conflates two distinct roles that `applicable()` is designed to serve.
  The design's stated purpose (§4): "lets composers ask 'should I offer to
  run this on this sample?' without instantiating the primitive." This is
  explicitly a UI-triage function — it controls whether the "Run AMR" button
  appears. It is not intended to be the final validation gate before
  subprocess execution.

  The filesystem I/O in `AMRFinder.applicable()` (checking
  `assembly_fasta` path) is appropriate for this triage role: before showing
  the user a button, verify the prerequisite artifact exists. The check runs
  in the GUI thread at render time, not in CI. For CI testing, the contract
  does not require calling `applicable()` — tests call `.run()` directly on
  a known fixture. The claim that a classmethod with filesystem I/O is
  "untestable" is wrong: mock `Path.exists()` or use `tmp_path` fixtures. This
  is standard pytest practice.

  The R1-architecture suggestion to move the `Path.exists()` check to
  `Project.sample_context()` and add `assembly_available: bool` to
  `SampleContext` is a valid refactor that makes mocking easier — but it does
  not change the logical correctness of the current design. It is a testability
  improvement, not a correctness fix.

- **Surviving valid kernel:**
  The gap is narrower than R1 claims but still real. `applicable()` cannot
  express runtime-variant conditions (DB version compatibility, organism-flag
  resolution against the current AMRFinder binary) without filesystem or
  subprocess I/O. Those checks legitimately belong in a pre-run validation
  step, not in the triage classmethod. The design's §11 Q6 acknowledges
  `PrimitiveSetupError` for environment-level failures but does not define
  where organism-flag validation lives. A developer implementing AMRFinder
  today will either skip organism validation (putting it in the caller's
  `build_kwargs`, which is undefined) or add subprocess I/O inside
  `applicable()`, making every button render expensive. Neither outcome is
  acceptable. The design needs a named two-phase validation hook:
  `applicable()` for cheap triage, `pre_run_check()` for environment
  validation that can raise `PrimitiveSetupError`.

---

## Convergent finding 5: Schemaless findings / `dict[str, Any]` web shape

- **Verdict: PARTIAL-REBUT**

- **Strongest defense:**
  `dict[str, Any]` findings are not unique to this design — they are standard
  practice in scientific pipeline frameworks (Snakemake, Nextflow) where each
  rule produces its own output format and the consumer is schema-aware. The
  design's findings list is NOT consumed generically: `.badge()`, `.latex()`,
  and `.excel()` are per-primitive methods that know the exact schema of their
  findings. The design never claims a generic renderer reads raw `findings`
  dicts — it claims the per-primitive `.web()` output can be consumed by a
  generic badge renderer. Those are different things.

  TSV column rename → silent empty findings is a real concern for any parser,
  but the design's `findings` list is explicitly normalized (§5: "tool-specific
  records, normalized") — the primitive is responsible for the TSV-to-findings
  translation. If AMRFinder renames a column, the parser in `amrfinder.py`
  breaks loudly during development (a `KeyError` in tests on the smoke-test
  TSVs) — not silently in production. The 8-sample regression fixture from
  §13 is exactly the CI guard that catches this.

  The R1-bioinformatics request for typed dataclasses (`AMRFinding`,
  `MLSTFinding`) is a valid quality improvement but not a correctness
  requirement. Python's type system with `TypedDict` or dataclasses would
  catch column renames at type-check time, which is earlier than CI test time
  but only marginally so. The design's priority was demonstrating the contract
  shape; adding per-finding dataclasses is a natural Step 1 refinement, not
  a design failure.

- **Surviving valid kernel:**
  The R1-UX critique is more damaging than the R1-bioinformatics framing.
  `.web()` returning `dict[str, Any]` means each primitive's React component
  must be written from scratch with no shared rendering infrastructure.
  The design's claim in §8 of "no per-tool special-casing in the GUI" is
  contradicted by `AMRFinder.web()` returning a gene table with class colors
  and a Kraken primitive returning taxonomy trees — these require different
  components regardless of the Python contract. Adding a `kind` field to
  `.web()` output (as R1-UX recommends) would enable generic dispatch in
  React without per-primitive `if/elif` chains. That is a real gap in the
  contract as specified: the backend is generic-friendly; the frontend
  contract is not.

---

## Summary

- **Findings rebutted (weaker than they look):**
  - Finding 3 (write atomicity): `provenance_writer.py`'s `_atomic_json_write()`
    already implements correct tempfile+rename semantics; the corruption scenario
    R1 describes cannot happen via that path. The critique's strength depended
    on assuming no atomicity mechanism exists.

- **Findings ratified (confirmed real):**
  - None fully ratified. Every finding has a partial defense. The design
    committed no single architectural blunder that should block implementation.

- **Findings with surviving kernel (design needs narrower changes than R1 demanded):**
  - Finding 1 (sample vs panel contract): Design is correct for 8/9 tools;
    needs a documentation note that group-scoped tools require a separate
    subcontract, not a rewrite.
  - Finding 2 (ensure_assembly race): Race is real if two sessions ever
    co-occur; fix is a single `fcntl.flock` annotation, not a redesign.
  - Finding 3 (samples.json corruption): `write_stats()` in `snp_analysis.py`
    uses bare `write_text` — that existing non-atomic path is the real gap,
    not the designed `record_finding()` path.
  - Finding 4 (applicable() expressiveness): Needs a named `pre_run_check()`
    hook for environment validation; the classmethod triage role is correct.
  - Finding 5 (schemaless findings): Backend `findings` dict is defensible as
    primitive-normalized; frontend `.web()` needs a `kind` discriminator to
    enable generic React dispatch without per-primitive branches.
