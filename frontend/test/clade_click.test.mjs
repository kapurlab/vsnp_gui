// Does clicking a branch actually select a clade?
//
// This is the one part of the clade -> SNP table feature that cannot be tested
// from Python: it needs a DOM, a real phylotree render, and a real click. It was
// reported as "I'm clicking on a tree but nothing is happening" while every
// non-DOM check passed — so this closes exactly the gap that let that through.
//
// It renders a REAL vSNP3 tree and dispatches genuine MouseEvents at branches
// and nodes, through the same DELEGATED capture-phase listener the viewer uses.
// The helpers under test are imported from src/clade.js, never copied — an
// earlier version of this file duplicated the guard and the copies drifted
// immediately.
//
// What made the feature fragile, and what this pins: phylotree's own branch
// handler calls modifySelection() + update(), which REPLACES every branch path
// on each click. A handler attached per element is therefore bound to something
// about to be discarded, and whether it survives depends on listener order —
// under jsdom it does not survive at all. Delegation on the container does.
//
// Run:  node test/clade_click.test.mjs [path/to/tree.tre]
// jsdom is not a project dependency (the app never runs under it); the test
// skips with a clear message when it is missing.

import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { cladeSamples, resolveClickTarget, styleable, tipsUnder } from "../src/clade.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");

// A small tree with a clearly-shaped clade, used when no .tre is passed.
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
function assertEq(actual, expected, msg) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  return assert(a === e, `${msg}${a === e ? ` = ${a}`
    : `\n        expected ${e}\n        got      ${a}`}`);
}

let JSDOM;
try {
  ({ JSDOM } = await import("jsdom"));
} catch {
  console.log("SKIP: jsdom is not installed.\n" +
    "      cd frontend && npm i --no-save jsdom");
  process.exit(0);
}

// ---------------------------------------------------------------------------
// A DOM good enough for phylotree: it measures text and boxes, which jsdom does
// not implement, so those are stubbed with plausible numbers. Layout geometry is
// not what is under test — reaching the handler is.
const dom = new JSDOM(`<!doctype html><html><body><div id="c"></div></body></html>`,
  { pretendToBeVisual: true });
