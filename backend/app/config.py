import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict

# Per-user config under XDG_CONFIG_HOME (defaults to ~/.config). With multiple
# users sharing one vsnp_gui install (T-12a), each user gets their own
# config.json — Tod's projects_root must not clobber Vivek's.
def _user_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg) / "vsnp_gui"
    return Path.home() / ".config" / "vsnp_gui"


DATA_DIR = _user_config_dir()
CONFIG_PATH = DATA_DIR / "config.json"

# Legacy location (single global file alongside the source tree). One-time
# migration handled on first load; safe to keep for back-compat reads.
_LEGACY_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"

HOME_DIR = Path.home()

# Shared lab tools live under <SITE_ROOT>/tools/ (see docs/dev/MULTIUSER.md).
# SITE_ROOT is /srv/kapurlab on the reference install (wgs3); other sites (e.g.
# NIVEDI -> /srv/nivedi) set VSNP_GUI_SITE_ROOT. The OOD launcher
# (template/script.sh.erb) exports it; it defaults to /srv/kapurlab so the
# reference install is unchanged. Falls back to a per-user $HOME path if the
# shared install doesn't exist (single-user dev box, or pre-T-11 systems).
_SITE_ROOT = Path(os.environ.get("VSNP_GUI_SITE_ROOT", "/srv/kapurlab").strip() or "/srv/kapurlab")
# Public: other modules (jobs.py, main.py, provenance_writer.py) derive their
# shared-tree paths (audit logs, deploy dir, refs) from this instead of
# hard-coding /srv/kapurlab. Driven by VSNP_GUI_SITE_ROOT (see above).
SITE_ROOT = _SITE_ROOT
_SHARED_VSNP3 = _SITE_ROOT / "tools" / "vsnp3"
_PERSONAL_VSNP3 = HOME_DIR / "miniforge3" / "envs" / "vsnp3"
_DEFAULT_VSNP3_PATH = _SHARED_VSNP3 if _SHARED_VSNP3.is_dir() else _PERSONAL_VSNP3
_DEFAULT_BCFTOOLS = str(_DEFAULT_VSNP3_PATH / "bin" / "bcftools")

# Shared projects root (T-12a). Surfaces in /api/config; backend's project
# listing scans both this and per-user projects_root. Multi-user server installs
# leave it deriving from SITE_ROOT/projects (shared visibility for the whole
# lab). Single-user installs (e.g. `bdtools local`) set the env var
# VSNP_GUI_SHARED_PROJECTS_ROOT — when PRESENT (even empty), it is authoritative
# and wins over both this default and any previously-saved value, so the local
# launcher can set it to "" to collapse to a single Projects root.
_SHARED_PROJECTS_ROOT = _SITE_ROOT / "projects"
_DEFAULT_SHARED_PROJECTS_ROOT = (
    str(_SHARED_PROJECTS_ROOT) if _SHARED_PROJECTS_ROOT.is_dir() else ""
)
# os.environ.get(...) is None when the var is unset; "" when explicitly disabled.
_ENV_SHARED_PROJECTS_ROOT = os.environ.get("VSNP_GUI_SHARED_PROJECTS_ROOT")

# Shared VCF database root. Subdirectories under this path are auto-discovered
# as VCF DB folders for every user (in addition to whatever they add manually
# via vcf_db_folders). Lab admins curate by managing the filesystem.
_SHARED_VCF_DB_ROOT = _SITE_ROOT / "refs" / "vsnp3" / "vcf_db_folders"
_DEFAULT_SHARED_VCF_DB_ROOT = (
    str(_SHARED_VCF_DB_ROOT) if _SHARED_VCF_DB_ROOT.is_dir() else ""
)

DEFAULTS: Dict[str, Any] = {
    "vsnp3_path": str(_DEFAULT_VSNP3_PATH),
    "projects_root": str(HOME_DIR / "projects"),
    "shared_projects_root": _DEFAULT_SHARED_PROJECTS_ROOT,
    # Curated, user-managed Projects-root bookmarks (Settings: save/remove/jump).
    "saved_project_roots": [],
    "bcftools_path": _DEFAULT_BCFTOOLS,
    "step1_max_parallel": 3,
    "sra": {
        "use_sratoolkit_first": True,
        "allow_insecure_https": False,
        "max_parallel": 2
    },
    "vcf_db_folders": [],
    "vcf_db_folders_root": _DEFAULT_SHARED_VCF_DB_ROOT,
    # T-46 dispatch junk-floor: paired fastqs where either read is smaller than
    # this are auto-skipped as likely junk/incomplete. Default 50 KB catches the
    # ~43-47 KB SRA-submission-error files we've seen while still passing
    # legitimately small viral/amplicon reads (e.g. SARS-CoV-2 ~200 KB). Raise
    # it for bacterial-WGS-only sites if you want a stricter floor.
    "step1_min_fastq_bytes": 50 * 1024,
    # Shared-tree paths the provenance writer (T-07) reads. Derived from
    # SITE_ROOT so they're correct at any site; were previously hard-coded to
    # /srv/kapurlab inside provenance_writer.py, which 500'd Step 1 elsewhere.
    "vsnp_gui_deploy_path": str(_SITE_ROOT / "tools" / "vsnp_gui"),
    "audit_root": str(_SITE_ROOT / "audit"),
    "vsnp3_reference_options_root": str(_SITE_ROOT / "refs" / "vsnp3" / "reference_options"),
    # Per-user opt-out for shared DBs: paths the user has chosen to skip in
    # their analyses. Shared entries are visible but unchecked when in here.
    "disabled_vcf_db_paths": [],
    # T-09 QC verdict thresholds. A project-level override may live in
    # project.json["qc_thresholds"]; backend merges project > user > defaults.
    "qc_thresholds": {
        "coverage": {"pass_min": 30.0, "review_min": 10.0},        # X (avg depth)
        "mapping_rate": {"pass_min": 90.0, "review_min": 70.0},    # %
        "contamination_review": True,                              # any positive flag → review
    },
}


def load_config() -> Dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        if _LEGACY_CONFIG_PATH.exists() and _LEGACY_CONFIG_PATH.stat().st_uid == os.geteuid():
            # The user that originally created the shared-install config is
            # migrating to per-user config. Other users start with defaults.
            shutil.copy2(_LEGACY_CONFIG_PATH, CONFIG_PATH)
        else:
            save_config(DEFAULTS)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.pop("igv_app_path", None)
    cfg.pop("figtree_app_path", None)
    # Backfill any keys added in newer schema versions (e.g. shared_projects_root
    # added in T-12a). Existing users get sensible defaults without losing
    # whatever they've customized.
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    # A present VSNP_GUI_SHARED_PROJECTS_ROOT overrides any saved/default value
    # on every load, so a single-user launcher setting it to "" reliably
    # disables the shared root regardless of what was persisted earlier.
    if _ENV_SHARED_PROJECTS_ROOT is not None:
        cfg["shared_projects_root"] = _ENV_SHARED_PROJECTS_ROOT.strip()
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
