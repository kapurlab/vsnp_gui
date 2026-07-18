import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union


_PROJECT_NAME_OK_CHARSET = re.compile(r"^[A-Za-z0-9._-]+$")


def normalize_project_name(name: str) -> str:
    """Turn a user-supplied project name into a filesystem-safe directory name.

    Auto-converts internal whitespace to underscores (regression fix —
    callers like seqkit, called via vsnp3, don't quote paths properly, so a
    space in the project dir produces a truncated path and a confusing
    `[ERRO] stat /path: no such file or directory` error). Rejects any
    other shell-unfriendly characters with a clear ValueError so the
    caller surfaces it as HTTP 400 instead of letting it explode at
    `seqkit stat` / `bwa mem` / similar downstream.
    """
    if not isinstance(name, str):
        raise ValueError("Project name must be a string")
    cleaned = re.sub(r"\s+", "_", name.strip())
    if not cleaned:
        raise ValueError("Project name is empty")
    if cleaned.startswith("."):
        raise ValueError("Project name cannot start with '.'")
    if len(cleaned) > 100:
        raise ValueError("Project name too long (max 100 characters)")
    if not _PROJECT_NAME_OK_CHARSET.match(cleaned):
        bad = sorted(set(ch for ch in cleaned if not re.match(r"[A-Za-z0-9._-]", ch)))
        raise ValueError(
            f"Project name contains characters that cause downstream tool failures: {''.join(bad)!r}. "
            "Only letters, digits, _ - . are allowed (spaces are auto-converted to underscores)."
        )
    return cleaned

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


def vcf_db_dir(step2_dir: Path) -> Path:
    """Return the step2 VCF database directory.

    The current name is ``vcf_database``; projects created before the rename
    used ``vcf_source``. Prefer the new name, fall back to the legacy one when
    it's the only one present, and default to the new name for creation so new
    projects always get ``vcf_database``.
    """
    new = step2_dir / "vcf_database"
    if new.exists():
        return new
    legacy = step2_dir / "vcf_source"
    if legacy.exists():
        return legacy
    return new


def ensure_project_dirs(project_dir: Path) -> None:
    # A project has exactly two workflow folders: step1/ and step2/. The single
    # cumulative VCF store lives at step2/vcf_database/ (the classic vSNP3
    # layout) — there is no separate <project>_VCFs/ folder anymore.
    (project_dir / "download").mkdir(parents=True, exist_ok=True)
    (project_dir / "step1").mkdir(parents=True, exist_ok=True)
    vcf_db_dir(project_dir / "step2").mkdir(parents=True, exist_ok=True)


def project_meta_path(project_dir: Path) -> Path:
    return project_dir / "project.json"


def create_project(roots: RootsLike, name: str, scope: Optional[str] = None, reference: str = "") -> Path:
    """Create a project under the requested scope. Defaults to the first root
    (personal) when scope is unspecified. The supplied name is normalized via
    `normalize_project_name` (spaces → underscores, other unsafe chars
    rejected) before being used as both the directory name and the project
    metadata. If `reference` is given it is stored in project.json immediately."""
    name = normalize_project_name(name)
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
    if project_dir.exists():
        raise ValueError(f"Project already exists: {name}")
    ensure_project_dirs(project_dir)
    meta: Dict[str, Any] = {
        "name": name,
        "created_at": _now_iso(),
        "status": "created",
    }
    if reference:
        meta["reference"] = reference
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


def _project_last_activity(project_dir: Path) -> float:
    """Best-effort "most recent activity" timestamp for a project, as a Unix
    epoch float. A directory's mtime only bumps when its *direct* children
    change, so the project root alone misses nested work (a new step1 sample
    dir, a fresh download). We take the max mtime across the root and the key
    workflow subdirs/files — cheap (a handful of stats) and captures adding
    samples, runs, and downloads without walking the whole tree."""
    candidates = [
        project_dir,
        project_dir / "project.json",
        project_dir / "download",
        project_dir / "step1",
        project_dir / "step2",
        vcf_db_dir(project_dir / "step2"),
    ]
    latest = 0.0
    for c in candidates:
        try:
            m = c.stat().st_mtime
        except OSError:
            continue
        if m > latest:
            latest = m
    return latest


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
                    meta = {}
            else:
                meta = {}
            # Always derive `name` from the directory. Older project.json files
            # written by update_project_meta may have set display_name and
            # reference without a name field; the directory is the source of
            # truth for the project's identity.
            meta["name"] = p.name
            meta.update(_project_counts(p))
            meta["scope"] = scope
            meta["_root"] = str(root)
            activity = _project_last_activity(p)
            meta["_mtime"] = activity
            # ISO string for display; the frontend sorts/labels by recency.
            from datetime import datetime
            meta["last_activity"] = (
                datetime.fromtimestamp(activity).isoformat(timespec="seconds")
                if activity
                else ""
            )
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


def _count_project_reads(download_dir: Path, step1_dir: Path) -> int:
    """Count a project's input read files (``*.fastq.gz``).

    Native projects keep reads in ``download/``; Roar-imported projects keep
    them in ``step1/<sample>/`` (and may also symlink them into ``download/``).
    Count the union of both, deduped by resolved path so a ``download/`` symlink
    pointing at a ``step1`` read isn't double-counted. Skip ``*_unmapped_*``
    (the unmapped-read subset vSNP3 emits alongside the real reads — not an
    input read set). ``step1`` is globbed one level deep (reads live directly
    under each sample dir) to avoid walking the per-sample alignment subtrees."""
    seen: set = set()
    candidates = []
    if download_dir.is_dir():
        candidates += download_dir.rglob("*.fastq.gz")
    if step1_dir.is_dir():
        candidates += step1_dir.glob("*/*.fastq.gz")
    for f in candidates:
        if "_unmapped_" in f.name:
            continue
        try:
            key = f.resolve()
        except OSError:
            key = f
        seen.add(key)
    return len(seen)


def _project_counts(project_dir: Path) -> Dict:
    download_dir = project_dir / "download"
    step1_dir = project_dir / "step1"
    step2_dir = project_dir / "step2"
    # The cumulative VCF store is step2/vcf_database now; vcfs_count (the card's
    # "collected VCFs" badge) and step2_vcfs both read from it.
    vcfs_dir = vcf_db_dir(step2_dir)
    try:
        return {
            "fastq_count": _count_project_reads(download_dir, step1_dir),
            "step1_samples": len([d for d in step1_dir.iterdir() if d.is_dir()]) if step1_dir.exists() else 0,
            "step1_vcfs": len(list(step1_dir.glob("*/alignment_*/*_zc.vcf"))) if step1_dir.exists() else 0,
            "step2_html": len(list(step2_dir.glob("*.html"))) if step2_dir.exists() else 0,
            "step2_vcfs": len(list(vcfs_dir.glob("*.vcf"))) if vcfs_dir.exists() else 0,
            "vcfs_count": (
                len(list(vcfs_dir.glob("*_zc.vcf"))) + len(list(vcfs_dir.glob("*_zc.vcf.gz")))
                if vcfs_dir.exists() else 0
            ),
        }
    except PermissionError:
        return {
            "fastq_count": 0,
            "step1_samples": 0,
            "step1_vcfs": 0,
            "step2_html": 0,
            "step2_vcfs": 0,
            "vcfs_count": 0,
        }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
