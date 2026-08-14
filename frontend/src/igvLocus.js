// Where to point igv.js, and whether it actually went there.
//
// Split out of IgvStandalone.jsx so it can be exercised without React, igv.js
// or a DOM (frontend/test/igv_locus.test.mjs) — the same reason the tree
// viewer's arithmetic lives in tree/. Both faults this module encodes were
// invisible failures, and an invisible failure is the kind worth a test.

// Normalize a locus string for igv.js. For a single-position locus like
// `CONTIG:POS`, igv.js's behavior depends on the underlying genome size:
// on a ~30kb viral genome it auto-zooms to a small flanking window; on a
// multi-megabase bacterial genome it silently falls back to showing the
// whole contig. We force consistent behavior by expanding a single-position
// locus to a small explicit range (`CONTIG:POS-FLANK – POS+FLANK`), so the
// user always lands centered on the variant with a few bases of context.
// Already-ranged loci (`CONTIG:START-END`) and contig-only strings pass
// through unchanged.
export function normalizeLocus(locus, flank = 25) {
  if (!locus) return "";
  const s = String(locus).trim();
  // Already a range — pass through.
  if (/:\d[\d,]*-\d/.test(s)) return s;
  // Single position: CONTIG:POS (POS may contain commas like 1,484,567).
  const m = s.match(/^([^:]+):([\d,]+)$/);
  if (m) {
    const contig = m[1];
    const pos = parseInt(m[2].replace(/,/g, ""), 10);
    if (Number.isFinite(pos) && pos >= 1) {
      const start = Math.max(1, pos - flank);
      const end = pos + flank;
      return `${contig}:${start}-${end}`;
    }
  }
  return s;
}

/**
 * Navigate to `locus`, and say so when it does not work.
 *
 * `browser.search()` is async in igv.js 3.x, so the shape this replaces —
 * `try { browser.search(x); } catch (e) {}` — could not do anything: a
 * synchronous try/catch never sees an async rejection, so the promise settled
 * unobserved and the viewer stayed where it was. Every failure along this path
 * was silent by construction, which is precisely the report: click a SNP,
 * nothing happens, nothing to go on.
 *
 * Awaiting also removes a race. The call was fired without awaiting and then
 * followed by `await addSample(...)`, so a navigation and a track load could
 * interleave inside igv.js's own view update.
 *
 * Returns "" on success, or a description of what went wrong.
 */
export async function goToLocus(browser, locus) {
  if (!browser || !locus) return "";
  const target = normalizeLocus(locus);
  try {
    await browser.search(target);
  } catch (err) {
    return `could not open ${target}: ${err && err.message ? err.message : err}`;
  }
  // igv.js answers an unresolvable locus by doing nothing rather than raising,
  // so success has to be checked afterwards. The signature to look for is the
  // browser still sitting on "all", the whole-genome pseudo-contig igv.js
  // creates for any multi-contig reference: its alignment readers return no
  // features there, so the reads track draws empty and the page looks broken.
  //
  // Only that exact signature is reported. A browser that exposes no frames at
  // all tells us nothing, and inventing an error out of an unreadable property
  // would put a false warning on every navigation that actually worked.
  try {
    const frames = browser.referenceFrameList;
    if (Array.isArray(frames) && frames.length && frames[0]) {
      const chr = String(frames[0].chr || "");
      if (chr.toLowerCase() === "all") {
        return `${target} did not resolve to a contig in this reference`;
      }
    }
  } catch (err) { /* the navigation itself worked; leave it at that */ }
  return "";
}
