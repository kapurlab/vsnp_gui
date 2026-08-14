// The viewport arithmetic, and the hit test that reads a click back out of it.
//
// This is the file that pins the actual bug reports:
//
//   "I searched for a sample in the tree but I can't see it"  -> reveal() must
//   leave the row on screen AND at a row height that can carry a label.
//
//   "when you zoom in the font doesn't adjust"  -> the font is a function of
//   row height in screen pixels, so pin that function, not a transform.
//
//   node test/tree_view.test.mjs

import assert from "node:assert/strict";
import { phylotree } from "phylotree";
import { buildLayout } from "../src/tree/layout.js";
import {
  fitView, fitRowH, clampView, zoomRows, zoomCols, panBy, revealRows,
  centreRow, rowsOnScreen, pyToRow, rowToPy, xToPx, pxToX, visibleRows,
  LABEL_MIN_ROW_H, REVEAL_ROW_H, MAX_ROW_H,
} from "../src/tree/view.js";
import { labelFontSize, nodeRadius, niceStep, formatTick } from "../src/tree/draw.js";
import { pickAt, tipRowAt, PICK_RADIUS } from "../src/tree/hit.js";

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed++;
  } catch (err) {
    console.error(`FAIL ${name}\n  ${err.message}`);
    process.exitCode = 1;
  }
}

const GEOM = { width: 1200, height: 626, padL: 10, padT: 26, plotW: 990, plotH: 600 };

/** A tree the size of the ones this viewer actually gets asked to draw. */
function bigTree(nLeaves = 4611) {
  const parts = [];
  for (let i = 0; i < nLeaves; i++) parts.push(`s${i}:0.0${(i % 9) + 1}`);
  // A balanced-ish comb, built by pairing, so it has real internal structure.
  let level = parts;
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      next.push(i + 1 < level.length ? `(${level[i]},${level[i + 1]}):0.01` : level[i]);
    }
    level = next;
  }
  const t = new phylotree(`${level[0]};`);
  return buildLayout(t.nodes);
}

const BIG = bigTree();
const XBIG = BIG.xLen;

test("fit shows every tip", () => {
  const v = fitView(BIG, GEOM, BIG.maxX);
  assert.equal(v.yTop, 0);
  assert.ok(Math.abs(v.rowH * BIG.nLeaves - GEOM.plotH) < 1e-6);
  const [lo, hi] = visibleRows(v, GEOM, BIG);
  assert.equal(lo, 0);
  assert.equal(hi, BIG.nLeaves - 1);
});

test("at fit, 4,611 tips share 600px and no label can be drawn", () => {
  // The premise of the whole rewrite, asserted rather than assumed.
  const v = fitView(BIG, GEOM, BIG.maxX);
  assert.ok(v.rowH < 0.2, `row height at fit is ${v.rowH}`);
  assert.ok(v.rowH < LABEL_MIN_ROW_H);
});

test("screen mapping round-trips both ways", () => {
  const v = fitView(BIG, GEOM, BIG.maxX);
  for (const r of [0, 1, 1000, 4610]) {
    assert.ok(Math.abs(pyToRow(v, GEOM, rowToPy(v, GEOM, r)) - r) < 1e-9);
  }
  for (const x of [0, BIG.maxX / 3, BIG.maxX]) {
    assert.ok(Math.abs(pxToX(v, GEOM, xToPx(v, GEOM, x)) - x) < 1e-9);
  }
});

test("vertical zoom holds the anchor row under the cursor", () => {
  let v = fitView(BIG, GEOM, BIG.maxX);
  v = clampView(zoomRows(v, GEOM, 30, 300), BIG, GEOM, BIG.maxX);
  const rowBefore = pyToRow(fitView(BIG, GEOM, BIG.maxX), GEOM, 300);
  assert.ok(Math.abs(pyToRow(v, GEOM, 300) - rowBefore) < 0.5,
            `anchor drifted from ${rowBefore} to ${pyToRow(v, GEOM, 300)}`);
});

