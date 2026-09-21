// Layout and clade-selection arithmetic.
//
// No DOM and no jsdom. The old tree tests needed a browser environment because
// the answers lived in SVG elements; here they live in arrays, so the parts
// that actually break — which tips a clade contains, where a row sits, what a
// search matches — are testable in plain node.
//
//   node test/tree_layout.test.mjs

import assert from "node:assert/strict";
import { phylotree } from "phylotree";
import {
  buildLayout, xAxis, tipIndicesUnder, tipNamesUnder, searchRows,
} from "../src/tree/layout.js";
import { sampleTips, stripZc } from "../src/clade.js";

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

function parse(nwk) {
  const t = new phylotree(nwk);
  if (t.nodes && t.nodes.data && t.nodes.data.name === "root") t.nodes.data.name = "";
  return t;
}

// A:0.1  B:0.2 under a node at 0.05; C:0.3 and (D,E) under a node at 0.07.
const NWK = "((A:0.1,B:0.2)95:0.05,(C:0.3,(D:0.15,E:0.05)80:0.1)90:0.07);";

test("tips take consecutive rows in newick order", () => {
  const lay = buildLayout(parse(NWK).nodes);
  assert.equal(lay.nLeaves, 5);
  const order = [];
  for (let r = 0; r < lay.nLeaves; r++) order.push(lay.names[lay.leafRows[r]]);
  assert.deepEqual(order, ["A", "B", "C", "D", "E"]);
});

test("x is the cumulative branch length from the root", () => {
  const lay = buildLayout(parse(NWK).nodes);
  const xOf = (n) => lay.xLen[lay.names.indexOf(n)];
  assert.ok(Math.abs(xOf("A") - 0.15) < 1e-9, `A at ${xOf("A")}`);
  assert.ok(Math.abs(xOf("B") - 0.25) < 1e-9, `B at ${xOf("B")}`);
  assert.ok(Math.abs(xOf("D") - 0.32) < 1e-9, `D at ${xOf("D")}`);
  assert.ok(Math.abs(lay.maxX - 0.37) < 1e-9, `maxX ${lay.maxX}`);
});

test("an internal node sits between its first and last child", () => {
  const lay = buildLayout(parse(NWK).nodes);
  const i = lay.names.indexOf("95");
  assert.equal(lay.row[i], 0.5);          // between rows 0 (A) and 1 (B)
  assert.equal(lay.rowMin[i], 0);
  assert.equal(lay.rowMax[i], 1);
});

test("rowMin/rowMax span the whole subtree, not just direct children", () => {
  const lay = buildLayout(parse(NWK).nodes);
  const i = lay.names.indexOf("90");       // (C,(D,E))
  assert.equal(lay.rowMin[i], 2);
  assert.equal(lay.rowMax[i], 4);
});

test("a clade's tips come back in row order", () => {
  const lay = buildLayout(parse(NWK).nodes);
  const i = lay.names.indexOf("80");
  assert.deepEqual(tipNamesUnder(lay, i), ["D", "E"]);
  assert.deepEqual(tipIndicesUnder(lay, i).map((k) => lay.names[k]), ["D", "E"]);
});

test("a tip's own clade is itself", () => {
  const lay = buildLayout(parse(NWK).nodes);
  assert.deepEqual(tipNamesUnder(lay, lay.names.indexOf("C")), ["C"]);
});

test("missing, negative and unparseable branch lengths become zero", () => {
  // A tree with no lengths at all is legal newick and must still lay out.
  const lay = buildLayout(parse("((A,B),(C,D));").nodes);
  assert.equal(lay.nLeaves, 4);
  for (let i = 0; i < lay.n; i++) {
    assert.ok(Number.isFinite(lay.xLen[i]), `x[${i}] is ${lay.xLen[i]}`);
    assert.equal(lay.xLen[i], 0);
  }
  // maxX never reports 0: a zero-width axis divides by zero downstream.
  assert.ok(lay.maxX > 0);
});

test("cladogram x puts every tip at the right edge", () => {
  const lay = buildLayout(parse(NWK).nodes);
  for (let r = 0; r < lay.nLeaves; r++) {
    assert.equal(lay.xTopo[lay.leafRows[r]], 1);
  }
  // and nothing is left of the root
  for (let i = 0; i < lay.n; i++) assert.ok(lay.xTopo[i] >= -1e-12);
});

test("xAxis picks the array and span for the mode", () => {
  const lay = buildLayout(parse(NWK).nodes);
  assert.equal(xAxis(lay, true).max, 1);
  assert.equal(xAxis(lay, true).x, lay.xTopo);
  assert.ok(Math.abs(xAxis(lay, false).max - 0.37) < 1e-9);
  assert.equal(xAxis(lay, false).x, lay.xLen);
});

test("a deep caterpillar tree does not blow the stack", () => {
  // Thousands of closely-related samples really do produce this shape, and a
  // recursive traversal dies on it.
  let nwk = "T0:0.01";
  for (let i = 1; i < 4000; i++) nwk = `(${nwk},T${i}:0.01):0.01`;
  const lay = buildLayout(parse(`${nwk};`).nodes);
  assert.equal(lay.nLeaves, 4000);
  assert.ok(lay.maxRank >= 3999, `maxRank ${lay.maxRank}`);
});

