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
# A path literal here is only ever right on the machine it was written for and
# fails silently everywhere else (a fresh HPC deployment 503'd "Kraken ID Parse
# is not installed at /srv/kapurlab/..." because the old unconditional
# /srv/kapurlab default won over reality). Resolution order — nothing baked-in
# ever decides a NEW deployment:
#   1. VSNP_GUI_SITE_ROOT — the launcher's explicit word (OOD script, bdtools)
#   2. BDTOOLS_SITE_ROOT  — the umbrella's site.conf, exported by tool_launch
#   3. the grandparent dir when this checkout sits at <root>/tools/vsnp_gui
#   4. /srv/kapurlab ONLY if it exists — reference-install (wgs3) compat for
#      dev checkouts launched outside bdtools on that machine
#   5. a per-user placeholder that exists nowhere, so every shared-tree feature
#      degrades honestly instead of pointing at another site's layout
def _resolve_site_root() -> Path:
    for var in ("VSNP_GUI_SITE_ROOT", "BDTOOLS_SITE_ROOT"):
        val = os.environ.get(var, "").strip()
        if val:
            return Path(val)
    checkout = Path(__file__).resolve().parents[2]   # .../vsnp_gui
    if checkout.parent.name == "tools":
        return checkout.parent.parent
    legacy = Path("/srv/kapurlab")
    if legacy.is_dir():
        return legacy
    return HOME_DIR / ".local" / "share" / "vsnp_gui" / "site"


_SITE_ROOT = _resolve_site_root()
# Public: other modules (jobs.py, main.py, provenance_writer.py) derive their
# shared-tree paths (audit logs, deploy dir, refs) from this instead of
# hard-coding /srv/kapurlab. Driven by VSNP_GUI_SITE_ROOT (see above).
SITE_ROOT = _SITE_ROOT

# Where the SIBLING tool checkouts live (kraken_id_parse_gui, vsnp3, ...).
# BDTOOLS_TOOLS_ROOT is exported by every bdtools launch and names the dir that
# actually CONTAINS the checkouts, whatever the site called it (_github/ on the
# ICDS cluster, tools/ on wgs3). Fall back to <SITE_ROOT>/tools, then to this
# checkout's own parent — a sibling install is a sibling wherever the tree sits.
def _resolve_tools_root() -> Path:
    val = os.environ.get("BDTOOLS_TOOLS_ROOT", "").strip()
    if val:
        return Path(val)
    site_tools = _SITE_ROOT / "tools"
    if site_tools.is_dir():
        return site_tools
    return Path(__file__).resolve().parents[3]


TOOLS_ROOT = _resolve_tools_root()

# Site database root (kraken2/, blast/ subdirs). BDTOOLS_DB_ROOT is exported by
# bdtools launches when the site recorded one; otherwise derive from SITE_ROOT.
# Consumers must existence-guard anything built from this — on a machine with
# no site databases the correct default is "", not a path that cannot exist.
def _resolve_db_root() -> Path:
    val = os.environ.get("BDTOOLS_DB_ROOT", "").strip()
    if val:
        return Path(val)
    return _SITE_ROOT / "databases"


DB_ROOT = _resolve_db_root()

_SHARED_VSNP3 = TOOLS_ROOT / "vsnp3"
_PERSONAL_VSNP3 = HOME_DIR / "miniforge3" / "envs" / "vsnp3"
_DEFAULT_VSNP3_PATH = _SHARED_VSNP3 if _SHARED_VSNP3.is_dir() else _PERSONAL_VSNP3

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

# --- Site-derived paths -----------------------------------------------------
# These keys are functions of the LAUNCH CONTEXT (site.conf, launcher env vars,
# checkout position), not user data. They used to sit in DEFAULTS and get
# frozen into each user's config.json by their FIRST launch — so whichever
# context a user happened to launch from first (often a personal dev install
# in $HOME) decided the vsnp3 every later session ran, including shared OOD
# sessions, and no update could ever heal it. Now they are recomputed on every
# load; config.json stores only an explicit user choice, under
# "path_overrides", so a deliberate override survives while stale frozen
# defaults self-repair the next time the app starts.
SITE_DERIVED_KEYS = (
    "vsnp3_path",
    "bcftools_path",
    "shared_projects_root",
    "vcf_db_folders_root",
    "vsnp_gui_deploy_path",
    "audit_root",
    "vsnp3_reference_options_root",
)


