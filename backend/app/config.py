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
DEFAULTS: Dict[str, Any] = {
    "vsnp3_path": str(HOME_DIR / "miniforge3" / "envs" / "vsnp3"),
    "projects_root": str(HOME_DIR / "projects"),
    "bcftools_path": "",
    "step1_max_parallel": 3,
    "sra": {
        "use_sratoolkit_first": True,
        "allow_insecure_https": False,
        "max_parallel": 2
    },
    "vcf_db_folders": []
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
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
