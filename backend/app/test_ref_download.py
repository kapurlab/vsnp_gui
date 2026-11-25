"""Download New Reference: the NCBI contact email, and nothing left behind.

On a lab server (2026-11) every download of a reference by accession failed.
The installed vsnp3_download_fasta_gbk_gff_by_acc.py exits at once with "NCBI
requires a contact email address for E-utilities requests" unless NCBI_EMAIL
is in its environment, a session started from the dashboard has none, and the
GUI passed none. Each attempt died in a tenth of a second and left a folder
holding the two template workbooks and download_ref.sh. That folder then held
the name (HTTP 409), so users tried again under other names and collected dead
folders, which the Reference dropdown offered as references. Some typed an
assembly identifier (ASM694v2, GCA_000006945.2), which the script can never
fetch: it queries the nucleotide database, where that genome is NC_003197.2.

Pinned here:
  - the email (config's ncbi block, else NCBI_EMAIL, else typed in the form)
    and the API key reach the download job through its environment; a typed
    email is saved to the config's ncbi block;
  - with no email the request is refused (400) before any folder is made,
    the output folder included, and the message says where to set one;
  - an assembly identifier is refused (400) the same way, and the message
    gives NC_003197.2 as the shape wanted; real nucleotide accessions pass;
  - a download that fails without fetching anything has its folder removed, a
    folder an older version left is cleared on the next attempt, and either
    way the same name downloads the second time;
  - a folder holding a FASTA, GenBank or GFF file, or anything else that the
    download handler did not write, is never removed;
  - the GFF is a step of its own. NCBI's efetch answered HTTP 500 for the GFF
    of NC_003197.2 while its FASTA and GenBank arrived (2026-11-24), and under
    `-fbg` that failed a reference that was complete for Step 1 and Step 2.
    When vsnp3's GFF step fails, the GFF is fetched from NCBI's sequence
    viewer (bin/ref_gff_fallback.py); when that fails too, the job still
    succeeds and a NOTE line in its log says the gene track is missing.

The downloader here is a stand-in script with the installed one's behavior,
and the sequence viewer is a folder of local files, so no test reaches NCBI.

Run directly:  PYTHONPATH=backend <python> backend/app/test_ref_download.py
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Importing app.main reads the config and places the job-log folder. When this
# file is the first to import it, point both at a scratch folder.
_SCRATCH = None
if "app.main" not in sys.modules:
    _SCRATCH = tempfile.mkdtemp(prefix="ref_download_import_")
    os.environ.setdefault("XDG_CONFIG_HOME", str(Path(_SCRATCH) / "config"))
    os.environ.setdefault("VSNP_GUI_SITE_ROOT", str(Path(_SCRATCH) / "site"))

from fastapi import HTTPException  # noqa: E402

from app import main as M  # noqa: E402
from app.jobs import JobManager  # noqa: E402

DOWNLOADER = "vsnp3_download_fasta_gbk_gff_by_acc.py"

# The installed downloader, reduced to what matters here: it refuses without
# NCBI_EMAIL, and -f, -b and -g each write <accession>.fasta, .gbk or .gff
# into the current folder, in that order. An accession NCBI does not know
# (UNKNOWN*) ends in an error with nothing written, PARTIAL* loses the GenBank
# record after the FASTA, and NOGFF* gets efetch's HTTP 500 for the GFF alone.
_FAKE_DOWNLOADER = """#!/bin/bash
acc=""
flags=""
while [ $# -gt 0 ]; do
  case "$1" in
    -a) acc="$2"; shift 2 ;;
    -*) flags="$flags${1#-}"; shift ;;
    *) shift ;;
  esac
done
if [ -z "${NCBI_EMAIL:-}" ]; then
  echo "NCBI requires a contact email address for E-utilities requests" >&2
  exit 1
fi
printf '%s\\n%s\\n' "$NCBI_EMAIL" "${NCBI_API_KEY:-}" > __SEEN__
case "$acc" in
  UNKNOWN*)
    echo "urllib.error.HTTPError: HTTP Error 400: Bad Request" >&2
    exit 1 ;;
esac
case "$flags" in *f*)
  printf '>%s\\nACGT\\n' "$acc" > "$acc.fasta" ;;
esac
case "$flags" in *b*)
  case "$acc" in PARTIAL*)
    echo "the GenBank record could not be fetched" >&2
    exit 1 ;;
  esac
  echo "LOCUS $acc" > "$acc.gbk" ;;
