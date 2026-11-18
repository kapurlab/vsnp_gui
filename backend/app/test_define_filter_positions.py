"""Filter positions under a group in the defining-SNP workbook.

The request: a position that should not appear in a group's SNP table is
listed under that group in the reference's *_define_filter.xlsx (rows 3 and
down of the group's column; column A for every table). Until now the only way
to add one was to edit the workbook by hand. The Reference Editor now lists
the file's groups and takes many positions at once, and a SNP table can send
the positions picked on it to its own group.

What is pinned here:
  * the positions land where vsnp3 reads them — the FIRST column carrying the
    group's name (pandas renames a repeated name "name.1", so a second
    defining-SNP column's filter rows are never applied), below the last
    non-empty cell, with gaps higher up left alone; checked by reading the
    result back exactly as vsnp3_group_on_defining_snps.Group does;
  * they are written as the tables spell abs_pos (chrom:pos, no commas),
    because vsnp3 matches a single position as text;
  * anything vsnp3 could not use stops the whole batch with nothing written:
    an unknown sequence, a position past its end, a backwards range, a bare
    number on a reference with more than one sequence;
  * a position already listed, or already covered by a range or by the All
    column, is skipped and said so — and a batch with nothing new writes
    nothing, archives nothing and logs nothing;
  * the rest of the workbook survives (other columns, other sheets) and the
    edit is archived and audited like every other reference edit;
  * a SNP table's group and reference come from where the table sits
    (step2/<run>/<group>/), the All table's folder maps to column A, and a
    path anywhere else is refused.

Run directly:  <env>/bin/python backend/app/test_define_filter_positions.py
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import main as M  # noqa: E402

FAILED = 0


def check(label, cond):
    global FAILED
    if cond:
        print(f"  OK  {label}")
    else:
        FAILED += 1
        print(f"  FAIL {label}")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def column(path: Path, col: int):
    ws = openpyxl.load_workbook(path).worksheets[0]
    return [ws.cell(row=r, column=col).value for r in range(1, ws.max_row + 1)]


def vsnp3_reads(path: Path):
    """Group.__init__'s reads, verbatim in effect."""
    df = pd.read_excel(path)
    filter_all = [x for x in df.iloc[:, 0].to_list()[1:] if str(x) != "nan"]
    by_group = pd.read_excel(path, header=1).iloc[:, 1:].to_dict(orient="list")
    by_group = {k: [e for e in v if not pd.isna(e)] for k, v in by_group.items()}
    return filter_all, by_group


def refused(fn, *a) -> str:
    try:
        fn(*a)
    except HTTPException as exc:
        return str(exc.detail)
    return ""


