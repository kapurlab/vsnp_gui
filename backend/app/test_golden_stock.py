"""Golden outputs of a stock project (no pathogen add-on), pinned before the
pathogen add-on work began.

The add-on framework changes Step 1 dispatch, the Results rows and the
reference list. For a project that names no pathogen, none of that may move:
the batch script, the Results rows, the CSV export and the reference list must
stay byte-identical to what main produced. This test compares each against a
file in backend/app/golden/.

A change that is MEANT to alter stock output regenerates the goldens on
purpose, and the diff is reviewed like code:

    env/bin/python backend/app/test_golden_stock.py --update

Run:  env/bin/python backend/app/test_golden_stock.py
"""
from __future__ import annotations

import gzip
import json
import os
import random
import shutil
import sys
import tempfile
from pathlib import Path

# Isolate from the real ~/.config/vsnp_gui and site BEFORE app.main is
# imported: importing it loads the config and creates the jobs directory.
_TMP = Path(tempfile.mkdtemp(prefix="golden_stock_")).resolve()
os.environ["XDG_CONFIG_HOME"] = str(_TMP / "xdg")
os.environ["VSNP_GUI_SITE_ROOT"] = str(_TMP / "site")
for _k in ("BDTOOLS_SITE_ROOT", "BDTOOLS_TOOLS_ROOT", "BDTOOLS_DEFAULT_PATHOGEN"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook  # noqa: E402

from app import main as m  # noqa: E402
from app import addons  # noqa: E402

# No pathogen add-on is visible to a stock project's goldens, whatever this
# machine has installed beside the checkout.
addons.ROOTS_OVERRIDE = []

GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = "--update" in sys.argv
FAILED = 0

STATS_HEADER = [
    "sample", "date", "FASTA/s", "Reference", "Found_Reference_Set", "FASTQ_R1",
    "R1 File Size", "R1 Read Count", "R1 Length Sum", "R1 Min Length", "R1 Ave Length",
    "R1 Max Length", "R1 Passing Q20", "R1 Passing Q30", "R1 Read Quality Ave",
    "FASTQ_R2", "R2 File Size", "R2 Read Count", "R2 Length Sum", "R2 Min Length",
    "R2 Ave Length", "R2 Max Length", "R2 Passing Q20", "R2 Passing Q30",
    "R2 Read Quality Ave", "Groups", "Aligner", "Mapped Paired Reads",
    "Mapped Single Reads", "Unmapped Reads", "Unmapped Percent",
    "Unmapped Assembled Contigs", "Duplicate Paired Reads", "Duplicate Single Reads",
    "Duplicate Percent of Mapped Reads", "BAM/Reference File", "Reference Length",
    "Genome with Coverage", "Average Depth", "No Coverage Bases",
    "Percent Ref with Zero Coverage", "Ambiguous SNPs", "Quality SNPs",
]


def check(label: str, cond: bool) -> None:
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


def norm(text: str) -> str:
    return text.replace(str(_TMP), "<TMP>")


def golden(name: str, actual: str) -> None:
    path = GOLDEN / name
    # Bytes, not text: the CSV export ends rows with \r\n and text mode would
    # translate them on the way back in.
    if UPDATE:
        GOLDEN.mkdir(exist_ok=True)
        path.write_bytes(actual.encode("utf-8"))
        print(f"  WROTE {path.name}")
        return
    want = path.read_bytes().decode("utf-8") if path.exists() else None
    check(f"{name} matches the golden copy", want == actual)
    if want is not None and want != actual:
        import difflib
        for line in list(difflib.unified_diff(want.splitlines(), actual.splitlines(),
                                              "golden", "now", lineterm=""))[:40]:
            print("      " + line)


def make_ref(root: Path, name: str, contig: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.fasta").write_text(f">{contig}\nACGTACGTAC\n", encoding="utf-8")
    (d / f"{name}.gbk").write_text("LOCUS x\n", encoding="utf-8")


def fastq(path: Path, length: int, n: int = 40) -> None:
    rnd = random.Random(path.name)
    lines = []
    for i in range(n):
        seq = "".join(rnd.choice("ACGT") for _ in range(length))
        lines += [f"@r{i}", seq, "+", "I" * length]
    path.write_bytes(gzip.compress(("\n".join(lines) + "\n").encode(), mtime=0))


def stats_workbook(path: Path, values: dict) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(STATS_HEADER)
    ws.append([values.get(h, "") for h in STATS_HEADER])
    wb.save(path)


class Fixture:
    def __init__(self) -> None:
        self.locs = _TMP / "ref locs"
        self.second = _TMP / "second"
        make_ref(self.locs, "Mycobacterium_AF2122", "NC_002945.4")
        make_ref(self.locs, "Brucella_suis1", "NC_017251.1")
        make_ref(self.second, "Mycobacterium_AF2122", "NC_002945.4")   # shadowed
        make_ref(self.second, "mtbc0_v1.1", "MTBC0")
        self.vsnp3 = _TMP / "vsnp3"
        (self.vsnp3 / "bin").mkdir(parents=True)
        (self.vsnp3 / "bin" / "vsnp3_step1.py").write_text("#!/usr/bin/env python\n")
        (self.vsnp3 / "bin" / "vsnp3_step2.py").write_text("#!/usr/bin/env python\n")
        (self.vsnp3 / "dependencies").mkdir()
        (self.vsnp3 / "dependencies" / "reference_options_paths.txt").write_text(
            f"{self.locs}\n{self.second}\n")
        self.projects = _TMP / "projects"
        self.cfg = {"vsnp3_path": str(self.vsnp3), "projects_root": str(self.projects),
                    "reference_locations": [str(self.locs), str(self.second)],
                    "step1_max_parallel": 2}

    def project(self, name: str) -> Path:
        p = self.projects / name
        a = p / "step1" / "TX-0001"
        a.mkdir(parents=True)
        fastq(a / "TX-0001_S1_L001_R1_001.fastq.gz", 150)
        fastq(a / "TX-0001_S1_L001_R2_001.fastq.gz", 150)
        b = p / "step1" / "ONT-07"
        b.mkdir(parents=True)
        fastq(b / "ONT-07.fastq.gz", 1200, n=8)
        return p


def test_scripts(fx: Fixture) -> None:
    print("[Step 1 batch script, stock project]")
    p = fx.project("with_ref")
    m.step1_run("with_ref", m.Step1Request(reference="Mycobacterium_AF2122"))
    golden("stock_run_step1_reference.sh", norm((p / "step1" / "run_step1.sh").read_text()))
    check("project.json records the reference name",
          m._project_reference(p) == "Mycobacterium_AF2122")
    p2 = fx.project("auto")
    m.step1_run("auto", m.Step1Request(reference=None))
    golden("stock_run_step1_autodetect.sh", norm((p2 / "step1" / "run_step1.sh").read_text()))


def test_results(fx: Fixture) -> None:
    print("[Step 1 Results rows and CSV, stock project]")
    p = fx.projects / "results"
    common = {"date": "2025-04-02", "Reference": "Mycobacterium_AF2122 Forced",
              "Found_Reference_Set": "None", "Aligner": "BWA",
              "Reference Length": "4,349,904", "Groups": "Mbovis-All",
              "FASTA/s": "NC_002945.4"}
    rows = {
        "TX-0001": {"FASTQ_R1": "TX-0001_S1_L001_R1_001.fastq.gz",
                    "FASTQ_R2": "TX-0001_S1_L001_R2_001.fastq.gz",
                    "R1 Read Count": "1,204,331", "R2 Read Count": "1,204,331",
                    "R1 Ave Length": "150", "R2 Ave Length": "150",
                    "Mapped Paired Reads": "2,201,002", "Unmapped Reads": "207,660",
                    "Unmapped Percent": "8.62%", "Duplicate Percent of Mapped Reads": "4.10%",
                    "Genome with Coverage": "99.71%", "Average Depth": "71.4",
                    "No Coverage Bases": "12,611", "Percent Ref with Zero Coverage": "0.29%",
                    "Ambiguous SNPs": "3", "Quality SNPs": "612"},
        "TX-0002": {"FASTQ_R1": "TX-0002_S2_L001_R1_001.fastq.gz",
                    "FASTQ_R2": "TX-0002_S2_L001_R2_001.fastq.gz",
                    "R1 Read Count": "88,210", "R2 Read Count": "88,210",
                    "Mapped Paired Reads": "61,000", "Unmapped Reads": "115,420",
                    "Unmapped Percent": "65.42%", "Genome with Coverage": "71.20%",
                    "Average Depth": "4.2", "Quality SNPs": "88"},
    }
    for sample, vals in rows.items():
        d = p / "step1" / sample
        al = d / "alignment_Mycobacterium_AF2122"
        al.mkdir(parents=True)
        fastq(d / vals["FASTQ_R1"], 150)
        fastq(d / vals["FASTQ_R2"], 150)
        stats_workbook(d / f"{sample}_2025-04-02_10-35-12_stats.xlsx",
                       {"sample": sample, **common, **vals})
        (al / f"{sample}_zc.vcf").write_text("##fileformat=VCFv4.2\n")
        prov = d / ".provenance"
        prov.mkdir()
        (prov / "exit_code").write_text("0\n")
    step1_dir = p / "step1"
    raw = m._qc_rows_blocking(step1_dir)
    annotated = [{k: v for k, v in r.items() if not k.startswith("_sa")} for r in raw]
    m._annotate_qc_rows(annotated, m._resolve_qc_thresholds(m.load_config(), p))
    for r in annotated:          # a file's own mtime is not part of the contract
        r.pop("_mtime", None)
        r.pop("mtime", None)
    golden("stock_results_rows.json",
           norm(json.dumps(sorted(annotated, key=lambda r: str(r.get("sample"))),
                           indent=1, sort_keys=True, default=str)) + "\n")
    resp = m.qc_summary_csv("results")
    golden("stock_results.csv", norm(resp.body.decode()))


def test_references(fx: Fixture) -> None:
    print("[reference list]")
    refs = m.list_references(m.load_config())
    golden("stock_references.json", norm(json.dumps(refs, indent=1, sort_keys=True, default=str)) + "\n")


def main() -> int:
    fx = Fixture()
    saved = {k: getattr(m, k) for k in ("load_config", "wrap_cmd", "build_env")}
    saved_start = m.job_manager.start_job
    saved_prov = m.provenance_writer.dispatch_step1_batch
    m.load_config = lambda: dict(fx.cfg)
    m.wrap_cmd = lambda cfg, cmd: cmd
    m.build_env = lambda cfg: {}
    m.job_manager.start_job = lambda **kw: "job-stub"
    m.provenance_writer.dispatch_step1_batch = lambda *a, **k: (None, {})
    try:
        test_scripts(fx)
        test_results(fx)
        test_references(fx)
    finally:
        for k, v in saved.items():
            setattr(m, k, v)
        m.job_manager.start_job = saved_start
        m.provenance_writer.dispatch_step1_batch = saved_prov
        shutil.rmtree(_TMP, ignore_errors=True)
    if UPDATE:
        print("\nGoldens written; review the diff before committing.")
        return 0
    print(f"\n{FAILED} FAILURE(S)" if FAILED else "\nAll stock golden tests passed.")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