esac
case "$flags" in *g*)
  case "$acc" in NOGFF*)
    echo "urllib.error.HTTPError: HTTP Error 500: Internal Server Error" >&2
    exit 1 ;;
  esac
  printf '##gff-version 3\\n%s\\tvsnp3\\tregion\\t1\\t4\\t.\\t+\\t.\\tID=r\\n' "$acc" > "$acc.gff" ;;
esac
exit 0
"""

# What NCBI's sequence viewer returns: GFF3 for a record it has, and HTTP 200
# with this body for an id it does not know.
_VIEWER_GFF = "##gff-version 3\n##sequence-region {acc} 1 4\n{acc}\tRefSeq\tregion\t1\t4\t.\t+\t.\tID=viewer\n"
_VIEWER_ERROR = "Error: F a i l e d  t o  u n d e r s t a n d  i d :  {acc} \n"


def check(label, cond):
    if not cond:
        raise AssertionError(label)
    print(f"  OK  {label}")


def call(fn, *args):
    """(result, None) or (None, (status, detail))."""
    try:
        return fn(*args), None
    except HTTPException as e:
        return None, (e.status_code, str(e.detail))


class Site:
    """A vsnp3 install with a stand-in downloader, a site reference list, and a
    per-user config held in memory."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.vsnp3 = tmp / "vsnp3"
        (self.vsnp3 / "bin").mkdir(parents=True)
        (self.vsnp3 / "dependencies").mkdir()
        for tpl in ("template_define_filter.xlsx", "template_remove_from_analysis.xlsx"):
            (self.vsnp3 / "dependencies" / tpl).write_bytes(b"template")
        self.seen = tmp / "seen_env.txt"
        script = self.vsnp3 / "bin" / DOWNLOADER
        script.write_text(_FAKE_DOWNLOADER.replace("__SEEN__", str(self.seen)), encoding="utf-8")
        script.chmod(0o755)
        self.site_list = tmp / "site" / "refs" / "vsnp3" / "locations" / "reference_locations.txt"
        self.out = tmp / "new_refs"          # does not exist until a download makes it
        # Stands in for NCBI's sequence viewer: one file per accession it knows.
        self.viewer = tmp / "viewer"
        self.viewer.mkdir()
        self.personal: list = []
        self.ncbi: dict = {}

    def load(self):
        cfg = {"vsnp3_path": str(self.vsnp3), "bcftools_path": "",
               "projects_root": str(self.tmp / "projects"),
               "reference_locations_file": str(self.site_list),
               "reference_locations": list(self.personal)}
        if self.ncbi:
            cfg["ncbi"] = dict(self.ncbi)
        return cfg

    def save(self, cfg):
        self.personal = list(cfg.get("reference_locations") or [])
        self.ncbi = dict(cfg.get("ncbi") or {})

    def seen_env(self):
        """(email, api key) the downloader was started with."""
        email, key = self.seen.read_text().split("\n")[:2]
        return email, key


@contextlib.contextmanager
def site(email: str = "lab@example.org", api_key: str = ""):
    """A Site with main pointed at it, a job manager of its own, and no
    NCBI_EMAIL or NCBI_API_KEY inherited from whoever runs the tests."""
    tmp = Path(tempfile.mkdtemp(prefix="ref_download_")).resolve()
    s = Site(tmp)
    if email:
        s.ncbi["email"] = email
    if api_key:
        s.ncbi["api_key"] = api_key
    saved = (M.load_config, M.save_config, M.job_manager)
    saved_env = {k: os.environ.pop(k, None)
                 for k in ("NCBI_EMAIL", "NCBI_API_KEY", "VSNP_GUI_GFF_FALLBACK_URL")}
    os.environ["VSNP_GUI_GFF_FALLBACK_URL"] = (s.viewer / "{acc}.gff3").as_uri().replace("%7B", "{").replace("%7D", "}")
    M.load_config, M.save_config = s.load, s.save
    M.job_manager = JobManager(tmp / "jobs")
    try:
        yield s
    finally:
        M.load_config, M.save_config, M.job_manager = saved
        for k, v in saved_env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


def download(s: Site, accession: str, name: str = None, email: str = None):
    return call(M.ref_download, M.RefDownloadRequest(
        accession=accession, output_dir=str(s.out), display_name=name, ncbi_email=email))


