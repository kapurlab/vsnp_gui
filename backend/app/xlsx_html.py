"""xlsx → self-contained HTML page preserving vSNP3 cascade-table formatting.

Pure function: takes a `Path` to an xlsx file, returns an HTML string
ready to serve as `text/html`. Uses openpyxl (already in the vsnp3 env
as pandas' xlsx engine) — no new dependencies.

When the optional `project` argument is provided AND the rendered sheet
looks like a vSNP3 variant-alignment table (row 1 has `contig:pos`
headers, column 1 has sample names — i.e. `name-All_cascade*` and
`name-All_sorted_*` outputs, or any future table with the same shape),
variant cells (those with a colored fill) gain a hover-revealed pair of
IGV launch links: "this" (single sample) and "all" (every sample in the
table). The cell itself stays non-clickable to prevent gratuitous IGV
launches while scanning.

Preserved formatting:
  - cell fill color
  - font: bold, italic, color, size, family
  - text alignment (h + v)
  - cell borders (collapsed; single 1px outline if any side has a style)
  - merged cells (rowspan / colspan)
  - column widths
  - frozen panes (top row / left columns become `position: sticky`)
  - number formats (basic — ints, floats, percents, dates render as
    displayed value when possible; fallback to raw value)

What we don't try to preserve:
  - rich text within a single cell (the rendered text uses the cell value;
    inline color runs are lost — vSNP3 doesn't use them)
  - charts, images, conditional formatting rules (we read the *resolved*
    fill colors from openpyxl's `data_only=True` workbook load — that
    gets the cached final colors for most spreadsheets we'll see, but
    purely-rule-based conditional formatting without a cached result
    will appear plain)
  - hyperlinks (could be added; not needed for cascade tables)
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import quote

import openpyxl
from openpyxl.utils import get_column_letter, column_index_from_string
from openpyxl.worksheet.cell_range import CellRange


_DEFAULT_FONT_SIZE = 11
_DEFAULT_FONT_COLOR = "FF000000"

_LOCUS_RE = re.compile(r"^[A-Za-z0-9_.\-]+:\d+$")

# --- Very large sheets -------------------------------------------------------
#
# vSNP3 cascade tables on a big group are enormous: a real one here is 1,047
# samples x 10,001 positions = 10.4 MILLION populated cells in a 35 MB file.
# openpyxl's normal (read_write) load builds a full object graph with per-cell
# style objects, which for that file took 47 seconds and 3.6 GB of RAM before a
# single row was rendered — on a memory-capped HPC session that is the
# "Internal Server Error after ten minutes" users were hitting.
#
# Above STREAM_ABOVE_CELLS we switch to openpyxl's read-only streaming mode
# (the same file opens in 0.09 s), and we render a bounded window of the sheet.
# The window is not a preference — no browser can lay out ten million table
# cells, and each variant cell additionally carries an IGV launch anchor of
# ~450 bytes, so the full grid would be a multi-gigabyte page. The page says
# plainly how much it is showing and links the xlsx for the rest.
# The streaming renderer now reproduces the full renderer's output exactly on
# these files (same colours, same IGV links — verified cell-for-cell), so the
# threshold is set low: the full path's only remaining advantages are merged
# cells and CF rules that reference other cells, neither of which vSNP3 emits,
# and it is the path with no size bound at all.
STREAM_ABOVE_CELLS = 20_000
# Rendered-cell budget for the streaming path. ~120k cells keeps the page in
# the low tens of MB (measured), which browsers open in a few seconds.
DEFAULT_MAX_CELLS = 120_000
DEFAULT_MAX_ROWS = 5_000
# …and a cap on the RENDERED BYTES, which is what actually decides whether a
# page opens and how much disk a cached preview costs. A cell budget alone
# bounds neither: bytes per cell range from ~30 (a plain cell) to ~450 (a
# variant cell carrying an IGV launch anchor), so a 480 KB sheet of mostly
# variant cells rendered to 48 MB while a 35 MB sheet rendered to 1.4 MB.
DEFAULT_MAX_TABLE_BYTES = 12 * 1024 * 1024
_NON_SAMPLE_LABELS = {
    "root", "mq", "annotation", "position not annotated",
    "n:p207l, orf1ab",  # vSNP3 sometimes carries annotation hints into col 1; skip
}


def _strip_vcf_suffix(s: str) -> str:
    """Reduce a variant-table sample label to the stem used by Step 1.

    vSNP3 variant-alignment tables put names like
    `hCoV-19-deer-USA-IA-201788-2020-EPI_zc.vcf` in column 1. Step 1 sample
    directories are named after the bare stem.
    """
    out = s.strip()
    for suffix in (".vcf.gz", ".vcf", "_zc"):
        if out.lower().endswith(suffix):
            out = out[: -len(suffix)]
    return out


def _canonical_stem(label: str, *stem_sets) -> str:
    """Map a variant-table label back to the bare sample stem used on disk.

    vSNP3 step2 relabels samples with descriptive metadata
    (`ERR930304_Sterne_A_Br_75_..._Denmark`), while step1 dirs and
    `vcf_database/` files are named after the bare id (`ERR930304`). Imported
    projects therefore show long labels in the cascade table that never match
    the on-disk stems — blanking "this" and tripping a false "no Step 1
    outputs". Resolve the label to the longest known stem ``S`` such that
    ``label == S`` or ``label`` starts with ``S + "_"``; fall back to the label
    unchanged when nothing matches (or when no stem sets were supplied)."""
    known: set[str] = set()
    for s in stem_sets:
        if s:
            known |= s
    if not known or label in known:
        return label
    best: str | None = None
    for s in known:
        if label.startswith(s + "_") and (best is None or len(s) > len(best)):
            best = s
    return best if best is not None else label


def sheet_extent(xlsx_path: Path) -> tuple[int, int]:
    """(rows, cols) of the active sheet, without loading its cells.

    Read-only mode reads only the sheet's declared dimension, so this is
    effectively free even on a 35 MB workbook — it is what lets the caller
    decide between the full-fidelity and streaming renderers before paying
    for either.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    try:
        ws = wb.active
        return int(ws.max_row or 0), int(ws.max_column or 0)
    finally:
        wb.close()


