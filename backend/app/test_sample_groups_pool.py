#!/usr/bin/env python3
"""The group reader's worker processes give the thread path's answer.

sample_groups.sample_groups reads a batch of POOL_MIN_FILES or more VCFs in
spawn-started worker processes (the parsing is CPU, and threads share one
core). Whatever the workers return must be what a thread would have put in the
cache, and a pool that cannot start, or breaks, must fall back to threads and
still answer. This module keeps its top level light on purpose: a spawned
worker re-imports the main module, so the heavy fixtures are imported inside
main().

Run: cd backend/app && ../../env/bin/python test_sample_groups_pool.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sample_groups as sg  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    print(("  OK  " if cond else "  FAIL") + " " + label)
    if not cond:
        FAILED += 1


class _BrokenPool:
    def map(self, *a, **k):
        raise RuntimeError("worker gone")

    def shutdown(self, **k):
        pass


def main() -> int:
    from test_sample_groups import write_define_filter, write_vcfs   # heavy: imports app.main
    tmp = Path(tempfile.mkdtemp(prefix="groups_pool_"))
    saved = (sg.POOL_MIN_FILES, sg._start_pool)
    try:
        defs = sg.load_definitions(write_define_filter(tmp / "H5_define_filter.xlsx"))
        files = write_vcfs(tmp / "db")
        # Enough files for a pool, under distinct names (the cache is keyed by name).
        many = list(files) + [(f"copy{i}_zc.vcf", path) for i in range(12) for _n, path in files[:2]]
        many = [(f"{i:03d}_{name}", path) for i, (name, path) in enumerate(many)]
        check(f"{len(many)} files, more than POOL_MIN_FILES ({sg.POOL_MIN_FILES})", len(many) >= sg.POOL_MIN_FILES)

        print("[threads, as the oracle]")
        sg.POOL_MIN_FILES = 10 ** 9
        threads = sg.sample_groups(many, defs, None)

        print("[worker processes]")
        sg.POOL_MIN_FILES = 2
        started = []
        real_start = saved[1]

        def recording_start(defs_, workers):
            pool = real_start(defs_, workers)
            started.append(pool is not None)
            return pool
        sg._start_pool = recording_start
        pooled = sg.sample_groups(many, defs, None)
        check("a pool was started", started == [True])
        check("the same groups for every file", pooled["groups"] == threads["groups"])
        check("the same counts read and unreadable",
              (pooled["read"], pooled["unreadable"]) == (threads["read"], threads["unreadable"]))
        check("every file got an answer", pooled["read"] == len(many) and not pooled["pending"])

        print("[a pool that breaks falls back to threads]")
        sg._start_pool = lambda defs_, workers: _BrokenPool()
        fell_back = sg.sample_groups(many, defs, None)
        check("the answer is the thread path's", fell_back["groups"] == threads["groups"])

        print("[a pool that cannot start reads in threads]")
        sg._start_pool = lambda defs_, workers: None
        no_pool = sg.sample_groups(many, defs, None)
        check("the answer is the thread path's", no_pool["groups"] == threads["groups"])

        print("[the cache written through the pool is the cache the thread path reads]")
        sg._start_pool = real_start
        cache = tmp / "cache.json"
        first = sg.sample_groups(many, defs, cache)
        sg.POOL_MIN_FILES = 10 ** 9
        warm = sg.sample_groups(many, defs, cache)
        check("nothing left to read", first["read"] == len(many) and warm["read"] == 0)
        check("and the groups agree", warm["groups"] == threads["groups"])
    finally:
        sg.POOL_MIN_FILES, sg._start_pool = saved
        shutil.rmtree(tmp, ignore_errors=True)
    print("PASS" if not FAILED else f"{FAILED} FAILURE(S)")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
