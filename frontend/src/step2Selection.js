// What a Step 2 run compares, as two pure functions.
//
// This lives outside App.jsx because the answer is arithmetic over five
// independent sets, and getting it wrong is invisible: the run starts, vsnp3
// builds a matrix, and the matrix is of the wrong samples. The bug that pulled
// it out here was exactly that — a project with every source unticked reported
// "185 VCFs, ready to Run", and the 185 were the only VCFs the user had NOT
// chosen (see selectStep2Run's noSourceTicked branch).
//
// The vocabulary, once:
//
//   set          every VCF physically in step2/vcf_database. Cumulative and
//                never pruned, so it is a superset of any one run.
//   sources      what the Build tab ticks: this project's Step 1 samples
//                (box 1) and each reference database panel (box 2). A run
//                keeps what its ticked sources claim.
//   unclaimed    in the set, claimed by no source — an old import, a VCF
//                hand-copied into the folder, or a Step 1 sample whose folder
//                has since been removed. No tick box can drop these, because
//                attributing them would be guesswork, so they are compared
//                whatever the ticks say. Callers surface the count.
//   tiers        the three exclusion stores, unioned at run time by the
//                backend and mirrored by comparisonSamples:
//                  A blocklist  — the reference's *_remove_from_analysis.xlsx
//                  B step1      — ticked in Step 1 Results; EXEMPTED for an
//                                 accession an enabled panel supplies
//                  C build      — ticked in the Step 2 sample list

/**
 * Which samples this run's sources choose, and which the set holds back.
 *
 * @param {object} args
 * @param {string[]} args.setSamples      every sample in vcf_database
 * @param {Set<string>} args.projectSamples  this project's Step 1 samples
 * @param {string[]} args.projectSamplesInSet  ...intersected with the set
 * @param {{enabled: boolean, samples: string[]}[]} args.panels reference databases
 * @param {boolean} args.useVcfDb         box 1 ticked
 * @param {"build"|"list"} args.mode      which setup tab is showing
 * @param {Set<string>} [args.listKeep]   list mode: what the pasted names matched
 * @param {boolean} [args.listIncludeDbs] list mode: also add the ticked panels
 * @returns {{keep: Set<string>, leaveOut: string[], fromList: number,
 *            fromDbs: number, noSourceTicked: boolean}}
 */
/** Distinct sample names, order preserved.
 *
 * `setSamples` is one entry per FILE, so a sample holding both `X_zc.vcf` and
 * `X_zc.vcf.gz` arrives twice. Every count derived from it then double-counted
 * that sample — "8,581 others stay out of this run" was a file tally being
 * subtracted from a name tally. The run itself is keyed by sample name, so the
 * names are what the arithmetic must use.
 */
function distinct(names) {
  return [...new Set(names || [])]
}