def build(ref_dir: Path) -> Path:
    book = ref_dir / "Ref_define_filter.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "all"
    grid = [
        [None, "chr1:100", "chr1:200", "chr1:300", "chr2:40"],
        ["Ref-All", "G1", "G2", "G1", "G3"],
        ["chr1:10-20", "chr1:5", None, "chr1:999", "chr2:1"],
        ["chr1:50", None, None, None, None],
        [None, "chr1:7", None, None, None],
        ["chr2:1,000-1,010", None, None, None, None],
    ]
    for r, row in enumerate(grid, start=1):
        for c, v in enumerate(row, start=1):
            if v is not None:
                ws.cell(row=r, column=c, value=v)
    wb.create_sheet("curation notes")["A1"] = "G1 reviewed 2026-10"
    wb.save(book)
    (ref_dir / "Ref.fasta").write_text(">chr1 first\n" + "A" * 1000 + "\n>chr2\n" + "C" * 2000 + "\n")
    return book


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="define_filter_positions_"))
    ref_dir = tmp / "references" / "Ref"
    ref_dir.mkdir(parents=True)
    book = build(ref_dir)

    M.load_config = lambda: {"vsnp3_path": str(tmp / "vsnp3")}
    M._ref_dir_or_404 = lambda _name: ref_dir
    M._current_os_user = lambda: "tester"
    M._T39_SHARED_AUDIT_PATH = tmp / "audit" / "reference-changes.jsonl"

    def add(group, positions, all_groups=False):
        return M.ref_define_filter_add_positions("Ref", M.DefineFilterAddPositionsRequest(
            group=group, all_groups=all_groups, positions=positions, rationale="test"))

    print("[the group picker lists what the file holds]")
    g = M.ref_define_filter_groups("Ref")
    check("every group once, in file order, the repeated G1 once",
          [x["name"] for x in g["groups"]] == ["G1", "G2", "G3"])
    check("each with the count of positions it already leaves out (G1's first column)",
          [x["count"] for x in g["groups"]] == [2, 0, 1])
    check("the All column is named and counted",
          g["all"] == {"name": "Ref-All", "count": 3})
    check("the reference's sequences come from its FASTA",
          g["sequences"] == ["chr1", "chr2"])

    print("[adding to a group: where vsnp3 reads, as vsnp3 spells it]")
    before_sha = sha(book)
    out = add("G1", ["chr1:8", "chr1:7", "chr1:15", "chr1:8", "chr2:1,500", "chr1:1-3"])
    check("the new positions were added, commas dropped, repeats once",
          out["added"] == ["chr1:8", "chr2:1500", "chr1:1-3"])
    check("an already-listed one and one inside the All range were skipped, with why",
          [s["position"] for s in out["skipped"]] == ["chr1:7", "chr1:15"]
          and "already listed" == out["skipped"][0]["why"]
          and "Ref-All" in out["skipped"][1]["why"])
    colB = column(book, 2)
    check("they went below the last entry of G1's FIRST column, the gap kept",
          colB[2:] == ["chr1:5", None, "chr1:7", "chr1:8", "chr2:1500", "chr1:1-3"]
          and out["column"] == "B" and out["rows"] == [6, 8])
    check("G1's second defining column is untouched", column(book, 4)[2:] == ["chr1:999", None, None, None, None, None])
    filter_all, by_group = vsnp3_reads(book)
    check("vsnp3's read of G1's filter list now includes them",
          by_group["G1"] == ["chr1:5", "chr1:7", "chr1:8", "chr2:1500", "chr1:1-3"])
    check("the other sheet survived",
          openpyxl.load_workbook(book)["curation notes"]["A1"].value == "G1 reviewed 2026-10")
    check("the previous bytes were archived",
          out["archived_old"] and sha(Path(out["archived_old"])) == before_sha)
    log = [json.loads(l) for l in Path(out["audit_log"]).read_text().splitlines()]
    check("the audit log names the group, column and positions",
          log[-1]["action"] == "define_filter_add_positions" and log[-1]["group"] == "G1"
          and log[-1]["column"] == "B" and log[-1]["positions"] == out["added"])

    print("[adding to every group: column A]")
    out = add("", ["chr1:60-70", "chr2:1005"], all_groups=True)
    check("a range is added; a position inside an existing All range is skipped",
          out["added"] == ["chr1:60-70"] and out["skipped"][0]["position"] == "chr2:1005")
    filter_all, _ = vsnp3_reads(book)
    check("vsnp3's filter-all list ends with it",
          filter_all[-1] == "chr1:60-70" and filter_all[0] == "chr1:10-20")

    print("[a position inside a range given in the same list]")
    out = add("G3", ["chr2:500", "chr2:400-600", "chr2:700"])
    check("is skipped, the range and the outside position added",
          out["added"] == ["chr2:400-600", "chr2:700"]
          and out["skipped"] == [{"position": "chr2:500", "why": "inside a range in the same list"}])

    print("[an empty group column starts at row 3]")
    out = add("G2", ["chr2:77"])
    check("G2's first filter row", column(book, 3)[2] == "chr2:77" and out["rows"] == [3, 3])

    print("[nothing new: nothing written, archived or logged]")
    sha_now = sha(book)
    hist = sorted((ref_dir / ".history").glob("*")) if (ref_dir / ".history").is_dir() else []
    log_lines = len(Path(out["audit_log"]).read_text().splitlines())
    out = add("G2", ["chr2:77"])
    check("reported as no change", out.get("no_change") and out["added"] == [])
    check("the file is byte-identical", sha(book) == sha_now)
    check("no new archive copy",
          sorted((ref_dir / ".history").glob("*")) == hist)
    check("no audit line", len(Path(M._T39_SHARED_AUDIT_PATH).read_text().splitlines()) == log_lines)

    print("[anything vsnp3 could not use stops the batch]")
    sha_now = sha(book)
    for label, positions, needle in [
        ("an unknown sequence", ["chr1:9", "chrX:5"], "not a sequence"),
        ("past the end of the sequence", ["chr1:1001"], "past the end"),
        ("a backwards range", ["chr1:50-40"], "low to high"),
        ("a bare number with two sequences", ["123"], "sequence:position"),
        ("not a position at all", ["chr1:abc"], "not a position"),
    ]:
        why = refused(add, "G1", positions)
        check(f"{label}: refused ({why[:60]}…)", needle in why and "Nothing was added" in why)
    check("...and the file was never touched", sha(book) == sha_now)
    check("an unknown group is refused",
          "no a group" not in refused(add, "G9", ["chr1:9"]) and "G9" in refused(add, "G9", ["chr1:9"]))

    print("[a one-sequence reference takes bare numbers]")
    (ref_dir / "Ref.fasta").write_text(">chr1\n" + "A" * 1000 + "\n")
    out = add("G3", ["44", "45-47"])
    check("prefixed with its only sequence", out["added"] == ["chr1:44", "chr1:45-47"])

    print("[a SNP table knows its group]")
    proj = tmp / "projects" / "p1"
    run = proj / "step2" / "2026-09-15_11-02-39"
    for grp in ("G2", "Ref-All", "Gone"):
        (run / grp).mkdir(parents=True)
        (run / grp / f"{grp}_cascade1_table.xlsx").write_bytes(b"")
    (proj / "project.json").write_text(json.dumps({"reference": "Ref"}))
    M._project_dir_for = lambda _c, _p: proj
    M._project_reference_dir = lambda _pd, _c: ref_dir
    ctx = M.snp_table_filter_context("p1", str(run / "G2" / "G2_cascade1_table.xlsx"))
    check("a group table: available, its folder is its group",
          ctx["available"] and ctx["group"] == "G2" and not ctx["is_all"]
          and ctx["entries"] == ["chr2:77"] and ctx["file"] == "Ref_define_filter.xlsx")
    check("...with the All column's entries to show what is already gone",
          "chr1:60-70" in ctx["all_entries"] and ctx["all_name"] == "Ref-All")
    ctx = M.snp_table_filter_context("p1", str(run / "Ref-All" / "Ref-All_cascade1_table.xlsx"))
    check("the All table maps to column A", ctx["available"] and ctx["is_all"])
    ctx = M.snp_table_filter_context("p1", str(run / "Gone" / "Gone_cascade1_table.xlsx"))
    check("a folder with no such group says so", not ctx["available"] and "Gone" in ctx["reason"])
    ctx = M.snp_table_filter_context("p1", str(proj / "step2" / "vcf_database" / "x.xlsx"))
    check("a table outside a group folder is not offered", not ctx["available"])

    out = M.snp_table_filter_add("p1", M.SnpTableFilterAddRequest(
        path=str(run / "G2" / "G2_cascade1_table.xlsx"), positions=["chr1:321"],
        rationale="seen in G2 table"))
    check("picked on the G2 table, added under G2", out["group"] == "G2" and column(book, 3)[3] == "chr1:321")
    log = [json.loads(l) for l in Path(out["audit_log"]).read_text().splitlines()]
    check("the audit record names the table it came from",
          log[-1]["source"] == {"project": "p1", "table": "G2_cascade1_table.xlsx"})
    out = M.snp_table_filter_add("p1", M.SnpTableFilterAddRequest(
        path=str(run / "Ref-All" / "Ref-All_cascade1_table.xlsx"), positions=["chr1:322"],
        rationale="seen in the All table"))
    check("picked on the All table, added to column A", out["all_groups"] and column(book, 1)[-1] == "chr1:322")
    why = refused(M.snp_table_filter_add, "p1", M.SnpTableFilterAddRequest(
        path=str(tmp / "elsewhere.xlsx"), positions=["chr1:1"], rationale="x"))
    check("a path outside the project's Step 2 is refused", "Step 2" in why)

    print("[a new defining SNP picked on a SNP table]")
    # Requested 2026-11-18: like "Filter positions", but click a position and
    # name a new group. One column at the end: row 1 the position, row 2 the
    # name, rows 3+ the table group's filter list (what a subgroup holds).
    g2_table = str(run / "G2" / "G2_cascade1_table.xlsx")
    def new_group(name, pos, copy=True, path=g2_table):
        return M.snp_table_new_group("p1", M.SnpTableNewGroupRequest(
            path=path, position=pos, group=name, copy_filters=copy, rationale="new subgroup"))
    ws = openpyxl.load_workbook(book).worksheets[0]
    g2_filters = [v for v in column(book, 3)[2:] if v]
    note_col = ws.max_column + 2                    # a note past the last group, below row 2
    ws.cell(row=5, column=note_col, value="curator note: keep")
    ws.parent.save(book)
    out = new_group("G2a", "chr1:555")
    wsn = openpyxl.load_workbook(book).worksheets[0]
    new_col = [c for c in range(1, wsn.max_column + 1) if wsn.cell(row=2, column=c).value == "G2a"]
    check("one new column, after the last group", len(new_col) == 1 and new_col[0] == 6
          and out["column"] == "F")
    check("row 1 the position, row 2 the name", wsn.cell(row=1, column=6).value == "chr1:555")
    check("its filter list starts as G2's", [v for v in column(book, 6)[2:] if v] == g2_filters
          and out["filters"] == len(g2_filters))
    check("the curator's note past it is untouched",
          wsn.cell(row=5, column=note_col).value == "curator note: keep")
    defs = pd.read_excel(book).iloc[:, 1:].head(1).to_dict(orient="records")[0]
    _, by_group = vsnp3_reads(book)
    check("vsnp3 reads chr1:555 as defining G2a", defs.get("chr1:555") == "G2a")
    check("...and G2a's filter list as the copy", by_group.get("G2a") == g2_filters)
    log = [json.loads(l) for l in Path(out["audit_log"]).read_text().splitlines()]
    check("the audit record names the group, position, copy and table",
          log[-1]["action"] == "define_filter_add_group" and log[-1]["positions"] == ["chr1:555"]
          and log[-1]["filters_copied_from"] == "G2"
          and log[-1]["source"]["table"] == "G2_cascade1_table.xlsx")
    ctx = M.snp_table_filter_context("p1", g2_table)
    check("the table page now sees chr1:555 as defining G2a", ctx["defining"].get("chr1:555") == "G2a")

    sha_now = sha(book)
    for label, args, needle in [
        ("a name that differs only in case", ("g2A", "chr1:556"), "already has a group named 'G2a'"),
        ("the All column's name", ("Ref-All", "chr1:557"), "already has a group"),
        ("a position that already defines a group", ("G9", "chr1:100"), "already defines 'G1'"),
        ("a name that cannot be a folder", ("a/b", "chr1:558"), "cannot be a group name"),
        ("a range", ("G9", "chr1:5-9"), "not a single position"),
        ("a sequence the reference lacks", ("G9", "chrX:5"), "not a sequence"),
    ]:
        why = refused(new_group, *args)
        check(f"{label}: refused", needle in why)
    check("...and the file was never touched", sha(book) == sha_now)

    out = new_group("G2b", "chr1:559", copy=False)
    wsn = openpyxl.load_workbook(book).worksheets[0]
    g2b = [c for c in range(1, wsn.max_column + 1) if wsn.cell(row=2, column=c).value == "G2b"][0]
    check("the next group skips the column holding the note", g2b == note_col + 1)
    check("without the copy, an empty filter list", out["filters"] == 0
          and not [v for v in column(book, g2b)[2:] if v])
    out = new_group("AllSub", "chr1:560", path=str(run / "Ref-All" / "Ref-All_cascade1_table.xlsx"))
    check("from the All table nothing is copied (its list applies to every group)", out["filters"] == 0)

    print("[samples picked on a SNP table go to the remove_from_analysis list]")
    # Requested 2026-11-18: click sample names, add them to the remove list.
    def remove(names, path=g2_table):
        return M.snp_table_remove_samples("p1", M.SnpTableRemoveSamplesRequest(
            path=path, samples=names, rationale="contaminated"))
    why = refused(remove, ["S1"])
    check("with no remove list yet, it says to create one", "Create one from template" in why)
    rm = ref_dir / "Ref_remove_from_analysis.xlsx"
    wb = openpyxl.Workbook(); wb.active["A1"] = "OLD-1"; wb.save(rm)
    out = remove(["S7", "S8", "S7"])
    col_a = [c.value for c in openpyxl.load_workbook(rm).worksheets[0]["A"] if c.value]
    check("the names are appended to column A once each", col_a == ["OLD-1", "S7", "S8"]
          and out["added"] == ["S7", "S8"])
    log = [json.loads(l) for l in Path(out["audit_log"]).read_text().splitlines()]
    check("the audit record names the table", log[-1]["action"] == "remove_from_analysis_add_sample"
          and log[-1]["source"]["table"] == "G2_cascade1_table.xlsx")
    ctx = M.snp_table_filter_context("p1", g2_table)
    check("the table page sees who is on the list",
          ctx["remove_file"] == rm.name and {"OLD-1", "S7", "S8"} <= set(ctx["removed"]))
    out = remove(["S8"])
    check("a name already on the list is skipped, not repeated",
          out["added"] == [] and out["skipped"] == ["S8"])
    out = remove(["S9"], path=str(run / "Gone" / "Gone_cascade1_table.xlsx"))
    check("a table whose group the define_filter lacks can still remove samples",
          out["added"] == ["S9"])

    print("[a download of the edited file is never a stale browser copy]")
    # Reported 2026-11-18: an edit, then Download, gave the workbook as it was.
    # With no Cache-Control a browser may reuse a held copy for a tenth of the
    # time since Last-Modified (weeks, for a file last changed in 2025).
    M._ref_entry = lambda _name: {"path": str(ref_dir)}
    resp = M.ref_download_file("Ref", filename=book.name)
    check("the reference download must be revalidated before reuse",
          resp.headers.get("cache-control") == "no-cache")

    print("[the table page asks for nothing in a pop-up]")
    # Reported 2026-11-18: "it never asked for a rationale". A browser that
    # declines to show window.prompt answers null, and the add returned without
    # a word. The rationale is an input in the panel now.
    import xlsx_html
    from test_xlsx_filter import make_group_table
    tbl = tmp / "t.xlsx"
    make_group_table(tbl, ["S1_meta", "S2_meta"], 4, lambda i, c: "A" if i == 1 else "C")
    page = xlsx_html.xlsx_to_html(tbl, project="p1")
    check("no window.prompt / window.confirm in the page",
          "window.prompt(" not in page and "window.confirm(" not in page)
    check("the panel has a rationale box", 'id="xlsxFltWhy"' in page)

    print(f"\n{'ALL PASSED' if not FAILED else f'{FAILED} FAILED'}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