test("horizontal zoom holds the anchor x under the cursor", () => {
  const v0 = fitView(BIG, GEOM, BIG.maxX);
  const xBefore = pxToX(v0, GEOM, 700);
  const v = clampView(zoomCols(v0, GEOM, 8, 700), BIG, GEOM, BIG.maxX);
  assert.ok(Math.abs(pxToX(v, GEOM, 700) - xBefore) < 1e-9);
});

test("the two axes are independent — vertical zoom does not touch x", () => {
  // This is the property the old SVG scale(k) could not have: to read tips you
  // had to magnify the branch axis by the same factor and lose your place.
  const v0 = fitView(BIG, GEOM, BIG.maxX);
  const v = zoomRows(v0, GEOM, 100, 300);
  assert.equal(v.x0, v0.x0);
  assert.equal(v.x1, v0.x1);
});

test("zoom-out stops at fit instead of shrinking into a corner", () => {
  const v = clampView(zoomRows(fitView(BIG, GEOM, BIG.maxX), GEOM, 0.01, 300),
                      BIG, GEOM, BIG.maxX);
  assert.ok(Math.abs(v.rowH - fitRowH(BIG, GEOM)) < 1e-9);
  assert.equal(v.yTop, 0);
});

test("zoom-in stops at MAX_ROW_H", () => {
  let v = fitView(BIG, GEOM, BIG.maxX);
  for (let i = 0; i < 60; i++) v = clampView(zoomRows(v, GEOM, 2, 300), BIG, GEOM, BIG.maxX);
  assert.ok(Math.abs(v.rowH - MAX_ROW_H) < 1e-9, `rowH ${v.rowH}`);
});

test("scrolling cannot run off either end of the tree", () => {
  let v = clampView(zoomRows(fitView(BIG, GEOM, BIG.maxX), GEOM, 200, 300),
                    BIG, GEOM, BIG.maxX);
  const up = clampView(panBy(v, GEOM, 0, 100000), BIG, GEOM, BIG.maxX);
  assert.equal(up.yTop, 0);
  const down = clampView(panBy(v, GEOM, 0, -100000), BIG, GEOM, BIG.maxX);
  const rowsShown = GEOM.plotH / down.rowH;
  assert.ok(Math.abs(down.yTop - (BIG.nLeaves - rowsShown)) < 1e-6);
  assert.ok(down.yTop + rowsShown <= BIG.nLeaves + 1e-6);
});

test("horizontal pan and zoom stay inside the tree's own width", () => {
  const v0 = fitView(BIG, GEOM, BIG.maxX);
  const l = clampView(panBy(v0, GEOM, 99999, 0), BIG, GEOM, BIG.maxX);
  assert.ok(l.x0 >= -1e-9, `x0 ${l.x0}`);
  const r = clampView(panBy(v0, GEOM, -99999, 0), BIG, GEOM, BIG.maxX);
  assert.ok(r.x1 <= BIG.maxX + 1e-9, `x1 ${r.x1}`);
  const out = clampView(zoomCols(v0, GEOM, 0.001, 500), BIG, GEOM, BIG.maxX);
  assert.ok(out.x1 - out.x0 <= BIG.maxX + 1e-9);
});

// ---- the search bug, stated as tests ------------------------------------

test("reveal puts the searched row on screen", () => {
  const v0 = fitView(BIG, GEOM, BIG.maxX);
  const row = 3987;
  assert.ok(!rowsOnScreen(clampView(v0, BIG, GEOM, BIG.maxX), GEOM, [row])
            || v0.rowH < LABEL_MIN_ROW_H);
  const v = clampView(revealRows(v0, BIG, GEOM, [row], { rowH: REVEAL_ROW_H, context: 8 }),
                      BIG, GEOM, BIG.maxX);
  assert.ok(rowsOnScreen(v, GEOM, [row]), "the revealed row is not on screen");
  const py = rowToPy(v, GEOM, row);
  assert.ok(py >= GEOM.padT && py <= GEOM.padT + GEOM.plotH, `row drawn at y=${py}`);
});