export function selectStep2Run({
  setSamples: setSamplesRaw,
  projectSamples,
  projectSamplesInSet,
  panels,
  useVcfDb,
  mode,
  listKeep,
  listIncludeDbs,
}) {
  const setSamples = distinct(setSamplesRaw)
  const inSet = new Set(setSamples);
  // Panel membership, restricted to what is physically in the database: a
  // panel accession that was never collected cannot be compared.
  const enabledPanelSamples = new Set();
  const anyPanelSamples = new Set();
  (panels || []).forEach((p) => {
    (p.samples || []).forEach((s) => {
      if (!inSet.has(s)) return;
      anyPanelSamples.add(s);
      if (p.enabled) enabledPanelSamples.add(s);
    });
  });

  if (mode === "list") {
    // The pasted list is an explicit allow-list, so everything unnamed is
    // dropped — unclaimed VCFs included. This is the tab to reach for when the
    // answer must contain exactly the samples you typed.
    const keep = new Set(listKeep || []);
    const fromList = keep.size;
    if (listIncludeDbs) enabledPanelSamples.forEach((s) => keep.add(s));
    return {
      keep,
      leaveOut: setSamples.filter((s) => !keep.has(s)),
      fromList,
      // What the databases ADD on top of the list, so fromList + fromDbs is
      // exactly the total (a listed sample that is also a panel accession is
      // counted once, under the list).
      fromDbs: keep.size - fromList,
      noSourceTicked: false,
    };
  }

  // No source ticked = nothing chosen, full stop.
  //
  // Without this the "never drop a VCF we cannot attribute" rule below runs
  // inside out: with every source off, the only survivors are the unclaimed
  // VCFs, so an empty selection offers a ready-to-Run comparison of precisely
  // the samples the user did not pick. An empty keep set is what the caller's
  // run veto already refuses, in words that point at the fix.
  const anySourceTicked = useVcfDb || (panels || []).some((p) => p.enabled);
  if (!anySourceTicked) {
    return {
      keep: new Set(),
      leaveOut: [...setSamples],
      fromList: 0,
      fromDbs: 0,
      noSourceTicked: true,
    };
  }

  // Ticked sources decide what is kept; an unclaimed sample is attributed to
  // nothing and so is never dropped. A sample can belong to several sources —
  // a project sample that is also a panel accession is kept if EITHER is
  // ticked.
  const keep = new Set();
  if (useVcfDb) projectSamplesInSet.forEach((s) => keep.add(s));
  enabledPanelSamples.forEach((s) => keep.add(s));
  const leaveOut = setSamples.filter(
    (s) => !keep.has(s) && (projectSamples.has(s) || anyPanelSamples.has(s))
  );
  const leftOut = new Set(leaveOut);
  return {
    keep: new Set(setSamples.filter((s) => !leftOut.has(s))),
    leaveOut,
    fromList: 0,
    fromDbs: enabledPanelSamples.size,
    noSourceTicked: false,
  };
}

/**
 * The samples vsnp3 will actually read: the run's selection minus the three
 * exclusion tiers, in the same order the backend applies them.
 *
 * Every count on screen has to come from here rather than from `keep`, which
 * counts only the source ticks: a project that excluded 4,616 samples in Step 1
 * Results still read "VCFs in set: 8,607 — ready to Run".
 *
 * @param {Set<string>|string[]} keep    selectStep2Run's keep set
 * @param {object} tiers
 * @param {Record<string, boolean>} tiers.blocklist        tier A
 * @param {Record<string, boolean>} tiers.step1Excluded    tier B
 * @param {Record<string, boolean>} tiers.buildExcluded    tier C
 * @param {Record<string, boolean>} tiers.panelAccessions  exempts tier B
 * @returns {string[]}
 */
export function comparisonSamples(keep, { blocklist, step1Excluded, buildExcluded, panelAccessions } = {}) {
  const A = blocklist || {};
  const B = step1Excluded || {};
  const C = buildExcluded || {};
  const exempt = panelAccessions || {};
  return Array.from(keep).filter((s) => !(A[s] || C[s] || (B[s] && !exempt[s])));
}

/**
 * VCFs in the set that no source can claim. Not a filter — the run compares
 * them regardless — but the pane has to be able to say how many there are.
 *
 * @param {string[]} setSamples
 * @param {Set<string>} projectSamples
 * @param {Set<string>} panelSampleSet  every accession any configured panel holds
 * @returns {string[]}
 */
export function unclaimedSamples(setSamples, projectSamples, panelSampleSet) {
  return setSamples.filter((s) => !projectSamples.has(s) && !panelSampleSet.has(s));
}

// --- A pasted list of names, resolved against the project -----------------
//
// Lifted out of App.jsx when the metadata cross-reference went in, because at
// that point it stopped being "compare two strings" and became a rule with
// enough cases to be worth testing on its own.
//
// The reason it needed one: the names people paste most often come from the
// FIRST COLUMN OF A SNP TABLE, and vSNP3 wrote that column through the
// reference's metadata file. So the list says
// `GISAID-19152935_FAV-0863-5_Razorbill_2022_CAN-NL` while the sample in the
// set is `EPI-ISL-19152935`, and no comparison of those two strings will ever
// connect them — the metadata replaced the id, it did not decorate it. Pasting
// a group straight out of its own SNP table matched nothing.
//
// `counterparts` closes that: {token: [other spellings of it]}, from the
// backend's /resolve-names, which reads the same metadata workbook vSNP3
// renamed from. A token is tried under its own spelling first and then under
// each counterpart, so the metadata can only ADD matches — a list that worked
// before works identically, at the same tier.

