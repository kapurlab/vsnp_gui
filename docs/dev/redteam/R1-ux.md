# R1 — UX / interaction design (adversarial)

## Attack vectors

### 1. Cross-card navigation produces a dead end the user cannot escape or reconstruct
- Evidence: §3 rule 5; §8 "a 'Run AMR' button on step1 result pages"
- Severity: blocker
- Falsification: show a rendered AMR card that displays "arrived from vSNP project X / sample Y" with a breadcrumb or back-link, and confirm the link survives a browser refresh.
- Detail: The design says the AMR card opens with `?project=X&sample=Y` pre-filled (§3 rule 5). It says nothing about what the AMR card renders to communicate that context back to the user. A bench scientist who clicked "Run AMR" in vsnp_gui, waited 5 minutes for an assembly, and then lands in the AMR card sees no indication they came from vsnp_gui or which sample they are looking at. If they hit the back button they leave the OOD session entirely (OOD uses iframes / new tabs); if they bookmark the AMR card URL the bookmark encodes `?project=X&sample=Y` but says nothing about how to re-launch the underlying OOD session. The cross-card link is a one-way door with no signposting.

### 2. Badge density crosses the threshold of meaningful at realistic panel sizes
- Evidence: §3 "every card lights up its findings on the project's sample list (via `.badge()`)" + §11.4 badge ordering hand-wave
- Severity: major
- Falsification: show a wireframe of 16 samples × 5 primitive badges at the actual pixel density of the vsnp_gui sample table, with color-blind simulation applied, and demonstrate that any individual badge is distinguishable without tooltip interaction.
- Detail: The design shows up to 5 primitives per sample row (vSNP, Kraken, AMR, Sourmash, MLST). At 16 samples that is 80 badge chips on one page, each potentially colored red/yellow/green. The design does not address: (a) color-blind accessibility — `CLASS_COLORS` in `amrfinder.py` (lines 309-317) encodes eight distinct hues with no luminance differentiation; (b) what renders when screen real estate is insufficient — truncation? scroll? collapse?; (c) §11.4 says badge ordering within a verdict is "hand-waved" and deferred. A `review` badge for "mecA+" and a `review` badge for "57% Proteus mirabilis contamination" have radically different clinical urgency, but the user sees two identically styled yellow chips.

### 3. Greyed-out primitives are silent about the reason for their silence
- Evidence: §4 `applicable() == False`; §11.6 "PrimitiveSetupError"; §4 MLST example
- Severity: major
- Falsification: show the exact UI element rendered when `MLST.applicable()` returns False for a *M. sciuri* sample, and confirm it distinguishes "no scheme exists for your organism" from "MLST has not been run yet" from "MLST failed."
- Detail: The design defines `applicable()` as a classmethod that returns a bool. There are at least three distinct reasons a badge might be absent: (a) the primitive is inapplicable for this organism (`applicable() == False`), (b) the primitive has not yet been run, (c) the primitive ran and failed (`PrimitiveError`). The design assigns no distinct UI state to case (a) — the example given is "MLST has no *M. sciuri* scheme" (§4), but from the user's perspective the MLST column simply does not appear. A senior researcher who knows MLST exists will assume it was skipped due to a bug, not an intentional applicability gate. Norman's gulf of evaluation: the system's internal state (organism lacks scheme) produces no visible signal, leaving the user to wonder whether to contact the PI, re-run the pipeline, or file a bug.

### 4. `web()` returns an untyped dict; each card must re-implement rendering logic it cannot generalize
- Evidence: §4 `.web()` returns `dict[str, Any]`; §5 `AMRFinder.web()` returns `{"findings": [...], "class_colors": {...}, "tsv_url": "..."}` (lines 384-393)
- Severity: major
- Falsification: show the React component that renders an arbitrary primitive's `.web()` output without knowing the primitive type at compile time, and demonstrate it works for both AMR (gene table) and Kraken (taxonomy pie chart) without per-primitive branches.
- Detail: The contract claims GUI renderers consume `.web()` generically ("no per-tool special-casing in the GUI," §8). But `AMRFinder.web()` returns a findings list of gene-level dicts, class color maps, and a TSV download URL. A Kraken primitive would return taxonomy trees and bracken abundances. These shapes are irreconcilable without a per-primitive React component or a generic JSON viewer. The design silently defers this problem: §8's backend sketch returns `.web()` JSON but says nothing about the frontend component that renders it. The claim of "no per-tool special-casing" is false unless every primitive's web() output is constrained to a canonical shape — which the contract explicitly does not require (`dict[str, Any]`).

