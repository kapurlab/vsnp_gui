from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pathlib import Path
from typing import List, Optional, Dict
import socket
import json
import os
import time
import subprocess
import shutil
import gzip
import sys
import logging

from app.config import load_config, save_config
from app.jobs import JobManager
from app.projects import create_project, list_projects, ensure_project_dirs, archive_project, delete_project
from app.refs import list_references
from app.sra import expand_accessions, build_download_script

app = FastAPI(title="vSNP GUI API")
logger = logging.getLogger("uvicorn.error")
_IGV_STATE = {"genome": ""}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

cfg = load_config()
projects_root = Path(cfg["projects_root"])
projects_root.mkdir(parents=True, exist_ok=True)
job_manager = JobManager(Path(cfg["projects_root"]) / ".jobs")


def build_env(cfg: Dict) -> Dict[str, str]:
    vsnp_bin = Path(cfg["vsnp3_path"]) / "bin"
    current_path = os.environ.get("PATH", "")
    env_path = cfg.get("conda_env_path", "").strip()
    if env_path:
        env_path_obj = Path(env_path)
        bin_path = env_path_obj.parent if env_path_obj.name == "python" else (env_path_obj / "bin")
        return {"PATH": f"{bin_path}:{vsnp_bin}:{current_path}"}
    return {"PATH": f"{vsnp_bin}:{current_path}"}


def wrap_cmd(cfg: Dict, command: str) -> str:
    env_path = cfg.get("conda_env_path", "").strip()
    if env_path:
        return f"PATH=\"{Path(env_path) / 'bin'}:$PATH\" {command}"
    conda_env = cfg.get("conda_env", "").strip()
    if conda_env:
        conda_exe = cfg.get("conda_exe", "").strip() or "conda"
        return f"{conda_exe} run -n {conda_env} {command}"
    return command


def conda_python_cmd(cfg: Dict, code: str, args: Optional[List[str]] = None) -> List[str]:
    args = args or []
    env_path = cfg.get("conda_env_path", "").strip()
    if env_path:
        env_path_obj = Path(env_path)
        python_exe = env_path_obj if env_path_obj.name == "python" else (env_path_obj / "bin" / "python")
        return [str(python_exe), "-c", code, *args]
    conda_env = cfg.get("conda_env", "").strip()
    conda_exe = cfg.get("conda_exe", "").strip() or "conda"
    return [conda_exe, "run", "-n", conda_env, "python", "-c", code, *args]


class ConfigUpdate(BaseModel):
    vsnp3_path: Optional[str] = None
    projects_root: Optional[str] = None
    conda_env: Optional[str] = None
    conda_exe: Optional[str] = None
    conda_env_path: Optional[str] = None
    igv_app_path: Optional[str] = None
    sra: Optional[Dict] = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)


class SraRequest(BaseModel):
    accessions: List[str]
    folder: Optional[str] = None


class LinkLocalRequest(BaseModel):
    path: str


class ImportVcfRequest(BaseModel):
    source_paths: List[str] = []
    include_step1: bool = False
    reference: Optional[str] = None
    action: Optional[str] = "copy"  # copy | link
    on_conflict: Optional[str] = "skip"  # skip | overwrite | rename
    allow_mismatch: bool = False
    prefix_duplicates: bool = False
    dedupe: bool = False
    allow_fuzzy_match: bool = True
    confirm_large: bool = False


class Step1Request(BaseModel):
    reference: Optional[str] = None
    debug: bool = False


class Step2Request(BaseModel):
    reference: Optional[str] = None


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def get_config():
    cfg = load_config()
    root_dir = Path(__file__).resolve().parent.parent.parent
    cfg["gui_root"] = str(root_dir)
    return cfg


@app.post("/api/config")
def update_config(update: ConfigUpdate):
    cfg = load_config()
    if update.vsnp3_path is not None:
        cfg["vsnp3_path"] = update.vsnp3_path
    if update.projects_root is not None:
        cfg["projects_root"] = update.projects_root
    if update.conda_env is not None:
        cfg["conda_env"] = update.conda_env
    if update.conda_exe is not None:
        cfg["conda_exe"] = update.conda_exe
    if update.conda_env_path is not None:
        cfg["conda_env_path"] = update.conda_env_path
    if update.igv_app_path is not None:
        cfg["igv_app_path"] = update.igv_app_path
    if update.sra is not None:
        cfg["sra"].update(update.sra)
    save_config(cfg)
    return cfg


@app.get("/api/references")
def references():
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    return list_references(vsnp3_path)


@app.get("/api/projects")
def projects():
    cfg = load_config()
    return list_projects(Path(cfg["projects_root"]))


@app.post("/api/projects")
def project_create(payload: ProjectCreate):
    cfg = load_config()
    project_dir = create_project(Path(cfg["projects_root"]), payload.name)
    return {"path": str(project_dir), "name": payload.name}


@app.post("/api/projects/{project}/archive")
def project_archive(project: str):
    cfg = load_config()
    try:
        target = archive_project(Path(cfg["projects_root"]), project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"archived_to": str(target)}


