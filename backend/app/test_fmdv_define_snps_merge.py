"""bin/fmdv_define_snps.py adds to a lineage's define_filter, it does not rebuild it.

The workbook the script writes its derived groups into is the same one people
edit in the Reference Editor: groups of their own, and under any group the
positions to leave out of its table. The script used to make a new workbook
every time, so rederiving the groups for a lineage (which the FMDV docs say to
do as genomes accumulate) silently deleted every one of those edits.

Run directly:  python test_fmdv_define_snps_merge.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "bin"))

import openpyxl  # noqa: E402

import fmdv_define_snps as ds  # noqa: E402


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label}")


def assert_true(cond, label):
    if not cond:
        raise AssertionError(label)
    print(f"  OK  {label}")


def columns(path: Path):
    """{group name: [row 1, row 3, row 4, ...]} for the first sheet."""
    ws = openpyxl.load_workbook(path).worksheets[0]
    out = {}
    for col in range(1, ws.max_column + 1):
        name = ws.cell(row=2, column=col).value
        if name in (None, ""):
            continue
        below = [ws.cell(row=r, column=col).value for r in range(3, ws.max_row + 1)]
        out[name] = [ws.cell(row=1, column=col).value] + [v for v in below if v not in (None, "")]
    return out


def record(folder: Path, chrom: str, kept) -> None:
    (folder / "DEFINING_SNPS.json").write_text(json.dumps(
        {"chrom": chrom, "groups": [{"name": n, "positions": s} for n, s in kept]}))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        out = folder / "SAT1-I_KM268899_define_filter.xlsx"
        rec = folder / "DEFINING_SNPS.json"
        chrom = "KM268899"

        print("no workbook yet: one is made")
        first = [("SAT1-II", [1390, 1207]), ("SAT1-III", [5838])]
        ds.write_groups(out, rec, chrom, first)
        record(folder, chrom, first)
        assert_eq(columns(out), {"name-All": [None],
                                 "SAT1-II": ["KM268899:1390, KM268899:1207"],
                                 "SAT1-III": ["KM268899:5838"]}, "the derived groups, and the all column")
        assert_true(not (folder / ".history").exists(), "nothing to archive the first time")

        print("somebody edits it: a group of their own, filter positions, a corrected signature")
        wb = openpyxl.load_workbook(out); ws = wb.worksheets[0]
        ws.cell(row=3, column=1, value="KM268899:100")            # left out of every table
        ws.cell(row=3, column=2, value="KM268899:2000")           # left out of SAT1-II's table
        ws.cell(row=4, column=2, value="KM268899:2001-2010")
        ws.cell(row=1, column=3, value="KM268899:5838, KM268899:6000")   # SAT1-III corrected by hand
        ws.cell(row=1, column=4, value="KM268899:777"); ws.cell(row=2, column=4, value="Outbreak-2026")
        ws.cell(row=3, column=4, value="KM268899:42")
        wb.save(out)
        before = out.read_bytes()

        print("the groups are rederived from more genomes")
        second = [("SAT1-II", [1390]), ("SAT1-III", [5838, 3008]), ("SAT1-IV", [2334])]
        report = ds.write_groups(out, rec, chrom, second)
        record(folder, chrom, second)
        got = columns(out)
        assert_eq(got["name-All"], [None, "KM268899:100"], "the all-tables filter is kept")
        assert_eq(got["SAT1-II"], ["KM268899:1390", "KM268899:2000", "KM268899:2001-2010"],
                  "an untouched signature is brought up to date, its filter positions kept")
        assert_eq(got["SAT1-III"], ["KM268899:5838, KM268899:6000"],
                  "a signature corrected by hand is left as corrected")
        assert_eq(got["Outbreak-2026"], ["KM268899:777", "KM268899:42"], "a group of their own is kept whole")
        assert_eq(got["SAT1-IV"], ["KM268899:2334"], "a newly derived group is added")
        assert_eq(list(got), ["name-All", "SAT1-II", "SAT1-III", "Outbreak-2026", "SAT1-IV"],
                  "added after the existing columns, none moved")
        archived = sorted((folder / ".history").iterdir())
        assert_eq(len(archived), 1, "the workbook as it was is archived")
        assert_eq(archived[0].read_bytes(), before, "byte for byte")
        assert_true(archived[0].name.endswith("_" + out.name) and archived[0].name[8] == "T",
                    "named as the Reference Editor names its archives")
        text = "\n".join(report)
        assert_true("left group SAT1-III" in text and "added group SAT1-IV" in text
                    and "updated group SAT1-II" in text, "the report says what happened to each")

        print("run again with nothing new: nothing changes")
        stamp = out.stat().st_mtime_ns
        report = ds.write_groups(out, rec, chrom, second)
        assert_eq(columns(out), got, "the same workbook")
        assert_eq(out.stat().st_mtime_ns, stamp, "not even rewritten")
        assert_eq(len(list((folder / ".history").iterdir())), 1, "and no second archive of it")
        assert_true("left as it is: 0 new, 0 updated" in report[-1], "and the report says so")

        print("a group that is no longer derived stays")
        third = [("SAT1-II", [1390])]
        report = ds.write_groups(out, rec, chrom, third)
        assert_eq(list(columns(out)), list(got), "no column removed")
        assert_true(any("SAT1-IV is no longer derived" in line for line in report), "and it is reported")

        print("--replace: only the derived groups, after an archive")
        n_before = len(list((folder / ".history").iterdir()))
        ds.write_groups(out, rec, chrom, third, replace=True)
        assert_eq(columns(out), {"name-All": [None], "SAT1-II": ["KM268899:1390"]}, "a fresh workbook")
        assert_true(len(list((folder / ".history").iterdir())) >= n_before, "the edited one is in .history")
    print("test_fmdv_define_snps_merge: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
