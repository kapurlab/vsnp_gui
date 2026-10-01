#!/usr/bin/env python3
"""The preview page inlines a window of columns and serves the rest in batches.

A cascade table is wide: 72 samples by 10,001 positions on a real run. Until
v0.4.126 the page inlined every column, and the browser spent about 20 s
parsing and laying out 720,000 cells for a page the server had answered in
0.1 s. Now compose_page inlines the first initial_cols_for(rows) columns and
the page asks for more as it is scrolled right (cols_batch), while row batches
are cut to the columns the page holds (rows_batch), so the two kinds of
scrolling never leave the table ragged.

These checks pin the seams: the cell split reproduces every row exactly, the
column batches tile the rows back together, the counts the script starts from
match what was inlined, and the served script still parses.

Run: cd backend/app && ../../env/bin/python test_xlsx_columns.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_xlsx_filter import assert_eq, assert_true, make_group_table
import xlsx_html


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="xlsx_columns_"))
    try:
        labels = [f"S{i}" for i in range(1, 61)]
        cols = 1500
        total_rows = len(labels) + 4
        book = tmp / "wide.xlsx"
        make_group_table(book, labels, cols, lambda i, c: "A" if (i + c) % 7 == 0 else "C")
        w = xlsx_html.render_window(book, total_rows, cols, None, "proj", set(labels), set(),
                                    xlsx_html.FULL_VIEW_MAX_CELLS, total_rows)
        assert_eq(len(w["rows"]), total_rows, "every row rendered")
        assert_eq(w["shown_cols"], cols, "every column rendered")

        print("\n[the cell split reproduces each row exactly]")
        cells = xlsx_html.window_cells(w)
        assert_true(all("".join(cells[i]) == w["rows"][i][4:-5] for i in range(total_rows)),
                    "joining a row's cells gives back the row's HTML")
        assert_true(all(len(r) == cols for r in cells), "every row splits into one cell per column")
        assert_true(xlsx_html.window_cells(w) is cells, "the split is kept on the window")
        assert_eq(len(xlsx_html.window_cols(w)), cols, "one <col> per column")
        kept = xlsx_html.persistable(w)
        assert_true("_cells" in w and "_cells" not in kept and "_cols" not in kept,
                    "the cache never sees the in-memory split")
        json.dumps(kept)

        print("\n[the page inlines a column window and knows the rest is there]")
        page = xlsx_html.compose_page(w)
        ic = xlsx_html.initial_cols_for(total_rows)
        assert_eq(ic, 400, "a 64-row table starts with 400 columns")

        def table_of(p: str) -> str:
            return p.split('<table class="xlsx" id="xlsxTable">', 1)[1].split("</table>", 1)[0]
        assert_eq(table_of(page).count("<tr>"), total_rows, "all 64 rows are inlined")
        assert_eq(table_of(page).count("<td"), total_rows * ic, "…but only the first 400 columns of each")
        assert_eq(table_of(page).count("<col "), ic, "the colgroup matches the inlined columns")
        assert_true(f"var loadedCols = {ic}, availableCols = {cols};" in page,
                    "the script starts from the inlined count and the window's width")
        assert_true("cols_from" in page and "xlsxMoreCols" in page and "Load more columns" in page,
                    "the page knows how to ask for more columns")
        loci = json.loads(page.split("var LOCI = ", 1)[1].split(";", 1)[0])
        assert_eq(len(loci), cols - 1, "LOCI covers every column, inlined or not")
        assert_eq(max(int(k) for k in loci), cols, "…up to the last one")

        print("\n[column batches tile the rows back together]")
        b = xlsx_html.cols_batch(w, ic, 400, total_rows)
        assert_eq(len(b["rows"]), total_rows, "one fragment per row on the page")
        assert_eq(b["colgroup"].count("<col "), 400, "and the <col>s of those columns")
        assert_true(all(b["rows"][i] == "".join(cells[i][ic:ic + 400]) for i in range(total_rows)),
                    "each fragment is exactly those columns' cells")
        for i in range(total_rows):
            parts = ["".join(cells[i][:ic])]
            start = ic
            while start < cols:
                parts.append(xlsx_html.cols_batch(w, start, 400, total_rows)["rows"][i])
                start += 400
            if "".join(parts) != w["rows"][i][4:-5]:
                assert_true(False, f"row {i}: the window plus its batches is not the row")
                break
        else:
            assert_true(True, "window + batches == every full row")
        tail = xlsx_html.cols_batch(w, 1200, 400, total_rows)
        assert_eq(tail["rows"][0].count("</td>"), 300, "the last batch is as short as what is left")
        beyond = xlsx_html.cols_batch(w, cols, 400, total_rows)
        assert_true(all(f == "" for f in beyond["rows"]) and beyond["colgroup"] == "",
                    "past the end there is nothing, not an error")
        over = xlsx_html.cols_batch(w, 0, 10, total_rows + 50)
        assert_eq(len(over["rows"]), total_rows, "rows_count is clamped to the rows there are")

        print("\n[row batches are as wide as the page]")
        rb = xlsx_html.rows_batch(w, 10, 5, ic)
        assert_eq(rb.count("<tr>"), 5, "five rows")
        assert_eq(rb.count("<td"), 5 * ic, "cut to the page's columns")
        assert_eq(xlsx_html.rows_batch(w, 10, 5), "".join(w["rows"][10:15]),
                  "without a width, whole rows as before")

        print("\n[a narrow table has nothing to page]")
        narrow = tmp / "narrow.xlsx"
        make_group_table(narrow, labels[:10], 30, lambda i, c: "A")
        wn = xlsx_html.render_window(narrow, 14, 30, None, "proj", set(labels), set(),
                                     xlsx_html.FULL_VIEW_MAX_CELLS, 14)
        pn = xlsx_html.compose_page(wn)
        assert_true("var loadedCols = 30, availableCols = 30;" in pn, "columns all inlined")
        assert_eq(table_of(pn).count("<td"), 14 * 30, "every cell on the page")

        print("\n[a clade view pages its columns the same way]")
        wf = xlsx_html.render_filtered_window(
            book, total_rows, cols, None, "proj", set(labels), set(), ["S1", "S2", "S3"],
            xlsx_html.DEFAULT_MAX_CELLS, xlsx_html.DEFAULT_MAX_ROWS)
        cf = xlsx_html.window_cells(wf)
        assert_true(all(len(r) == wf["shown_cols"] for r in cf), "kept rows split into the kept columns")
        pf = xlsx_html.compose_page(wf)
        inl = min(xlsx_html.initial_cols_for(len(wf["rows"])), wf["shown_cols"])
        assert_eq(table_of(pf).count("<td"), len(wf["rows"]) * inl, "the clade page inlines its window")

        print("\n[the served script parses]")
        node = shutil.which("node")
        scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
        assert_true(len(scripts) >= 1, f"{len(scripts)} script block(s) extracted")
        assert_eq("{{" in page or "}}" in page, False, "no doubled braces survive the format")
        if node:
            js = tmp / "served.js"
            js.write_text("\n;\n".join(scripts))
            proc = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
            assert_eq(proc.returncode, 0,
                      f"node --check on the served script{'' if not proc.stderr else ': ' + proc.stderr[:300]}")
        else:
            print("  SKIP  node not on PATH — cannot parse-check the served script")
        print("\nALL PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
