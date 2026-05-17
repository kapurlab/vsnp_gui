# Agent briefs

Included for audit: an independent reviewer can examine whether the briefs themselves biased the panel. Each agent was a fresh-context general-purpose Sonnet 4.6 instance with no memory of the parent conversation; its full instruction set is reproduced below.

## Common scaffolding (all agents)

All R1 and R2 prompts shared this structure:

- **Working directory** explicit (`/Users/vivekkapur/vsnp_gui`)
- **Required reads** enumerated — design doc, falsification fixture, prior R1 outputs as applicable
- **Adversarial framing**: "Your job is to break this. Hedging is a bug. If you find yourself agreeing, dig harder. You are NOT looking for things to like."
- **Forbidden language** explicitly enumerated: "overall the design is sound," "with minor adjustments," "intuitive," "edge case," "rare scenario"
- **Output schema** prescribed (claim / evidence / severity / falsification / detail)
- **Hard length cap** (1200–1800 words)
- **No memory disclaimer**: "You have no memory of any prior conversation — work only from the files you read."

The forbidden-language list is doing real work — it blocks the most common consensus-softening phrases agents fall back on under uncertainty.

---

## Round 1 — adversarial attack

### R1-A: software architecture

**Lead adversarial question**: "Does the existing `posthoc/snp_analysis.py` retrofit cleanly onto `AnalysisPrimitive`? If not, the contract is wrong — not the prototype. Show me concretely."

**Specific attack surfaces directed at the reviewer**:

- Six-method contract — right cut? What gets forced into the wrong shape?
- `PrimitiveResult` vs side-effects in `.run()` — retries, dry-runs, idempotency
- `Project` helper as god-object
- `applicable(sample_context)` — `SampleContext` schema deferred to §11
- Initial-home / extraction-deferral coupling cost
- `samples.json` schema versioning
- Contract evolution (§11 Q5 "ABC default no-op" plan)

**Required reads**: design doc, `snp_analysis.py`.

### R1-B: ops / deployment

**Lead adversarial question**: "What breaks on wgs3 specifically when this design hits real load — multiple users, multiple primitives racing on the same project, conda env corruption, OOD session timeouts?"

**Specific attack surfaces**:

- One-conda-env-per-tool storage cost, solve time, conflicts, upgrade strategy
- `run_in_conda_env` activation inside Apptainer `--pid` namespace
- "Apptainer swap = single-file change" claim
- `samples.json` advisory lockfile + stale-lock recovery
- `pip install -e` lifecycle for multi-user editable install
- DB versioning, monthly AMRFinder updates
- OOD session timeout + long-running primitive interaction
- `ensure_assembly` race between cards
- `.provenance/` directory quota / inode pressure
- "Deploy = git checkout on wgs3" model implications

**Required reads**: design doc, `snp_analysis.py`, `CLAUDE.md`.

### R1-C: bioinformatics workflow realism

**Lead adversarial question**: "Do real bioinformatics tools fit this contract, or does the contract paper over essential workflow complexity? Find the tools where the abstraction breaks."

**Specific attack surfaces**:

- Kraken+Bracken+Krona granularity (1 primitive or 3?)
- Sourmash sketch vs gather vs species ID
- AMRFinder organism-conditional `-O` handling
- `ensure_assembly()` cache key correctness across SPAdes/Shovill choices
- Multi-output tools, paired-vs-single-vs-long-read I/O
- DB versioning (AMRFinder monthly vs Kraken2 multi-GB)
- Schemaless `findings: list[dict]` field
- Post-step1 hook timing
- **Explicit**: snp_analysis.py operates on groups — how does sample-shaped contract handle that?

**Required reads**: design doc, `snp_analysis.py`.

### R1-D: failure-mode analysis

**Lead adversarial question**: "When things break — and they will — what state does the system end up in? What's recoverable? What's silently corrupted?"

**Twelve specific failure scenarios** enumerated in the prompt, including: partial primitive run, samples.json corruption, ensure_assembly race, conda env drift, provenance write failure (NaN in findings), cross-card navigation with missing target, OOD timeout mid-primitive, schema drift, `applicable()` lying about cached state, container-conda mismatch, exit-code dual-representation, failed-primitive badge-rendering.

**Forbidden language extension**: "edge case," "rare scenario," "unlikely in practice" — any failure mode is in scope.

**Required reads**: design doc, `snp_analysis.py`.

### R1-E: UX / interaction design

