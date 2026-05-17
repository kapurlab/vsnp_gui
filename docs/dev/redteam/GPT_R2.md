# Cross-examination document

The Round 2 instruction is not asking for reconciliation; it is asking for stress testing, verdicts, and explicit failure modes. I am treating Architect A as my R1 position, and Architects B and C as the two competing R1 positions. The lab brief matters here because the system must support required bioinformatics tools, T-07-level provenance, OOD session expiry, a filesystem-of-record constraint, no workflow DSL, and no analysis database backend.

## 1. Convergence

**All three architects converged on a filesystem ledger: immutable or append-only run records, content hashes, atomic writes, and filesystem state transitions.** This convergence is problem-forced. The brief forbids a database backend and makes T-07’s atomic JSON write and SHA capture the quality bar, so any credible design has to make the filesystem act like a transaction log rather than a loose output dump. A used immutable artifacts and atomic commit; B used committed run directories, `.tmp` directories, and `.committed` sentinels; C explicitly called the filesystem a typed immutable event store.

**All three converged on typed executable units: primitive, task, or job.** This is mostly problem-forced. The platform must support tools with different input/output shapes: vSNP3 sample and panel phases, AMRFinderPlus, SPAdes/Shovill, Kraken2/Bracken/Krona, sourmash sketch/gather, and SNP distance analysis. Bare shell scripts cannot safely encode those differences. The lower-confidence part is the shared Python-architect prior that dataclasses and protocols are the right expression. The problem requires typed contracts; it does not strictly require the amount of type ceremony all three designs reached.

**All three converged on validation before consumption.** This is high-confidence and problem-forced. The `snp_analysis.py`fixture demonstrates a production bug where `sanitize_matrix()` fills NaNs with zero, records `nan_filled`, still reports `"ok"`, and lets downstream plots render corrupted distances. A, B, and C all treated that as a class of failure: outputs must not become consumable merely because a file exists.

**All three converged on extending T-07 rather than shrinking it.** This is forced. The brief says any provenance design that captures less than T-07 is a regression, and T-07 already captures content-hashed conda envs, pip freeze hash, binary probes, Python/platform/hostname, input/output hashes, git SHA, dirty state, patches, and atomic JSON writes.

**All three converged on detached execution outside the user’s OOD session.** This is forced. SPAdes, Shovill, and vSNP3 can outlive interactive sessions; the brief explicitly says current vSNP3 uses a JobManager that detaches jobs so they survive session expiry. A used a filesystem queue and detached JobManager, B used executor submission into JobManager, and C used a persistent spool daemon with `os.setsid`.

**All three converged on Python-internal workflow composition rather than Nextflow/Snakemake/CWL.** This is mostly forced by the brief, which bans a new workflow DSL. The lower-confidence part is the implied belief that a custom Python executor will stay simpler than adopting a known scheduler. The lab’s constraint forces no DSL; it does not force writing a mediocre scheduler.

**All three converged on read-only presentation.** This is high-confidence. The UI must render cached and historical results without re-running tools. A proposed normalized `presentation.json`, B proposed run views as pure functions over committed run directories, and C proposed verified static serving from provenance sidecars.

## 2. Genuine disagreement

### Disagreement 1: Is panel scope first-class?

**A position:** “The platform must model sample artifacts, panel artifacts, and project artifacts as first-class citizens.”

**B position:** “There is nothing special about panel-level analyses; they are simply primitives whose input is a multi-set of artifacts rather than a single artifact.”

**C position:** “Every sample, pipeline run, and panel analysis must be modeled as a content-addressed, append-only directory node.”

**Implicit assumptions.** A assumes lab users, permissions, reports, and scientific interpretation require scope to be visible in the core model. B assumes the execution engine should care about fan-in/fan-out and artifact type, not sample/panel/project labels. C assumes filesystem layout is the schema, so panel directories deserve explicit structural representation.

**Verdict:** B is most correct for the execution layer; A and C are correct for presentation, authorization, and project organization. The lab absolutely has panel analyses, and `snp_analysis.py` is cross-sample by nature, but making `scope_kind="panel"` part of the primitive identity risks parallel logic for sample primitives versus panel primitives. The primitive should accept `MultiSampleAlignment` or a set of artifacts; the UI/report layer can call that panel scope. This is a verdict against my A R1 “Panel scope is first-class” rationale.

**Failure mode of my original position:** I could create special panel execution paths, special locks, and special cache keys when all that is needed is artifact cardinality and artifact type. That would age badly once sourmash sketches, Kraken cohorts, and project-level summaries appear.

### Disagreement 2: Who owns status and validation?

**A position:** A primitive returns a `PrimitiveResult` with `status`, then `validate()` returns findings.

