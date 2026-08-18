"""Unit tests for main._resolve_kraken_runtime.

The failure this guards against: on a personal install, Kraken ID Parse can
live in a NAMED conda env (<conda base>/envs/kraken_id_parse) with no
<checkout>/env directory at all. The old resolver guessed two hardcoded
paths and then silently fell back to THIS backend's interpreter — vsnp3's
python has no scikit-allel, so a Step 1 Kraken run died with a
ModuleNotFoundError three imports deep instead of "kraken env not found".
The launcher already exports the correct answer
(BDTOOLS_SIBLING_ENV_KRAKEN_ID_PARSE_GUI); the resolver must honor it first,
and when nothing is found it must return an EMPTY python so the endpoint can
refuse loudly.

Run directly:  python test_kraken_runtime.py   (needs fastapi importable)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main  # noqa: E402


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(condition, label):
    if not condition:
        raise AssertionError(f"{label}: expected truthy, got falsy")
    print(f"  OK  {label}")


def make_env(root: Path) -> Path:
    (root / "bin").mkdir(parents=True, exist_ok=True)
    (root / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    return root


SIB_VAR = "BDTOOLS_SIBLING_ENV_KRAKEN_ID_PARSE_GUI"


def main_() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="kraken_rt_"))
    saved_root = main._KRAKEN_GUI_ROOT
    saved_env = {k: os.environ.get(k) for k in (SIB_VAR, "CONDA_EXE", "HOME")}
    try:
        # Hermetic home so a real ~/miniforge3 env can't leak into the probe.
        os.environ["HOME"] = str(tmp / "home")
        os.environ.pop(SIB_VAR, None)
        os.environ.pop("CONDA_EXE", None)
        checkout = tmp / "kraken_checkout"
        checkout.mkdir()
        main._KRAKEN_GUI_ROOT = checkout

        print("\n[sibling env var wins]")
        named = make_env(tmp / "conda" / "envs" / "kraken_id_parse")
        os.environ[SIB_VAR] = str(named)
        rt = main._resolve_kraken_runtime()
        assert_eq(rt["python"], str(named / "bin" / "python"),
                  "python from the launcher-exported env")
        assert_eq(rt["env_bin"], str(named / "bin"), "env_bin matches")

        print("\n[stale var falls through to <checkout>/env]")
        os.environ[SIB_VAR] = str(tmp / "gone")
        own = make_env(checkout / "env")
        rt = main._resolve_kraken_runtime()
        assert_eq(rt["python"], str(own / "bin" / "python"),
                  "checkout env picked when the var path is dead")
        assert_true(str(tmp / "gone") in rt["looked"],
                    "the dead var path is recorded in looked")

        print("\n[named env found via CONDA_EXE base]")
        os.environ.pop(SIB_VAR, None)
        shutil.rmtree(own)
        os.environ["CONDA_EXE"] = str(tmp / "conda" / "bin" / "conda")
        rt = main._resolve_kraken_runtime()
        assert_eq(rt["python"], str(named / "bin" / "python"),
                  "personal named env resolved through CONDA_EXE")

        print("\n[nothing found -> empty python, loud caller]")
        os.environ.pop("CONDA_EXE", None)
        rt = main._resolve_kraken_runtime()
        # The bundled-env escape hatch only applies when kraken2 sits next to
        # this interpreter; the test env (vsnp3) has no kraken2.
        if (Path(sys.executable).parent / "kraken2").exists():
            print("  SKIP  interpreter env carries kraken2; bundled fallback applies")
        else:
            assert_eq(rt["python"], "", "no silent fallback to sys.executable")
            assert_true(len(rt["looked"]) >= 2, "looked lists the tried paths")

        print("\nAll kraken runtime resolution tests passed.")
        return 0
    finally:
        main._KRAKEN_GUI_ROOT = saved_root
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main_())
