import json
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS: Dict[str, Any] = {
    "vsnp3_path": "/Users/vivekkapur/vsnp3",
    "projects_root": "/Users/vivekkapur/vsnp3/projects",
    "conda_env": "vsnp3",
    "conda_exe": "",
    "conda_env_path": "",
    "sra": {
        "use_sratoolkit_first": True,
        "allow_insecure_https": False,
        "max_parallel": 2
    }
}


def load_config() -> Dict[str, Any]:
    DATA_DIR.mkdir(exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULTS)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
