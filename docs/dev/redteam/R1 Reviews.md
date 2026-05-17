## Round 1 design (Architect B)

# Design: A Bioinformatics Analysis Platform for the Kapur Lab

## 1. Problem statement

The brief is mostly right, but its framing of `snp_analysis.py` as a "panel-level" anomaly is a category error worth surfacing. There is nothing special about panel-level analyses; they are simply primitives whose input is a multi-set of artifacts rather than a single artifact. Treating "panel" as a separate architectural concern would lead to a parallel code path that splits the world badly. The real distinctions that matter to the design are (a) determinism vs stochasticity of a tool, (b) whether outputs are content-hashable, (c) whether the tool depends on an external database that drifts, and (d) the fan-in/fan-out shape of inputs. Sample-scope vs panel-scope is a derived property of fan-in, not a first-class concept.

The brief also frames the NaN bug as an instance to be addressed. The deeper failure is that the tool author was permitted to write `status: "ok"` while simultaneously recording `nan_filled: N`. That contradiction is not a coding error; it is the absence of any architectural mechanism that ties output status to data-quality invariants. Fixing the one line is worthless. The fix is to make it impossible for any primitive to declare success while violating its own postconditions.

Finally, I accept the no-DSL constraint and the no-DB-as-backend constraint, but I will push back in section 10 on a narrow point: a rebuildable SQLite index over the filesystem-of-record is not a database backend, it is a derived cache. The filesystem stays authoritative; the index exists because pagination and "find all runs of primitive X with input Y" should not require a recursive `find`.

The platform supports four primary actors: lab members with cross-project read/write, project-scoped collaborators, the OOD session itself (which is short-lived), and a long-running detached executor. The first-class objects of the platform are tool invocations, not sessions.

## 2. Core abstractions

Five concepts, in dependency order.

**Artifact.** A typed reference to a file or directory on disk with its content hash and a declared type. Artifacts are immutable once committed. Types are Python dataclasses describing on-disk shape, not just file format.

```python
@dataclass(frozen=True)
class Artifact:
    type: type[ArtifactType]   # e.g., ShortReadFastq, AssemblyFasta, MultiSampleAlignment
    path: Path
    sha256: str                # of the file, or of a manifest for directories
    role: str                  # the role this artifact plays in the run that produced it
```

Artifact types form a small closed hierarchy: `ShortReadFastq`, `LongReadFastq`, `AssemblyFasta`, `BamAlignment`, `VcfCalls`, `MultiSampleAlignment`, `DistanceMatrix`, `Plot`, `ReportJson`, `KronaHtml`, etc. New types are added explicitly. There is no `GenericFile`.

**Primitive.** A typed callable wrapping one tool invocation. Declares inputs (typed artifacts), outputs (typed artifacts), parameters (a typed dataclass), environment (a conda env reference or an apptainer image reference), version probes, and invariants. A primitive is the unit of caching, provenance, and retry.

```python
class Primitive(Protocol):
    name: str
    version: str                          # primitive code version, distinct from tool version
    stochastic: bool                      # True for SPAdes, Shovill, RAxML in some modes
    scope: Literal["sample", "panel", "pair"]   # informational, drives UI grouping only
    inputs:  dict[str, type[ArtifactType]]
    outputs: dict[str, type[ArtifactType]]
    params:  type                         # a dataclass type
    env:     EnvRef                       # CondaEnv(sha256=...) or ApptainerImage(sha256=...)
    invariants: list[Invariant]
    probes:  list[VersionProbe]           # tools and DBs whose versions must be captured

    def execute(self, ctx: RunContext, **artifacts) -> dict[str, Artifact]: ...
```

**Invariant.** A postcondition on outputs and inputs, checked by the executor after the primitive returns and before the run is committed. Returns `Pass`, `Repaired(detail)`, or `Fail(detail)`. The executor, not the primitive, sets the final status based on the invariant outcome set.

**Run.** A record of one execution of a primitive with specific inputs and parameters. Identified by a content-derived `input_hash` plus a ULID `run_id`. A Run owns a directory on disk and is the unit of audit.

**Executor.** Schedules primitives, performs input hashing, looks up the cache, acquires locks, dispatches to the JobManager for detachment, runs invariants, atomically commits, writes provenance. The executor is the only object that writes the `status` field.

That is the entire model. Projects, users, and groups are not abstractions of the analysis layer; they are filesystem properties (POSIX group ownership of project directories) consumed by a thin authorization layer in FastAPI.

## 3. Data model

The filesystem is the system of record. Layout:

```
/shared/projects/<project>/
  runs/<primitive>/<input_hash>/<run_id>/
    manifest.json        # inputs, params, env ref, invariants list, primitive version
    provenance.json      # extended T-07
    outputs/             # the actual output files, named by role
    invariants.json      # per-invariant outcome
    status               # one of: pending, running, committed, degraded, failed
    .committed           # zero-byte sentinel; presence means status is final
    .lock                # flock target during commit window only

  store/
    envs/<sha256>.yaml           # content-addressed conda env exports
    images/<sha256>.sif          # content-addressed apptainer images
    artifacts/<sha256>           # hardlinks to immutable output files (CAS)

  index.sqlite           # derived cache, rebuildable from runs/

/scratch/<user>/<run_id>/        # primitive working directory, never authoritative
/home/<user>/                    # user scratch and notebooks; not consumed by executor
```

`input_hash` is the SHA256 of a canonical JSON serialization of `{primitive name, primitive version, sorted inputs by role with their SHA256s, params dict, env ref, relevant DB hashes}`. Two requests with identical inputs and parameters produce the same `input_hash` and therefore can share a cache hit. The `run_id` distinguishes multiple runs of the same `input_hash` (allowed for stochastic primitives, returned-cached for deterministic ones).

Mutability rules:

A committed run directory is read-only. The application enforces this; on supported filesystems we also `chmod -R a-w` on commit. Reruns produce new run directories; we never rewrite. The `store/` directory is append-only; old envs and images are retained because old runs reference them. The `index.sqlite` is rebuildable and never the source of truth; if it is deleted, a `kapur reindex` walks `runs/` and reconstructs it.

User-authoritative vs tool-authoritative: `manifest.json` is user-authoritative (it records what the user asked for); everything in `outputs/`, `provenance.json`, `invariants.json`, and `status` is tool-authoritative.

Example `manifest.json` (abbreviated):

```json
{
  "primitive": "snp_distance",
  "primitive_version": "1.0.0",
  "inputs": {"alignment": {"sha256": "8f...", "type": "MultiSampleAlignment", "path": "../../vsnp_step2/.../outputs/alignment.fasta"}},
  "params": {"distance_method": "snp-dists", "max_n_frac": 0.1},
  "env_ref": "envs/3c91...yaml",
  "requested_by": "vkapur",
  "requested_at": "2026-05-16T14:22:01Z",
  "input_hash": "a2c4f1..."
}
```

## 4. Execution model

A primitive runs as a state machine: `pending → running → (committing → committed | degraded) | failed`. The transitions are:

1. Caller invokes `executor.submit(Primitive, inputs, params)`.
2. Executor computes `input_hash`. If a committed run with that hash exists and the primitive is deterministic, it returns the cached run. If stochastic, the caller must opt in to caching explicitly; default is a fresh run.
3. Executor creates `runs/<primitive>/<input_hash>/<run_id>.tmp/` and writes `manifest.json`. The `.tmp` suffix is critical; it makes orphans visible.
4. Executor acquires `flock` on `runs/<primitive>/<input_hash>/.lock` for the metadata-write window only. The lock is released before the long-running compute begins.
5. Executor submits the actual work to JobManager (the existing detachment mechanism from T-07). Compute runs under `/scratch/<user>/<run_id>/`. Session expiry has no effect on a detached job.
6. On primitive return, the executor moves outputs into `outputs/` inside the tmp dir, then runs invariants over inputs and outputs.
7. Executor writes `provenance.json` and `invariants.json`. Status is computed: `committed` if all invariants pass, `degraded` if any returned `Repaired`, `failed` if any returned `Fail`. The primitive does not write `status`.
8. Atomic commit: rename `<run_id>.tmp` → `<run_id>`, then `open(".committed", "x")`. The rename plus sentinel together are the commit barrier.

