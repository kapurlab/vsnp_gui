# Decisions required

Two **UNRESOLVED** items surfaced by the Round 3 synthesis. Round 2 reviewers disagreed; the synthesizer was forbidden from arbitrating. These are the calls a human (or independent reviewer) must make before T-27 (`pipelines/common/`) implementation begins.

Both sides are preserved verbatim from [`FINDINGS.md`](FINDINGS.md). The framing is: read both arguments, then pick.

---

## Decision 1: `ensure_assembly()` race — pre-T-27 blocker, or P2 annotation?

The single most-converged R1 finding (4 angles independently raised it) produced a genuine 5-way R2 split.

### The race condition

`pipelines.common.project.Project.ensure_assembly(sample)` is the shared helper that produces a de novo assembly FASTA on first call and returns the cached path on subsequent calls. Two cards on the same sample both check `Path(assembly_fasta).exists()`, both see `False`, both launch SPAdes (15–45 min, RAM-heavy), both write to the same output directory. Last-writer-wins on the final FASTA; downstream primitives consume whichever bytes won the race.

### R1: 4 angles, all real

- **R1-architecture (ARCH-AV4)**: blocker
- **R1-ops (OPS-AV2)**: blocker
- **R1-bioinformatics (BIO-AV4)**: major
- **R1-failure-modes (FM-AV2)**: major

### R2: 5 distinct positions

#### R2-ops (escalating R1-failure-modes' FM-AV2 to blocker)

> "R1 calls this major; it is a blocker. The entire §3 shared-artifact design is predicated on `ensure_assembly()` being safe to call concurrently across cards. It is not. Two OOD cards (AMR + MLST) launched seconds apart on the same project both pass the `Path.exists()` check before either SPAdes creates the file. Two concurrent SPAdes invocations on a 47-sample Brucella project can saturate RAM and produce a last-writer-wins corrupted assembly."

#### R2-bio (UNRESOLVED — both sides explicit)

**Bio side supporting R1**:
> "The race is real in principle. SPAdes writes intermediate files to its output directory during assembly; reading a partially-assembled FASTA would produce a truncated sequence that passes FASTA parsing but fails downstream tools silently. AMRFinderPlus takes 5 seconds per genome while SPAdes takes 15-45 minutes per genome. Any two cards launched within 45 minutes of each other hit this window."

**Bio side against R1**:
> "The lab workflow is single-user, sequential-card. The vsnp_gui OOD session runs one job at a time per project (the JobManager serializes runs). The kraken_gui is currently electron and not yet OOD-deployed. The concurrent-card scenario requires the kraken_gui OOD deployment (Step 6, ~3 hr) to be complete AND both cards running simultaneously on the same sample. This is a future state, not today's deployment."

#### R2-ux (REBUT for UX-priority purposes)

> "Wgs3 is a single-lab installation where concurrent users opening the same sample simultaneously is not a realistic near-term scenario. The user-facing outcome (corrupt assembly) is severe if it fires, but the prior is low enough that this does not rise to user-facing P0. This deserves a code fix (fcntl.flock) but not a UX escalation. Rebut for UX priority; keep as backend P2."

#### R2-steelman (PARTIAL-REBUT — cheaper fix available)

> "The actual OOD usage model on wgs3 today is one authenticated user (vxk1) running sequential pipeline sessions... The surviving valid kernel: `ensure_assembly()` as sketched has no locking stub, no sentinel file, no comment directing an implementer to add one. The fix is one `fcntl.flock` call and a per-sample lockfile. The design should either include it or explicitly annotate `ensure_assembly()` with a `# MUST HOLD <sample>.assemble.lock` comment."

### The decision

Two coherent positions emerge from the R2 split:

**Position A — Pre-T-27 blocker.** The race invalidates the §3 shared-artifact design's central claim. Even if today's wgs3 usage is sequential, the design is meant to scale to Step 6 (kraken_gui OOD) where concurrency materializes. Fix the design now: add `fcntl.flock` to the `ensure_assembly()` sketch and require all assembly-dependent primitives to assert lock-held status. Cost: a few lines in the sketch, plus a contract test that two concurrent `ensure_assembly()` calls serialize.

**Position B — P2 annotation, defer implementation.** Today's deployment cannot hit the race (single user, JobManager serializes). The fix is cheap (`fcntl.flock`) and well-understood, so deferring it to when Step 6 lands is low-risk. Add a `# MUST HOLD <sample>.assemble.lock` comment in the sketch so an implementer doesn't ship the unlocked version by accident, but don't gate T-27 on the locking implementation.

**Tiebreaker considerations** the independent reviewer should weigh:

