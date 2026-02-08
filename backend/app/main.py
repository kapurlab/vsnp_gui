from fastapi import FastAPI, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pathlib import Path
from typing import List, Optional, Dict
import zipfile
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
from app.projects import create_project, list_projects, ensure_project_dirs, archive_project, delete_project, update_project_meta
from app.refs import list_references, get_reference_paths, add_reference_path, remove_reference_path
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
    return {"PATH": f"{vsnp_bin}:{current_path}"}


def wrap_cmd(cfg: Dict, command: str) -> str:
    vsnp3_path = cfg.get("vsnp3_path", "").strip()
    if vsnp3_path:
        return f"PATH=\"{Path(vsnp3_path) / 'bin'}:$PATH\" {command}"
    return command


def _find_vcf_refs_csv(cfg: Dict[str, str]) -> Optional[Path]:
    candidates: List[Path] = []
    vcf_db_folders = cfg.get("vcf_db_folders", [])
    for folder in vcf_db_folders:
        path = Path(folder.get("path")) if isinstance(folder, dict) else Path(str(folder))
        candidates.append(path / "VCF_refs.csv")
        candidates.append(path / "vcf_refs.csv")
        candidates.append(path.parent / "VCF_refs.csv")
        candidates.append(path.parent / "vcf_refs.csv")
    vsnp3_path = Path(cfg.get("vsnp3_path", ""))
    if vsnp3_path:
        candidates.append(vsnp3_path / "VCF_REFS" / "VCF_refs.csv")
        candidates.append(vsnp3_path / "VCF_REFS" / "vcf_refs.csv")
    for c in candidates:
        if c.exists():
            return c
    return None


def _build_tree_label_script(step2_dir: Path, cfg: Dict[str, str]) -> Optional[Path]:
    mapping_csv = _find_vcf_refs_csv(cfg)
    if not mapping_csv:
        return None
    script_path = step2_dir / "_label_trees.py"
    script = f"""\
import csv
import re
from pathlib import Path

mapping_csv = Path({str(mapping_csv)!r})
step2_dir = Path({str(step2_dir)!r})

def short_label(name: str) -> str:
    name = name.strip()
    if name.lower().startswith("lineage "):
        parts = name.split()
        if len(parts) > 1 and parts[1].isdigit():
            return f"L{{parts[1]}}"
    if name.lower().startswith("m. "):
        name = name[3:]
    tokens = re.findall(r"[A-Za-z0-9]+", name)
    if not tokens:
        return name or "REF"
    return "_".join(tokens)

def load_mapping(path: Path):
    out = {{}}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            label, ident = row[0].strip(), row[1].strip()
            if not label or not ident or "number" in label.lower():
                continue
            short = short_label(label)
            out[ident] = f"{{short}}_{{ident}}"
    return out

mapping = load_mapping(mapping_csv)
if not mapping:
    raise SystemExit(0)

tree_files = list(step2_dir.rglob("*.tre")) + list(step2_dir.rglob("*.tree")) + list(step2_dir.rglob("*.nwk"))
for path in tree_files:
    text = path.read_text(encoding="utf-8", errors="ignore")
    for ident, label in mapping.items():
        text = re.sub(rf"\\b{{re.escape(ident)}}(_zc\\.vcf(?:\\.gz)?)\\b", rf"{{label}}\\1", text)
        text = re.sub(rf"\\b{{re.escape(ident)}}\\b", label, text)
    labeled = path.with_name(path.stem + "_labeled" + path.suffix)
    labeled.write_text(text, encoding="utf-8")
"""
    script_path.write_text(script, encoding="utf-8")
    return script_path


def conda_python_cmd(cfg: Dict, code: str, args: Optional[List[str]] = None) -> List[str]:
    args = args or []
    vsnp3_path = cfg.get("vsnp3_path", "").strip()
    if vsnp3_path:
        python_exe = Path(vsnp3_path) / "bin" / "python"
        return [str(python_exe), "-c", code, *args]
    return ["python", "-c", code, *args]


class ConfigUpdate(BaseModel):
    vsnp3_path: Optional[str] = None
    projects_root: Optional[str] = None
    igv_app_path: Optional[str] = None
    bcftools_path: Optional[str] = None
    step1_max_parallel: Optional[int] = None
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
    assemble_unmap: bool = False


class Step2Request(BaseModel):
    reference: Optional[str] = None
    no_filters: bool = False
    qual_threshold: Optional[int] = 150
    n_threshold: Optional[int] = 50
    mq_threshold: Optional[int] = 56
    all_vcf: bool = True
    find_new_filters: bool = False
    hash_groups: bool = False
    show_groups: bool = False
    html_tree: bool = False
    dp: bool = False
    density_threshold: Optional[int] = None
    density_window: Optional[int] = None


class PosthocScanRequest(BaseModel):
    folders: List[str]


class PosthocResolveRequest(BaseModel):
    samples: List[str]
    roots: Optional[List[str]] = None


class VcfEditRequest(BaseModel):
    sample: str
    locus: str
    new_alt: str
    note: Optional[str] = ""
    reason: Optional[str] = ""
    user: Optional[str] = ""


class VcfLookupRequest(BaseModel):
    sample: str
    locus: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _path_is_executable(path_str: str) -> bool:
    p = Path(path_str)
    return p.is_file() and os.access(p, os.X_OK)


@app.get("/api/config")
def get_config():
    cfg = load_config()
    root_dir = Path(__file__).resolve().parent.parent.parent
    cfg["gui_root"] = str(root_dir)
    cfg["_validation"] = {
        "vsnp3_path": Path(cfg.get("vsnp3_path", "")).is_dir() if cfg.get("vsnp3_path", "").strip() else False,
        "projects_root": Path(cfg.get("projects_root", "")).is_dir() if cfg.get("projects_root", "").strip() else False,
        "igv_app_path": Path(cfg.get("igv_app_path", "")).exists() if cfg.get("igv_app_path", "").strip() else None,
        "bcftools_path": _path_is_executable(cfg.get("bcftools_path", "")) if cfg.get("bcftools_path", "").strip() else None,
    }
    return cfg


@app.post("/api/config")
def update_config(update: ConfigUpdate):
    cfg = load_config()
    if update.vsnp3_path is not None:
        cfg["vsnp3_path"] = update.vsnp3_path
    if update.projects_root is not None:
        cfg["projects_root"] = update.projects_root
    if update.igv_app_path is not None:
        cfg["igv_app_path"] = update.igv_app_path
    if update.bcftools_path is not None:
        cfg["bcftools_path"] = update.bcftools_path
    if update.step1_max_parallel is not None:
        cfg["step1_max_parallel"] = update.step1_max_parallel
    if update.sra is not None:
        cfg["sra"].update(update.sra)
    save_config(cfg)
    return cfg


