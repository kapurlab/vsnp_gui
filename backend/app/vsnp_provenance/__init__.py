"""
vsnp_provenance
===============

Reader library for vsnp_gui run provenance metadata (T-07).

Loads `run_metadata.json`, `dispatch_metadata.json`, and pipeline-run records;
handles schema version dispatch; provides typed access and basic diff/query
helpers.

Pairs with `vsnp_provenance.index` (SQLite indexer + janitor) and
`backend/app/provenance_writer.py` (the writer the JobManager calls). All
three modules use the same pydantic v2 models defined here as the
single-source-of-truth schema.

Design notes
------------
- Reader-only. Writing is the backend's job; this module is for analysts,
  reviewers, and the cross-project indexer to consume what's been written.
- Pydantic v2 for validation. Unknown fields are preserved (extra='allow') so
  reading a forward-compatible record on an old reader does not crash.
- Schema dispatch is explicit. Each supported schema_version maps to a model.
  Unsupported versions raise UnsupportedSchemaVersion with guidance.
- No I/O assumptions beyond the local filesystem. The SQLite cross-project
  index lives in a sibling module (`vsnp_provenance.index`).

Usage
-----
    from vsnp_provenance import load, load_pipeline_run, diff_dispatch_vs_final

    rec = load("step2/run_metadata.json")
    print(rec.run_id, rec.status, rec.reference.folder_manifest_sha256)

    pr = load_pipeline_run("_provenance/pipeline_runs/<uuid>.json")
    for s1 in pr.step1_runs:
        print(s1.sample, s1.run_id, s1.status)

    drift = diff_dispatch_vs_final(
        "step2/dispatch_metadata.json",
        "step2/run_metadata.json",
    )
    if drift:
        print("State changed between dispatch and finalize:", drift)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

CURRENT_SCHEMA_VERSION = 2
SUPPORTED_SCHEMA_VERSIONS = {2}

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProvenanceError(Exception):
    """Base for all provenance reader errors."""


class UnsupportedSchemaVersion(ProvenanceError):
    """Schema version is outside what this reader knows how to parse."""

    def __init__(self, found: int, supported: set[int]):
        self.found = found
        self.supported = supported
        super().__init__(
            f"schema_version={found} not supported by this reader "
            f"(supports {sorted(supported)}). Upgrade vsnp_provenance."
        )


class MalformedRecord(ProvenanceError):
    """Record is parseable JSON but does not match the expected schema."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Step(str, Enum):
    STEP1 = "step1"
    STEP2 = "step2"


class RunStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    UNKNOWN_TERMINATED = "unknown_terminated"


class IdentityMethod(str, Enum):
    SHA256 = "sha256"
    STAGED_READONLY = "staged_readonly"
    SIZE_MTIME_PATH = "size_mtime_path"


