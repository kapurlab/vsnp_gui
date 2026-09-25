#!/usr/bin/env python3
"""Replay the GUI's project-switch cascade against a live backend.

Starts the real backend (uvicorn, the app's own env) under the latency model
in fslat/, fires the same requests App.jsx fires on a project click, in the
same dependency order and concurrency, and reports per-request wall time,
the settle time (first request sent -> last response in) and the number of
modelled filesystem calls the switch cost, backend and subprocesses alike.

  switch_bench.py --fx <fixture root> [--ms 1] [--project owl --other small]
                  [--checkout <vsnp_gui checkout>] [--port 8765] [--label X]

Scenarios run, in order, on ONE server process (every OOD launch is a fresh
backend, so "cold" = fresh process with whatever caches sit on disk):
  page load       GET /api/projects
  cold switch     cascade(project)
  switch away     cascade(other)
  warm revisit    cascade(project)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

HERE = Path(__file__).resolve().parent


def fs_calls(count_dir: str) -> int:
    total = 0
    for f in glob.glob(os.path.join(count_dir, "*.count")):
        try:
            total += int(open(f).read().strip() or 0)
        except (OSError, ValueError):
            pass
    return total


class Server:
    def __init__(self, checkout: Path, fx: Path, port: int, ms: float, count_dir: str, log: Path):
        self.checkout, self.fx, self.port, self.ms, self.count_dir, self.log = checkout, fx, port, ms, count_dir, log
        self.proc = None

    def env(self):
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(HERE / "fslat"),
            "FSLAT_ROOT": str(self.fx / "data"),
            "FSLAT_MS": str(self.ms),
            "FSLAT_COUNT_DIR": self.count_dir,
            "XDG_CONFIG_HOME": str(self.fx / "config"),
            "VSNP_GUI_SITE_ROOT": str(self.fx / "data" / "site"),
            "VSNP_GUI_SHARED_PROJECTS_ROOT": "",
            "PYTHONUNBUFFERED": "1",
        })
        return env

    def start(self):
        py = self.checkout / "env" / "bin" / "python"
        self.proc = subprocess.Popen(
            [str(py), "-m", "uvicorn", "app.main:app", "--port", str(self.port), "--log-level", "warning"],
            cwd=str(self.checkout / "backend"), env=self.env(),
            stdout=open(self.log, "ab"), stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/health", timeout=2).read()
                return
            except Exception:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"backend exited {self.proc.returncode}; see {self.log}")
                time.sleep(0.2)
        raise RuntimeError("backend did not come up")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Cascade:
    """One project click, as App.jsx makes it."""

    def __init__(self, base: str, project: str):
        self.base, self.p = base, project
        self.records = []
        self.lock = threading.Lock()
        self.errors = []

    def get(self, name: str, path: str):
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(self.base + path, timeout=900) as r:
                body = r.read()
            data = json.loads(body) if body else None
        except Exception as exc:  # a 404 etc. is still a finished request
            data = None
            with self.lock:
                self.errors.append((name, str(exc)))
        t1 = time.perf_counter()
        with self.lock:
            self.records.append((name, t0, t1))
        return data

    def api(self, name: str, tail: str):
        return self.get(name, f"/api/projects/{self.p}/{tail}")

    def run(self):
        p = self.p
        ex = ThreadPoolExecutor(max_workers=40)
        futures = []
        sub = lambda fn, *a: futures.append(ex.submit(fn, *a))

        def qc():
            # loadQC: poll while the scan runs (1.5 s, as the page does), then
            # the exclusions, the Kraken dirs and the edits list.
            while True:
                d = self.api("qc_summary", "qc_summary")
                if isinstance(d, dict) and d.get("status") == "scanning":
                    time.sleep(1.5)
                    continue
                break
            self.api("qc_exclude", "qc_exclude")
            sub(self.api, "kraken/samples", "kraken/samples")
            self.api("step1/edits", "step1/edits")

        def runs():
            runs = self.api("step2/runs", "step2/runs") or []
            pick = None
            for r in runs:
                if r.get("has_results"):
                    pick = r["run_id"]
                    break
            if pick is None and runs:
                pick = runs[0]["run_id"]
            rid = f"?run_id={pick}" if pick else ""
            self.api("step2_outputs", f"step2_outputs{rid}")
            sub(self.api, "step2/groupings", f"step2/groupings{rid}")
            self.api("step2/vcf_count", "step2/vcf_count")
            self.api("posthoc/status_all", f"posthoc/status_all?tool=snp_analysis{'&run_id=' + pick if pick else ''}")

        def vcfdb():
            self.api("vcf_database/samples", "step2/vcf_database/samples")
            for name, tail in (("reference_audit", "step2/reference_audit"),
                               ("build-exclusions", "step2/build-exclusions"),
                               ("qc_exclude (2)", "qc_exclude"),
                               ("blocklist", "step2/blocklist"),
                               ("panel-accessions", "step2/panel-accessions"),
                               ("name-aliases", "name-aliases"),
                               ("panels", "step2/panels"),
                               ("build-meta", "step2/build-meta")):
                sub(self.api, name, tail)

        # Wave 1: everything the two [selectedProject] effects and the card
        # expansion fire together.
        sub(self.api, "vcfs", "vcfs")
        sub(self.api, "reference_lock", "reference_lock")
        sub(qc)
        sub(self.api, "step1/status", "step1/status")
        sub(self.api, "quarantine", "quarantine")
        sub(self.api, "step1/setup", "step1/setup")
        sub(runs)
        sub(self.api, "step2/active", "step2/active")
        sub(vcfdb)
        sub(self.api, "inputs", "inputs")
        sub(self.api, "sra-download-report", "sra-download-report")
        sub(self.api, "step1/samples", "step1/samples")
        sub(self.api, "kraken/samples (card)", "kraken/samples")
        sub(self.api, "inputs (card)", "inputs")
        # Futures are appended while others run; drain until stable.
        while True:
            pending = [f for f in futures if not f.done()]
            if not pending:
                break
            wait(pending)
        ex.shutdown()
        for f in futures:
            f.result()
        return self

    def settle(self) -> float:
        return max(t1 for _n, _t0, t1 in self.records) - min(t0 for _n, t0, _t1 in self.records)

    def table(self) -> str:
        rows = sorted(self.records, key=lambda r: -(r[2] - r[1]))
        out = []
        for name, t0, t1 in rows:
            out.append(f"    {t1 - t0:7.2f} s  {name}")
        return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx", required=True)
    ap.add_argument("--checkout", default=os.path.expanduser("~/.local/share/bdtools/checkouts/vsnp_gui"))
    ap.add_argument("--ms", type=float, default=1.0)
    ap.add_argument("--project", default="owl")
    ap.add_argument("--other", default="small")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--label", default="")
    ap.add_argument("--top", type=int, default=8, help="slowest requests to print per switch")
    ap.add_argument("--json", default="", help="write the numbers here")
    args = ap.parse_args()

    fx = Path(args.fx).resolve()
    count_dir = str(fx / "counts")
    shutil.rmtree(count_dir, ignore_errors=True)
    os.makedirs(count_dir)
    log = fx / f"backend{('-' + args.label) if args.label else ''}.log"
    srv = Server(Path(args.checkout), fx, args.port, args.ms, count_dir, log)
    base = f"http://127.0.0.1:{args.port}"
    results = {}
    print(f"== {args.label or 'run'}: {args.ms} ms per filesystem call under {fx / 'data'}")
    srv.start()
    try:
        def scenario(name, fn):
            c0 = fs_calls(count_dir)
            t0 = time.perf_counter()
            r = fn()
            dt = time.perf_counter() - t0
            time.sleep(0.6)  # let the count files flush
            calls = fs_calls(count_dir) - c0
            settle = r.settle() if isinstance(r, Cascade) else dt
            results[name] = {"seconds": round(settle, 2), "fs_calls": calls}
            print(f"  {name:<28} {settle:7.2f} s   {calls:>8,} fs calls")
            if isinstance(r, Cascade):
                print(r.table().split("\n", args.top)[0] if args.top else "")
                print("\n".join(r.table().split("\n")[:args.top]))
                if r.errors:
                    print("    errors:", r.errors[:5])
            return r

        scenario("page load (/api/projects)", lambda: Cascade(base, "").get("projects", "/api/projects") and _Fake())
        scenario(f"cold switch to {args.project}", lambda: Cascade(base, args.project).run())
        scenario(f"switch to {args.other}", lambda: Cascade(base, args.other).run())
        scenario(f"back to {args.project}", lambda: Cascade(base, args.project).run())
    finally:
        srv.stop()
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))
    return 0


class _Fake:
    def settle(self):
        return 0.0


if __name__ == "__main__":
    sys.exit(main())