**B position:** “The executor, not the primitive, sets the final status based on the invariant outcome set.”

**C position:** `SNPDistanceAnalysisTask` raises `ValidationError` on NaN, then `_calculate_metrics()` returns `"status": "COMPLETED"`.

**Implicit assumptions.** A assumes primitive authors can be trusted to return status consistently with findings. B assumes the exact production bug happened because tool code was allowed to declare success. C assumes pre-write validation inside the task is enough if the task is strict.

**Verdict:** B is correct. The executor or invariant runner must be the only writer of final status. A is too permissive because it lets a primitive return `status="success"` and also return severe findings; C fixes the NaN instance but leaves status assignment inside the task’s schema. The production failure was not merely NaNs; it was contradictory status. B’s invariant model directly eliminates that contradiction.

**Failure mode of my original position:** My `PrimitiveResult.status` field recreates the same trust boundary that failed in production. I would revise it out.

### Disagreement 3: Runtime strategy: Conda, Apptainer, or hybrid?

**A position:** “Hybrid runtime now; Apptainer for stable external tools long-term.”

**B position:** “Conda over Apptainer as the long-term direction for lab-authored primitives.”

**C position:** “We dictate that all toolsets must migrate to Apptainer images.”

**Implicit assumptions.** A assumes the lab needs two runtime classes: developer-friendly Python/conda for lab-authored code and containerized stable third-party workflows for regulatory replay. B assumes inspectability and iteration speed matter more than host isolation for lab-authored primitives. C assumes long-term legal reproducibility is dominated by host/library drift, so containers should win everywhere.

**Verdict:** A is most correct, but it needs sharper rules. C is right that regulatory-grade third-party tools should move toward content-addressed Apptainer images; B is right that lab-authored Python primitives should not require rebuilding a SIF for every small code change. C’s claim that Apptainer “completely guarantees reproducible outputs down to the bit level” is wrong for stochastic tools, multithreading, nondeterministic filesystem ordering, and tools that embed timestamps.

**Failure mode of my position:** Hybrid can become a junk drawer: some tools in conda, some in containers, inconsistent provenance, and unclear migration pressure. The fix is not “use both”; the fix is a runtime policy table by tool class.

### Disagreement 4: Execution engine: existing JobManager or persistent spool daemon?

**A position:** “FastAPI submits a `request.json` into a filesystem queue. OOD sessions may die; execution belongs to the detached JobManager, not the web process.”

**B position:** “Executor submits the actual work to JobManager,” while `.tmp` directories are swept and reattached or marked failed on restart.

**C position:** “We will not use a generic ‘JobManager’… we will implement a decoupled, file-spooled, out-of-process execution engine driven by standard Linux process mechanics (`os.setsid`).”

**Implicit assumptions.** A assumes the existing JobManager abstraction is reliable enough if made filesystem-backed. B assumes the existing detachment mechanism can be wrapped with better run-state mechanics. C assumes anything child-detached from an OOD-managed process remains operationally brittle.

**Verdict:** C is right on the operational boundary, B is right on orphan handling, and A is under-specified. The worker must be a persistent process outside the OOD/FastAPI lifecycle, consuming a filesystem spool. Whether it literally uses C’s fork pattern is secondary; the decisive point is that execution ownership is not in the web session. B’s `.tmp` sweep and reattach/fail behavior should be imported.

**Failure mode of my position:** If “detached JobManager” is still started by, supervised by, or stateful inside the OOD-launched app, deploys and session death can strand jobs.

### Disagreement 5: Derived SQLite index or no SQLite?

**A position:** JSON indexes are derived and rebuildable, and cache indexes arrive late.

**B position:** “SQLite as a derived, rebuildable index is not a database backend in any meaningful sense; it is `find` with better ergonomics.”

**C position:** FastAPI reads flat project manifests and queue manifests, avoiding heavy recursive walks without a relational index.

**Implicit assumptions.** A assumes correctness comes before query performance, so index sophistication can wait. B assumes UI and replay queries will quickly require structured lookup but that authority can remain on disk. C assumes project manifests plus active queue state are enough for status discovery.

**Verdict:** B is correct. A’s JSON index plan is weaker than necessary, and C’s no-index plan will break once users ask “show every AMRFinderPlus run using DB X,” “show failed invariants for this project,” or “compare all runs of primitive Y.” SQLite is not the analysis source of truth if it is rebuildable from run directories.

**Failure mode of my position:** Delaying the index forces React/FastAPI into either recursive filesystem scans or hand-rolled JSON indexes that become a worse database.

### Disagreement 6: Deterministic cache hits versus new run per collaborator rerun

**A position:** “The rerun creates a new run under the collaborator’s UID but references the original input artifacts and provenance.”