test("reveal leaves the row height readable, not merely on screen", () => {
  // Scrolling to the hit without spreading the rows apart would put a 0.13px
  // label on screen, which is the original complaint with extra steps.
  const v = clampView(
    revealRows(fitView(BIG, GEOM, BIG.maxX), BIG, GEOM, [2000], { rowH: REVEAL_ROW_H, context: 8 }),
    BIG, GEOM, BIG.maxX);
  assert.ok(v.rowH >= LABEL_MIN_ROW_H, `rowH after reveal ${v.rowH}`);
  assert.ok(labelFontSize(v.rowH) >= 7);
});

test("reveal centres the hit", () => {
  const v = clampView(revealRows(fitView(BIG, GEOM, BIG.maxX), BIG, GEOM, [2000],
                                 { rowH: REVEAL_ROW_H, context: 8 }), BIG, GEOM, BIG.maxX);
  const py = rowToPy(v, GEOM, 2000);
  const middle = GEOM.padT + GEOM.plotH / 2;
  assert.ok(Math.abs(py - middle) < 2, `hit at ${py}, middle ${middle}`);
});

test("reveal works at both ends of the tree", () => {
  for (const row of [0, BIG.nLeaves - 1]) {
    const v = clampView(revealRows(fitView(BIG, GEOM, BIG.maxX), BIG, GEOM, [row],
                                   { rowH: REVEAL_ROW_H, context: 8 }), BIG, GEOM, BIG.maxX);
    assert.ok(rowsOnScreen(v, GEOM, [row]), `row ${row} not on screen`);
    const py = rowToPy(v, GEOM, row);
    assert.ok(py >= GEOM.padT - 1 && py <= GEOM.padT + GEOM.plotH + 1,
              `row ${row} drawn at y=${py}`);
  }
});

test("reveal of a whole clade fits it when it can, without going sparse", () => {
  const v = clampView(revealRows(fitView(BIG, GEOM, BIG.maxX), BIG, GEOM, [100, 129],
                                 { rowH: REVEAL_ROW_H, context: 4 }), BIG, GEOM, BIG.maxX);
  assert.ok(rowsOnScreen(v, GEOM, [100]), "clade start off screen");
  assert.ok(rowsOnScreen(v, GEOM, [129]), "clade end off screen");
  assert.ok(v.rowH <= REVEAL_ROW_H + 1e-9);
});

test("centreRow scrolls without changing the zoom", () => {
  const v0 = clampView(zoomRows(fitView(BIG, GEOM, BIG.maxX), GEOM, 100, 300), BIG, GEOM, BIG.maxX);
  const v = clampView(centreRow(v0, GEOM, 2500), BIG, GEOM, BIG.maxX);
  assert.equal(v.rowH, v0.rowH);
  assert.ok(rowsOnScreen(v, GEOM, [2500]));
});

// ---- appearance is a function of screen size, never of a zoom factor ----

test("label size tracks row height and stays inside readable bounds", () => {
  assert.ok(labelFontSize(0.13) >= 7);      // never microscopic
  assert.ok(labelFontSize(1000) <= 14);     // never absurd
  assert.ok(labelFontSize(16) > labelFontSize(8), "labels must grow with zoom");
  assert.ok(labelFontSize(REVEAL_ROW_H) >= 9);
});

test("node markers vanish rather than merge when rows are tight", () => {
  assert.equal(nodeRadius(0.13), 0);
  assert.equal(nodeRadius(4), 0);
  assert.ok(nodeRadius(20) > 0);
  assert.ok(nodeRadius(1e6) <= 3.5, "a marker can never grow with the zoom");
});

test("axis ticks are formatted to the precision the step resolves", () => {
  // The old axis printed eight ticks reading 0.00 0.00 0.01 0.01 on a tree
  // 0.02 wide, which is not an axis.
  const step = niceStep(0.02 / 6);
  const labels = [0, step, step * 2, step * 3].map((v) => formatTick(v, step));
  assert.equal(new Set(labels).size, labels.length, `duplicate tick labels: ${labels}`);
});

// ---- hit testing --------------------------------------------------------

const SMALL = buildLayout(
  new phylotree("((A:0.1,B:0.2)ab:0.05,(C:0.3,(D:0.15,E:0.05)de:0.1)cde:0.07);").nodes
);
const SGEOM = { width: 800, height: 426, padL: 10, padT: 26, plotW: 600, plotH: 400 };
const SVIEW = fitView(SMALL, SGEOM, SMALL.maxX);

