# Cross-examination: A vs B vs C

## 1. Convergence

Eleven decisions converged across all three designs. I separate them by whether the convergence is problem-forced (high confidence) or shared-priors (lower confidence).

**Problem-forced (high confidence):**

1. Content-addressed artifacts via SHA256. T-07 already does this; the brief states this is the quality floor.
2. Atomic JSON writes via mkstemp plus os.replace. The brief explicitly cites this T-07 pattern and flags `snp_analysis.py`'s bare `write_text` as the regression to avoid.
3. NaN must fail or surface, not silently pass. The falsification fixture defines the failure class.
4. Detached execution surviving OOD session expiry. The brief makes the 8-hour reaping a primary constraint.
5. Extending T-07 rather than replacing it. The brief states extending below T-07 is evaluated as regression.
6. Provenance includes tool DB version and hash. AMRFinderPlus and Kraken2 are listed; the `-O` foot-gun is described.
7. Python-internal composition over a workflow DSL. Constraint-forced.
8. Filesystem-of-record with append-only run directories. Constraint-forced (and arguably wrong; see section 4).
9. Apptainer accepted at minimum for NAHLN_AMR-distributed tools. All three accept this.

**Shared-priors (lower confidence):**

1. Typed Primitive abstraction with declared inputs, outputs, runtime, validation. A and C explicit; B less typed but same shape. We have all seen Nextflow processes, CWL CommandLineTools, WDL tasks, and Snakemake rules. The convergence may reflect this rather than the problem requiring the abstraction at 12-15 tools. A simpler "registered tool function with a decorator" pattern could plausibly be enough.
2. Cache-key memoization with submission-time deduplication. This is the standard Bazel/Nix pattern. Whether three lab members plus collaborators actually generate enough duplicate submissions to justify the design complexity is unclear. None of us measured the deduplication rate the design assumes.

The pattern I want to flag explicitly: all three of us treated "scope" (sample vs panel) as a question worth answering, even though the brief did not ask us to. The fact that we all reached for this concept may itself be a shared prior. See section 2.1.

## 2. Genuine disagreement

### 2.1 Panel scope: first-class or derived

**A (me):** Scope is a derived property of fan-in, not a first-class concept. Section 1. **B:** Panels are a directory tree (`/panels/PANEL_MAY_2026/`) parallel to samples. Section 3. Implicit first-class. **C:** Explicit first-class `ScopeKind = Literal["sample", "panel", "project"]`. Section 2.

Assumptions producing each position. A: the type system handles fan-in via artifact types (`MultiSampleAlignment`), so scope adds nothing. B: scope governs filesystem layout. C: scope governs UI grouping, access semantics, and how users address runs.

**Verdict: C is correct. I was wrong.** Citation: C Section 1, "The platform must model sample artifacts, panel artifacts, and project artifacts as first-class citizens." Panels have mutable membership (samples get added to an outbreak investigation as new sequences arrive) and stable identity that is independent of any specific alignment artifact derived from them. In my design, a user submitting `snp_distance` provides a `MultiSampleAlignment` artifact, which means they must know which vSNP3 step-2 run produced it. That is correct for caching but wrong for the UI: the user thinks "May 2026 Mbovis outbreak panel," not "alignment artifact 8f3c91...". The panel concept needs to exist independently so the executor can resolve current membership to a current alignment at submit time.

**Failure mode of the revised position:** if the lab later needs analyses that are neither sample, panel, nor project (e.g., pair-scope for transmission inference between two clusters), the enum needs extending. This is acceptable; pair-scope as a fourth tag is a small change.

### 2.2 Conda vs Apptainer as long-term direction

**A (me):** Conda for lab-authored primitives, Apptainer accepted for externally distributed tools. Section 10 rationale 3. **B:** Strict Apptainer migration in Phase 4. All tools become SIF. Section 5 and Section 10 rationale 2. **C:** Hybrid; Apptainer for stable external tools long-term. Section 10 rationale 4.

Assumptions. A: conda YAML introspectability is worth the dependency drift risk for code under active development. B: glibc-level isolation matters for legal use; long-term conda drift is unacceptable. C: the cost calculus is per-tool.

**Verdict: B is wrong. A and C converge from different sides.** Citation: B Section 10 rationale 2, "Conda does not isolate host system level libraries like glibc. An OS security update on the host Threadripper node can alter binary performance or break execution for historical Conda packages." This is technically true but operationally exaggerated. Bioconda packages that care about glibc are statically linked or pin to compatible ranges; the breakage frequency does not justify the migration cost. The deeper failure of B's position is the chicken-and-egg in Phase 4: building reproducible SIFs requires either pulling from a registry (which moves the trust problem) or building locally from conda lockfiles (which is exactly the conda dependency B called unreliable, just frozen at one moment). B is doing a multi-month engineering effort to get marginal gains over storing the resolved conda env post-solve.

