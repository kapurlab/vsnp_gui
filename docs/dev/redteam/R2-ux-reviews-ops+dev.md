# R2 — UX cross-examination of Ops (R1) + Dev (R1)

---

## On R1-ops

### OPS-AV1: `run_in_conda_env` activation will silently fail inside Apptainer
- Verdict: RATIFY
- User-facing manifestation: The user clicks "Run AMR" on a sample; the spinner runs briefly; the badge renders as `verdict=fail` with no message, or the job card disappears with no status.
- Argument: The design surfaces `exit_code` in the badge layer, but it does not mandate that `PrimitiveSetupError` vs. a genuine tool failure vs. an activation failure produce distinguishable badges. The user has no signal that the failure is environmental (env not found, conda not on PATH) rather than scientific (AMRFinder found no genes, tool crash on malformed FASTA). From the user's position — a post-doc who just waited for the job — a silent `fail` badge is indistinguishable from a zero-gene result unless the badge `detail` field is populated with the actual error, which the sketch does not guarantee for exit_code=127.

### OPS-AV2: `ensure_assembly` race condition double-runs SPAdes
- Verdict: REBUT
- User-facing manifestation: Corrupt FASTA or SPAdes crash — but this requires two concurrent OOD sessions opening the same project+sample within 30 seconds, which the single-authenticated-user model makes unlikely today.
- Argument: The ops concern is technically valid. However, wgs3 is a single-lab installation where concurrent users opening the same sample simultaneously is not a realistic near-term scenario. The user-facing outcome (corrupt assembly) is severe if it fires, but the prior is low enough that this does not rise to user-facing P0. This deserves a code fix (fcntl.flock) but not a UX escalation. Rebut for UX priority; keep as backend P2.

### OPS-AV3: `samples.json` stale lockfile freezes project permanently
- Verdict: ESCALATE
- User-facing manifestation: After a session is killed mid-write (OOD timeout, `fuser -k`), the next time the user opens that project in the GUI, every "Run" button on every sample silently blocks or errors — with no indication of which file is locked or how to recover. The project appears open but is functionally dead.
- Argument: R1-ops called this "major." From a UX lens it is worse: the lockfile failure mode is asymmetric — it is silent on the surface (the GUI opens, the project loads, the buttons are visible), and the block only reveals itself when the user attempts a new run. There is no recovery path documented. The user's only signal is a spinner that never resolves, or at best an opaque 500 from the API. This is a UX deadlock, not just an ops failure. The fact that the existing `write_stats()` in `posthoc/snp_analysis.py` has no locking at all makes this a present-tense risk, not a hypothetical.

### OPS-AV4: Nine conda envs is a disk quota landmine
- Verdict: RATIFY
- User-facing manifestation: During a "Run AMR" or assembly job, the process exits with a cryptic error (disk full mid-install), leaving the env partially constructed; subsequent runs fail silently or with an unrelated tool error, and the user has no actionable message.
- Argument: The user-facing injury is not the disk consumption itself — users never see quota numbers — but the failure mode of a partial conda env installation. A partial env produces errors that look like tool bugs, not infrastructure problems. A researcher who sees "amrfinder: command not found" during an AMR run will not think "the conda install ran out of disk space during the last mamba create"; they will think AMRFinder is misconfigured. The ops reviewer was right about the technical risk; the UX reviewer adds that the failure message will be maximally misleading.

### OPS-AV5: `pip install -e` breaks under concurrent sessions
- Verdict: REBUT
- User-facing manifestation: In theory, two concurrent users could run incompatible versions of `record_finding` against the same `samples.json`. In practice, this requires simultaneous active OOD sessions and a git pull during the overlap window.
- Argument: The failure mode is real but the user-facing manifestation requires a sequence of events (concurrent sessions + live deploy) that is not the normal workflow for a single-lab installation. The ops recommendation (versioned wheel) is architecturally sound but the urgency is low until there are multi-user deployments. Rebut for UX priority.