- The JobManager in vsnp_gui serializes runs *within one card*. Two cards calling `ensure_assembly()` cross-process is unblocked. Position B's "today's deployment can't hit it" relies on there being only one card. The moment Step 4 (vsnp_gui exposes a "Run AMR" button) lands, two contexts within vsnp_gui can race.
- BLOCKER-1 (OOD timeout orphans SPAdes — partial FASTA silently consumed) is independent of locking and remains a blocker either way. It must be solved by validating FASTA completeness, not just by locking.
- MAJOR-4 (cache key is path existence, not assembler identity) is independent of locking and requires `ensure_assembly()` to check provenance, not just `Path.exists()`. The locking fix touches the same code path; bundling them may be more efficient than two passes.

---

## Decision 2: §6 provenance schema — port `capture_env_snapshot()` now, or accept the reduction?

The new design's §6 provenance schema captures less than what T-07 already captures in production. Tod (R1-user-tod) framed this as a regulatory-submission blocker; R2-ops empirically confirmed the schema gap with file:line evidence.

### The gap

[`sources/provenance_writer.py`](sources/provenance_writer.py) at lines 476–487 captures, per run:

- `conda_env_yaml_sha256` (content-addressed to a shared store)
- `pip_freeze_sha256`
- Per-binary version strings for `samtools`, `bcftools`, `bwa`, `mafft`, `raxml`, `iqtree`
- `python_version`, `platform`

The new design's §6 `record.json` schema captures only:

```json
"env": "conda:amrfinder"
```

— just the env name string. No content hash, no version snapshot, no binary probes.

### R2 verdicts

#### R2-ops (RATIFY — empirically confirmed)

> "T-07 captures: `conda_env_yaml_sha256`, content-addressed shared-store path, `pip_freeze_sha256`, per-binary version strings for samtools/bcftools/bwa/mafft/raxml/iqtree, `python_version`, `platform` (provenance_writer.py lines 476–487, confirmed by direct read). The §6 schema for new primitives captures only `\"env\": \"conda:amrfinder\"` — the name string. This is not a sketch approximation; it is the literal canonical schema in the doc. Any developer implementing new primitives from §6 alone ships a two-tier provenance system with no justification."

#### R2-ux (REBUT — not a UX concern)

> "Dev's verdict is correct ('good enough for this lab's use case'). Not a UX issue. The missing `conda list --export` snapshot is invisible to the user at runtime and only matters for future reproducibility audits by external parties."

### The decision

Two coherent positions:

**Position A — Port `capture_env_snapshot()` into §6 now.** The lab's stated values include reproducibility 6 months later, regulatory-submission handoff (Tod's scenario), and audit by external collaborators. T-07 invested in capturing this; the new design abandons it without acknowledgment. An afternoon of porting `capture_env_snapshot()` into `common/provenance.py` brings the new primitives to T-07 parity. The cost of not doing it now is paid by every future primitive that needs retroactive provenance backfill.

**Position B — Accept env-name-only for now, document the reduction.** The new design's user base today is the kapurlab itself, where conda envs are stable and rarely change. The full T-07 provenance is over-engineered for current use; only Tod's handoff scenario needs it, and handoff is itself a future workflow (no pending request). Document in §6 that the schema is a reduction from T-07 with a forward reference; port when the first regulatory handoff actually materializes.

**Tiebreaker considerations**:

- The §6 schema is the **canonical schema** the design doc presents. Developers implementing new primitives from §6 alone (without reading T-07 history) will ship a regression by default. If you choose Position B, the doc must explicitly say "this is intentionally minimal; full env capture lives in `provenance_writer.py:capture_env_snapshot()` and should be added when porting to lab-external handoff."
- Position A's cost is one afternoon. Position B's cost is paid every time someone re-reads §6 and has to be told "actually, do the T-07 thing."
- Tradeoff with TRADEOFF-2 (Apptainer vs conda for regulatory submissions) — if you accept Position B here, you've doubled down on "regulatory submissions aren't first-class for now," which compounds.

---

## T-27 scope-freeze items dependent on these decisions

If you decide **Position A** on both: T-27's `pipelines/common/` includes `fcntl.flock`-based assembly locking AND the full env-snapshot provenance from day one. Heavier T-27, no future backfill.

If you decide **Position B** on both: T-27 ships with annotated stubs (`# MUST HOLD ...` for locking, `# TODO: add capture_env_snapshot() when handoff workflow lands` for provenance). Lighter T-27, two deferred items tracked as P2 follow-ups.

Mixed positions (A on one, B on the other) are coherent — the decisions are independent on the merits.

The full T-27 scope-freeze list (12 confirmed blockers + 2 unresolved + 1 narrowed-but-load-bearing) is in [`FINDINGS.md`](FINDINGS.md) under "Recommended scope-freeze for T-27."
