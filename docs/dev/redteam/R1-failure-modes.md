# R1 — Failure mode analysis (adversarial)

## Attack vectors

### 1. `applicable()` answers a question the filesystem may retract before `.run()` acts
- Evidence: `amrfinder.py:336` — `applicable()` calls `Path(sample_context["assembly_fasta"]).exists()`. If the file is deleted between the check and `AMRFinder.run()`, the primitive raises `FileNotFoundError` — not `PrimitiveError`, not a `verdict=fail` badge, just an uncaught exception. `PrimitiveError` is defined in §11.6 as an open question, so there is no catch-all in the §8 call site sketch.
- Severity: blocker
- Falsification: show every front-end call site wraps `primitive.run()` in `except PrimitiveError` that maps to `Badge(verdict="fail")`, and that `FileNotFoundError` inside a primitive is always re-raised as `PrimitiveError`.
- Detail: Any filesystem surprise (deleted file, wrong permissions, NFS timeout) produces an uncaught exception. The badge is never rendered, `samples.json` is not updated, and the user sees nothing — the system is in a partially-run state with no indication of which primitive failed.

### 2. `ensure_assembly()` has a check-then-act race with no per-sample lock
- Evidence: §3, rule 2 — "First card that needs `<sample>.fasta` runs SPAdes." `ensure_assembly()` checks `Path.exists()` then starts SPAdes. That check and the SPAdes invocation are not atomic. Two OOD sessions (AMR card + MLST card, same project/sample) both pass the check and both start SPAdes writing to `assembly/<sample>/<sample>.fasta`.
- Severity: major
- Falsification: show `ensure_assembly()` acquires a per-sample lock file (`assembly/<sample>/.assembling.lock`) and the second caller blocks and reuses the first result.
- Detail: Two concurrent SPAdes invocations on the same sample saturate the host and produce a silent last-writer-wins on the output path. Neither session detects the collision; both provenance records claim to own the assembly. The winning FASTA may have been produced under resource contention and is not the one the loser hashed.

### 3. `record_finding()` + `write_provenance()` failure leaves findings and provenance permanently out of sync
- Evidence: `amrfinder.py:370-381` — `self.result` is set, then `write_provenance()` is called bare. If `.provenance/` has wrong permissions or `self.provenance()` serializes a NaN (e.g., `float(row["% Identity..."])` on a malformed TSV row), `write_provenance()` raises and propagates out of `.run()`. The caller sees failure, but `record_finding()` may have already written into `samples.json`.
- Severity: major
- Falsification: show `write_provenance()` is wrapped so it degrades to a logged warning without raising, and NaN values in findings are sanitized before JSON serialization.
- Detail: The system ends with `samples.json` showing findings and no corresponding provenance. `verify_provenance.py` cannot distinguish "tool never ran" from "tool ran, provenance write failed," and re-running does not help if the root cause (permissions, NaN) persists.

### 4. `samples.json` corruption after a mid-write kill has no recovery path
- Evidence: §3 names "atomic tempfile + rename + per-project advisory lockfile" but does not specify what happens to the in-flight tempfile when the process holding the lock is killed. POSIX `flock` clears on process death — the lock releases — but the partial tempfile remains. The next writer produces a new tempfile and renames it, overwriting or racing the partial file.
- Severity: blocker
- Falsification: show the write path verifies JSON round-trips before rename and that `Project.from_path()` detects and removes stale tempfiles.
- Detail: Two primitives writing `samples.json` in parallel (different cards, same project) produce a window where the file is a partial tempfile. The next `json.load()` raises `JSONDecodeError`. The project's shared knowledge base is unreadable with no repair path in the design.

### 5. `snp_analysis.py` emits `status: ok` for a numerically corrupted matrix
- Evidence: `snp_analysis.py:181-186` — `sanitize_matrix()` fills all NaN cells with 0 and records `nan_filled: N` in `stats.json`, but `status` remains `"ok"`. A matrix with NaN cells filled as 0-SNP distances clusters those samples together. The KDP plot and closest-neighbor histogram are rendered normally.
- Severity: major
- Falsification: show a threshold (e.g., `nan_filled > 0`) forces `status = "degraded"` and suppresses plot generation, or that NaN in a distance matrix is treated as a hard failure.
- Detail: This is the one implemented primitive in the codebase and it already exhibits the core failure pattern: partial-data corruption produces plausible-looking scientific output with no user signal. The `Badge.verdict` taxonomy has no mapping from `nan_filled > 0`; a corrupted grouping renders as confidently as a correct one.

### 6. Schema drift in `samples.json` silently disqualifies old projects from new primitives
- Evidence: §11.3 — `SampleContext` fields are listed as an open question. No `schema_version` field exists in the §3 example JSON. `applicable()` consults `sample_context` directly; a missing key either returns a misleading `False` or raises `KeyError`.
- Severity: major
- Falsification: show `samples.json` carries `"schema_version"` and `Project.from_path()` runs a migration chain before any primitive consults the context.
- Detail: A project created at Step 1 of the migration path has `{fastqs, organism, amr}`. A ConFindr primitive added at Step 5 expects `coverage`. All pre-existing projects become ineligible for new tools with no diagnostic — indistinguishable from "ConFindr not applicable for this organism."

### 7. Exit-code dual-representation has no defined authority when values disagree
- Evidence: §6 — `record.json` contains `"exit_code": 0` and a separate `exit_code` text file is written. These are described as two separate writes. A kill between them leaves them in disagreement. `verify_provenance.py` is described as checking that they match, but the design does not specify which value is authoritative or what action mismatch triggers.
- Severity: minor
- Falsification: show both values are written atomically in the same `write_provenance()` call, and that `verify_provenance.py` marks a mismatched run as invalid and requires re-run.
- Detail: The sentinel-file pattern was inherited from T-07. Adding an equivalent field to the JSON without deprecating the file creates two sources of truth with no conflict resolution. A monitoring script reading only the file disagrees with one reading the JSON; neither is known to be wrong.

---

## Recommendations

1. **Implement `PrimitiveError` and wrap every `.run()` call site before writing any primitive.** The §8 sketch's bare `primitive.run()` is the gap through which every unhandled exception escapes. One `except Exception as e: raise PrimitiveError(...) from e` inside each primitive's `run()` method, plus `except PrimitiveError` at every call site, closes this gap entirely.

2. **Make `write_provenance()` non-fatal and `record_finding()` atomic with JSON validation before rename.** Provenance write failure must degrade to a logged warning. `record_finding()` must write to a tempfile, validate that `json.loads(tempfile.read())` succeeds, then rename — and scan for stale tempfiles at project open time.

3. **Add `schema_version` to `samples.json` and a migration function in `Project.from_path()`.** Version 0 is the current shape. Every consumer calls the migrator before reading. This is ~30 lines and is the only mechanism that makes projects survive multi-year tool evolution.

---

## Where I'd be wrong

- **Single-session, single-user usage invalidates the concurrency attacks.** If the actual workflow is one person, one OOD session, one project at a time, the `ensure_assembly()` race and `samples.json` write collision never trigger. The design should state this constraint explicitly if it is intentional.
- **T-07 implementation may already harden `write_provenance()` beyond what the design doc describes.** If the existing code (not the §3 sketch) already handles tempfile-rename atomicity and provenance write failure gracefully, vectors 3 and 4 are invalidated by the implementation rather than the design.
- **`snp_analysis.py` is not yet an `AnalysisPrimitive` subclass.** The NaN-filling issue exists in the standalone script. If it is fixed before the contract wrapper is written, vector 5 is pre-empted rather than inherited into the package.
