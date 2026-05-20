"""
provenance_writer.py
====================

Writer module for vsnp_gui run provenance (T-07).

Produces V2 records consumable by the existing reader/indexer without
modification. Two public dispatch functions and two finalize functions form
the integration surface; everything else is internal helpers.

Architecture
------------
- Dispatch is hard-fail. If the writer cannot capture state or write metadata,
  the run does not start.
- Finalize is soft-fail. If the writer fails at finalize, the subprocess result
  is preserved; the metadata gap is logged to metadata_failures.jsonl by the
  caller (JobManager).
- State capture is one-shot per uvicorn lifetime. capture_*() helpers cache
  on first call; service restart is the documented refresh.
- Step1 batch flow: pre-create per-sample run_metadata.json with shared
  dispatch_state at dispatch; bash batch writes sentinel files alongside
  vsnp3_step1.py invocations; post-batch finalize reads sentinels and
  rewrites per-sample metadata as terminal.
- Step2 flow: single subprocess. Pipeline_run record written first, then
  step2 run_metadata.json. Refuses dispatch on shared projects with
  in-flight step1; warn-and-proceed on personal projects.

See docs/dev/T-07-writer-context-for-opus.md for the full design context.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import platform as _platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 2
DEFAULT_HASH_THRESHOLD_BYTES = 256 * 1024 * 1024  # 256 MB

# System tools vsnp3 shells out to. Captured for environment provenance.
TRACKED_SYSTEM_PACKAGES = ("samtools", "bcftools", "bwa", "mafft", "raxml", "iqtree")

# Default trust scope. The writer captures all V1-supported guarantees; the
# scope block makes them explicit so reviewers don't have to read the docs.
DEFAULT_TRUST_SCOPE = {
    "timestamps": "local_ntp",
    "actor_authentication": "ood_session_uuid",
    "tamper_resistance": "append_only_advisory",
    "sample_chain_of_custody": "filename_only",
}

# Module import time, used as approximate uvicorn start.
_MODULE_IMPORTED_AT = datetime.now(tz=timezone.utc)

# Caches. Populated lazily on first capture_*() call. Threading.Lock protects
# concurrent capture from racing during populate.
_capture_lock = threading.Lock()
_env_snapshot_cache: dict[str, dict[str, Any]] = {}
_vsnp_gui_cache: dict[str, dict[str, Any]] = {}
_vsnp3_cache: dict[str, dict[str, Any]] = {}
_reference_cache: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WriterError(Exception):
    """Base for writer errors."""


class DispatchFailed(WriterError):
    """Raised when dispatch-time state capture or metadata write fails.

    Caller (main.py) should surface this as a hard error and not start the
    underlying job. Per the locked policy: hard fail at dispatch.
    """


class Step2DispatchBlocked(WriterError):
    """Raised when step2 cannot dispatch because step1 samples are still
    running on a shared project. Caller surfaces as HTTP 409.
    """

    def __init__(self, running_samples: list[str]):
        self.running_samples = list(running_samples)
        n = len(self.running_samples)
        super().__init__(
            f"Cannot dispatch step2: {n} step1 sample(s) still running "
            f"{self.running_samples}. Wait for them to finish or cancel."
        )


# ---------------------------------------------------------------------------
# Atomic write / read
# ---------------------------------------------------------------------------


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically: tempfile in same dir + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, indent=2, default=_json_default)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# File hashing
# ---------------------------------------------------------------------------


def _file_sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Folder manifest (used for reference folder + VCF DB folders)
# ---------------------------------------------------------------------------


def compute_folder_manifest(folder: Path) -> tuple[str, list[dict[str, Any]]]:
    """Return (rolled_sha256, [files]) for a folder.

    Manifest is deterministic: files sorted by relpath, each entry hashed
    individually, the rolled hash is SHA-256 of `\\n`-joined `relpath\\0sha256`
    lines. Symlinks are followed; broken symlinks raise.
    """
    if not folder.is_dir():
        raise DispatchFailed(f"folder manifest target is not a directory: {folder}")

    files: list[dict[str, Any]] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file():
            continue
        relpath = str(p.relative_to(folder))
        files.append({
            "relpath": relpath,
            "sha256": _file_sha256(p),
            "size": p.stat().st_size,
        })

    manifest = "".join(f"{f['relpath']}\0{f['sha256']}\n" for f in files)
    rolled = hashlib.sha256(manifest.encode()).hexdigest()
    return rolled, files


# ---------------------------------------------------------------------------
# Input identity
# ---------------------------------------------------------------------------


def compute_input_identity(path: Path, threshold_bytes: int) -> tuple[str | None, str]:
    """Return (sha256_or_none, identity_method) for an input file.

    Files at or below threshold_bytes get hashed (identity_method='sha256').
    Larger files get None hash and identity_method='size_mtime_path' (the
    `(size, mtime, abs_path)` tuple in the schema serves as identity).

    Threshold default is 256 MB per the locked V1 decision; configurable via
    cfg['provenance']['hash_max_bytes'].
    """
    size = path.stat().st_size
    if size <= threshold_bytes:
        return _file_sha256(path), "sha256"
    return None, "size_mtime_path"


# ---------------------------------------------------------------------------
# Capture: vsnp_gui
# ---------------------------------------------------------------------------


def capture_vsnp_gui_state(deploy_path: Path) -> dict[str, Any]:
    """Capture vsnp_gui git state + uvicorn process info.

    Cached for uvicorn lifetime. Service restart refreshes.
    """
    key = str(deploy_path.resolve())
    cached = _vsnp_gui_cache.get(key)
    if cached is not None:
        return cached

    with _capture_lock:
        # Double-check under lock
        cached = _vsnp_gui_cache.get(key)
        if cached is not None:
            return cached

        try:
            git_sha = _git(deploy_path, "rev-parse", "HEAD")
            git_branch = _git(deploy_path, "rev-parse", "--abbrev-ref", "HEAD")
            git_status = _git(deploy_path, "status", "--porcelain")
            git_dirty = bool(git_status.strip())
        except subprocess.CalledProcessError as e:
            raise DispatchFailed(
                f"failed to read git state from {deploy_path}: {e}"
            ) from e

        state = {
            "git_sha": git_sha,
            "git_branch": git_branch,
            "git_dirty": git_dirty,
            "deploy_path": str(deploy_path),
            "uvicorn_pid": os.getpid(),
            "uvicorn_started_at": _MODULE_IMPORTED_AT.isoformat(),
        }
        _vsnp_gui_cache[key] = state
        return state


def _git(cwd: Path, *args: str) -> str:
    # -c safe.directory bypasses CVE-2022-24765 ownership check when the
    # backend process runs as a different user than the repo owner (OOD context).
    result = subprocess.run(
        ["git", f"-c", f"safe.directory={cwd.resolve()}", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Capture: vsnp3 install
# ---------------------------------------------------------------------------


def capture_vsnp3_state(install_path: Path) -> dict[str, Any]:
    """Capture vsnp3 install state: version, install_path, applied patches.

    subprocess_pid and subprocess_exe_realpath are NOT captured here (the
    subprocess hasn't been spawned yet at dispatch time). The schema fields
    exist as None; main.py can populate subprocess_pid in the JobManager
    callback if useful, but doing so would mutate the frozen dispatch_state,
    which we don't want. Leave as None in V1.
    """
    key = str(install_path.resolve())
    cached = _vsnp3_cache.get(key)
    if cached is not None:
        return cached

    with _capture_lock:
        cached = _vsnp3_cache.get(key)
        if cached is not None:
            return cached

        version = _read_vsnp3_version(install_path)
        applied_patches = _read_applied_patches(install_path)

        state = {
            "version": version,
            "install_path": str(install_path),
            "subprocess_pid": None,
            "subprocess_exe_realpath": None,
            "applied_patches": applied_patches,
        }
        _vsnp3_cache[key] = state
        return state


def _read_vsnp3_version(install_path: Path) -> str:
    """Read vsnp3 version. Tries `vsnp3 --version`; falls back to install_path basename."""
    bin_dir = install_path / "bin"
    candidates = [bin_dir / "vsnp3", bin_dir / "vsnp3_step1.py"]
    for c in candidates:
        if not c.exists():
            continue
        try:
            result = subprocess.run(
                [str(c), "--version"],
                capture_output=True, text=True, timeout=10,
            )
            out = (result.stdout or result.stderr or "").strip()
            m = re.search(r"\d+\.\d+(?:\.\d+)?", out)
            if m:
                return m.group(0)
        except (subprocess.TimeoutExpired, OSError):
            continue
    # Fallback: try VERSION file in install root
    vfile = install_path / "VERSION"
    if vfile.is_file():
        return vfile.read_text().strip()
    # Last resort: empty string. Don't fail dispatch over a missing version.
    logger.warning("could not determine vsnp3 version at %s", install_path)
    return ""


def _read_applied_patches(install_path: Path) -> list[dict[str, Any]]:
    """Read applied patches from deploy/vsnp3-patches/.

    The patch set is a single file (v3.16-kapurlab.patch) containing 4 hunks.
    We hash the whole file once and surface the four logical hunk names with
    that shared hash, so the schema's per-patch object structure is honored
    without pretending hunks have independent identities.
    """
    # Walk up from install to find deploy/vsnp3-patches/
    candidates = [
        install_path.parent / "vsnp_gui" / "deploy" / "vsnp3-patches",
        Path("/srv/kapurlab/tools/vsnp_gui/deploy/vsnp3-patches"),
    ]
    patches: list[dict[str, Any]] = []
    for d in candidates:
        if not d.is_dir():
            continue
        for patch_file in sorted(d.glob("*.patch")):
            patch_sha = _file_sha256(patch_file)
            applied_at = datetime.fromtimestamp(
                patch_file.stat().st_mtime, tz=timezone.utc
            ).isoformat()
            # Extract hunk names from patch file headers (lines like 'Subject:' or comments)
            names = _extract_patch_hunk_names(patch_file)
            if not names:
                names = [patch_file.stem]
            for name in names:
                patches.append({
                    "name": name,
                    "patch_file": str(patch_file),
                    "patch_sha256": patch_sha,
                    "applied_at": applied_at,
                })
        if patches:
            break
    return patches


def _extract_patch_hunk_names(patch_file: Path) -> list[str]:
    """Best-effort hunk-name extraction from patch file. Looks for lines like
    `# hunk: <name>` or `Subject: [PATCH] <name>`. Returns empty list if none
    found; caller falls back to the patch filename.
    """
    names: list[str] = []
    try:
        with patch_file.open() as f:
            for line in f:
                m = re.match(r"^\s*#\s*hunk:\s*(\S+)", line)
                if m:
                    names.append(m.group(1))
                    continue
                m = re.match(r"^Subject:\s*\[PATCH(?:\s+\d+/\d+)?\]\s*(.+)$", line)
                if m:
                    names.append(m.group(1).strip())
    except OSError:
        pass
    return names


# ---------------------------------------------------------------------------
# Capture: environment (conda + pip + system packages)
# ---------------------------------------------------------------------------


def capture_env_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    """Capture conda env yaml + pip freeze + system tool versions.

    Side effect: writes normalized yaml + pip freeze to the shared
    env_snapshots store at <audit_root>/env_snapshots/<sha256>.{yaml,txt}
    (idempotent via stat-then-link).

    Returns environment dict with shared store paths. The caller is
    responsible for copying the yaml/txt into the per-run _provenance/ dir
    via copy_env_snapshot_into_run() and updating the path fields to
    per-run paths in the per-run record.

    Cached for uvicorn lifetime, keyed by the conda prefix (or vsnp3
    install path if no conda env is active).
    """
    vsnp3_path = Path(cfg.get("vsnp3_path", "/srv/kapurlab/tools/vsnp3"))
    conda_prefix = os.environ.get("CONDA_PREFIX", "") or str(vsnp3_path)
    cache_key = conda_prefix

    cached = _env_snapshot_cache.get(cache_key)
    if cached is not None:
        return dict(cached)  # return copy to prevent caller mutation

    with _capture_lock:
        cached = _env_snapshot_cache.get(cache_key)
        if cached is not None:
            return dict(cached)

        audit_root = Path(cfg.get("audit_root", "/srv/kapurlab/audit"))
        snapshots_dir = audit_root / "env_snapshots"
        try:
            snapshots_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("env_snapshots dir not writable (%s); skipping shared store", e)
            snapshots_dir = None

        # Conda env: try `conda env export` first; if that fails (no conda
        # binary on this deploy), fall back to reading conda-meta/*.json
        # from the install dir, which is conda's own authoritative record
        # of what's installed. The fallback yaml is a synthesized list of
        # `<package>=<version>=<build>` lines, which is what `conda env
        # export --no-builds` would produce in the dependencies section.
        conda_yaml_normalized = _capture_conda_env_yaml()
        if not conda_yaml_normalized:
            conda_yaml_normalized = _capture_conda_meta_fallback(vsnp3_path)
        conda_yaml_sha = (
            hashlib.sha256(conda_yaml_normalized.encode()).hexdigest()
            if conda_yaml_normalized else None
        )
        conda_yaml_shared_path = None
        if conda_yaml_sha and snapshots_dir is not None:
            target = snapshots_dir / f"{conda_yaml_sha}.yaml"
            _write_to_shared_store(target, conda_yaml_normalized)
            conda_yaml_shared_path = str(target)

        # Pip freeze (only if pip is available — vsnp3 conda envs sometimes
        # ship without pip).
        pip_freeze_normalized = _capture_pip_freeze()
        pip_freeze_sha = (
            hashlib.sha256(pip_freeze_normalized.encode()).hexdigest()
            if pip_freeze_normalized else None
        )
        pip_freeze_shared_path = None
        if pip_freeze_sha and snapshots_dir is not None:
            target = snapshots_dir / f"{pip_freeze_sha}.txt"
            _write_to_shared_store(target, pip_freeze_normalized)
            pip_freeze_shared_path = str(target)

        # System packages: try `dpkg-query` first (works for apt-installed
        # tools); if that returns nothing for a tool, probe the vsnp3 env's
        # bin/ for the binary and run --version. Many lab-tool installs are
        # conda-only; dpkg-query will be empty for them.
        system_packages = _capture_system_packages()
        bin_versions = _capture_install_bin_versions(vsnp3_path)
        for tool, version in bin_versions.items():
            if not system_packages.get(tool):
                system_packages[tool] = version

        env = {
            "conda_env_name": os.environ.get("CONDA_DEFAULT_ENV") or vsnp3_path.name or None,
            "conda_env_yaml_sha256": conda_yaml_sha,
            "conda_env_yaml_path": None,  # filled in per-run by caller
            "_conda_env_yaml_shared_path": conda_yaml_shared_path,  # internal; stripped before serialize
            "pip_freeze_sha256": pip_freeze_sha,
            "pip_freeze_path": None,  # filled in per-run by caller
            "_pip_freeze_shared_path": pip_freeze_shared_path,  # internal; stripped before serialize
            "system_packages": system_packages,
            "python_version": _platform.python_version(),
            "platform": f"{_platform.system()} {_platform.release()} {_platform.machine()}",
        }
        _env_snapshot_cache[cache_key] = env
        return dict(env)


def _capture_conda_meta_fallback(install_path: Path) -> str:
    """Read `<install>/conda-meta/*.json` filenames and synthesize a yaml-like
    manifest. Each file is named `<package>-<version>-<build>.json` by conda
    (this naming is part of conda's on-disk contract). Sorted, hashed → a
    stable fingerprint of the env's contents that doesn't require the conda
    binary. Returns "" if conda-meta doesn't exist (e.g. not a conda env)."""
    conda_meta = install_path / "conda-meta"
    if not conda_meta.is_dir():
        return ""
    try:
        entries = sorted(p.stem for p in conda_meta.glob("*.json"))
    except OSError:
        return ""
    if not entries:
        return ""
    # YAML-like for grep-ability; not a valid `conda env import` source
    # because conda doesn't preserve the channel info in conda-meta filenames.
    # That's fine for fingerprinting.
    body = "\n".join(f"  - {entry}" for entry in entries)
    return f"name: {install_path.name}\n# synthesized from conda-meta/*.json (no conda binary)\ndependencies:\n{body}\n"


_TOOL_VERSION_PROBES: tuple[tuple[str, list[str], int], ...] = (
    # (tool, args, regex-target — search both stdout and stderr)
    ("samtools", ["--version"], 1),
    ("bcftools", ["--version"], 1),
    ("bwa", [], 1),  # bwa with no args prints version on stderr
    ("mafft", ["--version"], 1),
    ("raxmlHPC", ["-v"], 1),
    ("raxml", ["-v"], 1),
    ("iqtree", ["--version"], 1),
    ("iqtree2", ["--version"], 1),
)


def _capture_install_bin_versions(install_path: Path) -> dict[str, str | None]:
    """For each tracked tool, probe `<install>/bin/<tool>` and parse a version.

    Returns a dict keyed by the canonical tool name (samtools, bcftools, bwa,
    mafft, raxml, iqtree). raxml/iqtree have multiple binary names across
    builds (raxmlHPC vs raxml; iqtree2 vs iqtree); whichever is present
    populates the canonical key.
    """
    canonical = {
        "samtools": "samtools",
        "bcftools": "bcftools",
        "bwa": "bwa",
        "mafft": "mafft",
        "raxmlHPC": "raxml",
        "raxml": "raxml",
        "iqtree": "iqtree",
        "iqtree2": "iqtree",
    }
    out: dict[str, str | None] = {tool: None for tool in TRACKED_SYSTEM_PACKAGES}
    bin_dir = install_path / "bin"
    if not bin_dir.is_dir():
        return out
    for binary, args, _ in _TOOL_VERSION_PROBES:
        path = bin_dir / binary
        if not path.is_file():
            continue
        canonical_name = canonical.get(binary)
        if not canonical_name or out.get(canonical_name):
            continue  # already filled by an earlier probe
        try:
            result = subprocess.run(
                [str(path), *args],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        text = (result.stdout or "") + " " + (result.stderr or "")
        # Match like "samtools 1.23.1", "Version: 1.17", "version 2.2.2.7"
        m = re.search(r"\b\d+\.\d+(?:\.\d+)*(?:[A-Za-z0-9._-]+)?\b", text)
        if m:
            out[canonical_name] = m.group(0)
    return out


def _capture_conda_env_yaml() -> str:
    """Run `conda env export` and normalize: drop prefix line, sort dependencies.

    Returns empty string if conda is not available.
    """
    try:
        result = subprocess.run(
            ["conda", "env", "export", "--no-builds"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            logger.warning("conda env export failed: %s", result.stderr)
            return ""
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("conda env export unavailable: %s", e)
        return ""

    return _normalize_conda_env_yaml(result.stdout)


def _normalize_conda_env_yaml(yaml_text: str) -> str:
    """Drop the `prefix:` line (embeds absolute path) and sort dependencies + channels.

    No external YAML lib needed; conda's output is deterministic enough for
    line-based handling.
    """
    lines = yaml_text.splitlines()
    out: list[str] = []
    section: str | None = None
    section_items: list[str] = []

    def flush_section() -> None:
        if section and section_items:
            out.append(f"{section}:")
            out.extend(sorted(section_items))
        elif section:
            out.append(f"{section}:")

    for line in lines:
        if line.startswith("prefix:"):
            continue
        if line.endswith(":") and not line.startswith(" "):
            # New top-level section
            flush_section()
            section = line[:-1]
            section_items = []
            continue
        if section in {"dependencies", "channels"} and line.startswith("  - "):
            section_items.append(line)
            continue
        if section in {"dependencies", "channels"} and line.startswith("    - "):
            # pip sub-deps under dependencies; preserve as-is, sort with parents
            section_items.append(line)
            continue
        # Anything else: flush current section, emit verbatim
        flush_section()
        section = None
        section_items = []
        out.append(line)
    flush_section()

    return "\n".join(out) + "\n"


def _capture_pip_freeze() -> str:
    """Run `pip freeze` and sort lines."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode != 0:
            logger.warning("pip freeze failed: %s", result.stderr)
            return ""
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("pip freeze unavailable: %s", e)
        return ""

    lines = sorted(line for line in result.stdout.splitlines() if line.strip())
    return "\n".join(lines) + "\n"


def _capture_system_packages() -> dict[str, str | None]:
    """Run `dpkg -l <pkg>...` for tracked tools, parse versions."""
    out: dict[str, str | None] = {pkg: None for pkg in TRACKED_SYSTEM_PACKAGES}
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f=${Package}\\t${Version}\\n", *TRACKED_SYSTEM_PACKAGES],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("dpkg-query unavailable: %s", e)
        return out

    for line in result.stdout.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            pkg, version = parts
            if pkg in out:
                out[pkg] = version
    return out


def _write_to_shared_store(target: Path, content: str) -> None:
    """Stat-first short-circuit; os.link with EEXIST fallback for race safety.

    Identical content -> identical hash -> identical file, so a lost race is
    benign. Multiple users writing the same env snapshot do not corrupt.
    """
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(tmp_fd, "w") as f:
            f.write(content)
        # tempfile.mkstemp() creates files mode 0600. The shared-store dir is
        # setgid kapurlab-admins so other lab admins can list it; widen the
        # snapshot itself to 0640 so they can also read it.
        os.chmod(tmp_path, 0o640)
        try:
            os.link(tmp_path, str(target))
        except FileExistsError:
            pass
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)


def copy_env_snapshot_into_run(env: dict[str, Any], provenance_dir: Path) -> dict[str, Any]:
    """Copy shared env snapshot files into a per-run _provenance/ dir.

    Returns a NEW environment dict with conda_env_yaml_path and
    pip_freeze_path set to the per-run paths. The internal `_*_shared_path`
    keys are stripped from the return value (they're for writer use only).
    """
    provenance_dir.mkdir(parents=True, exist_ok=True)
    out = dict(env)

    shared_yaml = out.pop("_conda_env_yaml_shared_path", None)
    if shared_yaml:
        dst = provenance_dir / "conda_env.yaml"
        shutil.copy2(shared_yaml, dst)
        out["conda_env_yaml_path"] = str(dst)

    shared_pip = out.pop("_pip_freeze_shared_path", None)
    if shared_pip:
        dst = provenance_dir / "pip_freeze.txt"
        shutil.copy2(shared_pip, dst)
        out["pip_freeze_path"] = str(dst)

    return out


# ---------------------------------------------------------------------------
# Capture: reference folder
# ---------------------------------------------------------------------------


def capture_reference_state(cfg: dict[str, Any], reference_name: str) -> dict[str, Any]:
    """Capture reference folder hash + per-file manifest.

    Cached per uvicorn lifetime per (reference_name, folder mtime) since
    references can in principle be updated; using mtime as a soft refresh
    signal is cheaper than always re-hashing. Service restart force-refreshes.
    """
    refs_root = Path(cfg.get(
        "vsnp3_reference_options_root",
        "/srv/kapurlab/refs/vsnp3/reference_options",
    ))
    folder = refs_root / reference_name
    if not folder.is_dir():
        raise DispatchFailed(f"reference folder not found: {folder}")

    folder_resolved = folder.resolve()
    resolved_via_symlink = (folder_resolved != folder)

    # Cache key: (folder, mtime). mtime invalidation is best-effort; not
    # security-critical since we always recompute the manifest hash on miss.
    mtime = folder.stat().st_mtime
    key = f"{folder_resolved}::{mtime}"
    cached = _reference_cache.get(key)
    if cached is not None:
        return dict(cached)

    with _capture_lock:
        cached = _reference_cache.get(key)
        if cached is not None:
            return dict(cached)

        rolled, files = compute_folder_manifest(folder_resolved)
        state = {
            "name": reference_name,
            "path": str(folder),
            "folder_manifest_sha256": rolled,
            "files": files,
            "resolved_via_symlink": resolved_via_symlink,
        }
        _reference_cache[key] = state
        return dict(state)


# ---------------------------------------------------------------------------
# Read: edit record refs (per-sample patchlog)
# ---------------------------------------------------------------------------


def read_edit_record_refs(
    sample_dir: Path,
    sample: str,
    run_started_at: datetime,
) -> list[dict[str, Any]]:
    """Read per-sample patchlog and return all edit records ≤ run_started_at.

    The actual edit log is per-sample at
    <sample_dir>/vcf_edits/<sample>_patchlog.jsonl (NOT the project-wide
    audit/edits.jsonl, which is currently unused). Edits accumulate (each
    layered on prior patched VCF), so all records up to dispatch time are
    forensically relevant.

    Each EditRecordRef:
      - audit_log: absolute path to the patchlog
      - line_number: 1-indexed line in file
      - record_sha256: SHA-256 of the JSON line bytes (survives line renumbering)

    Returns empty list if patchlog doesn't exist.
    """
    patchlog = sample_dir / "vcf_edits" / f"{sample}_patchlog.jsonl"
    if not patchlog.is_file():
        return []

    refs: list[dict[str, Any]] = []
    with patchlog.open("rb") as f:
        for line_num, raw in enumerate(f, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                logger.warning("malformed patchlog line %s:%d", patchlog, line_num)
                continue
            ts_raw = entry.get("timestamp") or entry.get("ts") or entry.get("at")
            if not ts_raw:
                # No timestamp: include conservatively (better to over-include for forensics)
                pass
            else:
                try:
                    ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts > run_started_at:
                        continue
                except (ValueError, TypeError):
                    pass
            refs.append({
                "audit_log": str(patchlog),
                "line_number": line_num,
                "record_sha256": hashlib.sha256(stripped).hexdigest(),
            })
    return refs


# ---------------------------------------------------------------------------
# VCF DB selections (step2 only)
# ---------------------------------------------------------------------------


def build_vcf_db_blocks(
    cfg: dict[str, Any],
    reference_name: str,
    resolved_vcf_db_folders: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build (vcf_db_selections, vcf_db_inventory_at_dispatch) blocks.

    Takes the output of vsnp_gui's existing _resolved_vcf_db_folders helper
    (a list of dicts with at minimum {path, reference, sample_count, enabled,
    scope}). Filters to selections matching reference_name; selections include
    folder_manifest_sha256 for tamper detection. Inventory captures everything
    available for the user at dispatch (so a missing-DB-later question can
    distinguish 'opted out' from 'didn't exist').
    """
    selections: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []

    for entry in resolved_vcf_db_folders:
        if entry.get("reference") != reference_name:
            continue

        path = Path(entry["path"])
        present = path.is_dir()

        inventory.append({
            "path": str(path),
            "scope": entry.get("scope", "shared"),
            "sample_count": entry.get("sample_count"),
            "present": present,
        })

        # Selections are the entries the user has enabled (didn't opt out of)
        if entry.get("enabled", True) and present:
            try:
                manifest_sha, _ = compute_folder_manifest(path)
            except (OSError, DispatchFailed):
                manifest_sha = None
            selections.append({
                "path": str(path),
                "scope": entry.get("scope", "shared"),
                "enabled": True,
                "sample_count": entry.get("sample_count"),
                "folder_manifest_sha256": manifest_sha,
            })

    return selections, inventory


# ---------------------------------------------------------------------------
# Bash sentinel wrapper (for main.py to insert into step1 batch script)
# ---------------------------------------------------------------------------


def step1_sample_command_with_sentinels(vsnp3_command: str) -> str:
    """Wrap a vsnp3_step1.py invocation with sentinel emission.

    Caller (main.py building the bash batch) inserts this where the raw
    vsnp3_step1.py invocation currently lives in `run_sample()`. The bash
    function must already be `cd`'d into the per-sample directory; sentinels
    are written to ./.provenance/.

    Returns multi-line bash. The exit code of the wrapped command is
    preserved; the wrapper returns it via the function return.
    """
    return (
        "mkdir -p .provenance\n"
        "date -u +%s.%N > .provenance/started_at\n"
        f"{vsnp3_command}\n"
        "STATUS=$?\n"
        "echo $STATUS > .provenance/exit_code\n"
        "date -u +%s.%N > .provenance/finished_at\n"
        "return $STATUS\n"
    )


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------


def _build_actor(user: str, ood_session_id: str | None) -> dict[str, Any]:
    return {
        "user": user,
        "uid": os.getuid() if hasattr(os, "getuid") else None,
        "hostname": socket.gethostname(),
        "ood_session_id": ood_session_id,
    }


def _build_step1_per_sample_cli(
    cfg: dict[str, Any],
    sample: str,
    reference_name: str,
    sample_dir: Path,
) -> dict[str, Any]:
    """Reconstruct the per-sample vsnp3_step1.py invocation."""
    vsnp3_path = Path(cfg.get("vsnp3_path", "/srv/kapurlab/tools/vsnp3"))
    cmd = (
        f"{vsnp3_path}/bin/vsnp3_step1.py "
        f"-r1 {sample}_R1.fastq.gz -r2 {sample}_R2.fastq.gz "
        f"-t {reference_name}"
    )
    return {
        "command": cmd,
        "flags": ["-t", reference_name],
        "env_vars": _capture_env_vars_allowlisted(),
        "env_capture_policy": "allowlist_v1",
    }


def _build_step1_batch_cli(
    cfg: dict[str, Any],
    samples: list[str],
    reference_name: str,
) -> dict[str, Any]:
    return {
        "command": f"bash step1_batch.sh  # {len(samples)} samples, ref={reference_name}",
        "flags": [],
        "env_vars": _capture_env_vars_allowlisted(),
        "env_capture_policy": "allowlist_v1",
    }


def _build_step2_cli(cli_command: str, cli_flags: list[str]) -> dict[str, Any]:
    return {
        "command": cli_command,
        "flags": list(cli_flags),
        "env_vars": _capture_env_vars_allowlisted(),
        "env_capture_policy": "allowlist_v1",
    }


_ENV_ALLOWLIST = (
    "PATH", "LD_LIBRARY_PATH", "CONDA_DEFAULT_ENV", "CONDA_PREFIX",
    "PYTHONPATH", "PYTHONWARNINGS", "VSNP3_BOOTSTRAP", "TMPDIR",
    "OMP_NUM_THREADS",
)


def _capture_env_vars_allowlisted() -> dict[str, str | None]:
    """Capture allow-listed env vars from os.environ.

    Per the locked V1 policy: allow-list rather than capture-all-with-redactor.
    Expansion is a one-line PR adding a key to _ENV_ALLOWLIST.
    """
    return {k: os.environ.get(k) for k in _ENV_ALLOWLIST}


def _build_dispatch_state(
    *,
    actor: dict[str, Any],
    vsnp_gui: dict[str, Any],
    vsnp3: dict[str, Any],
    environment: dict[str, Any],
    reference: dict[str, Any],
    inputs: list[dict[str, Any]],
    cli: dict[str, Any],
    vcf_db_selections: list[dict[str, Any]] | None = None,
    vcf_db_inventory_at_dispatch: list[dict[str, Any]] | None = None,
    edited_samples_at_run_time: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the dispatch_state dict.

    The same data is later both flattened into the top-level record AND nested
    as `dispatch_state` for diff_dispatch_vs_final() to compare against.
    """
    state: dict[str, Any] = {
        "actor": actor,
        "vsnp_gui": vsnp_gui,
        "vsnp3": vsnp3,
        "environment": environment,
        "reference": reference,
        "inputs": inputs,
        "cli": cli,
    }
    if vcf_db_selections is not None:
        state["vcf_db_selections"] = vcf_db_selections
    if vcf_db_inventory_at_dispatch is not None:
        state["vcf_db_inventory_at_dispatch"] = vcf_db_inventory_at_dispatch
    if edited_samples_at_run_time is not None:
        state["edited_samples_at_run_time"] = edited_samples_at_run_time
    return state


def _initial_record_from_dispatch_state(
    *,
    step: str,
    run_id: str,
    pipeline_run_id: str | None,
    parent_run_ids: list[str],
    started_at: datetime,
    dispatch_state: dict[str, Any],
) -> dict[str, Any]:
    """Build the initial run_metadata.json record.

    Spreads dispatch_state at top level AND nests a frozen copy under
    `dispatch_state` per the locked sub-block design.
    """
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "step": step,
        "run_id": run_id,
        "pipeline_run_id": pipeline_run_id,
        "parent_run_ids": list(parent_run_ids),
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "duration_seconds": None,
        "status": "running",
        "exit_code": None,
        "trust_scope": dict(DEFAULT_TRUST_SCOPE),
    }
    # Spread dispatch state to top level
    for key in (
        "actor", "vsnp_gui", "vsnp3", "environment", "reference",
        "inputs", "vcf_db_selections", "vcf_db_inventory_at_dispatch",
        "edited_samples_at_run_time", "cli",
    ):
        if key in dispatch_state:
            record[key] = dispatch_state[key]
    # Empty finalize-time fields
    record.setdefault("outputs", [])
    record.setdefault("qc", {"samples_excluded": [], "exclude_source": None})
    # Frozen dispatch copy
    record["dispatch_state"] = dispatch_state
    return record


# ---------------------------------------------------------------------------
# Public: step1 dispatch + finalize
# ---------------------------------------------------------------------------


def dispatch_step1_batch(
    cfg: dict[str, Any],
    project_dir: Path,
    samples: list[str],
    reference_name: str,
    *,
    user: str,
    ood_session_id: str | None,
) -> tuple[str, dict[str, str]]:
    """Pre-create per-sample run_metadata.json + batch roll-up at step1 dispatch.

    Returns (batch_run_id, {sample: per_sample_run_id}).

    Failure to capture state or write any metadata file raises DispatchFailed;
    caller should not start the underlying job. Per the locked policy:
    hard fail at dispatch.
    """
    step1_dir = project_dir / "step1"
    if not step1_dir.is_dir():
        raise DispatchFailed(f"step1 dir does not exist: {step1_dir}")

    # Capture shared state once
    deploy_path = Path(cfg.get("vsnp_gui_deploy_path", "/srv/kapurlab/tools/vsnp_gui"))
    vsnp3_path = Path(cfg.get("vsnp3_path", "/srv/kapurlab/tools/vsnp3"))
    threshold = int(
        cfg.get("provenance", {}).get("hash_max_bytes", DEFAULT_HASH_THRESHOLD_BYTES)
    )

    vsnp_gui_state = capture_vsnp_gui_state(deploy_path)
    vsnp3_state = capture_vsnp3_state(vsnp3_path)
    env_shared = capture_env_snapshot(cfg)
    reference = capture_reference_state(cfg, reference_name)
    actor = _build_actor(user, ood_session_id)

    started_at = datetime.now(tz=timezone.utc)
    batch_run_id = str(uuid.uuid4())
    sample_run_ids: dict[str, str] = {s: str(uuid.uuid4()) for s in samples}

    # Per-sample dispatch
    for sample in samples:
        sample_dir = step1_dir / sample
        if not sample_dir.is_dir():
            raise DispatchFailed(f"sample dir does not exist: {sample_dir}")

        try:
            inputs = _build_step1_inputs(sample_dir, sample, threshold)
        except (OSError, DispatchFailed) as e:
            raise DispatchFailed(f"failed to capture inputs for {sample}: {e}") from e

        # Per-sample env snapshot copy. copy_env_snapshot_into_run mutates the
        # returned env dict to point at per-run paths; pass a fresh copy of
        # the cached shared env each time.
        provenance_dir = sample_dir / "_provenance"
        env_for_sample = copy_env_snapshot_into_run(env_shared, provenance_dir)

        cli = _build_step1_per_sample_cli(cfg, sample, reference_name, sample_dir)

        dispatch_state = _build_dispatch_state(
            actor=actor,
            vsnp_gui=vsnp_gui_state,
            vsnp3=vsnp3_state,
            environment=env_for_sample,
            reference=reference,
            inputs=inputs,
            cli=cli,
        )
        record = _initial_record_from_dispatch_state(
            step="step1",
            run_id=sample_run_ids[sample],
            pipeline_run_id=None,
            parent_run_ids=[],
            started_at=started_at,
            dispatch_state=dispatch_state,
        )
        _atomic_json_write(sample_dir / "run_metadata.json", record)
        # Pre-create .provenance/ so the bash sentinel writes won't fail on
        # mkdir if the parallel worker is racy.
        (sample_dir / ".provenance").mkdir(exist_ok=True)

    # Batch roll-up
    batch_provenance_dir = step1_dir / "_provenance"
    env_for_batch = copy_env_snapshot_into_run(env_shared, batch_provenance_dir)
    batch_cli = _build_step1_batch_cli(cfg, samples, reference_name)
    batch_dispatch_state = _build_dispatch_state(
        actor=actor,
        vsnp_gui=vsnp_gui_state,
        vsnp3=vsnp3_state,
        environment=env_for_batch,
        reference=reference,
        inputs=[],  # batch level: per-sample inputs in per-sample records
        cli=batch_cli,
    )
    batch_record = _initial_record_from_dispatch_state(
        step="step1",
        run_id=batch_run_id,
        pipeline_run_id=None,
        parent_run_ids=list(sample_run_ids.values()),
        started_at=started_at,
        dispatch_state=batch_dispatch_state,
    )
    _atomic_json_write(step1_dir / "run_metadata.json", batch_record)

    return batch_run_id, sample_run_ids


def _build_step1_inputs(
    sample_dir: Path,
    sample: str,
    threshold_bytes: int,
) -> list[dict[str, Any]]:
    """Find R1/R2 fastq files for a sample and build the inputs block.

    Handles both Illumina (`<sample>_R1.fastq.gz`) and SRA (`<sample>_1.fastq.gz`)
    naming conventions — same as the bash step1 batch script.
    """
    inputs: list[dict[str, Any]] = []
    # (mate_label, [glob_patterns_to_try_in_order])
    mate_patterns = (
        ("R1", [f"{sample}*R1*.f*q.gz", f"{sample}*_1.f*q.gz", "*R1*.f*q.gz", "*_1.f*q.gz"]),
        ("R2", [f"{sample}*R2*.f*q.gz", f"{sample}*_2.f*q.gz", "*R2*.f*q.gz", "*_2.f*q.gz"]),
    )
    for label, patterns in mate_patterns:
        candidates: list[Path] = []
        for pat in patterns:
            candidates = sorted(sample_dir.glob(pat))
            if candidates:
                break
        if not candidates:
            raise DispatchFailed(f"no {label} fastq found for sample {sample} in {sample_dir}")
        # Take the first match if multiple
        path = candidates[0].resolve()
        sha, identity = compute_input_identity(path, threshold_bytes)
        stat = path.stat()
        inputs.append({
            "role": "fastq",
            "sample": sample,
            "filename": path.name,
            "abs_path": str(path),
            "staged_path": None,  # deferred to V2
            "size_bytes": stat.st_size,
            "sha256": sha,
            "identity_method": identity,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return inputs


def finalize_step1_batch(
    project_dir: Path,
    batch_run_id: str,
    exit_code: int,
    started_at: datetime,
    finished_at: datetime,
) -> None:
    """Walk per-sample dirs, parse sentinels, rewrite per-sample metadata as
    terminal, then finalize the batch roll-up.

    Per the locked soft-fail policy at finalize: per-sample errors are logged
    and skipped (with that sample's metadata left in status=running, to be
    cleaned up by the janitor). Errors during the batch roll-up are raised
    (the caller's outer try/except in JobManager logs them to
    metadata_failures.jsonl).
    """
    step1_dir = project_dir / "step1"
    duration = (finished_at - started_at).total_seconds()

    # Per-sample finalize
    for sample_dir in sorted(step1_dir.iterdir()):
        if not sample_dir.is_dir() or sample_dir.name.startswith(("_", ".")):
            continue
        per_sample_path = sample_dir / "run_metadata.json"
        if not per_sample_path.is_file():
            continue  # not a sample dir we created
        try:
            _finalize_step1_sample(sample_dir, per_sample_path)
        except Exception:
            logger.exception("finalize_step1_sample failed for %s", sample_dir)
            # Continue with other samples; soft fail at finalize.

    # Batch roll-up
    batch_path = step1_dir / "run_metadata.json"
    if not batch_path.is_file():
        raise WriterError(f"step1 batch roll-up missing at finalize: {batch_path}")
    rec = _read_json(batch_path)
    if rec.get("run_id") != batch_run_id:
        logger.warning(
            "batch run_id mismatch at finalize: expected %s, got %s",
            batch_run_id, rec.get("run_id"),
        )
    rec["finished_at"] = finished_at.isoformat()
    rec["duration_seconds"] = duration
    rec["status"] = "ok" if exit_code == 0 else "failed"
    rec["exit_code"] = exit_code
    rec["outputs"] = _scan_step1_batch_outputs(step1_dir)
    _atomic_json_write(batch_path, rec)


def _finalize_step1_sample(sample_dir: Path, metadata_path: Path) -> None:
    rec = _read_json(metadata_path)
    sentinels_dir = sample_dir / ".provenance"
    started = _read_sentinel_epoch(sentinels_dir / "started_at")
    finished = _read_sentinel_epoch(sentinels_dir / "finished_at")
    exit_code = _read_sentinel_int(sentinels_dir / "exit_code")

    # Determine status
    if finished is None or exit_code is None:
        status = "unknown_terminated"
        if finished is None:
            finished = datetime.now(tz=timezone.utc)
    elif exit_code == 0:
        status = "ok"
    else:
        status = "failed"

    rec["finished_at"] = finished.isoformat()
    if started is not None:
        rec["started_at"] = started.isoformat()  # sentinel is more accurate than dispatch time
        rec["duration_seconds"] = (finished - started).total_seconds()
    rec["status"] = status
    rec["exit_code"] = exit_code
    rec["outputs"] = _scan_step1_sample_outputs(sample_dir)
    _atomic_json_write(metadata_path, rec)


def _read_sentinel_epoch(path: Path) -> datetime | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text().strip()
        epoch = float(text)
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (OSError, ValueError):
        return None


def _read_sentinel_int(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def _scan_step1_sample_outputs(sample_dir: Path) -> list[dict[str, Any]]:
    """Find expected step1 outputs (the *_zc.vcf and BAM).

    vsnp3 step1 writes outputs into `<sample>/alignment_<ref>/` subdirs, not
    at the sample top level — so the glob has to be recursive. Also includes
    `*_filtered_hapall_annotated.vcf` (the QC-flag-detection target) since
    its presence is a finer-grained completion signal than just *_zc.vcf.
    """
    outputs: list[dict[str, Any]] = []
    for pattern in (
        "**/*_zc.vcf",
        "**/*_filtered_hapall_annotated.vcf",
        "**/*_nodup.bam",
    ):
        for p in sorted(sample_dir.glob(pattern)):
            stat = p.stat()
            outputs.append({
                "path": str(p.relative_to(sample_dir.parent.parent)),
                "exists": True,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            })
    return outputs


def _scan_step1_batch_outputs(step1_dir: Path) -> list[dict[str, Any]]:
    """Batch roll-up doesn't have specific outputs; the step1 directory
    itself is the output. Return an empty list."""
    return []


# ---------------------------------------------------------------------------
# Public: step2 dispatch + finalize
# ---------------------------------------------------------------------------


def dispatch_step2(
    cfg: dict[str, Any],
    project_dir: Path,
    reference_name: str,
    cli_command: str,
    cli_flags: list[str],
    *,
    user: str,
    ood_session_id: str | None,
    is_shared: bool,
    resolved_vcf_db_folders: list[dict[str, Any]],
    step2_run_dir: Path | None = None,
) -> tuple[str, str]:
    """Dispatch step2: write pipeline_run record, then step2 run_metadata.json.

    Returns (step2_run_id, pipeline_run_id).

    If ``step2_run_dir`` is supplied (timestamped layout), run_metadata.json
    and _provenance/ are written there instead of project_dir/step2/.

    Raises:
        Step2DispatchBlocked: if is_shared=True and step1 samples are still
            running. Caller should surface as HTTP 409.
        DispatchFailed: any other failure during state capture or write.

    Note: `is_shared` is added to the §5 signature; main.py determines this
    via path comparison to cfg["shared_projects_root"].
    `resolved_vcf_db_folders` is the output of vsnp_gui's existing
    _resolved_vcf_db_folders helper; passed in rather than re-derived.
    """
    step1_dir = project_dir / "step1"
    step2_dir = step2_run_dir if step2_run_dir is not None else project_dir / "step2"
    step2_dir.mkdir(parents=True, exist_ok=True)

    # Walk step1 sample dirs, collect run_ids and detect in-flight samples
    step1_records, missing_samples, running_samples = _collect_step1_records(step1_dir)

    if is_shared and running_samples:
        raise Step2DispatchBlocked(running_samples)

    # Capture shared state
    deploy_path = Path(cfg.get("vsnp_gui_deploy_path", "/srv/kapurlab/tools/vsnp_gui"))
    vsnp3_path = Path(cfg.get("vsnp3_path", "/srv/kapurlab/tools/vsnp3"))

    vsnp_gui_state = capture_vsnp_gui_state(deploy_path)
    vsnp3_state = capture_vsnp3_state(vsnp3_path)
    env_shared = capture_env_snapshot(cfg)
    reference = capture_reference_state(cfg, reference_name)
    actor = _build_actor(user, ood_session_id)

    # VCF DB blocks
    vcf_db_selections, vcf_db_inventory = build_vcf_db_blocks(
        cfg, reference_name, resolved_vcf_db_folders,
    )

    # Edit record refs across all samples
    edited_samples = _collect_edited_samples(step1_dir, step1_records)

    started_at = datetime.now(tz=timezone.utc)
    step2_run_id = str(uuid.uuid4())
    pipeline_run_id = str(uuid.uuid4())

    # Per-run env snapshot copy
    provenance_dir = step2_dir / "_provenance"
    env_for_step2 = copy_env_snapshot_into_run(env_shared, provenance_dir)

    cli = _build_step2_cli(cli_command, cli_flags)

    # Inputs at step2 are the consumed step1 VCFs; capture as a list
    inputs = _build_step2_inputs(step1_dir, step1_records, cfg)

    dispatch_state = _build_dispatch_state(
        actor=actor,
        vsnp_gui=vsnp_gui_state,
        vsnp3=vsnp3_state,
        environment=env_for_step2,
        reference=reference,
        inputs=inputs,
        cli=cli,
        vcf_db_selections=vcf_db_selections,
        vcf_db_inventory_at_dispatch=vcf_db_inventory,
        edited_samples_at_run_time=edited_samples,
    )

    parent_run_ids = [r["run_id"] for r in step1_records]

    # Pipeline-run record FIRST (per locked write ordering)
    pipeline_record = _build_pipeline_run_record(
        pipeline_run_id=pipeline_run_id,
        project_dir=project_dir,
        step1_records=step1_records,
        missing_samples=missing_samples,
        running_samples=running_samples,
        step2_run_id=step2_run_id,
        step2_started_at=started_at,
        actor=actor,
    )
    pipeline_path = (
        project_dir / "_provenance" / "pipeline_runs" / f"{pipeline_run_id}.json"
    )
    _atomic_json_write(pipeline_path, pipeline_record)

    # Step2 run_metadata.json
    step2_record = _initial_record_from_dispatch_state(
        step="step2",
        run_id=step2_run_id,
        pipeline_run_id=pipeline_run_id,
        parent_run_ids=parent_run_ids,
        started_at=started_at,
        dispatch_state=dispatch_state,
    )
    _atomic_json_write(step2_dir / "run_metadata.json", step2_record)

    return step2_run_id, pipeline_run_id


def _collect_step1_records(
    step1_dir: Path,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Walk step1/<sample>/ for run_metadata.json files.

    Returns (records, missing_samples, running_samples).
      - records: list of {run_id, sample, status, vsnp3_version, ...} dicts
        for samples with a parseable run_metadata.json (any status).
      - missing_samples: sample names that have a directory but no metadata
        (predates T-07 or dispatch failed).
      - running_samples: sample names whose metadata is status=running.
    """
    records: list[dict[str, Any]] = []
    missing: list[str] = []
    running: list[str] = []

    if not step1_dir.is_dir():
        return records, missing, running

    for sample_dir in sorted(step1_dir.iterdir()):
        if not sample_dir.is_dir() or sample_dir.name.startswith(("_", ".")):
            continue
        sample = sample_dir.name
        meta_path = sample_dir / "run_metadata.json"
        if not meta_path.is_file():
            missing.append(sample)
            continue
        try:
            rec = _read_json(meta_path)
        except (OSError, json.JSONDecodeError):
            missing.append(sample)
            continue

        status = rec.get("status")
        if status == "running":
            running.append(sample)

        records.append({
            "run_id": rec.get("run_id"),
            "sample": sample,
            "metadata_path": f"step1/{sample}/run_metadata.json",
            "status": status,
            "vsnp3_version": rec.get("vsnp3", {}).get("version"),
            "reference_name": rec.get("reference", {}).get("name"),
            "reference_folder_manifest_sha256": rec.get("reference", {}).get(
                "folder_manifest_sha256"
            ),
            "environment_hash": _digest_env_for_consistency(rec.get("environment", {})),
        })

    return records, missing, running


def _digest_env_for_consistency(env_block: dict[str, Any]) -> str | None:
    """Hash the env-identifying fields for cross-run consistency comparison."""
    parts = [
        env_block.get("conda_env_yaml_sha256") or "",
        env_block.get("pip_freeze_sha256") or "",
    ]
    sp = env_block.get("system_packages") or {}
    parts.extend(f"{k}={sp.get(k) or ''}" for k in TRACKED_SYSTEM_PACKAGES)
    if not any(parts):
        return None
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _collect_edited_samples(
    step1_dir: Path,
    step1_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect edit_record_refs for every sample with a non-empty patchlog."""
    now = datetime.now(tz=timezone.utc)
    edited: list[dict[str, Any]] = []
    for rec in step1_records:
        sample = rec["sample"]
        sample_dir = step1_dir / sample
        refs = read_edit_record_refs(sample_dir, sample, now)
        if refs:
            edited.append({"sample": sample, "edit_record_refs": refs})
    return edited


def _build_step2_inputs(
    step1_dir: Path,
    step1_records: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Step2 inputs are the per-sample *_zc.vcf files from step1."""
    threshold = int(
        cfg.get("provenance", {}).get("hash_max_bytes", DEFAULT_HASH_THRESHOLD_BYTES)
    )
    inputs: list[dict[str, Any]] = []
    for rec in step1_records:
        sample = rec["sample"]
        sample_dir = step1_dir / sample
        vcfs = sorted(sample_dir.glob("*_zc.vcf"))
        if not vcfs:
            continue
        vcf = vcfs[0]
        try:
            sha, identity = compute_input_identity(vcf, threshold)
        except OSError:
            sha, identity = None, "size_mtime_path"
        stat = vcf.stat()
        inputs.append({
            "role": "vcf",
            "sample": sample,
            "filename": vcf.name,
            "abs_path": str(vcf.resolve()),
            "staged_path": None,
            "size_bytes": stat.st_size,
            "sha256": sha,
            "identity_method": identity,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return inputs


def _build_pipeline_run_record(
    *,
    pipeline_run_id: str,
    project_dir: Path,
    step1_records: list[dict[str, Any]],
    missing_samples: list[str],
    running_samples: list[str],
    step2_run_id: str,
    step2_started_at: datetime,
    actor: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the pipeline_run record."""
    consumed_complete = (not missing_samples) and (not running_samples)

    refs = {r["reference_folder_manifest_sha256"] for r in step1_records if r.get("reference_folder_manifest_sha256")}
    versions = {r["vsnp3_version"] for r in step1_records if r.get("vsnp3_version")}
    env_hashes = {r["environment_hash"] for r in step1_records if r.get("environment_hash")}

    warnings_list: list[str] = []
    if missing_samples:
        warnings_list.append(
            f"{len(missing_samples)} step1 samples have no run_metadata.json "
            f"(predates T-07 or dispatch failed): {missing_samples}"
        )
    if running_samples:
        warnings_list.append(
            f"{len(running_samples)} step1 samples were still running at step2 "
            f"dispatch: {running_samples}; their run_ids reflect dispatch state, "
            f"not necessarily what was consumed by step2"
        )
    if len(refs) > 1:
        warnings_list.append(f"step1 runs used {len(refs)} different references")
    if len(versions) > 1:
        warnings_list.append(f"step1 runs used {len(versions)} different vsnp3 versions")
    if len(env_hashes) > 1:
        warnings_list.append(f"step1 runs used {len(env_hashes)} different environments")

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "pipeline_run",
        "pipeline_run_id": pipeline_run_id,
        "created_at": step2_started_at.isoformat(),
        "created_by": actor["user"],
        "label": None,
        "step1_runs": [
            {
                "run_id": r["run_id"],
                "sample": r["sample"],
                "metadata_path": r["metadata_path"],
                "status": r["status"],
                "vsnp3_version": r["vsnp3_version"],
                "reference_name": r["reference_name"],
                "reference_folder_manifest_sha256": r["reference_folder_manifest_sha256"],
            }
            for r in step1_records
        ],
        "step2_runs": [
            {
                "run_id": step2_run_id,
                "metadata_path": "step2/run_metadata.json",
                "status": "running",
                "consumed_step1_run_ids": [r["run_id"] for r in step1_records],
                "consumed_step1_run_ids_complete": consumed_complete,
                "tree_outputs": [],
            }
        ],
        "consistency": {
            "all_step1_same_reference": (len(refs) <= 1),
            "all_step1_same_vsnp3_version": (len(versions) <= 1),
            "all_step1_same_environment_hash": (len(env_hashes) <= 1),
            "warnings": warnings_list,
        },
        "trust_scope": dict(DEFAULT_TRUST_SCOPE),
    }


def finalize_step2(
    project_dir: Path,
    step2_run_id: str,
    exit_code: int,
    started_at: datetime,
    finished_at: datetime,
    step2_run_dir: Path | None = None,
) -> None:
    """Rewrite step2 run_metadata.json with terminal status; update pipeline_run.

    If ``step2_run_dir`` is supplied (timestamped layout), reads/writes
    run_metadata.json from there instead of project_dir/step2/.
    """
    step2_dir = step2_run_dir if step2_run_dir is not None else project_dir / "step2"
    metadata_path = step2_dir / "run_metadata.json"
    if not metadata_path.is_file():
        raise WriterError(f"step2 run_metadata.json missing at finalize: {metadata_path}")

    rec = _read_json(metadata_path)
    if rec.get("run_id") != step2_run_id:
        logger.warning(
            "step2 run_id mismatch at finalize: expected %s, got %s",
            step2_run_id, rec.get("run_id"),
        )

    rec["finished_at"] = finished_at.isoformat()
    rec["duration_seconds"] = (finished_at - started_at).total_seconds()
    rec["status"] = "ok" if exit_code == 0 else "failed"
    rec["exit_code"] = exit_code
    rec["outputs"] = _scan_step2_outputs(step2_dir)
    rec["qc"] = _scan_step2_qc(step2_dir)
    _atomic_json_write(metadata_path, rec)

    # Update the pipeline_run record's step2_runs[0].status + tree_outputs
    pipeline_run_id = rec.get("pipeline_run_id")
    if pipeline_run_id:
        _finalize_pipeline_run(
            project_dir / "_provenance" / "pipeline_runs" / f"{pipeline_run_id}.json",
            step2_run_id=step2_run_id,
            status=rec["status"],
            tree_outputs=[o["path"] for o in rec["outputs"] if o["path"].endswith(".tre")],
        )


def _scan_step2_outputs(step2_dir: Path) -> list[dict[str, Any]]:
    """Scan step2 group dirs for tree files and key outputs."""
    outputs: list[dict[str, Any]] = []
    for tree in sorted(step2_dir.rglob("*.tre")):
        stat = tree.stat()
        outputs.append({
            "path": str(tree.relative_to(step2_dir.parent)),
            "exists": True,
            "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return outputs


def _scan_step2_qc(step2_dir: Path) -> dict[str, Any]:
    """Scan for remove_from_analysis.xlsx and surface excluded samples."""
    remove_xlsx = step2_dir / "remove_from_analysis.xlsx"
    qc: dict[str, Any] = {"samples_excluded": [], "exclude_source": None}
    if remove_xlsx.is_file():
        qc["exclude_source"] = "step2/remove_from_analysis.xlsx"
        # Reading xlsx requires openpyxl; defer to V2 if not available.
        try:
            import openpyxl  # type: ignore
            wb = openpyxl.load_workbook(remove_xlsx, read_only=True, data_only=True)
            ws = wb.active
            samples = [
                str(row[0]).strip() for row in ws.iter_rows(values_only=True)
                if row and row[0] is not None
            ]
            qc["samples_excluded"] = samples
        except Exception:
            logger.exception("failed to parse remove_from_analysis.xlsx")
    return qc


def _finalize_pipeline_run(
    path: Path,
    *,
    step2_run_id: str,
    status: str,
    tree_outputs: list[str],
) -> None:
    if not path.is_file():
        logger.warning("pipeline_run record missing at finalize: %s", path)
        return
    rec = _read_json(path)
    for entry in rec.get("step2_runs", []):
        if entry.get("run_id") == step2_run_id:
            entry["status"] = status
            entry["tree_outputs"] = tree_outputs
    _atomic_json_write(path, rec)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_HASH_THRESHOLD_BYTES",
    "WriterError",
    "DispatchFailed",
    "Step2DispatchBlocked",
    "compute_folder_manifest",
    "compute_input_identity",
    "capture_vsnp_gui_state",
    "capture_vsnp3_state",
    "capture_env_snapshot",
    "capture_reference_state",
    "copy_env_snapshot_into_run",
    "read_edit_record_refs",
    "build_vcf_db_blocks",
    "step1_sample_command_with_sentinels",
    "dispatch_step1_batch",
    "finalize_step1_batch",
    "dispatch_step2",
    "finalize_step2",
]
