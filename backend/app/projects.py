import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

ARCHIVE_DIR_NAME = "projects_archive"

# A "root" is a directory that contains project subdirectories.
# Today we have two: a personal one (typically $HOME/projects) and a shared
# one (typically /srv/kapurlab/projects). Each project carries a "scope" tag
# in API responses based on which root it came from.
SCOPE_PERSONAL = "personal"
SCOPE_SHARED = "shared"

Root = Tuple[str, Path]                  # (scope, absolute path)
RootsLike = Union[Path, str, Iterable[Root]]


def _normalize_roots(roots: RootsLike) -> List[Root]:
    """Accept either a single Path/str (treated as personal) or an iterable
    of (scope, Path) tuples. Returns a list of (scope, Path) with non-existent
    paths filtered out."""
    if isinstance(roots, (str, Path)):
        return [(SCOPE_PERSONAL, Path(roots))] if Path(roots).exists() else []
    out: List[Root] = []
    for entry in roots:
        scope, p = entry
        p = Path(p)
        if p.exists():
            out.append((scope, p))
    return out


def ensure_project_dirs(project_dir: Path) -> None:
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    (project_dir / "step1").mkdir(parents=True, exist_ok=True)
    (project_dir / "step2" / "vcf_source").mkdir(parents=True, exist_ok=True)


def project_meta_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def create_project(roots: RootsLike, name: str, scope: Optional[str] = None) -> Path:
    """Create a project under the requested scope. Defaults to the first root
    (personal) when scope is unspecified."""
    norm = _normalize_roots(roots)
    if not norm:
        raise ValueError("No project root configured")
    chosen: Optional[Path] = None
    if scope is None:
        chosen = norm[0][1]
    else:
        for s, p in norm:
            if s == scope:
                chosen = p
                break
    if chosen is None:
        raise ValueError(f"No project root with scope={scope!r}")
    project_dir = chosen / name
    ensure_project_dirs(project_dir)
    meta = {
        "name": name,
        "created_at": _now_iso(),
        "status": "created",
    }
    with open(project_meta_path(project_dir), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    return project_dir


def update_project_meta(project_dir: Path, updates: Dict) -> Dict:
    meta_path = project_meta_path(project_dir)
    meta: Dict[str, Any] = {}
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    meta.update(updates)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
    return meta


def list_projects(roots: RootsLike) -> List[Dict]:
    """Walk all configured roots and return a flat list of projects, each
    tagged with `scope` and `_root` (the root path it lives under). On a
    name collision across roots, the personal one wins (listed first)."""
    norm = _normalize_roots(roots)
    seen: set = set()
    out: List[Dict] = []
    for scope, root in norm:
        for p in root.iterdir():
            if not p.is_dir():
                continue
            if p.name in (ARCHIVE_DIR_NAME,):
                continue
            if p.name.startswith("."):
                continue
            # Skip if a project with this name already came from an earlier
            # root. Earliest root wins (personal listed first).
            if p.name in seen:
                continue
            seen.add(p.name)
            meta_path = project_meta_path(p)
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except json.JSONDecodeError:
                    meta = {"name": p.name}
            else:
                meta = {"name": p.name}
            meta.update(_project_counts(p))
            meta["scope"] = scope
            meta["_root"] = str(root)
            try:
                meta["_mtime"] = p.stat().st_mtime
            except OSError:
                meta["_mtime"] = 0
            out.append(meta)
    out.sort(key=lambda x: x.get("_mtime", 0), reverse=True)
    for meta in out:
        meta.pop("_mtime", None)
    return out


def resolve_project_dir(roots: RootsLike, name: str) -> Optional[Path]:
    """Search all roots for a project by name. Returns the first match."""
    if "/" in name or name.startswith("."):
        raise ValueError("Invalid project name")
    norm = _normalize_roots(roots)
    for _scope, root in norm:
        cand = root / name
        if cand.is_dir():
            return cand
    return None


def archive_project(roots: RootsLike, name: str) -> Path:
    project_dir = resolve_project_dir(roots, name)
    if project_dir is None:
        raise FileNotFoundError(f"Project not found: {name}")
    archive_root = project_dir.parent / ARCHIVE_DIR_NAME
    archive_root.mkdir(parents=True, exist_ok=True)
    timestamp = _now_iso().replace(":", "-")
    target = archive_root / f"{name}_{timestamp}"
    project_dir.replace(target)
    return target


def delete_project(roots: RootsLike, name: str) -> Optional[Path]:
    project_dir = resolve_project_dir(roots, name)
    if project_dir is None or not project_dir.exists():
        return None
    shutil.rmtree(project_dir)
    return project_dir


# Back-compat shim: old call-sites pass a single Path. Keep them working.
def _resolve_project_dir(projects_root: Path, name: str) -> Path:
    """Deprecated: use resolve_project_dir([(scope, root), ...], name)."""
    if "/" in name or name.startswith("."):
        raise ValueError("Invalid project name")
    return projects_root / name


def _project_counts(project_dir: Path) -> Dict:
    download_dir = project_dir / "download"
    step1_dir = project_dir / "step1"
    step2_dir = project_dir / "step2"
    try:
        return {
            "fastq_count": len(list(download_dir.rglob("*.fastq.gz"))) if download_dir.exists() else 0,
            "step1_samples": len([d for d in step1_dir.iterdir() if d.is_dir()]) if step1_dir.exists() else 0,
            "step1_vcfs": len(list(step1_dir.glob("**/*_zc.vcf"))) if step1_dir.exists() else 0,
            "step2_html": len(list(step2_dir.glob("*.html"))) if step2_dir.exists() else 0,
            "step2_vcfs": len(list((step2_dir / "vcf_source").glob("*.vcf"))) if (step2_dir / "vcf_source").exists() else 0,
        }
    except PermissionError:
        # Dir exists but the requesting user can't list it (group perm). Just
        # report zeros so the project still appears in the list.
        return {
            "fastq_count": 0,
            "step1_samples": 0,
            "step1_vcfs": 0,
            "step2_html": 0,
            "step2_vcfs": 0,
        }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
