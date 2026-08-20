"""Site-derived paths are recomputed on every load; config.json persists only
explicit user choices (the "path_overrides" dict).

Guards the multi-user OOD bug this layout replaced: DEFAULTS used to be frozen
into ~/.config/vsnp_gui/config.json by each user's FIRST launch, so whichever
context that happened to be — typically a personal dev install under $HOME —
decided the vsnp3 every later session ran, shared OOD sessions included, and
no update could heal it. Now a stale frozen path self-repairs on the next
load, while a deliberate override survives updates.

Run directly:  python test_config_site_paths.py
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(condition, label):
    if not condition:
        raise AssertionError(f"{label}: expected truthy, got falsy")
    print(f"  OK  {label}")


_SAVED_ENV = {}
_ENV_KEYS = (
    "VSNP_GUI_SITE_ROOT", "BDTOOLS_SITE_ROOT", "BDTOOLS_TOOLS_ROOT",
    "BDTOOLS_DB_ROOT", "VSNP_GUI_SHARED_PROJECTS_ROOT", "XDG_CONFIG_HOME",
)


def load_module(site_root: Path, xdg: Path, shared_env=None):
    """(Re)import config under a controlled launch context. config.py resolves
    its roots at import time — exactly once per process — so each scenario is
    a fresh reload."""
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ["VSNP_GUI_SITE_ROOT"] = str(site_root)
    os.environ["XDG_CONFIG_HOME"] = str(xdg)
    if shared_env is not None:
        os.environ["VSNP_GUI_SHARED_PROJECTS_ROOT"] = shared_env
    import config
    return importlib.reload(config)


def read_file(cfg_path: Path) -> dict:
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cfg_site_paths_"))
    for k in _ENV_KEYS:
        _SAVED_ENV[k] = os.environ.get(k)
    try:
        site = tmp / "site"
        (site / "tools" / "vsnp3" / "bin").mkdir(parents=True)
        (site / "projects").mkdir(parents=True)
        xdg = tmp / "xdg"

        print("fresh user: effective values come from the launch context")
        config = load_module(site, xdg)
        cfg = config.load_config()
        assert_eq(cfg["vsnp3_path"], str(site / "tools" / "vsnp3"), "vsnp3_path")
        assert_eq(cfg["bcftools_path"], str(site / "tools" / "vsnp3" / "bin" / "bcftools"), "bcftools_path")
        assert_eq(cfg["shared_projects_root"], str(site / "projects"), "shared_projects_root")
        assert_eq(cfg["audit_root"], str(site / "audit"), "audit_root")
        on_disk = read_file(config.CONFIG_PATH)
        for k in config.SITE_DERIVED_KEYS:
            assert_true(k not in on_disk, f"{k} not persisted")

        print("stale frozen personal paths are dropped, not honoured")
        frozen = dict(on_disk)
        frozen["vsnp3_path"] = "/home/someone/.local/share/bdtools/vsnp3-site/tools/vsnp3"
        frozen["shared_projects_root"] = ""
        config.CONFIG_PATH.write_text(json.dumps(frozen), encoding="utf-8")
        cfg = config.load_config()
        assert_eq(cfg["vsnp3_path"], str(site / "tools" / "vsnp3"), "vsnp3_path healed")
        assert_eq(cfg["shared_projects_root"], str(site / "projects"), "shared root healed")
        config.save_config(cfg)
        on_disk = read_file(config.CONFIG_PATH)
        assert_true("vsnp3_path" not in on_disk, "frozen key gone after save")

        print("an explicit override persists and bcftools follows it")
        custom = tmp / "custom-vsnp3"
        (custom / "bin").mkdir(parents=True)
        cfg = config.load_config()
        config.set_path_override(cfg, "vsnp3_path", str(custom))
        config.save_config(config.apply_site_paths(cfg))
        cfg = config.load_config()
        assert_eq(cfg["vsnp3_path"], str(custom), "override wins")
        assert_eq(cfg["bcftools_path"], str(custom / "bin" / "bcftools"), "bcftools follows override")
        assert_eq(read_file(config.CONFIG_PATH)["path_overrides"], {"vsnp3_path": str(custom)}, "override on disk")

        print("choosing the site default clears the override; so does empty")
        config.set_path_override(cfg, "vsnp3_path", str(site / "tools" / "vsnp3"))
        assert_eq(cfg["path_overrides"], {}, "equal-to-default clears")
        config.set_path_override(cfg, "vsnp3_path", str(custom))
        config.set_path_override(cfg, "vsnp3_path", "")
        assert_eq(cfg["path_overrides"], {}, "empty clears")

        print("the launcher's shared-projects word stays authoritative")
        config.save_config(config.apply_site_paths(cfg))
        config = load_module(site, xdg, shared_env="")
        cfg = config.load_config()
        assert_eq(cfg["shared_projects_root"], "", "env '' disables shared root")

        print("unknown keys can't sneak in as overrides")
        config = load_module(site, xdg)
        cfg = config.load_config()
        config.set_path_override(cfg, "projects_root", "/somewhere")
        assert_eq(cfg["path_overrides"], {}, "non-derived key ignored")

        print("ALL PASSED")
        return 0
    finally:
        for k, v in _SAVED_ENV.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    sys.exit(main())
