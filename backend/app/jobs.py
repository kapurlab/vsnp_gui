import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


class JobManager:
    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict] = {}

    def start_job(self, name: str, command: str, cwd: Optional[Path] = None, env: Optional[Dict[str, str]] = None) -> str:
        job_id = uuid.uuid4().hex
        log_path = self.jobs_dir / f"{job_id}.log"
        started_at = datetime.now(timezone.utc)
        job = {
            "id": job_id,
            "name": name,
            "command": command,
            "cwd": str(cwd) if cwd else None,
            "status": "running",
            "exit_code": None,
            "log_path": str(log_path),
            "started_at": started_at.isoformat(),
            "finished_at": None,
            "duration_seconds": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        thread = threading.Thread(target=self._run, args=(job_id, command, cwd, env, log_path), daemon=True)
        thread.start()
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, job_id: str, command: str, cwd: Optional[Path], env: Optional[Dict[str, str]], log_path: Path) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as log:
            started_at = datetime.now(timezone.utc)
            log.write(f"# started_at_utc: {started_at.isoformat()}\n")
            log.write(f"$ {command}\n\n")
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=str(cwd) if cwd else None,
                env={**os.environ, **(env or {})},
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=True,
                text=True
            )
            exit_code = process.wait()
            finished_at = datetime.now(timezone.utc)
            duration = (finished_at - started_at).total_seconds()
            log.write("\n")
            log.write(f"# finished_at_utc: {finished_at.isoformat()}\n")
            log.write(f"# duration_seconds: {duration:.2f}\n")
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["exit_code"] = exit_code
                job["status"] = "succeeded" if exit_code == 0 else "failed"
                job["finished_at"] = finished_at.isoformat()
                job["duration_seconds"] = duration