def site_path_defaults(vsnp3_effective: str = "") -> Dict[str, str]:
    """The launch context's own answer for every site-derived key.

    bcftools ships inside the vsnp3 env, so its default follows the EFFECTIVE
    vsnp3 path (a user's override included), not the site default."""
    d = {
        "vsnp3_path": str(_DEFAULT_VSNP3_PATH),
        "shared_projects_root": _DEFAULT_SHARED_PROJECTS_ROOT,
        "vcf_db_folders_root": _DEFAULT_SHARED_VCF_DB_ROOT,
        "vsnp_gui_deploy_path": str(TOOLS_ROOT / "vsnp_gui"),
        "audit_root": str(_SITE_ROOT / "audit"),
        "vsnp3_reference_options_root": str(_SITE_ROOT / "refs" / "vsnp3" / "reference_options"),
    }
    base = (vsnp3_effective or "").strip() or d["vsnp3_path"]
    d["bcftools_path"] = str(Path(base) / "bin" / "bcftools")
    return d


def _clean_path_overrides(raw: Any) -> Dict[str, str]:
    """Only known keys, only non-empty strings. Anything else is dropped."""
    out: Dict[str, str] = {}
    if isinstance(raw, dict):
        for k in SITE_DERIVED_KEYS:
            v = str(raw.get(k, "") or "").strip()
            if v:
                out[k] = v
    return out


def set_path_override(cfg: Dict[str, Any], key: str, value: str) -> None:
    """Record (or clear) a user's explicit choice for one site-derived path.

    Empty value = back to the site default. A value EQUAL to the current site
    default is also stored as "no override": accepting the default is not a
    choice worth freezing — freezing it is exactly the bug this replaces."""
    if key not in SITE_DERIVED_KEYS:
        return
    overrides = _clean_path_overrides(cfg.get("path_overrides"))
    value = (value or "").strip()
    defaults = site_path_defaults(overrides.get("vsnp3_path", ""))
    if not value or value == defaults.get(key, ""):
        overrides.pop(key, None)
    else:
        overrides[key] = value
    cfg["path_overrides"] = overrides


def apply_site_paths(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Fill the effective value of every site-derived key: override, else the
    freshly computed site default. The launcher's word on the shared projects
    root stays authoritative when present (even empty — that's how a
    single-user local launch collapses to one Projects root)."""
    overrides = _clean_path_overrides(cfg.get("path_overrides"))
    cfg["path_overrides"] = overrides
    defaults = site_path_defaults(overrides.get("vsnp3_path", ""))
    for k in SITE_DERIVED_KEYS:
        cfg[k] = overrides.get(k) or defaults[k]
    if _ENV_SHARED_PROJECTS_ROOT is not None:
        cfg["shared_projects_root"] = _ENV_SHARED_PROJECTS_ROOT.strip()
    return cfg


DEFAULTS: Dict[str, Any] = {
    "projects_root": str(HOME_DIR / "projects"),
    # Curated, user-managed Projects-root bookmarks (Settings: save/remove/jump).
    "saved_project_roots": [],
    # Same idea for the vSNP3 install path (Settings: jump between installs;
    # the site default is always offered first and needs no bookmark).
    "saved_vsnp3_paths": [],
    # Explicit user choices for SITE_DERIVED_KEYS — the ONLY path state that
    # persists. Everything else is re-derived from the launch context.
    "path_overrides": {},
    "step1_max_parallel": 3,
    "sra": {
        "use_sratoolkit_first": True,
        "allow_insecure_https": False,
        "max_parallel": 2
    },
    "vcf_db_folders": [],
    # T-46 dispatch junk-floor: paired fastqs where either read is smaller than
    # this are auto-skipped as likely junk/incomplete. Default 50 KB catches the
    # ~43-47 KB SRA-submission-error files we've seen while still passing
    # legitimately small viral/amplicon reads (e.g. SARS-CoV-2 ~200 KB). Raise
    # it for bacterial-WGS-only sites if you want a stricter floor.
    "step1_min_fastq_bytes": 50 * 1024,
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
    # Older versions froze the site-derived paths into this file at first
    # launch. Drop any stored copy — the effective values are recomputed below,
    # and a deliberate choice lives in "path_overrides", not here. This is the
    # one-time migration for every pre-existing config.json, applied on every
    # load so a file written by an old version at any point stays harmless.
    for k in SITE_DERIVED_KEYS:
        cfg.pop(k, None)
    # Backfill any keys added in newer schema versions (e.g. saved_vsnp3_paths).
    # Existing users get sensible defaults without losing what they customized.
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    return apply_site_paths(cfg)


def save_config(cfg: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Never persist the effective site-derived values load_config() filled in —
    # a frozen copy of a computed default is precisely the multi-user bug this
    # layout replaced. "path_overrides" carries the user's explicit choices.
    to_write = {k: v for k, v in cfg.items() if k not in SITE_DERIVED_KEYS}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(to_write, f, indent=2, sort_keys=True)