Dependencies between tools are expressed as ordinary Python function composition. There is no DAG library and no DSL. A multi-step workflow like Kraken2 → Bracken → Krona is a small Python function that calls three primitives and returns their three runs. The function is itself the workflow definition, version-controlled in the application repo, and recorded in provenance as `workflow_caller`.

Session killed mid-run: nothing happens to the detached job. The job continues, completes, and commits. The user's UI session, when re-opened, finds the committed run by querying the index. If the killed session was the executor process itself (rare, but possible during deploys), on restart the executor sweeps `runs/*/*/*.tmp/`. For each tmp dir with a live job pid, it reattaches; for each tmp dir with a dead pid and no live job, it either retries (if `retry_on_orphan: true`) or marks the run `failed_orphaned` and leaves the tmp dir for human review. We never silently delete tmp dirs.

Retries are at the run level, not the step level. A failed run can be retried by submitting again; if the primitive is deterministic, the executor must be told `--force` because the cache would otherwise short-circuit. Retried runs are new run dirs with their own ULIDs; the failed run is preserved.

Concurrency on shared artifacts: two concurrent submissions for the same `input_hash` coalesce in the executor's in-memory submission table. The second submission attaches to the first run's completion future. Across processes, the on-disk lock during the commit window prevents two commits from racing for the same target dir; if both attempt, one wins the atomic rename and the other discovers the committed run on retry.

## 5. Provenance model

I extend T-07 rather than replace it. T-07 already captures the hard-won pieces (content-hashed envs, runtime binary version probes, input/output hashes, git SHA, atomic write). The extensions:

1. **Database versions.** For Kraken2, Bracken, sourmash, AMRFinderPlus, ABRicate, every database the tool consults gets a hash and a version string. AMRFinderPlus exposes `--database_version`; Kraken2 needs an explicit hash over the index files. These go in `provenance.databases: {name: {version, sha256}}`.
2. **Validity probes.** For AMRFinderPlus, the `-O` organism list is queried at runtime (`amrfinder --list_organisms`) and the requested organism is checked against it. The probe result is stored. A primitive that runs AMRFinderPlus with an organism not in the list either refuses to start or runs without `-O` and records the downgrade.
3. **Invariant outcomes.** Every invariant's name, declaration version, and outcome (with structured repair details if applicable) is written to `invariants.json` and summarized in `provenance.json`.
4. **Repair records.** When an invariant returns `Repaired`, the executor records what was repaired, what the original value was, and what the repaired value is. The repair record is part of the immutable run; users cannot acknowledge it away.
5. **Workflow caller.** For multi-primitive workflows, the Python function that orchestrated the call is recorded by qualified name plus git SHA. This lets a reproducer reconstruct not just the individual primitive but the composition.
6. **Schema version.** `provenance.json` carries `schema_version: "2.0"`. Old T-07 runs are `1.0`. The replay tool understands both.

A collaborator twelve months later reproduces a result as follows. They run `kapur replay /shared/projects/foo/runs/snp_distance/a2c4f1.../01J...`. The tool reads `provenance.json`, fetches `store/envs/<sha256>.yaml`, recreates the env (mamba/conda solve, with the same channels pinned by hash where possible; if the solve fails because a channel disappeared, the tool reports which packages drifted), fetches inputs by their SHA256s (resolving through `store/artifacts/` first, then by walking the original path), checks out the application repo at the recorded git SHA, runs the primitive, and compares outputs byte-for-byte for deterministic primitives or via a tolerance comparison for stochastic ones. Mismatches are reported with structured diffs.

The honest limit: full bit-reproducibility requires the conda solve to be reproducible, which is not guaranteed across years. We mitigate by storing the resolved env (post-solve, with builds) in addition to the abstract env file. If the resolved env's packages are still in the conda cache or in a mirror, replay works. If not, replay reports degraded reproducibility and lists missing packages. This is honest about a real failure mode.

## 6. Presentation model

FastAPI endpoints read from the filesystem (and the SQLite index for list and filter queries). The React UI consumes JSON. No endpoint executes a primitive synchronously; all execution goes through `executor.submit` which returns a run_id, and the UI polls or subscribes via server-sent events on the run's status file.

Run views are pure functions of the run directory. A view for a snp_distance run reads `provenance.json`, `invariants.json`, `outputs/distance_matrix.tsv`, and the PNGs in `outputs/`, and renders. Re-rendering does not re-execute. This is critical for the audit use case: when a USDA reviewer asks for the same report nine months later, the endpoint reads the committed run and produces the identical view.

Status indicators in the UI map directly to the on-disk `status` field. A `degraded` run is rendered with a prominent banner that lists the repair records. The current production behavior, where a corrupted distance matrix renders identically to a clean one, becomes impossible in the UI because the status is `degraded` and the banner is non-dismissible.

Exports (PDF reports for regulatory submissions, CSV exports for downstream tools) are generated by report-builder functions that consume committed runs. Reports include the run's `input_hash`, `run_id`, `git_sha`, and invariant summary in their footer so that a printed PDF is traceable back to a specific run directory. This is the audit-trail requirement made concrete.

The SQLite index has tables for `runs`, `artifacts`, `invariant_outcomes`, `databases`. It is updated by the executor on commit and rebuilt from filesystem on demand. Queries from React go through FastAPI through SQLite, never directly to the filesystem for list operations.

## 7. snp_analysis.py walkthrough

The current `run(group_dir, group_name, out_dir, snp_dists_path, scope)` signature reflects an ad-hoc input model: a directory of unspecified shape, a tool path passed in as a string, an output directory chosen by the caller. In the new architecture this becomes a Primitive with typed inputs:

```python
@primitive(
    name="snp_distance",
    version="2.0.0",
    stochastic=False,
    scope="panel",
    inputs={"alignment": MultiSampleAlignment},
    outputs={"matrix": DistanceMatrix, "kde": Plot, "neighbors": Plot},
    env=CondaEnvRef("snp_distance.yaml"),
    probes=[BinaryProbe("snp-dists"), PythonPkgProbe("matplotlib"), PythonPkgProbe("seaborn")],
)
class SnpDistance:
    @dataclass
    class Params:
        max_n_fraction: float = 0.1
        kde_bandwidth: float | None = None

    invariants = [
        NoNaN(target="matrix"),
        DimensionMatches(matrix="matrix", expected="inputs.alignment.n_samples"),
        Symmetric(target="matrix"),
        ZeroDiagonal(target="matrix"),
        TriangleInequalitySpotCheck(target="matrix", n_samples=200),
    ]

    def execute(self, ctx, alignment: Artifact) -> dict[str, Artifact]:
        matrix = run_snp_dists(alignment.path, ctx.scratch / "matrix.tsv")
        kde    = make_kde_plot(matrix, ctx.scratch / "kde.png")
        hist   = make_neighbor_hist(matrix, ctx.scratch / "neighbors.png")
        return {
            "matrix":    ctx.commit_artifact(matrix, type=DistanceMatrix),
            "kde":       ctx.commit_artifact(kde,    type=Plot),
            "neighbors": ctx.commit_artifact(hist,   type=Plot),
        }
```

The `MultiSampleAlignment` artifact type carries `n_samples` as part of its metadata (computed at creation time, verified on read). The primitive does not call `sanitize_matrix`. If NaN cells appear, they appear; the `NoNaN` invariant catches them.

Outcomes:

- All invariants pass: status `committed`, UI renders normally.
- NaN cells present and the user has opted in (via params) to a documented repair: the repair primitive runs explicitly, produces a separate `RepairedDistanceMatrix` artifact, and the run is committed with `Repaired` invariant outcome plus a structured record listing which pairs were imputed. Status is `degraded`. UI shows a banner. No silent corruption.
- NaN cells present and no repair opted in: status `failed`. The matrix is preserved in the run directory but marked degraded; downstream primitives that consume `DistanceMatrix` refuse to read a failed run's output, by executor-level check on the producer's status.

