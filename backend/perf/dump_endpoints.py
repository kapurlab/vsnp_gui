#!/usr/bin/env python3
"""Dump every switch-cascade endpoint's JSON for one project, in-process, from a
given vsnp_gui tree — so two trees can be diffed on the same fixture.
  <env python> dump_endpoints.py <tree> <project> <out.json>   (fixture env vars set)
"""
import json, os, sys, time
tree, project, out = sys.argv[1:4]
sys.path.insert(0, os.path.join(tree, "backend"))
import app.main as m  # noqa: E402


def qc():
    while True:
        d = m.qc_summary(project)
        if d.get("status") == "scanning":
            time.sleep(0.2)
            continue
        d.pop("scanned_at", None)
        d["rows"] = sorted(d["rows"], key=lambda r: str(r.get("_sample")))
        return d


res = {}
res["projects"] = [{k: v for k, v in p.items() if k not in ("last_activity",)} for p in m.projects()]
res["vcfs"] = m.project_vcfs_list(project)
res["qc_summary"] = qc()
res["reference_lock"] = m.reference_lock(project)
res["step1/status"] = m.step1_status(project)
res["step1/edits"] = m.step1_edits(project)
res["step1/samples"] = m.step1_samples(project)
res["kraken/samples"] = m.kraken_samples(project)
res["qc_exclude"] = m.qc_exclude_get(project)
res["quarantine"] = m.quarantine_list(project)
res["step1/setup"] = m.step1_setup_status(project)
res["inputs"] = m.project_inputs(project)
res["sra-download-report"] = m.project_sra_download_report(project)
runs = m.step2_runs_list(project)
res["step2/runs"] = runs
for pick in [None] + [r["run_id"] for r in runs]:
    tag = pick or "default"
    res[f"step2_outputs[{tag}]"] = m.step2_outputs(project, pick)
    res[f"step2/groupings[{tag}]"] = m.step2_groupings(project, pick)
    res[f"posthoc/status_all[{tag}]"] = m.posthoc_status_all(project, "snp_analysis", pick)
res["step2/vcf_count"] = m.step2_vcf_count(project)
res["step2/active"] = m.step2_active(project)
res["vcf_database/samples"] = m.step2_vcf_database_samples(project)
res["reference_audit"] = m.step2_reference_audit(project)
res["blocklist"] = m.step2_blocklist_get(project)
res["panel-accessions"] = m.step2_panel_accessions_get(project)
res["name-aliases"] = m.project_name_aliases(project)
res["panels"] = m.step2_panels_get(project)
res["build-exclusions"] = m.step2_build_exclusions_get(project)
# once more, warm, so the in-process caches are exercised too
res["step1/status (2)"] = m.step1_status(project)
res["step1/edits (2)"] = m.step1_edits(project)
res["step1/samples (2)"] = m.step1_samples(project)
res["projects (2)"] = [{k: v for k, v in p.items() if k not in ("last_activity",)} for p in m.projects()]
json.dump(res, open(out, "w"), indent=1, sort_keys=True, default=str)
print(f"wrote {out}: {len(res)} endpoints")
