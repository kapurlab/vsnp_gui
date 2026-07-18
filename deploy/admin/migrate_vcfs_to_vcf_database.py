#!/usr/bin/env python3
"""
migrate_vcfs_to_vcf_database.py — fold the legacy <project>_VCFs/ folder into
step2/vcf_database/.

Older projects kept a cumulative VCF collection in a top-level
``<project>_VCFs/`` folder. The suite now uses a single cumulative store at
``step2/vcf_database/`` (the classic vSNP3 layout). This script moves every VCF
(and symlink) from the legacy folder into ``step2/vcf_database/`` without
overwriting anything already there, then removes the now-empty legacy folder.
It is idempotent and safe to re-run.

Usage:
    # one project
    python migrate_vcfs_to_vcf_database.py /srv/kapurlab/tools/mtbc/dataset_large

    # every project under one or more roots
    python migrate_vcfs_to_vcf_database.py --root /srv/kapurlab/projects --root ~/projects

    # preview only
    python migrate_vcfs_to_vcf_database.py --root /srv/kapurlab/projects --dry-run

Exit codes:
    0 — nothing to do, or migration completed
    1 — at least one project could not be fully migrated (conflicts left in place)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _vcf_db_dir(step2_dir: Path) -> Path:
    """Mirror backend.app.projects.vcf_db_dir: prefer vcf_database, fall back to
    the legacy vcf_source name, default to vcf_database for creation."""
    new = step2_dir / "vcf_database"
    if new.exists():
        return new
    legacy = step2_dir / "vcf_source"
    if legacy.exists():
        return legacy
    return new


def migrate_project(project_dir: Path, dry_run: bool = False) -> tuple[int, int]:
    """Return (moved, conflicts) for one project. A conflict is a legacy file
    whose name already exists in the target — left in place, never overwritten."""
    legacy = project_dir / f"{project_dir.name}_VCFs"
    if not legacy.is_dir():
        return (0, 0)
    target = _vcf_db_dir(project_dir / "step2")
    moved = 0
    conflicts = 0
    entries = [p for p in legacy.iterdir() if not p.name.startswith(".")]
    if not entries:
        # Empty legacy folder — just remove it.
        if not dry_run:
            legacy.rmdir()
        print(f"  {project_dir.name}: removed empty {legacy.name}/")
        return (0, 0)
    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)
    for src in entries:
        dst = target / src.name
        if dst.exists() or dst.is_symlink():
            conflicts += 1
            print(f"  {project_dir.name}: SKIP (already in vcf_database) {src.name}")
            continue
        if dry_run:
            print(f"  {project_dir.name}: would move {src.name} -> step2/{target.name}/")
        else:
            # rename preserves symlinks as-is (the link, not its target).
            src.rename(dst)
            print(f"  {project_dir.name}: moved {src.name} -> step2/{target.name}/")
        moved += 1
    # Remove the legacy folder if it's now empty and there were no conflicts.
    if not dry_run and conflicts == 0:
        remaining = [p for p in legacy.iterdir() if not p.name.startswith(".")]
        if not remaining:
            for hidden in legacy.iterdir():
                hidden.unlink()
            legacy.rmdir()
            print(f"  {project_dir.name}: removed {legacy.name}/")
    return (moved, conflicts)


def _iter_projects(root: Path):
    for p in sorted(root.iterdir()):
        if not p.is_dir() or p.name.startswith(".") or p.name == "projects_archive":
            continue
        yield p


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("projects", nargs="*", type=Path, help="one or more project directories")
    ap.add_argument("--root", action="append", type=Path, default=[], help="a projects root to sweep (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="print what would happen, change nothing")
    args = ap.parse_args()

    targets: list[Path] = list(args.projects)
    for root in args.root:
        root = root.expanduser()
        if root.is_dir():
            targets.extend(_iter_projects(root))
        else:
            print(f"root not found: {root}", file=sys.stderr)

    if not targets:
        ap.error("give at least one project dir or --root")

    total_moved = 0
    total_conflicts = 0
    for project_dir in targets:
        project_dir = project_dir.expanduser()
        if not project_dir.is_dir():
            print(f"skip (not a dir): {project_dir}", file=sys.stderr)
            continue
        moved, conflicts = migrate_project(project_dir, dry_run=args.dry_run)
        total_moved += moved
        total_conflicts += conflicts

    verb = "would move" if args.dry_run else "moved"
    print(f"\n{verb} {total_moved} VCF(s); {total_conflicts} conflict(s) left in place.")
    return 1 if total_conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main())