The architectural lesson generalizes. The NaN bug is one instance of a class: tools that silently transform their outputs to keep downstream code happy. The class is fixed by separating computation, invariant checking, and status assignment into three actors (primitive, invariant runner, executor) where the primitive cannot write status. Repairs must be explicit, declared, and produce their own artifact lineage. No "fix it and don't tell anyone" path exists.

The panel shape requires no special handling. `MultiSampleAlignment` is just an artifact type whose content is an N-sample FASTA. The input_hash incorporates the alignment's SHA256 (which depends on all N samples). If a user wants to add one sample and rerun, the alignment artifact changes, the input_hash changes, a new run is produced, and the old run is preserved.

## 8. Failure modes considered

**Session killed mid-run.** Detached jobs survive. Orphan tmp dirs are swept on executor restart. Already covered.

**Concurrent runs on shared artifacts.** Same `input_hash` requests coalesce in-process; cross-process commits race for the atomic rename, loser discovers committed and returns it. Cross-user submissions to the same `input_hash` (lab member and collaborator both running the same Kraken2 over the same reads) share the cache, which is intended behavior and saves CPU.

**Tool DB version drift.** Database hashes are part of `input_hash`. A Kraken2 run with DB v2024.10 and another with DB v2025.04 have different input_hashes even with identical reads. Both runs are kept. The UI groups them and surfaces the DB version per run.

**Corrupted intermediate silently consumed downstream.** Two layers of defense. First, the producer's invariants block commit on corruption. Second, downstream primitives declare the artifact types they accept and check both the type tag and the run-level status of the producing run. If a downstream tries to read from a `failed` or `degraded` run, it must opt in explicitly with a parameter that gets recorded in provenance. There is no implicit reading of failed outputs.

**Collaborator re-running a primitive after the original author.** For deterministic primitives, the rerun is a cache hit; the collaborator sees the original author's run. For stochastic primitives, the rerun produces a new run dir; both are kept and the UI shows both. The collaborator can compare them. The provenance for each records who ran it and when.

**Schema evolution over multi-year projects.** `provenance.json` and `manifest.json` carry `schema_version`. Readers handle all known versions via a migration registry of pure functions. Old runs are never rewritten. New invariants added to a primitive do not invalidate old runs; they apply only to runs whose primitive version is at or above the version that introduced the invariant. Primitive version bumps are explicit and recorded in the run.

A failure mode the brief did not mention but matters: **silent dependency upgrade in a shared conda env.** If `envs/snp_distance.yaml` is edited and re-solved, its content hash changes, the env ref in new runs changes, but old runs still reference the old hash and are reproducible. The reproducibility is dependent on the old env file remaining in `store/envs/`. We never garbage-collect `store/envs/` or `store/images/`. Disk cost is bounded and small relative to outputs.

## 9. Migration path

Order of operations, each step reversible at its boundary.

1. Ship the core library: `Artifact`, `Primitive`, `Run`, `Executor`, `Invariant`, plus the storage layout. No tools wrapped yet. Library is importable but optional. (One to two weeks.)
2. Wrap `snp_analysis.py` as the `SnpDistance` primitive. Run it in parallel with the existing module, comparing outputs. This is the highest-value first wrap because it fixes the known production NaN bug. Once wrapped, retire the old module. (One week.)
3. Wrap `vSNP3 step 1` (per-sample mapping) and `vSNP3 step 2` (panel alignment) as primitives. The JobManager pattern is preserved; the executor calls into it. Keep the existing FastAPI endpoints; add new ones that operate on runs. (Two to three weeks.)
4. Wrap AMRFinderPlus, MLST, SeqSero2, ABRicate. Decide per tool whether to keep Apptainer (default for NAHLN_AMR-shipped tools) or migrate to conda. The architecture supports both via `EnvRef`. (Two weeks.)
5. Wrap SPAdes, Shovill, ConFindr, CheckM, Kraken2/Bracken/Krona chain, sourmash. (Three to four weeks.)
6. Build the SQLite index and the replay tool. Index can be added at any point after step 3; replay tool needs full primitive coverage to be useful. (Two weeks.)
7. Migrate UI to consume the new run-based endpoints. Old endpoints stay until the UI is fully migrated, then deprecate. (Concurrent with steps 3 to 5.)

Reversibility: through step 2, the old code paths exist unchanged. Through step 5, each tool wrap is independently revertible. After step 5, the old vSNP3 endpoints can be removed; this is the first irreversible step.

Total elapsed: three to four months for one engineer working at a sustainable pace. The platform is useful at step 2.

## 10. Rationale and tradeoffs

**Invariants as executor-enforced postconditions.** Considered: leaving validation to each tool author (status quo), or adding a separate "quality flag" file that the primitive writes. Rejected because both leave the contradiction (the NaN-with-status-ok pattern) possible. The chosen design makes it structurally impossible. Wrong if invariants become so expensive that they dominate primitive runtime; not a real risk for the listed tools (matrix invariants are O(N²) on matrices that are already O(N²), and tool-specific invariants like `coverage >= 30x` are cheap reads of existing outputs).

**Filesystem-of-record plus SQLite index.** The team has chosen filesystem-of-record. I respect that. SQLite as a derived, rebuildable index is not a database backend in any meaningful sense; it is `find` with better ergonomics. The filesystem remains the source of truth, dump-able to tar, portable across machines. Wrong if the query patterns required by the UI become genuinely relational (joins across projects, complex aggregations); at that point we revisit, but at current scale, no.

**Conda over Apptainer as the long-term direction for lab-authored primitives.** Considered: standardize on Apptainer for everything. Rejected because Apptainer images are opaque content blobs from the analysis perspective; you cannot easily introspect what's in them. Conda env files are human-readable and content-hashable. NAHLN_AMR ships Apptainer images and the architecture accepts that; for lab-authored primitives, conda is the default. Wrong if conda solvability continues to degrade (bioconda has shown signs of this); fallback is pixi or uv-based equivalents, which fit the architecture because `EnvRef` is an abstraction.

**Python-internal composition over DSL.** The lab does not want Nextflow or Snakemake. I agree. At this scale (twelve to fifteen distinct primitives, simple linear or two-step compositions), a workflow DSL costs more than it earns. Workflows are plain Python functions that call primitives. Wrong if the lab needs to share pipelines as portable artifacts with non-Python institutions, but the existing collaboration model (NAHLN_AMR Nextflow pipeline distributed by USDA) handles that case at the institution boundary, not inside the lab.

**Hash-by-input deduplication with explicit stochasticity opt-in.** Considered: cache everything by input hash, or cache nothing. Rejected the first because stochastic tools (SPAdes, Shovill, RAxML bootstrap) give different outputs on re-run and pretending otherwise is a lie. Rejected the second because deterministic primitives (snp-dists, AMRFinderPlus) genuinely should cache and the CPU savings matter. The chosen design requires every primitive to declare `stochastic: bool` honestly and treats stochastic primitives as always-fresh by default. Wrong if a primitive is mislabeled deterministic when it has subtle nondeterminism (multithreading order effects, hash randomization). Mitigation: a periodic CI job re-runs deterministic primitives on fixed inputs and compares outputs; mismatches reclassify the primitive.

## Architect C design

## 1. Problem Statement

The Kapur Lab requires a production-grade, highly auditable phylogenomics and surveillance platform under restrictive constraints: no database backend, no external workflow domain-specific language (DSL), and an underlying execution environment (Open OnDemand) that aggressively reaps user sessions after 8 hours.

The core architectural tension lies between the requirement for **strict, multi-year reproducibility and auditability**(suitable for legal proceedings and USDA submissions) and a **stateless, filesystem-of-record deployment** operating on an interactive HPC node.

### Reframing the Brief

The brief's constraint of having "no database backend" while simultaneously demanding multi-user priority queueing, cross-sample panel analyses, and robust audit trails is a dangerous anti-pattern if approached naively. Attempting to manage state across concurrent users by simply reading and writing unstructured text files will inevitably lead to race conditions, partial writes, and broken provenance.

