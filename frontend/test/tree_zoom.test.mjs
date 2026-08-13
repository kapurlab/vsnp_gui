// Do the zoom buttons zoom, and do the branches get thinner as they do?
//
// Two-finger zoom on this canvas is unreliable, so the viewer grew + and −
// buttons. They drive phylotree's OWN d3 zoom behaviour, which is where the
// risk is: phylotree registers an unnamed "zoom" listener that applies the
// transform, and taking that listener off — which is what `.on("zoom", …)`
// does — leaves buttons that fire, update a readout, and move nothing. That is
// invisible to any test that only checks our own callback ran, so what is
// asserted here is the TRANSFORM ON THE TREE'S <g>.
//
// The second half is the branch weight. phylotree zooms with scale(k), which
// multiplies stroke widths and radii too, so a 1,000-tip tree whose branches
// merge into a solid mass at fit-to-size is still a solid mass at 30x. The
// widths are counter-scaled to hold a fixed number of SCREEN pixels; the
// arithmetic for that is pinned below because getting it inverted (widths that
// grow with k) is silent — the tree just never resolves.
//
// Run:  node test/tree_zoom.test.mjs [path/to/tree.tre]
// jsdom is not a project dependency (the app never runs under it); the test
// skips with a clear message when it is missing.

import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  ZOOM_STEP, ZOOM_MIN, ZOOM_MAX,
  applyZoomStyles, attachZoom, restoreZoom, zoomBy, zoomReset, zoomStyleVars,
} from "../src/zoom.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");

const FALLBACK_NEWICK =
  "(((A_zc.vcf:0.1,(B:0.1,C:0.1)0.99:0.02):0.05,(D:0.1,E:0.1):0.03):0.01,root:0.01);";

function fail(msg) {
  console.error(`  FAIL  ${msg}`);
  process.exitCode = 1;
}
function ok(msg) {
  console.log(`  OK  ${msg}`);
}
function assert(cond, msg) {
  cond ? ok(msg) : fail(msg);
  return !!cond;
}
function near(a, b, tol, msg) {
  return assert(Math.abs(a - b) <= tol, `${msg} (${a} ≈ ${b})`);
}

// ---------------------------------------------------------------------------
// The arithmetic, which needs no DOM at all.
console.log("[branch weight follows the zoom]");

const px = (v) => parseFloat(String(v));
const screenWidth = (k) => px(zoomStyleVars(k)["--tree-branch-w"]) * k;

near(px(zoomStyleVars(1)["--tree-branch-w"]), 2, 1e-3,
  "at 1x a branch is still phylotree's own 2px — the unzoomed tree is unchanged");

let monotonic = true, everShrank = false;
let prev = px(zoomStyleVars(1)["--tree-branch-w"]);
for (const k of [1.4, 2, 4, 8, 16, 32, 60]) {
  const w = px(zoomStyleVars(k)["--tree-branch-w"]);
  if (w >= prev) monotonic = false; else everShrank = true;
  prev = w;
}
assert(monotonic && everShrank,
  "the drawn stroke-width SHRINKS at every step in as the zoom grows "
  + "(this is what lets a dense tree separate instead of staying a black mass)");

let bolder = true;
for (const k of [1.4, 2, 4, 8]) {
  if (!(screenWidth(k) > screenWidth(1))) bolder = false;
}
assert(bolder, "…while the width ON SCREEN grows, so zooming in makes branches easier to see");
// The tolerances below scale with k because the widths are written out to four
// decimal places, and at 60x that last place is worth 0.006 screen px.
const quantum = (k) => 1e-4 * k;
assert(screenWidth(ZOOM_MAX) <= 2 * screenWidth(1) + quantum(ZOOM_MAX),
  `…capped at twice the base, so far in it is bold and not a blob (${screenWidth(60).toFixed(2)}px)`);
assert(screenWidth(ZOOM_MIN) >= screenWidth(1) * 0.99,
  "zoomed all the way out the branches do not thin away to nothing");

