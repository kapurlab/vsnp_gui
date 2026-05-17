# Methodology

A 3-round adversarial design review structured to (a) maximize independent attack, (b) cross-examine every claim, and (c) **preserve disagreement rather than collapse it to consensus**.

## Design goals

| Failure mode in typical reviews | How this process defends against it |
|---|---|
| Anchoring (reviewers converge on first-stated view) | R1 agents fire in parallel, no shared context |
| Politeness / hedging | "Hedging is a bug" + critique-only output schema; forbidden phrases enumerated |
| Consensus collapse in synthesis | R3 synthesizer's authority is binning, not arbitration; UNRESOLVED bin preserved verbatim |
| Surface-level bikeshedding | Each angle has an adversarial lead question forcing depth |
| Rubber-stamping | "Where I'd be wrong" section forces reviewers to write falsifiability conditions |
| Stress-test theater (looks rigorous, isn't) | Every R1 reviewer required to anchor ≥1 attack vector in a working-prototype falsification fixture |

## Round 1 — independent adversarial attack

- **8 agents fire in parallel.** None sees another's output; no shared scratchpad.
- **6 angles for technical/UX review, 3 personas for user perspective:**
  - A: software architecture
  - B: ops / deployment
  - C: bioinformatics workflow realism
  - D: failure-mode analysis (replaces the standard "code-review hygiene" — naming bikeshedding is low-leverage at the design stage; failure modes *are* the architecture)
  - E: UX / interaction design (Nielsen heuristics + Norman gulf-of-execution)
  - F1: lab user persona — grad student, MTBC user, mental model "I want a tree"
  - F2: lab user persona — postdoc, CLI-comfortable, wants to see commands
  - F3: lab user persona — senior researcher, handoff/reproducibility focus
- **Required reads** per agent: design doc + falsification fixture (`snp_analysis.py`). Some agents also had `CLAUDE.md`, `provenance_writer.py`, or `App.jsx`.
- **Output schema** (technical agents):
  - 3–7 **attack vectors**, each with: claim, evidence (doc § or file:line), severity (blocker/major/minor/tradeoff), falsification test, 2–4-sentence detail
  - 2–3 concrete recommendations
  - "Where I'd be wrong" — 1–3 conditions under which the critique is invalid
- **Output schema** (persona agents):
  - In-character friction log (timestamps, click-by-click for walkthrough personas; stream-of-consciousness for open-ended Dev)
  - Out-of-character reflection at the end
- **Hard length cap**: 1200 words for technical, 1500–1800 for personas.
- **Forbidden language** enumerated explicitly: "overall the design is sound," "with minor adjustments," "intuitive," etc.

## Round 2 — cross-examination

- **6 agents fire in parallel.** Each is given one (or two) R1 outputs to cross-examine, plus the original design doc and falsification fixture as reference.
- **Cross-angle pairings** chosen to maximize cognitive distance (architecture reviewing UX, ops reviewing failure+Tod, etc. — see matrix below).
- **Verdict per R1 attack vector**:
  - **RATIFY** — real problem; add a new angle-specific argument the R1 reviewer didn't make
  - **REBUT** — cite a fact the R1 reviewer missed; close the critique
  - **ESCALATE** — real and worse than R1 thought; describe the bigger version
  - **UNRESOLVED** — genuine disagreement; preserve both sides verbatim. *This is the escape hatch that captures disagreement instead of suppressing it.*
- **Anti-consensus instruction**: "Partial agreement" verdicts are forbidden — pick one. "Hedging is a bug. If you ratify, ratify hard. If you rebut, rebut hard."

### The steelman role

One R2 agent is given a special assignment: **defend the design** against the most-converged R1 findings (those appearing in 3+ independent angles). It is the only R2 role permitted to render **PARTIAL-REBUT** — "the critique is right but the recommended fix is heavier than necessary; here's the cheaper one."

If the steelman cannot construct a rebuttal after trying, the convergent finding is confirmed real with maximum confidence (the strongest available defender failed).

### R2 pairing matrix

| Reviewer angle | Reviews |
|---|---|
| Architecture | UX (E) + Lingling (F1) |
| Ops | Failure-modes (D) + Tod (F3) |
| Bioinformatics | Architecture (A) |
| Failure-modes | Bioinformatics (C) |
| UX | Ops (B) + Dev (F2) |
| Steelman | All 5 convergent findings across angles |

Every R1 output is reviewed by at least one R2 agent. The convergent findings are reviewed twice (once by their cross-angle partner, once by the steelman).

## Round 3 — disagreement extraction

- **1 synthesizer agent.** Reads all 8 R1 + 6 R2 outputs. Bins every R1 attack vector into one of:
  - **CONFIRMED-BLOCKER** (R2 ratified, pre-implementation fix required)
  - **CONFIRMED-MAJOR** (R2 ratified, address during implementation)
  - **NARROWED** (R1 right, steelman showed cheaper fix; surviving kernel documented)
  - **REFUTED** (R2 closed; reasoning preserved for audit)
  - **ESCALATED** (R2 found worse version; supersedes R1)
  - **UNRESOLVED** (R2 reviewers disagreed; both sides preserved verbatim)
  - **TRADEOFF** (severity:tradeoff in R1, no compelling override; decision but not a bug)
- **Authority is binning, not arbitration.** The synthesizer has no power to pick a winner among R2 reviewers. When R2 verdicts conflict → UNRESOLVED.
- **Self-check before writing**: count UNRESOLVEDs. If zero, re-read R2 looking for suppressed disagreement.

## Cost / wall-clock

- R1: 8 agents, ~3 min average duration each (parallel) — total ~3 min wall clock
- R2: 6 agents, ~2.5 min average — total ~3 min wall clock
- R3: 1 synthesizer, ~8 min
- **End-to-end: ~15 min wall-clock, 15 agent invocations.**

## Model choice

All agents run on Claude Sonnet 4.6. Rationale: structured critique benefits more from cognitive variance across independent heads than from deeper reasoning per head. Opus was considered for the steelman role but not used — the steelman's task is finding alternative framings, not reasoning depth.

## Known limitations

1. **Agent simulations of users are not users.** F1/F2/F3 personas produce *plausible* friction logs based on the design doc. Real friction requires real users clicking through real mocks. The personas should be treated as priors for human walkthroughs, not substitutes.
2. **Single-author design under review.** Anchoring effects in the *design doc itself* (vs. the review) are not addressed by this process. The design doc reflects one author's framing; reviewers attack within that framing.
3. **No counterfactual exploration.** The review attacks the proposed design; it does not propose alternative designs from scratch. If the entire `AnalysisPrimitive` framing is wrong (e.g., a DAG-based pipeline orchestrator would be better), the review does not surface it — the steelman defended the design as given.
4. **Reviewer briefs are themselves a bias surface.** The lead questions in each R1 prompt shape what reviewers look for. See [`BRIEFS.md`](BRIEFS.md) for the prompts and audit them yourself.
5. **One conversational session.** Multi-session deliberation (e.g., reviewers given 24h to think, return with revised critiques) was not done. Real-world design reviews often surface deeper issues on re-read.
