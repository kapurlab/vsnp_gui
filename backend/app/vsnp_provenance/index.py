"""
vsnp_provenance.index
=====================

SQLite indexer for vsnp_gui run provenance metadata (T-07).

The per-project `run_metadata.json` and `_provenance/pipeline_runs/*.json`
files are the source of truth. This module maintains a queryable index over
them at `/srv/kapurlab/audit/runs.sqlite`.

Design notes
------------
- Idempotent. Re-indexing a record that's already present is a no-op (or an
  update if the underlying file changed). Schema migrations are versioned in
  a `schema_meta` table.
- File-modification-driven. We track `(metadata_path, file_mtime, file_sha256)`
  per indexed record; on incremental indexing we only re-parse files whose
  mtime has changed since last index. This makes nightly cron and on-demand
  re-indexing cheap.
- Single-writer. SQLite WAL handles concurrent readers. Backend run finalize
  triggers a one-shot index update (a single INSERT/UPDATE per finalized run);
  bulk indexing is done by a separate process that holds the write lock.
- Concurrency for finalize: WAL + immediate transaction + retry on
  SQLITE_BUSY. Backend should not block a run finalize on index contention.
- Project renames: tracked in `project_renames` table. Queries that need
  current-name resolution join through `project_renames` ordered by time;
  run records keep their snapshot-at-finalize project_name.

Usage
-----
    from vsnp_provenance.index import Indexer

    idx = Indexer("/srv/kapurlab/audit/runs.sqlite")
    idx.init_schema()                     # idempotent
    idx.upsert_run("/srv/kapurlab/projects/foo/step2/run_metadata.json")
    idx.crawl_project("/srv/kapurlab/projects/foo")
    idx.crawl_root("/srv/kapurlab/projects")
    idx.mark_orphaned_running(max_runtime_hours=48)
    idx.export_jsonl("/srv/kapurlab/audit/runs.sqlite.jsonl")

Or via the CLI:

    python -m vsnp_provenance.index init --db /srv/kapurlab/audit/runs.sqlite
    python -m vsnp_provenance.index crawl /srv/kapurlab/projects --db ...
    python -m vsnp_provenance.index gc --db ... --max-hours 48
    python -m vsnp_provenance.index export --db ... --out runs.jsonl
    python -m vsnp_provenance.index query --db ... \\
        --reference mtbc0_v1.1 --since 2026-01-01 --step step2
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import sqlite3
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import (
    MalformedRecord,
    PipelineRunV2,
    RunMetadataV2,
    RunStatus,
    Step,
    UnsupportedSchemaVersion,
    load,
    load_pipeline_run,
)

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 1

DDL = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
      run_id              TEXT PRIMARY KEY,
      pipeline_run_id     TEXT,
      step                TEXT NOT NULL,
      project_path        TEXT NOT NULL,
      project_name        TEXT NOT NULL,
      user                TEXT NOT NULL,
      ood_session_id      TEXT,
      reference_name      TEXT,
      reference_folder_manifest_sha256 TEXT,
      vsnp3_version       TEXT,
      vsnp_gui_git_sha    TEXT,
      environment_hash    TEXT,
      started_at          TEXT NOT NULL,
      finished_at         TEXT,
      duration_seconds    REAL,
      status              TEXT NOT NULL,
      exit_code           INTEGER,
      metadata_path       TEXT NOT NULL,
      metadata_mtime      TEXT NOT NULL,
      metadata_sha256     TEXT NOT NULL,
      schema_version      INTEGER NOT NULL,
      indexed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_reference   ON runs(reference_name);",
    "CREATE INDEX IF NOT EXISTS idx_runs_started     ON runs(started_at);",
    "CREATE INDEX IF NOT EXISTS idx_runs_user        ON runs(user);",
    "CREATE INDEX IF NOT EXISTS idx_runs_pipeline    ON runs(pipeline_run_id);",
    "CREATE INDEX IF NOT EXISTS idx_runs_step_status ON runs(step, status);",
    "CREATE INDEX IF NOT EXISTS idx_runs_metadata    ON runs(metadata_path);",
    """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
      pipeline_run_id     TEXT PRIMARY KEY,
      project_path        TEXT NOT NULL,
      project_name        TEXT NOT NULL,
      label               TEXT,
      created_at          TEXT NOT NULL,
      created_by          TEXT NOT NULL,
      metadata_path       TEXT NOT NULL,
      metadata_mtime      TEXT NOT NULL,
      metadata_sha256     TEXT NOT NULL,
      schema_version      INTEGER NOT NULL,
      indexed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_plruns_project ON pipeline_runs(project_path);",
    "CREATE INDEX IF NOT EXISTS idx_plruns_created ON pipeline_runs(created_at);",
    """
    CREATE TABLE IF NOT EXISTS project_renames (
      rename_id           INTEGER PRIMARY KEY AUTOINCREMENT,
      old_path            TEXT NOT NULL,
      new_path            TEXT NOT NULL,
      renamed_at          TEXT NOT NULL,
      renamed_by          TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_renames_old ON project_renames(old_path);",
    "CREATE INDEX IF NOT EXISTS idx_renames_new ON project_renames(new_path);",
    """
    CREATE TABLE IF NOT EXISTS index_errors (
      error_id            INTEGER PRIMARY KEY AUTOINCREMENT,
      metadata_path       TEXT NOT NULL,
      error_kind          TEXT NOT NULL,
      error_message       TEXT NOT NULL,
      seen_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    );
    """,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _file_sha256(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _project_path_for(metadata_path: Path) -> Path:
    """Walk up from a run_metadata.json to its project root.

    Project root is identified by the presence of an `audit/` subdir or by
    being the directory above `step1/` or `step2/`. Falls back to two parents
    up from the metadata file (works for both per-sample step1 and step2).
    """
    p = metadata_path.resolve()
    # Walk up at most 6 levels looking for the marker
    for parent in [p.parent] + list(p.parents)[:6]:
        if (parent / "audit").is_dir():
            return parent
        if (parent / "step1").is_dir() or (parent / "step2").is_dir():
            # If this is itself a step dir, go up one more
            if parent.name in {"step1", "step2"}:
                return parent.parent
            return parent
    # Fallback: assume <project>/<step>/<sample>/run_metadata.json
    if p.parent.parent.name in {"step1", "step2"}:
        return p.parent.parent.parent
    return p.parent.parent


def _environment_hash(rec: RunMetadataV2) -> str | None:
    """Stable hash combining conda yaml + pip freeze + system tool versions.

    Returns None if no environment data captured (records from before that
    field landed). Otherwise SHA-256 of a deterministic serialization.
    """
    env = rec.environment
    parts: list[str] = []
    if env.conda_env_yaml_sha256:
        parts.append(f"conda={env.conda_env_yaml_sha256}")
    if env.pip_freeze_sha256:
        parts.append(f"pip={env.pip_freeze_sha256}")
    sp = env.system_packages
    sp_items = []
    for tool in ("samtools", "bcftools", "bwa", "mafft", "raxml", "iqtree"):
        v = getattr(sp, tool, None)
        if v is not None:
            sp_items.append(f"{tool}={v}")
    if sp_items:
        parts.append("sys[" + ",".join(sp_items) + "]")
    if not parts:
        return None
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------


@dataclass
class IndexerStats:
    runs_inserted: int = 0
    runs_updated: int = 0
    runs_unchanged: int = 0
    runs_skipped_non_terminal: int = 0  # running records aren't indexed
    pipeline_runs_inserted: int = 0
    pipeline_runs_updated: int = 0
    pipeline_runs_unchanged: int = 0
    errors: int = 0
    error_paths: list[str] = field(default_factory=list)


class Indexer:
    """SQLite indexer over per-project run_metadata files."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        retry_attempts: int = 3,
        retry_backoff_s: float = 0.25,
    ):
        self.db_path = Path(db_path)
        self._busy_timeout_ms = busy_timeout_ms
        self._retry_attempts = retry_attempts
        self._retry_backoff_s = retry_backoff_s

    # ------ connection management ------

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    def _retrying(self, fn, *args, **kwargs):
        last: Exception | None = None
        for attempt in range(self._retry_attempts):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    last = e
                    time.sleep(self._retry_backoff_s * (attempt + 1))
                    continue
                raise
        if last:
            raise last

    # ------ schema ------

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for ddl in DDL:
                conn.execute(ddl)
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                ("index_schema_version", str(INDEX_SCHEMA_VERSION)),
            )
            conn.execute("COMMIT")

    def schema_version(self) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'index_schema_version'"
            ).fetchone()
            return int(row["value"]) if row else None

    # ------ run upsert ------

    def upsert_run(self, metadata_path: str | Path) -> str:
        """Insert or update a run record. Returns 'inserted', 'updated', or 'unchanged'."""
        path = Path(metadata_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)

        try:
            rec = load(path)
        except (MalformedRecord, UnsupportedSchemaVersion) as e:
            self._log_error(path, type(e).__name__, str(e))
            raise

        # Only index terminal runs. Indexing 'running' records would invite
        # stale entries; the finalize step is what triggers the index update.
        if rec.status == RunStatus.RUNNING:
            logger.debug("skipping non-terminal run at %s", path)
            return "skipped"

        mtime_iso = _file_mtime_iso(path)
        sha = _file_sha256(path)
        project_path = _project_path_for(path)
        project_name = project_path.name

        return self._retrying(self._upsert_run_row, rec, path, mtime_iso, sha,
                              project_path, project_name)

    def _upsert_run_row(
        self,
        rec: RunMetadataV2,
        path: Path,
        mtime_iso: str,
        sha: str,
        project_path: Path,
        project_name: str,
    ) -> str:
        env_hash = _environment_hash(rec)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT metadata_sha256 FROM runs WHERE run_id = ?",
                (rec.run_id,),
            ).fetchone()

            if existing and existing["metadata_sha256"] == sha:
                conn.execute("COMMIT")
                return "unchanged"

            params = {
                "run_id": rec.run_id,
                "pipeline_run_id": rec.pipeline_run_id,
                "step": rec.step.value,
                "project_path": str(project_path),
                "project_name": project_name,
                "user": rec.actor.user,
                "ood_session_id": rec.actor.ood_session_id,
                "reference_name": rec.reference.name,
                "reference_folder_manifest_sha256": rec.reference.folder_manifest_sha256,
                "vsnp3_version": rec.vsnp3.version,
                "vsnp_gui_git_sha": rec.vsnp_gui.git_sha,
                "environment_hash": env_hash,
                "started_at": rec.started_at.isoformat(),
                "finished_at": rec.finished_at.isoformat() if rec.finished_at else None,
                "duration_seconds": rec.duration_seconds,
                "status": rec.status.value,
                "exit_code": rec.exit_code,
                "metadata_path": str(path),
                "metadata_mtime": mtime_iso,
                "metadata_sha256": sha,
                "schema_version": 2,
            }

            if existing:
                conn.execute(
                    """
                    UPDATE runs SET
                      pipeline_run_id = :pipeline_run_id,
                      step = :step,
                      project_path = :project_path,
                      project_name = :project_name,
                      user = :user,
                      ood_session_id = :ood_session_id,
                      reference_name = :reference_name,
                      reference_folder_manifest_sha256 = :reference_folder_manifest_sha256,
                      vsnp3_version = :vsnp3_version,
                      vsnp_gui_git_sha = :vsnp_gui_git_sha,
                      environment_hash = :environment_hash,
                      started_at = :started_at,
                      finished_at = :finished_at,
                      duration_seconds = :duration_seconds,
                      status = :status,
                      exit_code = :exit_code,
                      metadata_path = :metadata_path,
                      metadata_mtime = :metadata_mtime,
                      metadata_sha256 = :metadata_sha256,
                      schema_version = :schema_version,
                      indexed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE run_id = :run_id
                    """,
                    params,
                )
                conn.execute("COMMIT")
                return "updated"
            else:
                conn.execute(
                    """
                    INSERT INTO runs (
                      run_id, pipeline_run_id, step, project_path, project_name,
                      user, ood_session_id, reference_name,
                      reference_folder_manifest_sha256, vsnp3_version,
                      vsnp_gui_git_sha, environment_hash, started_at,
                      finished_at, duration_seconds, status, exit_code,
                      metadata_path, metadata_mtime, metadata_sha256,
                      schema_version
                    ) VALUES (
                      :run_id, :pipeline_run_id, :step, :project_path, :project_name,
                      :user, :ood_session_id, :reference_name,
                      :reference_folder_manifest_sha256, :vsnp3_version,
                      :vsnp_gui_git_sha, :environment_hash, :started_at,
                      :finished_at, :duration_seconds, :status, :exit_code,
                      :metadata_path, :metadata_mtime, :metadata_sha256,
                      :schema_version
                    )
                    """,
                    params,
                )
                conn.execute("COMMIT")
                return "inserted"

    # ------ pipeline-run upsert ------

    def upsert_pipeline_run(self, metadata_path: str | Path) -> str:
        path = Path(metadata_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)

        try:
            rec = load_pipeline_run(path)
        except (MalformedRecord, UnsupportedSchemaVersion) as e:
            self._log_error(path, type(e).__name__, str(e))
            raise

        mtime_iso = _file_mtime_iso(path)
        sha = _file_sha256(path)
        project_path = _project_path_for(path)
        project_name = project_path.name

        return self._retrying(self._upsert_pipeline_row, rec, path, mtime_iso,
                              sha, project_path, project_name)

    def _upsert_pipeline_row(
        self,
        rec: PipelineRunV2,
        path: Path,
        mtime_iso: str,
        sha: str,
        project_path: Path,
        project_name: str,
    ) -> str:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT metadata_sha256 FROM pipeline_runs WHERE pipeline_run_id = ?",
                (rec.pipeline_run_id,),
            ).fetchone()

            if existing and existing["metadata_sha256"] == sha:
                conn.execute("COMMIT")
                return "unchanged"

            params = {
                "pipeline_run_id": rec.pipeline_run_id,
                "project_path": str(project_path),
                "project_name": project_name,
                "label": rec.label,
                "created_at": rec.created_at.isoformat(),
                "created_by": rec.created_by,
                "metadata_path": str(path),
                "metadata_mtime": mtime_iso,
                "metadata_sha256": sha,
                "schema_version": 2,
            }

            if existing:
                conn.execute(
                    """
                    UPDATE pipeline_runs SET
                      project_path = :project_path,
                      project_name = :project_name,
                      label = :label,
                      created_at = :created_at,
                      created_by = :created_by,
                      metadata_path = :metadata_path,
                      metadata_mtime = :metadata_mtime,
                      metadata_sha256 = :metadata_sha256,
                      schema_version = :schema_version,
                      indexed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                    WHERE pipeline_run_id = :pipeline_run_id
                    """,
                    params,
                )
                conn.execute("COMMIT")
                return "updated"
            else:
                conn.execute(
                    """
                    INSERT INTO pipeline_runs (
                      pipeline_run_id, project_path, project_name, label,
                      created_at, created_by, metadata_path, metadata_mtime,
                      metadata_sha256, schema_version
                    ) VALUES (
                      :pipeline_run_id, :project_path, :project_name, :label,
                      :created_at, :created_by, :metadata_path, :metadata_mtime,
                      :metadata_sha256, :schema_version
                    )
                    """,
                    params,
                )
                conn.execute("COMMIT")
                return "inserted"

    # ------ crawling ------

    def crawl_project(self, project_path: str | Path) -> IndexerStats:
        root = Path(project_path).resolve()
        stats = IndexerStats()

        for p in self._iter_run_metadata_paths(root):
            self._upsert_with_stats(self.upsert_run, p, stats, kind="run")

        pl_dir = root / "_provenance" / "pipeline_runs"
        if pl_dir.is_dir():
            for p in sorted(pl_dir.glob("*.json")):
                self._upsert_with_stats(self.upsert_pipeline_run, p, stats, kind="pipeline")

        return stats

    def crawl_root(self, projects_root: str | Path) -> IndexerStats:
        root = Path(projects_root).resolve()
        total = IndexerStats()
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            s = self.crawl_project(child)
            total.runs_inserted += s.runs_inserted
            total.runs_updated += s.runs_updated
            total.runs_unchanged += s.runs_unchanged
            total.runs_skipped_non_terminal += s.runs_skipped_non_terminal
            total.pipeline_runs_inserted += s.pipeline_runs_inserted
            total.pipeline_runs_updated += s.pipeline_runs_updated
            total.pipeline_runs_unchanged += s.pipeline_runs_unchanged
            total.errors += s.errors
            total.error_paths.extend(s.error_paths)
        return total

    def _iter_run_metadata_paths(self, project_root: Path) -> Iterator[Path]:
        for sub in ("step1", "step2"):
            d = project_root / sub
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("run_metadata.json")):
                yield p

    def _upsert_with_stats(self, fn, path: Path, stats: IndexerStats, *, kind: str) -> None:
        try:
            result = fn(path)
        except (MalformedRecord, UnsupportedSchemaVersion, FileNotFoundError) as e:
            stats.errors += 1
            stats.error_paths.append(str(path))
            warnings.warn(f"{path}: {type(e).__name__}: {e}", stacklevel=2)
            return
        if kind == "run":
            if result == "inserted":
                stats.runs_inserted += 1
            elif result == "updated":
                stats.runs_updated += 1
            elif result == "skipped":
                stats.runs_skipped_non_terminal += 1
            else:
                stats.runs_unchanged += 1
        else:
            if result == "inserted":
                stats.pipeline_runs_inserted += 1
            elif result == "updated":
                stats.pipeline_runs_updated += 1
            else:
                stats.pipeline_runs_unchanged += 1

    # ------ janitor ------

    def mark_orphaned_running(self, max_runtime_hours: float = 48.0) -> int:
        """Find indexed records that were running and have exceeded max runtime.

        Note: this only operates on rows already in the index. Truly stuck
        runs whose run_metadata.json was never finalized live on disk and
        need to be discovered by a separate crawl that includes 'running'
        records (not done by default; see upsert_run).

        For the disk-side janitor, see the standalone `gc_running` function
        below which scans for stuck on-disk records and rewrites them as
        unknown_terminated.
        """
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=max_runtime_hours)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """
                UPDATE runs SET status = 'unknown_terminated',
                                indexed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                WHERE status = 'running' AND started_at < ?
                """,
                (cutoff,),
            )
            conn.execute("COMMIT")
            return cur.rowcount

    # ------ project renames ------

    def record_rename(self, old_path: str, new_path: str, renamed_by: str) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO project_renames (old_path, new_path, renamed_at, renamed_by)
                VALUES (?, ?, ?, ?)
                """,
                (old_path, new_path, _utcnow_iso(), renamed_by),
            )
            conn.execute("COMMIT")

    def resolve_current_path(self, historical_path: str) -> str:
        """Walk the rename chain forward from a historical project path."""
        with self._connect() as conn:
            current = historical_path
            seen = {current}
            while True:
                row = conn.execute(
                    """
                    SELECT new_path FROM project_renames
                    WHERE old_path = ?
                    ORDER BY renamed_at ASC LIMIT 1
                    """,
                    (current,),
                ).fetchone()
                if not row:
                    return current
                nxt = row["new_path"]
                if nxt in seen:
                    raise RuntimeError(f"rename cycle detected at {nxt}")
                seen.add(nxt)
                current = nxt

    # ------ queries ------

    def query_runs(
        self,
        *,
        reference_name: str | None = None,
        step: str | None = None,
        status: str | None = None,
        user: str | None = None,
        since: str | None = None,
        until: str | None = None,
        pipeline_run_id: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if reference_name:
            clauses.append("reference_name = ?")
            params.append(reference_name)
        if step:
            clauses.append("step = ?")
            params.append(step)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if user:
            clauses.append("user = ?")
            params.append(user)
        if since:
            clauses.append("started_at >= ?")
            params.append(since)
        if until:
            clauses.append("started_at < ?")
            params.append(until)
        if pipeline_run_id:
            clauses.append("pipeline_run_id = ?")
            params.append(pipeline_run_id)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM runs {where} ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            out: dict[str, Any] = {}
            out["total_runs"] = conn.execute("SELECT COUNT(*) c FROM runs").fetchone()["c"]
            out["total_pipeline_runs"] = conn.execute(
                "SELECT COUNT(*) c FROM pipeline_runs"
            ).fetchone()["c"]
            out["by_status"] = {
                r["status"]: r["c"]
                for r in conn.execute(
                    "SELECT status, COUNT(*) c FROM runs GROUP BY status"
                ).fetchall()
            }
            out["by_step"] = {
                r["step"]: r["c"]
                for r in conn.execute(
                    "SELECT step, COUNT(*) c FROM runs GROUP BY step"
                ).fetchall()
            }
            out["by_reference"] = {
                r["reference_name"]: r["c"]
                for r in conn.execute(
                    """SELECT reference_name, COUNT(*) c FROM runs
                       WHERE reference_name IS NOT NULL
                       GROUP BY reference_name ORDER BY c DESC"""
                ).fetchall()
            }
            return out

    # ------ export ------

    def export_jsonl(self, out_path: str | Path) -> int:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with self._connect() as conn, out.open("w") as f:
            for row in conn.execute("SELECT * FROM runs ORDER BY started_at"):
                f.write(json.dumps(dict(row)) + "\n")
                n += 1
        return n

    # ------ error logging ------

    def _log_error(self, path: Path, kind: str, message: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO index_errors (metadata_path, error_kind, error_message)
                    VALUES (?, ?, ?)
                    """,
                    (str(path), kind, message),
                )
        except Exception:
            logger.exception("failed to log index error for %s", path)


