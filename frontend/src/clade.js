// Turning a picked clade into a list of sample names.
//
// These live outside the React component so they can be exercised directly
// (frontend/test/tree_layout.test.mjs). What used to be here as well — walking
// up the DOM from a clicked SVG element to find phylotree's datum, and
// duck-typing a d3 selection before styling it — went away with the SVG
// renderer: the viewer draws on a canvas now and resolves a click by
// arithmetic, in tree/hit.js.

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

// The tips a clade selection should carry: `_zc.vcf` stripped, with the
// reference/outgroup `root` tip dropped. It is not a sample, and matching it
// against a SNP table's rows only ever produces a row that is already shown.
export function sampleTips(names) {
  return (names || [])
    .filter((t) => String(t).toLowerCase() !== "root")
    .map((t) => stripZc(t));
}