To make this architecture succeed without a relational database, we must reframe the filesystem itself. We cannot treat it as a passive storage dump. Instead, **the filesystem must be treated as a strongly typed, immutable event store and state machine.** Every sample, pipeline run, and panel analysis must be modeled as a content-addressed, append-only directory node. State transitions must be governed by atomic POSIX file operations.

Furthermore, trying to execute long-running bioinformatic jobs (like *SPAdes* or *vSNP3*) directly inside or child-detached from an OOD-managed FastAPI process is brittle. We will not use a generic "JobManager" that simply detaches threads; we will implement a decoupled, file-spooled, out-of-process execution engine driven by standard Linux process mechanics (`os.setsid`) that communicates exclusively via atomic state files on disk.

------

## 2. Core Abstractions

To enforce type safety and clear structural boundaries across the Python codebase without an ORM, we define four core primitives using standard Python `dataclasses`.

Python

```
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

class JobStatus(Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

@dataclass(frozen=True)
def Artifact:
    """An immutable data asset on disk, verified by content hashing."""
    path: Path
    sha256: str
    file_type: str  # e.g., "FASTQ", "FASTA", "BAM", "NEWICK"
    metadata: Dict[str, str] = field(default_factory=dict)

@dataclass(frozen=True)
def TaskContext:
    """The complete execution environment context required for perfect replay."""
    task_name: str
    version: str
    environment_hash: str  # SHA256 of Apptainer image or Conda lockfile
    exec_command: List[str]
    parameters: Dict[str, str]

@dataclass
class JobManifest:
    """The on-disk authority for an execution instance."""
    job_id: str
    status: JobStatus
    submitter: str
    project_id: str
    priority: int  # 0 = Standard, 1 = Lab Member Priority
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    inputs: Dict[str, str] = field(default_factory=dict)  # parameter_name -> SHA256
    outputs: Dict[str, str] = field(default_factory=dict) # output_key -> SHA256
    failure_reason: Optional[str] = None
```

### Component Architecture

- **`PipelineEngine`**: A pure Python execution DAG coordinator. It evaluates dependencies by inspecting the existence and validity of target `Artifact` provenance manifests on disk before invoking a task.
- **`SpoolExecutor`**: An out-of-process runner that monitors a localized `/shared/queue/` spool directory, picks up pending workloads based on user priority groups, and executes them in detached process groups.
- **`ProvenanceAuditor`**: An extension of the T-07 engine responsible for enforcing atomic state writes, environment capturing, and validation of outputs before marking jobs as complete.

------

## 3. Data Model

The filesystem layout acts as our database schema. It enforces isolation via POSIX permissions and structural consistency via mandatory naming conventions.

### Filesystem Layout

```
/shared/kapur-lab/
├── apps/                          # Central repository of locked environments
│   ├── apptainer/                 # Read-only .sif images (e.g., nahln_amr_v1.sif)
│   └── conda/                     # Explicit lockfiles (*.lock) instead of loose env.yamls
├── projects/
│   └── PROJ_2026_TB_OUTBREAK/     # Project Root (POSIX Group: lab_tb_sub)
│       ├── .project_manifest.json # Project metadata and authorized user lists
│       ├── raw/                   # Append-only raw sequence data (chmod 440)
│       │   ├── sample_A_R1.fastq.gz
│       │   └── sample_A_R2.fastq.gz
│       ├── samples/               # Per-sample analysis outputs
│       │   └── sample_A/
│       │       ├── kraken2/
│       │       ├── spades/
│       │       └── .sample_A.prov.json # Aggregated sample-level provenance
│       └── panels/                # Cross-sample analyses (vSNP3 Step 2, SNP distance)
│           └── PANEL_MAY_2026/
│               ├── alignment.fasta
│               ├── distance_matrix.csv
│               ├── stats.json
│               └── .PANEL_MAY_2026.prov.json
└── queue/                         # Global file-based job spool
    ├── pending/                   # Job tokens waiting for execution
    ├── running/                   # Active jobs
    └── completed/                 # Archived job manifests (last 30 days)
```

### State and Mutability Rules

- **User-Authoritative**: The `raw/` directory and `.project_manifest.json`. Users place files here. Once written, files are stripped of write permissions (`chmod 440`) via a background cron or post-upload hook to prevent accidental alteration.
- **Tool-Authoritative**: All data inside `samples/` and `panels/`. The web UI and users never write directly to these folders. Only the `SpoolExecutor` writing under the pipeline service account has write authorization.
- **Append-Only State Transitions**: Pipeline execution directories are generated with unique execution IDs (e.g., `spades_run_uuid/`). If a user re-runs a tool, a new directory is generated. The project state shifts by changing a symlink pointing to the "latest" successful execution directory.

------

## 4. Execution Model

To guarantee that long-running tasks like *SPAdes* (45 mins) and *vSNP3* (90 mins) survive the 8-hour OOD session timeout, we completely decouple the FastAPI web application from job execution.

### The Spool and Detach Flow

1. **Job Submission**: When a user clicks "Run Pipeline" in the React UI, FastAPI validates the inputs and generates a `JobManifest` formatted as a JSON file.
2. **Atomic Placement**: FastAPI writes this file atomically to `/shared/queue/pending/{job_id}.json`.
3. **The Out-of-Process Worker**: A persistent daemon (`vsnp_daemon.py`) runs directly on the Threadripper server, independent of OOD. It can be managed via a user-space systemd service (`systemd --user`) or an independent background loop running under a persistent utility session.
4. **Priority Evaluation**: The daemon polls `/shared/queue/pending/`. It reads the `submitter` field, checks if the user belongs to the primary lab POSIX group (`kapur_staff`), and prioritizes their tasks over external collaborators (`project_scoped_users`).
5. **Process Detachment**: When launching a tool, the daemon uses POSIX process decoupling:

Python

```
import os
import subprocess
import sys

def execute_detached_workflow(job_id: str, command: list, work_dir: Path):
    """Launches a pipeline process detached from the parent environment."""
    pid = os.fork()
    if pid > 0:
        # Parent returns immediately to the daemon loop
        return

    # Child process customization
    os.setsid()          # Create a new session, detaching from OOD TTY completely
    os.umask(0option)    # Enforce safe file creation permissions
    
    # Redirect standard file descriptors to disk logs
    log_out = open(work_dir / f"{job_id}.stdout", "a")
    log_err = open(work_dir / f"{job_id}.stderr", "a")
    os.dup2(log_out.fileno(), sys.stdout.fileno())
    os.dup2(log_err.fileno(), sys.stderr.fileno())

    # Execute the actual workload (Conda or Apptainer context wrapper)
    try:
        subprocess.run(command, cwd=work_dir, check=True)
        # Update state on completion
        mark_job_complete(job_id)
    except subprocess.CalledProcessError as e:
        mark_job_failed(job_id, error=str(e))
    finally:
        log_out.close()
        log_err.close()
        sys.exit(0)
```

### Locking Strategy

We eliminate race conditions across concurrent users via **Cooperative Lock Files** using the Linux `fcntl` system call. Before any tool executes a cross-sample step inside a panel directory, it must acquire an exclusive lock on `.lock` within that specific panel directory. If a lock is held, concurrent runs will queue or fail explicitly rather than writing overlapping data.

------

## 5. Provenance Model

We will build directly on top of the T-07 baseline. To ensure the platform functions seamlessly as a multi-node system in the future, we must eliminate loose environment assumptions.

### The Long-Term Environment Plan: Strict Apptainer Migration

We dictate that **all toolsets must migrate to Apptainer images**. Conda environments—even with content-hashed YAML files—suffer from long-term dependency drift due to underlying glibc shifts on the host operating system and upstream channel deletions.

An Apptainer `.sif` file provides a bit-reproducible environment that remains constant across host OS upgrades and multi-node cluster migrations. For tools currently running in Conda, we will generate static Apptainer containers using explicit Conda lockfiles during the migration phase.

