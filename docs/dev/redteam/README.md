# PIPELINES_PACKAGE design review — archive

Self-contained archive of a 3-round adversarial red-team review of [`sources/PIPELINES_PACKAGE.md`](sources/PIPELINES_PACKAGE.md), a design doc for a shared analysis-primitives package proposed for the kapurlab WGS pipelines GUI.

**Conducted**: 2026-05-16
**Conducted by**: 15 fresh-context Claude Sonnet 4.6 agents under a structured adversarial protocol (8 R1 + 6 R2 + 1 R3 synthesis)
**Total wall-clock**: ~15 min
**Purpose**: pre-implementation review before T-27 (`pipelines/common/`) begins

## Reading order

For a first-time reviewer wanting the bottom line:

1. **[`CONTEXT.md`](CONTEXT.md)** — what was reviewed and why (10 min)
2. **[`FINDINGS.md`](FINDINGS.md)** — Round 3 synthesis, the punch list (15 min)
3. **[`DECISIONS.md`](DECISIONS.md)** — the two UNRESOLVED items, both sides preserved verbatim — *the calls you need to make* (10 min)

For a reviewer wanting to audit the methodology:

4. **[`PROCESS.md`](PROCESS.md)** — round structure, anti-anchoring, disagreement-capture design
5. **[`BRIEFS.md`](BRIEFS.md)** — the actual prompts given to each agent, with reviewer-bias self-check

For a reviewer wanting the raw evidence:

6. **R1-*.md** (8 files) — independent adversarial attacks, one per angle
7. **R2-*.md** (6 files) — cross-examinations
8. **sources/** — the artifacts the reviewers were given (frozen as of review time)

## What you're being asked to do

If you're reviewing this independently, the deliverables we'd value:

- **Sanity-check the methodology.** Does the round structure and verdict schema produce useful disagreement, or does it have a hidden bias?
- **Adjudicate the UNRESOLVED items.** Both sides are preserved verbatim in [`DECISIONS.md`](DECISIONS.md). Your read on which side is more correct.
- **Stress-test the blockers.** Are any of the 12 CONFIRMED-BLOCKERs wrong? Are any of the 10 REFUTEDs wrongly closed?
- **Spot omitted angles.** What dimension of the design did we not attack?
- **Verify the empirical claims.** Several findings reference specific file lines in `sources/`. Confirm they say what we say they say.

## Bottom line, in three sentences

- **54 attack vectors across 8 R1 angles; 34 survived R2 cross-examination; 10 were refuted by R2; 2 ended UNRESOLVED.**
- **12 confirmed blockers** must be addressed before `pipelines/common/` implementation begins; the most dangerous are silent false-negative pathways (BLOCKER-5 AMR badge, BLOCKER-1 SPAdes orphan, BLOCKER-11 NaN-fill).
- **2 unresolved decisions** require human judgment: (a) `ensure_assembly()` locking posture, (b) provenance schema scope vs existing T-07. Both have coherent positions on each side.

## File map

| File | What it contains |
|---|---|
| [`README.md`](README.md) | This file |
| [`CONTEXT.md`](CONTEXT.md) | What was reviewed, why, project background, falsification fixture |
| [`PROCESS.md`](PROCESS.md) | 3-round methodology, anti-anchoring, disagreement-capture, known limitations |
| [`BRIEFS.md`](BRIEFS.md) | Agent prompts (audit trail) + reviewer-bias self-check |
| [`FINDINGS.md`](FINDINGS.md) | Round 3 synthesis — 7 bins (blocker/major/narrowed/refuted/escalated/unresolved/tradeoff) |
| [`DECISIONS.md`](DECISIONS.md) | The 2 UNRESOLVED items extracted with both sides verbatim + T-27 scope-freeze guidance |
| `R1-architecture.md` | R1: software-architecture adversarial attack (6 vectors) |
| `R1-ops.md` | R1: ops/deployment attack (7 vectors) |
| `R1-bioinformatics.md` | R1: bioinformatics workflow realism (7 vectors) |
| `R1-failure-modes.md` | R1: failure-mode enumeration (7 vectors) |
| `R1-ux.md` | R1: UX / interaction design (7 vectors) |
| `R1-user-lingling.md` | R1: grad-student persona walkthrough (16-sample MTBC + AMR task) |
| `R1-user-dev.md` | R1: CLI-postdoc persona open-ended exploration |
| `R1-user-tod.md` | R1: senior-researcher persona handoff scenario (47-sample Brucella) |
| `R2-arch-reviews-ux.md` | R2: architecture cross-exam of UX + Lingling |
| `R2-ops-reviews-failure+tod.md` | R2: ops cross-exam of failure-modes + Tod (incl. T-07 regression check) |
| `R2-bio-reviews-arch.md` | R2: bioinformatics cross-exam of architecture |
| `R2-failure-reviews-bio.md` | R2: failure-modes cross-exam of bioinformatics |
| `R2-ux-reviews-ops+dev.md` | R2: UX cross-exam of ops + Dev |
| `R2-steelman-on-convergent.md` | R2: steelman defense of convergent (3+ angle) findings |
| `sources/PIPELINES_PACKAGE.md` | The design document under review |
| `sources/snp_analysis.py` | Falsification fixture — working primitive that predates the contract |
| `sources/provenance_writer.py` | Existing T-07 implementation, referenced for §6 regression analysis |
| `sources/CLAUDE.md` | Repo-level dev guide, deployment-model context |

## Tarball

To package for off-system review:

```bash
cd /Users/vivekkapur/vsnp_gui/docs/dev
tar -czf pipelines-redteam-2026-05-16.tar.gz redteam/
```

Result: ~200 KB tarball, fully self-contained. No external dependencies; all source artifacts the reviewers saw are in `sources/`.

## Citing this review

If referenced in a ticket or commit message:

> See `docs/dev/redteam/FINDINGS.md` for the pre-implementation review of `PIPELINES_PACKAGE.md` (3-round adversarial, 2026-05-16).