test("search matches on the displayed name as well as the raw one", () => {
  const lay = buildLayout(parse("((s1_zc.vcf:0.1,s2_zc.vcf:0.1):0.1,root:0.1);").nodes);
  // The suffix is in the raw name only; a user typing "s2" must still match.
  assert.deepEqual(searchRows(lay, "s2", stripZc), [1]);
  assert.deepEqual(searchRows(lay, "s2", null), [1]);
  // And a user typing the suffix matches the raw name.
  assert.equal(searchRows(lay, "_zc.vcf", stripZc).length, 2);
  assert.deepEqual(searchRows(lay, "", stripZc), []);
  assert.deepEqual(searchRows(lay, "   ", stripZc), []);
});

test("search is case-insensitive and returns rows in order", () => {
  const lay = buildLayout(parse("((AaA:0.1,bbb:0.1):0.1,(aab:0.1,ccc:0.1):0.1);").nodes);
  assert.deepEqual(searchRows(lay, "aa", null), [0, 2]);
});

test("sampleTips drops the reference tip and strips the suffix", () => {
  assert.deepEqual(sampleTips(["a_zc.vcf", "root", "b_zc.vcf.gz", "c"]), ["a", "b", "c"]);
  assert.deepEqual(sampleTips(["ROOT", "Root"]), []);
  assert.deepEqual(sampleTips([]), []);
});

test("selecting the clade that is only the reference tip yields nothing", () => {
  // This is the case the viewer has to report rather than silently no-op:
  // silence after a click is indistinguishable from a missed click.
  const lay = buildLayout(parse("((A:0.1,B:0.1):0.1,root:0.1);").nodes);
  const i = lay.leafRows[2];
  assert.equal(lay.names[i], "root");
  assert.deepEqual(sampleTips(tipNamesUnder(lay, i)), []);
});

test("internal labels are counted, so the Bootstrap control can be honest", () => {
  // The viewer disables its Bootstrap tick when there is nothing to show; that
  // decision is this count. NWK carries 95/80/90 on its three internal nodes.
  const lay = buildLayout(parse(NWK).nodes);
  assert.equal(lay.nInternalLabels, 3);
  assert.equal(lay.nNumericInternalLabels, 3);
});

test("a tree without support values reports none", () => {
  // What every Step 2 run produces today: RAxML's best tree, no bootstrap.
  const lay = buildLayout(parse("((A:0.1,B:0.2):0.05,(C:0.3,D:0.1):0.07);").nodes);
  assert.equal(lay.nInternalLabels, 0);
  assert.equal(lay.nNumericInternalLabels, 0);
});

test("the phantom root label is not counted as a support value", () => {
  // phylotree wraps the parsed tree in a synthetic node called "root", and a
  // vSNP3 tree also has a genuine `root` outgroup TIP. Neither is a label the
  // Bootstrap control should claim to be able to draw.
  const lay = buildLayout(parse("((A:0.1,B:0.1):0.1,root:0.1);").nodes);
  assert.equal(lay.nInternalLabels, 0);
});

test("named clades count as labels but not as bootstrap numbers", () => {
  const lay = buildLayout(parse("((A:0.1,B:0.2)clade_x:0.05,C:0.3);").nodes);
  assert.equal(lay.nInternalLabels, 1);
  assert.equal(lay.nNumericInternalLabels, 0);
});

test("an empty tree lays out without throwing", () => {
  const lay = buildLayout(null);
  assert.equal(lay.n, 0);
  assert.equal(lay.nLeaves, 0);
  assert.deepEqual(tipNamesUnder(lay, 0), []);
  assert.deepEqual(searchRows(lay, "x", null), []);
  assert.equal(lay.nInternalLabels, 0);
});

// --- Node ordering (FigTree's Increasing / Decreasing Node Order) ---------
//
// Asked for on a big tree, where the newick's own order scatters related
// samples down the canvas and the branching pattern cannot be read off the
// picture at all. Sorting siblings by subtree size is what turns it into a
// ladder.
//
// The property that matters is that ONLY the stacking changes: same tips, same
// topology, same branch lengths, same x. A reordering that quietly dropped or
// duplicated a tip would look plausible on screen and be wrong in every clade
// selection made from it, so the tests below check the tip SET as well as its
// order.

// Sibling clades of 3, 2 and then 1, 4 tips — deliberately unsorted in file
// order, and with a 5-vs-5 tie at the root.
const LADDER = "(((a:1,b:1,c:1):1,(d:1,e:1):1):1,(f:1,(g:1,h:1,i:1,j:1):1):1);";

const tipOrder = (lay) => {
  const out = [];
  for (let r = 0; r < lay.nLeaves; r++) out.push(lay.names[lay.leafRows[r]]);
  return out;
};

