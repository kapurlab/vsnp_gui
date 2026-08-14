// Pointing igv.js at a SNP, and noticing when it did not go.
//
// Both faults this file pins produced the same user-visible result — click a
// SNP in a table, nothing happens, nothing to go on — and both were silent by
// construction, which is why they survived two rounds of investigation:
//
//   1. `browser.search()` is async in igv.js 3.x, so wrapping it in a plain
//      try/catch caught nothing. A rejection went unobserved and the viewer
//      stayed put.
//   2. igv.js answers an unresolvable locus by doing nothing at all — no throw,
//      no return value to check on the browser — so "did it work?" has to be
//      asked afterwards, by looking at where the browser actually is.
//
//   node test/igv_locus.test.mjs

import assert from "node:assert/strict";
import { normalizeLocus, goToLocus } from "../src/igvLocus.js";

let passed = 0;
async function test(name, fn) {
  try {
    await fn();
    passed++;
  } catch (err) {
    console.error(`FAIL ${name}\n  ${err.message}`);
    process.exitCode = 1;
  }
}

/**
 * A stand-in for igv.js's browser.
 *
 * `lands` says which contig the browser ends up showing — including "all", the
 * whole-genome pseudo-contig igv.js creates for any multi-contig reference and
 * lands on by default. Its alignment readers return no features for "all", so a
 * viewer sitting there draws an empty reads track: the exact appearance of the
 * influenza bug.
 */
function fakeBrowser({ lands = null, throws = null, known = [] } = {}) {
  const calls = [];
  return {
    calls,
    referenceFrameList: [],
    async search(locus) {
      calls.push(locus);
      if (throws) throw new Error(throws);
      const chr = lands !== null
        ? lands
        : (known.includes(String(locus).split(":")[0]) ? String(locus).split(":")[0] : "all");
      this.referenceFrameList = chr ? [{ chr }] : [];
    },
  };
}

// The real contig names from the owl HPAI reference: slashes, dashes and
// underscores, each segment named after a different isolate.
const OWL = [
  "A/Blue-Winged_Teal/Alberta/39/2020_PB2",
  "A/Fancy_Ck/NL/FAV33/2021_PB1",
  "A/mallard/Nevada/AH0190913/2021_NP",
  "A/Fancy_Ck/NL/FAV33/2021_M",
];

await test("a bare position becomes a small window around it", () => {
  assert.equal(normalizeLocus("A/mallard/Nevada/AH0190913/2021_NP:729"),
               "A/mallard/Nevada/AH0190913/2021_NP:704-754");
  // Positions near the start must not go below 1.
  assert.equal(normalizeLocus("A/Fancy_Ck/NL/FAV33/2021_NS:10"),
               "A/Fancy_Ck/NL/FAV33/2021_NS:1-35");
  // Commas as thousands separators, as a bacterial position may carry.
  assert.equal(normalizeLocus("NC_002945.4:1,484,567"), "NC_002945.4:1484542-1484592");
});

await test("a segment name containing dashes survives normalisation", () => {
  // The range is split on '-' downstream, so a contig with its own dash is the
  // case to be sure of.
  assert.equal(normalizeLocus("A/Blue-Winged_Teal/Alberta/39/2020_PB2:1234"),
               "A/Blue-Winged_Teal/Alberta/39/2020_PB2:1209-1259");
});

await test("an existing range and a bare contig pass through untouched", () => {
  assert.equal(normalizeLocus("A/Fancy_Ck/NL/FAV33/2021_M:100-200"),
               "A/Fancy_Ck/NL/FAV33/2021_M:100-200");
  assert.equal(normalizeLocus("A/Fancy_Ck/NL/FAV33/2021_M"), "A/Fancy_Ck/NL/FAV33/2021_M");
  assert.equal(normalizeLocus(""), "");
  assert.equal(normalizeLocus(null), "");
});

await test("a real influenza locus navigates and reports success", async () => {
  const b = fakeBrowser({ known: OWL });
  for (const contig of OWL) {
    const err = await goToLocus(b, `${contig}:500`);
    assert.equal(err, "", `${contig} reported: ${err}`);
  }
  assert.equal(b.calls.length, OWL.length);
});

await test("landing on the whole-genome contig is reported, not accepted", async () => {
  // This is the influenza bug. igv.js does not raise; it simply leaves the
  // browser on "all", where the reads track renders nothing.
  const b = fakeBrowser({ lands: "all" });
  const err = await goToLocus(b, "A/mallard/Nevada/AH0190913/2021_NP:729");
  assert.ok(err, "a silent failure was accepted as success");
  assert.match(err, /did not resolve to a contig/);
  assert.match(err, /A\/mallard\/Nevada\/AH0190913\/2021_NP:704-754/,
               `the message must name the locus: ${err}`);
});

await test("an unknown contig is reported", async () => {
  const b = fakeBrowser({ known: OWL });
  const err = await goToLocus(b, "A/not/a/real/contig_XX:100");
  assert.ok(err, "an unresolvable locus was accepted as success");
});

await test("a rejected search is caught — a plain try/catch could not be", async () => {
  const b = fakeBrowser({ throws: "boom" });
  const err = await goToLocus(b, "A/Fancy_Ck/NL/FAV33/2021_M:914");
  assert.ok(err, "an async rejection went unobserved");
  assert.match(err, /boom/);
  assert.match(err, /could not open/);
});

await test("no browser or no locus is a no-op, not an error", async () => {
  assert.equal(await goToLocus(null, "chr1:100"), "");
  assert.equal(await goToLocus(fakeBrowser({ known: OWL }), ""), "");
  assert.equal(await goToLocus(fakeBrowser({ known: OWL }), null), "");
});

await test("a browser without referenceFrameList is not treated as a failure", async () => {
  // Older/odd igv states: the navigation itself did not raise, so do not
  // manufacture an error out of a missing property.
  const b = {
    async search() { /* succeeded, exposes nothing */ },
  };
  assert.equal(await goToLocus(b, "A/Fancy_Ck/NL/FAV33/2021_M:914"), "");
});

if (!process.exitCode) console.log(`ok — ${passed} igv locus assertions`);