### Extended Provenance Schema (`.prov.json`)

Every artifact folder will contain a `.prov.json` sidecar file written atomically using the `tempfile.mkstemp()` + `os.replace()`pattern.

JSON

```
{
  "provenance_format_version": "2.0.0",
  "timestamp": "2026-05-16T20:46:00Z",
  "executor": {
    "user": "jdoe",
    "host": "psu-threadripper-01.local",
    "platform": "Linux-6.1.0-21-amd64-x86_64",
    "git_sha": "d3b07384d113edec49eaa6238ad5ff00213d2134",
    "git_dirty": false
  },
  "environment": {
    "execution_type": "apptainer",
    "container_image_path": "/shared/apps/apptainer/nahln_amr_v1.sif",
    "container_sha256": "8f3c960a33c023d8cbb81f727cd8e3a241e3d32bb58231c6a2d184061245b7ee",
    "runtime_versions": {
      "python": "3.11.5",
      "amrfinder_binary": "3.12.8",
      "amrfinder_db_version": "2026-04-01.1"
    }
  },
  "inputs": [
    {
      "logical_name": "forward_reads",
      "path": "/shared/projects/PROJ_2026_TB/raw/sample_1_R1.fastq.gz",
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
  ],
  "outputs": [
    {
      "logical_name": "assembly_fasta",
      "path": "/shared/projects/PROJ_2026_TB/samples/sample_1/spades/contigs.fasta",
      "sha256": "4a8a08f09d37b73795649038408b5f33072b24f5450e118c4e402e9b1d3d154a"
    }
  ],
  "parameters": {
    "spades_mode": "--isolate",
    "cov_cutoff": "auto"
  }
}
```

### Verification 12 Months Later

To reproduce a result exactly, a collaborator executes a platform CLI replay command pointing directly to the target `.prov.json`:

```
vsnp-cli replay --manifest /path/to/.prov.json
```

The platform reads the `container_sha256`, pulls or verifies the local `.sif` image, confirms that the input files match the noted `sha256` keys, and re-runs the exact `parameters` array. This completely guarantees reproducible outputs down to the bit level.

------

## 6. Presentation Model

Because FastAPI has no relational database to query for the UI, **the presentation layer treats the filesystem as a high-performance read-only cache.**

### Stateless Discovery and Caching

When the React frontend requests the status of a project, FastAPI does not perform heavy recursive walks of the disk. It reads the project directory's flat `.project_manifest.json` and parses any active job manifests in `/shared/queue/` tagged with that `project_id`.

To render pipeline results (e.g., an AMRFinderPlus table or a Krona taxonomic chart) without re-executing data logic:

1. FastAPI checks if the target tool output directory exists and contains a valid `.prov.json` file.
2. It verifies that the inputs noted in that `.prov.json` match the current hashes of the project's raw source data.
3. If verified, FastAPI reads the static asset (e.g., parsing `stats.json` or serving `krona.html` as a static file response). The UI displays a green checkmark indicating **"Cached (Provenance Verified)"**.

```
[React UI] ──(HTTP GET /project/1/results)──> [FastAPI Backend]
                                                     │
                                            (Check .prov.json)
                                                     │
                                    ┌────────────────┴────────────────┐
                             [Hashes Match]                    [Hashes Differ]
                                    │                                 │
                        (Serve Static Files Directly)       (Flag "Out of Date" in UI)
```

------

## 7. `snp_analysis.py` Walkthrough

The legacy implementation of `snp_analysis.py` contains severe architectural risks: it does not perform atomic writes, uses an isolated schema, logs a successful status when data corruption occurs, and breaks the single-sample isolation pattern.

### Refactoring to the Platform Architecture

We will wrap the execution of the panel-level SNP distance computation in a strict contract called `PanelTask`. We address the NaN data corruption bug by enforcing pre-write data validation schemas using a strict structural assertion step. If a NaN is found, the task fails immediately.

Here is the refactored, robust, production-ready integration pattern for the core cross-sample logic:

Python

```
import json
import os
import tempfile
from pathlib import Path
import pandas as pd
import numpy as np

class ValidationError(Exception): """Raised when data integrity checks fail."""

class SNPDistanceAnalysisTask:
    def __init__(self, group_dir: Path, group_name: str, out_dir: Path, snp_dists_path: Path):
        self.group_dir = group_dir
        self.group_name = group_name
        self.out_dir = out_dir
        self.snp_dists_path = snp_dists_path

    def run_pipeline_step(self):
        """Executes the calculation, validates data, and performs atomic writes."""
        # 1. Setup working paths
        self.out_dir.mkdir(parents=True, exist_ok=True)
        raw_matrix_output = self.out_dir / f"{self.group_name}_matrix.raw.tsv"
        
        # Execute the underlying binary tool via bounded subprocess
        # (Simulating snp-dists tool execution on the multi-sample FASTA)
        self._execute_snp_dists(raw_matrix_output)

        # 2. Consume and Validate Data Matrix
        df = pd.read_csv(raw_matrix_output, sep="\t", index_col=0)
        
        # CRITICAL FIX: Direct interception of the NaN class of failure
        if df.isna().values.any():
            raise ValidationError(
                f"Data corruption detected: Distance matrix contains NaN values. "
                f"Aborting execution to protect downstream visualizers."
            )

        # 3. Structural Processing
        processed_stats = self._calculate_metrics(df)

        # 4. Atomic Writes using standard platform primitives
        self._atomic_write_json(self.out_dir / "stats.json", processed_stats)
        self._generate_plots(df)

    def _execute_snp_dists(self, target_path: Path):
        # Wraps execution of the binary under the provenance tracker...
        pass

    def _calculate_metrics(self, df: pd.DataFrame) -> dict:
        """Produces standardized panel metrics matching platform expectations."""
        return {
            "schema_version": "2.0.0",
            "group_name": self.group_name,
            "sample_count": len(df.columns),
            "mean_distance": float(np.mean(df.values)),
            "status": "COMPLETED"
        }

    def _atomic_write_json(self, target_path: Path, data: dict):
        """Enforces safe POSIX replacement via mkstemp to eliminate partial writes."""
        dir_name = target_path.parent
        fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
            os.replace(temp_path, target_path)
            os.chmod(target_path, 0o440) # Lock permission on completion
        except Exception as e:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise e

    def _generate_plots(self, df: pd.DataFrame):
        # Generates downstream KDE and closest-neighbor plots safely...
        pass
```

------

## 8. Failure Modes Considered

### Session Killed Mid-Run

- **The Risk**: An OOD window closes or hits its 8-hour limit while SPAdes is actively running.
- **Mitigation**: The architecture handles this natively. Because FastAPI merely spools a manifest to disk and the `vsnp_daemon.py` forks the process into a completely new POSIX session (`os.setsid`), killing the OOD user session or parent HTTP processes leaves the detached child running safely on the Threadripper compute core. The task updates its on-disk manifest independently when finished.

### Concurrent Runs on Shared Artifacts

- **The Risk**: Two lab members trigger a panel-level tree generation simultaneously for the exact same outbreak cohort, causing corrupt or interleaved file writes.
- **Mitigation**: The execution loop attempts to acquire an exclusive lock file (`.lock`) in the target panel directory using `fcntl.flock`. If lock acquisition fails, the second job is paused and re-queued by the spooler with an informational status shown in the frontend.

### Tool DB Version Drift

- **The Risk**: AMRFinderPlus or Kraken2 updates its internal database reference on disk. A sample run from today uses a different database version than a sample run from last week, silently skewing comparative results.
- **Mitigation**: The platform includes the database directory's absolute structural path and its contents' hash metadata directly within the validation step of `.prov.json`. If a database update occurs, the validation layer flags the old runs as having an outdated reference context, prompting the user to upgrade or frozen-pin the tool variant.

### Corrupted Intermediate Output Silently Consumed Downstream