class VcfDbFolderAction(BaseModel):
    action: str  # "add", "remove", "toggle"
    path: Optional[str] = None
    index: Optional[int] = None


@app.get("/api/vcf-db-folders")
def get_vcf_db_folders():
    cfg = load_config()
    return cfg.get("vcf_db_folders", [])


@app.post("/api/vcf-db-folders")
def update_vcf_db_folders(payload: VcfDbFolderAction):
    cfg = load_config()
    folders = cfg.get("vcf_db_folders", [])
    if payload.action == "add":
        if not payload.path:
            raise HTTPException(status_code=400, detail="path is required for add")
        p = str(Path(payload.path).expanduser().resolve())
        if not any(f.get("path") == p for f in folders):
            folders.append({"path": p, "enabled": True})
    elif payload.action == "remove":
        if payload.index is not None and 0 <= payload.index < len(folders):
            folders.pop(payload.index)
        elif payload.path:
            folders = [f for f in folders if f.get("path") != payload.path]
    elif payload.action == "toggle":
        if payload.index is not None and 0 <= payload.index < len(folders):
            folders[payload.index]["enabled"] = not folders[payload.index].get("enabled", True)
    else:
        raise HTTPException(status_code=400, detail="action must be add, remove, or toggle")
    cfg["vcf_db_folders"] = folders
    save_config(cfg)
    return folders


class RefPathRequest(BaseModel):
    path: str


class RefDownloadRequest(BaseModel):
    accession: str
    output_dir: str


class RefOpenFileRequest(BaseModel):
    filename: str


@app.get("/api/references")
def references():
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    return list_references(vsnp3_path)


@app.get("/api/references/paths")
def ref_paths():
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    return {"paths": get_reference_paths(vsnp3_path)}


@app.post("/api/references/paths")
def ref_path_add(payload: RefPathRequest):
    p = Path(payload.path).expanduser().resolve()
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {p}")
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    updated = add_reference_path(vsnp3_path, str(p))
    refs = list_references(vsnp3_path)
    return {"paths": updated, "references": refs}


@app.delete("/api/references/paths")
def ref_path_remove(payload: RefPathRequest):
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    updated = remove_reference_path(vsnp3_path, payload.path)
    refs = list_references(vsnp3_path)
    return {"paths": updated, "references": refs}


@app.post("/api/references/download")
def ref_download(payload: RefDownloadRequest):
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    accession = payload.accession.strip()
    if not accession:
        raise HTTPException(status_code=400, detail="Accession is required")
    output_dir = Path(payload.output_dir).expanduser().resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Cannot create output directory: {e}")
    acc_dir = output_dir / accession
    acc_dir.mkdir(parents=True, exist_ok=True)
    # Copy template files from dependencies, renaming "template" to accession
    template_dir = vsnp3_path / "dependencies"
    for tpl in ["template_define_filter.xlsx", "template_remove_from_analysis.xlsx"]:
        src = template_dir / tpl
        dest_name = tpl.replace("template", accession)
        dest = acc_dir / dest_name
        if src.is_file() and not dest.exists():
            shutil.copy2(str(src), str(dest))
    # Build script: download fasta/gbk/gff
    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        f"cd \"{acc_dir}\"",
        f"vsnp3_download_fasta_gbk_gff_by_acc.py -a {accession} -fbg",
    ]
    script_content = "\n".join(lines) + "\n"
    script_path = acc_dir / "download_ref.sh"
    script_path.write_text(script_content, encoding="utf-8")
    script_path.chmod(0o755)
    # Add parent dir to reference paths
    add_reference_path(vsnp3_path, str(output_dir))
    job_id = job_manager.start_job(
        name="ref_download",
        command=wrap_cmd(cfg, f"bash {script_path}"),
        cwd=acc_dir,
        env=build_env(cfg)
    )
    return {"job_id": job_id, "accession": accession, "target_dir": str(acc_dir)}


@app.get("/api/references/{ref_name}/files")
def ref_files(ref_name: str):
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    refs = list_references(vsnp3_path)
    ref = next((r for r in refs if r["name"] == ref_name), None)
    if not ref:
        raise HTTPException(status_code=404, detail=f"Reference not found: {ref_name}")
    ref_dir = Path(ref["path"])
    define_filter = list(ref_dir.glob("*define_filter*"))
    remove_from = list(ref_dir.glob("*remove_from_analysis*"))
    files = []
    for f in define_filter:
        files.append({"name": f.name, "path": str(f), "exists": f.exists(), "type": "define_filter"})
    for f in remove_from:
        files.append({"name": f.name, "path": str(f), "exists": f.exists(), "type": "remove_from_analysis"})
    return {"ref_name": ref_name, "ref_path": str(ref_dir), "files": files}


@app.post("/api/references/{ref_name}/open-file")
def ref_open_file(ref_name: str, payload: RefOpenFileRequest):
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    refs = list_references(vsnp3_path)
    ref = next((r for r in refs if r["name"] == ref_name), None)
    if not ref:
        raise HTTPException(status_code=404, detail=f"Reference not found: {ref_name}")
    ref_dir = Path(ref["path"])
    target = (ref_dir / payload.filename).resolve()
    if not str(target).startswith(str(ref_dir.resolve())):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    _open_path(target)
    return {"opened": str(target)}


class RefCreateFileRequest(BaseModel):
    file_type: str  # "define_filter" or "remove_from_analysis"