### OPS-AV6: OOD timeout orphans SPAdes, leaving partial assembly silently reused
- Verdict: ESCALATE
- User-facing manifestation: The user submits an AMR run. The OOD session times out during SPAdes. The next time the user opens the project, `ensure_assembly` finds the partial FASTA, passes it to AMRFinder, and the AMR badge renders `AMR: 0 genes` or `AMR: 1 gene` — a plausible-looking scientific result that is wrong because the input assembly was truncated.
- Argument: R1-ops identified the ops-level consequence (provenance `exit_code` sentinel never written). The UX consequence is worse: the user receives a confident-looking badge with a scientific verdict, not an error state. `verify_provenance.py` will flag the audit failure, but that script is not run in normal operation and its output is not surfaced in the GUI. The user has no signal that the result is corrupt. This is the most dangerous UX failure mode in the entire design: a wrong answer that looks like a right answer.

### OPS-AV7: AMRFinder DB version unpinned; same input yields different output across runs
- Verdict: RATIFY
- User-facing manifestation: A researcher runs AMR on the Shivasharanappa panel in May, then again in September after someone ran `amrfinder --update`; `mecA1` identity threshold shifts; the badge changes from `MRSA-like` to `AMR: 2 genes`; the researcher notices the discrepancy with no explanation.
- Argument: The provenance record *does* capture `db_version`, which is the correct signal. But the user has to actively compare provenance JSON files across two runs to notice the change — the GUI does not surface a "DB version changed since last run" warning. The design's reproducibility story is correct in principle (record the version) but incomplete in practice (never alert the user to a version change). For NAHLN surveillance contexts where the same panel is re-run for QC, this is a real workflow hazard.

---

## On R1-user-dev

### DEV-F1: `__main__` block not shown; provenance may not fire on CLI runs
- Verdict: REBUT
- User-facing manifestation: The CLI user gets results but no provenance — invisible to them at runtime; they discover it only when auditing later.
- Argument: This is a developer-experience gap, not a user-facing UX failure during normal operation. The GUI path calls `.run()` which calls `write_provenance()` explicitly. CLI provenance is a reproducibility concern for the postdoc persona, not a visible failure in the GUI. Rebut as backend/dev gap, not a UX escalation.

