// The viewport: the only place tree coordinates become screen pixels.
//
// A view is four numbers — a horizontal window `[x0, x1]` in tree units, a row
// height in screen pixels, and the first visible row. Two axes, scaled
// independently, which is the substantive difference from the old viewer.
//
// phylotree zoomed with a single SVG `scale(k)`, so the two axes were welded
// together: to get 4,611 tips far enough apart to read you had to magnify the
// tree 100× horizontally as well, ending up somewhere inside a canvas a hundred
// thousand pixels wide with no idea where. But these trees are crowded in one
// direction only — thousands of rows deep and a fraction of a substitution
// wide. Vertical zoom alone is what "spread this out so I can read it" means,
// and it is the default here; horizontal zoom exists but is rarely needed.
//
// Everything is a pure function of (view, geom): no DOM, no canvas, no React,
// so the arithmetic that decides whether a searched-for tip actually ends up on
// screen is testable without a browser. That is deliberate — the failure being
// fixed is precisely "the highlight is applied, and you cannot see it".

/** Rows must be at least this tall before tip labels are worth drawing. */
export const LABEL_MIN_ROW_H = 5.5;

/** Row height a reveal aims for: comfortably readable without being sparse. */
export const REVEAL_ROW_H = 16;

/** Never magnify beyond this — past it you see a handful of tips and no tree. */
export const MAX_ROW_H = 48;

/** Horizontal zoom range, as a multiple of the whole tree's width. */
export const MAX_X_ZOOM = 500;

/** Fit: the whole tree, both axes. */
export function fitView(layout, geom, xMax) {
  const rowH = layout.nLeaves > 0 ? geom.plotH / layout.nLeaves : geom.plotH;
  return { x0: 0, x1: xMax > 0 ? xMax : 1, rowH, yTop: 0 };
}

/** The row height at which the whole tree fits — also the zoom-out limit. */
export function fitRowH(layout, geom) {
  return layout.nLeaves > 0 ? geom.plotH / layout.nLeaves : geom.plotH;
}

export function xToPx(view, geom, x) {
  const span = view.x1 - view.x0 || 1;
  return geom.padL + ((x - view.x0) / span) * geom.plotW;
}

export function pxToX(view, geom, px) {
  const span = view.x1 - view.x0 || 1;
  return view.x0 + ((px - geom.padL) / (geom.plotW || 1)) * span;
}

// Rows are drawn on their centre line: row 0 sits half a row below the top, so
// at fit-to-size the first and last tips are inside the canvas rather than
// half-clipped by it.
export function rowToPy(view, geom, r) {
  return geom.padT + (r - view.yTop + 0.5) * view.rowH;
}

export function pyToRow(view, geom, py) {
  return view.yTop + (py - geom.padT) / (view.rowH || 1) - 0.5;
}

/** The row range on screen, widened by one row so partial rows still draw. */
export function visibleRows(view, geom, layout) {
  const lo = Math.max(0, Math.floor(pyToRow(view, geom, geom.padT)) - 1);
  const hi = Math.min(
    layout.nLeaves - 1,
    Math.ceil(pyToRow(view, geom, geom.padT + geom.plotH)) + 1
  );
  return [lo, hi];
}

/**
 * Pull a view back inside its limits.
 *
 * Zoom-out stops at fit rather than continuing into empty space, because on a
 * tree this size an accidental extra wheel notch otherwise leaves the user
 * staring at a grey field with the tree a speck in one corner — which reads as
 * "the tree disappeared", not as "zoomed out too far".
 */
export function clampView(view, layout, geom, xMax) {
  const fitH = fitRowH(layout, geom);
  const minRowH = Math.min(fitH, MAX_ROW_H);
  const maxRowH = Math.max(fitH, MAX_ROW_H);
  const rowH = Math.min(maxRowH, Math.max(minRowH, view.rowH));

  const rowsOnScreen = geom.plotH / rowH;
  const maxTop = Math.max(0, layout.nLeaves - rowsOnScreen);
  const yTop = Math.min(maxTop, Math.max(0, view.yTop));

  const full = xMax > 0 ? xMax : 1;
  const minSpan = full / MAX_X_ZOOM;
  let span = Math.min(full, Math.max(minSpan, view.x1 - view.x0));
  let x0 = Math.min(full - span, Math.max(0, view.x0));
  if (!Number.isFinite(x0)) x0 = 0;
  return { x0, x1: x0 + span, rowH, yTop };
}

