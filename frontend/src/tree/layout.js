// Tree layout: one pass over the parsed tree, into flat arrays.
//
// The viewer draws on a canvas, so nothing here knows about pixels. A layout is
// the tree in TREE coordinates — a horizontal position per node and a row per
// tip — and view.js is the only thing that turns those into screen positions.
// That separation is the whole point of the rewrite: the old viewer inherited
// phylotree's SVG `scale(k)`, which multiplies stroke widths, node radii and
// font sizes along with the geometry, so a 4,611-tip tree stayed an unreadable
// black mass at every zoom level. Sizes can only stay constant on screen if
// geometry and appearance are computed separately, and that starts here.
//
// Flat arrays rather than objects: a 4,611-tip tree is ~9,200 nodes, redrawn on
// every pan frame. Indices into typed arrays keep that in one cache-friendly
// loop, and they give the subtree row spans (rowMin/rowMax) that make both
// culling and clade-band drawing O(1) per node.

/**
 * Build the layout for a parsed tree.
 *
 * @param root a phylotree/d3-hierarchy node: {data:{name,attribute}, children}
 * @returns the flat layout described field by field below
 */
export function buildLayout(root) {
  if (!root) return emptyLayout();

  // Pass 1: pre-order walk, assigning each node an index. Iterative, not
  // recursive — a pathological newick (a caterpillar of 4,000 tips is a real
  // shape for closely related samples) is thousands of levels deep and would
  // blow the JS stack.
  const refs = [];
  const parent = [];
  const childrenOf = [];
  const stack = [[root, -1]];
  while (stack.length) {
    const [node, par] = stack.pop();
    const i = refs.length;
    refs.push(node);
    parent.push(par);
    childrenOf.push([]);
    if (par >= 0) childrenOf[par].push(i);
    const kids = node.children;
    if (kids && kids.length) {
      // Pushed in reverse so the stack pops them in newick order: the drawn
      // top-to-bottom order of tips has to match the file, or a tree the user
      // has seen elsewhere reads as a different tree.
      for (let c = kids.length - 1; c >= 0; c--) stack.push([kids[c], i]);
    }
  }

  const n = refs.length;
  const blen = new Float64Array(n);
  const xLen = new Float64Array(n);
  const xTopo = new Float64Array(n);
  const rank = new Int32Array(n);
  const row = new Float64Array(n);
  const rowMin = new Float64Array(n);
  const rowMax = new Float64Array(n);
  const isLeaf = new Uint8Array(n);
  const names = new Array(n);

  let maxRank = 0;
  let maxX = 0;
  for (let i = 0; i < n; i++) {
    const d = refs[i].data || {};
    names[i] = d.name == null ? "" : String(d.name);
    // A branch length is optional, can be negative in a badly-behaved tree, and
    // arrives as a string. Anything unusable becomes 0 rather than NaN: one NaN
    // propagates through every cumulative distance below it and silently blanks
    // that whole side of the tree.
    const b = Number.parseFloat(d.attribute);
    blen[i] = Number.isFinite(b) && b > 0 ? b : 0;
    const p = parent[i];
    xLen[i] = p >= 0 ? xLen[p] + blen[i] : 0;
    rank[i] = p >= 0 ? rank[p] + 1 : 0;
    if (xLen[i] > maxX) maxX = xLen[i];
    if (rank[i] > maxRank) maxRank = rank[i];
    isLeaf[i] = childrenOf[i].length === 0 ? 1 : 0;
  }

  // Pass 2: rows. Tips take consecutive rows in pre-order, which is the order
  // they are drawn down the canvas; an internal node sits at the midpoint of
  // its first and last child, the standard rectangular layout.
  const leafRows = [];
  for (let i = 0; i < n; i++) {
    if (isLeaf[i]) {
      row[i] = leafRows.length;
      rowMin[i] = row[i];
      rowMax[i] = row[i];
      leafRows.push(i);
    }
  }
  const nLeaves = leafRows.length;
  // Post-order by walking indices backwards: a pre-order index is always
  // greater than its parent's, so descending order visits every child before
  // its parent without a second traversal.
  for (let i = n - 1; i >= 0; i--) {
    if (isLeaf[i]) continue;
    const kids = childrenOf[i];
    let lo = Infinity;
    let hi = -Infinity;
    for (let c = 0; c < kids.length; c++) {
      const k = kids[c];
      if (rowMin[k] < lo) lo = rowMin[k];
      if (rowMax[k] > hi) hi = rowMax[k];
    }
    rowMin[i] = lo;
    rowMax[i] = hi;
    row[i] = (row[kids[0]] + row[kids[kids.length - 1]]) / 2;
  }

  // Cladogram positions: every tip at 1.0, every internal node one step left of
  // its nearest child. Branch lengths on these trees are often 1e-5 apart, so
  // the phylogram piles thousands of tips into a few pixels of width and the
  // topology — which clade is which — cannot be read at all. Same tree, same
  // rows, different x: switching between them costs a redraw, not a rebuild.
  const step = maxRank > 0 ? 1 / maxRank : 1;
  for (let i = n - 1; i >= 0; i--) {
    if (isLeaf[i]) {
      xTopo[i] = 1;
      continue;
    }
    const kids = childrenOf[i];
    let lo = Infinity;
    for (let c = 0; c < kids.length; c++) {
      if (xTopo[kids[c]] < lo) lo = xTopo[kids[c]];
    }
    xTopo[i] = lo - step;
  }
  // The root can land marginally below 0 through floating-point drift; shift so
  // the drawn tree always starts at exactly 0.
  let minTopo = 0;
  for (let i = 0; i < n; i++) if (xTopo[i] < minTopo) minTopo = xTopo[i];
  if (minTopo < 0) for (let i = 0; i < n; i++) xTopo[i] -= minTopo;

  // CSR-style child index, built once: the draw loop and the hit test both walk
  // children per node, and an array-of-arrays allocates 9,200 sub-arrays that
  // the GC then has to keep alive for the life of the tree.
  const childStart = new Int32Array(n + 1);
  for (let i = 0; i < n; i++) childStart[i + 1] = childStart[i] + childrenOf[i].length;
  const childIdx = new Int32Array(childStart[n]);
  for (let i = 0, w = 0; i < n; i++) {
    for (const c of childrenOf[i]) childIdx[w++] = c;
  }

  return {
    n,
    nLeaves,
    refs,
    parent: Int32Array.from(parent),
    childStart,
    childIdx,
    isLeaf,
    names,
    blen,
    xLen,
    xTopo,
    rank,
    row,
    rowMin,
    rowMax,
    leafRows: Int32Array.from(leafRows),
    maxX: maxX > 0 ? maxX : 1,
    maxRank,
    // A newick with no `:length` anywhere is legal and RAxML bootstrap
    // consensus trees are sometimes written that way. Drawn as a phylogram it
    // is a single vertical line, so the viewer opens such a tree in topology
    // mode instead of showing the user nothing and waiting to be asked.
    noBranchLengths: maxX <= 0,
  };
}