def finish(job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = M.job_manager.get_job(job_id)
        if job and job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def gone(path: Path, timeout: float = 5.0) -> bool:
    """The job's status turns terminal just before its finalize hook runs."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not path.exists():
            return True
        time.sleep(0.05)
    return not path.exists()


def names(d: Path) -> list:
    return sorted(p.name for p in d.iterdir())


def test_email_and_key_reach_the_job():
    print("[the NCBI email and API key reach the download job]")
    with site(email="lab@example.org", api_key="key-123") as s:
        got, err = download(s, "NC_003197.2")
        check("the download is accepted", err is None and got["accession"] == "NC_003197.2")
        job = finish(got["job_id"])
        check("the job succeeds where the downloader demands NCBI_EMAIL", job["status"] == "succeeded")
        check("the downloader saw the config's email and key", s.seen_env() == ("lab@example.org", "key-123"))
        d = s.out / "NC_003197.2"
        check("FASTA, GenBank and GFF are beside the template workbooks",
              names(d) == ["NC_003197.2.fasta", "NC_003197.2.gbk", "NC_003197.2.gff",
                           "NC_003197.2_define_filter.xlsx",
                           "NC_003197.2_remove_from_analysis.xlsx", "download_ref.sh"])
        log = Path(job["log_path"]).read_text()
        check("neither the email nor the key is written to the script or the job log",
              "lab@example.org" not in log and "key-123" not in log
              and "lab@example.org" not in (d / "download_ref.sh").read_text())

    with site(email="") as s:
        os.environ["NCBI_EMAIL"] = "env@example.org"
        try:
            got, err = download(s, "NC_003197.2")
            check("NCBI_EMAIL in the backend's environment is enough", err is None)
            finish(got["job_id"])
            check("...and no key is invented when none is set", s.seen_env() == ("env@example.org", ""))
        finally:
            os.environ.pop("NCBI_EMAIL", None)

    with site(email="") as s:
        got, err = download(s, "NC_003197.2", email="  typed@example.org ")
        check("an email typed in the form is enough", err is None)
        check("the typed email reaches the job",
              finish(got["job_id"])["status"] == "succeeded" and s.seen_env()[0] == "typed@example.org")
        check("...and is saved to the config's ncbi block", s.ncbi == {"email": "typed@example.org"})
        got, err = download(s, "AF325528.1")
        check("so the next download needs none typed",
              err is None and finish(got["job_id"])["status"] == "succeeded")


def test_no_email_is_refused_and_leaves_nothing():
    print("[no email: refused before any folder is made]")
    with site(email="") as s:
        got, err = download(s, "NC_003197.2", name="Salmonella_LT2")
        check("refused with 400", err is not None and err[0] == 400)
        check("the message says what is missing and where to set it",
              "contact email" in err[1] and "NCBI_EMAIL" in err[1] and "ncbi.email" in err[1])
        check("no reference folder, and no output folder either", not s.out.exists())
        check("no location was added", s.personal == [] and not s.site_list.exists())
        check("no job was started", M.job_manager.list_jobs() == [])

        got, err = download(s, "NC_003197.2", email="not an address")
        check("a typed value that is not an email is refused with 400",
              err is not None and err[0] == 400 and "not an email address" in err[1])
        check("...and nothing is made or saved", not s.out.exists() and s.ncbi == {})


def test_assembly_identifiers_are_refused_and_leave_nothing():
    print("[an assembly identifier: refused before any folder is made]")
    with site() as s:
        for value in ("GCA_000006945.2", "GCF_000006945.2", "ASM694v2", "gca_000006945.2", "asm694v2"):
            got, err = download(s, value, name="Salmonella_LT2")
            check(f"{value} is refused with 400", err is not None and err[0] == 400)
            check("...the message asks for the chromosome's nucleotide accession, like NC_003197.2",
                  "assembly identifier" in err[1] and "nucleotide accession of the chromosome" in err[1]
                  and "NC_003197.2" in err[1])
        check("no folder was made by any of them", not s.out.exists())
        check("no job was started", M.job_manager.list_jobs() == [])
        for value in ("NC_003197.2", "AE006468.2", "AF325528.1", "CP033688", "NZ_CP012345.1",
                      "NC_045512", "ASM694", "GCASEQ1"):
            check(f"{value} is not taken for an assembly identifier",
                  call(M._refuse_assembly_identifier, value)[1] is None)


def test_a_failed_attempt_does_not_hold_the_name():
    print("[a failed download does not hold the name]")
    with site() as s:
        d = s.out / "Salmonella_LT2"
        got, err = download(s, "UNKNOWN0001", name="Salmonella_LT2")
        check("an accession NCBI does not know is accepted as a request", err is None)
        check("...and its job fails", finish(got["job_id"])["status"] == "failed")
        check("the folder of the failed attempt is removed", gone(d))
        check("so the dropdown offers no reference of that name",
              [r["name"] for r in M.list_references(s.load())] == [])
        got, err = download(s, "NC_003197.2", name="Salmonella_LT2")
        check("the same name is accepted again", err is None)
        check("...and this time it downloads",
              finish(got["job_id"])["status"] == "succeeded" and (d / "NC_003197.2.fasta").is_file())
        got, err = download(s, "NC_003197.2", name="Salmonella_LT2")
        check("a reference that did download holds its name (409)", err is not None and err[0] == 409)

    print("[a folder an earlier version left is cleared on the next attempt]")
    with site() as s:
        d = s.out / "Salmonella_LT2"
        d.mkdir(parents=True)
        # What the dead attempts left: the first accession's workbooks and the script.
        (d / "ASM694v2_define_filter.xlsx").write_bytes(b"template")
        (d / "ASM694v2_remove_from_analysis.xlsx").write_bytes(b"template")
        (d / "download_ref.sh").write_text("#!/bin/bash\n")
        M.ref_path_add(M.RefPathRequest(path=str(s.out)))
        check("it is listed as a reference until then",
              [r["name"] for r in M.list_references(s.load())] == ["Salmonella_LT2"])
        got, err = download(s, "NC_003197.2", name="Salmonella_LT2")
        check("the name is accepted", err is None)
        check("the download succeeds", finish(got["job_id"])["status"] == "succeeded")
        check("the folder holds this attempt's files only",
              names(d) == ["NC_003197.2.fasta", "NC_003197.2.gbk", "NC_003197.2.gff",
                           "NC_003197.2_define_filter.xlsx",
                           "NC_003197.2_remove_from_analysis.xlsx", "download_ref.sh"])


def test_a_folder_with_sequence_is_never_removed():
    print("[a folder holding a sequence file is never removed]")
    with site() as s:
        d = s.out / "PARTIAL0001"
        got, err = download(s, "PARTIAL0001")
        check("a download that fetched the FASTA and then failed is a failed job",
              err is None and finish(got["job_id"])["status"] == "failed")
        time.sleep(0.5)   # the finalize hook has had its chance
        check("its folder and its FASTA are kept", (d / "PARTIAL0001.fasta").is_file())
        got, err = download(s, "PARTIAL0001")
        check("and a second attempt is refused (409) instead of clearing it",
              err is not None and err[0] == 409 and (d / "PARTIAL0001.fasta").is_file())

        def made(name: str, files, subdir: str = "") -> Path:
            f = s.tmp / "shapes" / name
            f.mkdir(parents=True)
            for n in files:
                (f / n).write_text("x")
            if subdir:
                (f / subdir).mkdir()
            return f

        left = ["X_define_filter.xlsx", "X_remove_from_analysis.xlsx", "download_ref.sh"]
        check("workbooks and script alone: a failed download",
              M._is_failed_download_dir(made("dead", left)))
        suffixes = (".fasta", ".fa", ".fna", ".gbk", ".gb", ".gff", ".gff3", ".FASTA")
        for i, suffix in enumerate(suffixes):
            f = made(f"seq{i}", left + [f"X{suffix}"])
            check(f"with a {suffix} file: not removed",
                  not M._is_failed_download_dir(f) and not M._remove_failed_download_dir(f)
                  and (f / f"X{suffix}").is_file())
        check("with any other file: not removed",
              not M._remove_failed_download_dir(made("notes", left + ["notes.txt"])))
        check("with another workbook: not removed",
              not M._remove_failed_download_dir(made("book", left + ["X_metadata.xlsx"])))
        check("with a subfolder: not removed",
              not M._remove_failed_download_dir(made("sub", left, subdir="star")))
        check("without download_ref.sh (not made by a download): not removed",
              not M._remove_failed_download_dir(made("hand", left[:2])))
        check("an empty folder: not removed", not M._remove_failed_download_dir(made("empty", [])))
        check("a folder that is not there: not removed",
              not M._remove_failed_download_dir(s.tmp / "shapes" / "absent"))


def test_a_running_download_keeps_its_folder():
    print("[a second request does not clear a download that is still running]")
    with site() as s:
        d = s.out / "NC_003197.2"
        d.mkdir(parents=True)
        (d / "NC_003197.2_define_filter.xlsx").write_bytes(b"template")
        (d / "download_ref.sh").write_text("#!/bin/bash\n")

        class Running:
            def list_jobs(self):
                return [{"name": "ref_download", "status": "running", "cwd": str(d)}]

        M.job_manager = Running()
        got, err = download(s, "NC_003197.2")
        check("refused with 409 while the first is running",
              err is not None and err[0] == 409 and "still running" in err[1])
        check("...and its folder is untouched",
              names(d) == ["NC_003197.2_define_filter.xlsx", "download_ref.sh"])


def test_a_gff_failure_does_not_fail_the_reference():
    print("[the GFF fails at efetch: fetched from the sequence viewer instead]")
    with site() as s:
        (s.viewer / "NOGFF0001.gff3").write_text(_VIEWER_GFF.format(acc="NOGFF0001"))
        d = s.out / "NOGFF0001"
        got, err = download(s, "NOGFF0001")
        job = finish(got["job_id"]) if err is None else {}
        check("the download succeeds", job.get("status") == "succeeded")
        check("FASTA and GenBank came through vsnp3",
              (d / "NOGFF0001.fasta").is_file() and (d / "NOGFF0001.gbk").is_file())
        check("the GFF is the sequence viewer's",
              (d / "NOGFF0001.gff").read_text() == _VIEWER_GFF.format(acc="NOGFF0001"))
        log = Path(job["log_path"]).read_text()
        check("the log says where the GFF came from, and carries no NOTE",
              "fetched from NCBI's sequence viewer" in log and "NOTE:" not in log)

    print("[the GFF fails everywhere: the reference is kept, with a note]")
    with site() as s:
        (s.viewer / "NOGFF0002.gff3").write_text(_VIEWER_ERROR.format(acc="NOGFF0002"))
        got, err = download(s, "NOGFF0002", name="Salmonella_LT2")
        d = s.out / "Salmonella_LT2"
        job = finish(got["job_id"]) if err is None else {}
        check("the download still succeeds", job.get("status") == "succeeded")
        check("the folder holds the FASTA and GenBank, and no GFF or partial GFF",
              names(d) == ["NOGFF0002.fasta", "NOGFF0002.gbk", "NOGFF0002_define_filter.xlsx",
                           "NOGFF0002_remove_from_analysis.xlsx", "download_ref.sh"])
        log = Path(job["log_path"]).read_text()
        check("the log's NOTE says the reference is usable and what is missing",
              "NOTE: NOGFF0002 was downloaded without its GFF annotation" in log
              and "complete for Step 1 and Step 2" in log and "gene track in IGV" in log)
        check("the viewer's error body is quoted as the reason", "the reply is not GFF3 (Error:" in log)
        check("the reference is offered",
              [r["name"] for r in M.list_references(s.load())] == ["Salmonella_LT2"])
        got, err = download(s, "NOGFF0002", name="Salmonella_LT2")
        check("...and holds its name (409)", err is not None and err[0] == 409)

    print("[what counts as GFF3 from the sequence viewer]")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
    import ref_gff_fallback as F
    check("a GFF3 with a feature is accepted", F.problem_with(_VIEWER_GFF.format(acc="X")) == "")
    check("the viewer's error body is refused",
          F.problem_with(_VIEWER_ERROR.format(acc="X")).startswith("the reply is not GFF3"))
    check("an empty reply is refused", "an empty reply" in F.problem_with(""))
    check("a header with no feature is refused",
          F.problem_with("##gff-version 3\n##sequence-region X 1 4\n") == "the GFF3 holds no feature")
    check("the default address is NCBI's viewer, asked for GFF3 of one record",
          F.DEFAULT_URL.startswith("https://www.ncbi.nlm.nih.gov/sviewer/viewer.cgi?")
          and "report=gff3" in F.DEFAULT_URL and "id={acc}" in F.DEFAULT_URL)


TESTS = [
    test_email_and_key_reach_the_job,
    test_no_email_is_refused_and_leaves_nothing,
    test_assembly_identifiers_are_refused_and_leave_nothing,
    test_a_failed_attempt_does_not_hold_the_name,
    test_a_folder_with_sequence_is_never_removed,
    test_a_running_download_keeps_its_folder,
    test_a_gff_failure_does_not_fail_the_reference,
]


def main() -> int:
    for t in TESTS:
        t()
    print("\nAll reference download tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
