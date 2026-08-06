"""Unit tests for app.main._serve_path_allowed (pure function).

/api/projects/{p}/serve is what igv.js streams every BAM/FASTA/GFF through, and
its allow-check decides whether a viewer loads at all: igv.js rejects the whole
createBrowser call when one track's resource fails, so a wrongly-refused path
presented as "IGV failed to load: Error accessing resource … status: 400" with an
empty viewer.

It refused a GFF the UI itself had just handed out, on any install with NO shared
reference set. There, install-local builds <site>/refs/vsnp3/reference_options as a
directory of SYMLINKS into ~/.local/share/bdtools/vsnp3-refs/. The old check
resolved the target (following the symlink out) but compared it against the
unresolved root, so the resolved path was outside every root. Installs with a
shared set never hit it: there the root is itself one symlink to the whole shared
dir, so both sides land in the same place — which is why this only ever appeared on
a new user's machine.

Run from anywhere with the per-site conda python:

    <conda>/bin/python backend/app/test_serve_path_allowed.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import _serve_path_allowed, _under  # noqa: E402


def assert_true(cond, label):
    if not cond:
        raise AssertionError(f"{label}: expected allowed, got refused")
    print(f"  OK  {label}")


def assert_false(cond, label):
    if cond:
        raise AssertionError(f"{label}: expected REFUSED, got allowed")
    print(f"  OK  {label}")


def main() -> int:
    print("[_serve_path_allowed]")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        project = root / "projects/test01"
        (project / "step1").mkdir(parents=True)
        (project / "step1/x.bam").write_text("")

        # The layout of a local install with no shared reference set: a private
        # cache of references, and a managed dir of per-reference symlinks.
        cache = root / "bdtools/vsnp3-refs/vSNP_reference_options/Mycobacterium_H37"
        cache.mkdir(parents=True)
        (cache / "NC_000962.gff").write_text("##gff-version 3\n")
        site_refs = root / "bdtools/vsnp3-site/refs/vsnp3/reference_options"
        site_refs.mkdir(parents=True)
        os.symlink(cache, site_refs / "Mycobacterium_H37")
        roots = [project, site_refs]

        assert_true(
            _serve_path_allowed(site_refs / "Mycobacterium_H37/NC_000962.gff", roots),
            "GFF reached through a per-reference symlink (the regression)")
        assert_true(_serve_path_allowed(project / "step1/x.bam", roots),
                    "file inside the project dir")

        # The shared-set layout, which worked before and must keep working: the
        # root is a single symlink to the whole reference collection.
        shared = root / "shared_refs"
        os.symlink(cache.parent, shared)
        assert_true(
            _serve_path_allowed(shared / "Mycobacterium_H37/NC_000962.gff",
                                [project, shared]),
            "GFF under a root that is itself a symlink")

        # Still refused — a laxer check would be a filesystem read primitive.
        assert_false(_serve_path_allowed(Path("/etc/passwd"), roots),
                     "arbitrary absolute path")
        assert_false(
            _serve_path_allowed(site_refs / "../../../../../../etc/passwd", roots),
            "'..' traversal out of a root")
        assert_false(_serve_path_allowed(Path(str(project) + "2") / "x.bam", roots),
                     "sibling directory sharing a name prefix")
        assert_false(_serve_path_allowed(Path("/etc/passwd"), [Path("")]),
                     "an empty root allows nothing")
        assert_false(_serve_path_allowed(Path("/etc/passwd"), []),
                     "no roots allows nothing")

    print("[_under]")
    assert_true(_under(Path("/a/b/c"), Path("/a/b")), "child under parent")
    assert_true(_under(Path("/a/b"), Path("/a/b")), "path is its own root")
    assert_false(_under(Path("/a/bc"), Path("/a/b")), "prefix is not containment")
    assert_false(_under(Path("/a/b"), Path("/")), "'/' is not a usable root")
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