**Failure mode of my position:** if the lab needs to defend a result in litigation 5 years out and the conda solve cannot be reconstructed because bioconda rotated channels, an Apptainer SIF would have been safer. My mitigation (storing the resolved env post-solve) helps but does not guarantee re-solveability. I flag this as a residual uncertainty.

### 2.3 Who writes `status`

**A (me):** Only the executor. The primitive cannot. Section 4 step 7, section 7. **B:** The worker subprocess writes status. Section 4: `execute_detached_workflow` calls `mark_job_complete(job_id)` from inside the forked child after `subprocess.run` returns. **C:** The primitive returns a `PrimitiveResult` with a `status` field; findings exist as typed data. Section 2.

Assumptions. A: separating computation from validation from status assignment is the only way to make the NaN-style contradiction structurally impossible. B: the worker has the most information, so it is trusted. C: primitives are trusted because findings are part of the typed result.

**Verdict: A is correct.** Citation: B Section 4, the forked child calls `mark_job_complete(job_id)` directly after the subprocess returns. This is exactly the failure mode that produced the NaN bug. Subprocess exit code 0 does not imply the output is valid; that is the whole point of the falsification fixture. C's position is closer to mine but still lets the primitive author write a status field; the executor should compute status from findings, the primitive should report findings.

**Failure mode of my position:** subprocess-level failures (the binary crashed before producing output) do not correspond to any declared data invariant, but the executor must still assign a status. My design handles this with an implicit `subprocess_exited_nonzero` invariant, which shoehorns process-level failures into the data-invariant framework. A cleaner design would distinguish "execution failed" from "validation failed" as orthogonal axes. I flag this as underspecified in R1.

### 2.4 Indexing

**A (me):** SQLite index, derived and rebuildable. Section 3 and 10 rationale 2. **B:** No index; FastAPI reads manifests and queue directly. Section 6. **C:** JSON index files. Section 3.

Assumptions. A: list queries require indexed lookup; SQLite is the smallest tool giving joins and pagination. B: `.project_manifest.json` plus queue files suffice. C: indexed lookups are needed but JSON files cover the flat-lookup case.

**Verdict: A is correct. C is acceptable. B is wrong.** Citation: B Section 6, "FastAPI does not perform heavy recursive walks of the disk. It reads the project directory's flat `.project_manifest.json` and parses any active job manifests in `/shared/queue/` tagged with that `project_id`." This works for "status of project X" but fails for "show me all AMRFinderPlus runs across projects in 2026 where the organism flag was Mycobacterium_bovis." The lab will want exactly this query for regulatory and surveillance reports. B has no answer short of full recursive walk on every page load.

C's JSON indexes work for flat lookups and require rewriting the moment a multi-field query appears. SQLite gives you that essentially free.

**Failure mode of my position:** SQLite as "derived cache" can quietly drift into being the source of truth if developers cache writes to it during the commit window and forget to rebuild. My `kapur reindex` mitigates but I did not specify a verification cadence. Index/filesystem drift is unpleasant to debug.

### 2.5 Lock mechanism

**A (me):** `flock` during the commit window only. **B:** `fcntl.flock` held for the duration of cross-sample analyses. **C:** Atomic `mkdir` plus heartbeat files.

**Verdict: C is correct.** `flock` does not survive across NFS reliably; if the lab moves to multi-node storage within 12 months (which the brief states), `flock` semantics become inconsistent. Atomic `mkdir` works over NFS. My short-lock-window design partially mitigates by reducing the time `flock` matters, but C's mechanism is more robust.

**Failure mode of C's position:** heartbeat-based stale detection has a window where a hung process (GC pause, swap, blocked I/O) holds the lock; tuning the heartbeat interval against worst-case pauses determines whether you get spurious lock breaks or wedged runs.

### 2.6 Process detachment mechanism

**A (me):** Use the existing JobManager from T-07. Section 4. **B:** Explicit `os.fork` plus `os.setsid` daemon. Section 4. **C:** Detached JobManager (existing pattern). Section 4.

**Verdict: B's mechanism is more invasive than the problem requires.** The brief states "the current vSNP3 uses a JobManager that detaches jobs so they survive session expiry; you should assume this pattern is available." B is rebuilding what already works. The `os.fork` plus `os.setsid` pattern is correct in isolation but reintroduces the daemon-lifecycle problem (who supervises the supervisor) that JobManager presumably already solved. B's section 4 says "managed via a user-space systemd service" almost as an afterthought; this is a real operational addition the brief did not request.

**Failure mode of my position:** if the existing JobManager has limitations not stated in the brief, my design inherits them.

## 3. Blind spots