function pickTip(name) {
  const i = SMALL.names.indexOf(name);
  return pickAt(SMALL, SVIEW, SGEOM, SMALL.xLen,
                xToPx(SVIEW, SGEOM, SMALL.xLen[i]) - 4,
                rowToPy(SVIEW, SGEOM, SMALL.row[i]));
}

test("clicking a terminal branch picks that tip", () => {
  for (const name of ["A", "B", "C", "D", "E"]) {
    const hit = pickTip(name);
    assert.ok(hit, `no hit for ${name}`);
    assert.equal(SMALL.names[hit.index], name);
  }
});

test("clicking an internal node's marker picks that clade", () => {
  const i = SMALL.names.indexOf("de");
  const hit = pickAt(SMALL, SVIEW, SGEOM, SMALL.xLen,
                     xToPx(SVIEW, SGEOM, SMALL.xLen[i]),
                     rowToPy(SVIEW, SGEOM, SMALL.row[i]));
  assert.ok(hit);
  assert.equal(SMALL.names[hit.index], "de");
  assert.equal(hit.kind, "node");
});

test("clicking the vertical connector picks the fork, not a child", () => {
  const i = SMALL.names.indexOf("cde");
  // Midway down the connector, between child rows.
  const cs = SMALL.childStart[i];
  const y0 = rowToPy(SVIEW, SGEOM, SMALL.row[SMALL.childIdx[cs]]);
  const y1 = rowToPy(SVIEW, SGEOM, SMALL.row[SMALL.childIdx[SMALL.childStart[i + 1] - 1]]);
  const hit = pickAt(SMALL, SVIEW, SGEOM, SMALL.xLen,
                     xToPx(SVIEW, SGEOM, SMALL.xLen[i]), (y0 + y1) / 2);
  assert.ok(hit);
  assert.equal(SMALL.names[hit.index], "cde");
});

test("clicking empty space picks nothing", () => {
  // Far right of every tip, and far below the last row.
  assert.equal(pickAt(SMALL, SVIEW, SGEOM, SMALL.xLen, 599, 410), null);
});

test("the pick target is a constant screen distance at every zoom", () => {
  // The old viewer's target WAS the stroke, so it was 4px zoomed out and 200px
  // zoomed in. Here the same offset picks at both extremes and neither more.
  const i = SMALL.names.indexOf("C");
  for (const factor of [1, 50]) {
    const v = clampView(zoomRows(SVIEW, SGEOM, factor, 200), SMALL, SGEOM, SMALL.maxX);
    const px = xToPx(v, SGEOM, SMALL.xLen[i]) - 5;
    const py = rowToPy(v, SGEOM, SMALL.row[i]);
    assert.ok(pickAt(SMALL, v, SGEOM, SMALL.xLen, px, py), `no hit at zoom ${factor}`);
    const far = pickAt(SMALL, v, SGEOM, SMALL.xLen, px, py + PICK_RADIUS * 3);
    if (far) assert.notEqual(SMALL.names[far.index], "C", `over-wide target at zoom ${factor}`);
  }
});

test("hit testing a 4,611-tip tree at fit stays fast", () => {
  const v = fitView(BIG, GEOM, BIG.maxX);
  const t0 = Date.now();
  for (let k = 0; k < 200; k++) {
    pickAt(BIG, v, GEOM, XBIG, 400 + (k % 50), GEOM.padT + (k * 3) % GEOM.plotH);
  }
  const ms = Date.now() - t0;
  assert.ok(ms < 1500, `200 picks took ${ms}ms`);
});

test("tipRowAt resolves a row only near its own centre line", () => {
  const v = clampView(zoomRows(SVIEW, SGEOM, 4, 200), SMALL, SGEOM, SMALL.maxX);
  const py = rowToPy(v, SGEOM, 2);
  assert.equal(tipRowAt(SMALL, v, SGEOM, py), 2);
  assert.equal(tipRowAt(SMALL, v, SGEOM, py + v.rowH * 0.9), 3);
  assert.equal(tipRowAt(SMALL, v, SGEOM, -500), -1);
});

if (!process.exitCode) console.log(`ok — ${passed} tree view assertions`);
