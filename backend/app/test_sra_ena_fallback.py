"""The ENA fallback (download method 3) survives one bad answer from the portal.

Method 3 is the ONLY download route on a laptop: the vsnp3 env ships no
sra-tools, so method 1 (wget + fasterq-dump) and method 2 (enaDataGet) are
skipped there. The ENA portal API sometimes answers a filereport query with an
error line instead of the table:

    ERROR occurred. Not all results may have been written.Query: {...}

The old parse took the LAST line of the reply as the file list, so that message
became the "URL" list and the accession failed outright — observed on 1 of 26
runs in a training batch, on a run ENA does serve. These tests run the real
generated method3 in bash against a fake curl.

A transfer can also stop moving partway. curl waits on a quiet connection for
ever unless told otherwise, so the job said "Downloading" until it was killed
(Ames HPC, 2026-11-19: SRR34371563 stopped at 12.2 of 50.3 MB), and the partial
file sat in download/ under its final name, listed as ready to run. The stall
tests run the generated fetch_resumable with the real curl against a local
server that stops sending. And a Nanopore run arrives from ENA as one file
named <acc>_1.fastq.gz, which already_have took for half of a pair.

Run directly:  python test_sra_ena_fallback.py
"""

from __future__ import annotations

import gzip
import http.server
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import sra

ACC = "SRR33842987"
TABLE = (
    "run_accession\tfastq_ftp\n"
    f"{ACC}\tftp.sra.ebi.ac.uk/vol1/fastq/SRR338/087/{ACC}/{ACC}_1.fastq.gz;"
    f"ftp.sra.ebi.ac.uk/vol1/fastq/SRR338/087/{ACC}/{ACC}_2.fastq.gz\n"
)
ERROR = ("ERROR occurred. Not all results may have been written.Query: "
         '{"bool":{"must":[{"term":{"run_accession":{"value":"' + ACC + '"}}}]}}\n')

# A curl stand-in: answers the portal API from a queue of canned replies (one
# file per call, consumed in order) and "downloads" a file for anything else.
FAKE_CURL = r"""#!/bin/bash
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    --connect-timeout|--speed-limit|--speed-time|--max-time|-C) shift 2;;
    -*) shift;;
    *) url="$1"; shift;;
  esac
done
case "$url" in
  *ena/portal/api/filereport*)
    n=$(ls "$REPLIES" | sort | head -1)
    [ -n "$n" ] || exit 0
    cat "$REPLIES/$n"; rm -f "$REPLIES/$n";;
  *_1.fastq.gz)
    if [ -n "${FAIL_R1:-}" ]; then echo "curl: (22) The requested URL returned error: 403" >&2; exit 22; fi
    echo "$url" >> "$FETCHED"; echo fastq | gzip > "$out";;
  *)
    echo "$url" >> "$FETCHED"; echo fastq | gzip > "$out";;
esac
"""


def function_source(name: str) -> str:
    script = sra.build_download_script(Path("/nonexistent"), [ACC], allow_insecure_https=False)
    m = re.search(r"^" + name + r"\(\) \{\n.*?^\}\n", script, re.S | re.M)
    if not m:
        raise AssertionError(f"{name}() not found in the generated script")
    return m.group(0)


def method3_source() -> str:
    """method3 and the two functions it calls."""
    return "\n".join(function_source(n) for n in ("human", "size_of", "fetch_resumable", "method3"))


def stall_setting() -> str:
    """The script's own CURL_STALL line, so the test runs the shipped limits
    (SRA_STALL_SECONDS only shortens the wait)."""
    script = sra.build_download_script(Path("/nonexistent"), [ACC], allow_insecure_https=False)
    m = re.search(r"^CURL_STALL=.*$", script, re.M)
    if not m:
        raise AssertionError("CURL_STALL not set in the generated script")
    return m.group(0)


PAYLOAD = gzip.compress(os.urandom(400_000))