const { window } = dom;
// CustomEvent matters as much as document: phylotree dispatches one on
// `document` during render, and jsdom rejects an event built by another realm's
// constructor ("parameter 1 is not of type 'Event'").
for (const k of ["window", "document", "navigator", "Node", "Element", "SVGElement",
                 "MouseEvent", "Event", "CustomEvent", "HTMLElement",
                 "getComputedStyle", "requestAnimationFrame"]) {
  // `navigator` is getter-only on modern node, so plain assignment throws.
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

const require = createRequire(path.join(FRONTEND, "package.json"));
const { phylotree } = require("phylotree");

// ---------------------------------------------------------------------------
const treeArg = process.argv[2];
let newick = FALLBACK_NEWICK, label = "fallback tree";
if (treeArg && existsSync(treeArg)) {
  newick = readFileSync(treeArg, "utf8").trim();
  label = path.basename(treeArg);
}
console.log(`[a branch or node click selects a clade — ${label}]`);

const tree = new phylotree(newick);
if (tree.nodes && tree.nodes.data && tree.nodes.data.name === "root") {
  tree.nodes.data.name = "";
}
const tips = tree.getTips ? tree.getTips() : [];
console.log(`  (tree has ${tips.length} tips)`);

// The viewer's own wiring. Clade picking is the DEFAULT (not a mode to find and
// switch on); reroot mode takes the click while it is set.
let cladePick = true;
let rerootMode = false;
let selected = null;
let rerooted = 0;
let styledBranches = 0;
let styleableRejections = 0;   // phylotree handing over a bogus selection

const container = window.document.getElementById("c");

// The delegated, capture-phase listener — the whole point.
let reachedPhylotree = 0;   // did the click get past us to phylotree's own handlers?
container.addEventListener("click", (ev) => {
  const hit = resolveClickTarget(ev.target, container);
  if (!hit) return;
  if (!rerootMode && !cladePick) return;
  ev.stopPropagation();
  if (rerootMode) {
    if (hit.kind === "edge") rerooted += 1;
    return;
  }
  const s = cladeSamples(hit.node);
  if (s.length) selected = s;
}, true);
// A bubble-phase listener on the same container stands in for phylotree's own
// element handlers: if it runs, the click was not stopped.
container.addEventListener("click", () => { reachedPhylotree += 1; }, false);

function draw() {
  container.innerHTML = "";
  const display = tree.render({
    container,
    "left-right-spacing": "fit-to-size",
    "top-bottom-spacing": "fit-to-size",
    width: 900,
    height: 700,
    "show-scale": "top",
    "draw-size-bubbles": false,
    "internal-names": false,
    selectable: false,
    collapsible: false,
    brush: false,
    zoom: true,
    "edge-styler": (element) => {
      if (!styleable(element)) { styleableRejections += 1; return; }
      styledBranches += 1;
      if (cladePick || rerootMode) element.style("cursor", "pointer");
    },
    "node-styler": (element) => {
      if (!styleable(element)) { styleableRejections += 1; return; }
      if (cladePick) element.style("cursor", "pointer");
    },
  });
  const svgNode = display.show ? display.show() : null;
  if (svgNode) container.appendChild(svgNode);
  return svgNode;
}

const svgNode = draw();
assert(!!svgNode, "phylotree returned an SVG node");
assert(styledBranches > 0,
  `the edge styler ran (${styledBranches} branches) — 0 would mean no branch is ever clickable`);
const branches = [...container.querySelectorAll("path.branch")];
assert(branches.length > 0, `branch paths are in the DOM (${branches.length})`);
const branchesForGuardCheck = branches;

// phylotree 2.6.0's refresh() calls the stylers with select(this) from inside an
// arrow function, so `this` is the renderer: a "selection" with no element
// behind it, which throws the moment a styler touches it. styleable() is what
// stands between that and a console full of TypeErrors on every click, so it is
// tested on the shape that actually turns up rather than on whether this
// particular render happened to produce one.
const fakeRendererSelection = {
  on: () => {}, style: () => {}, node: () => ({ some: "renderer object" }),
};
assert(!styleable(fakeRendererSelection),
  "styleable() rejects a selection wrapping a non-element (phylotree's refresh bug)");
assert(!styleable(null) && !styleable({}), "styleable() rejects nothing-at-all");
assert(styleable({ on: () => {}, style: () => {}, node: () => branchesForGuardCheck[0] }),
  "styleable() accepts a selection wrapping a real element");

const styled = branches.filter((b) => (b.getAttribute("style") || "").includes("cursor"));
assert(styled.length > 0, `branches are marked clickable (${styled.length})`);

function click(el) {
  selected = null;
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
  return selected;
}

// An internal branch: its clade is more than one tip and fewer than all of them.
const internal = branches.filter((b) => {
  const d = b.__data__;
  if (!d || !d.target) return false;
  const n = tipsUnder(d.target).length;
  return n > 1 && n < tips.length;
});
let firstInternal = null;
if (assert(internal.length > 0, `the tree has internal branches to click (${internal.length})`)) {
  firstInternal = internal[0];
  const expected = cladeSamples(firstInternal.__data__.target);
  const got = click(firstInternal);
  assert(got !== null, "clicking an internal branch selected something");
  assertEq(got, expected, `the clade came back (${expected.length} samples)`);
  if (got) {
    assert(!got.includes("root"), "the outgroup 'root' tip is not part of a selection");
    assert(got.every((s) => !/_zc\.vcf$/i.test(s)), "tip names arrive with _zc.vcf stripped");
    assert(reachedPhylotree === 0,
      "a handled click does not reach phylotree's own selection/menu handlers");
  }
}

// Clicking the NODE at a fork selects the same clade. This is the easy target:
// a branch is an SVG path with no fill, so it answers pointer events only on
// its ~2px painted stroke, while the node marker is a blob you can hit.
const nodeEls = [...container.querySelectorAll("g.internal-node")]
  .filter((g) => g.__data__ && tipsUnder(g.__data__).length > 1
                 && tipsUnder(g.__data__).length < tips.length);
if (assert(nodeEls.length > 0, `internal node markers are in the DOM (${nodeEls.length})`)) {
  const el = nodeEls[0];
  const expected = cladeSamples(el.__data__);
  assertEq(click(el), expected, `clicking a node selects its clade (${expected.length} samples)`);
  // A click on the node's child circle/text must resolve the same way — that is
  // what delegation has to get right.
  const inner = el.querySelector("circle, text");
  if (inner) assertEq(click(inner), expected, "…and so does a click on its marker or label");
}

// A terminal branch selects exactly its own sample: one row against the
// reference is a legitimate thing to want.
const terminal = branches.filter((b) => b.__data__ && b.__data__.target
  && tipsUnder(b.__data__.target).length === 1
  && String(b.__data__.target.data.name).toLowerCase() !== "root");
if (assert(terminal.length > 0, "terminal branches exist")) {
  const got = click(terminal[0]);
  assert(got && got.length === 1,
    `clicking a tip's own branch selects one sample (${got && got[0]})`);
}

// Clicking the background selects nothing — no datum to resolve.
assert(click(svgNode) === null, "a click on empty canvas selects nothing");

// The selection must survive phylotree replacing the DOM: its own click handler
// calls update(), and a redraw follows every React state change too.
if (firstInternal) {
  draw();
  const fresh = [...container.querySelectorAll("path.branch")].filter((b) => {
    const d = b.__data__;
    if (!d || !d.target) return false;
    const n = tipsUnder(d.target).length;
    return n > 1 && n < tips.length;
  });
  assert(fresh.length > 0, "branches exist again after a redraw");
  assert(click(fresh[0]) !== null,
    "a click still selects after the tree is redrawn (delegation survives it)");
}

// Selecting a clade must NOT rebuild the tree.
//
// Highlighting used to be a dependency of the render effect, so every click
// re-ran tree.render() — and phylotree keeps the zoom transform on the
// TreeRender instance it builds per render, so the user's zoom and pan were
// discarded. On a 1,000-tip tree zooming in is the only way to read tip labels,
// so clicking the clade you had just found threw you back to fit-to-size.
const svgBefore = container.querySelector("svg");
const gBefore = container.querySelector("g.phylotree-container");
const fakeTransform = "translate(120,40) scale(2.5)";
if (gBefore) gBefore.setAttribute("transform", fakeTransform);
const branchNow = container.querySelector("path.branch");
selected = null;
branchNow.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
assert(selected !== null, "the click selected a clade");
assert(container.querySelector("svg") === svgBefore,
  "the SVG was NOT replaced by selecting a clade");
assert(!gBefore || gBefore.getAttribute("transform") === fakeTransform,
  "…so an existing zoom/pan transform survives the selection");

// The pick-mode CSS has to target elements that actually exist. `g.node circle`
// matched NOTHING: phylotree classes leaves `node` and internal nodes
// `internal-node`, and leaves carry no circle with size bubbles off — so the
// "easier target" was styled on a selector that never applied.
const cssPath = path.join(FRONTEND, "src/styles.css");
const css = readFileSync(cssPath, "utf8");
assert(css.includes(".tree-pick g.internal-node circle"),
  "the CSS targets internal-node markers");
assert(!/\.tree-pick path\.branch:hover\s*\{[^}]*stroke-width/.test(css),
  "pick mode does not shrink phylotree's own 10px hover target");
const markerCount = container.querySelectorAll("g.internal-node circle").length;
assert(markerCount > 0,
  `internal-node markers exist to be styled (${markerCount})`);

// Reroot mode takes the branch click while it is on — never both at once.
rerootMode = true;
const before = rerooted;
const got = click(branches[0].isConnected ? branches[0]
  : container.querySelector("path.branch"));
assert(rerooted === before + 1, "with reroot mode on, a branch click reroots");
assert(got === null, "…and does NOT also select a clade");

// Clade picking off (no sibling SNP tables): inert.
rerootMode = false;
cladePick = false;
assert(click(container.querySelector("path.branch")) === null,
  "with clade picking off, a branch click selects nothing");

console.log(process.exitCode ? "\nFAILURES above." : "\nAll clade-click tests passed.");