- **The Risk**: A tool fails silently by producing a truncated or empty FASTA file, which downstream metrics steps consume without throwing an error.
- **Mitigation**: The `PipelineEngine` implements strict post-task validation assertions. Every registered task definition specifies validation rules (e.g., `Output file must be > 0 bytes` and `Fasta parsing must find valid structural headers`). If a validation check fails, the pipeline immediately stops execution, moves the directory to an isolated error space, and flags the manifest as `JobStatus.FAILED`.

### Schema Evolution Over Multi-Year Projects

- **The Risk**: The structure of `stats.json` or the provenance manifest changes in 2027, breaking the UI parsing logic for historical runs generated in 2026.
- **Mitigation**: All structured data payloads written to disk enforce a top-level explicit `schema_version` property. The FastAPI presentation serializers use this key to run backward-compatible data adapters on the fly, ensuring smooth rendering for legacy files.

------

## 9. Migration Path

We move the lab from its scattered environment to this structured architecture via a phased execution sequence, designed to minimize disruption to active outbreak surveillance workflows.

### Order of Operations

```
Phase 1: Foundation (Reversible)
 └── Establish /shared/queue/ structure
 └── Deploy decoupled vsnp_daemon.py execution daemon
 └── Wrap existing FastAPI layer to write manifests instead of spawning direct subprocesses

Phase 2: Provenance Enforcement (Reversible)
 └── Upgrade T-07 to the strict .prov.json sidecar format
 └── Enforce automated chmod 440 execution on completed outputs

Phase 3: Codebase Hardening (Breaking Change)
 └── Refactor snp_analysis.py into the explicit validation structure
 └── Deprecate the legacy direct /backend/app/posthoc path executions

Phase 4: Container Lock-In (Finalization)
 └── Convert active Conda environments to immutable Apptainer images (.sif)
 └── Lock the pipeline definitions to block untracked tool paths
```

### Rollback Strategy

Because the system stores state strictly as flat files on the disk, rollback is simple. If Phase 3 introduces an unexpected error, we can restore the legacy pipeline mechanics by pointing the FastAPI routes back to the historical execution scripts, leaving the created analysis folders completely intact as immutable text assets.

------

## 10. Rationale and Tradeoffs

### Decision 1: Sticking to the Filesystem-of-Record over an RDBMS

- **The Alternative**: Introducing a standard PostgreSQL database backend.
- **Why Rejected**: A database introduces a distinct service dependency that must be maintained, backed up, and kept in sync with the physical files on disk. If a researcher manually cleans up or deletes an old analysis folder via the Linux shell, an RDBMS quickly drifts out of sync with reality, leading to broken references. By keeping the filesystem as our single source of truth, moving or backing up a project directory automatically keeps its state metadata perfectly intact.
- **When I Would Be Wrong**: If the lab scales to hundreds of thousands of samples, scanning the filesystem for status monitoring will cause heavy I/O bottlenecks. At that scale, an indexing database layer would become necessary.

### Decision 2: Choosing Apptainer Over Conda Environments

- **The Alternative**: Standardizing completely on explicit Conda lockfiles.
- **Why Rejected**: Conda does not isolate host system level libraries like `glibc`. An OS security update on the host Threadripper node can alter binary performance or break execution for historical Conda packages. Apptainer containers isolate the entire operating system space down to the binary level, guaranteeing long-term execution stability for legal or regulatory validation.
- **When I Would Be Wrong**: If the lab regularly needs to test rapid parameter changes across minor tool variants on a daily basis, compiling or modifying Apptainer images introduces extra overhead compared to running a quick `conda install`.

### Decision 3: Custom Python Spool Engine Over an External DSL (Nextflow/Snakemake)

- **The Alternative**: Adopting Nextflow to handle process executions.
- **Why Rejected**: The lab explicitly ruled out maintaining team expertise for workflow DSLs. A pipeline framework like Nextflow relies on its own background runtimes and complex Java environments, adding complexity to an OOD setup. Building a custom, clean Python spooler keeps the entire software stack highly readable for graduate students and developers working directly within standard Python.
- **When I Would Be Wrong**: If the lab expands its infrastructure from the single Threadripper server into a massive distributed Slurm cluster with complex multi-stage job interdependencies, our lightweight file spooler would require custom scheduling code that Nextflow handles natively out of the box.

## Architect A

## 1. Problem statement

The problem is not “build a web UI that launches bioinformatics commands.” The correct framing is: **build a forensic analysis record system that happens to execute bioinformatics workflows**. The lab’s outputs feed surveillance, regulatory submissions, legal proceedings, and publications, so reproducibility, access control, provenance, and failure visibility are primary requirements, not operational polish. The brief’s required tools, OOD/FastAPI/React deployment, filesystem-of-record constraint, no workflow DSL constraint, and existing T-07 provenance baseline are the governing requirements.

I would push back on one framing: “workflow” is too often treated as synonymous with “per-sample pipeline.” That is wrong here. vSNP3 has per-sample and panel-level phases, Kraken/Bracken/Krona is one logical chain, sourmash has reusable sketches and DB-dependent searches, and `snp_analysis.py` is inherently panel-level. The platform must model **sample artifacts**, **panel artifacts**, and **project artifacts** as first-class citizens.

The other key reframing: no output should be consumed merely because a path exists. Outputs become consumable only after validation, provenance capture, and atomic commit.

------

## 2. Core abstractions

The platform should have five core modules:

```text
backend/app/platform/
  artifacts.py      # immutable artifact manifests and hashes
  primitives.py     # typed executable analysis units
  executor.py       # detached execution, locking, retries
  provenance.py     # T-07-compatible provenance writer, extended
  presentation.py   # UI/report summaries, not raw tool parsing
```

The central abstraction is a **Primitive**, not a workflow DSL. A primitive is a typed Python object with declared inputs, outputs, runtime, validators, and presentation.

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Mapping, Sequence

ScopeKind = Literal["sample", "panel", "project"]
Severity = Literal["ok", "warning", "fatal"]

@dataclass(frozen=True)
class Scope:
    kind: ScopeKind
    project_id: str
    sample_ids: tuple[str, ...] = ()
    panel_id: str | None = None

@dataclass(frozen=True)
class ArtifactRef:
    artifact_id: str
    role: str                  # "reads", "assembly", "vsnp_alignment", "distance_matrix"
    path: Path
    sha256: str
    media_type: str
    schema: str | None = None

@dataclass(frozen=True)
class RuntimeSpec:
    kind: Literal["conda", "apptainer", "host-python"]
    name: str
    content_sha256: str        # env yaml hash or SIF hash
    binary_probes: tuple[str, ...]

@dataclass(frozen=True)
class PrimitiveSpec:
    name: str
    version: str
    scope_kind: ScopeKind
    input_roles: tuple[str, ...]
    output_roles: tuple[str, ...]
    cacheable: bool

@dataclass
class ValidationFinding:
    code: str
    severity: Severity
    message: str
    metrics: Mapping[str, int | float | str]

@dataclass
class PrimitiveResult:
    status: Literal["success", "warning", "failed"]
    outputs: tuple[ArtifactRef, ...]
    findings: tuple[ValidationFinding, ...]
    presentation_path: Path

class Primitive(Protocol):
    spec: PrimitiveSpec
    runtime: RuntimeSpec

    def plan(self, scope: Scope, inputs: Sequence[ArtifactRef],
             params: Mapping[str, object]) -> Mapping[str, object]: ...

    def execute(self, work_dir: Path, inputs: Sequence[ArtifactRef],
                params: Mapping[str, object]) -> PrimitiveResult: ...

    def validate(self, result: PrimitiveResult) -> tuple[ValidationFinding, ...]: ...
```

A **Workflow** is just a Python dependency graph of primitives:

```python
@dataclass(frozen=True)
class Edge:
    from_role: str
    to_primitive: str
    to_role: str

@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    version: str
    primitives: tuple[PrimitiveSpec, ...]
    edges: tuple[Edge, ...]
