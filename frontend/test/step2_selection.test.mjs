// What a Step 2 run compares.
//
// Two faults are pinned here, both reported from the Ames HPC on a project with
// 8,606 VCFs in vcf_database, and both of the same shape: the run started, the
// matrix built, and the matrix was of the wrong samples. Nothing errored.
//
//   1. With EVERY source unticked, the "never drop a VCF we cannot attribute"
//      rule ran inside out. The tick boxes can only drop a sample they can
//      attribute to a source, so with nothing ticked the survivors were exactly
//      the unattributable ones — 185 old imports and hand-copied reference
//      VCFs — offered as "ready to Run" while box 1 still read "Nothing
//      selected". The 185 were the only VCFs the user had not chosen.
//
//   2. Every count on screen came from `keep`, which knows only about the
//      source ticks. A user who ticked all 4,641 rows in Step 1 Results, then
//      unticked the 25 of their dataset, was told the run would compare 8,607.
//      The number they had just spent the effort to shape appeared nowhere.
//
//   node test/step2_selection.test.mjs

import assert from "node:assert/strict";
import { selectStep2Run, comparisonSamples, unclaimedSamples } from "../src/step2Selection.js";

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

const mapOf = (names) => Object.fromEntries(names.map((n) => [n, true]));

/**
 * The reported project, in miniature and to scale.
 *
 * 30 Step 1 samples collected into vcf_database, plus 5 VCFs that arrived some
 * other way (an old import, a hand-copied reference control). No reference
 * database is configured — which is what made "ref db" on all five, and a run
 * consisting only of them, so hard to read as a bug in the ticks.
 */
function amesLike() {
  const step1 = Array.from({ length: 30 }, (_, i) => `WGS260${String(i).padStart(2, "0")}-DS1`);
  const strays = ["MBE1", "MBE10", "NVSL-BCG-Control", "P429", "SRR1173570"];
  return {
    setSamples: [...step1, ...strays].sort(),
    projectSamples: new Set(step1),
    projectSamplesInSet: step1,
    panels: [],           // nothing configured for this reference
    step1,
    strays,
  };
}

test("no source ticked compares nothing at all", () => {
  const { setSamples, projectSamples, projectSamplesInSet, panels, strays } = amesLike();
  const sel = selectStep2Run({
    setSamples, projectSamples, projectSamplesInSet, panels,
    useVcfDb: false, mode: "build",
  });
  assert.equal(sel.noSourceTicked, true, "the pane has to be able to say why");
  assert.equal(sel.keep.size, 0, "an empty selection must stay empty");
  assert.equal(sel.leaveOut.length, setSamples.length, "everything is held back");
  // The regression, stated as the user met it: the survivors used to be
  // precisely the samples no source claims.
  strays.forEach((s) => assert.ok(!sel.keep.has(s), `${s} must not be compared`));
});

test("ticking box 1 keeps this project's samples", () => {
  const { setSamples, projectSamples, projectSamplesInSet, panels, step1 } = amesLike();
  const sel = selectStep2Run({
    setSamples, projectSamples, projectSamplesInSet, panels,
    useVcfDb: true, mode: "build",
  });
  assert.equal(sel.noSourceTicked, false);
  assert.equal(sel.leaveOut.length, 0, "no source is unticked, so nothing is left out");
  step1.forEach((s) => assert.ok(sel.keep.has(s), `${s} should be compared`));
});

test("unattributable VCFs ride along once a source is ticked, and are counted", () => {
  const { setSamples, projectSamples, projectSamplesInSet, panels, strays } = amesLike();
  const sel = selectStep2Run({
    setSamples, projectSamples, projectSamplesInSet, panels,
    useVcfDb: true, mode: "build",
  });
  // Deliberate: dropping them would mean guessing where they came from. But
  // the pane must be able to name them, or a run of 35 where 30 were chosen
  // looks like the ticks were ignored.
  strays.forEach((s) => assert.ok(sel.keep.has(s), `${s} is compared regardless`));
  const unclaimed = unclaimedSamples(setSamples, projectSamples, new Set());
  assert.deepEqual(unclaimed.sort(), [...strays].sort());
});

