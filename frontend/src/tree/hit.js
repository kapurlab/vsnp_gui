// What did the user click on?
//
// The old viewer let the browser answer this: every branch was an SVG <path>,
// so a click landed on one and a delegated listener read the datum back off the
// element. That is why the branches had to be thick enough to hit — a 2px
// stroke is a 2px target — and why the pick target grew to 200px when zoomed
// in, since the stroke was scaled along with everything else.
//
// On a canvas there are no elements, so hit testing is arithmetic, and the
// target size stops being tied to the line width. A branch can be drawn one
// pixel wide and still be picked from 14 pixels away, at every zoom level.

import { rowToPy, xToPx, pyToRow } from "./view.js";

/** How far from a branch a click still counts, in screen pixels. */
export const PICK_RADIUS = 14;

function distToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  let t = len2 > 0 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
  t = t < 0 ? 0 : t > 1 ? 1 : t;
  const qx = ax + t * dx;
  const qy = ay + t * dy;
  return Math.hypot(px - qx, py - qy);
}

/**
 * The node nearest to a point on screen, or null.
 *
 * Candidates are the ancestors of every tip whose row is within the pick radius
 * — which is the complete set of nodes that could own a line segment near the
 * click, and no more. Adjacent tips share nearly all of their ancestry, so the
 * `seen` short-circuit collapses that to a few dozen nodes even when the pick
 * radius covers 90 rows at fit-to-size.
 *
 * `kind` distinguishes the node marker from the branch leading into it. Both
 * select the same clade — the marker exists because it is a much bigger target
 * and it is where the eye already is when someone means "this group".
 */
export function pickAt(layout, view, geom, x, px, py, opts = {}) {
  if (!layout.n || !layout.nLeaves) return null;
  const radius = opts.radius == null ? PICK_RADIUS : opts.radius;
  const nodeR = opts.nodeR == null ? 3 : opts.nodeR;

  const rowAt = pyToRow(view, geom, py);
  const slack = Math.max(1, Math.ceil(radius / (view.rowH || 1)));
  const rLo = Math.max(0, Math.floor(rowAt - slack));
  const rHi = Math.min(layout.nLeaves - 1, Math.ceil(rowAt + slack));

  const seen = new Uint8Array(layout.n);
  const candidates = [];
  for (let r = rLo; r <= rHi; r++) {
    let i = layout.leafRows[r];
    while (i >= 0 && !seen[i]) {
      seen[i] = 1;
      candidates.push(i);
      i = layout.parent[i];
    }
  }

  let best = null;
  for (const i of candidates) {
    const nx = xToPx(view, geom, x[i]);
    const ny = rowToPy(view, geom, layout.row[i]);

    // The node marker first: a click within the marker means the node, even if
    // some branch happens to pass marginally closer.
    const dNode = Math.hypot(px - nx, py - ny);
    if (dNode <= nodeR + 5 && (!best || dNode < best.dist)) {
      best = { index: i, kind: "node", dist: dNode };
    }

    // The branch leading into this node, from its parent's x at this node's row.
    const p = layout.parent[i];
    if (p >= 0) {
      const ppx = xToPx(view, geom, x[p]);
      const d = distToSegment(px, py, ppx, ny, nx, ny);
      if (d <= radius && (!best || d < best.dist)) {
        best = { index: i, kind: "edge", dist: d };
      }
    }

    // The vertical connector joining this node's children. Clicking it is the
    // most natural way to say "everything under this fork".
    const cs = layout.childStart[i];
    const ce = layout.childStart[i + 1];
    if (ce > cs) {
      const topRow = layout.row[layout.childIdx[cs]];
      const botRow = layout.row[layout.childIdx[ce - 1]];
      const d = distToSegment(
        px, py,
        nx, rowToPy(view, geom, topRow),
        nx, rowToPy(view, geom, botRow)
      );
      if (d <= radius && (!best || d < best.dist)) {
        best = { index: i, kind: "node", dist: d };
      }
    }
  }
  return best;
}

/**
 * The tip row under a point, when one is close enough — used for hover
 * readouts and for clicking a label rather than a branch.
 */
export function tipRowAt(layout, view, geom, py) {
  const r = Math.round(pyToRow(view, geom, py));
  if (r < 0 || r >= layout.nLeaves) return -1;
  // Only within half a row of the tip's own centre line, so the gaps between
  // widely-spaced tips do not silently resolve to a neighbour.
  const dy = Math.abs(py - rowToPy(view, geom, r));
  return dy <= Math.max(2, view.rowH / 2) ? r : -1;
}