**B position:** “For deterministic primitives, the rerun is a cache hit; for stochastic primitives, the rerun produces a new run dir.”

**C position:** “If a user re-runs a tool, a new directory is generated,” with project state moving through a latest symlink.

**Implicit assumptions.** A assumes audit identity requires a new run record for the collaborator’s act of rerunning. B assumes computational identity and execution identity should be separated: deterministic results can be reused while the access/request event can still be logged. C assumes every run request should materialize a new execution directory.

**Verdict:** B is correct. Deterministic cache hits should not waste CPU or create duplicate scientific artifacts. The system can still record a collaborator’s replay request as an event referencing the cached run. Stochastic tools are different: SPAdes/Shovill should default to fresh execution unless seeded and declared reproducible.

**Failure mode of my position:** My A rerun rule bloats the audit ledger with scientifically duplicate deterministic runs and makes “the result” harder to identify.

## 3. Blind spots

**Blind spot 1: executor-enforced invariants with executor-only status.** B addressed this more rigorously than I did. My A design has validators but still lets `PrimitiveResult` carry status. This is not incompatible, but it requires significant contract rework: primitive code can return outputs and raw findings, but final status must be computed centrally. I would revise A.

**Blind spot 2: stochasticity as a first-class cache policy.** B explicitly declared `stochastic: bool` and made stochastic primitives fresh by default; I used `cacheable` but did not distinguish deterministic caching from stochastic replay. This is significant rework, not trivial. SPAdes, Shovill, and some tree-building modes need a declared determinism policy, seed policy, and comparison policy.

**Blind spot 3: derived SQLite as the query layer.** B made the index concrete with tables for runs, artifacts, invariant outcomes, and databases. I postponed cache indexes and proposed JSON-derived indexes. My design accommodates SQLite with moderate rework because the filesystem remains authoritative. I would replace JSON indexes with rebuildable SQLite.

**Blind spot 4: service account and write authority.** C was more explicit that only the executor should write tool-authoritative directories, while users own raw/project metadata. A had ACLs and POSIX groups but did not pin down whether jobs run as the user or as a pipeline service identity. This is significant rework in deployment and permissions, but compatible with A’s artifact model.

## 4. Things still wrong with the consensus

**The consensus under-specifies resource scheduling.** All three discuss priority, queues, locks, and detachment; none adequately specifies CPU/RAM accounting. On a 64-core, 503 GB Threadripper, one careless SPAdes batch can starve AMRFinderPlus, Kraken2, and vSNP3. Priority ordering is not enough. The executor needs per-tool resource declarations, admission control, and concurrency caps.

**The consensus over-trusts “content hash” without defining canonical directory hashing.** Files are easy. Directories, DB indexes, Kraken databases, sourmash databases, and Apptainer build contexts are not. The platform needs canonical directory manifests: sorted paths, file sizes, SHA256s, excluded transient files, symlink policy, permissions policy, and manifest hash. Without that, “DB hash” is hand-waving.

**The consensus is too optimistic about replay.** B is the most honest about conda replay limits, and A admits stochastic non-determinism; C’s bit-level guarantee is plainly false. Even with Apptainer, stochastic tools and multithreaded reductions can produce different bytes. Replay should promise: same inputs, same code, same runtime artifact, same command, and either byte comparison or declared tolerance comparison. It should not promise universal byte identity.

**The consensus does not sufficiently separate scientific artifact identity from user request identity.** B gets closest with input hashes plus run IDs. The final design should have both: a scientific result artifact keyed by primitive/input/runtime/db hash, and a request/event record keyed by user/time/project. Otherwise cache hits either erase user actions or duplicate scientific records.

## 5. Net revision

I would revise A in five specific ways.

First, I would demote `ScopeKind` from primitive identity. B’s “fan-in, not panel specialness” argument is correct for execution. A should keep sample/panel/project as metadata for UI, ACL, reports, and project organization, not as a branching execution model.

Second, I would remove `status` from `PrimitiveResult`. B’s executor-enforced invariant model is the right response to the production NaN bug. A’s primitive can emit outputs and findings, but the executor alone computes `success`, `degraded`, or `failed`.

Third, I would replace `cacheable: bool` with `determinism` and `cache_policy`: `deterministic_cache`, `stochastic_fresh`, `seeded_reproducible`, or `never_cache`. B forced this revision.

Fourth, I would adopt a rebuildable SQLite index early, not JSON indexes late. B’s index position is correct and does not violate filesystem-of-record because the index is derived.

Fifth, I would make the out-of-process spool worker explicit. C forced this revision. A’s “detached JobManager” language is too vague unless it names a persistent worker outside the OOD/FastAPI lifecycle, with atomic claim, heartbeat, orphan recovery, and append-only job events.