### DEV-F2: `provenance()["command"]` is a hardcoded stub; recorded command diverges from actual argv
- Verdict: ESCALATE
- User-facing manifestation: A researcher opens the provenance panel for an AMR run and sees `amrfinder -n ... --plus` — the `...` placeholder is literally in the UI (if the GUI ever renders `command.sh`) — and cannot reconstruct which FASTA path, thread count, or DB flags were actually used.
- Argument: Dev correctly identified this as a real bug. The UX escalation is that provenance is one of the two features the design got most right (Dev's own verdict). If the provenance record is visibly broken — literal `...` in the command field — it undermines user trust in the entire provenance system, not just this one field. Worse, if the GUI surfaces provenance via a "Show details" drawer, researchers will copy this stub command and attempt to re-run it, failing immediately. The fix is two lines of Python; the user-trust cost of not fixing it is disproportionate.

### DEV-F3: `samples.json` race condition on concurrent primitive writes
- Verdict: RATIFY
- User-facing manifestation: User runs AMR and Kraken simultaneously on the same project; one card's findings are silently dropped from `samples.json` because the other card's write won the race; the missing badge appears absent with no error, as if the run never completed.
- Argument: This overlaps with OPS-AV3 but is narrower: concurrent primitives within a single session, not a lockfile stale from a killed process. The user experience is a missing badge after a successful run — the user re-runs, the badge appears, the user does not know a race occurred. Low visibility, non-trivial frequency once multi-primitive runs are the normal workflow.

### DEV-F4: Missing env error surface — silent hang vs. actionable error
- Verdict: ESCALATE
- User-facing manifestation: User clicks "Run AMR"; the job spinner runs for 30 minutes (or indefinitely); there is no timeout badge, no "env not found" message, and no way to cancel from the GUI without killing the OOD session.
- Argument: Dev correctly flagged this as the most user-hostile failure mode. The UX escalation over Dev's framing is the absence of a cancel mechanism: once a primitive is dispatched, the GUI has no documented way to interrupt it or surface a timeout. Open question 6 in the design (`PrimitiveSetupError`) must be closed before any real batch run — but because it is listed only as an open question, it is at risk of being deferred past the first production use. A missing-env failure today produces an uncaught exception (behavior undefined), which in a FastAPI context likely returns a 500 with a stack trace in the logs and a spinning badge in the UI.

### DEV-F5: No `ensure_amr()` equivalent; re-run overwrites successful outputs
- Verdict: REBUT
- User-facing manifestation: Re-running AMR after a partial failure silently overwrites completed samples' outputs.
- Argument: For AMRFinder (5 seconds/genome) this is harmless. The ops reviewer correctly notes that `ensure_assembly` handles the expensive case. The gap exists but its user-facing cost is a few seconds of redundant computation, not lost data. Rebut for UX priority.

### DEV-F6: Provenance schema adequate for surveillance, not publication-grade
- Verdict: REBUT
- User-facing manifestation: None — the missing `conda list --export` snapshot is invisible to the user at runtime and only matters for future reproducibility audits by external parties.
- Argument: Dev's verdict is correct ("good enough for this lab's use case"). Not a UX issue.

### DEV-F7: Apptainer bind-mount problem is more than a single-file change
- Verdict: REBUT
- User-facing manifestation: Future migration concern; no user-facing manifestation today.
- Argument: The claim is directionally correct but the impact is deferred until Apptainer migration is attempted. Rebut for current UX priority.

### DEV-F8: `posthoc/snp_analysis.py` unmentioned; writes non-T-07 provenance format
- Verdict: RATIFY
- User-facing manifestation: Posthoc analysis runs and writes `stats.json` in its own schema; when the GUI eventually surfaces provenance or badges for posthoc results, it reads a different shape than every other primitive's `record.json`, causing either a render error or silently missing fields in the provenance UI.
- Argument: This is not a hypothetical future problem: `posthoc/snp_analysis.py` exists today, is already in production use, and already diverges from the T-07 schema the design mandates. If the GUI adds a provenance drawer that reads `record.json` uniformly across all tools, posthoc will either break that drawer or silently show empty fields. The design's stated goal — eliminate format drift — has already failed for the one substantial analysis module already in the repo.

---

## Net delta

- **Backend-only concerns (REBUT'd as ops-only):**
  - OPS-AV2: `ensure_assembly` race (single-user installation, low prior)
  - OPS-AV5: `pip install -e` concurrent sessions (requires multi-user + live deploy overlap)
  - DEV-F1: CLI provenance gap (invisible at runtime)
  - DEV-F5: Re-run overwrites completed samples (AMRFinder cost negligible)
  - DEV-F6: Provenance not publication-grade
  - DEV-F7: Apptainer single-file claim oversimplified (deferred risk)

- **Concerns that translate to user-facing problems (RATIFY'd):**
  - OPS-AV1: Silent `fail` badge with no env-vs-science distinction
  - OPS-AV4: Partial conda env produces maximally misleading tool error
  - OPS-AV7: DB version change produces silent result divergence across runs
  - DEV-F3: Concurrent primitive writes silently drop findings
  - DEV-F8: `posthoc/snp_analysis.py` provenance schema drift breaks future provenance UI

- **Escalations (worse user impact than R1 reviewer noted):**
  - OPS-AV3 → ESCALATE: Stale lockfile is a silent GUI deadlock, not just an ops failure; project appears open but all run buttons block invisibly
  - OPS-AV6 → ESCALATE: Orphaned SPAdes produces a plausible-looking wrong badge — wrong answer that looks like right answer; no user signal whatsoever
  - DEV-F2 → ESCALATE: Hardcoded `...` stub in provenance command field is a trust-destroying visible artifact if GUI ever renders `command.sh`; researchers will try to copy-paste and re-run it
  - DEV-F4 → ESCALATE: Missing-env produces indefinite spinner with no cancel path; worse than Dev described because there is no documented interrupt mechanism in the GUI

- **Unresolved:**
  - None. Every attack vector received a verdict. The two vectors with genuine uncertainty (OPS-AV1, OPS-AV6) were ratified/escalated on the available evidence, with the caveat that the actual `runners.py` implementation (not yet written) is the deciding artifact.
