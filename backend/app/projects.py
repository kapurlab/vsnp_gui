import json
import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


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

    Never raises. ``Path.exists()`` PROPAGATES EACCES rather than returning
    False, and this is called while merely describing a project — including
    from inside the argument list that builds the activity-timestamp
    candidates. A project directory the current user cannot search therefore
    raised from a routine "which layout is this?" question, and took the whole
    listing with it.
    """
    new = step2_dir / "vcf_database"
    if _exists(new):
        return new
    legacy = step2_dir / "vcf_source"
    if _exists(legacy):
        return legacy
    return new


def _exists(p: Path) -> bool:
    """``Path.exists()`` without the EACCES surprise: unreachable is not there."""
    try:
        return p.exists()
    except OSError:
        return False


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
    existed = meta_path.exists()
    if existed:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    merged = dict(meta)
    merged.update(updates)
    # Skip the write when it would change nothing. This is not an optimization
    # nicety: reference_lock calls this on EVERY visit to a single-reference
    # project, and each write bumped project.json's mtime — which invalidated
    # the counts cache (forcing a full ~10-40s recount of the big project on
    # the next /api/projects) and floated the project to "most recent activity"
    # merely for having been looked at. On-disk state is identical either way.
    if existed and merged == meta:
        return meta
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, sort_keys=True)
    return merged


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
    todo: List[Tuple[str, Path, Path]] = []
    for scope, root in norm:
        try:
            entries = sorted(root.iterdir())
        except OSError:
            # A root that cannot be read contributes nothing; the other root's
            # projects are still perfectly listable.
            logger.warning("list_projects: cannot read root %s", root)
            continue
        for p in entries:
            try:
                if not p.is_dir():
                    continue
            except OSError:
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
            todo.append((scope, root, p))

    # Counted concurrently, because this is a syscall-bound walk and the
    # roots hold projects of wildly different sizes. Serially, N projects cost
    # the SUM of their scans: three influenza projects of ~24,000 samples each
    # took minutes, during which the whole GUI was unresponsive — every other
    # endpoint is a sync def sharing the same threadpool, and one of its
    # workers was held for the duration. Concurrently the cost is closer to
    # the largest single project.
    #
    # The cap is small on purpose. These are directory reads against one
    # filesystem, so past a handful of workers there is nothing left to
    # overlap and the extra threads only compete for the GIL with the requests
    # this was slowing down in the first place.
    workers = max(1, min(8, len(todo)))
    out: List[Dict] = []
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="proj-count") as pool:
            results = list(pool.map(lambda t: _describe_project(*t), todo))
    else:
        results = [_describe_project(*t) for t in todo]
    out = [m for m in results if m is not None]

    out.sort(key=lambda x: x.get("_mtime", 0), reverse=True)
    for meta in out:
        meta.pop("_mtime", None)
    return out


def _describe_project(scope: str, root: Path, p: Path) -> Optional[Dict]:
    """One project's card. Never raises: a project that cannot be described
    must not take the rest of the list down with it."""
    try:
        meta: Dict = {}
        meta_path = project_meta_path(p)
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        # A missing project.json is the normal case for an older project, and
        # is what `exists()` used to be asked. Asking by opening removes a stat
        # AND the failure mode it had: pathlib's exists() PROPAGATES EACCES
        # rather than returning False, so a single project whose project.json
        # this user cannot reach raised straight out of the listing and blanked
        # every readable project in the GUI.
        except FileNotFoundError:
            meta = {}
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("list_projects: unusable project.json in %s: %s", p, e)
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        # Always derive `name` from the directory. Older project.json files
        # written by update_project_meta may have set display_name and
        # reference without a name field; the directory is the source of
        # truth for the project's identity.
        meta["name"] = p.name
        # Activity first: it is a handful of stats and doubles as the
        # cache signature for the (much more expensive) counts below.
        activity = _project_last_activity(p)
        meta.update(_project_counts(p, activity))
        meta["scope"] = scope
        meta["_root"] = str(root)
        meta["_mtime"] = activity
        # ISO string for display; the frontend sorts/labels by recency.
        from datetime import datetime
        meta["last_activity"] = (
            datetime.fromtimestamp(activity).isoformat(timespec="seconds")
            if activity
            else ""
        )
        return meta
    except Exception:
        logger.exception("list_projects: skipping %s", p)
        return None


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


def _is_read_file(name: str) -> bool:
    """A project input read: ``*.fastq.gz``, excluding vSNP3's unmapped subset."""
    return name.endswith(".fastq.gz") and "_unmapped_" not in name


