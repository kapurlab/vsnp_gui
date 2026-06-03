from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Request
from fastapi.responses import Response, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pathlib import Path
from typing import List, Optional, Dict, Any
import zipfile
import csv
import socket
import json
import os
import time
import subprocess
import shutil
import gzip
import sys
import logging
import shlex
import re
import hashlib
import tempfile

from app.config import load_config, save_config
from app.jobs import JobManager
from app import qc_verdict
from app import provenance_writer
from app.projects import (
    create_project,
    list_projects,
    ensure_project_dirs,
    archive_project,
    delete_project,
    update_project_meta,
    resolve_project_dir,
    SCOPE_PERSONAL,
    SCOPE_SHARED,
)
from app.refs import (
    list_references,
    get_reference_paths,
    add_reference_path,
    remove_reference_path,
    reference_roots,
    find_gff_for_fasta,
)
from app.sra import expand_accessions, expand_accessions_with_mapping, build_download_script, SRAExpansionError, write_crosswalk_tsv
from app.posthoc import list_tools as posthoc_list_tools, get_tool as posthoc_get_tool, tool_status as posthoc_tool_status

app = FastAPI(title="vSNP GUI API")
logger = logging.getLogger("uvicorn.error")

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


def _project_roots(cfg_in: Dict) -> List:
    """Build the list of (scope, root) pairs from config. Personal first
    (so it wins on name collisions), then shared if present."""
    personal = cfg_in.get("projects_root", "").strip()
    shared = cfg_in.get("shared_projects_root", "").strip()
    out = []
    if personal:
        out.append((SCOPE_PERSONAL, Path(personal)))
    if shared:
        out.append((SCOPE_SHARED, Path(shared)))
    return out


def _project_dir_for(cfg_in: Dict, name: str) -> Path:
    """Resolve a project name to its on-disk directory across roots.
    Falls back to the personal root if the project doesn't exist yet
    (e.g. mid-create). Raises ValueError on invalid names."""
    if "/" in name or name.startswith("."):
        raise ValueError("Invalid project name")
    found = resolve_project_dir(_project_roots(cfg_in), name)
    if found is not None:
        return found
    return Path(cfg_in.get("projects_root", "")) / name


def _resolve_qc_thresholds(cfg: Dict, project_dir: Path | None = None) -> Dict:
    """Merge thresholds: module DEFAULTS < user cfg < project.json override."""
    project_layer = None
    if project_dir is not None:
        pj = project_dir / "project.json"
        if pj.is_file():
            try:
                project_layer = json.loads(pj.read_text()).get("qc_thresholds")
            except (json.JSONDecodeError, OSError):
                project_layer = None
    return qc_verdict.merge_thresholds(cfg.get("qc_thresholds"), project_layer)


def _annotate_qc_rows(rows: list, thresholds: Dict) -> list:
    """Attach `_qc_verdict` to each row in place. Returns the same list."""
    for row in rows:
        if isinstance(row, dict):
            row["_qc_verdict"] = qc_verdict.compute_verdict(row, thresholds)
    return rows


def _project_reference(project_dir: Path) -> str:
    """Return the project's locked reference, or "" if not yet set.
    Reads project.json["reference"] only — callers that want the inferred
    value should call the reference_lock endpoint and let it write it back."""
    meta_path = project_dir / "project.json"
    if not meta_path.exists():
        return ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    return (meta.get("reference") or "").strip()


def _project_reference_fasta_and_gff(project_dir: Path, cfg: Dict) -> tuple[str, str]:
    """Resolve a project's reference fasta + GFF paths for IGV consumption.

    Used by step1_files for imported-VCF samples (those that exist only in
    step2/vcf_source/, never went through Step 1, so have no alignment dir
    to crib the reference from).

    Strategy 1 — borrow from any step1 sample that DOES have an alignment
    dir in this project. Cheap when at least one sample was aligned here.

    Strategy 2 — use project.json's "reference" name to look up the dir
    under the configured reference roots. Needed for pure-import projects.

    Returns ("", "") when neither strategy finds a fasta — caller should
    surface that as a friendly error rather than a partial IGV view.
    """
    vsnp3_path = Path(cfg.get("vsnp3_path", ""))
    step1_dir = project_dir / "step1"
    if step1_dir.is_dir():
        for d in sorted(step1_dir.iterdir()):
            if not d.is_dir():
                continue
            fastas = sorted(d.glob("alignment_*/*.fasta"))
            if fastas:
                gff = find_gff_for_fasta(fastas[0], vsnp3_path)
                return str(fastas[0]), (str(gff) if gff else "")
    ref_name = _project_reference(project_dir)
    if ref_name:
        for root in reference_roots(vsnp3_path):
            ref_dir = root / ref_name
            if not ref_dir.is_dir():
                continue
            fastas = sorted(ref_dir.glob("*.fasta"))
            if fastas:
                gff = find_gff_for_fasta(fastas[0], vsnp3_path)
                return str(fastas[0]), (str(gff) if gff else "")
    return "", ""


def _path_under_any_project_root(cfg_in: Dict, target: Path) -> bool:
    """Security check for /serve, /download-file, /posthoc/open: target
    must resolve under any configured projects root."""
    target = target.resolve()
    for _scope, root in _project_roots(cfg_in):
        try:
            if str(target).startswith(str(root.resolve()) + "/") or target == root.resolve():
                return True
        except OSError:
            continue
    return False


