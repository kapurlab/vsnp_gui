#!/usr/bin/env python3
"""Replay an IGV launch and a table preview against a live backend.

Starts the real backend under the latency model in fslat/ (see switch_bench.py),
then fires what the browser fires when a cascade-table cell is clicked:

  1. GET /step1/files?sample=S           the launcher resolving the sample's files
  2. the igv.js sequence through /serve   .fai, a FASTA slice, the calls VCF,
                                          the .bai, the BAM header, a BAM chunk,
                                          the reference GFF
  3. GET /preview-xlsx?path=<cascade>     the table, cold and again warm, then a
                                          scroll batch (rows_from)

and reports each request's wall time and the number of modelled filesystem
calls it cost. A request that costs thousands of calls on a big project is one
that holds a browser connection for minutes on shared storage.

  igv_bench.py --fx <fixture root> [--ms 1] [--project owl] [--sample S]
               [--checkout <vsnp_gui checkout>] [--port 8766] [--label X]
               [--concurrent N]   also time one /serve request while N table
                                  previews are in flight (threadpool pressure)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from switch_bench import Server, fs_calls  # noqa: E402


def settle_counts(count_dir: str, previous: int) -> int:
    """The counter files are flushed ~4x/s; wait for them to catch up."""
    time.sleep(0.6)
    return fs_calls(count_dir) - previous


class Bench:
    def __init__(self, base: str, count_dir: str, project: str):
        self.base, self.count_dir, self.project = base, count_dir, project
        self.rows = []

    def get(self, label: str, path: str, headers: dict | None = None, expect=None):
        before = fs_calls(self.count_dir)
        req = urllib.request.Request(self.base + path, headers=headers or {})
        t0 = time.perf_counter()
        status = 0
        body = b""
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                status = r.status
                body = r.read()
        except urllib.error.HTTPError as e:
            status = e.code
            body = e.read()
        dt = time.perf_counter() - t0
        calls = settle_counts(self.count_dir, before)
        self.rows.append((label, status, dt, calls, len(body)))
        print(f"  {label:<34} HTTP {status}  {dt:7.2f} s  {calls:>7} calls  {len(body):>9} bytes", flush=True)
        return status, body

    def serve(self, label: str, abs_path: str, byte_range: tuple[int, int] | None = None):
        q = urllib.parse.quote(abs_path, safe="")
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"} if byte_range else {}
        return self.get(label, f"/api/projects/{self.project}/serve?path={q}", headers)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx", required=True)
    ap.add_argument("--ms", type=float, default=1.0)
    ap.add_argument("--project", default="owl")
    ap.add_argument("--sample", default="")
    ap.add_argument("--checkout", default=str(HERE.parents[1]))
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--label", default="")
    ap.add_argument("--table", default="", help="cascade table xlsx to preview (default: first found)")
    ap.add_argument("--concurrent", type=int, default=0)
    args = ap.parse_args()

    fx = Path(args.fx).resolve()
    checkout = Path(args.checkout).resolve()
    project_dir = fx / "data" / "projects" / args.project
    step1 = project_dir / "step1"
    sample = args.sample or sorted(
        d.name for d in step1.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))
    )[len(list(step1.iterdir())) // 2]
    table = args.table or next(iter(sorted(glob.glob(str(project_dir / "step2" / "*" / "*" / "*cascade_table.xlsx")))), "")

    count_dir = tempfile.mkdtemp(prefix="igvbench_")
    log = Path(count_dir) / "server.log"
    srv = Server(checkout, fx, args.port, args.ms, count_dir, log)
    print(f"[{args.label or 'run'}] backend from {checkout}, {args.ms} ms per call under {fx / 'data'}")
    srv.start()
    base = f"http://127.0.0.1:{args.port}"
    b = Bench(base, count_dir, args.project)
    try:
        b.get("warm-up /api/health", "/api/health")
        print(f"\n== IGV launch for {sample} ==")
        status, body = b.get("step1/files", f"/api/projects/{args.project}/step1/files?sample={urllib.parse.quote(sample)}")
        if status != 200:
            print("   step1/files failed:", body[:200])
            return 1
        files = json.loads(body)
        fasta = files["reference_fasta"]
        fai = fasta + ".fai"
        bam = files["bam"]
        calls_vcf = files.get("annotated_vcf") or files.get("source_vcf")
        gff = files.get("reference_gff")
        bam_size = os.path.getsize(bam) if bam else 0
        # The order igv.js takes: genome first, then each track's index and
        # header, then the data for the visible window, then the annotation.
        b.serve("serve .fai (whole)", fai)
        b.serve("serve .fasta (range)", fasta, (0, 1023))
        if calls_vcf:
            b.serve("serve calls VCF (whole)", calls_vcf)
        if bam:
            b.serve("serve .bai (whole)", bam + ".bai")
            b.serve("serve BAM header (range)", bam, (0, min(65535, bam_size - 1)))
            b.serve("serve BAM chunk (range)", bam, (0, min(2047, bam_size - 1)))
        if gff:
            b.serve("serve reference GFF (whole)", gff)

        if table:
            q = urllib.parse.quote(table, safe="")
            print(f"\n== table preview {Path(table).name} ==")
            b.get("preview-xlsx cold", f"/api/projects/{args.project}/preview-xlsx?path={q}")
            b.get("preview-xlsx warm (cached)", f"/api/projects/{args.project}/preview-xlsx?path={q}")
            b.get("preview-xlsx scroll batch", f"/api/projects/{args.project}/preview-xlsx?path={q}&rows_from=200&rows_count=200")

        if args.concurrent and table:
            print(f"\n== one /serve while {args.concurrent} previews are in flight ==")
            q = urllib.parse.quote(table, safe="")
            ex = ThreadPoolExecutor(max_workers=args.concurrent + 1)
            futs = [ex.submit(urllib.request.urlopen,
                              f"{base}/api/projects/{args.project}/preview-xlsx?path={q}&rows_from={200 + i}&rows_count=200",
                              None, 900)
                    for i in range(args.concurrent)]
            time.sleep(1.0)
            b.serve("serve .bai while previews run", bam + ".bai")
            for f in futs:
                try:
                    f.result().read()
                except Exception as exc:
                    print("   preview error:", exc)
            ex.shutdown()

        total_igv = sum(r[3] for r in b.rows if r[0].startswith(("step1/files", "serve")) and "while" not in r[0])
        wall_igv = sum(r[2] for r in b.rows if r[0].startswith(("step1/files", "serve")) and "while" not in r[0])
        print(f"\nIGV launch total: {wall_igv:.2f} s, {total_igv} calls across {sum(1 for r in b.rows if r[0].startswith(('step1/files', 'serve')) and 'while' not in r[0])} requests")
        return 0
    finally:
        srv.stop()
        shutil.rmtree(count_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