# ---------------------------------------------------------------------------
# Disk-side janitor (orphaned 'running' files)
# ---------------------------------------------------------------------------


def gc_running(
    projects_root: str | Path,
    *,
    max_runtime_hours: float = 48.0,
    dry_run: bool = False,
) -> list[Path]:
    """Find run_metadata.json files stuck in status='running' beyond max runtime
    and rewrite them in-place as status='unknown_terminated'.

    Returns the list of files that were (or would have been) rewritten.

    This is the disk-side counterpart to Indexer.mark_orphaned_running,
    which only operates on the index. The disk rewrite makes the source of
    truth consistent and ensures subsequent indexing reflects the terminal
    state. Backend is responsible for not re-finalizing a run after janitor
    has marked it terminated; the run_id collision check at dispatch handles
    the genuine restart case.
    """
    import os
    import tempfile

    root = Path(projects_root).resolve()
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=max_runtime_hours)
    rewritten: list[Path] = []

    for p in root.rglob("run_metadata.json"):
        try:
            with p.open() as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("status") != "running":
            continue
        started = data.get("started_at")
        if not started:
            continue
        try:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            continue
        if started_dt >= cutoff:
            continue

        rewritten.append(p)
        if dry_run:
            continue

        data["status"] = "unknown_terminated"
        data["finished_at"] = _utcnow_iso()
        data.setdefault("janitor_notes", []).append(
            f"marked unknown_terminated by gc_running at {_utcnow_iso()} "
            f"(exceeded max_runtime_hours={max_runtime_hours})"
        )
        # Atomic rewrite
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".run_metadata.", dir=str(p.parent))
        try:
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, p)
        except Exception:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)
            raise

    return rewritten


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="vsnp_provenance.index",
        description="SQLite indexer for vsnp_gui run provenance",
    )
    ap.add_argument("--db", required=True, help="path to runs.sqlite")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create or upgrade the index schema")

    cr = sub.add_parser("crawl", help="index every run_metadata.json under a path")
    cr.add_argument("path", help="project root or projects parent dir")
    cr.add_argument(
        "--single-project",
        action="store_true",
        help="treat path as a single project; without this, path is a directory of projects",
    )

    gc = sub.add_parser("gc", help="mark orphaned running runs as unknown_terminated")
    gc.add_argument("--max-hours", type=float, default=48.0)
    gc.add_argument(
        "--projects-root",
        help="if provided, also rewrite stuck on-disk run_metadata.json files",
    )
    gc.add_argument("--dry-run", action="store_true")

    ex = sub.add_parser("export", help="export runs table to JSONL")
    ex.add_argument("--out", required=True)

    q = sub.add_parser("query", help="query runs")
    q.add_argument("--reference")
    q.add_argument("--step", choices=["step1", "step2"])
    q.add_argument("--status", choices=["ok", "failed", "running", "unknown_terminated"])
    q.add_argument("--user")
    q.add_argument("--since")
    q.add_argument("--until")
    q.add_argument("--pipeline-run-id")
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--format", choices=["table", "json", "jsonl"], default="table")

    sub.add_parser("stats", help="print index statistics")

    rn = sub.add_parser("rename", help="record a project rename")
    rn.add_argument("--old", required=True)
    rn.add_argument("--new", required=True)
    rn.add_argument("--by", required=True)

    return ap