### 5. `samples.json` is a mutable shared file with no locking, no edit UI, and no recovery story
- Evidence: §3 `samples.json` description; §3 example JSON with user-supplied `organism`, `host`, `isolation_source`
- Severity: major
- Falsification: describe the exact user interaction for correcting `organism: "Mammaliicoccus sciuri"` to `organism: "Staphylococcus aureus"` after step1 has already run and AMR has already been invoked — including what happens to previously computed `applicable()` results.
- Detail: The design shows `organism`, `host`, and `isolation_source` as user-supplied fields in `samples.json` (§3), but provides no UI for entering or editing them. It is unclear whether these are entered before step1 (when FASTQ files are loaded), during step1 (as metadata), or after (as annotations). If a user types the wrong organism, `applicable()` produces wrong results for all subsequent primitives — MLST may be suppressed, AMRFinder may run in generic vs. organism-specific mode. Correcting the typo requires knowing to edit a JSON file on the server, after which previously computed primitive results become stale with no staleness indicator. The design does not acknowledge this invalidation problem.

### 6. Error surfaces collapse all failure modes into a single red badge with no recovery path
- Evidence: §11.6 "Define `PrimitiveError` and `PrimitiveSetupError`... Front-ends catch these and render a `verdict=fail` badge"
- Severity: major
- Falsification: show three distinct UI states for: (a) tool binary not found on PATH, (b) DB missing/outdated, (c) FASTA corrupted — and confirm each presents an actionable next step the user can take without contacting a system administrator.
- Detail: The design's entire error handling specification is one sentence in §11.6: catch the exception, render a `fail` badge. A `fail` badge gives the user no information: did AMRFinder crash because the `amrfinder` conda env is missing? Because the DB is out of date? Because the FASTA was truncated? Because the assembly step failed upstream? All of these look identical in the badge layer. The badge has a `detail` field (§4), but the design does not specify what text goes there for error cases — `PrimitiveResult` carries `exit_code` and `log_path`, but neither surfaces to the user. A bench scientist who sees a red "AMR: fail" chip has no action available other than asking the PI, which is not a recovery path.

### 7. The first-time user's mental model is destroyed, not migrated
- Evidence: §9 migration path table; §3 "The user picks a project once. Every card sees it."
- Severity: minor (long-term retention risk)
- Falsification: produce the onboarding text or UI affordance that a returning vsnp_gui user sees on first launch after the redesign, explaining the new multi-card model without requiring them to read a design doc.
- Detail: The migration plan (§9) is entirely a developer migration — incremental code steps, no big bang. There is no corresponding user migration: a postdoc who ran vSNP last month and returns to find new "Run AMR" buttons, badge chips, and cross-card links will encounter an unexplained conceptual shift. Their prior mental model was linear: Step1 → Step2 → tree. The new model requires understanding primitives, cards, badge verdicts, shared project state, and cross-card navigation. The design treats this as out-of-scope, but for a GUI used infrequently (monthly panel runs), re-learning overhead is not amortized quickly.

---

## Recommendations

- **Name the three badge-absence states distinctly.** "Not applicable" (grey, lock icon), "Not run" (empty slot with a dashed border and "Run" affordance), and "Failed" (red with log-link) must be visually and semantically distinct. Codify these as three additional verdict values (`n/a`, `pending`, `fail`) alongside `pass/review/fail` in `Badge.verdict`, not as CSS classes layered on top after the fact.
- **Require `web()` to return a typed envelope.** Add a `kind` field to the web dict (`kind: "gene_table" | "taxonomy_tree" | "kmer_hit" | "text_summary"`) and define one React component per `kind`. This is the only way to avoid per-primitive frontend branches while still rendering structured output. The contract should enforce this: `web()` must return `{"kind": str, "data": dict}`, not a free-form dict.
- **Add a breadcrumb component to every card's header.** When a card is opened with `?project=X&sample=Y`, render "Project X / Sample Y — arrived from vSNP" with a button that opens the originating card's URL. This is one shared React component placed in the card header layout, costs one hour, and eliminates the one-way-door problem entirely.

---

## Where I'd be wrong

- **If badge density is never > 3 primitives per sample in practice.** The vSNP workflow for M. bovis or Brucella typically skips MLST, Kraken, and assembly-dependent tools; real panels may have 2-3 badges per row, not 5, which changes the visual math significantly.
- **If the GUI is always operated by a single named bioinformatician who owns the project.** In that case `samples.json` mutability and stale-result invalidation is a workflow problem the operator knows about, not a UX surprise. The critique of §5 weakens substantially if the user who enters metadata is the same person who runs all downstream tools in a single session.
- **If `web()` is already constrained by convention in the kraken_id_parse_gui codebase.** If the existing kraken primitive already returns a `{"kind": ..., "data": ...}` shaped dict that the kraken_gui renders generically, then the AMR primitive will follow that pattern by imitation, and the lack of contract enforcement in `common/contract.py` is a documentation gap rather than an architectural failure.
