"""The pathogen add-on framework (addons.py, addon_run.py, the Step 1 / Step 2 /
Results seams in main.py), end to end with a small add-on and a fake vsnp3.

Pinned here:
  * discovery: a valid add-on is ready; a bad manifest is reported, not run;
    two folders with one id resolve to the one named as its tools.yml entry;
  * a project that names a pathogen gets a batch script that runs the hooks
    around vsnp3 (choose_reference -> -t, prepare_reads, -n <folder>,
    after_sample before exit_code, after_run after the pool), and the real
    batch, run with a fake vsnp3, aligns each sample to the folder its
    chooser returned and writes the add-on's row;
  * the chooser's failure stops the sample with the reason (status "error",
    kept out of a plain Run), a reference outside the library is refused,
    a paired sample given one prepared read keeps its own reads;
  * a sample is complete only once the add-on step ran (addon_done);
  * Results rows carry the add-on's metrics, the more severe level wins, and
    a result stamped for another alignment shows as pending;
  * the add-on's QC thresholds apply to its projects only, without editing
    its manifest; its references are a non-removable location listed last
    and tagged "addon"; its Step 2 databases are listed (scope "addon") and
    never feed the tree relabel csv; Step 2's reference check lists samples
    on another lineage reference instead of offering to delete them;
  * the endpoints: list, create with a pathogen, read, set, action, rerun.

    env/bin/python backend/app/test_addons.py
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="addons_test_")).resolve()
os.environ["XDG_CONFIG_HOME"] = str(_TMP / "xdg")
os.environ["VSNP_GUI_SITE_ROOT"] = str(_TMP / "site")
for _k in ("BDTOOLS_DEFAULT_PATHOGEN", "BDTOOLS_TOOLSDIR"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException  # noqa: E402

from app import addons, addon_run  # noqa: E402
from app import refs as refs_mod  # noqa: E402
from app import main as m  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    print(f"  {'OK ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILED += 1


HOOK = '''import json, sys
from pathlib import Path
ctx = json.load(open(sys.argv[sys.argv.index("--context") + 1]))
out = Path(ctx.get("run_dir") or (ctx.get("sample") or {}).get("out_dir") or ctx["project"]["pathogen_dir"])
out.mkdir(parents=True, exist_ok=True)
lib = Path(ctx["library"])
'''

CHOOSE = HOOK + '''
name = ctx["sample"]["name"]
if name.startswith("FAIL"):
    (out / "reference.json").write_text(json.dumps({"error": "no lineage within 20% of the reads"}))
    sys.exit(3)
if name.startswith("OUTSIDE"):
    (out / "reference.json").write_text(json.dumps({"reference_dir": str(lib.parent.parent / "outside" / "OUT_REF")}))
    sys.exit(0)
ref = "DEMO_B" if name.endswith("B") else "DEMO_A"
(out / "reference.json").write_text(json.dumps({"reference_dir": str(lib / "reference_options" / ref), "vote": 0.97}))
'''

PREPARE = HOOK + '''
reads = ctx["sample"]["reads"]
new = []
for r in reads[:1] if ctx["sample"]["name"].startswith("HALF") else reads:
    dst = out / ("clipped_" + Path(r).name)
    dst.write_bytes(Path(r).read_bytes())
    new.append(str(dst))
(out / "reads.json").write_text(json.dumps({"reads": new}))
'''

AFTER = HOOK + '''
s = ctx["sample"]
rep = out / "report.html"
rep.write_text("<!doctype html><title>r</title>")
level = "fail" if s["name"].startswith("BAD") else "review" if s.get("prepare_failed") else "pass"
(out / "result.json").write_text(json.dumps({
    "metrics": {"lineage": Path(s["reference_dir"]).name, "identity": 0.97},
    "flags": {"level": level, "reasons": ["serotype mixture"] if level == "fail" else []},
    "files": [{"path": str(rep), "kind": "report", "name": "report.html"}]}))
'''

RUN = HOOK + '''
(out / "summary.json").write_text(json.dumps({"rows": ctx["samples"], "text": "%d samples" % len(ctx["samples"])}))
'''

ACTION = HOOK + '''
t = out / "tree.tre"
t.write_text("(a:1,b:1);")
(out / "action.json").write_text(json.dumps({"status": "ok", "summary": "tree of " + ctx["params"].get("group", "all"),
                                             "files": [{"path": str(t), "kind": "tree"}]}))
'''


def make_addon(root: Path, addon_id: str = "demo", dirname: str = "demo_addon", bad: bool = False) -> Path:
    d = root / dirname
    (d / "hooks").mkdir(parents=True)
    (d / "env" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, d / "env" / "bin" / "python")
    for name, body in (("choose_reference", CHOOSE), ("prepare_reads", PREPARE), ("after_sample", AFTER),
                       ("after_run", RUN), ("tree", ACTION)):
        (d / "hooks" / f"{name}.py").write_text(body)
    lib = root / "lib" / addon_id
    for ref in ("DEMO_A", "DEMO_B"):
        (lib / "reference_options" / ref).mkdir(parents=True, exist_ok=True)
        (lib / "reference_options" / ref / f"{ref}.fasta").write_text(f">{ref}\nACGTACGT\n")
    dbdir = lib / "vcf_db_folders" / "DEMO_A" / "DEMO_all"
    dbdir.mkdir(parents=True)
    (dbdir / "PANEL-1_zc.vcf").write_text("##fileformat=VCFv4.2\n")
    (dbdir / "VCF_refs.csv").write_text("x,y\n")
    (lib / "LIBRARY_VERSION").write_text("1\n")
    out_ref = root / "outside" / "OUT_REF"
    out_ref.mkdir(parents=True, exist_ok=True)
    (out_ref / "OUT_REF.fasta").write_text(">OUT\nACGT\n")
    (lib / "scopes.json").write_text(json.dumps({"options": [{"value": "all", "label": "All"}]}))
    (d / ".library_path").write_text(str(lib) + "\n")
    man = {
        "contract": 1, "id": addon_id, "tool": dirname, "name": "Demo pathogen", "version": "0.1.0",
        "consumer": "vsnp_gui",
        "library": {"markers": ["LIBRARY_VERSION", "reference_options"]},
        "scopes": {"file": "scopes.json"},
        "hooks": {"choose_reference": "hooks/choose_reference.py", "prepare_reads": "hooks/prepare_reads.py",
                  "after_sample": "hooks/after_sample.py", "after_run": "hooks/after_run.py"},
        "actions": [{"id": "tree", "label": "Tree", "command": "hooks/tree.py",
                     "params": [{"name": "group", "fixture": "all"}]}],
        "qc_thresholds": {"genome_coverage": {"pass_min": 90.0, "review_min": 70.0}},
        "columns": [{"key": "lineage", "label": "Lineage"}, {"key": "identity", "label": "Identity"}],
    }
    if bad:
        man["contract"] = 99
    (d / "addon.json").write_text(json.dumps(man))
    return d


FAKE_VSNP3 = f'''#!{sys.executable}
import sys, time
from pathlib import Path
from openpyxl import Workbook
a = sys.argv[1:]
def opt(k, default=""):
    return a[a.index(k) + 1] if k in a else default
ref = Path(opt("-t"))
name = opt("-n") or Path(opt("-r1")).name.split("_")[0]
fasta = sorted(ref.glob("*.fasta"))[0]
al = Path("alignment_" + fasta.stem); al.mkdir(exist_ok=True)
(al / (name + "_zc.vcf")).write_text("##fileformat=VCFv4.2\\n##reference=file://" + str(fasta) + "\\n")
(al / fasta.name).write_text(fasta.read_text())
Path("vsnp3_args.txt").write_text("\\n".join(a))
wb = Workbook(); ws = wb.active
ws.append(["sample", "Reference", "Average Depth", "Unmapped Percent", "Genome with Coverage"])
ws.append([name, str(ref) + " Forced", "400", "5.0%", "80.0%"])
wb.save(name + "_2026-11-18_10-00-00_stats.xlsx")
'''


def reads(d: Path, pair: bool = True) -> None:
    d.mkdir(parents=True, exist_ok=True)
    body = gzip.compress(b"@r\nACGTACGTAC\n+\nIIIIIIIIII\n" * 50, mtime=0)
    (d / f"{d.name}_R1.fastq.gz").write_bytes(body)
    if pair:
        (d / f"{d.name}_R2.fastq.gz").write_bytes(body)


def setup():
    tools = _TMP / "tools"
    make_addon(tools)
    addons.ROOTS_OVERRIDE = [tools]
    addons._CACHE["addons"] = None
    vs = _TMP / "vsnp3"
    (vs / "bin").mkdir(parents=True)
    (vs / "dependencies").mkdir()
    for s in ("vsnp3_step1.py", "vsnp3_step2.py"):
        (vs / "bin" / s).write_text(FAKE_VSNP3)
        (vs / "bin" / s).chmod(0o755)
    stock_refs = _TMP / "site_refs"
    (stock_refs / "Mycobacterium_AF2122").mkdir(parents=True)
    (stock_refs / "Mycobacterium_AF2122" / "Mycobacterium_AF2122.fasta").write_text(">NC\nACGT\n")
    (_TMP / "projects").mkdir()
    cfg = {"vsnp3_path": str(vs), "projects_root": str(_TMP / "projects"),
           "reference_locations": [str(stock_refs)], "step1_max_parallel": 2,
           "step1_min_fastq_bytes": 1,
           "vcf_db_folders_root": str(_TMP / "no_shared")}
    m.load_config = lambda: dict(cfg)
    return cfg


def test_discovery():
    print("[discovery]")
    a = addons.get("demo")
    check("a valid add-on is found and ready", a is not None and a.ready)
    check("its env is the add-on's own", a is not None and a.env_dir == _TMP / "tools" / "demo_addon" / "env")
    other = _TMP / "other_tools"
    make_addon(other, dirname="demo_addon_src")
    make_addon(other, addon_id="broken", dirname="broken_addon", bad=True)
    addons.ROOTS_OVERRIDE = [_TMP / "tools", other]
    addons._CACHE["addons"] = None
    found = addons.discover(refresh=True)
    check("two folders with one id: the one named as its tools.yml entry wins",
          found["demo"].dir.name == "demo_addon" and any("also declared" in n for n in found["demo"].notes))
    check("a bad manifest is reported and not ready",
          "broken" in found and not found["broken"].ready and any("contract" in p for p in found["broken"].problems))
    addons.ROOTS_OVERRIDE = [_TMP / "tools"]
    addons._CACHE["addons"] = None


def run_batch(project: Path) -> None:
    step1 = project / "step1"
    env = dict(os.environ)
    env["PATH"] = str(_TMP / "vsnp3" / "bin") + os.pathsep + env.get("PATH", "")
    # The batch's helper must find this test's add-on: discovery reads the
    # same tools root the backend does (BDTOOLS_TOOLS_ROOT).
    env["BDTOOLS_TOOLS_ROOT"] = str(_TMP / "tools")
    subprocess.run(["/bin/bash", "run_step1.sh"], cwd=str(step1), env=env, capture_output=True, timeout=300)


def test_step1(cfg):
    print("[Step 1 in a pathogen project]")
    res = m.project_create(m.ProjectCreate(name="fmdv_run", pathogen="demo", pathogen_scope="all"))
    project = Path(res["path"])
    check("created with the pathogen in project.json", addons.project_pathogen(project) == "demo")
    for s in ("S-A", "S-B", "FAIL-1", "OUTSIDE-1", "HALF-1", "BAD-1"):
        reads(project / "step1" / s)
    started = []
    saved = (m.wrap_cmd, m.build_env, m.job_manager.start_job, m.provenance_writer.dispatch_step1_batch)
    m.wrap_cmd = lambda c, cmd: cmd
    m.build_env = lambda c: {}
    m.job_manager.start_job = lambda **kw: started.append(kw) or "job"
    prov = []
    m.provenance_writer.dispatch_step1_batch = lambda *a, **k: prov.append(k) or ("batch", {})
    try:
        m.step1_run("fmdv_run", m.Step1Request(reference="Mycobacterium_AF2122"))
    finally:
        m.wrap_cmd, m.build_env, m.job_manager.start_job, m.provenance_writer.dispatch_step1_batch = saved
    script = (project / "step1" / "run_step1.sh").read_text()
    check("the request's reference is ignored: no -t <site folder>", "Mycobacterium_AF2122" not in script)
    check("project.json reference not overwritten", m._project_reference(project) == "")
    check("choose_reference, prepare_reads, after_sample, after_run in the script",
          all(f"app.addon_run {h}" in script for h in ("choose_reference", "prepare_reads", "after_sample", "after_run")))
    check("after_sample runs before exit_code is written",
          script.index("app.addon_run after_sample") < script.index("echo $STATUS > .provenance/exit_code"))
    check("-t from the chooser and -n from the folder",
          'RUN_ARGS+=(-t "$REF_DIR")' in script and 'RUN_ARGS+=(-n "$SAMPLE_NAME")' in script)
    check("batch provenance says the add-on chooses per sample",
          prov and prov[0].get("per_sample_by", "").startswith("demo "))
    run_batch(project)
    s1 = project / "step1"
    args_a = (s1 / "S-A" / "vsnp3_args.txt").read_text().split("\n") if (s1 / "S-A" / "vsnp3_args.txt").exists() else []
    check("S-A aligned to the folder its chooser returned",
          "-t" in args_a and args_a[args_a.index("-t") + 1].endswith("reference_options/DEMO_A"))
    check("...named after its folder (-n S-A)", "-n" in args_a and args_a[args_a.index("-n") + 1] == "S-A")
    check("...from the prepared reads", any("clipped_S-A_R1" in x for x in args_a))
    check("S-B on its own lineage reference", (s1 / "S-B" / "alignment_DEMO_B").is_dir())
    check("FAIL-1 not aligned; the reason kept",
          not list((s1 / "FAIL-1").glob("alignment_*"))
          and "within 20%" in (s1 / "FAIL-1" / ".provenance" / "addon_choose_failed").read_text())
    check("OUTSIDE-1 refused: a reference outside the library",
          "outside" in (s1 / "OUTSIDE-1" / ".provenance" / "addon_choose_failed").read_text())
    args_h = (s1 / "HALF-1" / "vsnp3_args.txt").read_text()
    check("HALF-1: one prepared read for a pair -> its own reads, marked review",
          "clipped_" not in args_h and (s1 / "HALF-1" / ".provenance" / "addon_prepare_failed").exists())
    res_a = addons.read_json(addons.sample_out_dir(project, "demo", "S-A") / "result.json") or {}
    check("S-A's row written and stamped with its stats workbook",
          res_a.get("metrics", {}).get("lineage") == "DEMO_A"
          and str(res_a.get("_stamp", {}).get("stats_workbook", "")).endswith("_stats.xlsx"))
    check("after_run wrote the summary",
          (addons.pathogen_dir(project, "demo") / "summary.json").is_file())
    check("addon_done and exit_code 0 -> complete",
          m._step1_is_complete(s1 / "S-A") and (s1 / "S-A" / ".provenance" / "addon_done").exists())
    (s1 / "S-A" / ".provenance" / "addon_done").unlink()
    check("...without addon_done it is not complete", not m._step1_is_complete(s1 / "S-A"))
    (s1 / "S-A" / ".provenance" / "addon_done").write_text("demo 0.1.0\n")
    check("a chooser failure counts as errored (no plain-Run retry)", m._step1_errored(s1 / "FAIL-1"))
    st = {s["sample"]: s for s in m.step1_status("fmdv_run")["samples"]}
    check("status: FAIL-1 is error with the chooser's reason",
          st["FAIL-1"]["status"] == "error" and "within 20%" in st["FAIL-1"]["reason"])
    check("status: S-A complete", st["S-A"]["status"] == "complete")
    return project


def test_rows(project: Path, cfg):
    print("[Results rows]")
    rows = m._qc_rows_blocking(project / "step1")
    ann = [{k: v for k, v in r.items() if not k.startswith("_sa")} for r in rows]
    m._annotate_qc_rows(ann, m._resolve_qc_thresholds(cfg, project))
    m._apply_addon_rows(ann, project)
    by = {Path(r["_file"]).parent.name: r for r in ann}
    a = by.get("S-A", {})
    check("the add-on's metrics are on the row", a.get("pathogen:lineage") == "DEMO_A")
    check("the add-on's genome-covered threshold applies (80% < 90 -> review)",
          a.get("_qc_verdict", {}).get("level") == "review"
          and any("genome covered" in r for r in a["_qc_verdict"]["reasons"]))
    bad = by.get("BAD-1", {})
    check("the more severe level wins (add-on fail over review)",
          bad.get("_qc_verdict", {}).get("level") == "fail"
          and any("serotype mixture" in r for r in bad["_qc_verdict"]["reasons"]))
    res = addons.sample_out_dir(project, "demo", "S-B") / "result.json"
    data = json.loads(res.read_text()); data["_stamp"]["stats_workbook"] = "/elsewhere/old_stats.xlsx"
    res.write_text(json.dumps(data))
    ann2 = [dict(r) for r in rows]
    m._apply_addon_rows(ann2, project, verdicts=False)
    b = {Path(r["_file"]).parent.name: r for r in ann2}.get("S-B", {})
    check("a result stamped for another alignment shows as pending",
          b.get("pathogen:_status") == "pending" and "pathogen:lineage" not in b)
    csv = m.qc_summary_csv("fmdv_run").body.decode()
    check("the CSV export carries the add-on's columns", "demo: Lineage" in csv)
    th_stock = m._resolve_qc_thresholds(cfg, None)
    check("the add-on's thresholds do not reach a stock project", "genome_coverage" not in th_stock)
    man = addons.get("demo").manifest["qc_thresholds"]["genome_coverage"]
    check("...and its manifest is not edited by a merge", man == {"pass_min": 90.0, "review_min": 70.0})


def test_refs_and_step2(project: Path, cfg):
    print("[references and Step 2]")
    refs = m.list_references(cfg)
    names = [r["name"] for r in refs]
    check("the add-on's references are listed after the site's, tagged addon",
          names.index("Mycobacterium_AF2122") < names.index("DEMO_A")
          and next(r for r in refs if r["name"] == "DEMO_A").get("addon") == "demo")
    check("a site reference is not tagged", "addon" not in next(r for r in refs if r["name"] == "Mycobacterium_AF2122"))
    check("the add-on location cannot be removed",
          not next(l for l in refs_mod.reference_locations(cfg) if l["source"] == "addon")["removable"])
    check("Step 2 resolves an add-on reference by name", m._reference_dir_named(cfg, "DEMO_B") is not None)
    dbs = m._resolved_vcf_db_folders(cfg)
    demo_db = [d for d in dbs if d.get("scope") == "addon"]
    check("the add-on's Step 2 database is listed for its reference",
          demo_db and demo_db[0]["reference"] == "DEMO_A" and demo_db[0]["addon"] == "demo")
    check("...and never feeds the tree relabel csv", m._discover_vcf_refs_csv(cfg) is None)
    m.project_pathogen_set("fmdv_run", m.PathogenSettings(comparison_reference="DEMO_A"))
    check("the comparison reference is the project's Step 2 reference", m._project_reference(project) == "DEMO_A")
    db = project / "step2" / "vcf_database"
    db.mkdir(parents=True, exist_ok=True)
    for s in ("S-A", "S-B"):
        src = next((project / "step1" / s).glob("alignment_*/*_zc.vcf"))
        shutil.copy2(src, db / src.name)
    audit = m.step2_reference_audit("fmdv_run")
    check("the reference check lists S-B as another lineage, offers nothing to drop",
          not audit["removable"] and not audit["recoverable"]
          and [d["sample"] for d in audit.get("other_reference", [])] == ["S-B"])


def test_endpoints(project: Path):
    print("[endpoints]")
    lst = m.addons_list()
    check("/api/addons lists the add-on with its columns",
          [a["id"] for a in lst["addons"]] == ["demo"] and lst["addons"][0]["columns"][0]["key"] == "lineage")
    check("scopes come from the library file", m.addon_scopes("demo")["options"][0]["value"] == "all")
    got = m.project_pathogen_get("fmdv_run")
    check("the Pathogen tab's data: rows, no-reference samples, samples by reference",
          {r["sample"] for r in got["rows"]} >= {"S-A", "S-B"}
          and {f["sample"] for f in got["no_reference"]} == {"FAIL-1", "OUTSIDE-1"}
          and got["samples_by_reference"].get("DEMO_B") == ["S-B"])
    try:
        m.project_create(m.ProjectCreate(name="x", pathogen="nope"))
        check("creating with an add-on that is not installed is refused", False)
    except HTTPException as e:
        check("creating with an add-on that is not installed is refused", e.status_code == 400)
    jobs = []
    saved = m.job_manager.start_job
    m.job_manager.start_job = lambda **kw: jobs.append(kw) or "job"
    try:
        r = m.project_pathogen_action("fmdv_run", "tree", m.AddonActionRequest(params={"group": "SAT1"}))
        check("an action starts a job in its own run folder under pathogen/demo/runs",
              "/pathogen/demo/runs/" in r["run_dir"] and "addon_run action" in jobs[-1]["command"])
        try:
            m.project_pathogen_action("fmdv_run", "tree", m.AddonActionRequest(params={"group": "$(rm -rf /)"}))
            check("a parameter that is not a plain token is refused", False)
        except HTTPException as e:
            check("a parameter that is not a plain token is refused", e.status_code == 400)
        m.project_pathogen_rerun("fmdv_run")
        check("rerun starts the add-on steps as a job", "addon_run rerun" in jobs[-1]["command"])
    finally:
        m.job_manager.start_job = saved
    run_dir = addons.runs_dir(project, "demo") / "t_tree"
    rc = addon_run.main(["action", "--project-dir", str(project), "--action", "tree",
                         "--params", json.dumps({"group": "SAT1"}), "--run-dir", str(run_dir)])
    check("the action writes its files and run metadata",
          rc == 0 and (run_dir / "tree.tre").is_file()
          and addons.read_json(run_dir / "run_metadata.json")["status"] == "ok")
    check("...and the Pathogen tab lists the run",
          any(r["action"] == "tree" for r in m.project_pathogen_get("fmdv_run")["runs"]))


def main() -> int:
    saved_load = m.load_config
    try:
        cfg = setup()
        test_discovery()
        project = test_step1(cfg)
        test_rows(project, cfg)
        test_refs_and_step2(project, cfg)
        test_endpoints(project)
    finally:
        m.load_config = saved_load
        addons.ROOTS_OVERRIDE = None
        shutil.rmtree(_TMP, ignore_errors=True)
    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll pathogen add-on tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