def _add_read_identity(entry: "os.DirEntry", dir_dev: Optional[int], seen: set) -> None:
    """Record a read file's identity so the same file is never counted twice.

    Reads live in ``download/`` on native projects and in ``step1/<sample>/``
    on imported ones, and one is often a symlink to the other — so the count
    is of DISTINCT files, not dirents. Identity is (device, inode):

      - plain file: the inode comes from the directory entry itself, so this
        costs nothing at all;
      - symlink: one stat that follows the link, giving the target's identity,
        which is what makes a download/ -> step1/ link collapse to one file.

    The previous implementation called ``Path.resolve()`` on EVERY read; on a
    9,000-sample project over a network filesystem that was ~18,000 extra
    path-walk round-trips every time the project list was fetched. (dev, ino)
    is also strictly more accurate — it catches hard links, which resolve()
    reports as two different files.
    """
    try:
        if entry.is_symlink():
            st = entry.stat()                     # follows -> target identity
            seen.add((st.st_dev, st.st_ino))
        elif dir_dev is not None:
            seen.add((dir_dev, entry.inode()))    # free: straight from the dirent
        else:
            st = entry.stat(follow_symlinks=False)
            seen.add((st.st_dev, st.st_ino))
    except OSError:
        # Unreadable/vanished mid-scan: fall back to the path so it still
        # counts once rather than disappearing from the badge.
        seen.add(entry.path)


def _dir_device(path: Path) -> Optional[int]:
    try:
        return path.stat().st_dev
    except OSError:
        return None


def _scan_download_reads(download_dir: Path, seen: set) -> None:
    """Add every read under ``download/`` (recursively) to the identity set."""
    if not download_dir.is_dir():
        return
    stack = [download_dir]
    while stack:
        current = stack.pop()
        dev = _dir_device(current)
        try:
            with os.scandir(current) as it:
                for entry in it:
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
                    elif _is_read_file(entry.name):
                        _add_read_identity(entry, dev, seen)
        except OSError:
            continue


def _scan_step1(step1_dir: Path, seen: set) -> int:
    """One pass over step1/ for the numbers the project card needs.

    Returns the sample count and adds each sample's reads to the shared
    identity set.

    Previously this cost FOUR separate traversals of every sample directory —
    an ``iterdir`` for the sample count, a ``*/*.fastq.gz`` glob, an
    ``*/alignment_*/*_zc.vcf`` glob and a legacy ``*/alignment/*_zc.vcf``
    glob. On the 9,364-sample Ames project that is tens of thousands of
    directory reads for one project listing, and the GUI re-fetches
    /api/projects after most actions, so the whole app stalled for minutes
    each time.

    It then cost two: the sample dir, and every ``alignment*/`` inside it. The
    second read existed solely to produce a ``step1_vcfs`` count that the card
    never displayed — the badge reads ``vcfs_count ?? step1_vcfs`` and
    ``vcfs_count`` is set on every path, including the error path, so the
    fallback could not fire even when it was meant to. One directory read per
    sample was being spent on a number nothing could show. Now one traversal,
    and on a 24,000-sample influenza project that is 24,000 fewer directory
    reads per listing.
    """
    if not step1_dir.is_dir():
        return 0
    sample_dirs = []
    try:
        with os.scandir(step1_dir) as it:
            for entry in it:
                # Real sample dirs only — the writer's _provenance/ sibling and
                # dot-dirs are excluded, matching the inline sample browser.
                if entry.name.startswith(("_", ".")):
                    continue
                if entry.is_dir():
                    sample_dirs.append(entry.path)
    except OSError:
        return 0

    for sample_path in sample_dirs:
        dev = _dir_device(Path(sample_path))
        try:
            with os.scandir(sample_path) as it:
                for entry in it:
                    # Reads live directly under the sample dir.
                    if _is_read_file(entry.name):
                        _add_read_identity(entry, dev, seen)
        except OSError:
            continue
    return len(sample_dirs)