1. **Raw input write protection (B).** B's `chmod 440` on raw FASTQ files after upload. I never specified protecting user-uploaded raw data from later modification. Accommodates trivially: my design already chmods committed outputs read-only; extending to raw inputs is a one-line change in the upload handler.
2. **Explicit project manifest with authorized users (B and C).** B's `.project_manifest.json` and C's `acl.json` encode authorization intent alongside POSIX groups. My R1 said "POSIX group membership is the access primitive" and stopped there. POSIX groups answer "can this user read this file?" but not "who is the PI of record?" or "what is the regulatory disposition of this project?". Accommodates with significant rework: I need to add a `project.json` to the data model, user-authoritative and mutable, listing authorized users (mirroring the POSIX group, with POSIX as enforcement). Real addition; see revision 2.
3. **Normalized presentation.json as a committed artifact (C).** C's `presentation.json` as a separate file in every run with a defined schema that React consumes directly. My design has "view functions" but does not commit views to disk. Accommodates with significant rework: making presentation a committed artifact means presentation schemas can drift from artifact schemas and need their own migrations. I am not convinced this is worth the cost; on-read view functions are cheaper and version with the application code. I would not revise on this.
4. **Submit-time pinning of "latest" DB versions (C).** C resolves "latest" Kraken2/AMRFinderPlus DB to a content hash at submission time. I included DB hashes in `input_hash` but did not specify the submit-time freeze. Accommodates trivially: my submission flow needs a resolution step before hashing.
5. **AMRFinderPlus valid-organism list as a hashed artifact (C).** C captures the valid organism list as a hashable artifact and uses it to prevent invalid `-O` submissions in the UI. I treated the organism check as a runtime probe and a possible refusal. C's approach is stronger because the UI prevents the bad submission rather than catching it at execution time. Accommodates trivially; this is a UI improvement on the same data my provenance already captures.

## 4. Things still wrong with the consensus

All three of us accepted that running snp-dists on a 64-sample panel and waiting for a queue worker to claim it is acceptable. It is not. snp-dists runs in seconds; ABRicate on a single assembly runs in seconds; sourmash compare can run in seconds. The queue latency tax (poll interval plus claim plus dispatch) probably adds 10-30 seconds per call. A synchronous fast-path for primitives declared short-running and cacheable, executed in-process with the same provenance and validation machinery, would serve the interactive UI substantially better. None of us specified this. I think it matters for adoption: users will go around the platform if the interactive feel is worse than running the tool directly in a shell.

All three of us converged on hashing every output file. For SPAdes assemblies (50-500 MB) and Kraken2 outputs (potentially gigabytes), eager hashing is real CPU overhead, not negligible. None of us discussed when hashing should be deferred or computed in parallel with the next step. Provenance requires the hash exists before commit; the hot path of user-visible status updates does not require it.

All three of us accepted the no-DB constraint. I pushed back narrowly on SQLite-as-index; B and C accepted it more fully. The honest rebuttal to the team: a SQLite file living next to run directories, written by the executor, rebuildable from filesystem, is functionally a database. C's `indexes/*.json` is a database with worse query semantics. The constraint as stated produces worse architecture than necessary. Only I named this in section 10. B and C both went along.

## 5. Net revision

Two revisions to R1. Both are revisions to existing decisions, not new sections.

**Revision 1: Promote panel and project to first-class scope.** R1 section 2 stated `scope: Literal["sample", "panel", "pair"]` was "informational, drives UI grouping only." This is wrong. C Section 1's argument forced this. Panels have mutable membership and stable identity, distinct from any alignment artifact they produce. Panel and Project become first-class objects in the data model, each with its own directory under `/shared/projects/<project>/panels/<panel_id>/panel.json`. The artifact-derivation logic I described still holds; what changes is that users submit requests against a panel ID, and the executor resolves the current membership to an alignment artifact at submit time. This changes my section 3 (data model) by adding panel and project directories; it does not change my section 4 (execution model) because the input_hash is still computed from artifacts, not from panel IDs.

**Revision 2: Add `project.json` to the data model with an authorized_users list.** R1 section 3 stated "Projects, users, and groups are not abstractions of the analysis layer; they are filesystem properties." This left no place for project intent (PI of record, study identifier, regulatory disposition). B's `.project_manifest.json` and C's `acl.json` are correct that this needs to exist. I add a single `project.json` per project directory, user-authoritative and mutable, with project metadata and an `authorized_users` list that mirrors the POSIX group. POSIX remains the enforcement primitive; the JSON is the intent declaration plus the metadata the regulatory reports need.

I am not revising on Conda vs Apptainer (B's argument fails on migration cost and on its own internal logic), on status authority (B's worker-writes-status pattern is the falsification fixture's failure mode), on SQLite indexing (B's no-index approach cannot answer the cross-project queries the lab will need), or on the lock window (C's mkdir+heartbeat is better; I noted this but my flock during a short window is acceptable until multi-node storage arrives, at which point I migrate to C's mechanism). On those, B or C made arguments I considered and reject.