def _print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("(no rows)")
        return
    cols = ["started_at", "step", "status", "user", "reference_name",
            "vsnp3_version", "project_name", "run_id"]
    widths = {c: max(len(c), max(len(str(r.get(c, "") or "")) for r in rows)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "") or "").ljust(widths[c]) for c in cols))


def main(argv: list[str] | None = None) -> int:
    ap = _build_argparser()
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    idx = Indexer(args.db)

    if args.cmd == "init":
        idx.init_schema()
        print(f"initialized {args.db} (index schema v{INDEX_SCHEMA_VERSION})")
        return 0

    if args.cmd == "crawl":
        idx.init_schema()
        if args.single_project:
            stats = idx.crawl_project(args.path)
        else:
            stats = idx.crawl_root(args.path)
        print(json.dumps({
            "runs_inserted": stats.runs_inserted,
            "runs_updated": stats.runs_updated,
            "runs_unchanged": stats.runs_unchanged,
            "runs_skipped_non_terminal": stats.runs_skipped_non_terminal,
            "pipeline_runs_inserted": stats.pipeline_runs_inserted,
            "pipeline_runs_updated": stats.pipeline_runs_updated,
            "pipeline_runs_unchanged": stats.pipeline_runs_unchanged,
            "errors": stats.errors,
            "error_paths": stats.error_paths,
        }, indent=2))
        return 0 if stats.errors == 0 else 1

    if args.cmd == "gc":
        n_index = idx.mark_orphaned_running(args.max_hours)
        result: dict[str, Any] = {"index_rows_marked": n_index}
        if args.projects_root:
            paths = gc_running(args.projects_root, max_runtime_hours=args.max_hours,
                               dry_run=args.dry_run)
            result["disk_files_rewritten" if not args.dry_run else "disk_files_would_rewrite"] = [
                str(p) for p in paths
            ]
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "export":
        n = idx.export_jsonl(args.out)
        print(f"exported {n} rows to {args.out}")
        return 0

    if args.cmd == "query":
        rows = idx.query_runs(
            reference_name=args.reference,
            step=args.step,
            status=args.status,
            user=args.user,
            since=args.since,
            until=args.until,
            pipeline_run_id=args.pipeline_run_id,
            limit=args.limit,
        )
        if args.format == "json":
            print(json.dumps(rows, indent=2))
        elif args.format == "jsonl":
            for r in rows:
                print(json.dumps(r))
        else:
            _print_table(rows)
        return 0

    if args.cmd == "stats":
        print(json.dumps(idx.stats(), indent=2))
        return 0

    if args.cmd == "rename":
        idx.record_rename(args.old, args.new, args.by)
        print(f"recorded rename: {args.old} -> {args.new}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
