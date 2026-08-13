// Zooming the tree viewer.
//
// Two problems are solved here, and they are the same problem seen twice.
//
// 1. A two-finger trackpad zoom on this canvas is unreliable, so there has to
//    be something to press. The buttons drive phylotree's OWN d3 zoom behaviour
//    rather than a transform of our own, so wheel, pinch, drag and buttons all
//    move one transform and can never disagree about where the tree is.
//
// 2. phylotree zooms by putting `scale(k)` on the tree's <g>, and an SVG scale
//    multiplies stroke widths and circle radii along with the geometry. On a
//    1,044-tip tree fitted into 600px the rows sit 0.57px apart while a branch
//    is a 2px stroke and a pick-mode node marker is r=4px, so the branches
//    already merge into a solid mass at fit-to-size — and scaling them with the
//    zoom means they STAY one at every zoom level. The picture gets bigger and
//    never clearer, which is what "zooming in doesn't help me see the tree"
//    comes down to.
//
//    Tip labels are the opposite case and must keep scaling: phylotree sizes
//    them to the row spacing (min(font, spacing) — 0.57px on that tree), so
//    magnifying them is the entire point of zooming in.
//
//    So strokes and markers are counter-scaled — divided by k, which fixes them
//    at a chosen number of SCREEN pixels while the layout and the labels grow,
//    and the tree resolves into separate branches as you go in.
//
// These live in their own module, not inside the component, because the DOM
// behaviour is the part that breaks and it has to be testable — the same reason
// clade.js exists. Copies drift; imports do not.

// One press of + or − multiplies the zoom by this. Twelve presses cover the
// whole range, which is few enough to be worth pressing.
export const ZOOM_STEP = 1.4;

// phylotree's own cap is 10x. On a 1,000-tip tree fitted into a 600px canvas
// the rows are 0.57px apart and phylotree sizes tip labels to that spacing, so
// at 10x a label is still under 6px tall: the zoom runs out before the tree can
// be read. 60x puts those rows 34px apart.
export const ZOOM_MIN = 0.2;
export const ZOOM_MAX = 60;

/**
 * The stroke widths and radii to use at zoom level `k`, in SVG user units.
 *
 * Everything is divided by k so it lands at a fixed number of screen pixels.
 * Branches are then allowed to grow slowly on screen (k^0.3, capped at 2x) so
 * zooming in still makes them bolder and easier to follow — the ask was that
 * the zoom change the branch line widths, not merely stop inflating them. The
 * click target does NOT grow: it stays 12 screen px, so aiming at a branch
 * feels the same at every zoom level.
 */
export function zoomStyleVars(k) {
  const kk = k > 0 ? k : 1;
  const boost = Math.min(2, Math.max(1, Math.pow(kk, 0.3)));
  const px = (screenPx) => `${(screenPx / kk).toFixed(4)}px`;
  return {
    "--tree-branch-w": px(2 * boost),
    "--tree-branch-pick-w": px(3 * boost),
    "--tree-branch-hit-w": px(12),
    "--tree-node-r": px(4),
    "--tree-node-r-hover": px(6),
  };
}

/** Push those values onto the tree container, where the CSS reads them. */
export function applyZoomStyles(el, k) {
  if (!el || !el.style || !el.style.setProperty) return;
  const vars = zoomStyleVars(k);
  for (const name of Object.keys(vars)) el.style.setProperty(name, vars[name]);
}

// phylotree builds a fresh TreeRender on every render(), and the zoom behaviour
// and SVG selection hang off that object — so every helper below takes the
// CURRENT display and tolerates it not being ready yet.
function usable(display) {
  return !!(display && display.zoomBehavior && display.svg);
}

/**
 * Widen phylotree's zoom range and subscribe to it.
 *
 * The listener is NAMESPACED. d3-dispatch keys listeners by name, so
 * "zoom.branchweight" is added alongside phylotree's own unnamed "zoom"
 * handler; registering an unnamed one would REPLACE it and the tree would stop
 * moving altogether.
 */
export function attachZoom(display, onZoom) {
  if (!usable(display)) return false;
  display.zoomBehavior.scaleExtent([ZOOM_MIN, ZOOM_MAX]);
  display.zoomBehavior.on("zoom.branchweight", (ev) => {
    const t = ev && ev.transform;
    if (t) onZoom(t);
  });
  return true;
}

/** Zoom about `point` (SVG user units, i.e. CSS px from the canvas corner). */
export function zoomBy(display, factor, point) {
  if (!usable(display)) return false;
  display.svg.call(display.zoomBehavior.scaleBy, factor, point);
  return true;
}

/**
 * Back to the whole tree.
 *
 * This is d3.zoomIdentity without importing d3 — it is phylotree's dependency,
 * not one this app declares: scale back to 1, then put the world origin at the
 * top-left of the viewport again, which is precisely what identity is.
 */
export function zoomReset(display) {
  if (!usable(display)) return false;
  display.svg
    .call(display.zoomBehavior.scaleTo, 1)
    .call(display.zoomBehavior.translateTo, 0, 0, [0, 0]);
  return true;
}

/**
 * Put a saved transform back after a rebuild.
 *
 * Toggling Bootstrap or Strip-suffix calls tree.render() again, which
 * constructs a new TreeRender — and the zoom transform lives on that object, so
 * without this a checkbox throws away the zoom the user worked to set.
 * translateTo maps world (0,0) to the viewport point [x, y], which reproduces
 * the transform {k, x, y} exactly.
 */
export function restoreZoom(display, t) {
  if (!usable(display) || !t || !(t.k > 0)) return false;
  display.svg
    .call(display.zoomBehavior.scaleTo, t.k)
    .call(display.zoomBehavior.translateTo, 0, 0, [t.x, t.y]);
  return true;
}