def _count_vcf_database(vcfs_dir: Path) -> Tuple[int, int]:
    """(step2_vcfs, vcfs_count) from ONE scan of the cumulative VCF store.

    Was three globs over the same directory; on a 9k-VCF database that is
    three full directory reads for two numbers.
    """
    if not vcfs_dir.is_dir():
        return 0, 0
    plain = collected = 0
    try:
        with os.scandir(vcfs_dir) as it:
            for entry in it:
                name = entry.name
                if name.endswith(".vcf"):
                    plain += 1
                if name.endswith("_zc.vcf") or name.endswith("_zc.vcf.gz"):
                    collected += 1
    except OSError:
        return 0, 0
    return plain, collected


# Per-project count cache — deliberately a BURST COALESCER, not a real cache.
#
# /api/projects is re-fetched from 13 places in the GUI, several of which fire
# within the same moment (mount, then again after the first action completes),
# and on a big project this scan is the most expensive thing the backend does.
# Repeating it twice in one second is pure waste.
#
# The TTL is a few seconds ON PURPOSE. These counts cannot be cached safely for
# longer: a VCF written deep inside step1/<sample>/alignment_<ref>/ bumps no
# parent directory's mtime, so no cheap signature can notice it, and mtime
# resolution is coarse enough that several changes can share one tick. A window
# this short cannot show anyone a meaningfully stale badge, while still
# collapsing the duplicate scans that made the GUI feel frozen.
_COUNTS_CACHE: Dict[str, Tuple[float, float, Dict]] = {}
_COUNTS_TTL_SECONDS = 3.0


def _project_counts(project_dir: Path, activity: Optional[float] = None) -> Dict:
    import time as _time

    key = str(project_dir)
    sig = activity if activity is not None else _project_last_activity(project_dir)
    cached = _COUNTS_CACHE.get(key)
    now = _time.time()
    if cached is not None:
        cached_sig, cached_at, counts = cached
        if cached_sig == sig and (now - cached_at) < _COUNTS_TTL_SECONDS:
            return dict(counts)

    download_dir = project_dir / "download"
    step1_dir = project_dir / "step1"
    step2_dir = project_dir / "step2"
    # The cumulative VCF store is step2/vcf_database now; vcfs_count (the card's
    # "collected VCFs" badge) and step2_vcfs both read from it.
    try:
        # vcf_db_dir stats its candidates, so it belongs INSIDE the guard: on an
        # unreadable project it raised from the assignment line and escaped the
        # except below, which is how one project nobody can read became a 500
        # for the whole list.
        vcfs_dir = vcf_db_dir(step2_dir)
        # One shared identity set across both read locations, so a download/
        # symlink pointing at a step1 read (or the reverse) counts once.
        reads: set = set()
        _scan_download_reads(download_dir, reads)
        step1_samples = _scan_step1(step1_dir, reads)
        step2_vcfs, vcfs_count = _count_vcf_database(vcfs_dir)
        counts = {
            "fastq_count": len(reads),
            "step1_samples": step1_samples,
            "step2_html": len(list(step2_dir.glob("*.html"))) if step2_dir.exists() else 0,
            "step2_vcfs": step2_vcfs,
            "vcfs_count": vcfs_count,
        }
    # OSError, not PermissionError: a stale NFS handle, an ENOTDIR where a
    # directory is expected, or an EIO from a failing disk are all the same
    # situation — this project's numbers are unknowable — and none of them is a
    # reason to refuse the other projects.
    except OSError:
        counts = {
            "fastq_count": 0,
            "step1_samples": 0,
            "step2_html": 0,
            "step2_vcfs": 0,
            "vcfs_count": 0,
            "counts_unreadable": True,
        }
    _COUNTS_CACHE[key] = (sig, now, counts)
    # Bound the cache: projects come and go (create/archive/delete), and this
    # process is long-lived.
    if len(_COUNTS_CACHE) > 256:
        for stale_key in sorted(_COUNTS_CACHE, key=lambda k: _COUNTS_CACHE[k][1])[:64]:
            _COUNTS_CACHE.pop(stale_key, None)
    return dict(counts)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")
