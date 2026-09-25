#!/usr/bin/env python3
"""Filesystem calls per endpoint of the switch cascade, in-process.

Run with the fslat model on PYTHONPATH (FSLAT_MS=0 counts only):
  PYTHONPATH=fslat FSLAT_ROOT=<fx>/data FSLAT_COUNT_DIR=<fx>/counts \
  XDG_CONFIG_HOME=<fx>/config VSNP_GUI_SITE_ROOT=<fx>/data/site \
  VSNP_GUI_SHARED_PROJECTS_ROOT= <env>/bin/python endpoint_calls.py <checkout> <project>

Each endpoint is called once on a fresh process (cold: on-disk caches only)
and once more (warm: in-memory caches). Subprocess calls (the Results scan)
are read from the count files it leaves behind.
"""
import glob
import os
import sys
import time

checkout, project = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(checkout, "backend"))
import sitecustomize as sc  # noqa: E402  (the model, already imported at startup)
import app.main as m  # noqa: E402

COUNT_DIR = os.environ["FSLAT_COUNT_DIR"]
MYPID = str(os.getpid())


def sub_calls() -> int:
    n = 0
    for f in glob.glob(os.path.join(COUNT_DIR, "*.count")):
        if os.path.basename(f) == MYPID + ".count":
            continue
        try:
            n += int(open(f).read().strip() or 0)
        except (OSError, ValueError):
            pass
    return n


def qc_blocking():
    while True:
        d = m.qc_summary(project)
        if d.get("status") == "scanning":
            time.sleep(0.2)
            continue
        return d


def runs_then_outputs():
    runs = m.step2_runs_list(project)
    pick = next((r["run_id"] for r in runs if r.get("has_results")), runs[0]["run_id"] if runs else None)
    return pick


pick = None


def outputs():
    return m.step2_outputs(project, pick)


def groupings():
    return m.step2_groupings(project, pick)


def posthoc():
    return m.posthoc_status_all(project, "snp_analysis", pick)


ENDPOINTS = [
    ("/api/projects", lambda: m.projects()),
    ("vcfs", lambda: m.project_vcfs_list(project)),
    ("qc_summary (scan)", qc_blocking),
    ("reference_lock", lambda: m.reference_lock(project)),
    ("step1/status", lambda: m.step1_status(project)),
    ("step1/edits", lambda: m.step1_edits(project)),
    ("step1/samples", lambda: m.step1_samples(project)),
    ("kraken/samples", lambda: m.kraken_samples(project)),
    ("qc_exclude", lambda: m.qc_exclude_get(project) if hasattr(m, "qc_exclude_get") else None),
    ("quarantine", lambda: m.quarantine_list(project)),
    ("step1/setup", lambda: m.step1_setup_status(project)),
    ("inputs", lambda: m.project_inputs(project)),
    ("sra-download-report", lambda: m.project_sra_download_report(project)),
    ("step2/runs", runs_then_outputs),
    ("step2_outputs", outputs),
    ("step2/groupings", groupings),
    ("step2/vcf_count", lambda: m.step2_vcf_count(project)),
    ("posthoc/status_all", posthoc),
    ("step2/active", lambda: m.step2_active(project) if hasattr(m, "step2_active") else None),
    ("vcf_database/samples", lambda: m.step2_vcf_database_samples(project)),
    ("reference_audit", lambda: m.step2_reference_audit(project)),
    ("blocklist", lambda: m.step2_blocklist_get(project)),
    ("panel-accessions", lambda: m.step2_panel_accessions_get(project)),
    ("name-aliases", lambda: m.project_name_aliases(project)),
    ("panels", lambda: m.step2_panels_get(project)),
    ("build-exclusions", lambda: m.step2_build_exclusions_get(project)),
]


def one_pass(tag: str):
    global pick
    total = 0
    print(f"-- {tag}")
    for name, fn in ENDPOINTS:
        c0, s0, t0 = sc.count(), sub_calls(), time.perf_counter()
        r = fn()
        if name == "step2/runs":
            pick = r
        dt = time.perf_counter() - t0
        time.sleep(0.05)
        calls = sc.count() - c0
        subs = sub_calls() - s0
        total += calls + subs
        extra = f" (+{subs:,} in subprocess)" if subs else ""
        print(f"   {calls + subs:>8,} calls  {dt:6.2f} s  {name}{extra}")
    print(f"   {total:>8,} calls  total")
    return total


cold = one_pass("cold (fresh process, on-disk caches)")
warm = one_pass("warm (same process)")
print(f"== {project}: cold {cold:,} calls, warm {warm:,} calls")