# ---------------------------------------------------------------------------
# V2 models
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    """Permissive base; preserves unknown fields for forward compatibility."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class TrustScope(_Base):
    timestamps: Literal["local_ntp", "signed_tsa"] = "local_ntp"
    actor_authentication: Literal["ood_session_uuid", "signed_actor"] = "ood_session_uuid"
    tamper_resistance: Literal[
        "append_only_advisory", "merkle_chain", "object_lock"
    ] = "append_only_advisory"
    sample_chain_of_custody: Literal["filename_only", "lims_linked"] = "filename_only"


class Actor(_Base):
    user: str
    uid: int | None = None
    hostname: str
    ood_session_id: str | None = None


class VsnpGui(_Base):
    git_sha: str
    git_branch: str | None = None
    git_dirty: bool = False
    deploy_path: str
    uvicorn_pid: int | None = None
    uvicorn_started_at: datetime | None = None


class AppliedPatch(_Base):
    name: str
    patch_file: str
    patch_sha256: str
    applied_at: datetime | None = None


class Vsnp3(_Base):
    version: str
    install_path: str
    subprocess_pid: int | None = None
    subprocess_exe_realpath: str | None = None
    applied_patches: list[AppliedPatch] = Field(default_factory=list)


class SystemPackages(_Base):
    samtools: str | None = None
    bcftools: str | None = None
    bwa: str | None = None
    mafft: str | None = None
    raxml: str | None = None
    iqtree: str | None = None


class Environment(_Base):
    conda_env_name: str | None = None
    conda_env_yaml_sha256: str | None = None
    conda_env_yaml_path: str | None = None
    pip_freeze_sha256: str | None = None
    pip_freeze_path: str | None = None
    system_packages: SystemPackages = Field(default_factory=SystemPackages)
    python_version: str | None = None
    platform: str | None = None


class ReferenceFile(_Base):
    relpath: str
    sha256: str
    size: int


class Reference(_Base):
    name: str
    path: str
    folder_manifest_sha256: str
    files: list[ReferenceFile] = Field(default_factory=list)
    resolved_via_symlink: bool = False


class Input(_Base):
    role: str
    sample: str | None = None
    filename: str
    abs_path: str
    staged_path: str | None = None
    size_bytes: int
    sha256: str | None = None
    identity_method: IdentityMethod
    mtime: datetime | None = None


class VcfDbSelection(_Base):
    path: str
    scope: Literal["shared", "user"]
    enabled: bool
    sample_count: int | None = None
    folder_manifest_sha256: str | None = None


class VcfDbInventoryEntry(_Base):
    path: str
    scope: Literal["shared", "user"]
    sample_count: int | None = None
    present: bool


class EditRecordRef(_Base):
    audit_log: str
    line_number: int | None = None
    record_sha256: str


class EditedSample(_Base):
    sample: str
    edit_record_refs: list[EditRecordRef] = Field(default_factory=list)


class CliBlock(_Base):
    command: str
    flags: list[str] = Field(default_factory=list)
    env_vars: dict[str, str | None] = Field(default_factory=dict)
    env_capture_policy: str = "allowlist_v1"


class Output(_Base):
    path: str
    exists: bool
    mtime: datetime | None = None


class QcBlock(_Base):
    samples_excluded: list[str] = Field(default_factory=list)
    exclude_source: str | None = None


class RunMetadataV2(_Base):
    """V2 run metadata. Used for both step1 (per-sample and roll-up) and step2."""

    schema_version: Literal[2]
    step: Step
    run_id: str
    pipeline_run_id: str | None = None
    parent_run_ids: list[str] = Field(default_factory=list)

    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    status: RunStatus
    exit_code: int | None = None

    trust_scope: TrustScope = Field(default_factory=TrustScope)

    actor: Actor
    vsnp_gui: VsnpGui
    vsnp3: Vsnp3
    environment: Environment = Field(default_factory=Environment)
    reference: Reference

    inputs: list[Input] = Field(default_factory=list)

    # step2-only fields
    vcf_db_selections: list[VcfDbSelection] = Field(default_factory=list)
    vcf_db_inventory_at_dispatch: list[VcfDbInventoryEntry] = Field(default_factory=list)
    edited_samples_at_run_time: list[EditedSample] = Field(default_factory=list)

    cli: CliBlock
    outputs: list[Output] = Field(default_factory=list)
    qc: QcBlock = Field(default_factory=QcBlock)

    # Convenience
    @property
    def is_terminal(self) -> bool:
        return self.status in {RunStatus.OK, RunStatus.FAILED, RunStatus.UNKNOWN_TERMINATED}


class DispatchMetadataV2(_Base):
    schema_version: Literal[2]
    run_id: str
    pipeline_run_id: str | None = None
    dispatched_at: datetime
    dispatch_state: dict[str, Any]  # Same shape as RunMetadataV2 minus finalize fields


# ---------------------------------------------------------------------------
# Pipeline-run record
# ---------------------------------------------------------------------------


class PipelineStep1Entry(_Base):
    run_id: str
    sample: str
    metadata_path: str
    status: RunStatus
    vsnp3_version: str | None = None
    reference_name: str | None = None
    reference_folder_manifest_sha256: str | None = None


class PipelineStep2Entry(_Base):
    run_id: str
    metadata_path: str
    status: RunStatus
    consumed_step1_run_ids: list[str] = Field(default_factory=list)
    consumed_step1_run_ids_complete: bool = True
    tree_outputs: list[str] = Field(default_factory=list)


class ConsistencyBlock(_Base):
    all_step1_same_reference: bool = True
    all_step1_same_vsnp3_version: bool = True
    all_step1_same_environment_hash: bool = True
    warnings: list[str] = Field(default_factory=list)


class PipelineRunV2(_Base):
    schema_version: Literal[2]
    kind: Literal["pipeline_run"]
    pipeline_run_id: str
    created_at: datetime
    created_by: str
    label: str | None = None
    step1_runs: list[PipelineStep1Entry] = Field(default_factory=list)
    step2_runs: list[PipelineStep2Entry] = Field(default_factory=list)
    consistency: ConsistencyBlock = Field(default_factory=ConsistencyBlock)
    trust_scope: TrustScope = Field(default_factory=TrustScope)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    try:
        with p.open("rb") as f:
            return json.load(f)
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as e:
        raise MalformedRecord(f"{p}: not valid JSON: {e}") from e


def _check_schema_version(data: dict[str, Any], path: Path) -> int:
    v = data.get("schema_version")
    if not isinstance(v, int):
        raise MalformedRecord(f"{path}: missing or non-integer schema_version")
    if v not in SUPPORTED_SCHEMA_VERSIONS:
        raise UnsupportedSchemaVersion(v, SUPPORTED_SCHEMA_VERSIONS)
    return v


def load(path: str | Path) -> RunMetadataV2:
    """Load a run_metadata.json. Dispatches on schema_version."""
    p = Path(path)
    data = _read_json(p)
    v = _check_schema_version(data, p)
    if v == 2:
        try:
            return RunMetadataV2.model_validate(data)
        except Exception as e:
            raise MalformedRecord(f"{p}: schema validation failed: {e}") from e
    raise UnsupportedSchemaVersion(v, SUPPORTED_SCHEMA_VERSIONS)


def load_dispatch(path: str | Path) -> DispatchMetadataV2:
    p = Path(path)
    data = _read_json(p)
    v = _check_schema_version(data, p)
    if v == 2:
        try:
            return DispatchMetadataV2.model_validate(data)
        except Exception as e:
            raise MalformedRecord(f"{p}: schema validation failed: {e}") from e
    raise UnsupportedSchemaVersion(v, SUPPORTED_SCHEMA_VERSIONS)


def load_pipeline_run(path: str | Path) -> PipelineRunV2:
    p = Path(path)
    data = _read_json(p)
    v = _check_schema_version(data, p)
    if v == 2:
        try:
            return PipelineRunV2.model_validate(data)
        except Exception as e:
            raise MalformedRecord(f"{p}: schema validation failed: {e}") from e
    raise UnsupportedSchemaVersion(v, SUPPORTED_SCHEMA_VERSIONS)


# ---------------------------------------------------------------------------
# Walking helpers
# ---------------------------------------------------------------------------


def iter_run_metadata(project_root: str | Path) -> Iterator[RunMetadataV2]:
    """Yield every run_metadata.json under a project tree.

    Tolerates malformed or unsupported records by skipping with a warning
    rather than raising; this is meant for bulk indexing, not validation.
    """
    import warnings

    root = Path(project_root)
    for p in sorted(root.rglob("run_metadata.json")):
        try:
            yield load(p)
        except (MalformedRecord, UnsupportedSchemaVersion) as e:
            warnings.warn(f"skipping {p}: {e}", stacklevel=2)


# ---------------------------------------------------------------------------
# Dispatch-vs-finalize diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldDrift:
    field: str
    dispatch_value: Any
    final_value: Any


def diff_dispatch_vs_final(
    dispatch_path: str | Path,
    final_path: str | Path,
) -> list[FieldDrift]:
    """Return list of fields whose value changed between dispatch and finalize.

    Compares the dispatch_state dict against the corresponding fields in the
    finalized run_metadata. Drift on any of these is forensically interesting:
    reference hashes, vsnp3 install path, vsnp_gui git_sha, environment hashes.

    Returns empty list if no drift.
    """
    disp = load_dispatch(dispatch_path)
    final = load(final_path)

    if disp.run_id != final.run_id:
        raise ProvenanceError(
            f"run_id mismatch: dispatch={disp.run_id} final={final.run_id}"
        )

    drift: list[FieldDrift] = []
    fields_to_check = [
        ("vsnp_gui.git_sha", lambda d: d.get("vsnp_gui", {}).get("git_sha"),
         final.vsnp_gui.git_sha),
        ("vsnp3.version", lambda d: d.get("vsnp3", {}).get("version"),
         final.vsnp3.version),
        ("vsnp3.install_path", lambda d: d.get("vsnp3", {}).get("install_path"),
         final.vsnp3.install_path),
        ("reference.folder_manifest_sha256",
         lambda d: d.get("reference", {}).get("folder_manifest_sha256"),
         final.reference.folder_manifest_sha256),
        ("environment.conda_env_yaml_sha256",
         lambda d: d.get("environment", {}).get("conda_env_yaml_sha256"),
         final.environment.conda_env_yaml_sha256),
        ("environment.pip_freeze_sha256",
         lambda d: d.get("environment", {}).get("pip_freeze_sha256"),
         final.environment.pip_freeze_sha256),
    ]
    for field_name, getter, final_val in fields_to_check:
        disp_val = getter(disp.dispatch_state)
        if disp_val != final_val:
            drift.append(
                FieldDrift(field=field_name, dispatch_value=disp_val, final_value=final_val)
            )
    return drift


# ---------------------------------------------------------------------------
# Pipeline-run reconstruction
# ---------------------------------------------------------------------------


def reconstruct_pipeline_run_from_step2(
    step2_metadata_path: str | Path,
    project_root: str | Path,
) -> PipelineRunV2 | None:
    """Walk a project tree to retro-build a pipeline-run record from a step2 run.

    For projects that predate T-07's pipeline_run support: given a step2
    run_metadata.json, find all step1 run_metadata.json files under the
    project and assemble a PipelineRunV2. The result is not written to disk
    here; caller decides where to persist it.

    Returns None if the step2 record has no parent_run_ids and no step1
    run_metadata files exist under the project (nothing to reconstruct).
    """
    import uuid as _uuid

    s2 = load(step2_metadata_path)
    if s2.step != Step.STEP2:
        raise ProvenanceError(f"{step2_metadata_path}: expected step2 record")

    root = Path(project_root)
    step1_records: list[RunMetadataV2] = []
    for p in sorted((root / "step1").rglob("run_metadata.json")):
        # Skip the step1 roll-up; we want per-sample records.
        if p.parent == root / "step1":
            continue
        try:
            rec = load(p)
            if rec.step == Step.STEP1:
                step1_records.append(rec)
        except (MalformedRecord, UnsupportedSchemaVersion):
            continue

    if not step1_records and not s2.parent_run_ids:
        return None

    # Consistency checks.
    refs = {r.reference.folder_manifest_sha256 for r in step1_records}
    versions = {r.vsnp3.version for r in step1_records}
    env_hashes = {
        (r.environment.conda_env_yaml_sha256, r.environment.pip_freeze_sha256)
        for r in step1_records
    }
    warnings_list: list[str] = []
    if len(refs) > 1:
        warnings_list.append(f"step1 runs used {len(refs)} different references")
    if len(versions) > 1:
        warnings_list.append(f"step1 runs used {len(versions)} different vsnp3 versions")
    if len(env_hashes) > 1:
        warnings_list.append(f"step1 runs used {len(env_hashes)} different environments")

    return PipelineRunV2(
        schema_version=2,
        kind="pipeline_run",
        pipeline_run_id=str(_uuid.uuid4()),
        created_at=datetime.utcnow(),
        created_by="reconstruct",
        label=None,
        step1_runs=[
            PipelineStep1Entry(
                run_id=r.run_id,
                sample=r.inputs[0].sample if r.inputs else "unknown",
                metadata_path=str(
                    Path("step1") / (r.inputs[0].sample or "unknown") / "run_metadata.json"
                ),
                status=r.status,
                vsnp3_version=r.vsnp3.version,
                reference_name=r.reference.name,
                reference_folder_manifest_sha256=r.reference.folder_manifest_sha256,
            )
            for r in step1_records
        ],
        step2_runs=[
            PipelineStep2Entry(
                run_id=s2.run_id,
                metadata_path=str(Path(step2_metadata_path).relative_to(root))
                if Path(step2_metadata_path).is_absolute()
                else str(step2_metadata_path),
                status=s2.status,
                consumed_step1_run_ids=[r.run_id for r in step1_records],
                consumed_step1_run_ids_complete=bool(step1_records),
                tree_outputs=[o.path for o in s2.outputs if o.path.endswith(".tre")],
            )
        ],
        consistency=ConsistencyBlock(
            all_step1_same_reference=(len(refs) <= 1),
            all_step1_same_vsnp3_version=(len(versions) <= 1),
            all_step1_same_environment_hash=(len(env_hashes) <= 1),
            warnings=warnings_list,
        ),
        trust_scope=s2.trust_scope,
    )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ProvenanceError",
    "UnsupportedSchemaVersion",
    "MalformedRecord",
    "Step",
    "RunStatus",
    "IdentityMethod",
    "RunMetadataV2",
    "DispatchMetadataV2",
    "PipelineRunV2",
    "TrustScope",
    "FieldDrift",
    "load",
    "load_dispatch",
    "load_pipeline_run",
    "iter_run_metadata",
    "diff_dispatch_vs_final",
    "reconstruct_pipeline_run_from_step2",
]