**Lead adversarial framing**: Nielsen heuristics + Norman gulf-of-execution.

**Specific attack surfaces**:

- Cross-card navigation (back-button, bookmarks, dual tabs, OOD session expiry)
- "One project, every card sees it" claim across session lifecycles
- Badge density at 16 samples × 5 primitives
- Greyed-out primitives — does user see *why*?
- Error surfaces (`PrimitiveError`, `PrimitiveSetupError`)
- Discoverability of per-sample "Run X" buttons
- `.web()` returning untyped dict
- Badge ordering (§11.4 hand-waved)
- `.applicable()` user-visibility
- CLI escape hatch maintenance cost
- `samples.json` user-mutable surface workflow
- First-time-user mental model migration

**Required reads**: design doc, `snp_analysis.py`, optional `App.jsx`.

### R1-F1: Lingling persona (walkthrough)

**Persona**: grad student, used vsnp_gui twice on MTBC, never seen kraken_gui or AMR, mental model "I upload fastqs, I get a tree."

**Task**: advisor Slack message — "for your 16 MTBC samples, also check whether they carry beta-lactam resistance genes. Use the new pipelines GUI. Should be quick." 20-minute time budget.

**Output format**: click-by-click friction log with timestamps, annotated for confusion / hesitation / misinterpretation / dead-end / surprise / success. Required to flag "Doc-gap: I can't tell from the doc what X looks like" when design is too vague to imagine the UI.

**Out-of-character reflection** required at the end.

**Forbidden**: breaking character mid-walkthrough; pretending Lingling already understands software concepts she wouldn't; claiming success on tasks she couldn't actually complete given the doc's stated UI.

### R1-F2: Dev persona (open-ended exploration)

**Persona**: postdoc, CLI-comfortable, suspicious of GUIs, wants to see commands.

**Task**: free-roam evaluation. Has been told "use the new pipelines GUI for your next batch of NAHLN AMR samples." 45 min to form an opinion.

**Eight specific things Dev WILL try to figure out** enumerated: CLI parity, command transparency, missing-env behavior, samples.json as source of truth, crash recovery, provenance adequacy, "single-file Apptainer swap" claim, `snp_analysis.py` future.

**Output**: stream-of-consciousness narration, not click-by-click. Verdict at the end.

### R1-F3: Tod persona (walkthrough — handoff scenario)

**Persona**: senior researcher, 20+ years veterinary genomics, publications + regulatory submissions + legal proceedings, trained on USDA-VS tooling.

**Task**: hand off project `Brucella_2025_outbreak` (47 samples, 3 months of work) to a collaborator at another USDA lab. Package: results + provenance + reproducibility + raw outputs. 45-minute time budget.

**Seven specific questions Tod WILL try to answer** enumerated, including the **explicit regression check**: compare §6 provenance schema against T-07's existing `provenance_writer.py` (cited by file path so Tod can read it directly).

**Forbidden**: making Tod easily-pleased; making him a generic reproducibility-pedant; ignoring the regression-vs-existing-T-07 question.

---

## Round 2 — cross-examination

All R2 agents shared this scaffolding:

- Required reads include the R1 file(s) being reviewed + design doc + relevant source files
- Verdict per R1 attack vector: RATIFY / REBUT / ESCALATE / UNRESOLVED
- **Anti-consensus**: "Partial agreement verdicts are forbidden. Pick one. UNRESOLVED is for genuine tradeoffs, not 'depends.'"
- Output schema enforced (verdict / argument / evidence)
- "Net delta" section: what survived, what failed, what escalated, what's unresolved

### R2-architecture reviews R1-ux + R1-user-lingling

Architectural cross-exam: does the contract design produce these UX problems? Architecture facts (UI gating via `applicable()`, JobManager constraints, URL-scheme limitations) used to verdict UX claims.

### R2-ops reviews R1-failure-modes + R1-user-tod

Two specific instructions:
- For Tod's T-07 regression claim: **empirically verify against `provenance_writer.py`** — verdict must be evidence-based, not opinion-based.
- For failure-modes findings on samples.json locking / ensure_assembly race / OOD timeout: don't soft-pedal because they're "obvious" — render the verdict.

### R2-bioinformatics reviews R1-architecture

