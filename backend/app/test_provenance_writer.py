"""
End-to-end smoke test for provenance_writer.

Sets up a fake project tree mimicking vsnp_gui's layout, dispatches step1
batch + step2, simulates the bash batch's sentinel emission, finalizes both,
and verifies the resulting records load via the existing reader/indexer.

Tests integration points the writer alone can exercise; the actual main.py
wiring + JobManager change need on-server testing against nagalingam_test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import provenance_writer as pw
import vsnp_provenance as vp
from vsnp_provenance.index import Indexer


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(condition, label):
    if not condition:
        raise AssertionError(f"{label}: expected truthy, got falsy")
    print(f"  OK  {label}")


def make_fake_reference_folder(refs_root: Path, name: str) -> Path:
    """Build a minimal reference folder with fasta + gbk + define_filter.xlsx."""
    folder = refs_root / name
    folder.mkdir(parents=True)
    (folder / "NC_002945.4.fasta").write_text(
        ">NC_002945.4\nACGT" * 100 + "\n"
    )
    (folder / "NC_002945.4.gbk").write_text("LOCUS       NC_002945     400 bp\n")
    (folder / "define_filter.xlsx").write_text("fake xlsx bytes")
    return folder


def make_fake_vsnp3(install_root: Path) -> Path:
    """Minimal vsnp3 install with a vsnp3_step1.py shim."""
    install_root.mkdir(parents=True)
    (install_root / "VERSION").write_text("3.16\n")
    bin_dir = install_root / "bin"
    bin_dir.mkdir()
    step1 = bin_dir / "vsnp3_step1.py"
    step1.write_text("#!/usr/bin/env python\nprint('vsnp3 3.16')\n")
    step1.chmod(0o755)
    return install_root


def make_fake_vsnp_gui(deploy_root: Path) -> Path:
    """A real git repo so capture_vsnp_gui_state can succeed."""
    deploy_root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(deploy_root)], check=True)
    subprocess.run(["git", "-C", str(deploy_root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(deploy_root), "config", "user.name", "t"], check=True)
    (deploy_root / "README.md").write_text("fake vsnp_gui\n")
    subprocess.run(["git", "-C", str(deploy_root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(deploy_root), "commit", "-q", "-m", "init"],
        check=True,
    )
    return deploy_root


def make_fastq(path: Path, size_bytes: int = 1024) -> Path:
    """Make a small fake gzipped fastq."""
    import gzip
    with gzip.open(path, "wt") as f:
        f.write("@read1\nACGT\n+\nIIII\n" * (size_bytes // 16))
    return path


def make_sample_dir(step1_dir: Path, sample: str) -> Path:
    sample_dir = step1_dir / sample
    sample_dir.mkdir()
    make_fastq(sample_dir / f"{sample}_R1.fastq.gz")
    make_fastq(sample_dir / f"{sample}_R2.fastq.gz")
    return sample_dir


def simulate_bash_batch(step1_dir: Path, samples: list[str], exit_codes: dict[str, int]):
    """Simulate what the bash batch would do: write sentinel files per sample.

    Mimics the wrapper that step1_sample_command_with_sentinels() injects.
    """
    import time as _time
    for sample in samples:
        sample_dir = step1_dir / sample
        prov = sample_dir / ".provenance"
        prov.mkdir(exist_ok=True)
        started = _time.time()
        (prov / "started_at").write_text(f"{started}\n")
        # Simulate work: 50ms
        _time.sleep(0.05)
        ec = exit_codes.get(sample, 0)
        (prov / "exit_code").write_text(f"{ec}\n")
        (prov / "finished_at").write_text(f"{_time.time()}\n")
        # Write a fake *_zc.vcf output for step1 success
        if ec == 0:
            (sample_dir / f"{sample}_zc.vcf").write_text(
                "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\n"
            )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vsnp_writer_smoke_"))
    print(f"workspace: {tmp}")

    try:
        # Build fake server tree
        srv = tmp / "srv" / "kapurlab"
        audit_root = srv / "audit"
        audit_root.mkdir(parents=True)
        refs_root = srv / "refs" / "vsnp3" / "reference_options"
        refs_root.mkdir(parents=True)
        vsnp3_root = srv / "tools" / "vsnp3"
        vsnp_gui_root = srv / "tools" / "vsnp_gui"
        projects_root = srv / "projects"
        projects_root.mkdir(parents=True)

        make_fake_reference_folder(refs_root, "mtbc0_v1.1")
        make_fake_vsnp3(vsnp3_root)
        make_fake_vsnp_gui(vsnp_gui_root)

        # Build a project with 3 samples
        project_dir = projects_root / "smoke_test"
        project_dir.mkdir()
        step1_dir = project_dir / "step1"
        step1_dir.mkdir()
        samples = ["S001", "S002", "S003"]
        for s in samples:
            make_sample_dir(step1_dir, s)
        step2_dir = project_dir / "step2"
        step2_dir.mkdir()

        cfg = {
            "vsnp3_path": str(vsnp3_root),
            "vsnp_gui_deploy_path": str(vsnp_gui_root),
            "vsnp3_reference_options_root": str(refs_root),
            "audit_root": str(audit_root),
            "projects_root": str(projects_root),
            "shared_projects_root": str(projects_root),
            "provenance": {"hash_max_bytes": 256 * 1024 * 1024},
        }

        # ------- Test: step1 dispatch -------
        print("\n[step1 dispatch]")
        batch_run_id, sample_run_ids = pw.dispatch_step1_batch(
            cfg, project_dir, samples, "mtbc0_v1.1",
            user="vxk1",
            ood_session_id="batch-uuid-abc",
        )
        assert_eq(len(sample_run_ids), 3, "sample_run_ids count")
        assert_true(uuid.UUID(batch_run_id), "batch_run_id is uuid")

        # Each sample has run_metadata.json + dispatch_state sub-block
        for s in samples:
            meta_path = step1_dir / s / "run_metadata.json"
            assert_true(meta_path.is_file(), f"per-sample metadata exists ({s})")
            rec = vp.load(meta_path)
            assert_eq(rec.status.value, "running", f"{s} status at dispatch")
            assert_eq(rec.run_id, sample_run_ids[s], f"{s} run_id matches")
            assert_true(rec.dispatch_state is not None, f"{s} has dispatch_state sub-block")
            assert_true(
                rec.dispatch_state.get("vsnp_gui", {}).get("git_sha") == rec.vsnp_gui.git_sha,
                f"{s} dispatch_state.vsnp_gui.git_sha matches top-level",
            )
            # Per-run env files copied
            assert_true(
                (step1_dir / s / "_provenance" / "conda_env.yaml").is_file()
                or rec.environment.conda_env_yaml_path is None,
                f"{s} conda_env.yaml copied or absent (no conda)",
            )

        # Batch roll-up exists
        batch_path = step1_dir / "run_metadata.json"
        batch_rec = vp.load(batch_path)
        assert_eq(batch_rec.run_id, batch_run_id, "batch roll-up run_id")
        assert_eq(batch_rec.status.value, "running", "batch roll-up status")

        # Reference folder hash is populated
        assert_true(batch_rec.reference.folder_manifest_sha256, "reference folder manifest hashed")
        assert_eq(len(batch_rec.reference.files), 3, "reference folder file count")

        # ------- Test: simulate bash batch + step1 finalize -------
        print("\n[step1 finalize]")
        # S002 fails to test failure path
        simulate_bash_batch(step1_dir, samples, {"S002": 1})

        started = datetime.now(tz=timezone.utc) - timedelta(seconds=2)
        finished = datetime.now(tz=timezone.utc)
        pw.finalize_step1_batch(
            project_dir, batch_run_id, exit_code=1, started_at=started, finished_at=finished,
        )

        for s in samples:
            rec = vp.load(step1_dir / s / "run_metadata.json")
            expected_status = "failed" if s == "S002" else "ok"
            assert_eq(rec.status.value, expected_status, f"{s} terminal status")
            assert_true(rec.duration_seconds is not None, f"{s} duration set")
            assert_true(rec.dispatch_state is not None, f"{s} dispatch_state preserved at finalize")

        batch_rec = vp.load(batch_path)
        assert_eq(batch_rec.status.value, "failed", "batch roll-up terminal status")

        # ------- Test: dispatch_state vs final diff (no drift expected) -------
        print("\n[dispatch_vs_final diff]")
        for s in samples:
            rec = vp.load(step1_dir / s / "run_metadata.json")
            drift = vp.diff_dispatch_vs_final(rec)
            assert_eq(len(drift), 0, f"{s} no drift between dispatch and finalize")

        # ------- Test: step2 dispatch on personal project (warn-and-proceed semantics) -------
        # Create a 4th sample with no metadata to test the predates-T-07 path
        print("\n[step2 dispatch with predates-T-07 sample]")
        make_sample_dir(step1_dir, "S004_legacy")  # no run_metadata.json
        # Also simulate bash sentinels for the legacy sample so it has a *_zc.vcf
        legacy_dir = step1_dir / "S004_legacy"
        (legacy_dir / "S004_legacy_zc.vcf").write_text("##fileformat=VCFv4.2\n")

        step2_run_id, pipeline_run_id = pw.dispatch_step2(
            cfg, project_dir, "mtbc0_v1.1",
            cli_command="vsnp3_step2.py -wd . -a -t mtbc0_v1.1",
            cli_flags=["-a"],
            user="vxk1",
            ood_session_id="batch-uuid-abc",
            is_shared=False,  # personal: warn-and-proceed
            resolved_vcf_db_folders=[],
        )
        assert_true(uuid.UUID(step2_run_id), "step2 run_id is uuid")
        assert_true(uuid.UUID(pipeline_run_id), "pipeline_run_id is uuid")

        # pipeline_run record exists, references all step1 runs, flags missing sample
        pl_path = project_dir / "_provenance" / "pipeline_runs" / f"{pipeline_run_id}.json"
        assert_true(pl_path.is_file(), "pipeline_run record exists")
        pl_rec = vp.load_pipeline_run(pl_path)
        assert_eq(len(pl_rec.step1_runs), 3, "pipeline_run includes 3 step1 records (legacy excluded)")
        assert_true(
            not pl_rec.step2_runs[0].consumed_step1_run_ids_complete,
            "consumed_step1_run_ids_complete=False due to legacy sample",
        )
        assert_true(
            any("predates T-07" in w or "no run_metadata" in w
                for w in pl_rec.consistency.warnings),
            "warning surfaces predates-T-07 sample",
        )

        # step2 metadata exists with pipeline_run_id link
        step2_path = step2_dir / "run_metadata.json"
        s2_rec = vp.load(step2_path)
        assert_eq(s2_rec.pipeline_run_id, pipeline_run_id, "step2 metadata links pipeline_run_id")
        assert_eq(len(s2_rec.parent_run_ids), 3, "step2 parent_run_ids count")
        assert_true(s2_rec.dispatch_state is not None, "step2 dispatch_state present")

        # ------- Test: step2 finalize -------
        print("\n[step2 finalize]")
        # Make a fake tree output
        group_dir = step2_dir / "Lineage-04"
        group_dir.mkdir()
        (group_dir / "Lineage-04_2026-05-09_labeled.tre").write_text("(S001:1,S003:1);\n")

        s2_started = datetime.now(tz=timezone.utc) - timedelta(seconds=3)
        s2_finished = datetime.now(tz=timezone.utc)
        pw.finalize_step2(
            project_dir, step2_run_id, exit_code=0,
            started_at=s2_started, finished_at=s2_finished,
        )

        s2_rec = vp.load(step2_path)
        assert_eq(s2_rec.status.value, "ok", "step2 terminal status")
        tree_outputs = [o for o in s2_rec.outputs if o.path.endswith(".tre")]
        assert_eq(len(tree_outputs), 1, "step2 tree output captured")

        # pipeline_run updated with step2 finalize state
        pl_rec = vp.load_pipeline_run(pl_path)
        assert_eq(pl_rec.step2_runs[0].status.value, "ok", "pipeline_run step2 status updated")
        assert_eq(len(pl_rec.step2_runs[0].tree_outputs), 1, "pipeline_run tree_outputs updated")

        # ------- Test: step2 blocked on shared with running step1 -------
        print("\n[step2 blocked on shared with running step1]")
        # Reset one sample to running status
        s001_meta = step1_dir / "S001" / "run_metadata.json"
        rec_dict = json.loads(s001_meta.read_text())
        rec_dict["status"] = "running"
        rec_dict["finished_at"] = None
        s001_meta.write_text(json.dumps(rec_dict))

        try:
            pw.dispatch_step2(
                cfg, project_dir, "mtbc0_v1.1",
                cli_command="vsnp3_step2.py -wd . -a -t mtbc0_v1.1",
                cli_flags=["-a"],
                user="vxk1",
                ood_session_id="batch-uuid-abc",
                is_shared=True,  # shared: should block
                resolved_vcf_db_folders=[],
            )
            print("  FAIL  expected Step2DispatchBlocked, got success")
            return 1
        except pw.Step2DispatchBlocked as e:
            assert_eq(e.running_samples, ["S001"], "blocked exception lists running sample")
            print("  OK  shared+running dispatch correctly blocked")

        # Restore S001 to ok for the indexer test
        rec_dict["status"] = "ok"
        rec_dict["finished_at"] = datetime.now(tz=timezone.utc).isoformat()
        s001_meta.write_text(json.dumps(rec_dict))

        # ------- Test: indexer consumes writer-produced records -------
        print("\n[indexer round-trip]")
        idx_db = audit_root / "runs.sqlite"
        idx = Indexer(idx_db)
        idx.init_schema()
        crawl_stats = idx.crawl_root(projects_root)

        # 3 step1 per-sample + 1 step1 batch roll-up + 1 step2 = 5 indexed
        # The legacy S004 sample has no run_metadata.json so it's not indexed.
        # The S001 we restored is ok now, so indexed.
        # Note: writer may produce additional roll-up records.
        # Just verify the step2 record is indexed and pipeline_run is indexed.
        step2_query = idx.query_runs(step="step2")
        assert_eq(len(step2_query), 1, "step2 record indexed")
        assert_eq(step2_query[0]["run_id"], step2_run_id, "step2 indexed run_id")
        assert_eq(step2_query[0]["pipeline_run_id"], pipeline_run_id, "step2 indexed pipeline_run_id")

        stats = idx.stats()
        assert_eq(stats["total_pipeline_runs"], 1, "1 pipeline_run indexed")

        # Verify run_metadata records contain dispatch_state on indexer-readable load
        pipeline_query = idx.query_runs(pipeline_run_id=pipeline_run_id)
        assert_true(len(pipeline_query) >= 1, "indexer queries by pipeline_run_id")

        # ------- Test: env snapshot dedup (run dispatch twice, store has one yaml) -------
        print("\n[env snapshot dedup]")
        snapshots_dir = audit_root / "env_snapshots"
        if snapshots_dir.is_dir():
            # Snapshot files must be group-readable (0640) so other lab admins
            # in the kapurlab-admins group can audit env state, not just the
            # original writer.
            yamls = list(snapshots_dir.glob("*.yaml"))
            for y in yamls:
                mode = y.stat().st_mode & 0o777
                if mode != 0o640:
                    raise AssertionError(
                        f"{y.name} mode={oct(mode)}, expected 0o640"
                    )
            print(f"  OK  env snapshot files mode 0o640 (n={len(yamls)})")
            yaml_count_before = len(yamls)
            # Re-dispatch by running another fake step2
            project_dir2 = projects_root / "smoke_test_2"
            project_dir2.mkdir()
            (project_dir2 / "step1").mkdir()
            (project_dir2 / "step2").mkdir()
            try:
                pw.dispatch_step2(
                    cfg, project_dir2, "mtbc0_v1.1",
                    cli_command="vsnp3_step2.py", cli_flags=[],
                    user="vxk1", ood_session_id=None, is_shared=False,
                    resolved_vcf_db_folders=[],
                )
            except pw.DispatchFailed:
                # No step1 records is fine; the env snapshot still gets re-checked
                pass
            yaml_count_after = sum(1 for _ in snapshots_dir.glob("*.yaml"))
            assert_eq(yaml_count_after, yaml_count_before,
                      "env snapshot dedup: same content -> same file count")

        # ------- Test: unresolvable reference degrades, never blocks -------
        # The Ames HPC regression: the reference registry didn't contain the
        # folder the run uses, and capture_reference_state's DispatchFailed
        # 500'd the Run click before the job started. The capture must now
        # return a degraded block and the dispatch must succeed.
        print("\n[step2 dispatch with unresolvable reference]")
        step2_run_id3, _pipe3 = pw.dispatch_step2(
            cfg, project_dir, "not_a_registered_reference",
            cli_command="vsnp3_step2.py -wd . -t not_a_registered_reference",
            cli_flags=[],
            user="vxk1", ood_session_id=None, is_shared=False,
            resolved_vcf_db_folders=[],
        )
        rec3 = json.loads((step2_dir / "run_metadata.json").read_text())
        ref_block = rec3["dispatch_state"]["reference"]
        assert_true(ref_block.get("capture_error"),
                    "reference block records capture_error")
        assert_eq(ref_block.get("path"), None, "unresolved reference path")
        assert_true(ref_block.get("searched_locations"),
                    "searched locations recorded for diagnosis")
        print("  OK  dispatch succeeded without a resolvable reference folder")

        print("\nAll writer smoke tests passed.")
        return 0

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