test("file order is the default and is unchanged", () => {
  const lay = buildLayout(parse(LADDER).nodes);
  assert.deepEqual(tipOrder(lay), ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]);
  assert.equal(lay.ordering, "file");
  assert.deepEqual(tipOrder(buildLayout(parse(LADDER).nodes, { ordering: "file" })),
                   tipOrder(lay));
});

test("increasing puts the smaller clade first", () => {
  const lay = buildLayout(parse(LADDER).nodes, { ordering: "increasing" });
  // (d,e)=2 before (a,b,c)=3; f=1 before (g,h,i,j)=4.
  assert.deepEqual(tipOrder(lay), ["d", "e", "a", "b", "c", "f", "g", "h", "i", "j"]);
});

test("decreasing puts the larger clade first", () => {
  const lay = buildLayout(parse(LADDER).nodes, { ordering: "decreasing" });
  assert.deepEqual(tipOrder(lay), ["a", "b", "c", "d", "e", "g", "h", "i", "j", "f"]);
});

test("a tie keeps the file's order, so redraws are stable", () => {
  // Both halves of the root hold 5 tips; neither ordering may swap them.
  const inc = buildLayout(parse(LADDER).nodes, { ordering: "increasing" });
  const dec = buildLayout(parse(LADDER).nodes, { ordering: "decreasing" });
  assert.ok(tipOrder(inc).indexOf("a") < tipOrder(inc).indexOf("f"));
  assert.ok(tipOrder(dec).indexOf("a") < tipOrder(dec).indexOf("f"));
});

test("re-stacking moves tips and changes nothing else", () => {
  const file = buildLayout(parse(LADDER).nodes);
  const dec = buildLayout(parse(LADDER).nodes, { ordering: "decreasing" });
  assert.equal(dec.n, file.n);
  assert.equal(dec.nLeaves, file.nLeaves);
  assert.deepEqual(tipOrder(dec).slice().sort(), tipOrder(file).slice().sort(),
                   "the same tips, once each");
  assert.equal(dec.maxX, file.maxX);
  assert.equal(dec.maxRank, file.maxRank);
  // Every tip keeps its distance from the root; only its row moved.
  const xOf = (lay) => Object.fromEntries(
    Array.from(lay.leafRows).map((i) => [lay.names[i], lay.xLen[i]]));
  assert.deepEqual(xOf(dec), xOf(file));
});

test("rows stay consecutive and clade spans stay contiguous", () => {
  const lay = buildLayout(parse(LADDER).nodes, { ordering: "increasing" });
  const rows = Array.from(lay.leafRows).map((i) => lay.row[i]);
  assert.deepEqual(rows, rows.map((_, k) => k), "0..n-1, in order");
  for (let i = 0; i < lay.n; i++) {
    if (lay.isLeaf[i]) continue;
    // A clade's rows must be exactly its tips' rows — this is what makes a
    // band, a selection and the hit test O(1) per node.
    const tipRows = tipIndicesUnder(lay, i).map((t) => lay.row[t]).sort((a, b) => a - b);
    assert.equal(lay.rowMin[i], tipRows[0]);
    assert.equal(lay.rowMax[i], tipRows[tipRows.length - 1]);
    assert.equal(tipRows.length, lay.rowMax[i] - lay.rowMin[i] + 1);
    assert.equal(lay.tips[i], tipRows.length, "tip counts agree with the spans");
  }
});

test("a deep caterpillar does not blow the stack", () => {
  // 4,000 nested clades: the shape that forced pass 1 to be iterative, and now
  // the row walk too.
  let nwk = "t0:1";
  for (let i = 1; i < 4000; i++) nwk = `(${nwk},t${i}:1):1`;
  const lay = buildLayout(parse(`${nwk};`).nodes, { ordering: "increasing" });
  assert.equal(lay.nLeaves, 4000);
  // Every level is [3,999-tip subtree, one tip], so increasing peels the lone
  // tips off first, outermost first, and the innermost pair ends up last.
  const order = tipOrder(lay);
  assert.deepEqual(order.slice(0, 3), ["t3999", "t3998", "t3997"]);
  assert.deepEqual(order.slice(-2), ["t0", "t1"], "the tied innermost pair keeps file order");
});

test("the tip search can be given the metadata's other spelling", () => {
  // A tree written through the reference's metadata: the tip is the display
  // name, and the name the user has is the one Step 1 ran.
  const lay = buildLayout(parse(
    "((GISAID-19152935_FAV-0863-5_Razorbill:1,other:1):1,third:1);").nodes);
  assert.deepEqual(searchRows(lay, "EPI-ISL-19152935", null), [],
                   "as typed, it is not on this tree");
  assert.deepEqual(searchRows(lay, "EPI-ISL-19152935", null,
                              ["GISAID-19152935_FAV-0863-5_Razorbill"]), [0]);
  // The term as typed still wins on its own, and an empty list changes nothing.
  assert.deepEqual(searchRows(lay, "other", null, []), [1]);
  assert.deepEqual(searchRows(lay, "", null, ["other"]), [],
                   "an empty box matches nothing, whatever is passed");
});

if (!process.exitCode) console.log(`ok — ${passed} tree layout assertions`);