for (const k of [0.5, 1, 7, 60]) {
  const hit = px(zoomStyleVars(k)["--tree-branch-hit-w"]) * k;
  const r = px(zoomStyleVars(k)["--tree-node-r"]) * k;
  if (!near(hit, 12, quantum(k), `the click target stays 12 screen px at ${k}x`)) break;
  if (!near(r, 4, quantum(k), `the node marker stays 4 screen px at ${k}x`)) break;
}
assert(px(zoomStyleVars(1)["--tree-branch-hit-w"]) >= 10,
  "the hit width is never SMALLER than phylotree's own 10px hover target");
assert(ZOOM_MAX > 10,
  `the zoom goes past phylotree's 10x default (${ZOOM_MAX}x) — 10x cannot read a 1,000-tip tree`);

// applyZoomStyles must survive being handed nothing: it runs from a render path
// that can fire before the container exists.
applyZoomStyles(null, 3);
applyZoomStyles({}, 3);
ok("applyZoomStyles tolerates a missing container");

// ---------------------------------------------------------------------------
let JSDOM;
try {
  ({ JSDOM } = await import("jsdom"));
} catch {
  console.log("\nSKIP: the DOM half needs jsdom.\n" +
    "      cd frontend && npm i --no-save jsdom");
  process.exit(process.exitCode || 0);
}

const dom = new JSDOM(`<!doctype html><html><body><div id="c"></div></body></html>`,
  { pretendToBeVisual: true });
const { window } = dom;
for (const k of ["window", "document", "navigator", "Node", "Element", "SVGElement",
                 "MouseEvent", "Event", "CustomEvent", "HTMLElement",
                 "getComputedStyle", "requestAnimationFrame"]) {
  Object.defineProperty(globalThis, k, {
    value: window[k], writable: true, configurable: true,
  });
}
window.SVGElement.prototype.getBBox = function () {
  return { x: 0, y: 0, width: 120, height: 12 };
};
window.SVGElement.prototype.getComputedTextLength = function () {
  return ((this.textContent || "").length) * 7;
};
window.SVGElement.prototype.getScreenCTM = function () {
  return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0, inverse: () => this, multiply: () => this };
};
// d3-zoom's default extent reads svg.width.baseVal.value, which jsdom does not
// implement. The viewer's SVG is sized by phylotree to the canvas, so stand in
// with the same numbers the render is given.
const WIDTH = 900, HEIGHT = 700;
for (const [prop, value] of [["width", WIDTH], ["height", HEIGHT]]) {
  if (!(prop in window.SVGSVGElement.prototype)) {
    Object.defineProperty(window.SVGSVGElement.prototype, prop, {
      get() { return { baseVal: { value } }; }, configurable: true,
    });
  }
}

const require = createRequire(path.join(FRONTEND, "package.json"));
const { phylotree } = require("phylotree");

const treeArg = process.argv[2];
let newick = FALLBACK_NEWICK, label = "fallback tree";
if (treeArg && existsSync(treeArg)) {
  newick = readFileSync(treeArg, "utf8").trim();
  label = path.basename(treeArg);
}
console.log(`\n[the +/− buttons actually move the tree — ${label}]`);

const tree = new phylotree(newick);
if (tree.nodes && tree.nodes.data && tree.nodes.data.name === "root") {
  tree.nodes.data.name = "";
}

const container = window.document.getElementById("c");
let lastK = null, callbacks = 0;

function draw() {
  container.innerHTML = "";
  const display = tree.render({
    container,
    "left-right-spacing": "fit-to-size",
    "top-bottom-spacing": "fit-to-size",
    width: WIDTH,
    height: HEIGHT,
    "show-scale": "top",
    "draw-size-bubbles": false,
    "internal-names": false,
    selectable: false,
    collapsible: false,
    brush: false,
    zoom: true,
  });
  const svgNode = display.show ? display.show() : null;
  if (svgNode) container.appendChild(svgNode);
  attachZoom(display, (t) => {
    callbacks += 1;
    lastK = t.k;
    applyZoomStyles(container, t.k);
  });
  return display;
}

let display = draw();
assert(!!display && !!display.zoomBehavior,
  "phylotree built a zoom behaviour for the buttons to drive");

const gTransform = () => {
  const g = container.querySelector("g.phylotree-container");
  return g ? (g.getAttribute("transform") || "") : "";
};
const scaleOf = (t) => {
  const m = /scale\(([-0-9.eE]+)\)/.exec(t);
  return m ? parseFloat(m[1]) : 1;
};