```

This supports all required tools:

- `vSNP3SampleMappingPrimitive` → sample scope.
- `vSNP3PanelAlignmentPrimitive` → panel scope.
- `SnpDistancePrimitive` → panel scope.
- `AssemblyPrimitive(tool="spades" | "shovill")` → sample scope.
- `AMRFinderPrimitive` → sample scope, assembly input.
- `KrakenBrackenKronaWorkflow` → three primitives but one UI workflow.
- `SourmashSketchPrimitive` and `SourmashGatherPrimitive` → sketch is reusable; gather depends on DB artifact.
- `MLSTPrimitive`, `SeqSero2Primitive`, `ABRicatePrimitive` → sample scope, assembly input.
- `ConFindrPrimitive`, `CheckMPrimitive` → sample or assembly scope as appropriate.

------

## 3. Data model

Filesystem layout:

```text
/shared/
  projects/
    PRJ-00042/
      project.json                 # mutable, user-authoritative
      acl.json                     # mutable, mirrors POSIX group intent
      samples/
        SAMPLE-001/
          sample.json              # mutable metadata, versioned by history
          inputs/
            reads.R1.fastq.gz -> /shared/artifacts/sha256/ab/cd...
            reads.R2.fastq.gz -> /shared/artifacts/sha256/ef/12...
      panels/
        PANEL-2026-05-MBOVIS/
          panel.json               # sample membership, mutable until sealed
      runs/
        RUN-20260517-abc123/
          request.json             # immutable after submit
          status.json              # mutable, atomic updates
          events.jsonl             # append-only
          attempts/
            1/
              work/                # scratch copy or symlink map
              stdout.txt
              stderr.txt
              provenance.json
              result.json
              presentation.json
              outputs/
      indexes/
        artifacts.by_role.json     # derived, rebuildable
        latest.by_sample.json      # derived, rebuildable

  artifacts/
    sha256/
      ab/cd/<hash>/data
      ab/cd/<hash>/manifest.json

  envs/
    conda/<env_yaml_sha256>/environment.yml
    apptainer/<sif_sha256>/image.sif
    tooldb/<tool>/<db_sha256>/manifest.json
```

**Immutable:** raw inputs, committed outputs, provenance, attempt directories, environment records, DB records.

**Mutable but derived:** `status.json`, indexes, `latest` pointers.

**Mutable and user-authoritative:** `project.json`, `sample.json`, `panel.json` until a run snapshots them.

A run request freezes user intent:

```json
{
  "schema": "kapur.run_request.v1",
  "run_id": "RUN-20260517-abc123",
  "actor": {"uid": "jdoe", "groups": ["kapur_lab", "prj_00042"]},
  "project_id": "PRJ-00042",
  "scope": {
    "kind": "panel",
    "panel_id": "PANEL-2026-05-MBOVIS",
    "sample_ids": ["SAMPLE-001", "SAMPLE-002"]
  },
  "workflow": {"name": "vsnp3_plus_snp_distance", "version": "1.4.0"},
  "params": {"reference": "Mbovis_AF2122_97", "tree_tool": "iqtree"},
  "submitted_at": "2026-05-17T00:00:00Z",
  "priority_class": "lab_member"
}
```

The important rule: downstream tools receive `ArtifactRef`s, not arbitrary paths. A path alone is not a valid input.

------

## 4. Execution model

FastAPI submits a `request.json` into a filesystem queue. OOD sessions may die; execution belongs to the detached JobManager, not the web process.

Queue layout:

```text
/shared/queue/
  ready/high/<timestamp>.<run_id>.json
  ready/normal/<timestamp>.<run_id>.json
  claimed/<hostname>.<pid>.<run_id>.json
```

Workers claim jobs with atomic rename. Lab members go to `high`; collaborators go to `normal`. Use weighted fair scheduling so collaborators are slower under contention but not starved.

Locks are held at two levels:

```text
/shared/locks/run/<run_id>.lockdir
/shared/locks/artifact/<cache_key>.lockdir
```

Locks are acquired by atomic `mkdir`, with heartbeat files. A stale heartbeat permits recovery, but only after confirming the old process is gone.

Execution sequence:

1. Resolve workflow graph in Python.
2. Resolve each dependency to an `ArtifactRef`.
3. Compute cache key:

```text
sha256(
  primitive_name +
  primitive_version +
  input_artifact_ids +
  params_json +
  runtime_sha256 +
  db_or_reference_sha256
)
```

1. If a committed artifact with that cache key exists and validators passed, return cached result.
2. Otherwise run in `/scratch/<run_id>/<attempt>/`.
3. Write all outputs to scratch first.
4. Validate outputs.
5. Capture provenance.
6. Atomically commit result directory under `/shared/projects/.../runs/.../attempts/N/`.
7. Update `status.json` atomically.

Retries never overwrite. Attempt 1 may fail validation; attempt 2 is a new directory. A retry can reuse valid upstream artifacts but cannot silently reuse failed artifacts.

------

## 5. Provenance model

T-07 should be extended, not replaced. The existing baseline already captures content-hashed conda env YAML, pip freeze hash, binary versions, Python/platform/hostname, input/output SHA256s, git SHA, dirty flag, patches, and atomic JSON writes. That is the floor.

Every primitive run captures:

```json
{
  "schema": "kapur.provenance.v2",
  "run_id": "RUN-20260517-abc123",
  "attempt": 1,
  "primitive": {"name": "amrfinderplus", "version": "2.0.0"},
  "workflow": {"name": "amr_from_assembly", "version": "1.1.0"},
  "scope": {"kind": "sample", "project_id": "PRJ-00042", "sample_ids": ["SAMPLE-001"]},
  "actor": {"uid": "jdoe", "groups": ["kapur_lab", "prj_00042"]},
  "runtime": {
    "kind": "conda",
    "env_yaml_sha256": "...",
    "pip_freeze_sha256": "...",
    "python": "...",
    "platform": "...",
    "hostname": "..."
  },
  "code": {
    "git_sha": "...",
    "dirty": false,
    "patches_sha256": "..."
  },
  "command": {
    "argv": ["amrfinder", "-n", "assembly.fasta", "-O", "Mycobacterium_tuberculosis_complex"],
    "cwd": "/scratch/RUN-.../attempt-1",
    "environment_allowlist": {"LC_ALL": "C"}
  },
  "tool_probes": {
    "amrfinderplus_version": "...",
    "valid_organisms_sha256": "...",
    "valid_organisms_path": "/shared/tooldb/amrfinder/.../valid_organisms.json"
  },
  "databases": [
    {"tool": "amrfinderplus", "db_sha256": "...", "db_label": "resolved-at-submit"}
  ],
  "inputs": [
    {"role": "assembly", "artifact_id": "...", "sha256": "..."}
  ],
  "outputs": [
    {"role": "amr_table", "artifact_id": "...", "sha256": "..."}
  ],
  "validators": [
    {"code": "amrfinder_organism_valid", "severity": "ok"}
  ]
}
```

A collaborator 12 months later reproduces by:

1. Reading `provenance.json`.
2. Verifying project access by POSIX group.
3. Restoring the recorded conda YAML or Apptainer SIF by content hash.
4. Checking out the recorded git SHA and applying patches if any.
5. Binding the recorded input, reference, and DB artifacts by SHA256.
6. Running the recorded `argv`.
7. Comparing output hashes and validator results.

For stochastic tools like SPAdes/Shovill, the record is still complete. If the tool supports a seed, the primitive must set and record it. If exact byte reproduction is not possible, the provenance must say `determinism: "not_guaranteed"` and explain why. That is acceptable; pretending deterministic reproduction exists is not.

------

## 6. Presentation model

React must not parse arbitrary tool directories. FastAPI exposes normalized presentation documents:

```json
{
  "schema": "kapur.presentation.v1",
  "run_id": "RUN-20260517-abc123",
  "status": "warning",
  "title": "SNP distance analysis",
  "scope": {"kind": "panel", "panel_id": "PANEL-2026-05-MBOVIS"},
  "summary": [
    {"label": "Samples", "value": 64},
    {"label": "Closest pair distance", "value": 0}
  ],
  "warnings": [
    {"code": "low_variant_count", "message": "KDE may be uninformative"}
  ],
  "artifacts": [
    {"role": "distance_matrix", "label": "Pairwise SNP distances", "href": "..."},
    {"role": "kde_plot", "label": "KDE plot", "href": "..."}
  ]
}
```

Rendering is read-only. Cached results already have `presentation.json`, plots, tables, Krona HTML, and exports. Reopening a result does not re-execute the tool. Reports are generated from committed artifacts and provenance, not from scratch directories.

The UI shows four states: `queued`, `running`, `failed_validation`, `failed_execution`, `success/warning`. A warning is visible and exportable; it is not hidden in logs.

------

## 7. `snp_analysis.py` walkthrough

The current `snp_analysis.py` is a **panel primitive**. It should not be decomposed per sample. Its input is the vSNP3 multi-sample concatenated FASTA; its outputs are the distance matrix, KDE plot, closest-neighbor histogram, unified result JSON, presentation JSON, and provenance. The brief states its current signature and bug clearly: `run(group_dir, group_name, out_dir, snp_dists_path, scope)`, one group-level matrix/plot/histogram, NaN-filled distances reported as `"ok"`, ad-hoc `stats.json`, and non-atomic writes.

The successor:

```python
class SnpDistancePrimitive:
    spec = PrimitiveSpec(
        name="snp_distance_analysis",
        version="1.0.0",
        scope_kind="panel",
        input_roles=("vsnp_concatenated_fasta",),
        output_roles=("distance_matrix", "kde_plot", "nearest_neighbor_histogram"),
        cacheable=True,
    )

    runtime = RuntimeSpec(
        kind="conda",
        name="vsnp-posthoc",
        content_sha256="<env-yaml-sha>",
        binary_probes=("snp-dists --version",),
    )

    def execute(self, work_dir, inputs, params):
        alignment = require_role(inputs, "vsnp_concatenated_fasta")
        matrix_path = run_snp_dists(alignment.path, work_dir / "distances.tsv")
        matrix = read_distance_matrix(matrix_path)

        findings = validate_distance_matrix(matrix)
        if any(f.severity == "fatal" for f in findings):
            return failed_result(findings)

        kde = write_kde_plot(matrix, work_dir / "kde.png")
        hist = write_neighbor_histogram(matrix, work_dir / "closest_neighbor.png")

        return success_result(
            outputs=(artifact(matrix_path), artifact(kde), artifact(hist)),
            findings=findings,
            presentation=build_presentation(...)
        )
