"""IGV cell links when two row labels resolve to the same Step 1 sample folder.

The reported symptom was an influenza SNP table where clicking a SNP did
nothing and no cell even showed a hover — on a build that contained the fix
everybody was looking for. Two separate faults produce that, and both are
about the affordance disappearing without a word:

1. The clade-filtered renderer withholds the IGV link from any row that
   matched a sample folder only by canonicalisation, when more than one label
   reaches that same folder. That is the RIGHT call — a click would open one
   specimen's alignment while labelled as the other's — but it was silent, and
   on influenza names it can take out every row at once. Influenza labels are
   underscore joins led by a submission id, so `26G02776-002_DUCK_...` and
   `26G02776-007_DUCK_...` both extend `26G02776`.

2. The unfiltered streamed renderer had no such pass, so the SAME table served
   without a clade selection linked those cells anyway — and the click opened
   the wrong sample. The two views disagreed about the same rows, and the
   permissive one was the unsafe one.

So the properties pinned here are: both renderers withhold together, both say
why, an exact match is never punished for its look-alikes, and a table with
unambiguous names keeps every link.

Run directly:  python test_igv_ambiguous_names.py
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_xlsx_filter import assert_eq, assert_true, make_group_table

import xlsx_html

# vsnp3 names an influenza position header after the segment it came from, so
# the contig is an isolate name with slashes in it, not an accession.
SEGMENTS = ["PB2", "PB1", "PA", "HA", "NP", "NA", "M", "NS"]


def flu_locus(c: int) -> str:
    seg = SEGMENTS[(c - 2) % len(SEGMENTS)]
    return f"A/owl/ICA/25-003495-001/2024_{seg}:{100 + c}"


def count_variant(html_rows) -> int:
    return sum(len(re.findall(r"xlsx-variant", r)) for r in html_rows)


def count_none(html_rows) -> int:
    return sum(len(re.findall(r"xlsx-igv-none", r)) for r in html_rows)


def build(tmp: Path, labels, name="group.xlsx"):
    book = tmp / name
    make_group_table(book, labels, 10, lambda i, c: "A" if c >= 3 else "C",
                     locus_at=flu_locus)
    return book, len(labels) + 4, 10


def render_both(book, total_rows, total_cols, bams, selection):
    """The same table through the unfiltered and the clade-filtered renderer."""
    plain = xlsx_html.render_window(
        book, total_rows, total_cols, None, "proj",
        samples_with_bams=bams, samples_with_vcfs=set(),
        max_cells=1_000_000, max_rows=1_000, max_table_bytes=64 * 1024 * 1024)
    filtered = xlsx_html.render_filtered_window(
        book, total_rows, total_cols, None, "proj", bams, set(), selection,
        max_cells=1_000_000, max_rows=1_000, max_table_bytes=64 * 1024 * 1024)
    return plain, filtered


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="igv_ambig_"))
    try:
        # ---- THE root cause: underscored label, dashed Step 1 folder -------
        #
        # Step 1 dashes a sample prefix while staging, because vSNP3 splits the
        # sample name at the first underscore and would merge every specimen
        # sharing a prefix. Influenza names are full of underscores, so the
        # folder is all dashes while a label from an imported VCF or an older
        # run keeps them. Nothing matched, so nothing was clickable, and no
        # cell even hovered.
        print("influenza label vs dashed Step 1 folder")
        flu_labels = [
            "26G02488-004_DUCK_2026-02-11_Elkhart-20-p11_IN",
            "26G02776-002_DUCK_2026-02-17_Elkhart-21_IN",
        ]
        on_disk = {lbl.replace("_", "-") for lbl in flu_labels}
        book0, tr0, tc0 = build(tmp, flu_labels, "dashed.xlsx")
        plain0, filtered0 = render_both(book0, tr0, tc0, on_disk, flu_labels)
        assert_true(count_variant(plain0["rows"]) > 0,
                    "unfiltered: cells are clickable")
        assert_eq(count_none(plain0["rows"]), 0, "unfiltered: no dead cells")
        assert_true(count_variant(filtered0["rows"]) > 0,
                    "clade-filtered: cells are clickable")
        assert_eq(plain0["igv_withheld"], 0, "nothing withheld — names are distinct")
        # And the IGV target is the ON-DISK name, or the click 404s.
        assert_true(all(s in on_disk for s in plain0["row_samples"] if s),
                    f"row targets are on-disk stems: {plain0['row_samples']}")

        print("controls: bacterial names are resolved exactly as before")
        assert_eq(xlsx_html._canonical_stem("SRR33643035", {"SRR33643035"}, set()),
                  "SRR33643035", "accession")
        assert_eq(xlsx_html._canonical_stem("19-1234", {"19-1234"}, set()),
                  "19-1234", "bare id")
        assert_eq(xlsx_html._canonical_stem(
            "ERR930304_Sterne_A_Br_75_Denmark", {"ERR930304"}, set()),
            "ERR930304", "metadata-decorated label still takes the prefix rule")
        assert_eq(xlsx_html._canonical_stem("nothing-known", {"other"}, set()),
                  "nothing-known", "unmatched label is returned unchanged")

        # ---- look-alike influenza names, one folder on disk ----------------
        collide = [
            "26G02776-002_DUCK_2026-02-17_Elkhart-21_IN",
            "26G02776-007_DUCK_2026-02-17_Elkhart-21_IN",
            "26G03359-002_DUCK_2026-02-24_LaGrange-72_IN",
        ]
        book, tr, tc = build(tmp, collide)
        # Step 1 staged these under submission-id folders, so both of the first
        # two rows canonicalise onto `26G02776`.
        bams = {"26G02776", "26G03359-002_DUCK_2026-02-24_LaGrange-72_IN"}
        plain, filtered = render_both(book, tr, tc, bams, collide)

        print("unfiltered view, colliding names")
        assert_true(plain["igv_withheld"] == 2,
                    f"two rows lose their links (got {plain['igv_withheld']})")
        assert_true("26G02776" in plain["igv_withheld_stems"],
                    "the colliding folder is named")
        # The third row's name is its own folder, so it is unaffected.
        assert_true(count_variant(plain["rows"]) > 0,
                    "the unambiguous row keeps its links")

        print("clade-filtered view, same table")
        assert_true(filtered["igv_withheld"] >= 1,
                    f"filtered view withholds too (got {filtered['igv_withheld']})")

        print("both views agree")
        assert_eq(plain["igv_withheld"] > 0, filtered["igv_withheld"] > 0,
                  "withheld in one view iff withheld in the other")

        print("the page says why")
        for label, window in (("unfiltered", plain), ("filtered", filtered)):
            page = xlsx_html.compose_page(window)
            assert_true("IGV links are off" in page, f"{label}: banner present")
            assert_true("26G02776" in page, f"{label}: banner names the folder")
            assert_true("Step 1 results pane" in page,
                        f"{label}: banner says what to do instead")

        # ---- an exact match is not punished for its look-alikes ------------
        print("an exact row keeps its link")
        exact = [
            "26G02776",                                    # the folder itself
            "26G02776-002_DUCK_2026-02-17_Elkhart-21_IN",
            "26G02776-007_DUCK_2026-02-17_Elkhart-21_IN",
        ]
        book2, tr2, tc2 = build(tmp, exact, "exact.xlsx")
        plain2, _ = render_both(book2, tr2, tc2, {"26G02776"}, exact)
        assert_eq(plain2["igv_withheld"], 2, "only the two look-alikes lose links")
        assert_true(count_variant(plain2["rows"]) > 0,
                    "the exact row is still clickable")

        # ---- control: distinct names keep every link ----------------------
        print("control — unambiguous influenza names")
        distinct = [
            "26G02776-002_DUCK_2026-02-17_Elkhart-21_IN",
            "26G03359-002_DUCK_2026-02-24_LaGrange-72_IN",
        ]
        book3, tr3, tc3 = build(tmp, distinct, "distinct.xlsx")
        plain3, filtered3 = render_both(book3, tr3, tc3, set(distinct), distinct)
        assert_eq(plain3["igv_withheld"], 0, "unfiltered: nothing withheld")
        assert_eq(filtered3["igv_withheld"], 0, "filtered: nothing withheld")
        assert_true(count_variant(plain3["rows"]) > 0, "unfiltered: links present")
        assert_true(count_variant(filtered3["rows"]) > 0, "filtered: links present")
        assert_eq(count_none(plain3["rows"]), 0, "unfiltered: no dead cells")
        page3 = xlsx_html.compose_page(plain3)
        assert_true("IGV links are off" not in page3,
                    "no banner when nothing was withheld")

        # ---- the influenza contig names still resolve as loci -------------
        # The whole class of bug started here, so keep it asserted alongside.
        print("influenza position headers are recognised")
        assert_true(len(plain3["loci"]) >= 2,
                    f"loci detected: {len(plain3['loci'])}")
        assert_true(any("A/owl/ICA/25-003495-001/2024_" in v
                        for v in plain3["loci"].values()),
                    "segment-named contigs kept verbatim")

        # ---- the helper's own contract ------------------------------------
        print("ambiguous_stems")
        assert_eq(xlsx_html.ambiguous_stems([]), {}, "empty input")
        assert_eq(xlsx_html.ambiguous_stems([("a_x", "a"), ("b_y", "b")]), {},
                  "one label each")
        assert_eq(sorted(xlsx_html.ambiguous_stems(
            [("a_x", "a"), ("a_y", "a")])), ["a"], "two labels on one stem")
        assert_eq(xlsx_html.ambiguous_stems([("a", "a"), ("a_y", "a")]), {},
                  "an exact label plus one canonical match is not ambiguous")
        assert_eq(xlsx_html.ambiguous_stems([("a_x", ""), ("a_y", "")]), {},
                  "rows with no stem are ignored")
        assert_eq(xlsx_html.withheld_note({}, 5), "", "no stems, no note")
        assert_eq(xlsx_html.withheld_note({"a": ["a_x", "a_y"]}, 0), "",
                  "no rows, no note")

        print("\nALL PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
