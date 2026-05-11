"""xlsx → self-contained HTML page preserving vSNP3 cascade-table formatting.

Pure function: takes a `Path` to an xlsx file, returns an HTML string
ready to serve as `text/html`. Uses openpyxl (already in the vsnp3 env
as pandas' xlsx engine) — no new dependencies.

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

import openpyxl
from openpyxl.utils import get_column_letter, column_index_from_string
from openpyxl.worksheet.cell_range import CellRange


_DEFAULT_FONT_SIZE = 11
_DEFAULT_FONT_COLOR = "FF000000"


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


def xlsx_to_html(xlsx_path: Path, title: str | None = None) -> str:
    """Render the first (active) sheet of an xlsx file as a self-contained HTML page."""
    xlsx_path = Path(xlsx_path)
    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=False)
    ws = wb.active
    dxfs = list(wb._differential_styles.styles) if hasattr(wb, "_differential_styles") else []
    cf_extras = _build_cf_extras(ws, dxfs)

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

    # Column widths — openpyxl's `width` is in Excel character units;
    # roughly 7px per unit gives a usable approximation.
    colgroup_parts: list[str] = []
    for col_idx in range(1, ws.max_column + 1):
        letter = get_column_letter(col_idx)
        dim = ws.column_dimensions.get(letter)
        width_px = int(round(dim.width * 7)) if (dim and dim.width) else 80
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
            style_attr = f' style="{inline}"' if inline else ""
            rows_html.append(f"<td{attrs}{style_attr}>{value}</td>")
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
  }}
  table.xlsx td {{
    padding: 3px 8px;
    border: 1px solid #eee;  /* light default; cell inline styles override */
    white-space: nowrap;
    vertical-align: middle;
  }}
  .xlsx-sticky-top {{ position: sticky; top: 0; z-index: 2; background: var(--xlsx-header-bg); }}
  .xlsx-sticky-left {{ position: sticky; left: 0; z-index: 1; background: var(--xlsx-header-bg); }}
  .xlsx-sticky-top.xlsx-sticky-left {{ z-index: 3; }}
  /* Excel text-rotation: 1..90 = bottom-to-top read; 91..180 = top-to-bottom. */
  .xlsx-rot-up, .xlsx-rot-down, .xlsx-rot-stacked {{
    white-space: nowrap;
    vertical-align: bottom;
    text-align: left;
    padding: 6px 4px;
    min-width: 18px;
  }}
  .xlsx-rot-up {{ writing-mode: vertical-rl; transform: rotate(180deg); }}
  .xlsx-rot-down {{ writing-mode: vertical-rl; }}
  .xlsx-rot-stacked {{ writing-mode: vertical-rl; text-orientation: upright; }}
</style>
</head>
<body>
<div class="xlsx-bar">
  <span class="filename">{filename}</span>
  <span class="meta">sheet: {sheet} · {rows} rows × {cols} cols</span>
  <span style="margin-left: auto;"><a href="?download=1">Download xlsx</a></span>
</div>
<div class="xlsx-wrap">
{table}
</div>
</body>
</html>
"""
