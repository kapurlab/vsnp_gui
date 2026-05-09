#!/usr/bin/env python3
"""
T-21 Phase 2 — Migrate Mac projects to wgs3 per the edited TSV manifest.

Runs on the Mac. For each row marked KEEP in the manifest:
  1. SSHes wgs3, runs kapurlab-setup-project.sh <target-name> <admin-user>
     to create the proj-<name> group, /srv/kapurlab/projects/<name>/
     (mode 2770), XFS quota, audit ledger, project.json.
  2. rsyncs the Mac source dir → wgs3:/srv/kapurlab/projects/<target-name>/
     with --partial --append-verify so it's resumable.
  3. Verifies the byte count and file count match.
  4. Appends one line to wgs3:/srv/kapurlab/audit/t21-migration.jsonl.

Defaults to dry-run. Pass --execute to actually move data.

Names are lowercased to satisfy the setup script's regex
(^[a-z][a-z0-9_-]{1,30}$). Use --rename-tsv to override that mapping
on a per-row basis.

Usage
-----
    # Dry-run (default — prints the plan, runs no commands on wgs3)
    python3 t21-mac-migrate.py /tmp/t21-manifest.tsv

    # Real run
    python3 t21-mac-migrate.py /tmp/t21-manifest.tsv --execute

    # With explicit admin user (default: vxk1)
    python3 t21-mac-migrate.py /tmp/t21-manifest.tsv --execute --admin vxk1

    # With explicit ssh target
    python3 t21-mac-migrate.py /tmp/t21-manifest.tsv --execute --host wgs3
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

DEFAULT_HOST = "wgs3"                          # ssh target
DEFAULT_REMOTE_USER = "vxk1"                   # for ssh + the kapurlab-admins group
DEFAULT_TARGET_ROOT = "/srv/kapurlab/projects"
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")


def lower_name(s: str) -> str:
    """Lowercase + replace spaces with _ to satisfy NAME_RE."""
    out = s.strip().lower().replace(" ", "_")
    return out


def read_manifest(path: Path) -> List[Dict[str, str]]:
    rows = []
    with path.open() as f:
        # Sniff: prefer tab delimiter; fall back to comma if not present.
        first = f.readline()
        f.seek(0)
        delim = "\t" if "\t" in first else ","
        reader = csv.DictReader(f, delimiter=delim)
        for r in reader:
            # Normalize whitespace in values
            rows.append({k: (v or "").strip() for k, v in r.items()})
    return rows


def plan(rows: List[Dict[str, str]]) -> List[Tuple[Dict[str, str], str]]:
    """Return [(row, target_name)] for KEEP rows; warn on bad names; skip others."""
    keep: List[Tuple[Dict[str, str], str]] = []
    for r in rows:
        decision = r.get("decision", "").upper()
        name = r.get("project", "")
        if decision != "KEEP":
            continue
        target = lower_name(name)
        if not NAME_RE.match(target):
            print(f"[skip] '{name}' → '{target}' fails NAME_RE; rename manually and re-run", file=sys.stderr)
            continue
        keep.append((r, target))
    return keep


def total_bytes(rows: List[Tuple[Dict[str, str], str]]) -> int:
    n = 0
    for r, _ in rows:
        try:
            n += int(r.get("size_bytes") or "0")
        except ValueError:
            pass
    return n


def human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def run(cmd: List[str], dry: bool = False, **kw) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(shlex.quote(c) for c in cmd)}")
    if dry:
        return subprocess.CompletedProcess(cmd, 0, b"", b"")
    return subprocess.run(cmd, **kw)


def ssh(host: str, remote_cmd: str, dry: bool = False, **kw) -> subprocess.CompletedProcess:
    return run(["ssh", host, remote_cmd], dry=dry, **kw)


def provision_remote(host: str, target: str, admin: str, dry: bool) -> bool:
    rc = ssh(host, f"sudo /usr/local/sbin/kapurlab-setup-project.sh {target} {admin}", dry=dry)
    return rc.returncode == 0


def remote_count_bytes(host: str, target: str) -> int:
    out = subprocess.run(
        ["ssh", host, f"sudo du -sb /srv/kapurlab/projects/{target}/ 2>/dev/null | awk '{{print $1}}'"],
        capture_output=True, text=True
    )
    try:
        return int((out.stdout or "0").strip().split()[0])
    except (ValueError, IndexError):
        return 0


def remote_count_files(host: str, target: str) -> int:
    out = subprocess.run(
        ["ssh", host, f"sudo find /srv/kapurlab/projects/{target}/ -type f 2>/dev/null | wc -l"],
        capture_output=True, text=True
    )
    try:
        return int((out.stdout or "0").strip())
    except ValueError:
        return 0


def local_count_bytes(path: Path) -> int:
    n = 0
    for root, _, files in os.walk(path, onerror=lambda _e: None):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n


def local_count_files(path: Path) -> int:
    n = 0
    for _root, _, files in os.walk(path, onerror=lambda _e: None):
        n += len(files)
    return n


def append_audit(host: str, entry: Dict, dry: bool) -> None:
    line = json.dumps(entry, separators=(",", ":")) + "\n"
    cmd = f"cat >> /srv/kapurlab/audit/t21-migration.jsonl"
    if dry:
        print(f"  $ ssh {host} 'sudo tee -a /srv/kapurlab/audit/t21-migration.jsonl' <<< {shlex.quote(line)}")
        return
    p = subprocess.Popen(
        ["ssh", host, "sudo tee -a /srv/kapurlab/audit/t21-migration.jsonl >/dev/null"],
        stdin=subprocess.PIPE,
    )
    p.communicate(line.encode("utf-8"))


def migrate_one(host: str, source: Path, target: str, admin: str, dry: bool) -> Dict:
    print(f"\n=== {source.name} → {target} ===")
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.monotonic()

    # 1. Provision remote.
    if not provision_remote(host, target, admin, dry):
        return {"source": str(source), "target": target, "status": "provision_failed", "started_at": started}

    # 2. rsync. Trailing slash on source — copy *contents* into the
    # already-provisioned remote dir (preserving its setgid + group from
    # provisioning step). Excludes typical scratch.
    src = f"{source}/"
    dst = f"{host}:/srv/kapurlab/projects/{target}/"
    rsync_cmd = [
        "rsync", "-aH", "--info=stats", "--partial", "--append-verify",
        "--exclude=.DS_Store", "--exclude=.jobs", "--exclude=__pycache__",
        src, dst,
    ]
    rc = run(rsync_cmd, dry=dry)
    if rc.returncode != 0:
        return {"source": str(source), "target": target, "status": "rsync_failed", "started_at": started}

    # 3. Verify byte/file counts.
    if dry:
        local_b = local_count_bytes(source)
        local_f = local_count_files(source)
        print(f"  local : {local_f} files, {human_bytes(local_b)}")
        print(f"  remote: (skipped in dry-run)")
        return {"source": str(source), "target": target, "status": "dry_run",
                "local_bytes": local_b, "local_files": local_f}

    local_b = local_count_bytes(source)
    local_f = local_count_files(source)
    remote_b = remote_count_bytes(host, target)
    remote_f = remote_count_files(host, target)
    elapsed = time.monotonic() - t0
    ok = local_b == remote_b and local_f == remote_f
    status = "ok" if ok else "size_mismatch"
    print(f"  local : {local_f:>5} files, {human_bytes(local_b)}")
    print(f"  remote: {remote_f:>5} files, {human_bytes(remote_b)} ({status})")
    print(f"  elapsed: {elapsed:.1f}s")

    return {
        "source": str(source),
        "target": target,
        "status": status,
        "started_at": started,
        "elapsed_seconds": round(elapsed, 1),
        "local_bytes": local_b,
        "local_files": local_f,
        "remote_bytes": remote_b,
        "remote_files": remote_f,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("manifest", help="Edited TSV from t21-mac-manifest.py")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"ssh host (default: {DEFAULT_HOST})")
    p.add_argument("--admin", default=DEFAULT_REMOTE_USER,
                   help=f"Initial member added to each proj-* group (default: {DEFAULT_REMOTE_USER})")
    p.add_argument("--execute", action="store_true",
                   help="Actually run. Without this, prints the plan and exits.")
    args = p.parse_args()

    rows = read_manifest(Path(args.manifest))
    keepers = plan(rows)
    if not keepers:
        print("Nothing marked KEEP. Done.")
        return 0

    print(f"\nT-21 Phase 2 — {'EXECUTING' if args.execute else 'DRY RUN'}")
    print(f"  ssh host    : {args.host}")
    print(f"  initial admin: {args.admin}")
    print(f"  destination : {DEFAULT_TARGET_ROOT}/<target>/")
    print(f"  audit log   : /srv/kapurlab/audit/t21-migration.jsonl on {args.host}")
    print(f"\nKEEP rows ({len(keepers)} projects, ~{human_bytes(total_bytes(keepers))} estimated):")
    for r, target in keepers:
        print(f"  {r['project']:<28s} → {target:<28s}  ({r.get('size','?')})")

    if not args.execute:
        print("\nDry run only. Pass --execute to actually migrate.")
        return 0

    print("\n--- starting migration ---")
    audits: List[Dict] = []
    for r, target in keepers:
        result = migrate_one(args.host, Path(r["path"]), target, args.admin, dry=False)
        append_audit(args.host, result, dry=False)
        audits.append(result)

    # Summary
    print("\n=== summary ===")
    ok = sum(1 for a in audits if a["status"] == "ok")
    print(f"  ok           : {ok} / {len(audits)}")
    failures = [a for a in audits if a["status"] != "ok"]
    if failures:
        print(f"  not ok       :")
        for a in failures:
            print(f"    {a['target']}: {a['status']}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