def _wrapper_process_alive(script_path: Path) -> bool:
    """Return True if any process has the given wrapper script in its command line.

    Used as a fallback concurrency guard that survives backend reloads, where
    in-memory JobManager state is lost but an orphaned bash wrapper may still
    be running.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", str(script_path)],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _script_bin_dir(cfg: Dict) -> Optional[Path]:
    vsnp3_path = cfg.get("vsnp3_path", "").strip()
    if not vsnp3_path:
        return None
    candidate = Path(vsnp3_path) / "bin"
    return candidate if candidate.is_dir() else None


def _tool_bin_dir(cfg: Dict) -> Optional[Path]:
    bcftools_path = cfg.get("bcftools_path", "").strip()
    if bcftools_path:
        candidate = Path(bcftools_path).expanduser().resolve().parent
        if candidate.is_dir():
            return candidate
    return None


def build_env(cfg: Dict) -> Dict[str, str]:
    current_path = os.environ.get("PATH", "")
    path_parts: List[str] = []
    tool_bin = _tool_bin_dir(cfg)
    script_bin = _script_bin_dir(cfg)
    if tool_bin:
        path_parts.append(str(tool_bin))
    if script_bin:
        path_parts.append(str(script_bin))
    if current_path:
        path_parts.append(current_path)
    return {"PATH": ":".join(path_parts)}


# T-07 provenance helpers ---------------------------------------------------


def _ood_session_id() -> Optional[str]:
    """Best-effort OOD session UUID extraction.

    OOD's batch_connect spawns uvicorn with cwd somewhere under
    `~/ondemand/data/sys/dashboard/batch_connect/sys/vsnp_gui/output/<UUID>/`.
    If the env doesn't expose the UUID directly (it usually doesn't), we
    derive from the cwd path. Returns None if neither source succeeds —
    actor.ood_session_id will be null in the metadata, which is acceptable.
    """
    sid = os.environ.get("OOD_SESSION_ID") or os.environ.get("OOD_BC_SESSION_ID")
    if sid:
        return sid
    try:
        for parent in [Path.cwd()] + list(Path.cwd().parents):
            if parent.parent.name == "output":
                # Looks like .../output/<UUID>/...; UUID is the dir whose parent
                # is named "output".
                return parent.name
    except OSError:
        pass
    return None


def _current_user() -> str:
    """User identity to record in provenance actor block."""
    return os.environ.get("USER") or str(os.getuid()) if hasattr(os, "getuid") else "unknown"


def _is_shared_project(cfg: Dict, project_dir: Path) -> bool:
    """True iff project_dir lies under cfg['shared_projects_root']."""
    shared_root = cfg.get("shared_projects_root", "")
    if not shared_root:
        return False
    try:
        project_dir.resolve().relative_to(Path(shared_root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _step1_sample_names(step1_dir: Path) -> List[str]:
    """Discover step1 sample names: subdirs with at least one *_R1*.fastq.gz
    (or fallback patterns matching the bash script's R1 detection).

    Filters out hidden / underscore-prefixed dirs (the writer's _provenance/
    sibling) so they don't get treated as samples.

    NB: this is the *discovery* function — it accepts any dir that looks
    sample-shaped, including single-end-only. Callers that actually
    dispatch a batch should use `_step1_dispatch_plan()` which applies the
    stricter "paired + non-junk" gate.
    """
    samples = []
    for p in sorted(step1_dir.iterdir()):
        if not p.is_dir() or p.name.startswith(("_", ".")):
            continue
        if any(p.glob("*_R1*.fastq.gz")) or any(p.glob("*_1*.fastq.gz")) or any(p.glob("*.fastq.gz")):
            samples.append(p.name)
    return samples


# T-46 Phase 1: filter at dispatch time so Step 1 doesn't abort on samples
# that vsnp3 can't actually process (single-end, or suspiciously small
# fastqs that are usually SRA submission errors). Without this, one bad
# sample takes down the whole batch because T-07 provenance dispatch
# requires every sample's inputs hash cleanly. 1 MB is a deliberately
# generous floor — real WGS fastqs are usually multi-MB minimum.
_T46_JUNK_FASTQ_BYTES = 1024 * 1024  # 1 MB


def _safe_stat_size(p: Path) -> Optional[int]:
    """File size, or None if the file is missing / a broken symlink. Step1
    sample dirs are symlinks into download/, which can point at sources that
    were since removed (e.g. SRA fastqs deleted, or a Kraken parsed-read source
    cleaned up) — stat() then raises and must not 500 the whole run."""
    try:
        return p.stat().st_size
    except OSError:
        return None


def _step1_dispatch_plan(step1_dir: Path) -> tuple[List[str], List[Dict[str, Any]]]:
    """Decide which sample dirs to actually dispatch and which to skip.

    Returns (samples_to_run, skipped) where `skipped` is a list of
    {"sample", "reason", "size_bytes"} dicts surfaced back to the UI so the
    user knows what was excluded and why. Reasons are user-readable
    strings — they end up in an alert in the GUI.

    Skip rules (in order):
      1. R1 not found (no *_R1*/*_1* pattern) → single-end-only. Phase 1
         skips these; Phase 2 (separate ticket) will add real single-end
         Illumina support via a vsnp3 patch.
      2. R1 found but no R2 → incomplete download or single-end with R1
         naming. Skip with a distinct message.
      3. R1 < 1 MB OR R2 < 1 MB → suspiciously small. Real WGS fastqs are
         multi-MB minimum; sub-MB are almost always SRA submission errors
         (we've seen 43-47 KB ones in the LSDV batch).
    """
    samples: List[str] = []
    skipped: List[Dict[str, Any]] = []
    for p in sorted(step1_dir.iterdir()):
        if not p.is_dir() or p.name.startswith(("_", ".")):
            continue
        r1_matches = sorted(p.glob("*_R1*.fastq.gz")) or sorted(p.glob("*_1.fastq.gz"))
        r2_matches = sorted(p.glob("*_R2*.fastq.gz")) or sorted(p.glob("*_2.fastq.gz"))
        all_fq = sorted(p.glob("*.fastq.gz"))
        if not r1_matches:
            if not all_fq:
                skipped.append({
                    "sample": p.name,
                    "reason": "no fastq files in sample directory",
                    "size_bytes": 0,
                })
            else:
                total = sum(f.stat().st_size for f in all_fq if f.is_file())
                if total < _T46_JUNK_FASTQ_BYTES:
                    skipped.append({
                        "sample": p.name,
                        "reason": f"single-end and suspiciously small ({total} bytes); likely SRA submission error",
                        "size_bytes": total,
                    })
                else:
                    skipped.append({
                        "sample": p.name,
                        "reason": "single-end (paired-end required; single-end support is T-46 Phase 2)",
                        "size_bytes": total,
                    })
            continue
        if not r2_matches:
            skipped.append({
                "sample": p.name,
                "reason": "R1 found but no R2 — paired download incomplete or single-end with R1 naming",
                "size_bytes": _safe_stat_size(r1_matches[0]) or 0,
            })
            continue
        r1_size = _safe_stat_size(r1_matches[0])
        r2_size = _safe_stat_size(r2_matches[0])
        if r1_size is None or r2_size is None:
            # One or both reads are missing / a broken symlink (the download
            # source was removed). Skip rather than crash the whole batch.
            missing = [
                m.name for m in (r1_matches[0], r2_matches[0])
                if _safe_stat_size(m) is None
            ]
            skipped.append({
                "sample": p.name,
                "reason": f"fastq missing or broken link ({', '.join(missing)}); its source was removed — re-import the reads",
                "size_bytes": 0,
            })
            continue
        if r1_size < _T46_JUNK_FASTQ_BYTES or r2_size < _T46_JUNK_FASTQ_BYTES:
            skipped.append({
                "sample": p.name,
                "reason": f"suspiciously small paired fastqs (R1={r1_size}, R2={r2_size} bytes); likely junk",
                "size_bytes": r1_size + r2_size,
            })
            continue
        samples.append(p.name)
    return samples, skipped


def wrap_cmd(cfg: Dict, command: str) -> str:
    # PYTHONWARNINGS suppresses two classes of cosmetic noise from each vsnp3
    # worker:
    #   - SyntaxWarning: belt-and-suspenders for any regex literals we haven't
    #     patched yet (the known sites in step1/step2 are fixed in
    #     deploy/vsnp3-patches/v3.16-kapurlab.patch).
    #   - DeprecationWarning from markupsafe's __version__ access. vsnp3 still
    #     uses the deprecated attribute; harmless but spams logs once per
    #     subprocess. Scoped to the markupsafe module so other deprecation
    #     warnings still surface.
    env_prefix = 'PYTHONWARNINGS="ignore::SyntaxWarning,ignore::DeprecationWarning:markupsafe"'
    path_parts: List[str] = []
    tool_bin = _tool_bin_dir(cfg)
    script_bin = _script_bin_dir(cfg)
    if tool_bin:
        path_parts.append(str(tool_bin))
    if script_bin:
        path_parts.append(str(script_bin))
    if path_parts:
        return f"{env_prefix} PATH=\"{':'.join(path_parts)}:$PATH\" {command}"
    return f"{env_prefix} {command}"


def _load_vcf_label_map(cfg: Dict[str, str], label_style: str) -> Dict[str, str]:
    mapping_csv = _find_vcf_refs_csv(cfg)
    if not mapping_csv:
        return {}
    import re as _re
    def _short_label(name: str) -> str:
        name = name.strip()
        if name.lower().startswith("lineage "):
            parts = name.split()
            if len(parts) > 1 and parts[1].isdigit():
                return f"L{parts[1]}"
        if name.lower().startswith("m. "):
            name = name[3:]
        m = _re.match(r"^(L\\d+)\\b", name, _re.IGNORECASE)
        if m:
            return m.group(1).upper()
        tokens = _re.findall(r"[A-Za-z0-9]+", name)
        if not tokens:
            return name or "REF"
        first = tokens[0]
        if first.lower().startswith("l") and first[1:].isdigit():
            return first.upper()
        return first[:1].upper() + first[1:]
    def _rich_label(name: str) -> str:
        name = name.strip()
        if name.lower().startswith("m. "):
            name = name[3:]
        tokens = _re.findall(r"[A-Za-z0-9]+", name)
        if not tokens:
            return name or "REF"
        return "_".join(tokens)
    out: Dict[str, str] = {}
    with mapping_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            label, ident = row[0].strip(), row[1].strip()
            if not label or not ident or "number" in label.lower():
                continue
            friendly = _rich_label(label) if label_style == "rich" else _short_label(label)
            out[ident] = friendly
    return out


def _write_figtree_groups(step2_dir: Path, vcf_source_dir: Path, cfg: Dict[str, str], label_style: str) -> None:
    if not vcf_source_dir.exists():
        return
    import re as _re
    manifest_path = vcf_source_dir / ".vcf_source_manifest.csv"
    source_map: Dict[str, str] = {}
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                name = (row.get("filename") or "").strip()
                source_type = (row.get("source_type") or "").strip()
                if name:
                    source_map[name] = source_type
    label_map = _load_vcf_label_map(cfg, label_style)
    # Build group file(s)
    rows = []
    for vcf in sorted(vcf_source_dir.glob("*.vcf*")):
        taxon = vcf.name
        source_type = source_map.get(taxon, "reference")
        group = "sample" if source_type == "step1" else "reference"
        color = "#d1495b" if group == "sample" else "#2b6cb0"
        rows.append((taxon, group, color))
    if not rows:
        return
    group_path = step2_dir / "figtree_groups.tsv"
    with group_path.open("w", encoding="utf-8") as handle:
        handle.write("taxon\tgroup\tcolor\n")
        for taxon, group, color in rows:
            handle.write(f"{taxon}\t{group}\t{color}\n")
    # Labeled version
    if label_map:
        labeled_path = step2_dir / "figtree_groups_labeled.tsv"
        with labeled_path.open("w", encoding="utf-8") as handle:
            handle.write("taxon\tgroup\tcolor\n")
            for taxon, group, color in rows:
                labeled = taxon
                for ident, friendly in label_map.items():
                    labeled = _re.sub(rf"\\b{_re.escape(ident)}\\b", friendly, labeled)
                handle.write(f"{labeled}\t{group}\t{color}\n")


def _count_vcfs(folder: Path) -> int:
    """Count *.vcf and *.vcf.gz files at the top level of a DB folder."""
    try:
        return sum(
            1 for p in folder.iterdir()
            if p.is_file() and (p.name.endswith(".vcf") or p.name.endswith(".vcf.gz"))
        )
    except OSError:
        return 0


def _resolved_vcf_db_folders(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Discover VCF DBs as a 2-level tree under vcf_db_folders_root:
        <root>/<reference>/<db_name>/*.vcf

    Each entry carries:
        path           absolute folder path
        reference      parent dir name (matches vsnp3 reference key)
        name           leaf folder name (display name)
        sample_count   live count of *.vcf / *.vcf.gz at top level
        enabled        bool (user toggle; shared entries always True in V1)
        scope          "shared" | "user"

    Shared entries (auto-discovered) are emitted first; user entries (explicit
    config list) follow. Paths are deduplicated. User entries must declare
    their reference in the config item dict; legacy plain-string entries are
    surfaced with reference="" and the GUI can prompt the user to set one."""
    result: List[Dict[str, Any]] = []
    seen: set = set()

    def _emit(path: Path, reference: str, scope: str, enabled: bool):
        p = str(path.resolve())
        if p in seen:
            return
        result.append({
            "path": p,
            "reference": reference,
            "name": path.name,
            "sample_count": _count_vcfs(path),
            "enabled": enabled,
            "scope": scope,
        })
        seen.add(p)

    disabled_shared = set(cfg.get("disabled_vcf_db_paths", []) or [])
    root = cfg.get("vcf_db_folders_root", "")
    if root:
        root_path = Path(root)
        if root_path.is_dir():
            for ref_dir in sorted(root_path.iterdir()):
                if not ref_dir.is_dir():
                    continue
                for db_dir in sorted(ref_dir.iterdir()):
                    if not db_dir.is_dir():
                        continue
                    resolved = str(db_dir.resolve())
                    _emit(db_dir, reference=ref_dir.name, scope="shared",
                          enabled=resolved not in disabled_shared)

    for entry in cfg.get("vcf_db_folders", []) or []:
        if isinstance(entry, dict):
            raw = entry.get("path")
            enabled = entry.get("enabled", True)
            reference = entry.get("reference", "") or ""
        else:
            raw = str(entry)
            enabled = True
            reference = ""
        if not raw:
            continue
        p = Path(raw).expanduser()
        _emit(p, reference=reference, scope="user", enabled=bool(enabled))
    return result


def _find_vcf_refs_csv(cfg: Dict[str, str]) -> Optional[Path]:
    candidates: List[Path] = []
    for folder in _resolved_vcf_db_folders(cfg):
        path = Path(folder["path"])
        candidates.append(path / "VCF_refs.csv")
        candidates.append(path / "vcf_refs.csv")
        candidates.append(path.parent / "VCF_refs.csv")
        candidates.append(path.parent / "vcf_refs.csv")
    vsnp3_path = Path(cfg.get("vsnp3_path", ""))
    if vsnp3_path:
        candidates.append(vsnp3_path / "VCF_REFS" / "VCF_refs.csv")
        candidates.append(vsnp3_path / "VCF_REFS" / "vcf_refs.csv")
    projects_root = Path(cfg.get("projects_root", ""))
    if projects_root:
        parent = projects_root.parent
        candidates.append(parent / "VCF_REFS" / "VCF_refs.csv")
        candidates.append(parent / "VCF_REFS" / "vcf_refs.csv")
    for c in candidates:
        if c.exists():
            return c
    return None


def _build_tree_label_script(step2_dir: Path, cfg: Dict[str, str], label_style: str) -> Optional[Path]:
    mapping_csv = _find_vcf_refs_csv(cfg)
    if not mapping_csv:
        return None
    script_path = step2_dir / "_label_trees.py"
    script = """\
import csv
import re
from pathlib import Path

mapping_csv = Path("__MAPPING_CSV__")
step2_dir = Path("__STEP2_DIR__")
label_style = "__LABEL_STYLE__"

def short_label(name: str) -> str:
    name = name.strip()
    if name.lower().startswith("lineage "):
        parts = name.split()
        if len(parts) > 1 and parts[1].isdigit():
            return "L{}".format(parts[1])
    if name.lower().startswith("m. "):
        name = name[3:]
    m = re.match(r"^(L\\d+)\\b", name, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    tokens = re.findall(r"[A-Za-z0-9]+", name)
    if not tokens:
        return name or "REF"
    first = tokens[0]
    if first.lower().startswith("l") and first[1:].isdigit():
        return first.upper()
    return first[:1].upper() + first[1:]

def rich_label(name: str) -> str:
    name = name.strip()
    if name.lower().startswith("m. "):
        name = name[3:]
    tokens = re.findall(r"[A-Za-z0-9]+", name)
    if not tokens:
        return name or "REF"
    return "_".join(tokens)

def load_mapping(path: Path):
    out = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 2:
                continue
            label, ident = row[0].strip(), row[1].strip()
            if not label or not ident or "number" in label.lower():
                continue
            if label_style == "rich":
                friendly = rich_label(label)
            else:
                friendly = short_label(label)
            out[ident] = friendly + "_" + ident
    return out

mapping = load_mapping(mapping_csv)
if not mapping:
    raise SystemExit(0)

def load_color_map(step2_dir: Path):
    # Map accession -> color based on source type (sample vs reference)
    manifest = step2_dir / "vcf_source" / ".vcf_source_manifest.csv"
    out = {}
    if not manifest.exists():
        return out
    with manifest.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            name = (row.get("filename") or "").strip()
            source_type = (row.get("source_type") or "").strip()
            m = re.search(r"(SRR\\d+|ERR\\d+|DRR\\d+|SRX\\d+|ERX\\d+|DRX\\d+)", name)
            if not m:
                continue
            ident = m.group(1)
            out[ident] = "#d1495b" if source_type == "step1" else "#2b6cb0"
    return out

color_map = load_color_map(step2_dir)

def annotate_newick(text: str, color_map: dict) -> str:
    # Add FigTree-style color annotations before branch length.
    def repl(match):
        label = match.group(2)
        m = re.search(r"(SRR\\d+|ERR\\d+|DRR\\d+|SRX\\d+|ERX\\d+|DRX\\d+)", label)
        if not m:
            return match.group(0)
        ident = m.group(1)
        color = color_map.get(ident)
        if not color:
            return match.group(0)
        return match.group(1) + label + "[&!color=" + color + "]:"
    return re.sub(r"([,(])([^:(),]+):", repl, text)

tree_files = list(step2_dir.rglob("*.tre")) + list(step2_dir.rglob("*.tree")) + list(step2_dir.rglob("*.nwk"))
for path in tree_files:
    if "_labeled" in path.stem:
        continue
    text = path.read_text(encoding="utf-8", errors="ignore")
    for ident, label in mapping.items():
        text = re.sub(rf"\\b" + re.escape(ident) + r"(_zc\\.vcf(?:\\.gz)?)\\b", label + r"\\1", text)
        text = re.sub(rf"\\b" + re.escape(ident) + r"\\b", label, text)
    labeled = path.with_name(path.stem + "_labeled" + path.suffix)
    labeled.write_text(text, encoding="utf-8")
    # Write a NEXUS file with color annotations for FigTree
    annotated = annotate_newick(text, color_map)
    nexus_path = labeled.with_suffix(".nexus")
    nexus_path.write_text(
        \"#NEXUS\\nbegin trees;\\n  tree tree1 = \" + annotated.strip() + \"\\nend;\\n\",
        encoding=\"utf-8\"
    )
"""
    script = (
        script.replace("__MAPPING_CSV__", str(mapping_csv))
        .replace("__STEP2_DIR__", str(step2_dir))
        .replace("__LABEL_STYLE__", label_style)
    )
    script_path.write_text(script, encoding="utf-8")
    return script_path


def conda_python_cmd(cfg: Dict, code: str, args: Optional[List[str]] = None) -> List[str]:
    args = args or []
    return [sys.executable, "-c", code, *args]


class ConfigUpdate(BaseModel):
    vsnp3_path: Optional[str] = None
    projects_root: Optional[str] = None
    shared_projects_root: Optional[str] = None
    bcftools_path: Optional[str] = None
    step1_max_parallel: Optional[int] = None
    sra: Optional[Dict] = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    scope: Optional[str] = None  # "personal" (default) or "shared"
    reference: Optional[str] = None


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
    nanopore: bool = False


class Step2Request(BaseModel):
    reference: Optional[str] = None
    no_filters: bool = False
    qual_threshold: Optional[int] = 150
    n_threshold: Optional[int] = 50
    mq_threshold: Optional[int] = 56
    all_vcf: bool = True
    label_style: Optional[str] = "short"
    find_new_filters: bool = False
    hash_groups: bool = False
    show_groups: bool = False
    html_tree: bool = False
    dp: bool = False
    density_threshold: Optional[int] = None
    density_window: Optional[int] = None
    bootstrap: int = 0


class PosthocRunRequest(BaseModel):
    group: str
    tool: str
    scope: Optional[str] = "all"


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
    shared_root = cfg.get("shared_projects_root", "").strip()
    cfg["_validation"] = {
        "vsnp3_path": Path(cfg.get("vsnp3_path", "")).is_dir() if cfg.get("vsnp3_path", "").strip() else False,
        "projects_root": Path(cfg.get("projects_root", "")).is_dir() if cfg.get("projects_root", "").strip() else False,
        "shared_projects_root": Path(shared_root).is_dir() if shared_root else None,
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
    if update.shared_projects_root is not None:
        cfg["shared_projects_root"] = update.shared_projects_root
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
    reference: Optional[str] = None  # required for "add" — which reference this DB targets


@app.get("/api/vcf-db-folders")
def get_vcf_db_folders():
    cfg = load_config()
    return _resolved_vcf_db_folders(cfg)


@app.post("/api/vcf-db-folders")
def update_vcf_db_folders(payload: VcfDbFolderAction):
    """Mutates the user's explicit vcf_db_folders list. Shared (auto-discovered)
    entries are read-only — managed via the filesystem at vcf_db_folders_root."""
    cfg = load_config()
    folders = cfg.get("vcf_db_folders", [])
    target_path = (
        str(Path(payload.path).expanduser().resolve())
        if payload.path
        else None
    )
    if payload.action == "add":
        if not target_path:
            raise HTTPException(status_code=400, detail="path is required for add")
        ref = (payload.reference or "").strip()
        if not ref:
            raise HTTPException(status_code=400, detail="reference is required for add")
        if not any(
            (f.get("path") if isinstance(f, dict) else str(f)) == target_path
            for f in folders
        ):
            folders.append({"path": target_path, "enabled": True, "reference": ref})
    elif payload.action == "remove":
        if target_path:
            folders = [
                f for f in folders
                if (f.get("path") if isinstance(f, dict) else str(f)) != target_path
            ]
        elif payload.index is not None and 0 <= payload.index < len(folders):
            folders.pop(payload.index)
    elif payload.action == "toggle":
        # First check if path matches a user-explicit entry — flip its enabled.
        # Otherwise treat as a shared path: add/remove from disabled_vcf_db_paths.
        idx = None
        if target_path:
            for i, f in enumerate(folders):
                fp = f.get("path") if isinstance(f, dict) else str(f)
                if fp == target_path:
                    idx = i
                    break
        elif payload.index is not None and 0 <= payload.index < len(folders):
            idx = payload.index
        if idx is not None and isinstance(folders[idx], dict):
            folders[idx]["enabled"] = not folders[idx].get("enabled", True)
        elif target_path:
            disabled = list(cfg.get("disabled_vcf_db_paths", []) or [])
            if target_path in disabled:
                disabled = [p for p in disabled if p != target_path]
            else:
                disabled.append(target_path)
            cfg["disabled_vcf_db_paths"] = disabled
    else:
        raise HTTPException(status_code=400, detail="action must be add, remove, or toggle")
    cfg["vcf_db_folders"] = folders
    save_config(cfg)
    return _resolved_vcf_db_folders(cfg)


class RefPathRequest(BaseModel):
    path: str


class RefDownloadRequest(BaseModel):
    accession: str
    output_dir: str
    display_name: Optional[str] = None


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


_REF_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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

    # Optional display name controls the subdir name (and therefore the entry
    # shown in the Reference dropdown). File stems inside still use the
    # accession — matches the lab's existing convention where dir name and
    # file prefixes can differ (e.g. mtbc0_v1.1/ dir contains H37_*.xlsx).
    display_name = (payload.display_name or "").strip()
    if display_name:
        if len(display_name) > 100:
            raise HTTPException(status_code=400, detail="Display name too long (max 100 chars)")
        if display_name.startswith("."):
            raise HTTPException(status_code=400, detail="Display name cannot start with '.'")
        if not _REF_DISPLAY_NAME_RE.match(display_name):
            raise HTTPException(
                status_code=400,
                detail="Display name may only contain letters, digits, underscore, hyphen, and period",
            )
        subdir_name = display_name
    else:
        subdir_name = accession
    acc_dir = output_dir / subdir_name
    if acc_dir.exists() and any(acc_dir.iterdir()):
        raise HTTPException(
            status_code=409,
            detail=f"Reference directory already exists and is non-empty: {acc_dir}",
        )
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
        command=wrap_cmd(cfg, f"bash {shlex.quote(str(script_path))}"),
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
    meta_files = [f for f in ref_dir.glob("*meta*xlsx") if not f.name.startswith("~$")]
    for f in meta_files:
        files.append({"name": f.name, "path": str(f), "exists": f.exists(), "type": "metadata"})
    return {"ref_name": ref_name, "ref_path": str(ref_dir), "files": files}


def _read_metadata_xlsx(cfg: Dict, meta_path: Path) -> List[Dict[str, str]]:
    code = (
        "import pandas as pd, json, sys; "
        "df = pd.read_excel(sys.argv[1], header=None, usecols=[0,1], names=['original','display_name']); "
        "df = df.dropna(subset=['original']); "
        "df['original'] = df['original'].astype(str); "
        "df['display_name'] = df['display_name'].astype(str); "
        "print(json.dumps(df.to_dict(orient='records')))"
    )
    result = subprocess.run(conda_python_cmd(cfg, code, [str(meta_path)]), text=True, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Failed to read metadata: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return []


@app.get("/api/references/{ref_name}/metadata")
def ref_get_metadata(ref_name: str):
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    refs = list_references(vsnp3_path)
    ref = next((r for r in refs if r["name"] == ref_name), None)
    if not ref:
        raise HTTPException(status_code=404, detail=f"Reference not found: {ref_name}")
    ref_dir = Path(ref["path"])
    meta_files = [f for f in ref_dir.glob("*meta*xlsx") if not f.name.startswith("~$")]
    if not meta_files:
        return {"rows": [], "filename": None, "exists": False}
    meta_path = meta_files[0]
    rows = _read_metadata_xlsx(cfg, meta_path)
    return {"rows": rows, "filename": meta_path.name, "exists": True}


class MetadataAddRequest(BaseModel):
    rows: List[Dict[str, str]]


@app.post("/api/references/{ref_name}/metadata/add-rows")
def ref_add_metadata_rows(ref_name: str, payload: MetadataAddRequest):
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    refs = list_references(vsnp3_path)
    ref = next((r for r in refs if r["name"] == ref_name), None)
    if not ref:
        raise HTTPException(status_code=404, detail=f"Reference not found: {ref_name}")
    ref_dir = Path(ref["path"])
    if not payload.rows:
        raise HTTPException(status_code=400, detail="No rows provided")
    for row in payload.rows:
        if not str(row.get("original", "")).strip():
            raise HTTPException(status_code=400, detail="Each row requires a non-empty 'original' field")
        if not str(row.get("display_name", "")).strip():
            raise HTTPException(status_code=400, detail="Each row requires a non-empty 'display_name' field")

    meta_files = [f for f in ref_dir.glob("*meta*xlsx") if not f.name.startswith("~$")]
    meta_path = meta_files[0] if meta_files else ref_dir / f"{ref_name}_metadata.xlsx"

    existing_rows: List[Dict[str, str]] = []
    if meta_path.exists():
        existing_rows = _read_metadata_xlsx(cfg, meta_path)

    orig_to_idx = {r["original"]: i for i, r in enumerate(existing_rows)}
    added, updated = 0, 0
    for row in payload.rows:
        orig = str(row["original"]).strip()
        disp = str(row["display_name"]).strip()
        if orig in orig_to_idx:
            existing_rows[orig_to_idx[orig]]["display_name"] = disp
            updated += 1
        else:
            existing_rows.append({"original": orig, "display_name": disp})
            orig_to_idx[orig] = len(existing_rows) - 1
            added += 1

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir=str(ref_dir)) as tf:
        json.dump(existing_rows, tf)
        tmp_json = tf.name
    try:
        code = (
            "import pandas as pd, json, sys; "
            "rows = json.load(open(sys.argv[2])); "
            "df = pd.DataFrame([[r['original'], r['display_name']] for r in rows]); "
            "df.to_excel(sys.argv[1], index=False, header=False)"
        )
        result = subprocess.run(
            conda_python_cmd(cfg, code, [str(meta_path), tmp_json]),
            text=True, capture_output=True
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Failed to write metadata: {result.stderr.strip()}")
    finally:
        Path(tmp_json).unlink(missing_ok=True)

    return {"filename": meta_path.name, "rows_total": len(existing_rows), "added": added, "updated": updated}


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


def _resolve_ref_file(ref_name: str, filename: str) -> Path:
    """Resolve a reference-dir-relative filename to an absolute path with
    a directory traversal guard. Raises HTTPException on validation
    failures. Used by the reference preview / download endpoints."""
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    refs = list_references(vsnp3_path)
    ref = next((r for r in refs if r["name"] == ref_name), None)
    if not ref:
        raise HTTPException(status_code=404, detail=f"Reference not found: {ref_name}")
    ref_dir = Path(ref["path"]).resolve()
    if "/" in filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    target = (ref_dir / filename).resolve()
    if not str(target).startswith(str(ref_dir) + "/") and target != ref_dir:
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


@app.get("/api/references/{ref_name}/preview-xlsx", response_class=HTMLResponse)
def ref_preview_xlsx(ref_name: str, filename: str = Query(...), download: int = 0):
    """Render a reference-dir xlsx as a self-contained HTML page. With
    ?download=1, return the raw xlsx instead — used by the "Download xlsx"
    link inside the preview page (JS handler in xlsx_html appends
    download=1 to the current URL preserving the filename param)."""
    target = _resolve_ref_file(ref_name, filename)
    if target.suffix.lower() not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="Only .xlsx/.xlsm supported")
    if download:
        return FileResponse(
            target,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=target.name,
        )
    from app import xlsx_html
    try:
        html_page = xlsx_html.xlsx_to_html(target)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"xlsx render failed: {type(e).__name__}: {e}")
    return HTMLResponse(content=html_page)


@app.get("/api/references/{ref_name}/download-file")
def ref_download_file(ref_name: str, filename: str = Query(...), inline: int = 0):
    """Serve a file from a reference directory. Default = attachment;
    ?inline=1 lets the browser render in-tab where it can. Used by the
    Reference Editor's Download button."""
    target = _resolve_ref_file(ref_name, filename)
    suffix = target.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif suffix == ".xls":
        media_type = "application/vnd.ms-excel"
    elif suffix == ".csv":
        media_type = "text/csv"
    else:
        media_type = "application/octet-stream"
    headers = {}
    if not inline:
        headers["Content-Disposition"] = f'attachment; filename="{target.name}"'
    return FileResponse(target, media_type=media_type, headers=headers)


# T-39: re-upload reference xlsx to replace in place. Closes the offline-edit
# loop (download → edit locally in Excel/Numbers/LibreOffice → re-upload).
# Intentionally narrow:
#   - Only the two known reference xlsx filenames are accepted (defining filter
#     and remove_from_analysis). Other reference files (.fasta, .gbk, .gff,
#     best_reference.txt) are read-only via this endpoint.
#   - 10 MB hard cap; these files are typically < 100 KB.
#   - Old file moved aside to <ref>/.history/ with a timestamp prefix —
#     recoverable without `git`.
#   - Audit log appended to /srv/kapurlab/audit/reference-changes.jsonl
#     (best-effort; fall back to <ref>/.history/_audit.jsonl if the shared
#     audit dir isn't writable).
#   - No approval queue. T-17a will layer the proposal+admin-review flow on
#     top when shipped.
_T39_ALLOWED_REF_FILENAMES = re.compile(r"^[A-Za-z0-9._-]+_(define_filter|remove_from_analysis)\.xlsx$")
_T39_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_T39_SHARED_AUDIT_PATH = Path("/srv/kapurlab/audit/reference-changes.jsonl")


def _sha256_of_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _t39_audit_append(record: dict, fallback_dir: Path) -> str:
    """Append one JSON line to the audit log. Returns the path actually used."""
    line = json.dumps(record, sort_keys=True) + "\n"
    try:
        _T39_SHARED_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _T39_SHARED_AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return str(_T39_SHARED_AUDIT_PATH)
    except (OSError, PermissionError):
        fallback = fallback_dir / "_audit.jsonl"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        with fallback.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return str(fallback)


@app.post("/api/references/{ref_name}/upload-file")
async def ref_upload_file(
    ref_name: str,
    file: UploadFile = File(...),
    rationale: str = Query(..., min_length=1, max_length=4000),
):
    cfg = load_config()
    vsnp3_path = Path(cfg["vsnp3_path"])
    refs = list_references(vsnp3_path)
    ref = next((r for r in refs if r["name"] == ref_name), None)
    if not ref:
        raise HTTPException(status_code=404, detail=f"Reference not found: {ref_name}")
    ref_dir = Path(ref["path"]).resolve()

    # Filename validation: client-provided name only; we ignore any path
    # components and enforce the strict whitelist.
    upload_name = Path(file.filename or "").name
    if not upload_name or not _T39_ALLOWED_REF_FILENAMES.match(upload_name):
        raise HTTPException(
            status_code=400,
            detail="Only *_define_filter.xlsx or *_remove_from_analysis.xlsx may be replaced via this endpoint",
        )

    target = (ref_dir / upload_name).resolve()
    if not str(target).startswith(str(ref_dir) + "/"):
        raise HTTPException(status_code=400, detail="Path not allowed")

    # Spool to a temp file in the same dir (so the os.replace is atomic on
    # the same filesystem) while enforcing the size cap. Read in chunks so
    # we don't load 10 MB into memory.
    history_dir = ref_dir / ".history"
    history_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    tmp_path = ref_dir / f".{upload_name}.{ts}.tmp"
    bytes_written = 0
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > _T39_MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large (max {_T39_MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
                    )
                out.write(chunk)
        if bytes_written == 0:
            raise HTTPException(status_code=400, detail="Empty upload")
    except HTTPException:
        try: tmp_path.unlink()
        except OSError: pass
        raise

    # Minimal sanity check that it's actually an xlsx (zip magic bytes).
    with tmp_path.open("rb") as fh:
        magic = fh.read(4)
    if magic[:2] != b"PK":
        try: tmp_path.unlink()
        except OSError: pass
        raise HTTPException(status_code=400, detail="File is not a valid xlsx (missing zip magic)")

    new_sha = _sha256_of_path(tmp_path)
    old_sha = ""
    archived_old_path = ""
    if target.exists():
        old_sha = _sha256_of_path(target)
        # Move existing file aside under .history/ before replacing.
        archived = history_dir / f"{ts}_{old_sha[:8]}_{upload_name}"
        try:
            target.replace(archived)  # atomic rename
            archived_old_path = str(archived)
        except OSError as e:
            try: tmp_path.unlink()
            except OSError: pass
            raise HTTPException(status_code=500, detail=f"Failed to archive previous file: {e}")

    # Atomic install of the new file.
    try:
        tmp_path.replace(target)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to install new file: {e}")

    # Best-effort user identification (uvicorn runs under one OS user per OOD session).
    try:
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        user = os.environ.get("USER", "")

    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": "replace",
        "reference": ref_name,
        "filename": upload_name,
        "user": user,
        "rationale": rationale.strip(),
        "old_sha256": old_sha,
        "new_sha256": new_sha,
        "size_bytes": bytes_written,
        "archived_old": archived_old_path,
        "target": str(target),
    }
    audit_path = _t39_audit_append(record, history_dir)

    return {
        "ok": True,
        "filename": upload_name,
        "new_sha256": new_sha,
        "old_sha256": old_sha,
        "archived_old": archived_old_path,
        "audit_log": audit_path,
        "size_bytes": bytes_written,
    }


@app.get("/api/projects")
def projects():
    cfg = load_config()
    return list_projects(_project_roots(cfg))


@app.post("/api/projects")
def project_create(payload: ProjectCreate):
    cfg = load_config()
    scope = getattr(payload, "scope", None) or SCOPE_PERSONAL
    reference = (payload.reference or "").strip()
    try:
        project_dir = create_project(_project_roots(cfg), payload.name, scope=scope, reference=reference)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Return the actual (normalized) directory name so the frontend can
    # update its state if the name differs from what the user typed.
    return {"path": str(project_dir), "name": project_dir.name, "scope": scope, "reference": reference}


@app.post("/api/projects/{project}/archive")
def project_archive(project: str):
    cfg = load_config()
    try:
        target = archive_project(_project_roots(cfg), project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"archived_to": str(target)}


@app.delete("/api/projects/{project}")
def project_delete(project: str):
    cfg = load_config()
    try:
        deleted = delete_project(_project_roots(cfg), project)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if deleted is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"deleted": project}


class SetReferenceRequest(BaseModel):
    reference: str


@app.post("/api/projects/{project}/set_reference")
def project_set_reference(project: str, payload: SetReferenceRequest):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    reference = (payload.reference or "").strip()
    meta = update_project_meta(project_dir, {"reference": reference} if reference else {})
    if not reference:
        # Explicitly clearing — remove the key
        meta.pop("reference", None)
        with open(project_dir / "project.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, sort_keys=True)
    return {"reference": reference, "name": project}


@app.post("/api/projects/{project}/link-local")
def project_link_local(project: str, payload: LinkLocalRequest):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_project_dirs(project_dir)
    raw_path = payload.path or ""
    src = Path(raw_path.strip()).expanduser()
    if not src.exists():
        print(f"Link-local failed. Raw path: {raw_path!r} Resolved: {src}")
        raise HTTPException(status_code=400, detail=f"Input path not found: {src}")
    download_dir = project_dir / "download"
    # Accept either a directory of fastqs, or a single .fastq.gz file (used to
    # pull a Kraken parsed-read file into download/ so it can be re-run through
    # Step 1). For a single file, symlink it to its real target so the link
    # keeps working even if the original is itself a symlink.
    if src.is_file():
        candidates = [src] if src.name.endswith(".fastq.gz") else []
    else:
        candidates = sorted(src.glob("*.fastq.gz"))
    count = 0
    for f in candidates:
        target = download_dir / f.name
        if not target.exists():
            target.symlink_to(f.resolve())
            count += 1
    return {"linked": count}


@app.post("/api/projects/{project}/import-vcfs")
def project_import_vcfs(project: str, payload: ImportVcfRequest):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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
    already_present = 0  # already in vcf_source, not re-copied (on_conflict=skip)
    ref_skipped = 0      # excluded: reference mismatch or unknown ref
    dedup_skipped = 0    # excluded: older duplicate sample name (dedupe=true)
    renamed = 0
    mismatched = []
    seen_samples = {}
    manifest_path = vcf_source_dir / ".vcf_source_manifest.csv"
    manifest_exists = manifest_path.exists()
    with manifest_path.open("a", encoding="utf-8") as manifest_handle:
        if not manifest_exists:
            manifest_handle.write("filename,source_type,source_path\n")
        for vcf in vcfs:
            vcf_ref = _detect_vcf_reference(vcf, alias_map)
            if vcf_ref and not _refs_match(vcf_ref, detected_ref, payload.allow_fuzzy_match):
                mismatched.append({"path": str(vcf), "reference": vcf_ref})
                if not payload.allow_mismatch:
                    ref_skipped += 1
                    continue
            if not vcf_ref:
                mismatched.append({"path": str(vcf), "reference": "unknown"})
                if not payload.allow_mismatch:
                    ref_skipped += 1
                    continue
            if payload.dedupe:
                sample = vcf_sample_override.get(vcf, _sample_from_vcf(vcf))
                if sample in seen_samples:
                    prev = seen_samples[sample]
                    if vcf.stat().st_mtime <= prev.stat().st_mtime:
                        dedup_skipped += 1
                        continue
                seen_samples[sample] = vcf
            target = vcf_source_dir / vcf.name
            if target.exists():
                if on_conflict == "skip":
                    already_present += 1
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
            try:
                vcf_path = vcf.resolve()
            except FileNotFoundError:
                vcf_path = vcf
            source_type = "step1" if step1_dir.exists() and str(vcf_path).startswith(str(step1_dir.resolve())) else "reference"
            manifest_handle.write(f"{target.name},{source_type},{vcf_path}\n")

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
        "already_present": already_present,
        "ref_skipped": ref_skipped,
        "dedup_skipped": dedup_skipped,
        "skipped": ref_skipped + dedup_skipped,  # backward compat
        "renamed": renamed,
        "detected_reference": detected_ref or payload.reference or "",
        "mismatched": len(mismatched),
        "mismatch_report": mismatch_report,
        "total_found": len(vcfs)
    }


@app.post("/api/projects/{project}/upload")
async def project_upload(project: str, files: List[UploadFile] = File(...)):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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


@app.get("/api/projects/{project}/sra-crosswalk")
def project_sra_crosswalk(project: str):
    """Serve <project>/download/sra_crosswalk.tsv as text/plain so the browser
    renders it in-tab. Generated by the SRA download flow when expanding
    sample/study-level inputs (SRS/DRS/SRX/PRJNA) into run accessions
    (SRR/DRR). 404 if no crosswalk file exists for this project (project
    was created before the auto-write landed, OR no SRA downloads have
    been kicked off in this project yet, OR the user uploaded fastqs
    directly without going through the SRA path)."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    crosswalk = project_dir / "download" / "sra_crosswalk.tsv"
    if not crosswalk.is_file():
        raise HTTPException(status_code=404, detail="No SRA crosswalk for this project")
    return FileResponse(crosswalk, media_type="text/plain")


@app.get("/api/projects/{project}/inputs")
def project_inputs(project: str):
    """List files currently in <project>/download/.

    Returns name + size + mtime per entry, plus an aggregate. Used by the
    GUI's "Files in this project" panel under the upload dropzone."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    download_dir = project_dir / "download"
    files: List[Dict] = []
    total_bytes = 0
    if download_dir.is_dir():
        for p in sorted(download_dir.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            try:
                stat = p.stat()
            except OSError:
                continue
            files.append({
                "name": p.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
            total_bytes += stat.st_size
    return {"files": files, "total_bytes": total_bytes, "count": len(files)}


@app.delete("/api/projects/{project}/inputs/{filename}")
def project_input_delete(project: str, filename: str):
    """Delete a single file from <project>/download/.

    Filename is treated as a basename only — any directory traversal attempt
    (`..`, slashes, leading dots) is rejected. Idempotent: deleting a missing
    file returns 404."""
    if not filename or "/" in filename or "\\" in filename or filename.startswith(".") or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    target = (project_dir / "download" / filename).resolve()
    download_dir = (project_dir / "download").resolve()
    # Defense in depth: even if the basename guard above slipped, the resolved
    # target must live inside the project's download dir.
    try:
        target.relative_to(download_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
    return {"deleted": filename}


@app.post("/api/projects/{project}/sra/expand")
def sra_expand(project: str, payload: SraRequest):
    _ = project
    try:
        expanded = expand_accessions(payload.accessions, strict=True)
    except SRAExpansionError as e:
        raise HTTPException(status_code=502, detail=f"NCBI eutils unavailable: {e}")
    return {"expanded": expanded}


@app.post("/api/projects/{project}/sra/download")
def sra_download(project: str, payload: SraRequest):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    ensure_project_dirs(project_dir)
    try:
        expanded, mapping = expand_accessions_with_mapping(payload.accessions, strict=True)
    except SRAExpansionError as e:
        # Fail fast — building a download script with the literal unexpanded
        # accession (e.g. SRX*/SRP*) would just produce a guaranteed-fail job.
        # The 502 status is a hint that the failure is upstream (NCBI), not
        # the user's input.
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not resolve SRA accessions via NCBI eutils: {e}. "
                "This is usually NCBI rate-limiting; wait ~30 s and retry. "
                "If it persists, set NCBI_API_KEY in the backend env to get a "
                "10 req/s allowance instead of 3."
            ),
        )
    download_root = project_dir / "download"
    if payload.folder:
        safe = Path(payload.folder).name
        download_root = download_root / safe
    download_root.mkdir(parents=True, exist_ok=True)
    # Persist the input→runs crosswalk so the user can map any downloaded
    # SRR/DRR back to the SRS/DRS/SRX they originally submitted. Without
    # this the resolution is computed at download time and then discarded,
    # leaving no way to reconcile post-hoc.
    try:
        write_crosswalk_tsv(download_root, mapping)
    except OSError as e:
        logger.warning("Failed to write sra_crosswalk.tsv: %s", e)
    script = build_download_script(download_root, expanded, cfg["sra"]["allow_insecure_https"])
    script_path = download_root / "download_sra.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    job_id = job_manager.start_job(
        name="sra_download",
        command=wrap_cmd(cfg, f"bash {shlex.quote(str(script_path))}"),
        cwd=download_root,
        env=build_env(cfg)
    )
    return {"job_id": job_id}


