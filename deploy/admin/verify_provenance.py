#!/usr/bin/env python3
"""
verify_provenance.py — T-07 end-to-end verifier.

Walks a project directory, validates every produced run_metadata.json /
pipeline_run record against the reader schema, summarizes presence and
content of provenance artifacts, and prints a punch list. Use after a
step1+step2 cycle to confirm provenance landed correctly without eyeballing
JSON by hand.

Usage:
    /srv/kapurlab/tools/vsnp3/bin/python deploy/admin/verify_provenance.py \\
        /home/vxk1/projects/nagalingam_test
    /srv/kapurlab/tools/vsnp3/bin/python deploy/admin/verify_provenance.py \\
        /srv/kapurlab/projects/sanity_test --strict   # exit 1 on any FAIL

Exit codes:
    0 — every check passed (or no FAILs in non-strict mode)
    1 — at least one FAIL
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `vsnp_provenance` importable wherever the script lives. Try the
# deployed location first, then a sibling-of-script-as-package fallback.
for candidate in (
    "/srv/kapurlab/tools/vsnp_gui/backend",
    str(Path(__file__).resolve().parent.parent.parent / "backend"),
):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

try:
    from app import vsnp_provenance as vp
    from app.vsnp_provenance import diff_dispatch_vs_final
except ImportError as e:
    print(f"FATAL: could not import vsnp_provenance ({e}). Check sys.path.", file=sys.stderr)
    sys.exit(2)


GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def fmt(level: str, label: str, detail: str = "") -> str:
    color = {"PASS": GREEN, "WARN": YELLOW, "FAIL": RED}.get(level, "")
    head = f"  {color}{level}{RESET}  {label}"
    return f"{head}  {detail}" if detail else head


class Report:
    def __init__(self):
        self.passes = 0
        self.warns = 0
        self.fails = 0
        self.lines: list[str] = []

    def ok(self, label: str, detail: str = ""):
        self.passes += 1
        self.lines.append(fmt("PASS", label, detail))

    def warn(self, label: str, detail: str = ""):
        self.warns += 1
        self.lines.append(fmt("WARN", label, detail))

    def fail(self, label: str, detail: str = ""):
        self.fails += 1
        self.lines.append(fmt("FAIL", label, detail))

    def section(self, label: str):
        self.lines.append(f"\n[{label}]")


def check_step1_sample(report: Report, sample_dir: Path) -> None:
    sample = sample_dir.name
    meta_path = sample_dir / "run_metadata.json"

    if not meta_path.is_file():
        report.warn(f"{sample}: no run_metadata.json", "(predates T-07 or dispatch failed)")
        return

    try:
        rec = vp.load(meta_path)
    except Exception as e:
        report.fail(f"{sample}: run_metadata.json invalid", f"{type(e).__name__}: {e}")
        return

    # Status
    if rec.status == vp.RunStatus.OK:
        report.ok(f"{sample}: status=ok", f"duration={rec.duration_seconds:.1f}s" if rec.duration_seconds else "")
    elif rec.status == vp.RunStatus.FAILED:
        report.warn(f"{sample}: status=failed", f"exit_code={rec.exit_code}")
    elif rec.status == vp.RunStatus.RUNNING:
        report.fail(f"{sample}: status=running", "(stuck; janitor would mark unknown_terminated after 48h)")
    elif rec.status == vp.RunStatus.UNKNOWN_TERMINATED:
        report.warn(f"{sample}: status=unknown_terminated", "(sentinels missing at finalize)")

    # Reference manifest
    if rec.reference and rec.reference.folder_manifest_sha256:
        report.ok(
            f"{sample}: reference manifest hashed",
            f"name={rec.reference.name} files={len(rec.reference.files)} sha={rec.reference.folder_manifest_sha256[:12]}…",
        )
    else:
        report.fail(f"{sample}: reference folder_manifest_sha256 missing")

    # vsnp_gui git provenance
    if rec.vsnp_gui and rec.vsnp_gui.git_sha:
        dirty = " (dirty)" if rec.vsnp_gui.git_dirty else ""
        report.ok(f"{sample}: vsnp_gui git captured", f"sha={rec.vsnp_gui.git_sha[:12]}{dirty} branch={rec.vsnp_gui.git_branch}")
    else:
        report.fail(f"{sample}: vsnp_gui.git_sha missing")

    # vsnp3 patches
    if rec.vsnp3 and rec.vsnp3.applied_patches:
        report.ok(f"{sample}: vsnp3 patches captured", f"version={rec.vsnp3.version} patches={len(rec.vsnp3.applied_patches)}")
    else:
        report.warn(f"{sample}: vsnp3.applied_patches empty", "(check deploy/vsnp3-patches/ exists)")

    # Environment snapshot
    env = rec.environment
    parts = []
    if env.conda_env_yaml_sha256:
        parts.append(f"conda={env.conda_env_yaml_sha256[:8]}…")
    if env.pip_freeze_sha256:
        parts.append(f"pip={env.pip_freeze_sha256[:8]}…")
    sp_count = sum(1 for tool in ("samtools", "bcftools", "bwa", "mafft", "raxml", "iqtree")
                   if getattr(env.system_packages, tool, None))
    parts.append(f"sys_pkgs={sp_count}/6")
    if env.conda_env_yaml_sha256 or env.pip_freeze_sha256 or sp_count > 0:
        report.ok(f"{sample}: environment captured", " ".join(parts))
    else:
        report.warn(f"{sample}: environment block empty", "(env capture may have failed)")

    # Per-run env snapshot files on disk
    prov_dir = sample_dir / "_provenance"
    if env.conda_env_yaml_path:
        if (prov_dir / "conda_env.yaml").is_file():
            report.ok(f"{sample}: per-run conda_env.yaml present")
        else:
            report.warn(f"{sample}: conda_env.yaml referenced but not on disk")
    if env.pip_freeze_path:
        if (prov_dir / "pip_freeze.txt").is_file():
            report.ok(f"{sample}: per-run pip_freeze.txt present")
        else:
            report.warn(f"{sample}: pip_freeze.txt referenced but not on disk")

    # Sentinels (forensic for finalize timing)
    sent_dir = sample_dir / ".provenance"
    sentinel_status = []
    for sent in ("started_at", "finished_at", "exit_code"):
        sentinel_status.append("✓" if (sent_dir / sent).is_file() else "✗")
    if all(s == "✓" for s in sentinel_status):
        report.ok(f"{sample}: sentinels {''.join(sentinel_status)}", "(start/finish/exit all written)")
    else:
        report.warn(
            f"{sample}: sentinels {''.join(sentinel_status)}",
            "(missing → status will be unknown_terminated)",
        )

    # Inputs
    if rec.inputs:
        identity_methods = {i.identity_method.value for i in rec.inputs}
        report.ok(
            f"{sample}: inputs captured",
            f"n={len(rec.inputs)} identity_methods={sorted(identity_methods)}",
        )
    else:
        report.fail(f"{sample}: inputs list empty")

    # Dispatch_state sub-block + drift
    if rec.dispatch_state:
        report.ok(f"{sample}: dispatch_state sub-block present")
        try:
            drift = diff_dispatch_vs_final(rec)
            if drift:
                report.warn(
                    f"{sample}: dispatch→finalize drift",
                    f"fields={[d.field for d in drift]}",
                )
            else:
                report.ok(f"{sample}: no drift between dispatch and finalize")
        except Exception as e:
            report.warn(f"{sample}: drift check failed", f"{type(e).__name__}: {e}")
    else:
        report.fail(f"{sample}: dispatch_state sub-block missing")

    # Outputs
    if rec.outputs:
        report.ok(f"{sample}: outputs captured", f"n={len(rec.outputs)}")
    else:
        report.warn(f"{sample}: outputs list empty", "(BAM/VCF didn't land?)")


def check_step1(report: Report, project_dir: Path) -> None:
    report.section("step1")
    step1_dir = project_dir / "step1"
    if not step1_dir.is_dir():
        report.warn("step1/ does not exist", "(this project hasn't run step1)")
        return

    # Batch roll-up
    batch_path = step1_dir / "run_metadata.json"
    if batch_path.is_file():
        try:
            batch = vp.load(batch_path)
            status = batch.status.value
            extra = f"duration={batch.duration_seconds:.1f}s" if batch.duration_seconds else ""
            level = report.ok if status == "ok" else report.warn
            level(f"step1 batch run_metadata.json: status={status}", extra)
        except Exception as e:
            report.fail("step1 batch run_metadata.json invalid", f"{type(e).__name__}: {e}")
    else:
        report.warn("step1 batch run_metadata.json missing", "(run predates T-07?)")

    # Per-sample
    sample_dirs = sorted(
        d for d in step1_dir.iterdir()
        if d.is_dir() and not d.name.startswith(("_", "."))
    )
    if not sample_dirs:
        report.warn("no sample dirs found")
        return
    report.ok(f"step1 sample dirs: {len(sample_dirs)}", f"({', '.join(d.name for d in sample_dirs)})")
    for sd in sample_dirs:
        check_step1_sample(report, sd)


def check_step2(report: Report, project_dir: Path) -> None:
    report.section("step2")
    step2_dir = project_dir / "step2"
    if not step2_dir.is_dir():
        report.warn("step2/ does not exist", "(this project hasn't run step2)")
        return

    meta_path = step2_dir / "run_metadata.json"
    if not meta_path.is_file():
        report.warn("step2 run_metadata.json missing", "(step2 didn't run, or predates T-07)")
        return

    try:
        rec = vp.load(meta_path)
    except Exception as e:
        report.fail("step2 run_metadata.json invalid", f"{type(e).__name__}: {e}")
        return

    if rec.status == vp.RunStatus.OK:
        report.ok(
            f"step2: status=ok",
            f"duration={rec.duration_seconds:.1f}s" if rec.duration_seconds else "",
        )
    elif rec.status == vp.RunStatus.RUNNING:
        report.fail("step2: status=running", "(stuck)")
    else:
        report.warn(f"step2: status={rec.status.value}", f"exit_code={rec.exit_code}")

    if rec.pipeline_run_id:
        report.ok("step2: pipeline_run_id set", f"id={rec.pipeline_run_id}")
    else:
        report.fail("step2: pipeline_run_id missing")

    if rec.parent_run_ids:
        report.ok(f"step2: parent_run_ids set", f"n={len(rec.parent_run_ids)}")
    else:
        report.warn("step2: parent_run_ids empty", "(no step1 records linked)")

    if rec.dispatch_state:
        report.ok("step2: dispatch_state sub-block present")
        try:
            drift = diff_dispatch_vs_final(rec)
            if drift:
                report.warn(f"step2: drift", f"fields={[d.field for d in drift]}")
            else:
                report.ok("step2: no drift between dispatch and finalize")
        except Exception as e:
            report.warn("step2: drift check failed", str(e))

    if rec.vcf_db_selections:
        report.ok(
            f"step2: vcf_db_selections captured",
            f"n={len(rec.vcf_db_selections)}",
        )
    else:
        report.warn("step2: vcf_db_selections empty", "(no DBs selected at dispatch)")

    if rec.vcf_db_inventory_at_dispatch:
        report.ok(
            f"step2: vcf_db_inventory_at_dispatch captured",
            f"n={len(rec.vcf_db_inventory_at_dispatch)} (forensic snapshot of what was available)",
        )
    else:
        report.warn("step2: vcf_db_inventory_at_dispatch empty")

    if rec.outputs:
        tre_count = sum(1 for o in rec.outputs if o.path.endswith(".tre"))
        report.ok(f"step2: outputs captured", f"total={len(rec.outputs)} trees={tre_count}")
    else:
        report.warn("step2: outputs list empty")


def check_pipeline_runs(report: Report, project_dir: Path) -> None:
    report.section("pipeline_runs")
    pl_dir = project_dir / "_provenance" / "pipeline_runs"
    if not pl_dir.is_dir():
        report.warn("_provenance/pipeline_runs/ does not exist", "(no step2 has been dispatched)")
        return

    files = sorted(pl_dir.glob("*.json"))
    if not files:
        report.warn("no pipeline_run records found")
        return
    report.ok(f"pipeline_run records: {len(files)}")

    for f in files:
        try:
            pl = vp.load_pipeline_run(f)
        except Exception as e:
            report.fail(f"{f.name}: invalid", f"{type(e).__name__}: {e}")
            continue
        report.ok(
            f"{f.name[:8]}…: kind=pipeline_run",
            f"step1={len(pl.step1_runs)} step2={len(pl.step2_runs)} created_by={pl.created_by}",
        )
        for s2 in pl.step2_runs:
            if s2.consumed_step1_run_ids_complete:
                report.ok(f"  step2 {s2.run_id[:8]}…: consumed_step1_run_ids_complete=True")
            else:
                report.warn(
                    f"  step2 {s2.run_id[:8]}…: consumed_step1_run_ids_complete=False",
                    "(some step1 samples missing/running at dispatch)",
                )
        if pl.consistency.warnings:
            for w in pl.consistency.warnings:
                report.warn(f"  consistency warning", w)
        if not pl.consistency.all_step1_same_reference:
            report.warn("  step1 runs used different references")
        if not pl.consistency.all_step1_same_vsnp3_version:
            report.warn("  step1 runs used different vsnp3 versions")
        if not pl.consistency.all_step1_same_environment_hash:
            report.warn("  step1 runs used different environments")


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify T-07 provenance artifacts in a project")
    ap.add_argument("project_dir", type=Path, help="Path to the project directory")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 on any FAIL (default: exit 1 only if zero PASSes)",
    )
    args = ap.parse_args()

    project_dir = args.project_dir.resolve()
    if not project_dir.is_dir():
        print(f"FATAL: not a directory: {project_dir}", file=sys.stderr)
        return 2

    print(f"Verifying provenance for: {project_dir}\n")
    report = Report()
    check_step1(report, project_dir)
    check_step2(report, project_dir)
    check_pipeline_runs(report, project_dir)

    print("\n".join(report.lines))
    print(
        f"\n{GREEN}{report.passes} PASS{RESET}  "
        f"{YELLOW}{report.warns} WARN{RESET}  "
        f"{RED}{report.fails} FAIL{RESET}"
    )

    if args.strict and report.fails > 0:
        return 1
    if report.passes == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