test("the reported count is the count after exclusions, not before", () => {
  const { setSamples, projectSamples, projectSamplesInSet, panels, step1 } = amesLike();
  const sel = selectStep2Run({
    setSamples, projectSamples, projectSamplesInSet, panels,
    useVcfDb: true, mode: "build",
  });
  // The user's workflow: exclude every Step 1 row, then un-exclude the five of
  // the dataset they want compared.
  const wanted = step1.slice(0, 5);
  const excluded = step1.filter((s) => !wanted.includes(s));
  const cmp = comparisonSamples(sel.keep, { step1Excluded: mapOf(excluded) });
  assert.equal(sel.keep.size, 35, "the source ticks alone still see everything");
  assert.equal(cmp.length, 10, "5 wanted + 5 unattributable — not 35");
  wanted.forEach((s) => assert.ok(cmp.includes(s), `${s} was chosen`));
  excluded.forEach((s) => assert.ok(!cmp.includes(s), `${s} was excluded in Step 1 Results`));
});

test("all three exclusion tiers bite, and an enabled panel exempts tier B", () => {
  const keep = ["a", "b", "c", "d", "e"];
  const cmp = comparisonSamples(keep, {
    blocklist: mapOf(["a"]),        // tier A — the reference's own removal list
    step1Excluded: mapOf(["b", "d"]),  // tier B — ticked in Step 1 Results
    buildExcluded: mapOf(["c"]),    // tier C — ticked in the Step 2 sample list
    // "d" is also supplied by an enabled reference panel: an external panel VCF
    // is not a Step 1 sample, so a Step 1 exclusion of the same accession must
    // not drop it. Tier A and tier C are never exempted.
    panelAccessions: mapOf(["a", "d"]),
  });
  assert.deepEqual(cmp, ["d", "e"]);
});

test("unticking a panel leaves its samples out without touching the project's", () => {
  const setSamples = ["S1", "S2", "PANEL1", "PANEL2", "STRAY"];
  const sel = selectStep2Run({
    setSamples,
    projectSamples: new Set(["S1", "S2"]),
    projectSamplesInSet: ["S1", "S2"],
    panels: [{ enabled: false, samples: ["PANEL1", "PANEL2"] }],
    useVcfDb: true,
    mode: "build",
  });
  assert.deepEqual(sel.leaveOut.sort(), ["PANEL1", "PANEL2"]);
  assert.deepEqual(Array.from(sel.keep).sort(), ["S1", "S2", "STRAY"]);
});

test("a ticked panel is a source of its own, so box 1 can stay off", () => {
  const setSamples = ["S1", "PANEL1"];
  const sel = selectStep2Run({
    setSamples,
    projectSamples: new Set(["S1"]),
    projectSamplesInSet: ["S1"],
    panels: [{ enabled: true, samples: ["PANEL1"] }],
    useVcfDb: false,
    mode: "build",
  });
  assert.equal(sel.noSourceTicked, false, "a ticked database IS a source");
  assert.deepEqual(Array.from(sel.keep), ["PANEL1"]);
  assert.deepEqual(sel.leaveOut, ["S1"]);
  assert.equal(sel.fromDbs, 1);
});

test("the list tab is an allow-list: unattributable VCFs are dropped too", () => {
  const { setSamples, projectSamples, projectSamplesInSet, panels, step1, strays } = amesLike();
  const sel = selectStep2Run({
    setSamples, projectSamples, projectSamplesInSet, panels,
    useVcfDb: true, mode: "list",
    listKeep: new Set(step1.slice(0, 3)),
    listIncludeDbs: false,
  });
  assert.equal(sel.keep.size, 3, "exactly what was named");
  assert.equal(sel.fromList, 3);
  strays.forEach((s) => assert.ok(!sel.keep.has(s), `${s} was not on the list`));
});

test("a panel accession that was never collected cannot be compared", () => {
  // sample_count is read live from the panel folder, so a panel can name a VCF
  // that Build has not copied into vcf_database yet.
  const sel = selectStep2Run({
    setSamples: ["S1"],
    projectSamples: new Set(["S1"]),
    projectSamplesInSet: ["S1"],
    panels: [{ enabled: true, samples: ["NOT-COLLECTED"] }],
    useVcfDb: true,
    mode: "build",
  });
  assert.deepEqual(Array.from(sel.keep), ["S1"]);
  assert.equal(sel.fromDbs, 0, "a name with no file behind it adds nothing");
});

if (!process.exitCode) console.log(`step2 selection: ${passed} tests passed`);