@app.post("/api/projects/{project}/step1/setup")
def step1_setup(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    step1_dir = project_dir / "step1"
    # Refuse to spawn a second batch while a prior step1 job is still
    # running — concurrent batches share the same per-sample dirs and race
    # over the SAM / log / .provenance/exit_code files, producing the
    # FileNotFoundError-on-temp_fastq_seqkit_stats.txt class of failures
    # we hit on the M. sciuri panel. 409 is the right shape; the frontend
    # surfaces it via the same setSraStatus-style error handler.
    prior_job_id_path = step1_dir / ".step1_job_id"
    if prior_job_id_path.exists():
        try:
            prior_id = prior_job_id_path.read_text(encoding="utf-8").strip()
        except OSError:
            prior_id = ""
        if prior_id:
            prior_job = job_manager.get_job(prior_id)
            if prior_job and prior_job.get("status") == "running":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Step 1 is already running (job {prior_id}). "
                        "Wait for it to finish before clicking Run again."
                    ),
                )
    script_path = step1_dir / "run_step1.sh"

    # Concurrency guard: reject duplicate runs so parallel wrappers don't
    # trample each other's output directories (the wrapper's per-sample
    # cleanup deletes alignment_* on entry, which corrupts any in-flight run).
    job_id_path = step1_dir / ".step1_job_id"
    if job_id_path.exists():
        existing_id = job_id_path.read_text(encoding="utf-8").strip()
        if existing_id:
            existing_job = job_manager.get_job(existing_id)
            if existing_job and existing_job.get("status") == "running":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Step 1 is already running for this project (job {existing_id}). "
                        "Wait for it to finish before starting a new run."
                    ),
                )
    if script_path.exists() and _wrapper_process_alive(script_path):
        raise HTTPException(
            status_code=409,
            detail=(
                "Step 1 is already running for this project "
                "(a previous wrapper process is still active). "
                "Wait for it to finish before starting a new run."
            ),
        )

    debug_flag = "--debug" if payload.debug else ""
    assemble_unmap_flag = "-assemble_unmap" if payload.assemble_unmap else ""
    nanopore_flag = "--nanopore" if payload.nanopore else ""
    # Auto-populate reference from project.json if not supplied in payload
    if not payload.reference:
        payload.reference = _project_reference(project_dir)
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

    # Decide which sample dirs to actually run BEFORE writing the batch script,
    # so the script iterates only the valid samples. This keeps a stale dir
    # (e.g. one whose download source was deleted, leaving broken symlinks)
    # from being run and failing the whole batch — those are surfaced to the
    # user as `skipped_samples` instead.
    samples, skipped_samples = _step1_dispatch_plan(step1_dir)
    samples_bash = " ".join(shlex.quote(s) for s in samples)
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
            "  # Skip writer/janitor scaffolding (T-07 _provenance, .git, etc.).",
            "  case \"$d\" in",
            "    _*/|.*/|_*|.*) return 0 ;;",
            "  esac",
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
            "  if [ -z \"$R1\" ]; then",
            "    if [ \"" + ("1" if payload.nanopore else "0") + "\" = \"1\" ]; then",
            "      R1=$(ls *.fastq.gz 2>/dev/null | head -n1 || true)",
            "    fi",
            "  fi",
            "  if [ -z \"$R1\" ]; then",
            "    echo \"Missing R1 in $d\" | tee -a \"$LOG\"",
            "    cd ..",
            "    return 0",
            "  fi",
            "  mkdir -p .provenance",
            "  date -u +%s.%N > .provenance/started_at",
            f"  if [ -n \"$R2\" ]; then",
            f"    vsnp3_step1.py -r1 \"$R1\" -r2 \"$R2\" {ref_arg} {debug_flag} {assemble_unmap_flag} {nanopore_flag} >> \"$LOG\" 2>&1",
            "  else",
            f"    vsnp3_step1.py -r1 \"$R1\" {ref_arg} {debug_flag} {assemble_unmap_flag} {nanopore_flag} >> \"$LOG\" 2>&1",
            "  fi",
            "  STATUS=$?",
            "  echo $STATUS > .provenance/exit_code",
            "  date -u +%s.%N > .provenance/finished_at",
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
            # Rolling worker pool. `wait -n` (bash 4.3+) blocks until ANY
            # backgrounded job finishes — vs the previous `wait $pids[0]`
            # which blocked on a specific PID and stalled the queue when the
            # head was the slowest sample, capping effective parallelism well
            # below MAX_PARALLEL on heterogeneous inputs.
            "pids=()",
            f"SAMPLES=({samples_bash})",
            "for d in \"${SAMPLES[@]}\"; do",
            "  run_sample \"$d\" &",
            "  pids+=(\"$!\")",
            "  if [ ${#pids[@]} -ge \"$MAX_PARALLEL\" ]; then",
            "    wait -n || FAIL=1",
            "    new_pids=()",
            "    for p in \"${pids[@]}\"; do",
            "      if kill -0 \"$p\" 2>/dev/null; then new_pids+=(\"$p\"); fi",
            "    done",
            "    pids=(\"${new_pids[@]}\")",
            "  fi",
            "done",
            "for p in \"${pids[@]}\"; do",
            "  wait \"$p\" || FAIL=1",
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

    # T-07: provenance dispatch. Pre-create per-sample run_metadata.json with
    # frozen dispatch_state sub-block; bash batch will write sentinel files
    # alongside vsnp3_step1.py invocations; finalize_callback rewrites per-
    # sample metadata as terminal once the batch exits. Skip provenance if
    # no reference is set (legacy/unconfigured runs) — the bash still runs.
    prov_finalize_cb = None
    if payload.reference:
        if samples:
            try:
                prov_batch_run_id, _sample_run_ids = provenance_writer.dispatch_step1_batch(
                    cfg, project_dir, samples, payload.reference,
                    user=_current_user(),
                    ood_session_id=_ood_session_id(),
                )
            except provenance_writer.DispatchFailed as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Provenance dispatch failed (step1): {e}",
                )

            def prov_finalize_cb(job_id, exit_code, started_at, finished_at):
                provenance_writer.finalize_step1_batch(
                    project_dir, prov_batch_run_id, exit_code, started_at, finished_at,
                )

    job_id = job_manager.start_job(
        name="step1",
        command=wrap_cmd(cfg, f"bash {shlex.quote(str(script_path))}"),
        cwd=step1_dir,
        env=build_env(cfg),
        finalize_callback=prov_finalize_cb,
    )
    (step1_dir / ".step1_job_id").write_text(job_id, encoding="utf-8")
    # T-46 Phase 1: surface samples auto-excluded from the dispatch so the
    # user can see what didn't run and why. The frontend renders this in
    # the Step 1 status panel.
    return {"job_id": job_id, "skipped_samples": skipped_samples}


