"""Step 1 Results and IGV work for samples aligned by vSNP v1.

vSNP v1 (2017-2019) wrote each sample's stats as <sample>_<YYYY-MM-DD_HH-MM-SS>.xlsx,
with its own column names (sample_name, ave_coverage, good_snp_count, ...),
parked the reads in zips/, and left its BAM in a plain alignment/ as
<sample>-nodup.bam. The Results scan read vsnp3's *_stats.xlsx only, so a
project aligned back then (the Ames brucella/suis2 project, 2018) listed every
sample as Complete in Step 1 and showed "No stats loaded yet." in Step 1
Results. IGV looked for vsnp3's <sample>_nodup.bam only, so it had no reads to
show for any of them.

These tests pin what the scan now does with those workbooks, and what it does
not do:

  * a v1 workbook is read, renamed or not, with the columns that measure what
    vsnp3's do shown under vsnp3's names, and everything else kept for the
    exports;
  * nothing v1 recorded reaches Reference, which reference_lock reads to
    rewrite project.json;
  * a vsnp3 row is exactly what it was, and a timestamped workbook that is not
    v1's is ignored rather than read as the newest run;
  * an index written before this change re-lists only v1-shaped folders;
  * IGV opens a v1 sample's BAM, a vsnp3 BAM still wins wherever there is one,
    and deciding which table rows get reads costs no more filesystem calls
    than it did, on any shape of folder.

Run from anywhere with the checkout's python:

    env/bin/python backend/app/test_step1_results_vsnp1.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook  # noqa: E402

from app import main as m  # noqa: E402
from app import qc_verdict  # noqa: E402
from app import step1_index as si  # noqa: E402
from app.stats_workbooks import is_stats_workbook  # noqa: E402

FAILURES: list[str] = []

# The header v1's functions.py writes (align_reads, and the run summary in
# loop mode), in its order.
V1_HEADER = ["time_stamp", "sample_name", "species", "reference_sequence_name", "R1size", "R2size",
             "Q_ave_R1", "Q_ave_R2", "Q30_R1", "Q30_R2", "allbam_mapped_reads", "genome_coverage",
             "ave_coverage", "ave_read_length", "unmapped_reads", "unmapped_assembled_contigs",
             "good_snp_count", "mlst_type", "octalcode", "sbcode", "hexadecimal_code", "binarycode"]


def check(actual, expected, label):
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")
        print(f"  FAIL  {label}: expected {expected!r}, got {actual!r}")
    else:
        print(f"  OK    {label}")


def workbook(path: Path, header: list, *rows: list) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def v1_values(sample: str, stamp: str, depth: str = "85.3", snps: int = 1234) -> list:
    """One sample's row as v1 wrote it: strings from its format calls, the SNP
    and contig counts as numbers."""
    return [stamp, sample, "suis2", "NC_010169.1", "151.2MB", "160.4MB", "34.1", "33.0", "92.3%",
            "88.5%", "1,893,201", "99.87%", depth, "150.0", 4021, 12, snps, "MLST type 13",
            "N/A", "N/A", "N/A", "N/A"]


V1_REF = "NC_010169-NC_010167"


def v1_sample(s1: Path, name: str, stamp: str, *, filename: str = "", bam: bool = True, **kw) -> Path:
    """A sample folder as v1 left it: reads in zips/, and in alignment/ what
    functions.py moved there: both BAMs with their indexes, the VCFs, and the
    reference FASTA it aligned to with its .fai."""
    d = s1 / name
    (d / "zips").mkdir(parents=True)
    (d / "zips" / f"{name}_S1_L001_R1_001.fastq.gz").write_bytes(b"\x1f\x8b")
    (d / "zips" / f"{name}_S1_L001_R2_001.fastq.gz").write_bytes(b"\x1f\x8b")
    a = d / "alignment"
    a.mkdir()
    (a / f"{name}_zc.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    (a / f"{name}-hapall.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    if bam:
        for b in ("nodup", "sorted"):
            (a / f"{name}-{b}.bam").write_bytes(b"BAM\x01")
            (a / f"{name}-{b}.bam.bai").write_bytes(b"BAI\x01")
    (a / f"{V1_REF}.fasta").write_text(">NC_010169.1\nACGT\n>NC_010167.1\nACGT\n", encoding="utf-8")
    (a / f"{V1_REF}.fasta.fai").write_text("NC_010169.1\t4\t13\t4\t5\nNC_010167.1\t4\t31\t4\t5\n", encoding="utf-8")
    (d / "unmapped").mkdir()
    for f in ("best_reference.txt", "mlst.txt", "version_capture.txt"):
        (d / f).write_text("x\n", encoding="utf-8")
    workbook(d / (filename or f"{name}_{stamp}.xlsx"), V1_HEADER, v1_values(name, stamp, **kw))
    return d


VSNP3_HEADER = ["sample", "date", "Reference", "R1 Ave Length", "R2 Ave Length", "Unmapped Percent",
                "Average Depth", "Genome with Coverage", "Quality SNPs"]


def vsnp3_sample(s1: Path, name: str) -> Path:
    d = s1 / name
    d.mkdir(parents=True)
    (d / f"{name}_S2_L001_R1_001.fastq.gz").write_bytes(b"\x1f\x8b")
    (d / f"{name}_S2_L001_R2_001.fastq.gz").write_bytes(b"\x1f\x8b")
    workbook(d / f"{name}_2026-03-01_stats.xlsx", VSNP3_HEADER,
             [name, "2026-03-01_09-00-00", "Brucella_suis2", "150.1", "149.8", "0.4%", "120.5",
              "99.91%", "1,301"])
    return d


def scan_rows(step1: Path, **kw) -> dict:
    return {r["_sample"]: r for r in m._qc_run_scanner(step1, **kw)}


def test_names():
    print("which names are stats workbooks")
    for name, want in [("A_2026-03-01_stats.xlsx", True), ("A_2018-04-18_17-47-14.xlsx", True),
                       ("A_2018-04-18_17-47-14_stats.xlsx", True), ("A_2018-04-18.xlsx", False),
                       ("A_2018-04-18_17-47-14.xls", False), ("A_2018-4-18_17-47-14.xlsx", False),
                       ("A_fastq_stats.txt", False), ("alignment", False), ("A.xlsx", False)]:
        check(is_stats_workbook(name), want, name)


def test_scan(root: Path):
    print("the Results scan")
    proj = root / "suis2"
    s1 = proj / "step1"
    v1_sample(s1, "18-006620-BS-00-001", "2018-04-18_17-47-14")
    v1_sample(s1, "BsuisF7-06-2", "2018-04-18_17-55-23", filename="BsuisF7-06-2_2018-04-18_17-55-23_stats.xlsx")
    v1_sample(s1, "B-thomsen", "2018-04-18_17-05-24", depth="8.2", snps=0)
    vsnp3_sample(s1, "V3")
    # A vsnp3 sample with some other timestamped workbook beside its stats: it
    # is newer, and must not be read as the newest run.
    vsnp3_sample(s1, "NOISE")
    workbook(s1 / "NOISE" / "NOISE_2026-09-30_10-00-00.xlsx", ["sample", "taxon", "reads"],
             ["NOISE", "Brucella", 5])
    # v1's run summary at the top of step1/: one row per sample, same header.
    workbook(s1 / "stat_alignment_summary_2018-04-18_17-05-24.xlsx", V1_HEADER,
             v1_values("18-006620-BS-00-001", "2018-04-18_17-05-24", depth="1.0"),
             v1_values("B-thomsen", "2018-04-18_17-05-24", depth="1.0"))
    (proj / "project.json").write_text(json.dumps({"name": "suis2", "reference": "Brucella_suis2"}))

    si.invalidate(s1)
    rows = scan_rows(s1, index=si.stats_sigs(s1))
    check(sorted(rows), ["18-006620-BS-00-001", "B-thomsen", "BsuisF7-06-2", "NOISE", "V3"],
          "every sample has a row, the v1 ones included")
    a = rows["18-006620-BS-00-001"]
    check({k: a.get(k) for k in ("sample", "Average Depth", "Genome with Coverage", "Quality SNPs",
                                  "R1 Ave Length", "read_type", "_run_date", "_stats_format")},
          {"sample": "18-006620-BS-00-001", "Average Depth": "85.3", "Genome with Coverage": "99.87%",
           "Quality SNPs": "1,234", "R1 Ave Length": "150.0", "read_type": "paired",
           "_run_date": "2018-04-18T17:47:14", "_stats_format": "vSNP v1"},
          "a v1 row carries its measures under the vsnp3 names, its run date and read type")
    check(any(k in a for k in ("Reference", "ave_coverage", "sample_name", "good_snp_count")), False,
          "and nothing under Reference, nor the v1 names it was given")
    check((a.get("species"), a.get("reference_sequence_name"), a.get("Q30_R1"), a.get("allbam_mapped_reads")),
          ("suis2", "NC_010169.1", "92.3%", "1,893,201"),
          "v1's other columns are kept, as they are, for the exports")
    check(rows["B-thomsen"].get("Quality SNPs"), "0", "a SNP count of 0 reads 0, as vsnp3 writes it")
    check(rows["BsuisF7-06-2"].get("Average Depth"), "85.3", "a v1 workbook renamed to *_stats.xlsx reads the same")
    check(rows["BsuisF7-06-2"].get("_run_date"), "2018-04-18T17:55:23", "with its run date")
    check(rows["NOISE"].get("Reference"), "Brucella_suis2", "a timestamped workbook that is not v1's is ignored")
    check("_stats_format" in rows["V3"], False, "a vsnp3 row is not marked")

    thresholds = qc_verdict.merge_thresholds()
    check(qc_verdict.compute_verdict(a, thresholds)["level"], "pass", "the QC verdict reads a v1 depth")
    check(qc_verdict.compute_verdict(rows["B-thomsen"], thresholds)["level"], "fail",
          "and fails a v1 sample at 8.2x")

    discovered = scan_rows(s1)
    check(discovered, rows, "discovering the workbooks gives the rows the index does")
    direct = scan_rows(s1, direct=True)
    check(direct["18-006620-BS-00-001"]["_file"].endswith("18-006620-BS-00-001_2018-04-18_17-47-14.xlsx"), True,
          "a post-hoc scan of step1/ does not read v1's run summary as a sample")
    check(direct["B-thomsen"].get("Average Depth"), "8.2", "for any of its samples")
    return proj


def test_reference_lock(root: Path, proj: Path):
    print("the project reference")
    cfg = {"projects_root": str(root), "vsnp3_path": str(root / "no_refs")}
    m.load_config = lambda: cfg
    m._project_dir_for = lambda c, p: proj
    m._QC_SCANS.clear()
    lock = m.reference_lock("suis2")
    check(lock["references"], ["Brucella_suis2"], "only the vsnp3 rows' reference is counted")
    check(json.loads((proj / "project.json").read_text())["reference"], "Brucella_suis2",
          "project.json keeps its reference")
    only_v1 = root / "only_v1"
    v1_sample(only_v1 / "step1", "S1", "2018-04-18_17-47-14")
    (only_v1 / "project.json").write_text(json.dumps({"name": "only_v1", "reference": "Brucella_suis2"}))
    m._project_dir_for = lambda c, p: only_v1
    m._QC_SCANS.clear()
    check(m.reference_lock("only_v1")["references"], [], "a project of v1 rows only offers no reference")
    check(json.loads((only_v1 / "project.json").read_text())["reference"], "Brucella_suis2",
          "and its project.json is left alone")
    m._project_dir_for = lambda c, p: proj


def test_stats_button(proj: Path):
    print("the Stats button")
    s1 = proj / "step1"
    check(m._latest_step1_stats("suis2", "18-006620-BS-00-001").name, "18-006620-BS-00-001_2018-04-18_17-47-14.xlsx",
          "a v1 sample opens its own workbook")
    check(m._latest_step1_stats("suis2", "V3").name, "V3_2026-03-01_stats.xlsx", "a vsnp3 sample opens its workbook")
    check(m._latest_step1_stats("suis2", "NOISE").name, "NOISE_2026-03-01_stats.xlsx",
          "and never the other timestamped workbook beside it")
    workbook(s1 / "B-thomsen" / "B-thomsen_2026-03-02_stats.xlsx", VSNP3_HEADER,
             ["B-thomsen", "2026-03-02_09-00-00", "Brucella_suis2", "150", "150", "0.1%", "90", "99%", "7"])
    check(m._latest_step1_stats("suis2", "B-thomsen").name, "B-thomsen_2026-03-02_stats.xlsx",
          "a v1 sample re-run with vsnp3 opens the vsnp3 workbook")
    m._QC_SCANS.clear()
    si.invalidate(s1)
    rows = scan_rows(s1, index=si.stats_sigs(s1))
    check((rows["B-thomsen"]["Reference"], "_stats_format" in rows["B-thomsen"]), ("Brucella_suis2", False),
          "and its Results row is the vsnp3 run, the newer one")


class Listings:
    """Which directories os.scandir lists, in every thread."""

    def __init__(self):
        self.paths: list = []
        self._lock = threading.Lock()

    def __enter__(self):
        self._real = os.scandir

        def counted(path=".", _real=self._real):
            with self._lock:
                self.paths.append(os.fspath(path))
            return _real(path)
        os.scandir = counted
        return self

    def __exit__(self, *exc):
        os.scandir = self._real


def test_index_migration(root: Path):
    """An index written before this change is trusted for every folder except
    the ones shaped like a v1 sample, which are listed once to find the
    workbook it could not see."""
    print("an index written by version 1")
    proj = root / "migrate"
    s1 = proj / "step1"
    v1_sample(s1, "OLD", "2018-04-18_17-47-14")
    vsnp3_sample(s1, "DONE")
    pending = s1 / "PENDING"
    pending.mkdir()
    (pending / "PENDING_R1_001.fastq.gz").write_bytes(b"\x1f\x8b")
    old = time.time() - 60
    for p in sorted(s1.rglob("*"), key=lambda p: -len(p.parts)):
        os.utime(p, (old, old), follow_symlinks=False)
    os.utime(s1, (old, old))

    si.invalidate(s1)
    si._FACTS_MEMO.clear()
    fresh = si.facts(s1)
    check(sorted(fresh["OLD"]["stats"]), ["OLD_2018-04-18_17-47-14.xlsx"], "a fresh index records the v1 workbook")

    # The same folders as version 1 recorded them: it saw no workbook in OLD.
    v1 = {k: [os.stat(s1 / k).st_mtime_ns, dict(v)] for k, v in fresh.items()}
    v1["OLD"][1]["stats"] = {}
    (s1 / si.INDEX_BASENAME).write_text(json.dumps({"version": 1, "samples": v1}))
    si.invalidate(s1)
    si._FACTS_MEMO.clear()
    si.listing(s1)  # the listing of step1/ itself is not what is being counted
    with Listings() as seen:
        migrated = si.facts(s1)
    check(sorted(Path(p).name for p in seen.paths), ["OLD"], "only the v1-shaped folder is listed again")
    check(migrated, fresh, "and the answers are the ones a fresh index gives")
    si.invalidate(s1)
    si._FACTS_MEMO.clear()
    check(json.loads((s1 / si.INDEX_BASENAME).read_text())["version"], si._INDEX_VERSION,
          "the index is written back in the new version")
    si.listing(s1)
    with Listings() as seen:
        si.facts(s1)
    check(seen.paths, [], "after which nothing is listed again")


class Calls:
    """os.stat / lstat / scandir / listdir calls, in every thread."""

    def __init__(self):
        self.n = 0
        self._lock = threading.Lock()
        self._saved = {}

    def __enter__(self):
        for name in ("stat", "lstat", "scandir", "listdir"):
            real = getattr(os, name)
            self._saved[name] = real

            def counted(*a, _real=real, **k):
                with self._lock:
                    self.n += 1
                return _real(*a, **k)
            setattr(os, name, counted)
        return self

    def __exit__(self, *exc):
        for name, real in self._saved.items():
            setattr(os, name, real)


def old_confirm(step1_dir: Path, name: str, name_set: set, by_prefix: dict) -> bool:
    """_igv_bam_stems' confirm before v1 BAMs counted, verbatim."""
    if name in name_set and m._AlignListing(step1_dir / name).glob(f"{name}_nodup.bam"):
        return True
    return any(m._AlignListing(step1_dir / d).glob(f"{name}_nodup.bam")
               for d in by_prefix.get(name, ()))