@app.post("/api/references/{ref_name}/create-file")
def ref_create_file(ref_name: str, payload: RefCreateFileRequest):
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    refs = list_references(vsnp3_path)
    ref = next((r for r in refs if r["name"] == ref_name), None)
    if not ref:
        raise HTTPException(status_code=404, detail=f"Reference not found: {ref_name}")
    ref_dir = Path(ref["path"])
    template_dir = vsnp3_path / "dependencies"
    if payload.file_type == "define_filter":
        template_name = "template_define_filter.xlsx"
        dest_name = f"{ref_name}_define_filter.xlsx"
    elif payload.file_type == "remove_from_analysis":
        template_name = "template_remove_from_analysis.xlsx"
        dest_name = f"{ref_name}_remove_from_analysis.xlsx"
    else:
        raise HTTPException(status_code=400, detail="file_type must be define_filter or remove_from_analysis")
    dest = ref_dir / dest_name
    if dest.exists():
        raise HTTPException(status_code=400, detail=f"File already exists: {dest_name}")
    template = template_dir / template_name
    if template.exists():
        shutil.copy2(template, dest)
    else:
        # Create an empty xlsx if no template exists
        code = (
            "import pandas as pd, sys; "
            "pd.DataFrame().to_excel(sys.argv[1], index=False)"
        )
        cmd_list = conda_python_cmd(cfg, code, [str(dest)])
        result = subprocess.run(cmd_list, text=True, capture_output=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to create file: {result.stderr.strip()}")
    return {"created": str(dest), "name": dest_name}


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

    step1_dir = project_dir / "step1"
    resolved_vcfs = []
    vcf_sample_override = {}
    for vcf in vcfs:
        try:
            if step1_dir.exists() and str(vcf.resolve()).startswith(str(step1_dir.resolve())):
                sample = _sample_from_vcf(vcf)
                sample_dir = step1_dir / sample
                patched = _find_patched_vcf(sample_dir, sample, vcf)
                if patched:
                    resolved_vcfs.append(patched)
                    vcf_sample_override[patched] = sample
                    continue
        except FileNotFoundError:
            pass
        resolved_vcfs.append(vcf)
    vcfs = resolved_vcfs

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
            sample = vcf_sample_override.get(vcf, _sample_from_vcf(vcf))
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

    edited_in_source = _edited_samples_in_dir(vcf_source_dir)
    _write_step2_edit_summary(vcf_source_dir.parent, edited_in_source)

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
    assemble_unmap_flag = "-assemble_unmap" if payload.assemble_unmap else ""
    ref_arg = f"-t {payload.reference}" if payload.reference else ""
    if payload.reference:
        update_project_meta(project_dir, {
            "reference": payload.reference,
            "display_name": f"{project}_{payload.reference}"
        })
    max_parallel = cfg.get("step1_max_parallel", 1) or 1
    try:
        max_parallel = max(1, int(max_parallel))
    except (TypeError, ValueError):
        max_parallel = 1
    script_path.write_text(
        "\n".join([
            "#!/bin/bash",
            "set -uo pipefail",
            "FAIL=0",
            f"MAX_PARALLEL={max_parallel}",
            "OVERALL_START=$(date +%s)",
            "OVERALL_LOG=\"step1_run_summary.log\"",
            "echo \"Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> \"$OVERALL_LOG\"",
            "run_sample() {",
            "  local d=\"$1\"",
            "  if [ ! -d \"$d\" ]; then",
            "    return 0",
            "  fi",
            "  echo \"== Running step1 in $d ==\"",
            "  cd \"$d\"",
            "  LOG=run_step1.log",
            "  echo \"== Running step1 in $d ==\" | tee -a \"$LOG\"",
            "  START_TS=$(date +%s)",
            "  echo \"Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)\" | tee -a \"$LOG\"",
            "  if [ \"" + ("1" if payload.debug else "0") + "\" = \"0\" ]; then",
            "    for dir in alignment_*; do",
            "      if [ -d \"$dir\" ]; then",
            "        rm -rf \"$dir\"",
            "      fi",
            "    done",
            "    if [ -d \"unmapped_reads\" ]; then",
            "      rm -rf \"unmapped_reads\"",
            "    fi",
            "    if [ -d \"sourmash\" ]; then",
            "      rm -rf \"sourmash\"",
            "    fi",
            "  fi",
            "  R1=$(ls *_R1*.fastq.gz 2>/dev/null | head -n1 || true)",
            "  R2=$(ls *_R2*.fastq.gz 2>/dev/null | head -n1 || true)",
            "  if [ -z \"$R1\" ]; then R1=$(ls *_1*.fastq.gz 2>/dev/null | head -n1 || true); fi",
            "  if [ -z \"$R2\" ]; then R2=$(ls *_2*.fastq.gz 2>/dev/null | head -n1 || true); fi",
            "  if [ -z \"$R1\" ] || [ -z \"$R2\" ]; then",
            "    echo \"Missing R1/R2 in $d\" | tee -a \"$LOG\"",
            "    cd ..",
            "    return 0",
            "  fi",
            f"  vsnp3_step1.py -r1 \"$R1\" -r2 \"$R2\" {ref_arg} {debug_flag} {assemble_unmap_flag} >> \"$LOG\" 2>&1",
            "  STATUS=$?",
            "  if [ \"$STATUS\" -eq 0 ]; then",
            "    END_TS=$(date +%s)",
            "    DURATION=$((END_TS-START_TS))",
            "    echo \"Complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)\" | tee -a \"$LOG\"",
            "    echo \"Duration: ${DURATION}s\" | tee -a \"$LOG\"",
            "  else",
            "    echo \"Error: exit $STATUS\" | tee -a \"$LOG\"",
            "    FAIL=1",
            "  fi",
            "  cd ..",
            "  return $STATUS",
            "}",
            "pids=()",
            "for d in */; do",
            "  run_sample \"$d\" &",
            "  pids+=(\"$!\")",
            "  if [ ${#pids[@]} -ge \"$MAX_PARALLEL\" ]; then",
            "    pid=${pids[0]}",
            "    wait \"$pid\" || FAIL=1",
            "    pids=(\"${pids[@]:1}\")",
            "  fi",
            "done",
            "for pid in \"${pids[@]}\"; do",
            "  wait \"$pid\" || FAIL=1",
            "done",
            "OVERALL_END=$(date +%s)",
            "OVERALL_DURATION=$((OVERALL_END-OVERALL_START))",
            "echo \"End: $(date -u +%Y-%m-%dT%H:%M:%SZ)\" >> \"$OVERALL_LOG\"",
            "echo \"Duration: ${OVERALL_DURATION}s\" >> \"$OVERALL_LOG\"",
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
    for existing in step2_dir.glob("*.vcf*"):
        try:
            existing.unlink()
        except FileNotFoundError:
            pass
    count = 0
    edited_samples = []
    for sample_dir in sorted(step1_dir.glob("*")):
        if not sample_dir.is_dir():
            continue
        sample = sample_dir.name
        vcf_candidates = sorted(sample_dir.glob("**/*_zc.vcf*"), key=lambda p: p.stat().st_mtime)
        if not vcf_candidates:
            continue
        source_vcf = vcf_candidates[-1]
        patched_vcf = _find_patched_vcf(sample_dir, sample, source_vcf)
        chosen_vcf = patched_vcf or source_vcf
        target_name = _target_name_for_vcf(source_vcf, chosen_vcf)
        target = step2_dir / target_name
        if target.exists():
            continue
        target.symlink_to(chosen_vcf)
        count += 1
        if patched_vcf:
            edited_samples.append(sample)
    _write_step2_edit_summary(step2_dir.parent, edited_samples)
    total = len(list(step2_dir.glob("*_zc.vcf"))) + len(list(step2_dir.glob("*_zc.vcf.gz")))
    return {"linked": count, "total": total, "edited": len(set(edited_samples))}


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
    _write_step2_edit_summary(step2_dir, _edited_samples_in_dir(vcf_source_dir))
    # Build Step 2 command with options
    step2_flags = []
    if payload.all_vcf:
        step2_flags.append("-a")
    if payload.no_filters:
        step2_flags.append("-n")
    if payload.find_new_filters:
        step2_flags.append("-i")
    if payload.hash_groups:
        step2_flags.append("-hash")
    if payload.show_groups:
        step2_flags.append("--show_groups")
    if payload.html_tree:
        step2_flags.append("-html_tree")
    if payload.dp:
        step2_flags.append("-dp")
    if payload.qual_threshold is not None and payload.qual_threshold != 150:
        step2_flags.append(f"-w {payload.qual_threshold}")
    if payload.n_threshold is not None and payload.n_threshold != 50:
        step2_flags.append(f"-x {payload.n_threshold}")
    if payload.mq_threshold is not None and payload.mq_threshold != 56:
        step2_flags.append(f"-y {payload.mq_threshold}")
    if payload.density_threshold is not None:
        step2_flags.append(f"--density_threshold {payload.density_threshold}")
    if payload.density_window is not None:
        step2_flags.append(f"--density_window {payload.density_window}")
    flags_str = " ".join(step2_flags)
    cmd = f"vsnp3_step2.py -wd {vcf_source_dir} {flags_str} -t {payload.reference}{remove_arg}"
    label_script = _build_tree_label_script(step2_dir, cfg)
    if label_script:
        cmd = f"{cmd} && python {label_script}"
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
    vsnp3_path = cfg.get("vsnp3_path", "").strip()
    if not vsnp3_path:
        raise HTTPException(status_code=400, detail="vSNP3 path is not set")
    vsnp3_dir = Path(vsnp3_path)
    if not vsnp3_dir.is_dir():
        raise HTTPException(status_code=400, detail=f"vSNP3 path not found: {vsnp3_path}")
    check_code = (
        "import importlib.util, json; "
        "mods=['pandas','Bio']; "
        "missing=[m for m in mods if importlib.util.find_spec(m) is None]; "
        "issues=[]; "
        "print(json.dumps({'missing': missing, 'checked': mods, 'issues': issues}))"
    )
    debug_code = (
        "import importlib.util, json, sys, site; "
        "mods=['pandas','Bio']; "
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


@app.post("/api/posthoc/step1/scan")
def posthoc_step1_scan(payload: PosthocScanRequest):
    cfg = load_config()
    folders = [str(Path(p).expanduser()) for p in payload.folders]
    if not folders:
        return []
    code = (
        "import pandas as pd, glob, json, os, sys\n"
        "folders=sys.argv[1:]\n"
        "rows=[]\n"
        "for step1 in folders:\n"
        "    direct=glob.glob(os.path.join(step1,'*_stats.xlsx'))\n"
        "    nested=glob.glob(os.path.join(step1,'*','*_stats.xlsx'))\n"
        "    for f in direct + nested:\n"
        "        try:\n"
        "            df=pd.read_excel(f)\n"
        "        except Exception:\n"
        "            continue\n"
        "        if df.empty:\n"
        "            continue\n"
        "        row=df.iloc[0].to_dict()\n"
        "        row['_file']=f\n"
        "        sample=row.get('sample') or os.path.basename(f).split('_')[0]\n"
        "        row['_sample']=sample\n"
        "        sample_dir=os.path.dirname(f)\n"
        "        row['_sample_dir']=sample_dir\n"
        "        step1_dir=os.path.dirname(sample_dir)\n"
        "        row['_step1_dir']=step1_dir\n"
        "        row['_project']=os.path.basename(os.path.dirname(step1_dir))\n"
        "        edits_dir=os.path.join(sample_dir,'vcf_edits')\n"
        "        patched=''\n"
        "        if os.path.isdir(edits_dir):\n"
        "            candidates=[c for c in glob.glob(os.path.join(edits_dir,'*.vcf*')) if not c.endswith('.tbi')]\n"
        "            if candidates:\n"
        "                candidates=sorted(candidates, key=os.path.getmtime)\n"
        "                patched=candidates[-1]\n"
        "        edit_log=os.path.join(edits_dir, f\"{sample}_patchlog.jsonl\")\n"
        "        row['_patched_vcf']=patched\n"
        "        row['_edit_log']=edit_log if os.path.exists(edit_log) else ''\n"
        "        row['_edited']=bool(patched) and os.path.exists(edit_log)\n"
        "        rows.append(row)\n"
        "latest={}\n"
        "for row in rows:\n"
        "    key=(row.get('_project'), row.get('_sample'))\n"
        "    date=row.get('date','') or ''\n"
        "    if key not in latest:\n"
        "        latest[key]=row\n"
        "    else:\n"
        "        if date > (latest[key].get('date','') or ''):\n"
        "            latest[key]=row\n"
        "print(json.dumps(list(latest.values())))\n"
    )
    cmd_list = conda_python_cmd(cfg, code, folders)
    result = subprocess.run(cmd_list, text=True, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Post-hoc scan failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Post-hoc scan parse failed")


@app.post("/api/posthoc/step1/resolve_samples")
def posthoc_resolve_samples(payload: PosthocResolveRequest):
    cfg = load_config()
    samples = [s.strip() for s in payload.samples if s and s.strip()]
    if not samples:
        raise HTTPException(status_code=400, detail="Samples are required")
    roots = payload.roots or []
    if roots:
        root_dirs = [Path(r).expanduser() for r in roots]
    else:
        projects_root = Path(cfg["projects_root"])
        root_dirs = list(projects_root.glob("*/step1"))
    found = []
    missing = []
    for sample in samples:
        hit = None
        for root in root_dirs:
            candidate = root / sample
            if candidate.is_dir():
                hit = candidate
                break
        if hit:
            step1_dir = hit.parent
            found.append({
                "sample": sample,
                "sample_dir": str(hit),
                "step1_dir": str(step1_dir),
                "project": step1_dir.parent.name
            })
        else:
            missing.append(sample)
    return {"found": found, "missing": missing}


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
    if len(refs) == 1:
        update_project_meta(project_dir, {
            "reference": refs[0],
            "display_name": f"{project}_{refs[0]}"
        })
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
    vcfs = list(vcf_source_dir.glob("*.vcf")) + list(vcf_source_dir.glob("*.vcf.gz"))
    edited_samples = set()
    for vcf in vcfs:
        if _vcf_is_edited(vcf):
            edited_samples.add(_sample_from_vcf(vcf))
    return {
        "count": len(vcfs),
        "path": str(vcf_source_dir),
        "edited_count": len(edited_samples)
    }


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
    edited_summary = step2_dir / "edited_samples.json"
    if edited_summary.exists():
        edited_summary.unlink()
    return {"cleared": True}


def _resolve_sample_dir(step1_dir: Path, sample: str) -> Optional[Path]:
    """Resolve sample name to its step1 subdirectory.

    Tries exact match first, then falls back to matching directories
    whose name starts with ``sample_`` (e.g. sample ``13-1941-6``
    matches directory ``13-1941-6_S4_L001``).
    """
    exact = step1_dir / sample
    if exact.is_dir():
        return exact
    candidates = sorted(
        d for d in step1_dir.iterdir()
        if d.is_dir() and d.name.startswith(f"{sample}_")
    )
    return candidates[0] if candidates else None


@app.get("/api/projects/{project}/step1/files")
def step1_files(project: str, sample: str = Query(...)):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step1_dir = project_dir / "step1"
    sample_dir = _resolve_sample_dir(step1_dir, sample) if step1_dir.is_dir() else None
    if not sample_dir:
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
    vcf_candidates = sorted(sample_dir.glob(f"**/{sample}*zc.vcf*"), key=lambda p: p.stat().st_mtime)
    source_vcf = vcf_candidates[-1] if vcf_candidates else None
    patched_vcf = _find_patched_vcf(sample_dir, sample, source_vcf)
    edit_log = _edit_log_path(sample_dir, sample)
    return {
        "stats": stats_path,
        "bam": bam_path,
        "alignment_dir": align_dir,
        "reference_fasta": ref_fasta,
        "sample_dir": str(sample_dir),
        "source_vcf": str(source_vcf) if source_vcf else "",
        "patched_vcf": str(patched_vcf) if patched_vcf else "",
        "edit_log": str(edit_log) if edit_log.exists() else "",
        "edited": bool(patched_vcf) and edit_log.exists()
    }


@app.get("/api/projects/{project}/step1/edits")
def step1_edits(project: str):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step1_dir = project_dir / "step1"
    if not step1_dir.exists():
        raise HTTPException(status_code=404, detail="Step1 directory not found")
    edits = {}
    for sample_dir in sorted(step1_dir.glob("*")):
        if not sample_dir.is_dir():
            continue
        sample = sample_dir.name
        vcf_candidates = sorted(sample_dir.glob(f"**/{sample}*zc.vcf*"), key=lambda p: p.stat().st_mtime)
        source_vcf = vcf_candidates[-1] if vcf_candidates else None
        patched_vcf = _find_patched_vcf(sample_dir, sample, source_vcf)
        edit_log = _edit_log_path(sample_dir, sample)
        if patched_vcf or edit_log.exists():
            edits[sample] = {
                "patched_vcf": str(patched_vcf) if patched_vcf else "",
                "edit_log": str(edit_log) if edit_log.exists() else "",
                "edited": bool(patched_vcf) and edit_log.exists()
            }
    return edits


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
    step1_dir = project_dir / "step1"
    sample_dir = _resolve_sample_dir(step1_dir, sample) if step1_dir.is_dir() else None
    if not sample_dir:
        raise HTTPException(status_code=404, detail="Sample not found")
    bam_files = sorted(sample_dir.glob(f"**/*_nodup.bam"), key=lambda p: p.stat().st_mtime)
    if not bam_files:
        raise HTTPException(status_code=404, detail="BAM not found")
    bam_path = bam_files[-1]
    align_dir = bam_path.parent
    # Clean up legacy per-alignment .genome files now that we use shared project genomes.
    try:
        for legacy_genome in align_dir.glob("*.genome"):
            legacy_genome.unlink(missing_ok=True)
    except Exception:
        pass
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
    genome_id = ref_fasta.stem
    genome_store_dir = project_dir / ".igv_genomes"
    genome_path = _ensure_genome_file(ref_fasta, genome_store_dir, genome_id)
    gbk_path = _find_gbk_for_fasta(ref_fasta)
    locus_attr = f' locus="{contig}:1-10000"' if contig else ""
    session_lines = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>\n",
        f"<Session genome=\"{genome_path}\"{locus_attr} hasGeneTrack=\"true\" hasSequenceTrack=\"true\" version=\"8\">\n",
        f"  <Genome path=\"{genome_path}\"/>\n",
        "  <Resources>\n",
        f"    <Resource path=\"{ref_fasta}\"/>\n",
        f"    <Resource path=\"{bam_path}\"/>\n",
    ]
    if gbk_path:
        session_lines.append(f"    <Resource path=\"{gbk_path}\"/>\n")
    session_lines.extend([
        "  </Resources>\n",
        "</Session>\n",
    ])
    session_xml = "".join(session_lines)
    session_path.write_text(session_xml, encoding="utf-8")
    igv_app_path = cfg.get("igv_app_path", "")
    igv_status = _igv_running()
    desired_genome = genome_id
    include_genome = not igv_status or _IGV_STATE["genome"] != desired_genome
    if not igv_status:
        _open_igv(session_path, igv_app_path, None)
        _wait_for_igv(timeout=15.0)
    elif igv_status == "process":
        _wait_for_igv(timeout=15.0)
    send_goto = include_genome
    # Only load annotation on genome switch to avoid duplicate tracks.
    extra_tracks = [gbk_path] if (gbk_path and include_genome) else []
    sent, err, sent_commands, responses = _send_igv_commands(
        genome_path,
        bam_path,
        contig,
        extra_tracks=extra_tracks,
        include_genome=include_genome,
        send_goto=send_goto,
        retries=10,
        wait_before_genome=3.0 if not igv_status else 0.0,
        repeat_genome=not igv_status
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


@app.post("/api/posthoc/igv_session")
def posthoc_igv_session(payload: OpenRequest):
    cfg = load_config()
    projects_root = Path(cfg["projects_root"]).resolve()
    target = Path(payload.path).resolve()
    if not str(target).startswith(str(projects_root)):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    sample = target.name
    sample_dir = target
    step1_dir = sample_dir.parent
    project_dir = step1_dir.parent
    bam_files = sorted(sample_dir.glob(f"**/*_nodup.bam"), key=lambda p: p.stat().st_mtime)
    if not bam_files:
        raise HTTPException(status_code=404, detail="BAM not found")
    bam_path = bam_files[-1]
    align_dir = bam_path.parent
    try:
        for legacy_genome in align_dir.glob("*.genome"):
            legacy_genome.unlink(missing_ok=True)
    except Exception:
        pass
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
    genome_id = ref_fasta.stem
    genome_store_dir = project_dir / ".igv_genomes"
    genome_path = _ensure_genome_file(ref_fasta, genome_store_dir, genome_id)
    gbk_path = _find_gbk_for_fasta(ref_fasta)
    locus_attr = f' locus="{contig}:1-10000"' if contig else ""
    session_lines = [
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>\n",
        f"<Session genome=\"{genome_path}\"{locus_attr} hasGeneTrack=\"true\" hasSequenceTrack=\"true\" version=\"8\">\n",
        f"  <Genome path=\"{genome_path}\"/>\n",
        "  <Resources>\n",
        f"    <Resource path=\"{ref_fasta}\"/>\n",
        f"    <Resource path=\"{bam_path}\"/>\n",
    ]
    if gbk_path:
        session_lines.append(f"    <Resource path=\"{gbk_path}\"/>\n")
    session_lines.extend([
        "  </Resources>\n",
        "</Session>\n",
    ])
    session_xml = "".join(session_lines)
    session_path.write_text(session_xml, encoding="utf-8")
    igv_app_path = cfg.get("igv_app_path", "")
    igv_status = _igv_running()
    desired_genome = genome_id
    include_genome = not igv_status or _IGV_STATE["genome"] != desired_genome
    if not igv_status:
        _open_igv(session_path, igv_app_path, None)
        _wait_for_igv(timeout=15.0)
    elif igv_status == "process":
        _wait_for_igv(timeout=15.0)
    send_goto = include_genome
    extra_tracks = [gbk_path] if (gbk_path and include_genome) else []
    sent, err, sent_commands, responses = _send_igv_commands(
        genome_path,
        bam_path,
        contig,
        extra_tracks=extra_tracks,
        include_genome=include_genome,
        send_goto=send_goto,
        retries=10,
        wait_before_genome=3.0 if not igv_status else 0.0,
        repeat_genome=not igv_status
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
    genome_path: Path,
    bam_path: Path,
    contig: str,
    include_genome: bool = True,
    send_goto: bool = True,
    extra_tracks: Optional[List[Path]] = None,
    retries: int = 1,
    wait_before_genome: float = 0.0,
    repeat_genome: bool = False
) -> tuple[bool, str, List[str], List[str]]:
    commands: List[str] = []
    responses: List[str] = []

    def _send(payload_commands: List[str]) -> tuple[bool, str]:
        last_error = ""
        for _ in range(retries):
            try:
                with socket.create_connection(("127.0.0.1", 60151), timeout=1) as sock:
                    payload = "\n".join(payload_commands) + "\n"
                    sock.sendall(payload.encode("utf-8"))
                return True, ""
            except OSError as exc:
                last_error = str(exc)
                time.sleep(1.5)
                continue
        return False, last_error

    # Phase 1: set genome (separate session avoids IGV dropping subsequent commands).
    if include_genome:
        if wait_before_genome > 0:
            time.sleep(wait_before_genome)
        genome_cmds = [f"genome {genome_path}"]
        commands.extend(genome_cmds)
        ok, err = _send(genome_cmds)
        if not ok:
            return False, err, commands, []
        time.sleep(1.5)
        if repeat_genome:
            commands.extend(genome_cmds)
            ok, err = _send(genome_cmds)
            if not ok:
                return False, err, commands, []
            time.sleep(1.5)

    # Phase 2: load BAM and go to locus.
    load_cmds = [f"load {bam_path}"]
    if extra_tracks:
        for track in extra_tracks:
            load_cmds.append(f"load {track}")
    if contig and send_goto:
        load_cmds.append(f"goto {contig}:1-10000")
    commands.extend(load_cmds)
    ok, err = _send(load_cmds)
    if not ok:
        return False, err, commands, []
    return True, "", commands, responses


def _ensure_genome_file(ref_fasta: Path, out_dir: Path, genome_id: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    genome_path = out_dir / f"{genome_id}.genome"
    props_lines = [
        f"id={genome_id}",
        f"name={genome_id}",
        "fasta=true",
        f"sequenceLocation={ref_fasta}"
    ]
    props = "\n".join(props_lines) + "\n"

    def needs_write() -> bool:
        if not genome_path.exists():
            return True
        try:
            if genome_path.stat().st_mtime < ref_fasta.stat().st_mtime:
                return True
        except OSError:
            return True
        try:
            with zipfile.ZipFile(genome_path, "r") as zf:
                existing = zf.read("property.txt").decode("utf-8")
            return existing != props
        except Exception:
            return True

    if needs_write():
        with zipfile.ZipFile(genome_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("property.txt", props)
    return genome_path


def _find_gbk_for_fasta(ref_fasta: Path) -> Optional[Path]:
    candidates: List[Path] = []
    candidates.extend(ref_fasta.parent.glob("*.gbk"))
    default_test = Path.home() / "vsnp3_test_dataset" / "vsnp_dependencies"
    if default_test.exists():
        candidates.extend(default_test.rglob("*.gbk"))
    if not candidates:
        return None
    stem = ref_fasta.stem
    for c in candidates:
        cstem = c.stem
        if cstem == stem or cstem.startswith(stem) or stem.startswith(cstem):
            return c
    return candidates[0]


def _wait_for_igv(timeout: float = 10.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection(("127.0.0.1", 60151), timeout=0.5):
                return
        except OSError:
            time.sleep(0.5)


def _igv_port_open() -> bool:
    """Check if the IGV command port (60151) is accepting connections."""
    try:
        with socket.create_connection(("127.0.0.1", 60151), timeout=0.3):
            return True
    except OSError:
        return False


def _igv_running() -> str:
    """Check IGV status. Returns 'port' if port is open, 'process' if only
    the process is detected (port not ready yet), or '' if not running."""
    if _igv_port_open():
        return "port"
    if sys.platform.startswith("darwin") or sys.platform.startswith("linux"):
        try:
            res = subprocess.run(["pgrep", "-f", "IGV"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                return "process"
        except Exception:
            pass
    return ""


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


@app.post("/api/posthoc/open")
def posthoc_open_path(payload: OpenRequest):
    cfg = load_config()
    projects_root = Path(cfg["projects_root"]).resolve()
    target = Path(payload.path).resolve()
    if not str(target).startswith(str(projects_root)):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    _open_path(target)
    return {"opened": str(target)}


@app.post("/api/projects/{project}/vcf_edit")
def vcf_edit(project: str, payload: VcfEditRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    step1_dir = project_dir / "step1"
    sample_dir = _resolve_sample_dir(step1_dir, payload.sample) if step1_dir.is_dir() else None
    if not sample_dir:
        raise HTTPException(status_code=404, detail="Sample not found")

    bcftools = _resolve_bcftools(cfg)
    if not bcftools or not Path(bcftools).exists():
        raise HTTPException(status_code=400, detail="bcftools not configured or not found")

    # Find source VCF (prefer *_zc.vcf[.gz])
    source_vcf = _find_source_vcf(sample_dir, payload.sample)
    if not source_vcf:
        raise HTTPException(status_code=404, detail="Source VCF not found")

    edits_dir = sample_dir / "vcf_edits"
    edits_dir.mkdir(parents=True, exist_ok=True)
    expected_patched = _expected_patched_vcf_path(edits_dir, source_vcf)
    legacy_patched = edits_dir / f"{payload.sample}_patched.vcf.gz"
    if legacy_patched.exists() and not expected_patched.exists():
        patched_vcf = legacy_patched
    else:
        patched_vcf = expected_patched
    if ":" not in payload.locus:
        raise HTTPException(status_code=400, detail="Locus must be in contig:pos format")
    contig, pos_str = payload.locus.split(":", 1)
    contig = contig.strip()
    if not contig:
        raise HTTPException(status_code=400, detail="Contig is required")
    try:
        pos = int(pos_str.replace(",", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Position must be an integer")
    new_alt = payload.new_alt.strip().upper()
    if not new_alt or not all(c in "ACGTN" for c in new_alt):
        raise HTTPException(status_code=400, detail="ALT must be A/C/G/T/N (no commas)")
    reason = (payload.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Reason is required")

    base_vcf = patched_vcf if patched_vcf.exists() else (legacy_patched if legacy_patched.exists() else source_vcf)
    if base_vcf and base_vcf != source_vcf:
        if not _scan_vcf_for_locus(base_vcf, contig, pos):
            base_vcf = source_vcf

    tbi_path = patched_vcf.with_suffix(patched_vcf.suffix + ".tbi")
    if tbi_path.exists():
        tbi_path.unlink()
    tmp_vcf = edits_dir / f".{payload.sample}_edit_{int(time.time())}.vcf"
    try:
        edit_meta = _rewrite_vcf_with_alt(base_vcf, tmp_vcf, contig, pos, new_alt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    compress = subprocess.run([bcftools, "view", "-Oz", "-o", str(patched_vcf), str(tmp_vcf)], text=True, capture_output=True)
    try:
        tmp_vcf.unlink()
    except OSError:
        pass
    if compress.returncode != 0:
        raise HTTPException(status_code=500, detail=f"VCF compression failed: {compress.stderr.strip()}")

    # Index patched VCF
    idx = subprocess.run([bcftools, "index", "-t", str(patched_vcf)], text=True, capture_output=True)
    if idx.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Index failed: {idx.stderr.strip()}")

    edit_meta = edit_meta or {}

    log_path = _edit_log_path(sample_dir, payload.sample)
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": payload.user or "",
        "project": project,
        "sample": payload.sample,
        "reference": contig,
        "locus": f"{contig}:{pos}",
        "original": {
            "ref": edit_meta.get("old_ref", ""),
            "alt": edit_meta.get("old_alt", ""),
            "dp": edit_meta.get("old_dp", None),
            "ad": edit_meta.get("old_ad", None),
        },
        "updated": {
            "ref": edit_meta.get("old_ref", ""),
            "alt": new_alt,
            "note": payload.note or "",
            "reason": reason
        },
        "source_vcf": str(source_vcf),
        "base_vcf": str(base_vcf),
        "patched_vcf": str(patched_vcf)
    }
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")

    return {
        "patched_vcf": str(patched_vcf),
        "log": str(log_path),
        "entry": entry
    }


@app.post("/api/projects/{project}/vcf_lookup")
def vcf_lookup(project: str, payload: VcfLookupRequest):
    cfg = load_config()
    project_dir = Path(cfg["projects_root"]) / project
    sample = (payload.sample or "").strip()
    if not sample:
        raise HTTPException(status_code=400, detail="Sample is required")
    step1_dir = project_dir / "step1"
    sample_dir = _resolve_sample_dir(step1_dir, sample) if step1_dir.is_dir() else None
    if not sample_dir:
        raise HTTPException(status_code=404, detail="Sample not found")
    if ":" not in payload.locus:
        raise HTTPException(status_code=400, detail="Locus must be in contig:pos format")
    contig, pos_str = payload.locus.split(":", 1)
    contig = contig.strip()
    if not contig:
        raise HTTPException(status_code=400, detail="Contig is required")
    try:
        pos = int(pos_str.replace(",", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Position must be an integer")
    source_vcf = _find_source_vcf(sample_dir, sample)
    if not source_vcf:
        raise HTTPException(status_code=404, detail="Source VCF not found")
    edits_dir = sample_dir / "vcf_edits"
    patched_vcf = _find_patched_vcf(sample_dir, sample, source_vcf)
    base_vcf = patched_vcf or source_vcf
    record = _scan_vcf_for_locus(base_vcf, contig, pos)
    if not record and patched_vcf and source_vcf and patched_vcf != source_vcf:
        record = _scan_vcf_for_locus(source_vcf, contig, pos)
    if not record:
        logger.warning(
            "VCF lookup miss contig=%s pos=%s base=%s fallback=%s",
            contig,
            pos,
            base_vcf,
            source_vcf
        )
        raise HTTPException(status_code=400, detail="Record not found at locus")
    return {
        "ref": record.get("ref", ""),
        "alt": record.get("alt", ""),
        "dp": record.get("dp", None),
        "ad": record.get("ad", None),
        "base_vcf": record.get("path", str(base_vcf))
    }


def _edit_log_path(sample_dir: Path, sample: str) -> Path:
    return sample_dir / "vcf_edits" / f"{sample}_patchlog.jsonl"


def _expected_patched_vcf_path(edits_dir: Path, source_vcf: Path) -> Path:
    suffixes = source_vcf.suffixes
    if suffixes[-2:] == [".vcf", ".gz"]:
        return edits_dir / source_vcf.name
    if suffixes and suffixes[-1] == ".vcf":
        return edits_dir / f"{source_vcf.name}.gz"
    return edits_dir / f"{source_vcf.name}.gz"


def _find_patched_vcf(sample_dir: Path, sample: str, source_vcf: Optional[Path]) -> Optional[Path]:
    edits_dir = sample_dir / "vcf_edits"
    if not edits_dir.exists():
        return None
    legacy = edits_dir / f"{sample}_patched.vcf.gz"
    if source_vcf:
        expected = _expected_patched_vcf_path(edits_dir, source_vcf)
        if expected.exists():
            return expected
    if legacy.exists():
        return legacy
    candidates = sorted(
        [p for p in edits_dir.glob("*.vcf*") if p.suffix != ".tbi"],
        key=lambda p: p.stat().st_mtime
    )
    return candidates[-1] if candidates else None


def _target_name_for_vcf(source_vcf: Path, chosen_vcf: Path) -> str:
    source_name = source_vcf.name
    chosen_suffixes = chosen_vcf.suffixes
    if chosen_suffixes[-2:] == [".vcf", ".gz"]:
        if source_name.endswith(".gz"):
            return source_name
        return f"{source_name}.gz"
    return source_name


def _vcf_is_edited(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return False
    return "vcf_edits" in resolved.parts


def _edited_samples_in_dir(vcf_source_dir: Path) -> List[str]:
    edited = set()
    if not vcf_source_dir.exists():
        return []
    vcfs = list(vcf_source_dir.glob("*.vcf")) + list(vcf_source_dir.glob("*.vcf.gz"))
    for vcf in vcfs:
        if _vcf_is_edited(vcf):
            edited.add(_sample_from_vcf(vcf))
    return sorted(edited)


def _write_step2_edit_summary(step2_dir: Path, edited_samples: List[str]) -> None:
    payload = {
        "edited_samples": sorted(set(edited_samples)),
        "edited_count": len(set(edited_samples)),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        (step2_dir / "edited_samples.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def _scan_vcf_for_locus(path: Path, contig: str, pos: int) -> Optional[Dict[str, object]]:
    if not path or not path.exists():
        return None
    opener = gzip.open if str(path).endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if not line or line[0] == "#":
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 8:
                    continue
                if parts[0] != contig:
                    continue
                try:
                    lpos = int(parts[1])
                except ValueError:
                    continue
                if lpos != pos:
                    continue
                ref = parts[3]
                alt = parts[4].split(",")[0] if parts[4] else ""
                dp = None
                ad = None
                for field in parts[7].split(";"):
                    if field.startswith("DP="):
                        try:
                            dp = int(field.split("=", 1)[1])
                        except ValueError:
                            dp = None
                        break
                if len(parts) >= 10:
                    fmt = parts[8].split(":")
                    sample = parts[9].split(":")
                    if "AD" in fmt:
                        try:
                            idx = fmt.index("AD")
                            ad_val = sample[idx] if idx < len(sample) else ""
                            ad = [int(x) for x in ad_val.split(",") if x != ""] if ad_val else None
                        except Exception:
                            ad = None
                return {
                    "ref": ref,
                    "alt": alt,
                    "dp": dp,
                    "ad": ad,
                    "path": str(path)
                }
    except OSError:
        return None
    return None


def _rewrite_vcf_with_alt(base_vcf: Path, out_vcf: Path, contig: str, pos: int, new_alt: str) -> Dict[str, object]:
    opener = gzip.open if str(base_vcf).endswith(".gz") else open
    found = False
    old_ref = ""
    old_alt = ""
    old_dp = None
    old_ad = None
    with opener(base_vcf, "rt", encoding="utf-8", errors="ignore") as src, out_vcf.open("w", encoding="utf-8") as dst:
        for line in src:
            if not line or line[0] == "#":
                dst.write(line)
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                dst.write(line)
                continue
            try:
                lpos = int(parts[1])
            except ValueError:
                dst.write(line)
                continue
            if parts[0] == contig and lpos == pos:
                alt_field = parts[4]
                if not alt_field:
                    raise ValueError("Record has no ALT")
                if "," in alt_field:
                    raise ValueError("Record must have exactly one ALT")
                old_ref = parts[3]
                old_alt = alt_field
                if old_alt == new_alt:
                    raise ValueError("ALT is unchanged")
                parts[4] = new_alt
                if len(parts) > 7:
                    for field in parts[7].split(";"):
                        if field.startswith("DP="):
                            try:
                                old_dp = int(field.split("=", 1)[1])
                            except ValueError:
                                old_dp = None
                            break
                if len(parts) > 9:
                    fmt = parts[8].split(":")
                    sample = parts[9].split(":")
                    if "AD" in fmt:
                        try:
                            idx = fmt.index("AD")
                            ad_val = sample[idx] if idx < len(sample) else ""
                            old_ad = [int(x) for x in ad_val.split(",") if x != ""] if ad_val else None
                        except Exception:
                            old_ad = None
                found = True
                line = "\t".join(parts) + "\n"
            dst.write(line)
    if not found:
        raise ValueError("Record not found at locus")
    return {"old_ref": old_ref, "old_alt": old_alt, "old_dp": old_dp, "old_ad": old_ad}


def _scan_vcf_first_record(path: Optional[Path]) -> Optional[Dict[str, object]]:
    if not path or not Path(path).exists():
        return None
    opener = gzip.open if str(path).endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if not line or line[0] == "#":
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 2:
                    continue
                return {"contig": parts[0], "pos": parts[1]}
    except OSError:
        return None
    return None


def _find_source_vcf(sample_dir: Path, sample: str) -> Optional[Path]:
    candidates = []
    for vcf in sample_dir.glob(f"**/{sample}*zc.vcf*"):
        if vcf.suffix == ".tbi":
            continue
        if "vcf_edits" in vcf.parts:
            continue
        candidates.append(vcf)
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.stat().st_mtime)[-1]


def _open_path(target: Path) -> None:
    if sys.platform.startswith("darwin"):
        suffix = target.suffix.lower()
        if suffix in {".json", ".jsonl", ".txt", ".log", ".csv", ".tsv"}:
            subprocess.run(["open", "-e", str(target)])
        else:
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


def _resolve_bcftools(cfg: Dict) -> str:
    path = cfg.get("bcftools_path", "").strip()
    if path:
        return path
    found = shutil.which("bcftools")
    return found or ""
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
