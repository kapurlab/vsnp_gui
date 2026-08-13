"""Unit tests for refs.sanitize_upstream_paths.

The existence guard and the restore pass exist for one real deployment shape:
the upstream author's shipped registry paths are ALSO real directories on the
USDA Ames HPC, where /project/mycobacteria_brucella/mycobacterium/
vsnp_dependencies genuinely holds the reference folders. Removing it there
broke vsnp3 -t resolution for every reference under it.

Run directly:  python test_refs_sanitize.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import refs


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(condition, label):
    if not condition:
        raise AssertionError(f"{label}: expected truthy, got falsy")
    print(f"  OK  {label}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="refs_sanitize_"))
    orig_upstream = refs.UPSTREAM_SHIPPED_PATHS
    try:
        vsnp3 = tmp / "vsnp3"
        deps = vsnp3 / "dependencies"
        deps.mkdir(parents=True)
        rop = deps / "reference_options_paths.txt"
        marker = deps / ".upstream_paths_removed"

        # Stand-ins for the shipped paths: one that exists on "this machine"
        # (the Ames case) and one that doesn't (every other deployment).
        real_upstream = tmp / "project" / "mycobacterium" / "vsnp_dependencies"
        real_upstream.mkdir(parents=True)
        missing_upstream = str(tmp / "no" / "such" / "vsnp_dependencies")
        refs.UPSTREAM_SHIPPED_PATHS = frozenset(
            {str(real_upstream), missing_upstream})
        user_added = str(tmp / "user_added_refs")

        # ------- fresh install: keep what exists, remove what doesn't -------
        print("\n[fresh install sanitize]")
        rop.write_text(
            f"{real_upstream}\n{missing_upstream}\n{user_added}\n",
            encoding="utf-8")
        removed = refs.sanitize_upstream_paths(vsnp3)
        assert_eq(removed, [missing_upstream], "removed only the missing path")
        left = refs.get_reference_paths(vsnp3)
        assert_true(str(real_upstream) in left,
                    "existing upstream path kept (Ames case)")
        assert_true(user_added in left, "user-added path untouched")
        assert_true(missing_upstream not in left, "missing path gone")
        mtext = marker.read_text(encoding="utf-8")
        assert_true("restored: (not applicable)" in mtext,
                    "fresh-install marker disables the repair pass")
        assert_eq(refs.sanitize_upstream_paths(vsnp3), [],
                  "second start is a no-op")

        # ------- install sanitized by the OLD unconditional code -------
        print("\n[restore pass for old-style sanitized install]")
        rop.write_text(f"{user_added}\n", encoding="utf-8")
        marker.write_text(
            "reference_options_paths.txt was checked for upstream-shipped "
            "author paths at first backend start.\n"
            f"removed: {real_upstream}, {missing_upstream}\n",
            encoding="utf-8")
        refs.sanitize_upstream_paths(vsnp3)
        left = refs.get_reference_paths(vsnp3)
        assert_true(str(real_upstream) in left,
                    "existing removed path restored")
        assert_true(missing_upstream not in left,
                    "missing removed path stays removed")
        assert_true(f"restored: {real_upstream}" in marker.read_text(encoding="utf-8"),
                    "marker records the restore")

        # The restore is once-per-install: delete the path from the registry
        # again (a deliberate user choice) and confirm no re-add on start.
        rop.write_text(f"{user_added}\n", encoding="utf-8")
        refs.sanitize_upstream_paths(vsnp3)
        assert_true(str(real_upstream) not in refs.get_reference_paths(vsnp3),
                    "user's later removal is respected")

        # ------- marker with removed: (none) -------
        print("\n[old marker with nothing removed]")
        marker.write_text(
            "reference_options_paths.txt was checked for upstream-shipped "
            "author paths at first backend start.\nremoved: (none)\n",
            encoding="utf-8")
        refs.sanitize_upstream_paths(vsnp3)
        assert_true("restored: (none)" in marker.read_text(encoding="utf-8"),
                    "repair pass runs once and records (none)")

        print("\nAll refs sanitize tests passed.")
        return 0
    finally:
        refs.UPSTREAM_SHIPPED_PATHS = orig_upstream
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