const before = gTransform();
assert(zoomBy(display, ZOOM_STEP, [WIDTH / 2, HEIGHT / 2]), "the + button ran");
assert(callbacks > 0 && Math.abs(lastK - ZOOM_STEP) < 1e-9,
  `one press multiplies the zoom by ${ZOOM_STEP} (now ${lastK}x)`);
// The one that matters: phylotree's own listener still applied the transform.
// If our namespaced listener had replaced it, everything above would still pass
// and the tree would sit perfectly still.
assert(gTransform() !== before,
  "the tree's own transform changed — phylotree's unnamed zoom listener survived ours");
near(scaleOf(gTransform()), ZOOM_STEP, 1e-6, "…and it is the scale we asked for");

// The counter-scaling reached the DOM, where the CSS can read it.
const declared = container.style.getPropertyValue("--tree-branch-w");
assert(declared && parseFloat(declared) < 2,
  `the container now declares a thinner branch stroke (${declared || "unset"})`);

// Zooming in far: past phylotree's 10x default, and the widths keep up.
for (let i = 0; i < 12; i += 1) zoomBy(display, ZOOM_STEP, [WIDTH / 2, HEIGHT / 2]);
assert(lastK > 10, `the zoom passes 10x (reached ${lastK.toFixed(1)}x)`);
near(scaleOf(gTransform()), lastK, 1e-4, "the tree is drawn at the zoom the readout claims");
for (let i = 0; i < 30; i += 1) zoomBy(display, ZOOM_STEP, [WIDTH / 2, HEIGHT / 2]);
near(lastK, ZOOM_MAX, 1e-6, "and it stops at the cap rather than running away");

// A saved transform has to come back exactly, or ticking a checkbox loses the
// user's place in the tree.
const saved = { k: 6.5, x: -120, y: -240 };
assert(restoreZoom(display, saved), "restoreZoom ran");
near(lastK, saved.k, 1e-9, "the saved zoom came back");
near(scaleOf(gTransform()), saved.k, 1e-6, "…on the tree, not just in the readout");
assert(/translate\(\s*-?[\d.]+/.test(gTransform()), "…with a pan applied too");

// Fit: all the way back to the whole tree.
assert(zoomReset(display), "the Fit button ran");
near(lastK, 1, 1e-9, "Fit puts the zoom back to 1x");
near(scaleOf(gTransform()), 1, 1e-6, "…and the tree with it");

// Every render() builds a NEW TreeRender with its own zoom behaviour, so the
// wiring has to be redone each time. This is the failure where zoom works until
// you tick Bootstrap, then the buttons go dead.
display = draw();
assert(zoomBy(display, ZOOM_STEP * ZOOM_STEP, [WIDTH / 2, HEIGHT / 2]),
  "the buttons still work after a redraw");
near(scaleOf(gTransform()), ZOOM_STEP * ZOOM_STEP, 1e-6, "…and the redrawn tree moves");
assert(restoreZoom(display, saved) && Math.abs(scaleOf(gTransform()) - saved.k) < 1e-6,
  "a zoom saved before a redraw is restored onto the new render");

// The helpers are called with whatever the render left behind, including
// nothing at all.
assert(zoomBy(null, 2, [0, 0]) === false && zoomReset(undefined) === false
       && restoreZoom(display, null) === false && attachZoom({}, () => {}) === false,
  "every helper refuses a missing or half-built render instead of throwing");

// The CSS has to actually read the properties: setting a custom property no
// rule consumes is a no-op that nothing else would catch.
const css = readFileSync(path.join(FRONTEND, "src/styles.css"), "utf8");
for (const name of Object.keys(zoomStyleVars(1))) {
  assert(css.includes(`var(${name}`), `styles.css consumes ${name}`);
}
assert(/\.tree-canvas path\.branch\s*\{[^}]*stroke-width/.test(css),
  "the branch stroke-width rule is on .tree-canvas, so it applies outside pick mode too");
assert(!/\.tree-pick path\.branch:hover\s*\{[^}]*stroke-width/.test(css),
  "pick mode still does not shrink the hover target");

console.log(process.exitCode ? "\nFAILURES above." : "\nAll tree-zoom tests passed.");