def vsnp3_alignment(d: Path, name: str, *, bam: bool = True) -> Path:
    a = d / "alignment_Brucella_suis2"
    a.mkdir(parents=True)
    (a / f"{name}_zc.vcf").write_text("##fileformat=VCFv4.2\n", encoding="utf-8")
    if bam:
        (a / f"{name}_nodup.bam").write_bytes(b"BAM\x01")
        (a / f"{name}_nodup.bam.bai").write_bytes(b"BAI\x01")
    (a / "Brucella_suis2.fasta").write_text(">NC_010169.1\nACGT\n", encoding="utf-8")
    (a / "Brucella_suis2.fasta.fai").write_text("NC_010169.1\t4\t13\t4\t5\n", encoding="utf-8")
    return a


def test_igv(root: Path):
    print("IGV")
    proj = root / "igv"
    s1 = proj / "step1"
    v1_sample(s1, "V1", "2018-04-18_17-47-14")
    v1_sample(s1, "V1NOBAM", "2018-04-18_17-49-02", bam=False)
    vsnp3_alignment(vsnp3_sample(s1, "V3"), "V3")
    vsnp3_alignment(v1_sample(s1, "BOTH", "2018-04-18_17-05-24"), "BOTH")   # re-run with vsnp3
    vsnp3_alignment(vsnp3_sample(s1, "FAILED"), "FAILED", bam=False)
    vsnp3_alignment(vsnp3_sample(s1, "S9_S4_L001"), "S9")                    # lane-suffixed folder
    (proj / "project.json").write_text(json.dumps({"name": "igv", "reference": "Brucella_suis2"}))
    cfg = {"projects_root": str(root), "vsnp3_path": str(root / "no_refs")}
    m.load_config = lambda: cfg
    m._project_dir_for = lambda c, p: proj

    f = m.step1_files("igv", sample="V1")
    check({k: f[k].replace(str(s1) + "/", "") for k in ("bam", "alignment_dir", "reference_fasta", "source_vcf")},
          {"bam": "V1/alignment/V1-nodup.bam", "alignment_dir": "V1/alignment",
           "reference_fasta": f"V1/alignment/{V1_REF}.fasta", "source_vcf": "V1/alignment/V1_zc.vcf"},
          "IGV opens a v1 sample's BAM, with the reference it was aligned to and its calls")
    check(str(m._sample_bam(proj, "V1")), f["bam"], "and the reads window serves the same BAM")
    check(m.step1_files("igv", sample="V3")["bam"].replace(str(s1) + "/", ""),
          "V3/alignment_Brucella_suis2/V3_nodup.bam", "a vsnp3 sample opens its BAM as before")
    check(m.step1_files("igv", sample="BOTH")["bam"].replace(str(s1) + "/", ""),
          "BOTH/alignment_Brucella_suis2/BOTH_nodup.bam", "a v1 sample re-run with vsnp3 opens the vsnp3 BAM")
    check((m.step1_files("igv", sample="V1NOBAM")["bam"], m._sample_bam(proj, "V1NOBAM")), ("", None),
          "a v1 folder without a BAM still has none")

    si.invalidate(s1)
    stems = m._igv_bam_stems(s1)
    names = ["V1", "V1NOBAM", "V3", "BOTH", "FAILED", "S9", "S9_S4_L001", "NOT_A_FOLDER"]
    check({n: n in stems for n in names},
          {"V1": True, "V1NOBAM": False, "V3": True, "BOTH": True, "FAILED": False, "S9": True,
           "S9_S4_L001": False, "NOT_A_FOLDER": False},
          "Step 2 table rows get reads for v1 samples, and the same answers as before for the rest")
    regular = [s.name for s in si.listing(s1).regular()]
    by_prefix: dict = {}
    for n in regular:
        if "_" in n:
            by_prefix.setdefault(n.split("_")[0], []).append(n)
    old_calls, new_calls, old_answers = {}, {}, {}
    for n in names:
        with Calls() as c:
            old_answers[n] = old_confirm(s1, n, set(regular), by_prefix)
        old_calls[n] = c.n
        with Calls() as c:
            stems._confirm(n)
        new_calls[n] = c.n
    check({n: a for n, a in old_answers.items() if n != "V1"}, {n: n in stems for n in names if n != "V1"},
          "only the v1 sample's answer changed")
    check(new_calls, old_calls, f"and every row costs the filesystem calls it did ({sum(new_calls.values())} in all)")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="vsnp1_results_"))
    try:
        test_names()
        proj = test_scan(tmp)
        test_reference_lock(tmp, proj)
        test_stats_button(proj)
        test_index_migration(tmp)
        test_igv(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("\nall passed")


if __name__ == "__main__":
    main()
