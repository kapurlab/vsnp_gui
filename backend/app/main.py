from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pathlib import Path
from typing import List, Optional, Dict
import json
import os
import time
import subprocess
import shutil

from app.config import load_config, save_config
from app.jobs import JobManager
from app.projects import create_project, list_projects, ensure_project_dirs
from app.refs import list_references
from app.sra import expand_accessions, build_download_script

app = FastAPI(title="vSNP GUI API")

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
    sra: Optional[Dict] = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)


class SraRequest(BaseModel):
    accessions: List[str]


class LinkLocalRequest(BaseModel):
    path: str


class Step1Request(BaseModel):
    reference: str
    debug: bool = False


class Step2Request(BaseModel):
    reference: Optional[str] = None


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def get_config():
    return load_config()


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
    script = build_download_script(project_dir / "download", expanded, cfg["sra"]["allow_insecure_https"])
    script_path = project_dir / "download" / "download_sra.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    job_id = job_manager.start_job(
        name="sra_download",
        command=wrap_cmd(cfg, f"bash {script_path}"),
        cwd=project_dir / "download",
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

    # Group by sample prefix before _R1/_R2
    fastqs = list(download_dir.glob("*.fastq.gz"))
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
    script_path.write_text(
        "\n".join([
            "#!/bin/bash",
            "set -euo pipefail",
            "for d in */; do",
            "  if [ -d \"$d\" ]; then",
            "    echo \"== Running step1 in $d ==\"",
            "    cd \"$d\"",
            "    if [ \"" + ("1" if payload.debug else "0") + "\" = \"0\" ]; then",
            "      for dir in alignment_*; do",
            "        if [ -d \"$dir\" ]; then",
            "          rm -rf \"$dir\"",
            "        fi",
            "      done",
            "      if [ -d \"unmapped_reads\" ]; then",
            "        rm -rf \"unmapped_reads\"",
            "      fi",
            "    fi",
            "    R1=$(ls *_R1*.fastq.gz 2>/dev/null | head -n1 || true)",
            "    R2=$(ls *_R2*.fastq.gz 2>/dev/null | head -n1 || true)",
            "    if [ -z \"$R1\" ]; then R1=$(ls *_1*.fastq.gz 2>/dev/null | head -n1 || true); fi",
            "    if [ -z \"$R2\" ]; then R2=$(ls *_2*.fastq.gz 2>/dev/null | head -n1 || true); fi",
            "    if [ -z \"$R1\" ] || [ -z \"$R2\" ]; then",
            "      echo \"Missing R1/R2 in $d\"",
            "      cd ..",
            "      continue",
            "    fi",
            f"    vsnp3_step1.py -r1 \"$R1\" -r2 \"$R2\" -t {payload.reference} {debug_flag}",
            "    cd ..",
            "  fi",
            "done",
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
    return {"job_id": job_id}


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
    for vcf in step1_dir.glob("**/*_filtered_hapall_annotated.vcf"):
        target = step2_dir / vcf.name
        if target.exists():
            continue
        target.symlink_to(vcf)
        count += 1
    total = len(list(step2_dir.glob("*_filtered_hapall_annotated.vcf")))
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


@app.get("/api/projects/{project}/step2_outputs")
def step2_outputs(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step2_dir = project_dir / "step2"
    if not step2_dir.exists():
        raise HTTPException(status_code=404, detail="Step2 directory not found")
    outputs = []
    for f in step2_dir.glob("*.html"):
        outputs.append({"label": f.name, "path": str(f), "type": "html"})
    for f in step2_dir.glob("*.zip"):
        outputs.append({"label": f.name, "path": str(f), "type": "zip"})
    for f in (step2_dir / "name-All").glob("*"):
        if f.is_file():
            ext = f.suffix.lstrip(".")
            outputs.append({"label": f.name, "path": str(f), "type": ext or "file"})
    outputs.sort(key=lambda x: x["label"])
    return outputs


@app.post("/api/projects/{project}/open")
def open_path(project: str, payload: OpenRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    target = Path(payload.path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    subprocess.run(["open", str(target)])
    return {"opened": str(target)}