def _sheet_layout(xlsx_path: Path) -> dict:
    """Column widths and frozen panes, straight from the sheet XML.

    Read-only worksheets expose neither `column_dimensions` nor
    `freeze_panes`, but both live in the sheet XML's header — before
    `<sheetData>` — so they can be read from the first few KB of the entry
    without touching the millions of cells that follow. Returns
    {"widths": {col_index: width_units}, "default_width": float|None,
     "freeze_row": int, "freeze_col": int}; empty/zero values on any problem,
    since layout is cosmetic and must never break a preview.
    """
    out = {"widths": {}, "default_width": None, "freeze_row": 0, "freeze_col": 0}
    try:
        import zipfile
        from xml.etree import ElementTree as ET

        with zipfile.ZipFile(xlsx_path) as zf:
            name = next((n for n in zf.namelist()
                         if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")),
                        None)
            if not name:
                return out
            with zf.open(name) as fh:
                for event, el in ET.iterparse(fh, events=("start", "end")):
                    tag = el.tag.split("}")[-1]
                    if event == "start" and tag == "sheetData":
                        break                      # everything we want precedes the cells
                    if event != "end":
                        continue
                    if tag == "sheetFormatPr":
                        w = el.get("defaultColWidth")
                        if w:
                            out["default_width"] = float(w)
                    elif tag == "col":
                        w = el.get("width")
                        if w:
                            lo = int(el.get("min", 1))
                            hi = int(el.get("max", lo))
                            # Guard against the "col span to 16384" idiom that
                            # would otherwise build a dict of every column.
                            for ci in range(lo, min(hi, lo + 20_000) + 1):
                                out["widths"][ci] = float(w)
                    elif tag == "pane":
                        out["freeze_row"] = int(float(el.get("ySplit") or 0))
                        out["freeze_col"] = int(float(el.get("xSplit") or 0))
                    el.clear()
    except Exception:
        return {"widths": {}, "default_width": None, "freeze_row": 0, "freeze_col": 0}
    return out


class _XmlCfRule:
    """A conditional-formatting rule parsed straight from the sheet XML.

    Deliberately not an openpyxl Rule: it only carries the attributes
    _cf_rule_matches() reads, so the streaming renderer can evaluate rules
    without the full (read-write) workbook load that exposes them normally.
    """
    __slots__ = ("type", "operator", "text", "formula", "dxfId", "priority")

    def __init__(self, el):
        self.type = el.get("type")
        self.operator = el.get("operator")
        self.text = el.get("text")
        try:
            self.dxfId = int(el.get("dxfId")) if el.get("dxfId") is not None else None
        except ValueError:
            self.dxfId = None
        try:
            self.priority = int(el.get("priority") or 0)
        except ValueError:
            self.priority = 0
        self.formula = [
            (child.text or "") for child in el
            if child.tag.split("}")[-1] == "formula"
        ]


class _NoRefSheet:
    """Stand-in worksheet for CF evaluation while streaming.

    _resolve_cf_value() falls back to reading another cell for rules whose
    comparison value is a cell reference. Read-only sheets have no random
    access, so those rules simply don't resolve here — every rule vSNP3
    actually writes compares against a literal, which needs no sheet at all.
    """
    def cell(self, *_args, **_kwargs):
        raise LookupError("no random access while streaming")


def _cf_from_sheet_xml(xlsx_path: Path) -> list[tuple[object, list]]:
    """Conditional-formatting ranges + rules, read from the sheet XML.

    This exists because EVERY colour in a vSNP3 SNP table comes from
    conditional formatting — the cells carry no direct fill at all — and
    openpyxl's read-only worksheets do not expose `conditional_formatting`.
    Without this the streaming renderer produced a perfectly laid out,
    completely colourless table, and no IGV links (which are gated on a cell
    having a background).

    In the OOXML schema `conditionalFormatting` elements follow `sheetData`,
    so they sit near the END of the sheet part. The stream is decompressed
    once, keeping only a rolling tail, and the blocks are parsed out of that —
    no cell objects are built.

    Returns [(CellRange, [rules]), ...]; empty on any problem, because a
    preview without colour is bad but a preview that raises is worse.
    """
    TAIL = 8 * 1024 * 1024
    try:
        import zipfile
        from xml.etree import ElementTree as ET

        with zipfile.ZipFile(xlsx_path) as zf:
            name = next((n for n in zf.namelist()
                         if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")),
                        None)
            if not name:
                return []
            tail = b""
            with zf.open(name) as fh:
                while True:
                    chunk = fh.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    tail = (tail + chunk)[-TAIL:]
        text = tail.decode("utf-8", errors="replace")
        out: list[tuple[object, list]] = []
        for m in re.finditer(r"<conditionalFormatting\b.*?</conditionalFormatting>",
                             text, re.S):
            block = m.group(0)
            try:
                el = ET.fromstring(block)
            except ET.ParseError:
                continue
            sqref = el.get("sqref") or ""
            rules = [_XmlCfRule(child) for child in el
                     if child.tag.split("}")[-1] == "cfRule"]
            if not rules:
                continue
            for part in str(sqref).split():
                try:
                    out.append((CellRange(part), rules))
                except Exception:
                    continue
        return out
    except Exception:
        return []


def _detect_variant_table(ws) -> dict | None:
    """Return variant-alignment-table metadata if `ws` matches the shape.

    Matches vSNP3 `name-All_cascade*_table-*.xlsx` and
    `name-All_sorted_table-*.xlsx` outputs (and any future table with the
    same shape — detection is pattern-based, not filename-based).

    Heuristic: row 1 has ≥2 cells matching `<contig>:<pos>`, and column 1
    (rows 2+) has sample-like labels. Returns:

        {
          "positions": {col_idx: "contig:pos", ...},
          "samples":   {row_idx: sample_stem, ...},  # in row order
        }

    or None when the sheet doesn't look like a variant-alignment table — in which
    case the renderer skips the IGV-link injection entirely.
    """
    if ws.max_row < 2 or ws.max_column < 2:
        return None
    positions: dict[int, str] = {}
    for cell in ws[1]:
        v = cell.value
        if v is None:
            continue
        s = str(v).strip()
        if _LOCUS_RE.match(s):
            positions[cell.column] = s
    if len(positions) < 2:
        return None
    samples: dict[int, str] = {}
    for row_idx in range(2, ws.max_row + 1):
        v = ws.cell(row=row_idx, column=1).value
        if v is None:
            continue
        raw = str(v).strip()
        if not raw or raw.lower() in _NON_SAMPLE_LABELS:
            continue
        stem = _strip_vcf_suffix(raw)
        if not stem:
            continue
        samples[row_idx] = stem
    if not samples:
        return None
    return {"positions": positions, "samples": samples}


def _igv_cell_html(
    value: str,
    project: str,
    this_stem: str,
    locus: str,
    this_loadable: bool = True,
    this_calls_only: bool = False,
) -> str:
    """Wrap a variant cell's value in a full-cell IGV-launch anchor.

    The whole cell is clickable — clicking anywhere in a colored variant
    cell opens the row's sample in IGV at this locus. (Previously a small
    "↗ this" text link in the corner was the only target; selecting the
    SNP cell itself is more direct.) The anchor fills the cell via
    ``display:block`` and inherits the cell's color/alignment so the
    nucleotide letter still reads normally.

    Same additive single-window behavior as before: the anchor uses
    ``target="vsnp_igv"`` (a named window) and an onclick that posts to the
    existing IGV tab when one is open, so variant-after-variant clicks build
    up the cohort in one IGV view rather than spawning N tabs. Modifier-click
    (cmd / ctrl / shift / middle button) bypasses the handler so users can
    still force a fresh tab.

    The URL is constructed relative to the current preview path so it
    survives the OOD proxy prefix: the preview is served at
    ``/api/projects/{p}/preview-xlsx``, so ``../../../`` climbs back out
    to the SPA root regardless of the OOD rnode prefix in front of it
    (``/rnode/host/port/api/projects/p/...``).

    ``this_loadable``: false only when the sample has NEITHER a BAM nor an
    imported VCF — the cell renders plain (not clickable) with an
    explanatory tooltip, since clicking would land in an empty IGV.
    ``this_calls_only``: true when the sample has an imported VCF but no
    BAM; the cell stays clickable (calls-only IGV anchored to the project
    reference) and is italicized with a tooltip noting the limitation.
    """
    if not this_loadable:
        return (
            '<span class="xlsx-igv-disabled" '
            f'title="No data for {html.escape(this_stem)} — neither Step 1 BAM '
            f'nor imported VCF in step2/vcf_database/.">{value}</span>'
        )
    enc_proj = quote(project, safe="")
    enc_locus = quote(locus, safe="")
    onclick = (
        "if(event.metaKey||event.ctrlKey||event.shiftKey||event.button===1)return true;"
        "window.__vsnpLaunchIgv(this.href);return false;"
    )
    this_track = f"{enc_proj}:{quote(this_stem, safe='')}"
    this_href = f"../../../?view=igv&tracks={this_track}&locus={enc_locus}"
    if this_calls_only:
        cls = "xlsx-igv-cell xlsx-igv-calls-only"
        title = (
            f"Open {html.escape(this_stem)} in IGV at {html.escape(locus)} "
            "(calls only — imported VCF, no BAM to load)"
        )
    else:
        cls = "xlsx-igv-cell"
        title = f"Open {html.escape(this_stem)} in IGV at {html.escape(locus)}"
    return (
        f'<a class="{cls}" href="{html.escape(this_href, quote=True)}" '
        f'target="vsnp_igv" rel="noopener" onclick="{onclick}" '
        f'title="{title}">{value}</a>'
    )


def _rgb_hex(color) -> str | None:
    """Extract a `#rrggbb` string from openpyxl Color, or None if absent/transparent."""
    if color is None:
        return None
    rgb = getattr(color, "rgb", None)
    if not rgb or not isinstance(rgb, str):
        return None
    # ARGB form like 'FFRRGGBB' → take last 6 chars
    if len(rgb) == 8:
        if rgb[:2] == "00":  # fully transparent
            return None
        return f"#{rgb[-6:].lower()}"
    if len(rgb) == 6:
        return f"#{rgb.lower()}"
    return None


def _cell_rotation_class(cell) -> str:
    """Map openpyxl's text_rotation (Excel encoding) to one of our CSS classes.

    Excel OOXML stores rotation as:
      - 0          → horizontal
      - 1..90      → that many degrees counter-clockwise from horizontal
                     (90 = reads bottom-to-top)
      - 91..180    → (rotation - 90) degrees clockwise from horizontal
                     (180 = reads top-to-bottom)
      - 255        → vertically stacked characters
    vSNP3 cascade tables use 90 on the variant-position header row and 180
    on the annotation row. We map both to vertical writing-mode + transform
    so the cell becomes a narrow tall column.
    """
    rot = (cell.alignment.text_rotation or 0) if cell.alignment else 0
    if rot == 0:
        return ""
    if 1 <= rot <= 90:
        return "xlsx-rot-up"
    if 91 <= rot <= 180:
        return "xlsx-rot-down"
    if rot == 255:
        return "xlsx-rot-stacked"
    return ""


def _cell_inline_style(cell) -> str:
    parts: list[str] = []

    # Fill (background)
    fill = cell.fill
    if fill and getattr(fill, "patternType", None) in ("solid", "darkVertical", "darkHorizontal"):
        bg = _rgb_hex(fill.fgColor)
        if bg:
            parts.append(f"background-color: {bg}")

    # Font
    font = cell.font
    if font:
        if font.bold:
            parts.append("font-weight: 600")
        if font.italic:
            parts.append("font-style: italic")
        fc = _rgb_hex(font.color)
        if fc and fc != "#000000":
            parts.append(f"color: {fc}")
        if font.size and font.size != _DEFAULT_FONT_SIZE:
            parts.append(f"font-size: {float(font.size):.1f}px")
        if font.name and font.name not in ("Calibri", "Arial"):
            parts.append(f"font-family: '{font.name}', sans-serif")

    # Alignment
    align = cell.alignment
    if align:
        if align.horizontal:
            parts.append(f"text-align: {align.horizontal}")
        if align.vertical:
            vmap = {"center": "middle", "top": "top", "bottom": "bottom"}
            parts.append(f"vertical-align: {vmap.get(align.vertical, align.vertical)}")
        if align.wrap_text:
            parts.append("white-space: pre-wrap")

    # Borders — collapse to a single hint when any side has a style
    border = cell.border
    if border:
        for side in ("top", "right", "bottom", "left"):
            b = getattr(border, side, None)
            if b and b.style:
                bc = _rgb_hex(b.color) or "#888"
                parts.append(f"border-{side}: 1px solid {bc}")

    return "; ".join(parts)


def _format_cell_value(cell) -> str:
    """Render a cell value as HTML-safe text, honoring the number format when straightforward."""
    v = cell.value
    if v is None:
        return ""
    nf = (cell.number_format or "General")
    # Percent
    if isinstance(v, (int, float)) and "%" in nf:
        # heuristic: '0.00%' style means multiply by 100; vSNP3 usually stores
        # the displayed value already, so be conservative — only multiply
        # when value is in [0, 1].
        try:
            if 0 <= float(v) <= 1:
                return html.escape(f"{float(v) * 100:.1f}%")
        except (TypeError, ValueError):
            pass
    # Integer-like
    if isinstance(v, float) and v.is_integer():
        return html.escape(str(int(v)))
    # Non-integer float with no specific number format → vsnp3's cascade-table
    # MQ row produces values like 59.5833333333 / 59.9090909091 because it's
    # averaging integer-bounded scores across samples. Python's `str()` gives
    # full precision repr which is unreadable in the preview AND produces
    # variable-width cells (59.58 vs 60 vs 59.5) that look ragged. If the
    # cell author didn't specify a format, round to a whole number — the
    # MQ-average use case is what produces these values and 1-point
    # differences (60 vs 59) aren't biologically meaningful.
    if isinstance(v, float) and nf in ("General", "@"):
        return html.escape(str(int(round(v))))
    return html.escape(str(v))


def _parse_single_ref(formula: str) -> tuple[bool, str, bool, int] | None:
    """Parse a single-cell ref like B$2 / $A2 / A1 / $C$5.

    Returns (col_absolute, col_letter, row_absolute, row_num) or None if not a single ref."""
    s = formula.strip()
    m = re.fullmatch(r"(\$?)([A-Z]+)(\$?)(\d+)", s)
    if not m:
        return None
    col_abs_marker, col_letter, row_abs_marker, row_num = m.groups()
    return (col_abs_marker == "$", col_letter, row_abs_marker == "$", int(row_num))


def _resolve_cf_value(formula: str, ws, anchor_row: int, anchor_col: int, cell_row: int, cell_col: int):
    """Resolve a CF rule's formula to a Python literal for comparison.

    Handles: number literal, quoted string, single cell ref (relative or absolute
    parts honoured). Returns the resolved value or None when we can't evaluate."""
    s = formula.strip()
    # Number
    try:
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)
    except ValueError:
        pass
    # Quoted string
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    # Single cell ref
    parsed = _parse_single_ref(s)
    if parsed is None:
        return None
    col_abs, col_letter, row_abs, row_num = parsed
    ref_col = column_index_from_string(col_letter)
    ref_row = row_num
    if not col_abs:
        ref_col += (cell_col - anchor_col)
    if not row_abs:
        ref_row += (cell_row - anchor_row)
    if ref_row < 1 or ref_col < 1:
        return None
    try:
        return ws.cell(row=ref_row, column=ref_col).value
    except Exception:
        return None


def _cf_rule_matches(rule, cell, ws, anchor_row: int, anchor_col: int) -> bool:
    """Return True if a conditional-formatting rule applies to a cell."""
    cv = cell.value
    if rule.type == "containsText":
        target = rule.text
        if target is None or cv is None:
            return False
        return str(target) in str(cv)
    if rule.type == "notContainsText":
        target = rule.text
        if target is None:
            return False
        return str(target) not in str(cv or "")
    if rule.type == "cellIs":
        if not rule.formula:
            return False
        rhs = _resolve_cf_value(rule.formula[0], ws, anchor_row, anchor_col, cell.row, cell.column)
        if rhs is None:
            return False
        op = rule.operator
        try:
            if op == "equal":
                return cv == rhs or str(cv) == str(rhs)
            if op == "notEqual":
                return cv != rhs and str(cv) != str(rhs)
            lhs_f = float(cv) if cv is not None else None
            rhs_f = float(rhs)
            if lhs_f is None:
                return False
            return {
                "lessThan": lhs_f < rhs_f,
                "lessThanOrEqual": lhs_f <= rhs_f,
                "greaterThan": lhs_f > rhs_f,
                "greaterThanOrEqual": lhs_f >= rhs_f,
            }.get(op, False)
        except (TypeError, ValueError):
            return False
    return False


def _dxf_style_fragments(dxf) -> list[str]:
    """Translate an openpyxl differential format (dxf) into CSS fragments."""
    out: list[str] = []
    if dxf is None:
        return out
    fill = getattr(dxf, "fill", None)
    if fill:
        # CF dxf fills typically expose the color via bgColor; fall back to fgColor.
        for color_attr in ("bgColor", "fgColor"):
            color = getattr(fill, color_attr, None)
            hexc = _rgb_hex(color)
            if hexc:
                out.append(f"background-color: {hexc}")
                break
    font = getattr(dxf, "font", None)
    if font:
        fc = _rgb_hex(getattr(font, "color", None))
        if fc and fc != "#000000":
            out.append(f"color: {fc}")
        # openpyxl's dxf font uses .b for bold (not .bold)
        if getattr(font, "b", None) is True:
            out.append("font-weight: 600")
        if getattr(font, "i", None) is True:
            out.append("font-style: italic")
    return out


def _build_cf_extras(ws, dxfs) -> dict[str, list[str]]:
    """Walk every conditional-formatting range × cell, applying first-match rule.

    Returns a dict mapping cell coordinate → list of CSS style fragments to
    append to that cell's inline style.
    """
    extras: dict[str, list[str]] = {}
    try:
        cf_rules = ws.conditional_formatting._cf_rules
    except Exception:
        return extras
    for cf_range, rules in cf_rules.items():
        sqref = cf_range.sqref if hasattr(cf_range, "sqref") else str(cf_range)
        # sqref may be space-separated multi-range ("A1:A5 B1:B5"). Split.
        for r_str in str(sqref).split():
            try:
                cr = CellRange(r_str)
            except Exception:
                continue
            anchor_row, anchor_col = cr.min_row, cr.min_col
            for r in range(cr.min_row, cr.max_row + 1):
                for c in range(cr.min_col, cr.max_col + 1):
                    cell = ws.cell(row=r, column=c)
                    if cell.value is None or cell.value == "":
                        continue
                    # First-match-wins (Excel's default with priority-ordered rules).
                    for rule in rules:
                        if _cf_rule_matches(rule, cell, ws, anchor_row, anchor_col):
                            dxf_id = getattr(rule, "dxfId", None)
                            if dxf_id is None or dxf_id < 0 or dxf_id >= len(dxfs):
                                break
                            frags = _dxf_style_fragments(dxfs[dxf_id])
                            if frags:
                                extras.setdefault(cell.coordinate, []).extend(frags)
                            break
    return extras


def _cf_fragments_for(cell, row_idx: int, col_idx: int,
                      cf_ranges: list, dxfs: list, cf_sheet) -> list[str]:
    """CSS fragments a cell picks up from conditional formatting, first match wins.

    Mirrors _build_cf_extras, but evaluated per cell as the sheet streams past
    rather than by walking every CF range up front — the ranges in these files
    span the whole table, so materialising them would defeat the point.
    """
    if not cf_ranges:
        return []
    for cr, rules in cf_ranges:
        if not (cr.min_row <= row_idx <= cr.max_row
                and cr.min_col <= col_idx <= cr.max_col):
            continue
        for rule in sorted(rules, key=lambda r: r.priority or 0):
            try:
                if not _cf_rule_matches(rule, cell, cf_sheet, cr.min_row, cr.min_col):
                    continue
            except Exception:
                continue
            if rule.dxfId is None or rule.dxfId >= len(dxfs):
                continue
            frags = _dxf_style_fragments(dxfs[rule.dxfId])
            if frags:
                return frags
    return []


def _render_streaming(
    xlsx_path: Path,
    total_rows: int,
    total_cols: int,
    title: str | None,
    project: str | None,
    samples_with_bams: set[str] | None,
    samples_with_vcfs: set[str] | None,
    max_cells: int,
    max_rows: int,
    max_table_bytes: int = None,
) -> str:
    """Render a bounded window of a very large sheet in one streaming pass.

    Same per-cell appearance and same IGV behaviour as the full renderer — it
    reuses the identical style/value/link helpers, which all work on
    read-only cells. What it cannot do is random access, so the
    variant-table detection (row 1 = loci, column 1 = samples) is built up
    during the pass instead of probed up front.

    Conditional-formatting overlays are skipped: the rules are not readable
    in read-only mode. vSNP3 cascade tables colour their variant cells with
    direct fills, which ARE read, so what a user actually looks at survives.
    """
    if max_table_bytes is None:
        max_table_bytes = DEFAULT_MAX_TABLE_BYTES
    render_rows = min(total_rows, max_rows)
    render_cols = max(1, min(total_cols, max_cells // max(1, render_rows)))

    layout = _sheet_layout(xlsx_path)
    default_width_units = layout["default_width"] or 8.43
    colgroup_parts = [
        f'<col style="width: '
        f'{max(int(round(layout["widths"].get(ci, default_width_units) * 7)), 16)}px">'
        for ci in range(1, render_cols + 1)
    ]
    freeze_row, freeze_col = layout["freeze_row"], layout["freeze_col"]

    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    try:
        ws = wb.active
        sheet_title = ws.title or "Sheet1"
        # Conditional formatting is where ALL of a vSNP3 table's colour lives,
        # and read-only sheets don't expose it — so it comes from the XML.
        dxfs = []
        try:
            dxfs = list(wb._differential_styles.styles)
        except Exception:
            dxfs = []
        cf_ranges = _cf_from_sheet_xml(xlsx_path) if dxfs else []
        cf_sheet = _NoRefSheet()
        positions: dict[int, str] = {}
        # Rows are buffered as per-cell lists rather than one flat string, so
        # the column count can be trimmed after the fact — see the byte budget
        # below, which is only measurable once real cells have been rendered.
        rows_cells: list[list[str]] = []
        effective_cols = render_cols
        bytes_so_far = 0

        # Row/column indices are tracked by position, not read off the cell:
        # a read-only sheet yields `EmptyCell` for gaps, and those carry no
        # coordinates at all (nor styles) — reading cell.row on one is an
        # AttributeError, not a missing value.
        for row_idx, row in enumerate(
                ws.iter_rows(min_row=1, max_row=render_rows,
                             min_col=1, max_col=render_cols), start=1):
            if not row:
                continue
            # Row 1 carries the locus headers; capture them before any data row
            # needs them (they arrive first, so a single pass is enough).
            if row_idx == 1 and project:
                for col_idx, cell in enumerate(row, start=1):
                    v = getattr(cell, "value", None)
                    if v is not None and _LOCUS_RE.match(str(v).strip()):
                        positions[col_idx] = str(v).strip()

            # Column 1 of a data row is the sample label.
            row_stem = ""
            if project and positions and row_idx > 1:
                first = getattr(row[0], "value", None)
                raw = str(first).strip() if first is not None else ""
                if raw and raw.lower() not in _NON_SAMPLE_LABELS:
                    stem = _strip_vcf_suffix(raw)
                    if stem:
                        row_stem = _canonical_stem(
                            stem, samples_with_bams, samples_with_vcfs)

            cells: list[str] = []
            for col_idx, cell in enumerate(row, start=1):
                if col_idx > effective_cols:
                    break
                if not hasattr(cell, "column"):     # EmptyCell: no value, no style
                    cells.append("<td></td>")
                    continue
                inline = _cell_inline_style(cell)
                # CF wins over direct cell styles for the properties it sets,
                # so its fragments go last (later declarations override).
                cf_frags = _cf_fragments_for(
                    cell, row_idx, col_idx, cf_ranges, dxfs, cf_sheet)
                if cf_frags:
                    inline = "; ".join([p for p in (inline,) if p] + cf_frags)
                classes = []
                if row_idx <= freeze_row:
                    classes.append("xlsx-sticky-top")
                if col_idx <= freeze_col:
                    classes.append("xlsx-sticky-left")
                rot_class = _cell_rotation_class(cell)
                if rot_class:
                    classes.append(rot_class)
                value = _format_cell_value(cell)
                cell_inner = value
                if (row_stem and col_idx in positions
                        and "background-color" in inline):
                    has_bam = samples_with_bams is None or row_stem in samples_with_bams
                    has_vcf = (samples_with_vcfs is not None
                               and row_stem in samples_with_vcfs)
                    this_loadable = (
                        samples_with_bams is None and samples_with_vcfs is None
                    ) or has_bam or has_vcf
                    cell_inner = _igv_cell_html(
                        value,
                        project=project,
                        this_stem=row_stem,
                        locus=positions[col_idx],
                        this_loadable=this_loadable,
                        this_calls_only=(not has_bam) and has_vcf,
                    )
                    classes.append("xlsx-variant")
                attrs = f' class="{" ".join(classes)}"' if classes else ""
                style_attr = f' style="{inline}"' if inline else ""
                cells.append(f"<td{attrs}{style_attr}>{cell_inner}</td>")
            rows_cells.append(cells)
            bytes_so_far += sum(len(c) for c in cells)

            # Bound the OUTPUT, not just the cell count. Bytes per cell vary by
            # more than tenfold — a plain cell is ~30 bytes, a coloured variant
            # cell carrying an IGV anchor ~450 — so a cell budget bounds
            # nothing useful: a 480 KB sheet rendered to 48 MB, which is a page
            # no browser enjoys and a cache entry larger than the spreadsheet
            # that produced it.
            #
            # Enforced continuously rather than predicted once. A single
            # sample row is a bad estimator here: the first rows of a cascade
            # table are headers and annotations with no colour at all, so
            # estimating from them left the budget 5x overshot by the time the
            # dense rows arrived. After each row, project the finished size
            # from what has actually been emitted and narrow the window if it
            # is heading over — already-buffered rows are trimmed to match, so
            # the result is the same as if the width had been right all along.
            if bytes_so_far and effective_cols > 1:
                projected = bytes_so_far / len(rows_cells) * render_rows
                if projected > max_table_bytes:
                    scale = max_table_bytes / projected
                    narrowed = max(1, int(effective_cols * scale))
                    if narrowed < effective_cols:
                        effective_cols = narrowed
                        rows_cells = [r[:effective_cols] for r in rows_cells]
                        bytes_so_far = sum(len(c) for r in rows_cells for c in r)
    finally:
        wb.close()

    # Trim to the width the byte budget allows (a no-op when nothing was over).
    if effective_cols < render_cols:
        rows_cells = [r[:effective_cols] for r in rows_cells]
        colgroup_parts = colgroup_parts[:effective_cols]
    render_cols = min(render_cols, effective_cols)
    truncated = render_rows < total_rows or render_cols < total_cols
    rows_html = ["<tr>" + "".join(r) + "</tr>" for r in rows_cells]

    notice = ""
    if truncated:
        notice = (
            '<div class="xlsx-truncated">'
            f'<strong>Showing the first {render_rows:,} of {total_rows:,} rows '
            f'and {render_cols:,} of {total_cols:,} columns.</strong> '
            f'This sheet holds about {total_rows * total_cols:,} cells — far more '
            'than a browser can lay out, so the preview renders the leading '
            'block, which for a cascade table is the most informative end. '
            'Use <em>Download xlsx</em> above for the complete table, and open '
            'it in Excel or LibreOffice.'
            '</div>'
        )

    table_html = ('<table class="xlsx"><colgroup>' + "".join(colgroup_parts)
                  + "</colgroup>" + "".join(rows_html) + "</table>")
    return _PAGE_TEMPLATE.format(
        title=html.escape(title or xlsx_path.name),
        filename=html.escape(xlsx_path.name),
        sheet=html.escape(sheet_title),
        rows=total_rows,
        cols=total_cols,
        notice=notice,
        table=table_html,
    )


def xlsx_to_html(
    xlsx_path: Path,
    title: str | None = None,
    project: str | None = None,
    samples_with_bams: set[str] | None = None,
    samples_with_vcfs: set[str] | None = None,
    max_cells: int = DEFAULT_MAX_CELLS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> str:
    """Render the first (active) sheet of an xlsx file as a self-contained HTML page.

    When ``project`` is provided and the sheet looks like a vSNP3 variant-alignment
    table, variant cells (those with a colored fill) become clickable
    IGV-launch targets — clicking anywhere in the cell opens the row's
    sample in the IgvStandalone viewer at the cell's locus (additive
    across clicks).

    ``samples_with_bams`` (optional) is the set of step1 sample names for
    which a BAM exists on disk.

    ``samples_with_vcfs`` (optional) is the set of sample names that have
    an imported VCF in ``step2/vcf_database/`` (no BAM, calls-only IGV).

    A cell is clickable if the sample is in EITHER set; calls-only mode is
    indicated in the tooltip (and italics) when only the VCF is present.
    The cell renders plain/non-clickable only when the sample is in
    neither set (genuinely nothing to load).
    """
    xlsx_path = Path(xlsx_path)
    # Decide the renderer from the sheet's declared size, which read-only mode
    # answers without reading any cells. Anything of ordinary size keeps the
    # full-fidelity path below, byte for byte as before; only sheets that the
    # old path could not open at all take the streaming route.
    try:
        total_rows, total_cols = sheet_extent(xlsx_path)
    except Exception:
        total_rows = total_cols = 0
    if total_rows * total_cols > STREAM_ABOVE_CELLS:
        return _render_streaming(
            xlsx_path, total_rows, total_cols, title, project,
            samples_with_bams, samples_with_vcfs, max_cells, max_rows,
        )

    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=False)
    ws = wb.active
    dxfs = list(wb._differential_styles.styles) if hasattr(wb, "_differential_styles") else []
    cf_extras = _build_cf_extras(ws, dxfs)
    vtable = _detect_variant_table(ws) if project else None

    # Pre-compute merged-cell map: anchor coordinate → (rowspan, colspan);
    # all other cells in the merge get skipped.
    merge_anchors: dict[str, tuple[int, int]] = {}
    merged_skip: set[str] = set()
    for mr in ws.merged_cells.ranges:
        anchor = ws.cell(row=mr.min_row, column=mr.min_col).coordinate
        merge_anchors[anchor] = (mr.max_row - mr.min_row + 1, mr.max_col - mr.min_col + 1)
        for r in range(mr.min_row, mr.max_row + 1):
            for c in range(mr.min_col, mr.max_col + 1):
                coord = ws.cell(row=r, column=c).coordinate
                if coord != anchor:
                    merged_skip.add(coord)

    # Column widths — openpyxl stores column-range widths under a single
    # anchor key with `.min` / `.max` spanning the range (e.g. one entry on
    # `B` covers cols 2..52). Resolve to a per-column-index map first, then
    # emit the <colgroup>. Excel char-units → px is ~7 for Calibri 11pt.
    col_widths_units: dict[int, float] = {}
    for _letter, dim in ws.column_dimensions.items():
        if dim.width is None:
            continue
        lo = dim.min if dim.min is not None else 1
        hi = dim.max if dim.max is not None else lo
        for ci in range(lo, hi + 1):
            col_widths_units[ci] = float(dim.width)
    default_width_units = (
        float(ws.sheet_format.defaultColWidth)
        if ws.sheet_format and ws.sheet_format.defaultColWidth
        else 8.43  # Excel's documented default
    )
    colgroup_parts: list[str] = []
    for col_idx in range(1, ws.max_column + 1):
        w_units = col_widths_units.get(col_idx, default_width_units)
        width_px = max(int(round(w_units * 7)), 16)  # never collapse a column to invisible
        colgroup_parts.append(f'<col style="width: {width_px}px">')

    # Frozen panes: openpyxl exposes ws.freeze_panes as the top-left of the
    # *unfrozen* area, e.g. "B2" means row 1 + col A are frozen.
    freeze_row, freeze_col = 0, 0
    if ws.freeze_panes:
        try:
            anchor_cell = ws[ws.freeze_panes]
            freeze_row = anchor_cell.row - 1
            freeze_col = anchor_cell.column - 1
        except Exception:
            freeze_row, freeze_col = 0, 0

    rows_html: list[str] = []
    for row in ws.iter_rows():
        if not row:
            continue
        # Per-row height. Excel stores row height in points (1pt ≈ 1.333px).
        row_idx = row[0].row
        row_dim = ws.row_dimensions.get(row_idx)
        row_style = ""
        if row_dim and row_dim.height:
            row_style = f' style="height: {int(round(row_dim.height * 1.333))}px"'
        rows_html.append(f"<tr{row_style}>")
        for cell in row:
            if cell.coordinate in merged_skip:
                continue
            inline_parts = [_cell_inline_style(cell)] if _cell_inline_style(cell) else []
            cf_frags = cf_extras.get(cell.coordinate)
            if cf_frags:
                # CF wins over direct cell styles for the properties it sets,
                # so put it last (later declarations override).
                inline_parts.extend(cf_frags)
            inline = "; ".join(inline_parts)
            attrs = ""
            if cell.coordinate in merge_anchors:
                rs, cs = merge_anchors[cell.coordinate]
                if rs > 1:
                    attrs += f' rowspan="{rs}"'
                if cs > 1:
                    attrs += f' colspan="{cs}"'
            # Class bundle: sticky pane hints + rotation
            classes = []
            if cell.row <= freeze_row:
                classes.append("xlsx-sticky-top")
            if cell.column <= freeze_col:
                classes.append("xlsx-sticky-left")
            rot_class = _cell_rotation_class(cell)
            if rot_class:
                classes.append(rot_class)
            if classes:
                attrs += f' class="{" ".join(classes)}"'
            value = _format_cell_value(cell)
            # Cascade-table IGV launch: only on variant cells in a detected
            # variant-alignment table when a project context is available. A "variant"
            # here is any cell in the data area with a resolved background
            # color (direct fill or conditional formatting) — that's the
            # visible signal vSNP3 uses to mark a call that differs from root.
            # The whole variant cell becomes a clickable IGV-launch target
            # (cell_inner wraps the value in an anchor); other cells render
            # their value bare.
            cell_inner = value
            if (
                vtable
                and cell.row in vtable["samples"]
                and cell.column in vtable["positions"]
                and any("background-color" in p for p in inline_parts)
            ):
                # Resolve the (possibly metadata-decorated) cascade label back
                # to the bare on-disk stem so loadability + the IGV track id
                # match the BAM/VCF filenames (imported step2-renamed samples).
                row_stem = _canonical_stem(
                    vtable["samples"][cell.row], samples_with_bams, samples_with_vcfs
                )
                has_bam = samples_with_bams is None or row_stem in samples_with_bams
                has_vcf = samples_with_vcfs is not None and row_stem in samples_with_vcfs
                # Loadable if either source is present (default-loadable when
                # caller didn't supply sets).
                this_loadable = (
                    samples_with_bams is None and samples_with_vcfs is None
                ) or has_bam or has_vcf
                this_calls_only = (not has_bam) and has_vcf
                cell_inner = _igv_cell_html(
                    value,
                    project=project,
                    this_stem=row_stem,
                    locus=vtable["positions"][cell.column],
                    this_loadable=this_loadable,
                    this_calls_only=this_calls_only,
                )
                classes.append("xlsx-variant")
                # Re-emit class attr — `attrs` already had it baked in above
                # for sticky/rotation classes, so replace rather than append.
                attrs = re.sub(r'\s+class="[^"]*"', "", attrs)
                attrs += f' class="{" ".join(classes)}"'
            style_attr = f' style="{inline}"' if inline else ""
            rows_html.append(f"<td{attrs}{style_attr}>{cell_inner}</td>")
        rows_html.append("</tr>")

    display_title = html.escape(title or xlsx_path.name)
    colgroup = "<colgroup>" + "".join(colgroup_parts) + "</colgroup>"
    table_html = '<table class="xlsx">' + colgroup + "".join(rows_html) + "</table>"

    page = _PAGE_TEMPLATE.format(
        title=display_title,
        filename=html.escape(xlsx_path.name),
        sheet=html.escape(ws.title or "Sheet1"),
        rows=ws.max_row,
        cols=ws.max_column,
        notice="",
        table=table_html,
    )
    return page


_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  :root {{
    --xlsx-border: #d4ccb8;
    --xlsx-header-bg: #fbf9f3;
    --xlsx-text: #1a2536;
    --xlsx-muted: #6a7585;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; height: 100%; background: #f6f3ec; color: var(--xlsx-text); font-family: 'IBM Plex Sans', system-ui, sans-serif; }}
  .xlsx-bar {{
    position: sticky; top: 0; z-index: 10;
    background: var(--xlsx-header-bg);
    border-bottom: 1px solid var(--xlsx-border);
    padding: 10px 20px;
    display: flex; align-items: baseline; gap: 18px; flex-wrap: wrap;
  }}
  .xlsx-truncated {{
    margin: 0; padding: 10px 20px;
    background: #fff5e0; border-bottom: 1px solid #e6c98a;
    color: #4a3a12; font-size: 13px; line-height: 1.5;
  }}
  .xlsx-bar .filename {{ font-weight: 600; font-size: 14px; }}
  .xlsx-bar .meta {{ color: var(--xlsx-muted); font-size: 12px; font-family: 'IBM Plex Mono', monospace; }}
  .xlsx-bar a {{ color: #b85a3e; text-decoration: none; font-size: 13px; }}
  .xlsx-bar a:hover {{ text-decoration: underline; }}
  .xlsx-wrap {{ padding: 16px 20px; overflow: auto; }}
  table.xlsx {{
    border-collapse: collapse;
    background: white;
    font-size: 13px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    table-layout: fixed;  /* honor <colgroup> widths */
  }}
  table.xlsx td {{
    padding: 2px 4px;
    border: 1px solid #eee;  /* light default; cell inline styles override */
    white-space: nowrap;
    vertical-align: middle;
    text-align: center;
    overflow: visible;  /* let rotated annotation rows extend past the row when needed */
  }}
  /* Sample-name column (anchored at first column) should left-align its long
     identifiers rather than centering. */
  table.xlsx td:first-child {{ text-align: left; padding-left: 8px; }}
  .xlsx-sticky-top {{ position: sticky; top: 0; z-index: 2; background: var(--xlsx-header-bg); }}
  .xlsx-sticky-left {{ position: sticky; left: 0; z-index: 1; background: var(--xlsx-header-bg); }}
  .xlsx-sticky-top.xlsx-sticky-left {{ z-index: 3; }}
  /* Excel text-rotation: 1..90 = bottom-to-top read; 91..180 = top-to-bottom. */
  .xlsx-rot-up, .xlsx-rot-down, .xlsx-rot-stacked {{
    white-space: nowrap;
    vertical-align: bottom;
    text-align: left;
    padding: 4px 1px;  /* tight horizontal — narrow column is already the rotated text height */
  }}
  .xlsx-rot-up {{ writing-mode: vertical-rl; transform: rotate(180deg); }}
  .xlsx-rot-down {{ writing-mode: vertical-rl; }}
  .xlsx-rot-stacked {{ writing-mode: vertical-rl; text-orientation: upright; }}
  /* IGV launch on variant cells: the whole colored cell is clickable.
     The anchor fills the cell and inherits color/alignment so the
     nucleotide letter reads normally; a hover outline signals the cell
     is a launch target. */
  td.xlsx-variant {{ position: relative; padding: 0; }}
  td.xlsx-variant a.xlsx-igv-cell {{
    display: block;
    width: 100%;
    height: 100%;
    padding: 2px 4px;
    box-sizing: border-box;
    color: inherit;
    text-align: inherit;
    text-decoration: none;
    cursor: pointer;
    min-height: 1em;
  }}
  td.xlsx-variant a.xlsx-igv-cell:hover {{
    outline: 2px solid rgba(15, 22, 33, 0.6);
    outline-offset: -2px;
  }}
  /* Calls-only — sample is loadable but has only the VCF, no BAM; italic
     hints that this click won't produce a reads pile-up. */
  td.xlsx-variant a.xlsx-igv-cell.xlsx-igv-calls-only {{ font-style: italic; }}
  /* Disabled — sample has neither BAM nor imported VCF; render the value
     plain (not clickable). Tooltip explains why. */
  td.xlsx-variant span.xlsx-igv-disabled {{
    display: block;
    padding: 2px 4px;
    cursor: default;
  }}
</style>
</head>
<body>
<div class="xlsx-bar">
  <span class="filename">{filename}</span>
  <span class="meta">sheet: {sheet} · {rows} rows × {cols} cols</span>
  <span style="margin-left: auto;"><a href="#" onclick="var u=new URL(window.location.href);u.searchParams.set('download','1');window.location.href=u.toString();return false;">Download xlsx</a></span>
</div>
{notice}
<div class="xlsx-wrap">
{table}
</div>
<script>
// IGV launcher for cascade-table variant cells. First click opens a new
// IGV tab; subsequent clicks reuse that tab additively (postMessage tells
// IgvStandalone to add any new samples + navigate to the new locus).
// Window reference is kept on `window.__vsnpIgvWin` so it survives
// across many clicks in the same preview page. If the user closes the
// IGV tab, .closed flips true and we open a fresh one.
(function() {{
  window.__vsnpLaunchIgv = function(url) {{
    var w = window.__vsnpIgvWin;
    if (w && !w.closed) {{
      try {{
        w.postMessage({{ type: "vsnpIgvLaunch", url: url }}, window.location.origin);
        w.focus();
        return;
      }} catch (e) {{ /* lost the handle (cross-origin or torn-down) — fall through to a fresh open */ }}
    }}
    window.__vsnpIgvWin = window.open(url, "vsnp_igv");
  }};
}})();
</script>
</body>
</html>
"""
