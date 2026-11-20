"""Where the GUI looks for the FMDV reference library, and in what order.

`bdtools setup-databases fmdv` builds the library at
<database root>/fmdv/fmdv_refs and links <site>/refs/fmdv to it. The GUI used
to read only the link (then ~/fmdv_refs), so it depended on the link having
been made in the right place by an account allowed to make it. On the Ames HPC
(2026-11-20) the library was built under the site's database root, the link
went into the installing user's own home, and every Import said the library
had not been built. The built copy is now read directly as well.

Run directly:  python test_fmdv_reflib_lookup.py
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_ENV_KEYS = ("VSNP_GUI_SITE_ROOT", "BDTOOLS_SITE_ROOT", "BDTOOLS_TOOLS_ROOT", "BDTOOLS_DB_ROOT",
             "VSNP_GUI_SHARED_PROJECTS_ROOT", "XDG_CONFIG_HOME", "HOME")


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label}")


def load(site: Path, home: Path, db_root=None):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    os.environ["VSNP_GUI_SITE_ROOT"] = str(site)
    os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
    os.environ["HOME"] = str(home)
    if db_root is not None:
        os.environ["BDTOOLS_DB_ROOT"] = str(db_root)
    import config
    return importlib.reload(config)


def build(lib: Path) -> Path:
    lib.mkdir(parents=True)
    (lib / "lineages.json").write_text("{}")
    return lib


def main() -> int:
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp).resolve()
            site, home, dbs = t / "site", t / "home", t / "site" / "databases"
            site.mkdir(); home.mkdir()

            print("nothing built anywhere")
            c = load(site, home, dbs)
            assert_eq(c.fmdv_reflib_default(), home / "fmdv_refs", "falls back to ~/fmdv_refs")
            assert_eq(c.fmdv_reflib_candidates(),
                      [site / "refs" / "fmdv", dbs / "fmdv" / "fmdv_refs", home / "fmdv_refs"],
                      "and says every place it looked, in order")

            print("built under the database root, never linked into the site (Ames)")
            built = build(dbs / "fmdv" / "fmdv_refs")
            c = load(site, home, dbs)
            assert_eq(c.fmdv_reflib_default(), built, "the built copy is read directly")

            print("the same with no BDTOOLS_DB_ROOT exported: <site>/databases")
            c = load(site, home)
            assert_eq(c.fmdv_reflib_default(), built, "found under <site>/databases")

            print("a database root somewhere else entirely")
            other = build(t / "elsewhere" / "fmdv" / "fmdv_refs")
            c = load(site, home, t / "elsewhere")
            assert_eq(c.fmdv_reflib_default(), other, "the exported database root is the one read")

            print("the site link, when there is one, still comes first")
            linked = build(site / "refs" / "fmdv")
            c = load(site, home, dbs)
            assert_eq(c.fmdv_reflib_default(), linked, "<site>/refs/fmdv wins")

            print("a user's own fmdv_reflib wins over all of them")
            assert_eq(c.fmdv_reflib({"fmdv_reflib": str(t / "mine")}), t / "mine", "config.json's own path")
            assert_eq(c.fmdv_reflib({}), linked, "and without one, the default")
        print("test_fmdv_reflib_lookup: all checks passed")
        return 0
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    sys.exit(main())