function emptyLayout() {
  return {
    n: 0, nLeaves: 0, refs: [], parent: new Int32Array(0),
    childStart: new Int32Array(1), childIdx: new Int32Array(0),
    isLeaf: new Uint8Array(0), names: [], blen: new Float64Array(0),
    xLen: new Float64Array(0), xTopo: new Float64Array(0),
    rank: new Int32Array(0), row: new Float64Array(0),
    rowMin: new Float64Array(0), rowMax: new Float64Array(0),
    leafRows: new Int32Array(0), maxX: 1, maxRank: 0, noBranchLengths: false,
  };
}

/** The x array for the current mode, and the value the whole tree spans. */
export function xAxis(layout, cladogram) {
  return cladogram
    ? { x: layout.xTopo, max: 1 }
    : { x: layout.xLen, max: layout.maxX };
}

/** Node indices of every tip under `i`, itself included when it is a tip. */
export function tipIndicesUnder(layout, i) {
  const out = [];
  if (i == null || i < 0 || i >= layout.n) return out;
  // The tips under a node are exactly the rows it spans, and rows are already
  // indexed — so this is a slice, not a walk, however big the clade is.
  const lo = Math.round(layout.rowMin[i]);
  const hi = Math.round(layout.rowMax[i]);
  for (let r = lo; r <= hi; r++) out.push(layout.leafRows[r]);
  return out;
}

/** Tip names under `i`, in row order. */
export function tipNamesUnder(layout, i) {
  return tipIndicesUnder(layout, i).map((k) => layout.names[k]);
}

/**
 * Rows whose tip name matches `term`, case-insensitively.
 *
 * Returns rows rather than node indices because everything the search then does
 * — reveal it, mark it in the overview, step to the next one — is positional.
 */
export function searchRows(layout, term, displayName) {
  const out = [];
  const t = String(term || "").trim().toLowerCase();
  if (!t) return out;
  for (let r = 0; r < layout.nLeaves; r++) {
    const i = layout.leafRows[r];
    const raw = layout.names[i] || "";
    const shown = displayName ? displayName(raw) : raw;
    if (raw.toLowerCase().includes(t) || shown.toLowerCase().includes(t)) out.push(r);
  }
  return out;
}