```

The legacy function remains only as an adapter:

```python
def run(group_dir, group_name, out_dir, snp_dists_path, scope):
    primitive = SnpDistancePrimitive()
    inputs = legacy_resolve_vsnp_alignment(group_dir, group_name, scope)
    request = legacy_request(scope=to_panel_scope(scope), out_dir=out_dir)
    return PrimitiveExecutor.run_inline_for_legacy(
        primitive=primitive,
        request=request,
        inputs=inputs,
        params={"snp_dists_path": str(snp_dists_path)}
    )
```

The NaN bug is fixed architecturally by banning silent scientific repair:

```python
def validate_distance_matrix(matrix) -> tuple[ValidationFinding, ...]:
    findings = []
    nan_count = count_nan(matrix)
    asymmetry_count = count_asymmetry(matrix)
    diagonal_bad = count_nonzero_diagonal(matrix)

    if nan_count:
        findings.append(ValidationFinding(
            code="distance_matrix_nan",
            severity="fatal",
            message="Distance matrix contains NaN values; plots were not generated.",
            metrics={"nan_count": nan_count}
        ))

    if asymmetry_count:
        findings.append(ValidationFinding(
            code="distance_matrix_asymmetric",
            severity="fatal",
            message="Pairwise matrix is not symmetric.",
            metrics={"asymmetric_cells": asymmetry_count}
        ))

    if diagonal_bad:
        findings.append(ValidationFinding(
            code="distance_matrix_bad_diagonal",
            severity="fatal",
            message="Self-distances must be zero.",
            metrics={"bad_diagonal_cells": diagonal_bad}
        ))

    return tuple(findings) or (
        ValidationFinding("distance_matrix_valid", "ok", "Distance matrix passed validation.", {}),
    )
```

If the lab later wants imputation, it must be an explicit primitive named something like `DistanceMatrixImputationPrimitive`, with original matrix hash, repaired matrix hash, imputation policy, and a visible warning. The default panel analysis must fail closed.

All JSON writes use the T-07 atomic pattern: write temp file in the same directory, `fsync`, then `os.replace`.

------

## 8. Failure modes considered

**Session killed mid-run:** OOD death does not matter. The detached JobManager owns the process. UI reconnects by reading `status.json` and `events.jsonl`.

**Worker killed mid-run:** No output is committed from scratch. The heartbeat goes stale, the run becomes retryable, and a new attempt is created.

**Concurrent runs on shared artifacts:** Cache-key lock prevents duplicate writers. Atomic commit prevents half-written artifacts. Existing successful artifacts are immutable.

**Tool DB version drift:** “latest” DB is resolved at submission time to a content hash. AMRFinderPlus stores the valid organism list hash for that exact binary/DB pairing; the UI cannot submit an invalid `-O` value.

**Corrupted intermediate silently consumed:** Downstream primitives accept only artifacts whose manifests show required validators passed. They do not accept raw paths.

**Collaborator re-running after original author:** The rerun creates a new run under the collaborator’s UID but references the original input artifacts and provenance. Original outputs remain immutable.

**Schema evolution:** Every JSON file has a schema name and version. Readers support adapters from old schemas; writers emit only the current schema. Old project archives remain readable.

------

## 9. Migration path

First, keep T-07 and extract its atomic writer, hashing, environment capture, git capture, and binary probing into `platform/provenance.py`. No workflow behavior changes yet.

Second, introduce `ArtifactRef`, `PrimitiveSpec`, `RunStore`, and normalized `result.json`/`presentation.json`. Wrap existing vSNP3 steps without changing their scientific behavior.

Third, port `snp_analysis.py`. This ships early because it fixes an active production bug and proves panel-level primitives.

Fourth, add the tool registry: conda runtimes, Apptainer runtimes, reference artifacts, and tool DB artifacts. AMRFinderPlus should be first because its organism mode is a known foot-gun.

Fifth, onboard SPAdes/Shovill, MLST, SeqSero2, ABRicate, ConFindr, CheckM, Kraken/Bracken/Krona, and sourmash. Each gets validators and presentation schemas.

Sixth, add cache indexes and multi-node workers. This is deliberately last; correctness comes before throughput.

Everything is reversible through the adapter layer: old scripts can still call legacy functions while new runs produce platform manifests.

------

## 10. Rationale and tradeoffs

**1. Filesystem ledger over database.**
I would normally prefer a small database for queryability, but the lab chose filesystem-of-record. The correct compromise is append-only run directories, immutable artifact manifests, atomic writes, and rebuildable indexes. I would be wrong if the lab grows to hundreds of users or needs complex cross-project search.

**2. Python primitives over a workflow DSL.**
A DSL would solve some scheduling problems but create a new competence requirement. Typed Python primitives are enough and keep debugging local. I would be wrong if workflows become large enough that visual graph management matters more than Python maintainability.

**3. Panel scope is first-class.**
Forcing panel analyses into per-sample jobs would corrupt the model. vSNP3 panel alignment and SNP distance analysis are group computations. I would be wrong only if the lab stops doing comparative genomics, which is not the case.

**4. Hybrid runtime now; Apptainer for stable external tools long-term.**
Conda is fine for development and Python-native steps. For regulatory-grade reproducibility, stable third-party tools should move toward Apptainer images built from locked recipes and stored by SIF hash. NAHLN_AMR-derived MLST/SeqSero2/ABRicate should stay Apptainer unless conda demonstrably reduces maintenance without weakening provenance.

**5. Validation gates before presentation.**
The platform must fail closed on invalid scientific data. The current NaN behavior is exactly the failure class this architecture prevents. I would be wrong only where the science explicitly endorses a repair policy; then repair becomes a named, provenance-bearing primitive, not a hidden sanitize step.