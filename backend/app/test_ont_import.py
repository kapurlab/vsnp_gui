"""Importing a MinKNOW run folder as one FASTQ per sample (app.ont_import).

What these pin:
  * the three layouts are recognised: fastq_pass/barcodeNN (also nested under
    experiment/sample/run_id), bare barcodeNN folders, and a flat folder of
    barcode-named files; unclassified is kept apart;
  * the sample sheet needs only barcode and sample_id, accepts NB02 / BC2 / 2
    as barcode labels, and reports (not silently drops) a bad row;
  * underscores in a sample_id become dashes at import, because vsnp3 names a
    sample from everything before the first underscore;
  * a name already in download/ refuses the plan, and the commit concatenates
    gzip members into one valid stream through a .part file.

    <conda>/bin/python backend/app/test_ont_import.py
"""
from __future__ import annotations

import gzip
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ont_import as oi  # noqa: E402

FAILS = []


def check(got, want, what):
    if got != want:
        FAILS.append(f"{what}\n    got:  {got!r}\n    want: {want!r}")


def _reads(n: int, tag: str) -> str:
    return "".join(f"@{tag}_{i}\nACGT\n+\nIIII\n" for i in range(n))


def _chunk(path: Path, n: int, tag: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        fh.write(_reads(n, tag))


def make_run(root: Path, nested: bool = False) -> Path:
    run = root / "20261114_run"
    base = run / "exp" / "sample" / "abc123" / "fastq_pass" if nested else run / "fastq_pass"
    for bc, n in (("barcode01", 3), ("barcode02", 2)):
        for c in range(n):
            _chunk(base / bc / f"FAX_pass_{bc}_{c:03d}.fastq.gz", 5, bc)
    _chunk(base / "unclassified" / "FAX_pass_unclassified_000.fastq.gz", 4, "unc")
    return run


def test_barcode_normalisation():
    for raw, want in (("barcode07", "barcode07"), ("NB07", "barcode07"), ("BC7", "barcode07"),
                      ("7", "barcode07"), ("Barcode96", "barcode96"), ("97", None), ("bc", None), ("", None)):
        check(oi.normalize_barcode(raw), want, f"normalize_barcode({raw!r})")


def test_sample_names_lose_underscores():
    check(oi.safe_sample_name("ETH_SAT1_001"), "ETH-SAT1-001", "underscores become dashes")
    check(oi.safe_sample_name(" ETH-SAT1-002 "), "ETH-SAT1-002", "surrounding space dropped")
    check(oi.safe_sample_name("a b/c"), "a-b-c", "other separators become dashes")
    check(oi.safe_sample_name("___"), "", "nothing usable gives an empty name")


def test_layouts():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        flat_run = make_run(root)
        scan = oi.scan_run_dir(flat_run)
        check(scan["layout"], "fastq_pass", "fastq_pass layout found")
        check(sorted(scan["barcodes"]), ["barcode01", "barcode02"], "two barcodes")
        check(len(scan["barcodes"]["barcode01"]), 3, "three chunks for barcode01")
        check(len(scan["unclassified"]), 1, "unclassified kept apart")

        nested_run = make_run(root / "n", nested=True)
        check(oi.scan_run_dir(nested_run)["layout"], "fastq_pass", "nested fastq_pass found three levels down")

        bare = root / "bare"
        _chunk(bare / "barcode05" / "x_000.fastq.gz", 2, "b5")
        check(oi.scan_run_dir(bare)["layout"], "barcode_dirs", "bare barcode folders")
        check(sorted(oi.scan_run_dir(bare)["barcodes"]), ["barcode05"], "barcode05 only")

        flat = root / "flat"
        _chunk(flat / "run_barcode09_pass.fastq.gz", 2, "f9")
        _chunk(flat / "run_barcode10_pass.fq.gz", 2, "f10")
        _chunk(flat / "run_unclassified.fastq.gz", 1, "fu")
        s = oi.scan_run_dir(flat)
        check(s["layout"], "flat", "flat folder of barcode-named files")
        check(sorted(s["barcodes"]), ["barcode09", "barcode10"], "both suffixes accepted")
        check(len(s["unclassified"]), 1, "flat unclassified file kept apart")

        empty = root / "empty"
        empty.mkdir()
        check(oi.scan_run_dir(empty)["layout"], "", "no layout in an empty folder")


def test_sheet_reading_and_problems():
    with tempfile.TemporaryDirectory() as td:
        run = Path(td)
        (run / "sample_sheet.csv").write_text(
            "barcode,sample_id,amplicon\n"
            "barcode01,ETH_SAT1_001,ignored\n"
            "NB02,ETH-SAT1-002,\n"
            "3,SAT1_ctrl_3,\n"
            "barcode04,,\n"
            "bogus,X,\n"
            "barcode01,DUP,\n"
            "barcode06,ETH-SAT1-001,\n"
        )
        mapping, name, problems = oi.read_sample_sheet(run)
        check(name, "sample_sheet.csv", "sheet found")
        check(mapping, {"barcode01": "ETH_SAT1_001", "barcode02": "ETH-SAT1-002", "barcode03": "SAT1_ctrl_3"},
              "three usable rows, labels normalised")
        check(len(problems), 4, "empty id, bad barcode, duplicate barcode, name collision all reported")
        check(any("collides" in p for p in problems), True, "the collision names the dashed clash")

        (run / "sample_sheet.tsv").write_text("Barcode\tSample_ID\nbarcode01\tA\n")
        (run / "sample_sheet_20261120.csv").write_text("barcode,sample_id\nbarcode01,LATEST\n")
        mapping, name, _ = oi.read_sample_sheet(run)
        check(name, "sample_sheet_20261120.csv", "the dated sheet wins")
        check(mapping["barcode01"], "LATEST", "and its rows are used")

        (run / "sample_sheet_20261120.csv").write_text("name,id\nx,y\n")
        mapping, _, problems = oi.read_sample_sheet(run)
        check(mapping, {}, "missing required columns gives no mapping")
        check("header must contain barcode and sample_id" in problems[0], True, "and says why")


def test_preview_and_plan():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run = make_run(root)
        (run / "sample_sheet.csv").write_text("barcode,sample_id\nbarcode01,ETH_SAT1_001\nbarcode09,GHOST\n")
        download = root / "download"
        download.mkdir()
        (download / "barcode02.fastq.gz").write_bytes(b"")
        pv = oi.preview(run, download)
        rows = {r["barcode"]: r for r in pv["rows"]}
        check(rows["barcode01"]["sample"], "ETH-SAT1-001", "sheet name, dashed")
        check(rows["barcode01"]["renamed"], True, "renamed flag set")
        check(rows["barcode01"]["files"], 3, "chunk count")
        check(rows["barcode02"]["sample"], "barcode02", "no sheet row: the barcode is the name")
        check(rows["barcode02"]["exists"], True, "already in download/ is flagged")
        check(any("barcode09" in p for p in pv["sheet_problems"]), True, "a sheet row with no reads is reported")
        check(pv["unclassified"]["files"], 1, "unclassified counted")

        plan, errors = oi.build_plan(run, download, [{"barcode": "barcode01", "sample": "ETH_SAT1_001"}], False)
        check(errors, [], "a clean plan has no errors")
        check(plan[0]["sample"], "ETH-SAT1-001", "plan uses the dashed name")
        check(plan[0]["out"].endswith("ETH-SAT1-001.fastq.gz"), True, "output path in download/")

        _, errors = oi.build_plan(run, download, [{"barcode": "barcode02", "sample": ""}], False)
        check(any("already in download" in e for e in errors), True, "an existing output refuses the plan")
        _, errors = oi.build_plan(run, download, [{"barcode": "barcode01", "sample": "S"}, {"barcode": "barcode02", "sample": "S"}], False)
        check(any("both be named" in e for e in errors), True, "two barcodes with one name refused")
        _, errors = oi.build_plan(run, download, [{"barcode": "barcode77", "sample": "x"}], False)
        check(any("no reads" in e for e in errors), True, "a barcode with no reads refused")
        plan, errors = oi.build_plan(run, download, [], True)
        check(errors, [], "unclassified alone is a valid plan")
        check(plan[0]["barcode"], "unclassified", "and it is planned")


def test_commit_concatenates_gzip_members():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run = make_run(root)
        download = root / "download"
        plan, errors = oi.build_plan(run, download, [{"barcode": "barcode01", "sample": "A_1"}], True)
        check(errors, [], "plan ok")
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan))
        rc = oi.run_commit(plan_path)
        check(rc, 0, "commit succeeds")
        out = download / "A-1.fastq.gz"
        check(out.is_file(), True, "output written under the dashed name")
        with gzip.open(out, "rt") as fh:
            n = sum(1 for _ in fh) // 4
        check(n, 15, "three chunks of five reads read back as one stream")
        check((download / "unclassified.fastq.gz").is_file(), True, "unclassified written when asked")
        check(sorted(p.name for p in download.iterdir() if p.name.endswith(".part")), [], "no .part left behind")


for fn in sorted([v for k, v in list(globals().items()) if k.startswith("test_")],
                 key=lambda f: f.__code__.co_firstlineno):
    fn()

if FAILS:
    print(f"FAIL — {len(FAILS)} assertion(s)")
    for f in FAILS:
        print("  " + f)
    sys.exit(1)
print("ok — ont_import")