/** Vertical zoom about a point on screen — that point keeps its row. */
export function zoomRows(view, geom, factor, anchorPy) {
  const py = anchorPy == null ? geom.padT + geom.plotH / 2 : anchorPy;
  const rowAt = pyToRow(view, geom, py);
  const rowH = view.rowH * factor;
  // Invert rowToPy for the new rowH, holding `rowAt` at `py`.
  const yTop = rowAt + 0.5 - (py - geom.padT) / rowH;
  return { ...view, rowH, yTop };
}

/** Horizontal zoom about a point on screen — that point keeps its position. */
export function zoomCols(view, geom, factor, anchorPx) {
  const px = anchorPx == null ? geom.padL + geom.plotW / 2 : anchorPx;
  const xAt = pxToX(view, geom, px);
  const span = (view.x1 - view.x0) / factor;
  const frac = geom.plotW > 0 ? (px - geom.padL) / geom.plotW : 0.5;
  const x0 = xAt - frac * span;
  return { ...view, x0, x1: x0 + span };
}

/** Drag. */
export function panBy(view, geom, dxPx, dyPx) {
  const span = view.x1 - view.x0;
  const dx = geom.plotW > 0 ? (dxPx / geom.plotW) * span : 0;
  return {
    ...view,
    x0: view.x0 - dx,
    x1: view.x1 - dx,
    yTop: view.yTop - dyPx / (view.rowH || 1),
  };
}

/**
 * Put a set of rows on screen, readably.
 *
 * This is the answer to "I searched for a sample and I cannot see it". Marking
 * the hit was never the problem — the mark was 0.13 px tall, because 4,611 rows
 * were sharing 600 pixels. Nothing short of moving the viewport can fix that,
 * so search moves the viewport.
 *
 * The row height chosen is whichever is smaller: enough to show the whole span
 * with a little context, or REVEAL_ROW_H. It never zooms *out* to fit a span
 * scattered across the tree — the caller steps through hits one at a time
 * instead, because a "view" containing two hits 3,000 rows apart is just the
 * whole tree again.
 */
export function revealRows(view, layout, geom, rows, opts = {}) {
  if (!rows || !rows.length) return view;
  const want = opts.rowH || REVEAL_ROW_H;
  let lo = Infinity;
  let hi = -Infinity;
  for (const r of rows) {
    if (r < lo) lo = r;
    if (r > hi) hi = r;
  }
  const context = opts.context == null ? 6 : opts.context;
  const spanRows = hi - lo + 1 + context * 2;
  const rowH = Math.min(want, geom.plotH / spanRows);
  // Centre the span. A hit at the very top or bottom of the tree is pulled back
  // inside by clampView, which is what should happen.
  const centre = (lo + hi) / 2;
  const yTop = centre + 0.5 - geom.plotH / (2 * (rowH || 1));
  return { ...view, rowH, yTop };
}

/** Scroll so `row` is centred, without changing any zoom. */
export function centreRow(view, geom, row) {
  return { ...view, yTop: row + 0.5 - geom.plotH / (2 * (view.rowH || 1)) };
}

/**
 * Are these rows currently on screen?
 *
 * Used to decide whether a selection needs revealing or is already in view —
 * re-centring a clade the user is already looking at is disorienting.
 */
export function rowsOnScreen(view, geom, rows) {
  if (!rows || !rows.length) return false;
  const top = pyToRow(view, geom, geom.padT);
  const bot = pyToRow(view, geom, geom.padT + geom.plotH);
  return rows.some((r) => r >= top && r <= bot);
}