/** Split pasted text into candidate names.
 *
 * Line by line, because the two ways people paste need different splitting. A
 * spreadsheet column arrives TAB-delimited with the label in later columns, so
 * on a tabbed line only the first column is a name; otherwise every
 * space/comma/semicolon-separated word on the line is one. A `#` line is a
 * comment, and a pasted file name (…_zc.vcf.gz) is accepted as the name.
 */
export function listTokens(text) {
  const tokens = [];
  String(text || "").split(/\r?\n/).forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) return;
    const head = trimmed.includes("\t") ? trimmed.split("\t")[0] : trimmed;
    head.split(/[\s,;]+/).forEach((f) => {
      const t = f.trim().replace(/^["']|["']$/g, "").replace(/(_zc)?\.vcf(\.gz)?$/i, "");
      if (t && !t.startsWith("#")) tokens.push(t);
    });
  });
  return tokens;
}

/** The part of a name that identifies the sample: left of the first underscore. */
export function leadingId(name) {
  const head = String(name).split("_")[0].trim();
  return head.length >= 4 ? head.toLowerCase() : "";
}

/** How well `token` names `sample`; 0 for not at all, lower is better.
 *
 *   1 exact      — the names are identical.
 *   2 leading ID — same text left of the first underscore (4+ characters, so a
 *                  stray fragment can't sweep up the project).
 *   3 prefix     — for IDs with no underscore at all (dashed lab IDs), one
 *                  name is the other's prefix at a `-` or `.` boundary:
 *                  `13-1941` finds `13-1941-6-S4-L001`.
 */
export function matchTier(sample, token) {
  const s = String(sample).toLowerCase();
  const t = String(token).toLowerCase();
  if (s === t) return 1;
  const ls = leadingId(sample);
  const lt = leadingId(token);
  if (ls && ls === lt) return 2;
  const boundary = (long, short) =>
    long.length > short.length && long.startsWith(short) && /[-.]/.test(long[short.length]);
  if (boundary(s, t) || boundary(t, s)) return 3;
  return 0;
}

/**
 * Resolve a pasted list against the samples available to the run.
 *
 * @param {string} text            what the user pasted
 * @param {Iterable<string>} candidates  samples this run could compare
 * @param {Record<string,string[]>} [counterparts]  token -> other spellings
 * @returns {{tokens: string[], rows: object[], keep: Set<string>,
 *            unmatched: string[], ambiguous: object[], viaMetadata: number}}
 */
export function resolveList(text, candidates, counterparts) {
  const tokens = listTokens(text);
  const alt = counterparts || {};
  const keep = new Set();
  const rows = [];
  const unmatched = [];
  const ambiguous = [];
  const seen = new Set();
  let viaMetadata = 0;
  const all = [...(candidates || [])];
  tokens.forEach((token) => {
    const key = token.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    // The token's OWN spelling decides, whenever it decides anything. The
    // metadata is consulted only for a token that matched nothing, which makes
    // this change strictly additive: every list that worked before resolves to
    // the same samples at the same tier, and "matched through the reference
    // metadata" in the pane means it genuinely could not have matched
    // otherwise.
    let bestTier = 0;
    let matches = [];
    let matchedVia = "";
    const against = (spelling, via) => {
      all.forEach((sample) => {
        const tier = matchTier(sample, spelling);
        if (!tier) return;
        if (!bestTier || tier < bestTier) {
          bestTier = tier; matches = [sample]; matchedVia = via;
        } else if (tier === bestTier && !matches.includes(sample)) {
          matches.push(sample);
        }
      });
    };
    against(token, "");
    if (!matches.length) {
      (alt[token] || []).forEach((spelling) => against(spelling, spelling));
    }
    if (!matches.length) { unmatched.push(token); return; }
    if (matchedVia) viaMetadata++;
    if (matches.length > 1) ambiguous.push({ token, matches });
    matches.forEach((m) => keep.add(m));
    rows.push({ token, matches, tier: bestTier, via: matchedVia });
  });
  return { tokens, rows, keep, unmatched, ambiguous, viaMetadata };
}
