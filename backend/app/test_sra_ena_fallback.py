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

Run directly:  python test_sra_ena_fallback.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
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
    --retry|--retry-delay) shift 2;;
    -*) shift;;
    *) url="$1"; shift;;
  esac
done
case "$url" in
  *ena/portal/api/filereport*)
    n=$(ls "$REPLIES" | sort | head -1)
    [ -n "$n" ] || exit 0
    cat "$REPLIES/$n"; rm -f "$REPLIES/$n";;
  *)
    echo "$url" >> "$FETCHED"; echo fastq > "$out";;
esac
"""


def method3_source() -> str:
    script = sra.build_download_script(Path("/nonexistent"), [ACC], allow_insecure_https=False)
    m = re.search(r"^method3\(\) \{\n.*?^\}\n", script, re.S | re.M)
    if not m:
        raise AssertionError("method3() not found in the generated script")
    return m.group(0)


def run_method3(replies: list[str]) -> tuple[int, str, list[str], list[str]]:
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
            "set -u\nCAN_METHOD3=1\nsleep() { :; }\n" + method3_source()
            + f'\nmethod3 "{ACC}"\n'
        )
        env = dict(os.environ, PATH=f"{tmp/'bin'}:{os.environ['PATH']}",
                   REPLIES=str(tmp / "replies"), FETCHED=str(tmp / "fetched"))
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

    print("\nall ENA fallback checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