class _Stalling(http.server.BaseHTTPRequestHandler):
    """/stall: the first request sends half the file and goes quiet; later ones
    honour Range. /dead: headers, then nothing, every time. /damaged: bytes
    that are not gzip. Requests are recorded on the server as (path, Range)."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range", "")
        self.server.seen.append((self.path, rng))
        if self.path.startswith("/damaged"):
            body = b"this is not a gzip file\n" * 50
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/dead"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.flush()
            time.sleep(15)
            self.close_connection = True
            return
        first = sum(1 for path, _ in self.server.seen if path == self.path) == 1
        if first:
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD[: len(PAYLOAD) // 2])
            self.wfile.flush()
            time.sleep(15)
            self.close_connection = True
            return
        start = int(re.match(r"bytes=(\d+)-", rng).group(1)) if rng else 0
        body = PAYLOAD[start:]
        self.send_response(206 if rng else 200)
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_fetch(path: str) -> tuple[int, str, dict, list, float]:
    """The generated fetch_resumable, real curl, against the local server.
    Returns (exit status, log, {file name: bytes}, requests seen, seconds)."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stalling)
    srv.daemon_threads = True
    srv.seen = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp(prefix="stall-"))
    try:
        harness = (
            "set -u\nsleep() { :; }\n" + stall_setting() + "\n"
            + function_source("human") + function_source("size_of")
            + function_source("fetch_resumable")
            + f'\nfetch_resumable "http://127.0.0.1:{srv.server_address[1]}{path}" out.fastq.gz\n'
        )
        env = dict(os.environ, SRA_STALL_SECONDS="2", NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
        t0 = time.time()
        p = subprocess.run(["bash", "-c", harness], cwd=work, env=env,
                           capture_output=True, text=True, timeout=120)
        took = time.time() - t0
        files = {f.name: f.read_bytes() for f in work.iterdir()}
        return p.returncode, p.stdout + p.stderr, files, list(srv.seen), took
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(work, ignore_errors=True)


def already_have(files: list[str]) -> int:
    """already_have's exit status with these files present in download/."""
    tmp = Path(tempfile.mkdtemp(prefix="have-"))
    try:
        for f in files:
            (tmp / f).write_text("x")
        harness = "set -u\n" + function_source("already_have") + f'\nalready_have "{ACC}"\n'
        return subprocess.run(["bash", "-c", harness], cwd=tmp, timeout=30).returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_method3(replies: list[str], fail_r1: bool = False) -> tuple[int, str, list[str], list[str]]:
    tmp = Path(tempfile.mkdtemp(prefix="ena3-"))
    try:
        (tmp / "bin").mkdir()
        curl = tmp / "bin/curl"
        curl.write_text(FAKE_CURL)
        curl.chmod(0o755)
        (tmp / "replies").mkdir()
        for i, r in enumerate(replies):
            (tmp / "replies" / f"{i:02d}").write_text(r)
        work = tmp / "work"
        work.mkdir()
        harness = (
            "set -u\nCAN_METHOD3=1\nCURL_STALL=\"\"\nsleep() { :; }\n" + method3_source()
            + f'\nmethod3 "{ACC}"\n'
        )
        env = dict(os.environ, PATH=f"{tmp/'bin'}:{os.environ['PATH']}",
                   REPLIES=str(tmp / "replies"), FETCHED=str(tmp / "fetched"))
        if fail_r1:
            env["FAIL_R1"] = "1"
        p = subprocess.run(["bash", "-c", harness], cwd=work, env=env,
                           capture_output=True, text=True, timeout=60)
        fetched = (tmp / "fetched").read_text().split() if (tmp / "fetched").exists() else []
        return p.returncode, p.stdout + p.stderr, sorted(os.listdir(work)), fetched
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check(cond: bool, label: str) -> None:
    if not cond:
        raise AssertionError(label)
    print(f"  OK  {label}")


def main() -> int:
    if not shutil.which("bash") or not shutil.which("awk"):
        print("SKIP: needs bash and awk")
        return 0

    print("\n[a clean answer downloads both mates]")
    rc, out, files, fetched = run_method3([TABLE])
    check(rc == 0, "method3 succeeds")
    check(files == [f"{ACC}_1.fastq.gz", f"{ACC}_2.fastq.gz"], f"both files written ({files})")
    check(all(u.startswith("https://ftp.sra.ebi.ac.uk/") for u in fetched),
          "fetched over HTTPS from the URLs in the table")

    print("\n[an error line first, then the table: the sample is NOT lost]")
    rc, out, files, fetched = run_method3([ERROR, TABLE])
    check(rc == 0, "method3 succeeds on the second ask")
    check(len(files) == 2, "both files written")
    check("try 1 of 4" in out, "the retry is visible in the log")
    check(not any("ERROR" in u for u in fetched), "the error text was never treated as a URL")

    print("\n[a run ENA really has no FASTQ for fails cleanly after four asks]")
    rc, out, files, fetched = run_method3([f"run_accession\tfastq_ftp\n{ACC}\t\n"] * 4)
    check(rc == 1, "method3 returns 1 so the caller records a failure")
    check(files == [] and fetched == [], "nothing downloaded")
    check("ENA did not return URLs" in out, "says why")

    print("\n[a reply for a different run is not taken as this run's file list]")
    other = TABLE.replace(ACC, "SRR00000001")
    rc, out, files, fetched = run_method3([other, TABLE])
    check(rc == 0 and len(files) == 2 and all(ACC in u for u in fetched),
          "only this accession's row is used")

    print("\n[one mate refused: nothing is left behind, so Download again re-fetches the pair]")
    rc, out, files, fetched = run_method3([TABLE], fail_r1=True)
    check(rc == 1, "method3 reports the accession as failed")
    check(files == [], f"no lone mate left in download/ ({files})")

    print("\n[a run ENA serves as one file named _1 is a single-read run]")
    single = f"run_accession\tfastq_ftp\n{ACC}\tftp.sra.ebi.ac.uk/vol1/fastq/SRR338/087/{ACC}/{ACC}_1.fastq.gz\n"
    rc, out, files, fetched = run_method3([single])
    check(rc == 0, "method3 succeeds")
    check(files == [f"{ACC}.fastq.gz"], f"saved under the single-read name ({files})")
    check(already_have(files) == 0, "and already_have counts it, so the run is reported downloaded")

    print("\n[no download is left under a .part name, and none is written straight to its final name]")
    rc, out, files, fetched = run_method3([TABLE])
    check(not any(f.endswith(".part") for f in files), f"no .part left after a clean run ({files})")
    check('-o "$part"' in function_source("fetch_resumable"), "curl writes to the .part file")

    if shutil.which("curl"):
        print("\n[a transfer that goes quiet halfway is ended and resumed, not waited on]")
        rc, out, files, seen, took = run_fetch("/stall.fastq.gz")
        check(rc == 0, f"fetch_resumable succeeds ({out.strip()[-200:]})")
        check(files.get("out.fastq.gz") == PAYLOAD, "the file is whole and byte-identical")
        check(list(files) == ["out.fastq.gz"], f"no .part left ({sorted(files)})")
        check(len(seen) == 2 and seen[1][1] == f"bytes={len(PAYLOAD) // 2}-",
              f"the second request resumed at the byte the first stopped on ({seen})")
        check("resuming" in out, "the resume is visible in the log")
        check(took < 30, f"ended by the stall limit, not by the server giving up ({took:.0f}s)")

        print("\n[a link that sends nothing fails after three tries and leaves nothing behind]")
        rc, out, files, seen, took = run_fetch("/dead.fastq.gz")
        check(rc == 1, "fetch_resumable reports failure")
        check(files == {}, f"nothing left in download/ ({sorted(files)})")
        check(len(seen) == 3, f"three tries ({len(seen)})")
        check(took < 40, f"it ends ({took:.0f}s)")

        print("\n[a file that is not gzip is discarded, not named as a FASTQ]")
        rc, out, files, seen, took = run_fetch("/damaged.fastq.gz")
        check(rc == 1 and files == {}, f"refused and removed ({sorted(files)})")
        check("gzip test" in out, "says why")
    else:
        print("\nSKIP stall tests: no curl")

    print("\n[already_have: a pair needs both mates]")
    check(already_have([f"{ACC}_1.fastq.gz", f"{ACC}_2.fastq.gz"]) == 0, "both mates: have")
    check(already_have([f"{ACC}_2.fastq.gz"]) == 1, "only mate 2: NOT have (re-download)")
    check(already_have([f"{ACC}_1.fastq.gz"]) == 1, "only mate 1: NOT have (re-download)")
    check(already_have([f"{ACC}.fastq.gz"]) == 0, "single-end file: have")
    check(already_have([]) == 1, "nothing: not have")

    print("\n[the generated script is valid bash, with and without -k]")
    for insecure in (False, True):
        script = sra.build_download_script(Path("/x"), [ACC, "SRR1"], allow_insecure_https=insecure)
        r = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True)
        check(r.returncode == 0, f"bash -n passes (insecure={insecure}) {r.stderr.strip()}")

    print("\nall ENA fallback checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
