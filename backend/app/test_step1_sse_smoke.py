"""
Offline smoke test for the T-05 SSE multiplexer in /api/jobs/<id>/events.

Builds a fake batch log + per-sample log layout, monkey-patches the job
manager to look like a step1 job, drives the event_stream generator
through several poll cycles while appending to the fake logs, and
verifies the yielded SSE lines carry the right [batch] / [sample]
prefixes — including a sample log that appears mid-stream.

Run from the backend dir (or anywhere — sys.path is derived from __file__):

    /srv/kapurlab/tools/vsnp3/bin/python backend/app/test_step1_sse_smoke.py
"""
from __future__ import annotations
import asyncio
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

# Make `from app import main` work whether we're invoked from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as backend_main  # noqa: E402


class FakeJobManager:
    def __init__(self, name: str, cwd: Path, log_path: Path):
        self._jobs = {
            "fake_job_id": {
                "name": name,
                "cwd": str(cwd),
                "log_path": str(log_path),
                "status": "running",
            }
        }

    def get_job(self, job_id):
        return self._jobs.get(job_id)

    def finish(self, status="succeeded"):
        self._jobs["fake_job_id"]["status"] = status


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="t05_sse_smoke_"))
    try:
        # Layout
        step1_dir = tmp / "step1"
        step1_dir.mkdir()
        batch_log = tmp / "batch.log"
        batch_log.write_text("== Running step1 ==\n")
        sample_a = step1_dir / "Mg220"
        sample_a.mkdir()
        (sample_a / "run_step1.log").write_text("Start: Mg220\n[M::bwa] line 1 of Mg220\n")
        # sample_b will appear mid-stream

        # Monkey-patch job_manager
        fake = FakeJobManager(name="step1", cwd=step1_dir, log_path=batch_log)
        backend_main.job_manager = fake

        resp = backend_main.job_events("fake_job_id")
        gen = resp.body_iterator
        collected: list[str] = []

        async def consume_async():
            async for chunk in gen:
                if isinstance(chunk, bytes):
                    collected.append(chunk.decode("utf-8"))
                else:
                    collected.append(chunk)

        def runner():
            asyncio.run(consume_async())

        t = threading.Thread(target=runner, daemon=True)
        t.start()

        time.sleep(0.6)  # one poll cycle

        # Append more to Mg220's log + create Mg222 mid-stream
        with (sample_a / "run_step1.log").open("a") as f:
            f.write("[M::bwa] line 2 of Mg220\n")
        sample_b = step1_dir / "Mg222"
        sample_b.mkdir()
        (sample_b / "run_step1.log").write_text("Start: Mg222\n[M::bwa] line 1 of Mg222\n")
        time.sleep(0.6)

        # Append batch log + finish
        with batch_log.open("a") as f:
            f.write("All samples queued\n")
        time.sleep(0.6)
        fake.finish(status="succeeded")
        t.join(timeout=2)

        text = "".join(collected)

        def must(condition, label):
            if not condition:
                print("--- raw SSE stream on failure ---")
                print(text)
                print("--- end ---")
                raise AssertionError(f"FAIL: {label}")
            print(f"  OK  {label}")

        must("data: [batch] == Running step1 ==" in text, "batch log line carries [batch] prefix")
        must("data: [Mg220] [M::bwa] line 1 of Mg220" in text, "Mg220 line 1 carries [Mg220] prefix")
        must("data: [Mg220] [M::bwa] line 2 of Mg220" in text, "Mg220 mid-stream append picked up")
        must("data: [Mg222] Start: Mg222" in text, "Mg222 discovered mid-stream")
        must("data: [Mg222] [M::bwa] line 1 of Mg222" in text, "Mg222 content streamed")
        must("data: [batch] All samples queued" in text, "late batch line picked up")
        must("data: [job:succeeded]" in text, "job terminator emitted")
        bad = [
            ln for ln in text.splitlines()
            if ln.startswith("data: ") and not (
                ln.startswith("data: [batch] ")
                or ln.startswith("data: [Mg220] ")
                or ln.startswith("data: [Mg222] ")
                or ln.startswith("data: [job:")
            )
        ]
        must(not bad, f"every data line is prefixed (offenders: {bad})")

        print("\nAll T-05 SSE multiplexer smoke assertions passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
