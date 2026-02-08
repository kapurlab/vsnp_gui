import json
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"

HOME_DIR = Path.home()
DEFAULTS: Dict[str, Any] = {
    "vsnp3_path": str(HOME_DIR / "vsnp3"),
    "projects_root": str(HOME_DIR / "vsnp3" / "projects"),
    "igv_app_path": "",
    "figtree_app_path": "",
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
    DATA_DIR.mkdir(exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    vsnp3_path = Path(cfg.get("vsnp3_path", ""))
    projects_root = Path(cfg.get("projects_root", ""))
    if str(vsnp3_path).startswith("/Users/vivekkapur") and not vsnp3_path.exists():
        cfg["vsnp3_path"] = DEFAULTS["vsnp3_path"]
    if str(projects_root).startswith("/Users/vivekkapur") and not projects_root.exists():
        cfg["projects_root"] = DEFAULTS["projects_root"]
    return cfg


def save_config(cfg: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
