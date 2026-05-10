"""
Smoke test for the indexer.

Builds a small fake project tree with a few run_metadata.json files plus a
pipeline-run record, runs init/crawl/query/stats/export through both the
Python API and the CLI, and verifies expected behavior.

Run with:

    cd backend/app
    /srv/kapurlab/tools/vsnp3/bin/python test_provenance_indexer.py

(Lives next to the vsnp_provenance/ package so the sys.path manipulation
inside this file resolves cleanly.)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vsnp_provenance.index import Indexer, gc_running


def make_run(
    project: Path,
    step: str,
    sample: str | None,
    *,
    run_id: str | None = None,
    pipeline_run_id: str | None = None,
    status: str = "ok",
    started_offset_hours: float = 0,
    reference: str = "mtbc0_v1.1",
    vsnp3_version: str = "3.16",
    parent_run_ids: list[str] | None = None,
) -> Path:
    """Write a minimal valid run_metadata.json under the project."""
    run_id = run_id or str(uuid.uuid4())
    started = datetime.now(tz=timezone.utc) - timedelta(hours=started_offset_hours)
    rec = {
        "schema_version": 2,
        "step": step,
        "run_id": run_id,
        "pipeline_run_id": pipeline_run_id,
        "parent_run_ids": parent_run_ids or [],
        "started_at": started.isoformat(),
        "finished_at": (started + timedelta(minutes=10)).isoformat() if status != "running" else None,
        "duration_seconds": 600.0 if status != "running" else None,
        "status": status,
        "exit_code": 0 if status == "ok" else (1 if status == "failed" else None),
        "actor": {"user": "vxk1", "uid": 12047, "hostname": "wgs3"},
        "vsnp_gui": {"git_sha": "79aae04", "deploy_path": "/srv/kapurlab/tools/vsnp_gui",
                     "git_branch": "web", "git_dirty": False},
        "vsnp3": {"version": vsnp3_version, "install_path": "/srv/kapurlab/tools/vsnp3"},
        "environment": {
            "conda_env_name": "vsnp3-3.16",
            "conda_env_yaml_sha256": "9f2c1a",
            "pip_freeze_sha256": "82de00",
            "system_packages": {"samtools": "1.17-2", "bcftools": "1.17-3"},
        },
        "reference": {
            "name": reference,
            "path": f"/srv/kapurlab/refs/vsnp3/reference_options/{reference}",
            "folder_manifest_sha256": "ee01" + reference,
            "files": [],
            "resolved_via_symlink": False,
        },
        "inputs": [
            {
                "role": "fastq", "sample": sample or "n/a",
                "filename": f"{sample or 'unknown'}_R1.fastq.gz",
                "abs_path": f"/data/{sample}_R1.fastq.gz",
                "size_bytes": 345533011,
                "identity_method": "staged_readonly",
            }
        ],
        "cli": {
            "command": f"vsnp3_{step}.py",
            "flags": ["-a"] if step == "step2" else [],
            "env_vars": {"PATH": "/srv/kapurlab/tools/vsnp3/bin:/usr/bin"},
        },
        "outputs": [],
        "qc": {"samples_excluded": []},
    }
    if step == "step1":
        sample_dir = project / "step1" / sample
        sample_dir.mkdir(parents=True, exist_ok=True)
        path = sample_dir / "run_metadata.json"
    else:
        step_dir = project / "step2"
        step_dir.mkdir(parents=True, exist_ok=True)
        path = step_dir / "run_metadata.json"
    path.write_text(json.dumps(rec, indent=2))
    return path


def make_pipeline_run(project: Path, step1_run_ids: list[str], step2_run_id: str) -> Path:
    pl_id = str(uuid.uuid4())
    rec = {
        "schema_version": 2,
        "kind": "pipeline_run",
        "pipeline_run_id": pl_id,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "created_by": "vxk1",
        "label": "smoke_test_figure3",
        "step1_runs": [
            {
                "run_id": rid,
                "sample": f"S{i}",
                "metadata_path": f"step1/S{i}/run_metadata.json",
                "status": "ok",
                "vsnp3_version": "3.16",
                "reference_name": "mtbc0_v1.1",
                "reference_folder_manifest_sha256": "ee01mtbc0_v1.1",
            }
            for i, rid in enumerate(step1_run_ids)
        ],
        "step2_runs": [
            {
                "run_id": step2_run_id,
                "metadata_path": "step2/run_metadata.json",
                "status": "ok",
                "consumed_step1_run_ids": step1_run_ids,
                "consumed_step1_run_ids_complete": True,
                "tree_outputs": ["step2/Lineage-04/tree.tre"],
            }
        ],
        "consistency": {
            "all_step1_same_reference": True,
            "all_step1_same_vsnp3_version": True,
            "all_step1_same_environment_hash": True,
            "warnings": [],
        },
    }
    pl_dir = project / "_provenance" / "pipeline_runs"
    pl_dir.mkdir(parents=True, exist_ok=True)
    path = pl_dir / f"{pl_id}.json"
    path.write_text(json.dumps(rec, indent=2))
    return path


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vsnp_idx_smoke_"))
    print(f"workspace: {tmp}")
    try:
        projects_root = tmp / "projects"
        projects_root.mkdir()
        proj_a = projects_root / "alpha"
        proj_b = projects_root / "beta"
        proj_a.mkdir()
        proj_b.mkdir()

        # Project alpha: 3 step1 (one failed), 1 step2 with a pipeline-run linking them
        s1_ids = [str(uuid.uuid4()) for _ in range(3)]
        for sid, sample in zip(s1_ids, ["S1", "S2", "S3"]):
            status = "ok" if sample != "S2" else "failed"
            make_run(proj_a, "step1", sample, run_id=sid, status=status, started_offset_hours=2)
        s2_id = str(uuid.uuid4())
        make_run(proj_a, "step2", None, run_id=s2_id, parent_run_ids=s1_ids,
                 started_offset_hours=1)
        make_pipeline_run(proj_a, s1_ids, s2_id)

        # Project beta: 1 stuck-running step1 from 72h ago to test gc
        stuck_id = str(uuid.uuid4())
        make_run(proj_b, "step1", "S99", run_id=stuck_id, status="running",
                 started_offset_hours=72)
        # And one ok run to verify mixed-status crawl
        make_run(proj_b, "step1", "S100", started_offset_hours=4)

        db_path = tmp / "audit" / "runs.sqlite"
        idx = Indexer(db_path)

        # init
        idx.init_schema()
        assert_eq(idx.schema_version(), 1, "index_schema_version after init")

        # crawl
        stats = idx.crawl_root(projects_root)
        # Expected: 3 step1 + 1 step2 from alpha + 1 ok step1 from beta = 5 indexed
        # The stuck 'running' record from beta is skipped (not indexed).
        assert_eq(stats.runs_inserted, 5, "runs inserted on first crawl")
        assert_eq(stats.runs_updated, 0, "runs updated on first crawl")
        assert_eq(stats.runs_skipped_non_terminal, 1, "running record skipped")
        assert_eq(stats.pipeline_runs_inserted, 1, "pipeline runs inserted")
        assert_eq(stats.errors, 0, "crawl errors")

        # idempotency
        stats2 = idx.crawl_root(projects_root)
        assert_eq(stats2.runs_inserted, 0, "runs inserted on second crawl (idempotent)")
        assert_eq(stats2.runs_unchanged, 5, "runs unchanged on second crawl")
        assert_eq(stats2.runs_skipped_non_terminal, 1, "running record still skipped")
        assert_eq(stats2.pipeline_runs_unchanged, 1, "pipeline runs unchanged on second crawl")

        # query: filter by step
        step2_runs = idx.query_runs(step="step2")
        assert_eq(len(step2_runs), 1, "step2 runs count")
        assert_eq(step2_runs[0]["run_id"], s2_id, "step2 run_id")
        assert_eq(step2_runs[0]["pipeline_run_id"], None, "step2 pipeline_run_id (none in record)")

        # query: filter by reference
        mtbc_runs = idx.query_runs(reference_name="mtbc0_v1.1")
        assert_eq(len(mtbc_runs), 5, "mtbc0_v1.1 reference runs")

        # query: filter by status
        failed_runs = idx.query_runs(status="failed")
        assert_eq(len(failed_runs), 1, "failed runs")
        assert_eq(failed_runs[0]["step"], "step1", "failed run is step1")

        # stats
        s = idx.stats()
        assert_eq(s["total_runs"], 5, "stats total_runs")
        assert_eq(s["total_pipeline_runs"], 1, "stats total_pipeline_runs")
        assert_eq(s["by_status"].get("ok"), 4, "stats by_status ok")
        assert_eq(s["by_status"].get("failed"), 1, "stats by_status failed")

        # disk-side gc: dry run
        would = gc_running(projects_root, max_runtime_hours=48, dry_run=True)
        assert_eq(len(would), 1, "gc dry run finds stuck file")

        # disk-side gc: actual rewrite
        rewritten = gc_running(projects_root, max_runtime_hours=48, dry_run=False)
        assert_eq(len(rewritten), 1, "gc rewrites stuck file")
        # Verify the file is now unknown_terminated
        stuck_path = proj_b / "step1" / "S99" / "run_metadata.json"
        rec = json.loads(stuck_path.read_text())
        assert_eq(rec["status"], "unknown_terminated", "stuck file status after gc")
        assert "janitor_notes" in rec, "janitor_notes added"

        # Re-crawl picks up the now-terminal record
        stats3 = idx.crawl_root(projects_root)
        assert_eq(stats3.runs_inserted, 1, "stuck run indexed after gc")

        # query: unknown_terminated now in index
        ut = idx.query_runs(status="unknown_terminated")
        assert_eq(len(ut), 1, "unknown_terminated in query")

        # rename tracking
        idx.record_rename("/srv/kapurlab/projects/alpha", "/srv/kapurlab/projects/alpha_v2",
                          renamed_by="vxk1")
        resolved = idx.resolve_current_path("/srv/kapurlab/projects/alpha")
        assert_eq(resolved, "/srv/kapurlab/projects/alpha_v2", "rename resolution")

        # export
        out_path = tmp / "runs.jsonl"
        n = idx.export_jsonl(out_path)
        assert_eq(n, 6, "exported row count")
        lines = out_path.read_text().strip().split("\n")
        assert_eq(len(lines), 6, "jsonl line count")
        # Each line should be valid JSON
        for ln in lines:
            json.loads(ln)
        print("  OK  all jsonl lines parse")

        # CLI smoke: stats subcommand
        result = subprocess.run(
            [sys.executable, "-m", "vsnp_provenance.index", "--db", str(db_path), "stats"],
            capture_output=True, text=True, check=True,
            cwd=str(Path(__file__).resolve().parent),
        )
        cli_stats = json.loads(result.stdout)
        assert_eq(cli_stats["total_runs"], 6, "CLI stats total_runs")

        # CLI smoke: query with filter
        result = subprocess.run(
            [sys.executable, "-m", "vsnp_provenance.index", "--db", str(db_path),
             "query", "--reference", "mtbc0_v1.1", "--format", "json", "--limit", "10"],
            capture_output=True, text=True, check=True,
            cwd=str(Path(__file__).resolve().parent),
        )
        cli_rows = json.loads(result.stdout)
        assert_eq(len(cli_rows), 6, "CLI query row count")

        print("\nAll smoke tests passed.")
        return 0

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
