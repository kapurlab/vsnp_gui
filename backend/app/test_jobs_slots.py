"""The job manager names who holds a category's run slots.

A Step 2 run queued behind the one-slot cap used to show only "will start when
a run slot is free". slot_holders() says which job holds the slot, so the GUI
can name it, and the holder's public pid lets the slot report check that its
process group is still alive.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.jobs import JobManager  # noqa: E402


def wait_for(pred, timeout=10.0, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        jm = JobManager(Path(td))
        assert jm.slot_limit("step2") is None, "no limit before the first capped dispatch"
        assert jm.slot_holders("step2") == [], "no holders before any dispatch"

        a = jm.start_job(name="step2", command="sleep 30", cwd=Path(td), category="step2", max_concurrent=1)
        wait_for(lambda: jm.get_job(a)["status"] == "running", what="first job to run")
        b = jm.start_job(name="step2", command="sleep 30", cwd=Path(td), category="step2", max_concurrent=1)
        time.sleep(0.3)
        assert jm.get_job(b)["status"] == "queued", jm.get_job(b)["status"]
        assert jm.slot_limit("step2") == 1

        holders = jm.slot_holders("step2")
        assert [h["id"] for h in holders] == [a], holders
        assert holders[0]["status"] == "running"
        pid = holders[0].get("pid")
        assert isinstance(pid, int) and pid > 0, "the holder's pid is public"
        os.kill(pid, 0)  # alive
        assert "pid" not in jm.get_job(b) or jm.get_job(b)["pid"] is None, "a queued job has no pid yet"
        print("  OK  the running job is the one named holder, with its pid")

        # Stopping the holder frees the slot: the queued job becomes the holder.
        assert jm.stop_job(a)
        wait_for(lambda: jm.get_job(a)["status"] == "cancelled", what="holder to be cancelled")
        wait_for(lambda: jm.get_job(b)["status"] == "running", what="queued job to start")
        wait_for(lambda: [h["id"] for h in jm.slot_holders("step2")] == [b], what="holder handover")
        print("  OK  stopping the holder hands the slot to the queued job")

        # A job cancelled while queued never becomes a holder.
        c = jm.start_job(name="step2", command="sleep 30", cwd=Path(td), category="step2", max_concurrent=1)
        time.sleep(0.2)
        assert jm.get_job(c)["status"] == "queued"
        assert jm.stop_job(c)
        wait_for(lambda: jm.get_job(c)["status"] == "cancelled", what="queued job to cancel")
        assert [h["id"] for h in jm.slot_holders("step2")] == [b]
        print("  OK  a run cancelled while queued never held the slot")

        # Finished: no holders left.
        jm.stop_job(b)
        wait_for(lambda: jm.get_job(b)["status"] == "cancelled", what="second job to stop")
        wait_for(lambda: jm.slot_holders("step2") == [], what="slot release")
        # Uncapped jobs are not slots.
        d = jm.start_job(name="misc", command="true", cwd=Path(td))
        wait_for(lambda: jm.get_job(d)["status"] == "succeeded", what="uncapped job")
        assert jm.slot_holders("") == [] and jm.slot_holders("step2") == []
        print("  OK  a released slot has no holder; uncapped jobs never hold one")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
