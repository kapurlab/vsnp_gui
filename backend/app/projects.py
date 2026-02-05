import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

ARCHIVE_DIR_NAME = "projects_archive"


def ensure_project_dirs(project_dir: Path) -> None:
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    (project_dir / "step1").mkdir(parents=True, exist_ok=True)
    (project_dir / "step2" / "vcf_source").mkdir(parents=True, exist_ok=True)


def project_meta_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def create_project(projects_root: Path, name: str) -> Path:
    project_dir = projects_root / name
    ensure_project_dirs(project_dir)
    meta = {
        "name": name,
        "created_at": _now_iso(),
        "status": "created"
    }
    with open(project_meta_path(project_dir), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    return project_dir


def update_project_meta(project_dir: Path, updates: Dict) -> Dict:
    meta_path = project_meta_path(project_dir)
    meta = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    meta.update(updates)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    return meta


def list_projects(projects_root: Path) -> List[Dict]:
    if not projects_root.exists():
        return []
    projects = []
    for p in projects_root.iterdir():
        if not p.is_dir():
            continue
        if p.name == ARCHIVE_DIR_NAME:
            continue
        if p.name.startswith("."):
            continue
        meta_path = project_meta_path(p)
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        else:
            meta = {"name": p.name}
        meta.update(_project_counts(p))
        meta["_mtime"] = p.stat().st_mtime
        projects.append(meta)
    projects.sort(key=lambda x: x.get("_mtime", 0), reverse=True)
    for meta in projects:
        meta.pop("_mtime", None)
    return projects


def archive_project(projects_root: Path, name: str) -> Path:
    project_dir = _resolve_project_dir(projects_root, name)
    archive_root = projects_root / ARCHIVE_DIR_NAME
    archive_root.mkdir(parents=True, exist_ok=True)
    timestamp = _now_iso().replace(":", "-")
    target = archive_root / f"{name}_{timestamp}"
    project_dir.replace(target)
    return target


def delete_project(projects_root: Path, name: str) -> Optional[Path]:
    project_dir = _resolve_project_dir(projects_root, name)
    if not project_dir.exists():
        return None
    shutil.rmtree(project_dir)
    return project_dir


def _resolve_project_dir(projects_root: Path, name: str) -> Path:
    if "/" in name or name.startswith("."):
        raise ValueError("Invalid project name")
    return projects_root / name


def _project_counts(project_dir: Path) -> Dict:
    download_dir = project_dir / "download"
    step1_dir = project_dir / "step1"
    step2_dir = project_dir / "step2"
    return {
        "fastq_count": len(list(download_dir.rglob("*.fastq.gz"))) if download_dir.exists() else 0,
        "step1_samples": len([d for d in step1_dir.iterdir() if d.is_dir()]) if step1_dir.exists() else 0,
        "step1_vcfs": len(list(step1_dir.glob("**/*.vcf"))) if step1_dir.exists() else 0,
        "step2_html": len(list(step2_dir.glob("*.html"))) if step2_dir.exists() else 0,
        "step2_vcfs": len(list((step2_dir / "vcf_source").glob("*.vcf"))) if (step2_dir / "vcf_source").exists() else 0,
    }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
