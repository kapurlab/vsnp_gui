// Clade selection helpers, kept out of the React component so they can be
// exercised directly: the tree viewer's branch-click path is the one piece of
// this feature that needs a DOM and a real phylotree render to test at all
// (see backend/app/../../frontend/test/clade_click.test.mjs).

// Tree tips may carry the vSNP3 `_zc.vcf` suffix; reduce to the sample name the
// way the viewer's display option does. The backend strips again on its side,
// so this only needs to be good, not perfect.
export function stripZc(name) {
  const n = String(name || "");
  const low = n.toLowerCase();
  if (low.endsWith("_zc.vcf.gz")) return n.slice(0, -"_zc.vcf.gz".length);
  if (low.endsWith("_zc.vcf")) return n.slice(0, -"_zc.vcf".length);
  return n;
}

// All tip names under a node — the clade a clicked branch leads to.
//
// phylotree hands the edge styler a d3-hierarchy link, so `edge.target` is the
// node the branch descends INTO and `.leaves()` is the clade. The manual walk
// is the fallback for a node object without d3's hierarchy methods (a plain
// parsed subtree), so a selection still resolves rather than coming back empty.
export function tipsUnder(node) {
  const out = [];
  if (!node) return out;
  if (typeof node.leaves === "function") {
    for (const leaf of node.leaves()) {
      const name = leaf && leaf.data && leaf.data.name;
      if (name) out.push(name);
    }
    if (out.length) return out;
  }
  (function walk(n) {
    if (!n) return;
    const kids = n.children;
    if (!kids || !kids.length) {
      const name = (n.data && n.data.name) || n.name;
      if (name) out.push(name);
      return;
    }
    kids.forEach(walk);
  })(node);
  return out;
}

// The tips a clade selection should carry: tip names, `_zc.vcf` stripped, with
// the reference/outgroup `root` tip dropped — it is not a sample and matching it
// against a SNP table's rows only ever produces a row that is already shown.
export function cladeSamples(node) {
  return tipsUnder(node)
    .filter((t) => String(t).toLowerCase() !== "root")
    .map((t) => stripZc(t));
}

// Walk up from a clicked element to the nearest thing carrying a phylotree
// datum, and say what was hit.
//
// This is why clade picking is a DELEGATED listener on the container rather
// than a handler per branch. phylotree's own branch handler calls
// modifySelection() + update(), which re-renders the tree and REPLACES the
// branch paths on every click — so per-element listeners are attached to
// elements that are about to be thrown away, and whether ours survives comes
// down to listener ordering. Delegation is immune to all of it: the container
// stays put, and d3 leaves the datum on each element for us to read back.
//
// Returns {kind: "edge"|"node", node, edge} — `node` is the clade root either
// way — or null when the click landed on the background.
export function resolveClickTarget(start, container) {
  let el = start;
  while (el && el !== container) {
    const d = el.__data__;
    if (d) {
      // A d3-hierarchy link has both ends; a node does not.
      if (d.target && d.source) return { kind: "edge", node: d.target, edge: d };
      if (d.data || d.children || d.parent) return { kind: "node", node: d, edge: null };
    }
    el = el.parentNode;
  }
  return null;
}

// Is this d3 selection backed by a real element we can style and bind to?
//
// phylotree 2.6.0's refresh() calls the stylers as
// `edges.each(d => this.edge_styler(select(this), d))` — an ARROW function, so
// `this` is the renderer, not the element, and the selection wraps the renderer
// object. refresh() runs on every branch click (phylotree's own selection
// machinery), so unguarded stylers throw on every click. Checking `.node()` is
// truthy is NOT enough: the renderer object is perfectly truthy. Duck-type the
// two things the stylers actually use.
export function styleable(element) {
  if (!element || typeof element.on !== "function"
      || typeof element.style !== "function"
      || typeof element.node !== "function") return false;
  const n = element.node();
  return !!n && typeof n.addEventListener === "function"
    && !!n.style && typeof n.style.setProperty === "function";
}