@app.delete("/api/projects/{project}")
def project_delete(project: str):
    cfg = load_config()
    try:
        deleted = delete_project(Path(cfg["projects_root"]), project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if deleted is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": project}


@app.post("/api/projects/{project}/link-local")
def project_link_local(project: str, payload: LinkLocalRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_project_dirs(project_dir)
    raw_path = payload.path or ""
    src = Path(raw_path.strip()).expanduser()
    if not src.exists():
        print(f"Link-local failed. Raw path: {raw_path!r} Resolved: {src}")
        raise HTTPException(status_code=400, detail=f"Input path not found: {src}")
    download_dir = project_dir / "download"
    count = 0
    for f in src.glob("*.fastq.gz"):
        target = download_dir / f.name
        if not target.exists():
            target.symlink_to(f)
            count += 1
    return {"linked": count}


@app.post("/api/projects/{project}/import-vcfs")
def project_import_vcfs(project: str, payload: ImportVcfRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_project_dirs(project_dir)

    vcfs = []
    source_roots = []
    for raw in payload.source_paths or []:
        src = Path((raw or "").strip()).expanduser()
        if not src.exists():
            raise HTTPException(status_code=400, detail=f"Source path not found: {src}")
        source_roots.append(src)
        vcfs.extend(list(src.rglob("*_zc.vcf")))
        vcfs.extend(list(src.rglob("*_zc.vcf.gz")))

    if payload.include_step1:
        step1_dir = project_dir / "step1"
        if step1_dir.exists():
            source_roots.append(step1_dir)
            vcfs.extend(list(step1_dir.rglob("*_zc.vcf")))
            vcfs.extend(list(step1_dir.rglob("*_zc.vcf.gz")))

    if not vcfs:
        raise HTTPException(status_code=400, detail="No *_zc.vcf files found in provided sources")
    if len(vcfs) > 500 and not payload.confirm_large:
        raise HTTPException(status_code=400, detail=f"Large import ({len(vcfs)} VCFs). Confirm to continue.")

    alias_map = _reference_alias_map(Path(cfg["vsnp3_path"]))
    detected_refs = _detect_vcf_references(vcfs, alias_map)
    if not payload.reference:
        if len(detected_refs) > 1:
            raise HTTPException(status_code=400, detail=f"Mixed references detected: {', '.join(sorted(detected_refs))}")
        detected_ref = next(iter(detected_refs), "")
        if not detected_ref:
            raise HTTPException(status_code=400, detail="Reference is required (could not detect from VCF headers)")
    else:
        detected_ref = payload.reference

    vcf_source_dir = project_dir / "step2" / "vcf_source"
    vcf_source_dir.mkdir(parents=True, exist_ok=True)
    action = (payload.action or "copy").lower()
    on_conflict = (payload.on_conflict or "skip").lower()

    imported = 0
    skipped = 0
    renamed = 0
    mismatched = []
    seen_samples = {}
    for vcf in vcfs:
        vcf_ref = _detect_vcf_reference(vcf, alias_map)
        if vcf_ref and not _refs_match(vcf_ref, detected_ref, payload.allow_fuzzy_match):
            mismatched.append({"path": str(vcf), "reference": vcf_ref})
            if not payload.allow_mismatch:
                skipped += 1
                continue
        if not vcf_ref:
            mismatched.append({"path": str(vcf), "reference": "unknown"})
            if not payload.allow_mismatch:
                skipped += 1
                continue
        if payload.dedupe:
            sample = _sample_from_vcf(vcf)
            if sample in seen_samples:
                prev = seen_samples[sample]
                if vcf.stat().st_mtime <= prev.stat().st_mtime:
                    skipped += 1
                    continue
            seen_samples[sample] = vcf
        target = vcf_source_dir / vcf.name
        if target.exists():
            if on_conflict == "skip":
                skipped += 1
                continue
            if on_conflict == "rename":
                if payload.prefix_duplicates:
                    prefix = _source_prefix(vcf, source_roots)
                    target = _unique_target(vcf_source_dir, f"{prefix}__{vcf.name}")
                else:
                    target = _unique_target(vcf_source_dir, vcf.name)
                renamed += 1
            else:
                target.unlink()
        if action == "link":
            target.symlink_to(vcf)
        else:
            shutil.copy2(vcf, target)
        imported += 1

    mismatch_report = ""
    if mismatched:
        report_path = project_dir / "step2" / "mismatch_report.csv"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("path,reference\n")
            for row in mismatched:
                f.write(f"\"{row['path']}\",\"{row['reference']}\"\n")
        mismatch_report = str(report_path)

    return {
        "imported": imported,
        "skipped": skipped,
        "renamed": renamed,
        "detected_reference": detected_ref or payload.reference or "",
        "mismatched": len(mismatched),
        "mismatch_report": mismatch_report,
        "total_found": len(vcfs)
    }


@app.post("/api/projects/{project}/upload")
async def project_upload(project: str, files: List[UploadFile] = File(...)):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_project_dirs(project_dir)
    download_dir = project_dir / "download"
    saved = 0
    for f in files:
        if not f.filename:
            continue
        target = download_dir / Path(f.filename).name
        with open(target, "wb") as out:
            while True:
                chunk = await f.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
        saved += 1
    return {"uploaded": saved}


@app.post("/api/projects/{project}/sra/expand")
def sra_expand(project: str, payload: SraRequest):
    _ = project
    expanded = expand_accessions(payload.accessions)
    return {"expanded": expanded}


@app.post("/api/projects/{project}/sra/download")
def sra_download(project: str, payload: SraRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_project_dirs(project_dir)
    expanded = expand_accessions(payload.accessions)
    download_root = project_dir / "download"
    if payload.folder:
        safe = Path(payload.folder).name
        download_root = download_root / safe
    download_root.mkdir(parents=True, exist_ok=True)
    script = build_download_script(download_root, expanded, cfg["sra"]["allow_insecure_https"])
    script_path = download_root / "download_sra.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    job_id = job_manager.start_job(
        name="sra_download",
        command=wrap_cmd(cfg, f"bash {script_path}"),
        cwd=download_root,
        env=build_env(cfg)
    )
    return {"job_id": job_id}


@app.post("/api/projects/{project}/step1/setup")
def step1_setup(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_project_dirs(project_dir)
    download_dir = project_dir / "download"
    step1_dir = project_dir / "step1"

    # Group by sample prefix before _R1/_R2 (scan recursively for subfolders)
    fastqs = list(download_dir.rglob("*.fastq.gz"))
    if not fastqs:
        return {"created": 0, "message": "No FASTQ files found"}

    import re
    created = 0
    for f in fastqs:
        stem = f.name
        match = re.match(r"(.+?)(?:_R?[12])(?:_[^./]+)?\.fastq\.gz$", stem)
        if match:
            sample = match.group(1)
        else:
            sample = stem.split(".")[0]
        sample_dir = step1_dir / sample
        sample_dir.mkdir(parents=True, exist_ok=True)
        target = sample_dir / f.name
        if not target.exists():
            target.symlink_to(f)
            created += 1
    return {"created": created}


@app.post("/api/projects/{project}/step1/run")
def step1_run(project: str, payload: Step1Request):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    step1_dir = project_dir / "step1"
    script_path = step1_dir / "run_step1.sh"
    debug_flag = "--debug" if payload.debug else ""
    ref_arg = f"-t {payload.reference}" if payload.reference else ""
    script_path.write_text(
        "\n".join([
            "#!/bin/bash",
            "set -uo pipefail",
            "FAIL=0",
            "for d in */; do",
            "  if [ -d \"$d\" ]; then",
            "    echo \"== Running step1 in $d ==\"",
            "    cd \"$d\"",
            "    LOG=run_step1.log",
            "    echo \"== Running step1 in $d ==\" | tee -a \"$LOG\"",
            "    echo \"Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)\" | tee -a \"$LOG\"",
            "    if [ \"" + ("1" if payload.debug else "0") + "\" = \"0\" ]; then",
            "      for dir in alignment_*; do",
            "        if [ -d \"$dir\" ]; then",
            "          rm -rf \"$dir\"",
            "        fi",
            "      done",
            "      if [ -d \"unmapped_reads\" ]; then",
            "        rm -rf \"unmapped_reads\"",
            "      fi",
            "      if [ -d \"sourmash\" ]; then",
            "        rm -rf \"sourmash\"",
            "      fi",
            "    fi",
            "    R1=$(ls *_R1*.fastq.gz 2>/dev/null | head -n1 || true)",
            "    R2=$(ls *_R2*.fastq.gz 2>/dev/null | head -n1 || true)",
            "    if [ -z \"$R1\" ]; then R1=$(ls *_1*.fastq.gz 2>/dev/null | head -n1 || true); fi",
            "    if [ -z \"$R2\" ]; then R2=$(ls *_2*.fastq.gz 2>/dev/null | head -n1 || true); fi",
            "    if [ -z \"$R1\" ] || [ -z \"$R2\" ]; then",
            "      echo \"Missing R1/R2 in $d\" | tee -a \"$LOG\"",
            "      cd ..",
            "      continue",
            "    fi",
            f"    vsnp3_step1.py -r1 \"$R1\" -r2 \"$R2\" {ref_arg} {debug_flag} >> \"$LOG\" 2>&1",
            "    STATUS=$?",
            "    if [ \"$STATUS\" -eq 0 ]; then",
            "      echo \"Complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)\" | tee -a \"$LOG\"",
            "    else",
            "      echo \"Error: exit $STATUS\" | tee -a \"$LOG\"",
            "      FAIL=1",
            "    fi",
            "    cd ..",
            "  fi",
            "done",
            "exit $FAIL",
        ]),
        encoding="utf-8"
    )
    script_path.chmod(0o755)
    job_id = job_manager.start_job(
        name="step1",
        command=wrap_cmd(cfg, f"bash {script_path}"),
        cwd=step1_dir,
        env=build_env(cfg)
    )
    (step1_dir / ".step1_job_id").write_text(job_id, encoding="utf-8")
    return {"job_id": job_id}


@app.get("/api/projects/{project}/step1/status")
def step1_status(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step1_dir = project_dir / "step1"
    if not step1_dir.exists():
        raise HTTPException(status_code=404, detail="Step1 directory not found")

    job_id_path = step1_dir / ".step1_job_id"
    job_id = job_id_path.read_text(encoding="utf-8").strip() if job_id_path.exists() else ""
    job = job_manager.get_job(job_id) if job_id else None
    job_status = job["status"] if job else "unknown"

    statuses = []
    for sample_dir in sorted(step1_dir.glob("*")):
        if not sample_dir.is_dir():
            continue
        sample = sample_dir.name
        log_path = sample_dir / "run_step1.log"
        vcf = next(sample_dir.glob("alignment_*/*_filtered_hapall_annotated.vcf"), None)
        nodup = next(sample_dir.glob("alignment_*/*_nodup.bam"), None)
        status = "not_started"
        log_tail = ""
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()[-200:]
                    log_tail = "".join(lines)
            except OSError:
                log_tail = ""
        if vcf and nodup:
            status = "complete"
        elif log_tail:
            if "Traceback" in log_tail or "Error:" in log_tail or "Exception" in log_tail:
                status = "error"
            elif job_status == "running":
                status = "running"
            else:
                status = "unknown"
        statuses.append({
            "sample": sample,
            "status": status,
            "log_path": str(log_path),
            "has_log": log_path.exists(),
            "has_outputs": bool(vcf and nodup),
        })
    return {"job_status": job_status, "samples": statuses}


@app.get("/api/projects/{project}/step1/log")
def step1_log(project: str, sample: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    log_path = project_dir / "step1" / sample / "run_step1.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-400:]
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"sample": sample, "log": "".join(lines)}


@app.post("/api/projects/{project}/step2/setup")
def step2_setup(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    step1_dir = project_dir / "step1"
    step2_dir = project_dir / "step2" / "vcf_source"
    step2_dir.mkdir(parents=True, exist_ok=True)
    # Clean existing VCFs so the source matches the selected workflow
    for existing in step2_dir.glob("*.vcf"):
        try:
            existing.unlink()
        except FileNotFoundError:
            pass
    count = 0
    for vcf in step1_dir.glob("**/*_zc.vcf"):
        target = step2_dir / vcf.name
        if target.exists():
            continue
        target.symlink_to(vcf)
        count += 1
    total = len(list(step2_dir.glob("*_zc.vcf")))
    return {"linked": count, "total": total}


@app.post("/api/projects/{project}/step2/run")
def step2_run(project: str, payload: Step2Request):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    refs = reference_lock(project)["references"]
    if len(refs) > 1:
        raise HTTPException(status_code=400, detail=f"Mixed references detected: {', '.join(refs)}")
    if not payload.reference:
        if len(refs) == 1:
            payload.reference = refs[0]
        else:
            raise HTTPException(status_code=400, detail="Reference type is required for Step 2")
    if len(refs) == 1 and payload.reference != refs[0]:
        raise HTTPException(status_code=400, detail=f"Reference mismatch: expected {refs[0]}")
    step2_dir = project_dir / "step2"
    vcf_source_dir = step2_dir / "vcf_source"
    starting_files = step2_dir / "vcf_starting_files"
    if starting_files.exists():
        shutil.rmtree(starting_files)
    # Clean previous group output folders (keep vcf_source)
    for child in step2_dir.iterdir():
        if child.is_dir() and child.name != "vcf_source":
            shutil.rmtree(child)
    remove_file = step2_dir / "remove_from_analysis.xlsx"
    remove_arg = f" -remove_by_name {remove_file}" if remove_file.exists() else ""
    cmd = f"vsnp3_step2.py -wd {vcf_source_dir} -a -t {payload.reference}{remove_arg}"
    job_id = job_manager.start_job(
        name="step2",
        command=wrap_cmd(cfg, cmd),
        cwd=step2_dir,
        env=build_env(cfg)
    )
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str):
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    log_path = Path(job["log_path"])

    def event_stream():
        last_size = 0
        while True:
            if log_path.exists():
                size = log_path.stat().st_size
                if size > last_size:
                    with open(log_path, "r", encoding="utf-8") as f:
                        f.seek(last_size)
                        chunk = f.read()
                        for line in chunk.splitlines():
                            yield f"data: {line}\n\n"
                    last_size = size
            job = job_manager.get_job(job_id)
            if job and job["status"] in {"succeeded", "failed"}:
                yield f"data: [job:{job['status']}]\n\n"
                break
            time.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/preflight")
def preflight(debug: bool = Query(False)):
    cfg = load_config()
    conda_env = cfg.get("conda_env", "").strip()
    conda_env_path = cfg.get("conda_env_path", "").strip()
    conda_exe = cfg.get("conda_exe", "").strip() or "conda"
    if not conda_env and not conda_env_path:
        raise HTTPException(status_code=400, detail="Conda env is not set")
    if conda_env and (shutil.which(conda_exe) is None and not Path(conda_exe).exists()):
        raise HTTPException(status_code=400, detail=f"Conda executable not found: {conda_exe}")
    check_code = (
        "import importlib.util, json; "
        "mods=['pandas','Bio','pysam']; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "issues=[]; "
        "spec=importlib.util.find_spec('pandas'); "
        "ver=(__import__('pandas').__version__.split('.')[0] if spec else '0'); "
        "issues.append('pandas>=2 not supported') if ver.isdigit() and int(ver) >= 2 else None; "
        "print(json.dumps({'missing': missing, 'checked': mods, 'issues': issues}))"
    )
    debug_code = (
        "import importlib.util, json, sys, site; "
        "mods=['pandas','Bio','pysam']; "
        "result={m: bool(importlib.util.find_spec(m)) for m in mods}; "
        "print(json.dumps({"
        "'executable': sys.executable, "
        "'site': site.getsitepackages(), "
        "'modules': result"
        "}))"
    )
    code = debug_code if debug else check_code
    cmd_list = conda_python_cmd(cfg, code)
    result = subprocess.run(cmd_list, text=True, capture_output=True)
    if debug:
        return {
            "cmd": " ".join(cmd_list),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Preflight failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"Preflight output parse failed: {result.stdout.strip()}")


@app.get("/api/projects/{project}/qc_summary")
def qc_summary(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step1_dir = project_dir / "step1"
    if not step1_dir.exists():
        raise HTTPException(status_code=404, detail="Step1 directory not found")
    code = (
        "import pandas as pd, glob, json, os, sys\n"
        "step1=sys.argv[1]\n"
        "rows=[]\n"
        "for f in glob.glob(os.path.join(step1,'*','*_stats.xlsx')):\n"
        "    try:\n"
        "        df=pd.read_excel(f)\n"
        "    except Exception:\n"
        "        continue\n"
        "    if df.empty:\n"
        "        continue\n"
        "    row=df.iloc[0].to_dict()\n"
        "    row['_file']=f\n"
        "    sample=row.get('sample') or os.path.basename(f).split('_')[0]\n"
        "    row['_sample']=sample\n"
        "    rows.append(row)\n"
        "latest={}\n"
        "for row in rows:\n"
        "    sample=row.get('_sample')\n"
        "    date=row.get('date','') or ''\n"
        "    if sample not in latest:\n"
        "        latest[sample]=row\n"
        "    else:\n"
        "        if date > (latest[sample].get('date','') or ''):\n"
        "            latest[sample]=row\n"
        "print(json.dumps(list(latest.values())))\n"
    )
    cmd_list = conda_python_cmd(cfg, code, [str(step1_dir)])
    result = subprocess.run(cmd_list, text=True, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"QC summary failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="QC summary parse failed")


@app.get("/api/projects/{project}/qc_summary.csv")
def qc_summary_csv(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step1_dir = project_dir / "step1"
    if not step1_dir.exists():
        raise HTTPException(status_code=404, detail="Step1 directory not found")
    code = (
        "import pandas as pd, glob, os, sys\n"
        "step1=sys.argv[1]\n"
        "rows=[]\n"
        "for f in glob.glob(os.path.join(step1,'*','*_stats.xlsx')):\n"
        "    try:\n"
        "        df=pd.read_excel(f)\n"
        "    except Exception:\n"
        "        continue\n"
        "    if df.empty:\n"
        "        continue\n"
        "    row=df.iloc[0]\n"
        "    sample=row.get('sample') or os.path.basename(f).split('_')[0]\n"
        "    row['_sample']=sample\n"
        "    rows.append(row)\n"
        "latest={}\n"
        "for row in rows:\n"
        "    sample=row.get('_sample')\n"
        "    date=row.get('date','') or ''\n"
        "    if sample not in latest:\n"
        "        latest[sample]=row\n"
        "    else:\n"
        "        if date > (latest[sample].get('date','') or ''):\n"
        "            latest[sample]=row\n"
        "if not latest:\n"
        "    print('')\n"
        "else:\n"
        "    out=pd.DataFrame(list(latest.values()))\n"
        "    print(out.to_csv(index=False))\n"
    )
    cmd_list = conda_python_cmd(cfg, code, [str(step1_dir)])
    result = subprocess.run(cmd_list, text=True, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"QC summary CSV failed: {result.stderr.strip()}")
    return Response(content=result.stdout, media_type="text/csv")


@app.get("/api/projects/{project}/qc_summary.xlsx")
def qc_summary_xlsx(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step1_dir = project_dir / "step1"
    if not step1_dir.exists():
        raise HTTPException(status_code=404, detail="Step1 directory not found")
    output_path = step1_dir / "combined_excelworksheets.xlsx"
    code = (
        "import pandas as pd, glob, os, sys\n"
        "step1=sys.argv[1]\n"
        "out=sys.argv[2]\n"
        "rows=[]\n"
        "for f in glob.glob(os.path.join(step1,'*','*_stats.xlsx')):\n"
        "    try:\n"
        "        df=pd.read_excel(f)\n"
        "    except Exception:\n"
        "        continue\n"
        "    if df.empty:\n"
        "        continue\n"
        "    row=df.iloc[0]\n"
        "    sample=row.get('sample') or os.path.basename(f).split('_')[0]\n"
        "    row['_sample']=sample\n"
        "    rows.append(row)\n"
        "latest={}\n"
        "for row in rows:\n"
        "    sample=row.get('_sample')\n"
        "    date=row.get('date','') or ''\n"
        "    if sample not in latest:\n"
        "        latest[sample]=row\n"
        "    else:\n"
        "        if date > (latest[sample].get('date','') or ''):\n"
        "            latest[sample]=row\n"
        "if not latest:\n"
        "    out_df=pd.DataFrame()\n"
        "else:\n"
        "    out_df=pd.DataFrame(list(latest.values()))\n"
        "out_df.to_excel(out, index=False)\n"
    )
    cmd_list = conda_python_cmd(cfg, code, [str(step1_dir), str(output_path)])
    result = subprocess.run(cmd_list, text=True, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"QC summary XLSX failed: {result.stderr.strip()}")
    content = output_path.read_bytes()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.get("/api/projects/{project}/reference_lock")
def reference_lock(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step1_dir = project_dir / "step1"
    if not step1_dir.exists():
        raise HTTPException(status_code=404, detail="Step1 directory not found")
    code = (
        "import pandas as pd, glob, os, sys, json\n"
        "step1=sys.argv[1]\n"
        "rows=[]\n"
        "for f in glob.glob(os.path.join(step1,'*','*_stats.xlsx')):\n"
        "    try:\n"
        "        df=pd.read_excel(f)\n"
        "    except Exception:\n"
        "        continue\n"
        "    if df.empty:\n"
        "        continue\n"
        "    row=df.iloc[0].to_dict()\n"
        "    sample=row.get('sample') or os.path.basename(f).split('_')[0]\n"
        "    row['_sample']=sample\n"
        "    rows.append(row)\n"
        "latest={}\n"
        "for row in rows:\n"
        "    sample=row.get('_sample')\n"
        "    date=row.get('date','') or ''\n"
        "    if sample not in latest:\n"
        "        latest[sample]=row\n"
        "    else:\n"
        "        if date > (latest[sample].get('date','') or ''):\n"
        "            latest[sample]=row\n"
        "refs=set()\n"
        "for row in latest.values():\n"
        "    ref=row.get('Reference') or ''\n"
        "    ref=ref.replace(' Forced','').replace(' by Best Reference','').strip()\n"
        "    if ref:\n"
        "        refs.add(ref)\n"
        "print(json.dumps(sorted(list(refs))))\n"
    )
    cmd_list = conda_python_cmd(cfg, code, [str(step1_dir)])
    result = subprocess.run(cmd_list, text=True, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Reference lock failed: {result.stderr.strip()}")
    try:
        refs = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Reference lock parse failed")
    return {"references": refs}


class ExcludeRequest(BaseModel):
    samples: List[str]


class OpenRequest(BaseModel):
    path: str


@app.get("/api/projects/{project}/step2/vcf_count")
def step2_vcf_count(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    vcf_source_dir = project_dir / "step2" / "vcf_source"
    if not vcf_source_dir.exists():
        return {"count": 0}
    count = len(list(vcf_source_dir.glob("*.vcf"))) + len(list(vcf_source_dir.glob("*.vcf.gz")))
    return {"count": count, "path": str(vcf_source_dir)}


@app.post("/api/projects/{project}/qc_exclude")
def qc_exclude(project: str, payload: ExcludeRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step2_dir = project_dir / "step2"
    step2_dir.mkdir(parents=True, exist_ok=True)
    remove_path = step2_dir / "remove_from_analysis.xlsx"
    code = (
        "import pandas as pd, sys; "
        "out=sys.argv[1]; "
        "samples=sys.argv[2:]; "
        "df=pd.DataFrame(samples); "
        "df.to_excel(out, header=False, index=False)"
    )
    cmd_list = conda_python_cmd(cfg, code, [str(remove_path), *payload.samples])
    result = subprocess.run(cmd_list, text=True, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Exclude list failed: {result.stderr.strip()}")
    return {"remove_file": str(remove_path), "count": len(payload.samples)}


@app.post("/api/projects/{project}/step2/clear")
def step2_clear(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step2_dir = project_dir / "step2"
    vcf_source_dir = step2_dir / "vcf_source"
    if vcf_source_dir.exists():
        shutil.rmtree(vcf_source_dir)
    vcf_source_dir.mkdir(parents=True, exist_ok=True)
    return {"cleared": True}


@app.get("/api/projects/{project}/step1/files")
def step1_files(project: str, sample: str = Query(...)):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    sample_dir = project_dir / "step1" / sample
    if not sample_dir.exists():
        raise HTTPException(status_code=404, detail="Sample not found")
    stats_files = sorted(sample_dir.glob(f"{sample}_*_stats.xlsx"), key=lambda p: p.stat().st_mtime)
    stats_path = str(stats_files[-1]) if stats_files else ""
    bam_files = sorted(sample_dir.glob(f"**/{sample}_nodup.bam"), key=lambda p: p.stat().st_mtime)
    bam_path = str(bam_files[-1]) if bam_files else ""
    align_dir = str(bam_files[-1].parent) if bam_files else ""
    ref_fasta = ""
    if align_dir:
        fasta_files = sorted(Path(align_dir).glob("*.fasta"))
        if fasta_files:
            ref_fasta = str(fasta_files[0])
    return {
        "stats": stats_path,
        "bam": bam_path,
        "alignment_dir": align_dir,
        "reference_fasta": ref_fasta,
        "sample_dir": str(sample_dir)
    }


@app.post("/api/projects/{project}/step1/igv_session")
def step1_igv_session(project: str, payload: OpenRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    target = Path(payload.path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    sample = target.name
    sample_dir = project_dir / "step1" / sample
    if not sample_dir.exists():
        raise HTTPException(status_code=404, detail="Sample not found")
    bam_files = sorted(sample_dir.glob(f"**/{sample}_nodup.bam"), key=lambda p: p.stat().st_mtime)
    if not bam_files:
        raise HTTPException(status_code=404, detail="BAM not found")
    bam_path = bam_files[-1]
    align_dir = bam_path.parent
    fasta_files = sorted(align_dir.glob("*.fasta"))
    if not fasta_files:
        raise HTTPException(status_code=404, detail="Reference FASTA not found")
    ref_fasta = fasta_files[0]
    contig = ""
    try:
        with ref_fasta.open("r", encoding="utf-8") as fh:
            header = fh.readline().strip()
        if header.startswith(">"):
            contig = header[1:].split()[0]
    except Exception:
        contig = ""
    session_path = sample_dir / f"{sample}.igv.xml"
    locus_attr = f' locus="{contig}:1-10000"' if contig else ""
    session_xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>\n"
        f"<Session genome=\"{ref_fasta}\"{locus_attr} hasGeneTrack=\"true\" hasSequenceTrack=\"true\" version=\"8\">\n"
        f"  <Genome path=\"{ref_fasta}\"/>\n"
        "  <Resources>\n"
        f"    <Resource path=\"{ref_fasta}\"/>\n"
        f"    <Resource path=\"{bam_path}\"/>\n"
        "  </Resources>\n"
        "</Session>\n"
    )
    session_path.write_text(session_xml, encoding="utf-8")
    igv_app_path = cfg.get("igv_app_path", "")
    igv_running = _igv_running()
    desired_genome = str(ref_fasta)
    include_genome = not igv_running or _IGV_STATE["genome"] != desired_genome
    if not igv_running:
        _open_igv(session_path, igv_app_path, None)
        _wait_for_igv()
    sent, err, sent_commands, responses = _send_igv_commands(
        ref_fasta,
        bam_path,
        contig,
        include_genome=include_genome,
        retries=10
    )
    if sent and include_genome:
        _IGV_STATE["genome"] = desired_genome
    logger.info("IGV commands sent=%s error=%s commands=%s responses=%s", sent, err, sent_commands, responses)
    return {
        "session": str(session_path),
        "igv_commands_sent": sent,
        "igv_error": err,
        "igv_commands": sent_commands
    }


def _open_igv(session_path: Path, igv_app_path: str = "", batch_path: Optional[Path] = None) -> None:
    if sys.platform.startswith("darwin"):
        igv_path = Path(igv_app_path).expanduser() if igv_app_path else None
        if igv_path and igv_path.exists():
            if igv_path.suffix == ".app" or igv_path.is_dir():
                if batch_path:
                    subprocess.run(["open", "-a", str(igv_path), "--args", "-b", str(batch_path)])
                    return
                subprocess.run(["open", "-a", str(igv_path), str(session_path)])
                return
            if igv_path.is_file() and os.access(igv_path, os.X_OK):
                if batch_path:
                    subprocess.run([str(igv_path), "-b", str(batch_path)])
                else:
                    subprocess.run([str(igv_path), str(session_path)])
                return
        igv_apps = sorted(Path("/Applications").glob("IGV*.app"))
        if igv_apps:
            if batch_path:
                subprocess.run(["open", "-a", str(igv_apps[0]), "--args", "-b", str(batch_path)])
                return
            subprocess.run(["open", "-a", str(igv_apps[0]), str(session_path)])
            return
    if sys.platform.startswith("linux"):
        igv_sh = shutil.which("igv.sh")
        if igv_sh:
            if batch_path:
                subprocess.run([igv_sh, "-b", str(batch_path)])
            else:
                subprocess.run([igv_sh, str(session_path)])
            return
    _open_path(session_path)


def _send_igv_commands(
    ref_fasta: Path,
    bam_path: Path,
    contig: str,
    include_genome: bool = True,
    retries: int = 1
) -> tuple[bool, str, List[str], List[str]]:
    commands = []
    if include_genome:
        commands.append("new")
        commands.append(f"genome {ref_fasta}")
    commands.append(f"load {bam_path}")
    if contig:
        commands.append(f"goto {contig}:1-10000")
    last_error = ""
    for _ in range(retries):
        try:
            responses = []
            with socket.create_connection(("127.0.0.1", 60151), timeout=2) as sock:
                file = sock.makefile("rwb")
                for cmd in commands:
                    file.write((cmd + "\n").encode("utf-8"))
                    file.flush()
                    resp = file.readline().decode("utf-8").strip()
                    responses.append(resp)
                    if resp and "OK" not in resp:
                        last_error = resp
                        return False, last_error, commands, responses
            return True, "", commands, responses
        except OSError as exc:
            last_error = str(exc)
            time.sleep(1.5)
            continue
    return False, last_error, commands, []


def _wait_for_igv(timeout: float = 10.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", 60151), timeout=0.5):
                return
        except OSError:
            time.sleep(0.5)


def _igv_running() -> bool:
    # Prefer checking command server availability; it indicates a live IGV instance.
    try:
        with socket.create_connection(("127.0.0.1", 60151), timeout=0.3):
            return True
    except OSError:
        pass
    if sys.platform.startswith("darwin"):
        try:
            res = subprocess.run(["pgrep", "-f", "IGV"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return res.returncode == 0
        except Exception:
            return False
    if sys.platform.startswith("linux"):
        try:
            res = subprocess.run(["pgrep", "-f", "IGV"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return res.returncode == 0
        except Exception:
            return False
    return False


@app.get("/api/projects/{project}/step2_outputs")
def step2_outputs(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step2_dir = project_dir / "step2"
    if not step2_dir.exists():
        raise HTTPException(status_code=404, detail="Step2 directory not found")
    top = []
    html_files = sorted(step2_dir.glob("*.html"), key=lambda p: p.stat().st_mtime)
    if html_files:
        latest_html = html_files[-1]
        top.append({"label": latest_html.name, "path": str(latest_html), "type": "html"})
    for f in step2_dir.glob("*.zip"):
        top.append({"label": f.name, "path": str(f), "type": "zip"})
    mismatch_report = step2_dir / "mismatch_report.csv"
    if mismatch_report.exists():
        top.append({"label": mismatch_report.name, "path": str(mismatch_report), "type": "csv"})
    top.sort(key=lambda x: x["label"])

    groups = []
    for d in sorted(step2_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name == "vcf_source":
            continue
        files = []
        for f in sorted(d.iterdir()):
            if f.is_file():
                ext = f.suffix.lstrip(".")
                files.append({"label": f.name, "path": str(f), "type": ext or "file"})
        if files:
            groups.append({"name": d.name, "files": files})
    return {"top": top, "groups": groups}


@app.post("/api/bootstrap")
def bootstrap():
    cfg = load_config()
    root_dir = Path(__file__).resolve().parent.parent.parent
    script_path = root_dir / "scripts" / "bootstrap_vsnp3.sh"
    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Bootstrap script not found")
    job_id = job_manager.start_job(
        name="bootstrap",
        command=f"bash {script_path}",
        cwd=root_dir,
        env=build_env(cfg)
    )
    return {"job_id": job_id}


@app.post("/api/projects/{project}/open")
def open_path(project: str, payload: OpenRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    target = Path(payload.path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    _open_path(target)
    return {"opened": str(target)}


def _open_path(target: Path) -> None:
    if sys.platform.startswith("darwin"):
        subprocess.run(["open", str(target)])
        return
    if sys.platform.startswith("linux"):
        opener = shutil.which("xdg-open")
        if opener:
            subprocess.run([opener, str(target)])
            return
    if sys.platform.startswith("win"):
        subprocess.run(["explorer", str(target)])
        return
    subprocess.run(["open", str(target)])


def _detect_vcf_references(vcfs: List[Path], alias_map: Dict[str, str]) -> set:
    refs = set()
    for vcf in vcfs:
        ref = _detect_vcf_reference(vcf, alias_map)
        if ref:
            refs.add(ref)
    return {r for r in refs if r}


def _detect_vcf_reference(vcf: Path, alias_map: Dict[str, str]) -> str:
    try:
        opener = gzip.open if vcf.suffix == ".gz" else open
        with opener(vcf, "rt", encoding="utf-8", errors="ignore") as f:
            for _ in range(200):
                line = f.readline()
                if not line:
                    break
                if line.startswith("##reference="):
                    ref = line.split("=", 1)[1].strip()
                    return _normalize_reference(ref, alias_map)
    except Exception as e:
        print(f"[vcf-ref] Failed to read {vcf}: {e}")
        return ""
    return ""


def _normalize_reference(ref: str, alias_map: Dict[str, str]) -> str:
    ref = ref.replace("file://", "").strip()
    lower_ref = ref.lower()
    for name in alias_map.values():
        if f"/{name.lower()}/" in lower_ref:
            return name
    ref_path = Path(ref)
    candidate = ref_path.stem
    if candidate in alias_map:
        return alias_map[candidate]
    return candidate.replace("_", " ").strip().replace(" ", "_")


def _reference_alias_map(vsnp3_path: Path) -> Dict[str, str]:
    aliases: Dict[str, str] = {}
    refs = list_references(vsnp3_path)
    for ref in refs:
        name = ref.get("name")
        base = Path(ref.get("path", ""))
        if not name or not base.exists():
            continue
        for ext in (".fa", ".fasta", ".fna", ".fas"):
            for fasta in base.rglob(f"*{ext}"):
                aliases[fasta.stem] = name
            if name in aliases.values():
                break
    return aliases


def _refs_match(a: str, b: str, allow_fuzzy: bool) -> bool:
    if allow_fuzzy:
        ca = _canonical_ref_key(a)
        cb = _canonical_ref_key(b)
        return ca == cb or ca.startswith(cb) or cb.startswith(ca)
    return a == b


def _canonical_ref_key(ref: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", ref.lower())


def _unique_target(base_dir: Path, filename: str) -> Path:
    stem = Path(filename).stem
    suffix = "".join(Path(filename).suffixes)
    idx = 1
    while True:
        candidate = base_dir / f"{stem}_import{idx}{suffix}"
        if not candidate.exists():
            return candidate
        idx += 1


def _source_prefix(vcf: Path, source_roots: List[Path]) -> str:
    for root in source_roots:
        try:
            vcf.relative_to(root)
            return root.name or "source"
        except ValueError:
            continue
    return "source"


def _sample_from_vcf(vcf: Path) -> str:
    name = vcf.name
    for suffix in ("_zc.vcf.gz", "_zc.vcf"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return vcf.stem