Bio-domain cross-exam of architecture claims. Particularly:
- Sample-shaped vs panel-shaped — read `snp_analysis.py` empirically; verify signatures.
- `applicable()` filesystem I/O — would real-tool behavior require runtime filesystem checks, or can samples.json-only context suffice?
- `ensure_assembly()` race — theoretical or practical?
- `PrimitiveResult` vs `write_provenance()` side-effect — domain-realistic or architectural-purity?

### R2-failure-modes reviews R1-bioinformatics

Reliability cross-exam of bio claims. For each bio concern, ask: what state does the system end up in when this fires? Specific attention: Kraken+Bracken+Krona partial failure, `ensure_assembly` wrong-assembler propagation, `-O` runtime resolution silent wrong-answer, schemaless findings TSV-rename failure mode, NaN-fill propagation in `snp_analysis.py`.

### R2-ux reviews R1-ops + R1-user-dev

UX cross-exam of ops/dev claims: which are user-visible vs purely backend?

Specific user-facing manifestations to assess:
- Nine conda envs / disk full → tool error
- `pip install -e` mid-write → user sees what?
- OOD timeout orphans SPAdes → user perceives confident wrong badge
- AMRFinder DB version drift → same input → different output, does user have signal?
- Dev's hardcoded `provenance()` command stub → does user ever see provenance?
- Dev's missing-env spinner → indefinite hang UX

### R2-steelman on convergent findings

Special role. Five convergent findings (3+ R1 angle agreement) enumerated explicitly:

1. Sample-shaped contract vs panel-shaped tools
2. `ensure_assembly()` race
3. `samples.json` corruption / stale-lock
4. `applicable()` can't express runtime state
5. Schemaless findings / `web() → dict[str, Any]`

Each presented with a "steelman direction" — a starting argument the steelman might develop. The steelman is the only R2 role permitted **PARTIAL-REBUT**: "the critique is right but the recommended fix is heavier than necessary; here's the cheaper one."

Length cap raised to 1800 words. Instruction: "Take the time to argue hard for the design — that's the point of this role."

---

## Round 3 — synthesis

Single agent. Reads all 8 R1 + 6 R2 outputs + design doc + falsification fixture + `provenance_writer.py`.

**Authority**: binning, not arbitration. Critical instruction repeated multiple times:

> "Where you see disagreement — bin as UNRESOLVED. Preserve all sides verbatim. Do not pick a winner."

Three specific UNRESOLVED candidates flagged by name in the prompt to prevent the synthesizer from suppressing them:

1. `ensure_assembly()` race (5-way R2 split — listed all five positions in the prompt)
2. Sample vs panel contract (note: ended up NARROWED, not UNRESOLVED)
3. §6 provenance vs T-07 (note: confirmed empirically by R2-ops)

**Bin definitions** prescribed:

- CONFIRMED-BLOCKER / CONFIRMED-MAJOR / NARROWED / REFUTED / ESCALATED / UNRESOLVED / TRADEOFF

**Self-check before writing**: count R1 attack vectors; count UNRESOLVEDs. "If the count of UNRESOLVEDs is zero, you probably suppressed disagreement. Re-read the R2 files."

**Length**: 2500–3500 words, "length should follow the data."

**Hard rules**:

- Every R1 attack vector must appear somewhere in the output
- Do NOT soften disagreement
- Quote VERBATIM in UNRESOLVED
- Cite specifically — doc § or file:line
- "If you find yourself wanting to insert your own opinion, stop. You're the binner, not the judge."

---

## Reviewer-bias self-check

For an independent auditor: things to scrutinize about these briefs themselves.

1. **The lead questions in R1 are leading.** Architecture's "Does the existing prototype fit the contract?" pre-commits the reviewer to the falsification fixture's relevance. A reviewer who thought the prototype was irrelevant would have a harder time saying so.
2. **The "forbidden language" list is a values choice.** It bans softening but also bans some legitimate hedges. A reviewer who genuinely thought one critique was minor would have to escalate it to "major" or skip it.
3. **The personas (F1/F2/F3) are constructed.** Lingling's "mental model: I upload fastqs, I get a tree" is the design team's projection of a grad student, not an empirical user study.
4. **The steelman role is biased toward defense.** It's instructed to "argue hard for the design" — which is the point, but means convergent findings that *don't* survive the steelman are extra-confirmed, while findings the steelman defends successfully may be under-credited.
5. **The synthesizer's UNRESOLVED bias is asymmetric.** The prompt repeatedly emphasizes preserving disagreement; less emphasis on confirming when disagreement is fake or manufactured.

These biases were intentional (anti-consensus design) but you should know they're there.
