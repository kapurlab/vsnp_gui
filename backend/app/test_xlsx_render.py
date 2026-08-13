"""The streaming renderer must match the full one — colour above all.

Every colour in a vSNP3 SNP table comes from CONDITIONAL FORMATTING; the cells
carry no direct fill whatsoever. openpyxl's read-only worksheets do not expose
conditional formatting, so the first version of the streaming renderer produced
a perfectly laid out, completely colourless table — and, because the IGV links
are gated on a cell having a background, no links either. It looked fine in a
size measurement and was useless on screen.

So this compares the two renderers cell-for-cell on a workbook built the way
vSNP3 builds them, and pins the output size bound that keeps a dense table from
rendering to tens of megabytes.

Run directly:  python test_xlsx_render.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import openpyxl
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import PatternFill

import xlsx_html


def assert_eq(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  OK  {label} = {actual!r}")


def assert_true(condition, label):
    if not condition:
        raise AssertionError(f"{label}: expected truthy, got falsy")
    print(f"  OK  {label}")


def make_cascade_like(path: Path, rows: int, cols: int) -> None:
    """A workbook shaped like a vSNP3 cascade table.

    Row 1 is contig:pos loci, column 1 is sample names, and the variant cells
    are coloured by a CONDITIONAL FORMATTING rule rather than a direct fill —
    which is the whole point of the test.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    for c in range(2, cols + 1):
        ws.cell(row=1, column=c, value=f"MTBC0:{1000 + c}")
    for r in range(2, rows + 1):
        ws.cell(row=r, column=1, value=f"SAMPLE{r - 1}")
        for c in range(2, cols + 1):
            # "A" matches the CF rule below; "-" does not.
            ws.cell(row=r, column=c, value="A" if (r + c) % 2 == 0 else "-")
    rng = f"B2:{openpyxl.utils.get_column_letter(cols)}{rows}"
    ws.conditional_formatting.add(rng, CellIsRule(
        operator="equal", formula=['"A"'],
        fill=PatternFill(start_color="FFFF0000", end_color="FFFF0000",
                         fill_type="solid")))
    wb.save(path)
    wb.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="xlsx_render_"))
    try:
        book = tmp / "cascade.xlsx"
        make_cascade_like(book, rows=30, cols=40)
        samples = {f"SAMPLE{i}" for i in range(1, 30)}

        print("\n[the two renderers agree — colour comes from conditional formatting]")
        full = xlsx_html.xlsx_to_html(
            book, project="p", samples_with_bams=samples, samples_with_vcfs=set())
        stream = xlsx_html._render_streaming(
            book, 30, 40, None, "p", samples, set(),
            xlsx_html.DEFAULT_MAX_CELLS, xlsx_html.DEFAULT_MAX_ROWS)

        full_colour = full.count("background-color")
        assert_true(full_colour > 0, "the full renderer finds CF colour at all")
        assert_eq(stream.count("background-color"), full_colour,
                  "streaming finds the same number of coloured cells")
        assert_eq(stream.count("xlsx-igv-cell"), full.count("xlsx-igv-cell"),
                  "streaming produces the same number of IGV links")
        assert_true(full.count("xlsx-igv-cell") > 0,
                    "IGV links are produced (they hang off the CF colour)")

        print("\n[a dense sheet is bounded in BYTES, not just cells]")
        # 120 x 900 = 108,000 cells, under the cell cap, but nearly all of them
        # coloured with an IGV anchor — the shape that rendered to 48 MB when
        # only cells were counted.
        dense = tmp / "dense.xlsx"
        make_cascade_like(dense, rows=120, cols=900)
        many = {f"SAMPLE{i}" for i in range(1, 120)}
        budget = 2 * 1024 * 1024
        page = xlsx_html._render_streaming(
            dense, 120, 900, None, "p", many, set(),
            xlsx_html.DEFAULT_MAX_CELLS, xlsx_html.DEFAULT_MAX_ROWS,
            max_table_bytes=budget)
        assert_true(len(page) < budget * 1.6,
                    f"page ({len(page)/1e6:.2f} MB) respects a {budget/1e6:.0f} MB budget")
        assert_true("Showing the first" in page, "the trim is disclosed on the page")
        assert_true(page.count("background-color") > 0,
                    "trimming did not cost the colour")

        print("\n[a small sheet keeps the untouched full-fidelity path]")
        small = tmp / "small.xlsx"
        make_cascade_like(small, rows=5, cols=6)
        page = xlsx_html.xlsx_to_html(small, project="p",
                                      samples_with_bams={"SAMPLE1"}, samples_with_vcfs=set())
        assert_true("Showing the first" not in page, "no truncation notice")
        assert_true(page.count("background-color") > 0, "small sheet is coloured")

        print("\nAll xlsx render tests passed.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