@app.get("/api/projects/{project}/step1/status")
def step1_status(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    step1_dir = project_dir / "step1"
    if not step1_dir.exists():
        raise HTTPException(status_code=404, detail="Step1 directory not found")

    job_id_path = step1_dir / ".step1_job_id"
    job_id = job_id_path.read_text(encoding="utf-8").strip() if job_id_path.exists() else ""
    job = job_manager.get_job(job_id) if job_id else None
    job_status = job["status"] if job else "unknown"

    vcfs_dir = project_dir / f"{project}_VCFs"
    in_vcfs_folder: set = set()
    if vcfs_dir.exists():
        for vf in vcfs_dir.glob("*_zc.vcf*"):
            stem = vf.name.replace("_zc.vcf.gz", "").replace("_zc.vcf", "")
            in_vcfs_folder.add(stem)

    statuses = []
    for sample_dir in sorted(step1_dir.glob("*")):
        if not sample_dir.is_dir():
            continue
        # Skip writer/janitor scaffolding (e.g. _provenance/) so they don't
        # surface as "Unknown" samples in the GUI.
        if sample_dir.name.startswith(("_", ".")):
            continue
        sample = sample_dir.name
        log_path = sample_dir / "run_step1.log"
        exit_code_path = sample_dir / ".provenance" / "exit_code"
        vcf = next(sample_dir.glob("alignment_*/*_filtered_hapall_annotated.vcf"), None)
        nodup = next(sample_dir.glob("alignment_*/*_nodup.bam"), None)
        zc_vcf = next(sample_dir.glob("**/*_zc.vcf"), None) or next(sample_dir.glob("**/*_zc.vcf.gz"), None)

        # Status logic (in priority order):
        #   1. .provenance/exit_code present  → authoritative per-sample terminal
        #      state (T-07 sentinel written by the bash batch after vsnp3_step1.py
        #      exits). 0 = success, non-zero = real failure.
        #   2. Outputs (VCF + BAM) present     → complete (legacy projects pre-T-07
        #      sentinels, or sentinel write raced).
        #   3. log_path exists + job running   → still running.
        #   4. log_path exists + job not running → unknown (sample's bash leg died
        #      before writing the sentinel — kill / OOM / batch interrupted).
        #   5. else                            → not_started.
        # The previous heuristic grepped the running log for "Error:" / "Exception"
        # / "Traceback", which false-positives on vsnp3's verbose intermediate
        # output (deprecation warnings, etc) and made every sample flicker into
        # "Error" before transitioning to "Complete" once the VCF landed.
        status = "not_started"
        exit_code_str = ""
        if exit_code_path.exists():
            try:
                exit_code_str = exit_code_path.read_text(encoding="utf-8").strip()
            except OSError:
                exit_code_str = ""
        if exit_code_str:
            status = "complete" if exit_code_str == "0" else "error"
        elif vcf and nodup:
            status = "complete"
        elif log_path.exists():
            status = "running" if job_status == "running" else "unknown"
        statuses.append({
            "sample": sample,
            "status": status,
            "log_path": str(log_path),
            "has_log": log_path.exists(),
            "has_outputs": bool(vcf and nodup),
            "has_zc_vcf": bool(zc_vcf),
            "in_vcfs_folder": sample in in_vcfs_folder,
        })
    return {"job_status": job_status, "samples": statuses}


@app.get("/api/projects/{project}/step1/log")
def step1_log(project: str, sample: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    log_path = project_dir / "step1" / sample / "run_step1.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-400:]
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"sample": sample, "log": "".join(lines)}


@app.get("/api/projects/{project}/vcfs")
def project_vcfs_list(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    vcfs_dir = project_dir / f"{project}_VCFs"
    vcfs_dir.mkdir(parents=True, exist_ok=True)
    vcfs = sorted([*vcfs_dir.glob("*_zc.vcf"), *vcfs_dir.glob("*_zc.vcf.gz")], key=lambda p: p.name)
    samples = []
    for v in vcfs:
        stem = v.name.replace("_zc.vcf.gz", "").replace("_zc.vcf", "")
        samples.append({"filename": v.name, "sample": stem})
    return {"count": len(samples), "path": str(vcfs_dir), "folder_name": vcfs_dir.name, "samples": samples}


class VcfsCollectRequest(BaseModel):
    force_samples: List[str] = []


@app.post("/api/projects/{project}/vcfs/collect")
def project_vcfs_collect(project: str, payload: VcfsCollectRequest):
    """Scan step1/ for passing _zc.vcf files and symlink them into <project>_VCFs/.
    Samples in force_samples are included even if they did not pass Step 1."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    step1_dir = project_dir / "step1"
    vcfs_dir = project_dir / f"{project}_VCFs"
    vcfs_dir.mkdir(parents=True, exist_ok=True)

    force_set = set(payload.force_samples or [])
    auto_added: List[str] = []
    force_added: List[str] = []
    already_present: List[str] = []
    no_vcf: List[str] = []

    for sample_dir in sorted(step1_dir.glob("*")):
        if not sample_dir.is_dir() or sample_dir.name.startswith(("_", ".")):
            continue
        sample = sample_dir.name

        # Determine pass/fail from provenance sentinel, fall back to output presence
        exit_code_path = sample_dir / ".provenance" / "exit_code"
        passed = False
        if exit_code_path.exists():
            try:
                passed = exit_code_path.read_text(encoding="utf-8").strip() == "0"
            except OSError:
                pass
        else:
            vcf_out = next(sample_dir.glob("alignment_*/*_filtered_hapall_annotated.vcf"), None)
            nodup_out = next(sample_dir.glob("alignment_*/*_nodup.bam"), None)
            passed = bool(vcf_out and nodup_out)

        if not passed and sample not in force_set:
            continue

        # Find the latest _zc.vcf (prefer uncompressed; fall back to .gz)
        candidates = sorted(sample_dir.glob("**/*_zc.vcf"), key=lambda p: p.stat().st_mtime)
        candidates_gz = sorted(sample_dir.glob("**/*_zc.vcf.gz"), key=lambda p: p.stat().st_mtime)
        all_candidates = candidates + candidates_gz
        if not all_candidates:
            if sample in force_set:
                no_vcf.append(sample)
            continue

        source_vcf = all_candidates[-1].resolve()
        target = vcfs_dir / source_vcf.name

        if target.exists():
            already_present.append(sample)
            continue

        target.symlink_to(source_vcf)
        if sample in force_set and not passed:
            force_added.append(sample)
        else:
            auto_added.append(sample)

    total = len(list(vcfs_dir.glob("*_zc.vcf"))) + len(list(vcfs_dir.glob("*_zc.vcf.gz")))
    return {
        "auto_added": auto_added,
        "force_added": force_added,
        "already_present": already_present,
        "no_vcf": no_vcf,
        "total": total,
    }


@app.post("/api/projects/{project}/step2/setup")
def step2_setup(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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

    # Read the persisted exclusion set (written by qc_exclude when the user
    # toggles checkboxes). Previously step2_setup ignored this list — it
    # symlinked every step1 VCF regardless, leaving the user with a
    # "VCFs in set: N" count that didn't match what they'd checked. vsnp3
    # later filtered them out via -remove_by_name at run time, so the final
    # analysis was correct, but the UI display lied. Filter at link time so
    # the count and the file list reflect what'll actually be analyzed.
    excluded_names: set[str] = set()
    remove_xlsx = project_dir / "step2" / "remove_from_analysis.xlsx"
    if remove_xlsx.exists():
        try:
            import pandas as pd  # vsnp3 env always has pandas
            df = pd.read_excel(remove_xlsx, header=None)
            for s in df.iloc[:, 0].tolist():
                name = str(s).strip()
                if name and name.lower() != "nan":
                    excluded_names.add(name)
        except Exception as exc:
            # Don't fail the whole build for a broken xlsx — vsnp3's
            # -remove_by_name at run time is the backstop. Surface a warning.
            logger.warning("step2_setup: failed to parse exclusion xlsx %s: %s", remove_xlsx, exc)

    count = 0
    skipped_excluded = 0
    edited_samples = []
    manifest_path = step2_dir / ".vcf_source_manifest.csv"
    with manifest_path.open("w", encoding="utf-8") as manifest:
        manifest.write("filename,source_type,source_path\n")
        for sample_dir in sorted(step1_dir.glob("*")):
            if not sample_dir.is_dir():
                continue
            sample = sample_dir.name
            if sample in excluded_names:
                skipped_excluded += 1
                continue
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
            manifest.write(f"{target.name},step1,{chosen_vcf}\n")
            count += 1
            if patched_vcf:
                edited_samples.append(sample)
    _write_step2_edit_summary(step2_dir.parent, edited_samples)
    total = len(list(step2_dir.glob("*_zc.vcf"))) + len(list(step2_dir.glob("*_zc.vcf.gz")))
    return {
        "linked": count,
        "total": total,
        "edited": len(set(edited_samples)),
        "skipped_excluded": skipped_excluded,
    }


@app.post("/api/projects/{project}/step2/run")
def step2_run(project: str, payload: Step2Request):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    # Auto-populate reference: project.json first, then reference_lock inference
    if not payload.reference:
        payload.reference = _project_reference(project_dir)
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
    # Store the resolved reference back into project.json
    update_project_meta(project_dir, {"reference": payload.reference})

    step2_dir = project_dir / "step2"
    step2_dir.mkdir(parents=True, exist_ok=True)

    # Concurrency guard: one step2 run at a time per project
    job_id_path = step2_dir / ".step2_job_id"
    if job_id_path.exists():
        prior_id = job_id_path.read_text(encoding="utf-8").strip()
        if prior_id:
            prior_job = job_manager.get_job(prior_id)
            if prior_job and prior_job.get("status") == "running":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Step 2 is already running for this project (job {prior_id}). "
                        "Wait for it to finish before starting a new run."
                    ),
                )

    vcf_source_dir = step2_dir / "vcf_source"

    # Timestamped run directory — each run gets its own subdirectory so
    # multiple comparisons accumulate without overwriting previous outputs.
    from datetime import datetime as _dt
    run_ts = _dt.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = step2_dir / "runs" / run_ts
    run_dir.mkdir(parents=True, exist_ok=True)

    remove_file = step2_dir / "remove_from_analysis.xlsx"
    remove_arg = f" -remove_by_name {remove_file}" if remove_file.exists() else ""
    _write_step2_edit_summary(run_dir, _edited_samples_in_dir(vcf_source_dir))
    _write_figtree_groups(run_dir, vcf_source_dir, cfg, payload.label_style or "short")
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
    cmd = f"vsnp3_step2.py -wd {shlex.quote(str(vcf_source_dir))} {flags_str} -t {payload.reference}{remove_arg}"
    label_style = payload.label_style or "short"
    label_script = _build_tree_label_script(run_dir, cfg, label_style)
    if label_script:
        cmd = f"{cmd} && python {shlex.quote(str(label_script))}"
    step2_env = build_env(cfg)
    if payload.bootstrap and payload.bootstrap > 0:
        step2_env["VSNP3_BOOTSTRAP"] = str(int(payload.bootstrap))

    # T-07: provenance dispatch. Writes pipeline_run record (linking step1
    # samples) and step2 run_metadata.json with frozen dispatch_state.
    # On shared projects, refuses to dispatch if any step1 sample is still
    # running (HTTP 409). On personal projects, warn-and-proceed via
    # consumed_step1_run_ids_complete: false in the pipeline_run record.
    try:
        prov_step2_run_id, _prov_pipeline_run_id = provenance_writer.dispatch_step2(
            cfg, project_dir, payload.reference,
            cli_command=cmd, cli_flags=step2_flags,
            user=_current_user(),
            ood_session_id=_ood_session_id(),
            is_shared=_is_shared_project(cfg, project_dir),
            resolved_vcf_db_folders=_resolved_vcf_db_folders(cfg),
            step2_run_dir=run_dir,
        )
    except provenance_writer.Step2DispatchBlocked as e:
        raise HTTPException(status_code=409, detail=str(e))
    except provenance_writer.DispatchFailed as e:
        raise HTTPException(
            status_code=500,
            detail=f"Provenance dispatch failed (step2): {e}",
        )

    def prov_finalize_cb(job_id, exit_code, started_at, finished_at):
        provenance_writer.finalize_step2(
            project_dir, prov_step2_run_id, exit_code, started_at, finished_at,
            step2_run_dir=run_dir,
        )

    job_id = job_manager.start_job(
        name="step2",
        command=wrap_cmd(cfg, cmd),
        cwd=run_dir,
        env=step2_env,
        finalize_callback=prov_finalize_cb,
    )
    # Record the active job and the current run so the frontend can auto-select it
    job_id_path.write_text(job_id, encoding="utf-8")
    (step2_dir / ".current_run").write_text(run_ts, encoding="utf-8")
    return {"job_id": job_id, "run_id": run_ts}


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
    # T-05: for step1 batch jobs, also tail every per-sample run_step1.log so
    # the GUI shows live bwa/samtools/etc output during the long part of the
    # run, not just the bash-batch coordination noise. Other job types (step2,
    # SRA, genome download) keep the original single-log behavior.
    is_step1 = job.get("name") == "step1"
    step1_dir = Path(job["cwd"]) if is_step1 and job.get("cwd") else None
    batch_prefix = "[batch] " if is_step1 else ""

    def event_stream():
        offsets: Dict[Path, int] = {}
        sample_offsets: Dict[Path, int] = {}

        def discover_step1_samples() -> None:
            if not (is_step1 and step1_dir and step1_dir.is_dir()):
                return
            try:
                children = sorted(step1_dir.iterdir())
            except OSError:
                return
            for child in children:
                if not child.is_dir():
                    continue
                slog = child / "run_step1.log"
                if slog.exists() and slog not in sample_offsets:
                    sample_offsets[slog] = 0

        def emit_new(path: Path, offset_dict: Dict[Path, int], prefix: str):
            try:
                size = path.stat().st_size
            except OSError:
                return
            last = offset_dict.get(path, 0)
            if size <= last:
                return
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last)
                    chunk = f.read(size - last)
            except OSError:
                return
            offset_dict[path] = size
            for line in chunk.splitlines():
                yield f"data: {prefix}{line}\n\n"

        def drain_all():
            if log_path.exists():
                yield from emit_new(log_path, offsets, batch_prefix)
            if is_step1:
                discover_step1_samples()
                for slog in sorted(sample_offsets.keys()):
                    sample = slog.parent.name
                    yield from emit_new(slog, sample_offsets, f"[{sample}] ")

        while True:
            yield from drain_all()
            j = job_manager.get_job(job_id)
            if j and j["status"] in {"succeeded", "failed"}:
                # Catch anything written between the last poll and process exit.
                yield from drain_all()
                yield f"data: [job:{j['status']}]\n\n"
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
    project_dir = _project_dir_for(cfg, project)
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
        rows = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="QC summary parse failed")
    return _annotate_qc_rows(rows, _resolve_qc_thresholds(cfg, project_dir))


@app.get("/api/projects/{project}/qc_summary.csv")
def qc_summary_csv(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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
    project_dir = _project_dir_for(cfg, project)
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
        rows = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Post-hoc scan parse failed")
    # Post-hoc rows aggregate across many projects, so per-project overrides
    # don't apply cleanly here — annotate with cfg-resolved thresholds only.
    return _annotate_qc_rows(rows, _resolve_qc_thresholds(cfg))


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


@app.get("/api/posthoc/tools")
def posthoc_tools():
    cfg = load_config()
    tool_bin = str(_tool_bin_dir(cfg) or "")
    tools = []
    for tool in posthoc_list_tools():
        status = posthoc_tool_status(tool, tool_bin)
        tools.append({
            "id": tool.tool_id,
            "label": tool.label,
            "description": tool.description,
            "requires": tool.requires,
            "outputs": tool.outputs,
            "available": status["available"],
            "missing": status["missing"],
            "requirements": status["requirements"],
        })
    return tools


@app.post("/api/projects/{project}/posthoc/run")
def posthoc_run(project: str, payload: PosthocRunRequest):
    cfg = load_config()
    tool = posthoc_get_tool(payload.tool)
    if not tool:
        raise HTTPException(status_code=404, detail="Unknown posthoc tool")
    status = posthoc_tool_status(tool, str(_tool_bin_dir(cfg) or ""))
    if not status["available"]:
        raise HTTPException(status_code=400, detail=f"Missing dependencies: {', '.join(status['missing'])}")
    project_dir = Path(cfg["projects_root"]) / project
    step2_dir = project_dir / "step2"
    group_dir = step2_dir / payload.group
    if not group_dir.exists():
        raise HTTPException(status_code=404, detail=f"Group not found: {payload.group}")
    posthoc_dir = group_dir / "posthoc"
    posthoc_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _posthoc_lock_path(step2_dir, payload.group, tool.tool_id)
    _posthoc_clear_stale_lock(lock_path)
    if lock_path.exists():
        raise HTTPException(status_code=409, detail="Posthoc job already running for this group")
    stats_path = posthoc_dir / "stats.json"
    scope = (payload.scope or "all").lower()
    if tool.tool_id == "snp_analysis":
        cmd = _posthoc_snp_analysis_command(
            group_dir,
            payload.group,
            posthoc_dir,
            str(_tool_bin_dir(cfg) or ""),
            scope,
        )
    else:
        cmd = _posthoc_stub_command(cfg, stats_path, payload.group, tool.tool_id)
    backend_root = Path(__file__).resolve().parent.parent
    job_id = job_manager.start_job(
        name=f"posthoc:{tool.tool_id}:{project}:{payload.group}",
        command=cmd,
        cwd=backend_root,
    )
    lock_path.write_text(job_id, encoding="utf-8")
    return {"job_id": job_id, "group": payload.group, "tool": tool.tool_id, "outputs": tool.outputs}


@app.get("/api/projects/{project}/posthoc/status")
def posthoc_status(project: str, group: str, tool: str = "snp_analysis"):
    cfg = load_config()
    tool_obj = posthoc_get_tool(tool)
    if not tool_obj:
        raise HTTPException(status_code=404, detail="Unknown posthoc tool")
    step2_dir = Path(cfg["projects_root"]) / project / "step2"
    group_dir = step2_dir / group
    if not group_dir.exists():
        raise HTTPException(status_code=404, detail=f"Group not found: {group}")
    posthoc_dir = group_dir / "posthoc"
    lock_path = _posthoc_lock_path(step2_dir, group, tool_obj.tool_id)
    _posthoc_clear_stale_lock(lock_path)
    running = lock_path.exists()
    outputs = []
    for rel in tool_obj.outputs:
        path = group_dir / rel
        outputs.append({"path": str(path), "exists": path.exists()})
    return {"running": running, "outputs": outputs}


@app.get("/api/projects/{project}/reference_lock")
def reference_lock(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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


@app.get("/api/projects/{project}/step2/vcf_count")
def step2_vcf_count(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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


@app.get("/api/projects/{project}/step2/vcf_source/samples")
def step2_vcf_source_samples(project: str):
    """Return all sample names in the vcf_source directory, parsed from the manifest."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    vcf_source_dir = project_dir / "step2" / "vcf_source"
    if not vcf_source_dir.exists():
        return []
    manifest_path = vcf_source_dir / ".vcf_source_manifest.csv"
    samples = []
    if manifest_path.exists():
        with manifest_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            seen: set = set()
            for row in reader:
                fn = row.get("filename", "").strip()
                if not fn or fn in seen:
                    continue
                seen.add(fn)
                samples.append({
                    "filename": fn,
                    "sample": fn.replace("_zc.vcf.gz", "").replace("_zc.vcf", ""),
                    "source_type": row.get("source_type", ""),
                    "source_path": row.get("source_path", ""),
                })
    else:
        for vcf in sorted(vcf_source_dir.glob("*.vcf")) + sorted(vcf_source_dir.glob("*.vcf.gz")):
            fn = vcf.name
            samples.append({
                "filename": fn,
                "sample": fn.replace("_zc.vcf.gz", "").replace("_zc.vcf", ""),
                "source_type": "",
                "source_path": str(vcf),
            })
    samples.sort(key=lambda x: x["sample"].lower())
    return samples


@app.get("/api/projects/{project}/step2/runs")
def step2_runs_list(project: str):
    """List all timestamped step2 runs newest-first. Falls back to a synthetic
    'legacy' entry when the flat layout is present but runs/ does not exist."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    step2_dir = project_dir / "step2"
    if not step2_dir.exists():
        return []
    runs_dir = step2_dir / "runs"
    results = []
    if runs_dir.is_dir():
        for run_entry in sorted(runs_dir.iterdir(), reverse=True):
            if not run_entry.is_dir():
                continue
            meta_path = run_entry / "run_metadata.json"
            started_at = None
            status = "unknown"
            reference = ""
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    started_at = meta.get("started_at")
                    status = meta.get("status", "running")
                    reference = (
                        meta.get("dispatch_state", {})
                        .get("reference", {})
                        .get("name", "")
                    )
                except (json.JSONDecodeError, OSError):
                    pass
            else:
                # run_metadata.json missing → job is still running or very new
                status = "running"
            group_count = sum(1 for d in run_entry.iterdir() if d.is_dir() and not d.name.startswith("_"))
            results.append({
                "run_id": run_entry.name,
                "started_at": started_at,
                "status": status,
                "reference": reference,
                "group_count": group_count,
            })
    else:
        # Legacy flat layout: any group dirs directly under step2/
        has_groups = any(
            d.is_dir() and d.name not in ("vcf_source",) and not d.name.startswith(".")
            for d in step2_dir.iterdir()
        )
        if has_groups:
            results.append({
                "run_id": "legacy",
                "started_at": None,
                "status": "ok",
                "reference": "",
                "group_count": sum(
                    1 for d in step2_dir.iterdir()
                    if d.is_dir() and d.name not in ("vcf_source",) and not d.name.startswith(".")
                ),
            })
    return results


@app.post("/api/projects/{project}/qc_exclude")
def qc_exclude(project: str, payload: ExcludeRequest):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    step2_dir = project_dir / "step2"
    step2_dir.mkdir(parents=True, exist_ok=True)
    remove_path = step2_dir / "remove_from_analysis.xlsx"
    # If the GUI clears every checkbox, delete the file rather than writing
    # an empty xlsx — `step2_run` checks for file existence to decide whether
    # to pass `-remove_by_name` to vsnp3, and an empty list is logically the
    # same as "no exclusions".
    if not payload.samples:
        if remove_path.exists():
            try:
                remove_path.unlink()
            except OSError:
                pass
        return {"remove_file": str(remove_path), "count": 0}
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


@app.get("/api/projects/{project}/qc_exclude")
def qc_exclude_get(project: str):
    """Return the persisted exclusion set so the GUI can hydrate the QC table
    on project load. Reads `step2/remove_from_analysis.xlsx` directly via
    pandas (uvicorn runs in the vsnp3 env). Returns empty list if the file
    is absent — that is the canonical "no exclusions" state."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    remove_path = project_dir / "step2" / "remove_from_analysis.xlsx"
    if not remove_path.exists():
        return {"samples": []}
    try:
        import pandas as pd  # vsnp3 env always has pandas
        df = pd.read_excel(remove_path, header=None)
        samples = [
            str(s).strip()
            for s in df.iloc[:, 0].tolist()
            if str(s).strip() and str(s).strip().lower() != "nan"
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read exclusions: {exc}")
    return {"samples": samples}


@app.post("/api/projects/{project}/step2/clear")
def step2_clear(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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


def _resolve_kraken_sample_dir(kraken_dir: Path, sample: str) -> Optional[Path]:
    """Resolve a step1 sample name to its Kraken output subdirectory.

    Unlike step1, Kraken's dir name can be either LONGER or SHORTER than the
    step1 sample name depending on which read-tag/lane suffix each tool
    stripped (and whether Kraken was run from its own GUI on the raw download
    fastqs, before step1 existed). So match in both directions:

      1. exact: kraken/<sample>
      2. kraken dir longer:  <sample>_*           (e.g. sample 13-1941-6
         matches kraken dir 13-1941-6_S4_L001)
      3. kraken dir shorter: sample == <dir>_*    (e.g. step1 sample
         13-1941-6_S4_L001 matches kraken dir 13-1941-6)

    For the shorter case, prefer the longest-matching dir name so we don't
    match ``S1`` to a sample named ``S10_...``.
    """
    exact = kraken_dir / sample
    if exact.is_dir():
        return exact
    try:
        dirs = [d for d in kraken_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    except (OSError, PermissionError):
        return None
    # Kraken dir longer than the sample.
    longer = sorted(d for d in dirs if d.name.startswith(f"{sample}_"))
    if longer:
        return longer[0]
    # Kraken dir shorter than the sample (run on the bare/raw fastq name).
    shorter = [d for d in dirs if sample.startswith(f"{d.name}_")]
    if shorter:
        return max(shorter, key=lambda d: len(d.name))
    return None


@app.get("/api/projects/{project}/step1/files")
def step1_files(project: str, sample: str = Query(...)):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    step1_dir = project_dir / "step1"
    sample_dir = _resolve_sample_dir(step1_dir, sample) if step1_dir.is_dir() else None
    if not sample_dir:
        # Imported-VCF case: sample lives only in step2/vcf_source/ (no Step 1
        # alignment, so no BAM). Still useful in IGV as a calls-only track —
        # anchor it to the project's reference so the user can compare the
        # variant positions against the local cohort.
        vcf_source_dir = project_dir / "step2" / "vcf_source"
        imported_vcf = None
        if vcf_source_dir.is_dir():
            for suffix in ("_zc.vcf", "_zc.vcf.gz", ".vcf", ".vcf.gz"):
                cand = vcf_source_dir / f"{sample}{suffix}"
                if cand.exists():
                    imported_vcf = cand
                    break
        if imported_vcf is not None:
            ref_fasta, ref_gff = _project_reference_fasta_and_gff(project_dir, cfg)
            if not ref_fasta:
                # No way to anchor the VCF — surface as a clearly-distinct error.
                raise HTTPException(status_code=404, detail="imported_vcf_no_reference")
            return {
                "stats": "",
                "bam": "",
                "alignment_dir": "",
                "reference_fasta": ref_fasta,
                "reference_gff": ref_gff,
                "annotated_vcf": "",  # imports lack the rich ID-column annotation
                "sample_dir": str(vcf_source_dir),
                "source_vcf": str(imported_vcf),
                "patched_vcf": "",
                "edit_log": "",
                "edited": False,
                "kind": "imported_vcf",
            }
        raise HTTPException(status_code=404, detail="no_step1")
    stats_files = sorted(sample_dir.glob(f"{sample}_*_stats.xlsx"), key=lambda p: p.stat().st_mtime)
    stats_path = str(stats_files[-1]) if stats_files else ""
    bam_files = sorted(sample_dir.glob(f"**/{sample}_nodup.bam"), key=lambda p: p.stat().st_mtime)
    bam_path = str(bam_files[-1]) if bam_files else ""
    align_dir = str(bam_files[-1].parent) if bam_files else ""
    ref_fasta = ""
    ref_gff = ""
    annotated_vcf = ""
    if align_dir:
        fasta_files = sorted(Path(align_dir).glob("*.fasta"))
        if fasta_files:
            ref_fasta = str(fasta_files[0])
            # GFF lives in the reference options dir, not the alignment dir.
            vsnp3_path = Path(cfg.get("vsnp3_path", ""))
            gff_path = find_gff_for_fasta(fasta_files[0], vsnp3_path)
            if gff_path:
                ref_gff = str(gff_path)
        # The *_filtered_hapall_annotated.vcf holds per-variant annotation
        # in the ID column (gene/product/codon/AA change/mutation_type).
        # Surface its path so igv.js can render it as a variant track and
        # show the annotation on hover.
        ann_candidates = sorted(
            Path(align_dir).glob(f"{sample}_filtered_hapall_annotated.vcf*"),
            key=lambda p: p.stat().st_mtime,
        )
        if ann_candidates:
            annotated_vcf = str(ann_candidates[-1])
    vcf_candidates = sorted(sample_dir.glob(f"**/{sample}*zc.vcf*"), key=lambda p: p.stat().st_mtime)
    source_vcf = vcf_candidates[-1] if vcf_candidates else None
    patched_vcf = _find_patched_vcf(sample_dir, sample, source_vcf)
    edit_log = _edit_log_path(sample_dir, sample)
    return {
        "stats": stats_path,
        "bam": bam_path,
        "alignment_dir": align_dir,
        "reference_fasta": ref_fasta,
        "reference_gff": ref_gff,
        "annotated_vcf": annotated_vcf,
        "sample_dir": str(sample_dir),
        "source_vcf": str(source_vcf) if source_vcf else "",
        "patched_vcf": str(patched_vcf) if patched_vcf else "",
        "edit_log": str(edit_log) if edit_log.exists() else "",
        "edited": bool(patched_vcf) and edit_log.exists()
    }


def _latest_step1_stats(project: str, sample: str) -> Path:
    """Discover the latest *_stats.xlsx for a sample. Shared by the download
    and preview endpoints so they stay in sync on the resolution rules."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    step1_dir = project_dir / "step1"
    sample_dir = _resolve_sample_dir(step1_dir, sample) if step1_dir.is_dir() else None
    if not sample_dir:
        raise HTTPException(status_code=404, detail="Sample not found")
    stats_files = sorted(sample_dir.glob(f"{sample}_*_stats.xlsx"), key=lambda p: p.stat().st_mtime)
    if not stats_files:
        raise HTTPException(status_code=404, detail="Stats file not found")
    return stats_files[-1]


@app.get("/api/projects/{project}/step1/samples/{sample}/stats/download")
def step1_sample_stats_download(project: str, sample: str):
    target = _latest_step1_stats(project, sample)
    return FileResponse(
        target,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{sample}_stats.xlsx",
    )


@app.get("/api/projects/{project}/step1/samples/{sample}/stats/preview", response_class=HTMLResponse)
def step1_sample_stats_preview(project: str, sample: str, download: int = 0):
    """Render the latest stats xlsx for a sample as a formatted HTML preview
    (cell colors + conditional formatting preserved via openpyxl). With
    ?download=1, returns the raw xlsx — used by the "Download xlsx" link
    inside the preview page so users can still grab the file for offline
    spreadsheet work without leaving the preview."""
    target = _latest_step1_stats(project, sample)
    if download:
        return FileResponse(
            target,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"{sample}_stats.xlsx",
        )
    from app import xlsx_html
    try:
        html_page = xlsx_html.xlsx_to_html(target)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"xlsx render failed: {type(e).__name__}: {e}")
    return HTMLResponse(content=html_page)


@app.get("/api/projects/{project}/step1/samples/{sample}/files")
def step1_sample_files(project: str, sample: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    step1_dir = project_dir / "step1"
    sample_dir = _resolve_sample_dir(step1_dir, sample) if step1_dir.is_dir() else None
    if not sample_dir:
        raise HTTPException(status_code=404, detail="Sample not found")
    base = sample_dir.resolve()
    entries = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(".~lock"):
            continue
        try:
            rel = path.relative_to(base).as_posix()
            stat = path.stat()
        except (OSError, ValueError):
            continue
        entries.append({
            "name": path.name,
            "relpath": rel,
            "path": str(path),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "type": path.suffix.lstrip(".").lower() or "file",
        })
    return {
        "project": project,
        "sample": sample,
        "sample_dir": str(base),
        "files": entries,
    }


@app.get("/api/projects/{project}/kraken/samples/{sample}/files")
def kraken_sample_files(project: str, sample: str):
    """List Kraken ID Parse outputs for a sample (cross-tool visibility).

    Kraken ID Parse GUI writes its per-sample results into
    ``<project>/kraken/<sample>/`` — the same project directory vSNP uses.
    Since both tools share /srv/kapurlab/projects, those files already sit
    on disk next to our step1 output; this endpoint simply surfaces them so
    a user looking at a sample in vSNP can see (and download, via the
    existing /download-file route) whatever Kraken produced for it.

    Returns ``present: false`` (not 404) when no Kraken run exists for the
    sample, so the UI can show a calm "no Kraken run yet" instead of an error.
    """
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    kraken_dir = project_dir / "kraken"
    # Kraken strips _R1/_R2 / _1/_2 read tags, so its sample dir name can be
    # longer OR shorter than the step1 sample name (especially when Kraken was
    # run from its own GUI on the raw download fastqs before step1). Match in
    # both directions so those earlier runs still surface here.
    sample_dir = _resolve_kraken_sample_dir(kraken_dir, sample) if kraken_dir.is_dir() else None
    if not sample_dir:
        return {
            "project": project,
            "sample": sample,
            "present": False,
            "sample_dir": "",
            "files": [],
        }
    base = sample_dir.resolve()
    entries = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(".~lock"):
            continue
        try:
            rel = path.relative_to(base).as_posix()
            stat = path.stat()
        except (OSError, ValueError):
            continue
        ext = path.suffix.lower()
        entries.append({
            "name": path.name,
            "relpath": rel,
            "path": str(path),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "type": path.suffix.lstrip(".").lower() or "file",
            # Browser-renderable (open in a tab) vs download-only. Krona/report
            # HTML, coverage PDFs, preview PNGs etc. open inline.
            "openable": ext in (".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg",
                                 ".svg", ".txt", ".log", ".csv", ".json"),
        })
    # Surface the most useful artifacts first: report/krona HTML, then the rest.
    def _rank(e):
        n = e["name"].lower()
        if n.endswith("_krona.html") or n == "report.html":
            return 0
        if n.endswith(".html") or n.endswith(".pdf"):
            return 1
        return 2
    entries.sort(key=lambda e: (_rank(e), e["relpath"]))
    return {
        "project": project,
        "sample": sample,
        "present": True,
        "sample_dir": str(base),
        "files": entries,
    }


@app.get("/api/projects/{project}/kraken/samples")
def kraken_samples(project: str):
    """List the sample names that have a Kraken output dir under
    <project>/kraken/. Lets the UI flag which download samples already have
    Kraken results without a per-sample request each."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    kraken_dir = project_dir / "kraken"
    names: List[str] = []
    if kraken_dir.is_dir():
        try:
            names = sorted(
                d.name for d in kraken_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            )
        except (OSError, PermissionError):
            names = []
    return {"project": project, "samples": names}


# ---------------------------------------------------------------------------
# Run Kraken ID Parse on a sample (cross-tool invocation).
#
# vSNP and Kraken ID Parse share /srv/kapurlab/projects, so we can kick off the
# Kraken pipeline directly from the vSNP Step 1 view and have it write into the
# same <project>/kraken/<sample>/ directory the results panel already reads.
#
# Crucially this uses the SHARED Kraken install — its own conda env and bin/
# scripts under /srv/kapurlab/tools/kraken_id_parse_gui — NOT the vSNP env (the
# vsnp3 env has neither kraken2, krona, nor SPAdes). The Kraken tool itself is
# lab-shared functionality; only the project data lives per-user/shared.
# ---------------------------------------------------------------------------
_KRAKEN_GUI_ROOT = Path("/srv/kapurlab/tools/kraken_id_parse_gui")

# Read-tag stripping identical to Kraken ID Parse's own pairing logic, so the
# sample name and R1/R2 selection match what that tool would pick on its own.
_KRAKEN_READ_TAG_RE = re.compile(r'(?:_R([12])(?:_\d+)?|_([12]))\.fastq\.gz$', re.IGNORECASE)


def _kraken_strip_read_tag(filename: str):
    m = _KRAKEN_READ_TAG_RE.search(filename)
    if m:
        return filename[:m.start()], (m.group(1) or m.group(2))
    return filename[:-len(".fastq.gz")], None


def _find_sample_fastqs(download_dir: Path, sample: str):
    """Return (r1, r2) Paths for a sample from <project>/download/.

    Matches the sample's read files by stripping Illumina/SRA read tags, with
    the same prefix fallback used elsewhere (sample ``13-1941-6`` matches
    ``13-1941-6_S4_L001_R1_001.fastq.gz``). r2 is None for single-end.
    """
    if not download_dir.is_dir():
        return None, None
    try:
        all_fq = sorted(download_dir.glob("*.fastq.gz"))
    except OSError:
        return None, None
    r1 = r2 = single = None
    for fq in all_fq:
        base, tag = _kraken_strip_read_tag(fq.name)
        if base != sample and not base.startswith(f"{sample}"):
            continue
        # Require the bare sample name to match the file's base (exact) or be a
        # prefix up to a lane/index separator, to avoid matching "S1" to "S10".
        if base != sample and not base.startswith(f"{sample}_"):
            continue
        if tag == "1":
            r1 = r1 or fq
        elif tag == "2":
            r2 = r2 or fq
        else:
            single = single or fq
    if r1:
        return r1, r2
    if single:
        return single, None
    return None, None


def _resolve_kraken_runtime() -> Dict[str, str]:
    """Locate the shared Kraken install's python + bin. Falls back to a
    per-user miniforge env only if the shared env is absent (mirrors the OOD
    launch script's resolution). Returns {python, gui_root, env_bin}."""
    shared_env = _KRAKEN_GUI_ROOT / "env"
    personal_env = Path.home() / "miniforge3" / "envs" / "kraken_id_parse"
    if (shared_env / "bin" / "python").exists():
        env_dir = shared_env
    elif (personal_env / "bin" / "python").exists():
        env_dir = personal_env
    else:
        env_dir = None
    python = str((env_dir / "bin" / "python")) if env_dir else sys.executable
    return {
        "python": python,
        "gui_root": str(_KRAKEN_GUI_ROOT),
        "env_bin": str(env_dir / "bin") if env_dir else "",
    }


class KrakenRunRequest(BaseModel):
    sample: str
    # "full": classify + parse reads + assemble + BLAST identification (needs a
    # taxon). "kraken_only": Kraken2 + Krona graph only (no taxon needed).
    mode: str = "full"
    taxon: Optional[str] = None
    kraken_db: Optional[str] = None
    blast_db: Optional[str] = None


@app.post("/api/projects/{project}/kraken/run")
def kraken_run(project: str, payload: KrakenRunRequest):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail="Project not found")

    if not _KRAKEN_GUI_ROOT.is_dir():
        raise HTTPException(
            status_code=503,
            detail=f"Kraken ID Parse is not installed at {_KRAKEN_GUI_ROOT}.",
        )
    script = _KRAKEN_GUI_ROOT / "bin" / "kraken_id_parse.py"
    if not script.is_file():
        raise HTTPException(status_code=503, detail=f"Kraken pipeline script missing: {script}")

    kraken_only = (payload.mode or "full").strip() == "kraken_only"
    taxon = (payload.taxon or "").strip()
    if not kraken_only and not taxon:
        raise HTTPException(status_code=400, detail="A target taxon is required for a full run.")

    sample = (payload.sample or "").strip()
    if not sample or "/" in sample or sample.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid sample name")

    r1, r2 = _find_sample_fastqs(project_dir / "download", sample)
    if r1 is None:
        raise HTTPException(
            status_code=404,
            detail=f"No FASTQ files found for sample {sample!r} in the project's download/ folder.",
        )

    # Name the output dir EXACTLY as the Kraken ID Parse tool would when run
    # from its own GUI on these same FASTQs (read-tag stripped from R1). This
    # keeps <project>/kraken/<dir> identical no matter which GUI launched the
    # run, so the Kraken GUI — which lists samples by that stripped name —
    # finds the results instead of showing "No Kraken results yet".
    kraken_sample = _kraken_strip_read_tag(r1.name)[0]

    # Kraken DB: request override → vsnp config (if a user set one) → shared default.
    kraken_db = (payload.kraken_db or "").strip() or cfg.get("kraken_db", "") \
        or "/srv/kapurlab/databases/kraken2/k2_standard_08gb"
    blast_db = (payload.blast_db or "").strip() or cfg.get("blast_db", "") \
        or "/srv/kapurlab/databases/blast/ref_prok_rep_genomes"
    if kraken_only and not kraken_db:
        raise HTTPException(status_code=400, detail="Kraken-only mode requires a Kraken DB path.")

    run_dir = project_dir / "kraken" / kraken_sample
    # Guard against two runs racing on the same output dir (they'd clobber each
    # other's temp/output folders). Track the last job id in a sentinel file,
    # the same pattern step1 uses with .step1_job_id.
    job_id_file = run_dir / ".kraken_job_id"
    if job_id_file.exists():
        prior = job_manager.get_job(job_id_file.read_text(encoding="utf-8").strip())
        if prior and prior.get("status") == "running":
            raise HTTPException(
                status_code=409,
                detail=f"A Kraken run is already in progress for {sample}.",
            )
    run_dir.mkdir(parents=True, exist_ok=True)

    rt = _resolve_kraken_runtime()
    parts = [shlex.quote(rt["python"]), "-u", shlex.quote(str(script)), "-r1", shlex.quote(str(r1))]
    if r2 is not None:
        parts += ["-r2", shlex.quote(str(r2))]
    if taxon:
        parts += ["-t", shlex.quote(taxon)]
    if kraken_db:
        parts += ["-k", shlex.quote(kraken_db)]
    if kraken_only:
        parts.append("--kraken-only")
    elif blast_db:
        parts += ["-b", shlex.quote(blast_db)]

    # Prepend the Kraken env bin so kraken2/krona/spades/seqkit resolve, and set
    # PYTHONPATH so the bin/ scripts' local imports work — exactly as the Kraken
    # GUI launches them.
    path_prefix = f"{rt['env_bin']}:" if rt["env_bin"] else ""
    command = (
        f'PYTHONUNBUFFERED=1 PYTHONPATH={shlex.quote(rt["gui_root"] + "/bin")} '
        f'PATH="{path_prefix}$PATH" {" ".join(parts)}'
    )

    label = "Kraken-only (Krona)" if kraken_only else (taxon or "identification")
    job_id = job_manager.start_job(
        name="kraken",
        command=command,
        cwd=run_dir,
        env=build_env(cfg),
    )
    job_id_file.write_text(job_id, encoding="utf-8")
    return {"job_id": job_id, "run_dir": str(run_dir), "sample": kraken_sample, "mode": "kraken_only" if kraken_only else "full", "label": label}


@app.get("/api/projects/{project}/step1/edits")
def step1_edits(project: str):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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




def _resolve_step2_output_dir(step2_dir: Path, run_id: Optional[str]) -> Path:
    """Resolve which directory to read step2 outputs from.

    Priority:
    1. Explicit run_id → step2/runs/{run_id}/
    2. .current_run sentinel → step2/runs/{value}/
    3. Latest entry in step2/runs/ by directory name (lexicographic = chronological)
    4. Legacy flat layout: step2/ itself
    """
    runs_dir = step2_dir / "runs"
    if run_id and run_id != "legacy":
        candidate = runs_dir / run_id
        if candidate.is_dir():
            return candidate
    if run_id == "legacy":
        return step2_dir
    current_file = step2_dir / ".current_run"
    if current_file.exists():
        current_ts = current_file.read_text(encoding="utf-8").strip()
        candidate = runs_dir / current_ts
        if candidate.is_dir():
            return candidate
    if runs_dir.is_dir():
        entries = sorted(
            (d for d in runs_dir.iterdir() if d.is_dir()),
            key=lambda d: d.name,
            reverse=True,
        )
        if entries:
            return entries[0]
    return step2_dir  # legacy flat


@app.get("/api/projects/{project}/step2_outputs")
def step2_outputs(project: str, run_id: Optional[str] = Query(None)):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    step2_dir = project_dir / "step2"
    if not step2_dir.exists():
        raise HTTPException(status_code=404, detail="Step2 directory not found")
    output_dir = _resolve_step2_output_dir(step2_dir, run_id)

    def _safe_name(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)

    def _find_group_fasta(group_dir: Path) -> Optional[Path]:
        for pattern in ("*.fasta", "*.fa", "*.fna"):
            matches = sorted(group_dir.glob(pattern))
            if matches:
                return matches[-1]
        return None

    def _count_fasta_sequences(fasta_path: Optional[Path]) -> int:
        if not fasta_path or not fasta_path.exists():
            return 0
        count = 0
        with fasta_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.startswith(">"):
                    count += 1
        return count

    top = []
    html_files = sorted(output_dir.glob("*.html"), key=lambda p: p.stat().st_mtime)
    if html_files:
        latest_html = html_files[-1]
        top.append({
            "label": latest_html.name,
            "path": str(latest_html),
            "type": "html",
            "download_name": f"{_safe_name(project)}__{latest_html.name}",
        })
    for f in output_dir.glob("*.zip"):
        top.append({
            "label": f.name,
            "path": str(f),
            "type": "zip",
            "download_name": f"{_safe_name(project)}__{f.name}",
        })
    mismatch_report = output_dir / "mismatch_report.csv"
    if mismatch_report.exists():
        top.append({
            "label": mismatch_report.name,
            "path": str(mismatch_report),
            "type": "csv",
            "download_name": f"{_safe_name(project)}__{mismatch_report.name}",
        })
    top.sort(key=lambda x: x["label"])

    groups = []
    for d in sorted(output_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name in ("vcf_source", "runs", "_provenance"):
            continue
        if d.name.startswith("."):
            continue
        fasta_path = _find_group_fasta(d)
        fasta_count = _count_fasta_sequences(fasta_path)
        # If a *_labeled.tre exists for a base group tree, hide the unlabeled
        # sibling — labeled has the lineage prefix prepended to each leaf
        # (e.g. L4_ERR2704709_zc.vcf), which is what makes the tree useful
        # for placing run samples in context. Unlabeled file is still on
        # disk for anyone going via the filesystem.
        labeled_bases = {
            f.name.removesuffix("_labeled.tre")
            for f in d.iterdir()
            if f.is_file() and f.name.endswith("_labeled.tre")
        }
        files = []
        for f in sorted(d.iterdir()):
            if f.is_file():
                if f.name.endswith(".tre") and not f.name.endswith("_labeled.tre"):
                    if f.name.removesuffix(".tre") in labeled_bases:
                        continue
                ext = f.suffix.lstrip(".")
                files.append({
                    "label": f.name,
                    "path": str(f),
                    "type": ext or "file",
                    "download_name": f"{_safe_name(project)}__{_safe_name(d.name)}__{f.name}",
                })
            elif f.is_dir() and f.name == "posthoc":
                for pf in sorted(f.iterdir()):
                    if pf.is_file():
                        ext = pf.suffix.lstrip(".")
                        files.append({
                            "label": f"posthoc/{pf.name}",
                            "path": str(pf),
                            "type": ext or "file",
                            "download_name": f"{_safe_name(project)}__{_safe_name(d.name)}__posthoc__{pf.name}",
                        })
        if files:
            groups.append({
                "name": d.name,
                "files": files,
                "posthoc_possible": fasta_count >= 3,
                "posthoc_reason": "" if fasta_count >= 3 else "Requires a FASTA with at least 3 sequences",
                "posthoc_sequence_count": fasta_count,
            })
    return {"top": top, "groups": groups, "run_id": output_dir.name if output_dir != step2_dir else "legacy"}


@app.get("/api/projects/{project}/step2/trees")
def step2_trees(project: str, run_id: Optional[str] = Query(None)):
    """List the latest .tre file per group under step2/."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    step2_dir = project_dir / "step2"
    if not step2_dir.exists():
        raise HTTPException(status_code=404, detail="Step2 directory not found")
    output_dir = _resolve_step2_output_dir(step2_dir, run_id)
    trees = []
    for d in sorted(output_dir.iterdir()):
        if not d.is_dir():
            continue
        if d.name in ("vcf_source", "runs", "_provenance") or d.name.startswith("."):
            continue
        tre_files = sorted(d.glob("*.tre"), key=lambda p: p.stat().st_mtime)
        if not tre_files:
            continue
        latest = tre_files[-1]
        trees.append({
            "group": d.name,
            "name": latest.name,
            "path": str(latest),
            "size": latest.stat().st_size,
            "mtime": latest.stat().st_mtime,
        })
    return {"trees": trees}


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


@app.get("/api/projects/{project}/preview-xlsx", response_class=HTMLResponse)
def preview_xlsx(project: str, path: str = Query(...), download: int = 0):
    """Render an xlsx file as a self-contained HTML page (formatting preserved
    via openpyxl). With ?download=1, returns the raw xlsx (for the "Download
    xlsx" link inside the preview page)."""
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    target = Path(path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.suffix.lower() not in (".xlsx", ".xlsm"):
        raise HTTPException(status_code=400, detail="Only .xlsx/.xlsm supported")
    if download:
        return FileResponse(
            target,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=target.name,
        )
    # Build sets of samples loadable in IGV so the cascade-table render
    # can correctly enable / grey out the "↗ this" affordance per row:
    #   - samples_with_bams: have a Step 1 BAM (full IGV: reads + calls)
    #   - samples_with_vcfs: have an imported VCF in step2/vcf_source/
    #     (calls-only IGV, anchored to the project reference)
    # A sample qualifies for "↗ this" if it's in either set.
    samples_with_bams: set[str] = set()
    samples_with_vcfs: set[str] = set()
    step1_dir = project_dir / "step1"
    if step1_dir.is_dir():
        for d in step1_dir.iterdir():
            if not d.is_dir():
                continue
            if any(d.glob(f"**/{d.name}_nodup.bam")):
                samples_with_bams.add(d.name)
            # _resolve_sample_dir accepts a bare sample name even when the
            # step1 dir carries lane suffixes (e.g. dir `13-1941-6_S4_L001`
            # resolves from input `13-1941-6`). Mirror that fallback here so
            # cascade stems match.
            if "_" in d.name:
                prefix = d.name.split("_")[0]
                if prefix and any(d.glob(f"**/{prefix}_nodup.bam")):
                    samples_with_bams.add(prefix)
    vcf_source_dir = project_dir / "step2" / "vcf_source"
    if vcf_source_dir.is_dir():
        for f in vcf_source_dir.iterdir():
            if not f.is_file():
                continue
            name = f.name
            # Strip the standard vSNP3 suffixes back to the stem.
            for suffix in ("_zc.vcf.gz", "_zc.vcf", ".vcf.gz", ".vcf"):
                if name.endswith(suffix):
                    samples_with_vcfs.add(name[: -len(suffix)])
                    break

    from app import xlsx_html
    try:
        html_page = xlsx_html.xlsx_to_html(
            target,
            project=project,
            samples_with_bams=samples_with_bams,
            samples_with_vcfs=samples_with_vcfs,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"xlsx render failed: {type(e).__name__}: {e}")
    return HTMLResponse(content=html_page)


@app.get("/api/projects/{project}/download-file")
def download_file(project: str, path: str = Query(...), inline: int = 0, download_name: Optional[str] = Query(None)):
    """Serve a file from within a project directory.

    Default (no ?inline) sets `Content-Disposition: attachment` so the browser
    downloads. With ?inline=1, omits the attachment disposition so the browser
    renders the file in-tab when it can (html, fasta, vcf, png, pdf, …) —
    used by the "View" buttons on the step2 results panel. With ?download_name=…,
    overrides the attachment filename (server-side enforcement of the project-
    qualified naming convention).
    """
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    target = Path(path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "application/octet-stream"
    suffix = target.suffix.lower()
    # Map suffix -> MIME so browsers render inline (?inline=1 path) for
    # everything the frontend's fileViewMode() flags as viewable. Without
    # this, e.g. JSON returns octet-stream and browsers force-download.
    if suffix in (".html", ".htm"):
        media_type = "text/html"
    elif suffix == ".csv":
        media_type = "text/csv"
    elif suffix in (".xlsx", ".xls", ".xlsm"):
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif suffix == ".pdf":
        media_type = "application/pdf"
    elif suffix == ".json":
        media_type = "application/json"
    elif suffix in (".jsonl", ".ndjson"):
        # NDJSON / JSON-lines — not valid as a single JSON document, so
        # render as plain text (browser shows the lines; doesn't try to
        # parse a tree). Used by Mg***_patchlog.jsonl from VCF edits.
        media_type = "text/plain"
    elif suffix == ".svg":
        media_type = "image/svg+xml"
    elif suffix == ".png":
        media_type = "image/png"
    elif suffix in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
    elif suffix == ".gif":
        media_type = "image/gif"
    elif suffix == ".webp":
        media_type = "image/webp"
    elif suffix in (
        ".tre", ".nwk", ".nexus", ".nex",
        ".fasta", ".fa", ".fna",
        ".vcf", ".txt", ".tsv", ".log",
        ".yaml", ".yml", ".md",
    ):
        media_type = "text/plain"
    if inline:
        # Defensive: if we didn't recognise the suffix and would otherwise
        # serve application/octet-stream, force text/plain so the browser
        # shows the file in-tab instead of triggering a download. This is
        # the safe default for "View" buttons — research data files often
        # have idiosyncratic extensions (e.g. `.amr.tsv`, `.patchlog.jsonl`)
        # that we don't want to enumerate exhaustively.
        if media_type == "application/octet-stream":
            media_type = "text/plain"
        return FileResponse(target, media_type=media_type)
    filename = target.name
    if download_name:
        safe_name = Path(download_name).name
        if safe_name:
            filename = safe_name
    return FileResponse(target, media_type=media_type, filename=filename)


@app.get("/api/download-file")
def download_file_global(path: str = Query(...)):
    """Download any file from within any configured projects root."""
    cfg = load_config()
    target = Path(path).resolve()
    if not _path_under_any_project_root(cfg, target):
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, media_type="application/octet-stream", filename=target.name)


_IGV_SERVE_MEDIA_TYPES = {
    ".bam": "application/octet-stream",
    ".bai": "application/octet-stream",
    ".cram": "application/octet-stream",
    ".crai": "application/octet-stream",
    ".vcf": "text/plain",
    ".gz": "application/gzip",
    ".tbi": "application/octet-stream",
    ".fasta": "text/plain",
    ".fa": "text/plain",
    ".fai": "text/plain",
    ".gff": "text/plain",
    ".gff3": "text/plain",
    ".bed": "text/plain",
    ".gbk": "text/plain",
}


def _range_response(target: Path, request: Request, media_type: str):
    file_size = target.stat().st_size
    range_header = request.headers.get("range") or request.headers.get("Range")
    if not range_header:
        def _full():
            with open(target, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        }
        return StreamingResponse(_full(), media_type=media_type, headers=headers)
    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=400, detail="Invalid Range header")
    try:
        spec = range_header[len("bytes="):].split(",", 1)[0].strip()
        start_s, end_s = spec.split("-", 1)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Range header")
    if start < 0 or end >= file_size or start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    length = end - start + 1

    def _slice():
        with open(target, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
    }
    return StreamingResponse(_slice(), status_code=206, media_type=media_type, headers=headers)


@app.get("/api/projects/{project}/serve")
def serve_project_file(project: str, request: Request, path: str = Query(...)):
    """Serve a file from within the project directory with HTTP byte-range support.

    Used by igv.js to stream BAM/BAI/FASTA/FAI without forcing a full
    download. Also permits paths under any configured reference options
    root (so igv.js can fetch the reference GFF annotation track — the
    GFF lives next to the source fasta in the reference dir, not in the
    project's alignment dir).
    """
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
    target = Path(path).resolve()
    target_str = str(target)
    allowed = target_str.startswith(str(project_dir.resolve()))
    if not allowed:
        vsnp3_path = Path(cfg.get("vsnp3_path", ""))
        for root in reference_roots(vsnp3_path):
            if target_str.startswith(str(root.resolve())):
                allowed = True
                break
    if not allowed:
        raise HTTPException(status_code=400, detail="Path not allowed")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    suffix = target.suffix.lower()
    media_type = _IGV_SERVE_MEDIA_TYPES.get(suffix, "application/octet-stream")
    return _range_response(target, request, media_type)


@app.post("/api/projects/{project}/vcf_edit")
def vcf_edit(project: str, payload: VcfEditRequest):
    cfg = load_config()
    project_dir = _project_dir_for(cfg, project)
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
    project_dir = _project_dir_for(cfg, project)
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
    """Map every plausible FASTA stem -> reference directory name.

    vsnp3_step1.py copies the reference FASTA into each sample's alignment
    dir and renames it, stripping NCBI version suffixes — so a reference
    FASTA shipped as `NZ_LS483305.1.fasta` becomes `NZ_LS483305.fasta` in
    the sample dirs, and the resulting VCF's `##reference=` points at the
    renamed file. To survive that rename, index both the original stem
    and the version-stripped form (and a fully-extensionless variant for
    multi-dot filenames like `MTBC0_v1.1.fasta`).
    """
    import re
    aliases: Dict[str, str] = {}

    def _add(key: str, name: str) -> None:
        if not key:
            return
        existing = aliases.get(key)
        if existing is None:
            aliases[key] = name
            return
        # Prefer the more-specific match if a stem collides across refs.
        key_lc = key.lower()
        if key_lc in name.lower() and key_lc not in existing.lower():
            aliases[key] = name

    refs = list_references(vsnp3_path)
    for ref in refs:
        name = ref.get("name")
        base = Path(ref.get("path", ""))
        if not name or not base.exists():
            continue
        for ext in (".fa", ".fasta", ".fna", ".fas"):
            for fasta in base.rglob(f"*{ext}"):
                stem = fasta.stem
                _add(stem, name)
                # NCBI version-suffix variant: `NZ_LS483305.1` -> `NZ_LS483305`.
                stripped = re.sub(r"\.\d+$", "", stem)
                if stripped != stem:
                    _add(stripped, name)
                # Fully extensionless variant for multi-dot stems like
                # `MTBC0_v1.1` -> would still resolve via the previous rule,
                # but covers anything else weird.
                root = fasta.name.split(".", 1)[0]
                if root and root != stem:
                    _add(root, name)
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


# ---------------------------------------------------------------------------
# Serve frontend static files (added for OOD deployment)
# ---------------------------------------------------------------------------
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse as _FileResponse

_frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"

if _frontend_dist.exists():
    @app.get("/", include_in_schema=False)
    async def _serve_root():
        return _FileResponse(_frontend_dist / "index.html")

    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="static_assets")

    # Serve other static files at root level (favicon, etc.)
    for _f in _frontend_dist.iterdir():
        if _f.is_file() and _f.name != "index.html":
            _fname = _f.name
            @app.get(f"/{_fname}", include_in_schema=False)
            async def _serve_static(fname=_fname):
                return _FileResponse(_frontend_dist / fname)


def _posthoc_lock_path(step2_dir: Path, group: str, tool: str) -> Path:
    return step2_dir / group / "posthoc" / f".{tool}.lock"


def _posthoc_clear_stale_lock(lock_path: Path) -> None:
    if not lock_path.exists():
        return
    job_id = lock_path.read_text(encoding="utf-8").strip()
    if not job_id:
        lock_path.unlink()
        return
    job = job_manager.get_job(job_id)
    if not job or job.get("status") in {"succeeded", "failed"}:
        lock_path.unlink()


def _posthoc_stub_command(cfg: Dict, stats_path: Path, group: str, tool: str) -> str:
    code = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "out=Path(sys.argv[1])\n"
        "payload={\n"
        "    'tool': sys.argv[2],\n"
        "    'group': sys.argv[3],\n"
        "    'status': 'stub',\n"
        "    'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())\n"
        "}\n"
        "out.write_text(json.dumps(payload, indent=2), encoding='utf-8')\n"
    )
    cmd_list = conda_python_cmd(cfg, code, [str(stats_path), tool, group])
    return " ".join(shlex.quote(part) for part in cmd_list)


def _posthoc_snp_analysis_command(group_dir: Path, group_name: str, out_dir: Path, tool_bin: str, scope: str) -> str:
    snp_dists_path = "snp-dists"
    if tool_bin:
        candidate = Path(tool_bin) / "snp-dists"
        if candidate.exists():
            snp_dists_path = str(candidate)
    cmd_parts = [
        sys.executable,
        "-m",
        "app.posthoc.snp_analysis",
        "--group-dir",
        str(group_dir),
        "--group-name",
        group_name,
        "--out-dir",
        str(out_dir),
        "--snp-dists",
        snp_dists_path,
        "--scope",
        scope,
    ]
    return " ".join(shlex.quote(part) for part in cmd_parts)
