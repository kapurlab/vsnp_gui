import json
from pathlib import Path
from typing import Dict, List


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


def list_projects(projects_root: Path) -> List[Dict]:
    if not projects_root.exists():
        return []
    projects = []
    for p in sorted(projects_root.iterdir()):
        if not p.is_dir():
            continue
        meta_path = project_meta_path(p)
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        else:
            meta = {"name": p.name}
        meta.update(_project_counts(p))
        projects.append(meta)
    return projects


def _project_counts(project_dir: Path) -> Dict:
    download_dir = project_dir / "download"
    step1_dir = project_dir / "step1"
    step2_dir = project_dir / "step2"
    return {
        "fastq_count": len(list(download_dir.glob("*.fastq.gz"))) if download_dir.exists() else 0,
        "step1_samples": len([d for d in step1_dir.iterdir() if d.is_dir()]) if step1_dir.exists() else 0,
        "step1_vcfs": len(list(step1_dir.glob("**/*.vcf"))) if step1_dir.exists() else 0,
        "step2_html": len(list(step2_dir.glob("*.html"))) if step2_dir.exists() else 0,
        "step2_vcfs": len(list((step2_dir / "vcf_source").glob("*.vcf"))) if (step2_dir / "vcf_source").exists() else 0,
    }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
