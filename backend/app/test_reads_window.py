"""Reads for the viewer's window (reads_ticket / reads_data in main.py).

igv.js asks htsget-style: a ticket for a header or a window, then the URL the
ticket names. Both answers are ordinary BAM streams, and the window holds
exactly the reads samtools finds overlapping it. Needs samtools on PATH, as
the vsnp3 env provides; says so and passes when it is not.

Run directly:  python test_reads_window.py
"""
from __future__ import annotations

import gzip
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import main as app_main

FAILURES = []


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  ok   {label}")


def ticket(*a, **kw):
    kw.setdefault("klass", None)   # a Query default is an object when called directly
    return app_main.reads_ticket(*a, **kw)


def data(*a, **kw):
    kw.setdefault("klass", None)
    return app_main.reads_data(*a, **kw)


def answer(result):
    """(status, payload) from what an endpoint function returned: a dict is a
    200 JSON answer, a Response carries its own status and body."""
    if isinstance(result, dict):
        return 200, result
    body = result.body
    try:
        return result.status_code, json.loads(body)
    except ValueError:
        return result.status_code, body


def count_reads(samtools: str, bam_bytes: bytes) -> bytes:
    return subprocess.run([samtools, "view", "-c"], input=bam_bytes, capture_output=True).stdout.strip()


def make_bam(samtools: str, align_dir: Path, sample: str) -> Path:
    """A sorted, indexed BAM: two contigs, reads at known positions."""
    rnd = random.Random(3)
    seqs = {"seg1": "".join(rnd.choice("ACGT") for _ in range(600)),
            "with:colon/slash": "".join(rnd.choice("ACGT") for _ in range(400))}
    sam = align_dir / "sim.sam"
    with sam.open("w") as fh:
        fh.write("@HD\tVN:1.6\tSO:unsorted\n")
        for n, s in seqs.items():
            fh.write(f"@SQ\tSN:{n}\tLN:{len(s)}\n")
        i = 0
        for n, s in seqs.items():
            for pos in range(0, len(s) - 50, 10):      # a read every 10 bp, 50 bp long
                fh.write(f"r{i}\t0\t{n}\t{pos + 1}\t60\t50M\t*\t0\t0\t{s[pos:pos + 50]}\t{'I' * 50}\n")
                i += 1
    bam = align_dir / f"{sample}_nodup.bam"
    subprocess.run([samtools, "sort", "-o", str(bam), str(sam)], check=True, capture_output=True)
    subprocess.run([samtools, "index", str(bam)], check=True, capture_output=True)
    sam.unlink()
    return bam


def main() -> int:
    samtools = shutil.which("samtools")
    if not samtools:
        print("samtools is not on PATH here; nothing to test (the endpoints answer 503 without it)")
        return 0
    tmp = Path(tempfile.mkdtemp(prefix="reads_window_"))
    try:
        proj = tmp / "proj"
        align = proj / "step1" / "S1" / "alignment_REF"
        align.mkdir(parents=True)
        bam = make_bam(samtools, align, "S1")
        (proj / "step1" / "S2").mkdir()                       # a sample with no BAM
        app_main._project_dir_for = lambda cfg, name: proj

        print("the ticket names the data URL, page-relative, with the window carried over")
        status, body = answer(ticket("p", "S1", format="BAM", referenceName="seg1", start=100, end=160))
        check(status, 200, "status")
        check(body["htsget"]["format"], "BAM", "format")
        url = body["htsget"]["urls"][0]["url"]
        check(url.startswith("./api/projects/p/reads/S1/data?"), True, "data url is page-relative")
        check("referenceName=seg1" in url and "start=100" in url and "end=160" in url, True, "window carried")
        status, body = answer(ticket("p", "S1", format="BAM", klass="header"))
        check(body["htsget"]["urls"][0]["url"].endswith("/data?class=header"), True, "header ticket")
        status, body = answer(ticket("p", "S1", format="BAM", referenceName="with:colon/slash", start=0, end=5))
        check("referenceName=with%3Acolon%2Fslash" in body["htsget"]["urls"][0]["url"], True, "a contig name is quoted in the url")
        check(answer(ticket("p", "S1", format="CRAM", referenceName="seg1"))[0], 400, "only BAM")
        check(answer(ticket("p", "S2", format="BAM", referenceName="seg1"))[0], 404, "no BAM: not found")
        check(answer(ticket("p", "S1", format="BAM"))[0], 400, "a window needs a contig")

        print("the header alone")
        status, body = answer(data("p", "S1", klass="header"))
        check(status, 200, "status")
        check(gzip.decompress(body)[:4], b"BAM\x01", "a BAM stream")
        check(count_reads(samtools, body), b"0", "no reads in the header answer")

        print("a window holds exactly the reads overlapping it")
        for contig, start, end in (("seg1", 100, 160), ("seg1", 0, 10), ("with:colon/slash", 200, 230), ("seg1", 560, 600)):
            status, body = answer(data("p", "S1", referenceName=contig, start=start, end=end))
            check(status, 200, f"{contig}:{start}-{end} status")
            got = count_reads(samtools, body)
            want = subprocess.run([samtools, "view", "-c", str(bam), f"{{{contig}}}:{start + 1}-{end}"],
                                  capture_output=True).stdout.strip()
            check(got, want, f"{contig}:{start}-{end} read count matches samtools")
            check(int(want) > 0, True, f"{contig}:{start}-{end} is a non-empty window in the fixture")
        status, body = answer(data("p", "S1", referenceName="seg1", start=300))
        check(status, 200, "an open-ended window")
        check(count_reads(samtools, body),
              subprocess.run([samtools, "view", "-c", str(bam), "{seg1}:301"], capture_output=True).stdout.strip(),
              "open-ended count matches samtools")

        print("bad input is an answer, not a truncated file")
        check(answer(data("p", "S1", referenceName="seg1", start=50, end=10))[0], 400, "end before start")
        check(answer(data("p", "S1"))[0], 400, "a window needs a contig")
        check(answer(data("p", "S2", referenceName="seg1", start=0, end=10))[0], 404, "no BAM: not found")
        status, body = answer(data("p", "S1", referenceName="nope", start=0, end=10))
        check(status in (200, 400), True, "an unknown contig is refused or empty")
        if status == 200:
            check(count_reads(samtools, body), b"0", "unknown contig: no reads")

        print("without samtools the ticket says so")
        saved = app_main._resolve_samtools
        app_main._resolve_samtools = lambda cfg: ""
        try:
            check(answer(ticket("p", "S1", format="BAM", referenceName="seg1", start=0, end=10))[0], 503, "ticket: unavailable")
            check(answer(data("p", "S1", referenceName="seg1", start=0, end=10))[0], 503, "data: unavailable")
        finally:
            app_main._resolve_samtools = saved
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED")
        return 1
    print("\nAll reads-window tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
