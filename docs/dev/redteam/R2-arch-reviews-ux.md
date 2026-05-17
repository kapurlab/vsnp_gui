# R2 — Architecture cross-examination of UX (R1) + Lingling (R1)

---

## On R1-ux

### UX-AV1: Cross-card navigation produces a one-way door with no breadcrumb or back-link
- Verdict: ESCALATE
- Argument: The R1 critique frames this as a UX signposting problem. The architectural version is worse: the `?project=X&sample=Y` URL parameter scheme has no durable identity for the originating card. There is no "card identifier" in the URL, so no breadcrumb component can link back to the correct originating OOD session — OOD sessions are ephemeral, port-bound objects, not addressable by project name. Even if a breadcrumb renders "arrived from vSNP," clicking it cannot reopen the correct session; it can only navigate to the OOD dashboard, losing all scroll state and selected sample. The problem is not a missing UI component; it is that the cross-card URL scheme lacks a `source_card` and `source_session_id` parameter that would make bidirectional navigation representable.
- Evidence: PIPELINES_PACKAGE.md §3 rule 5; CLAUDE.md "OOD session execution model" (sessions are port-allocated at launch, no stable URL identity)

### UX-AV2: Badge density crosses the threshold of meaningful at realistic panel sizes
- Verdict: REBUT
- Argument: The R1 reviewer's "80 badge chips on one page" scenario requires five primitives to have been run on all 16 samples — a condition the design explicitly does not create. `applicable()` gates which badges render (§4), and the PIPELINES_PACKAGE.md §3 card table shows vSNP as the only card that unconditionally runs; AMR requires assembly, MLST requires an organism with a scheme, Kraken requires FASTQ. For a typical MTBC panel (the R1-lingling target organism), MLST has no *M. tuberculosis* complex scheme and AMR in generic mode is of marginal utility — realistically 2-3 badge columns render, not 5. The color-blind accessibility concern about `CLASS_COLORS` (snp_analysis.py is the wrong file; CLASS_COLORS is at amrfinder.py:309-317) is valid but scoped to the AMR detail view, not the badge-chip layer, where only verdict (`pass/review/fail`) drives color.
- Evidence: PIPELINES_PACKAGE.md §4 `applicable()` classmethod; §3 card table showing conditional applicability; R1 self-rebuttal ("badges never > 3 primitives in practice")

### UX-AV3: Greyed-out primitives are silent about the reason for their silence
- Verdict: RATIFY
- Argument: The R1 reviewer frames this as a Norman gulf-of-evaluation problem. The stronger architectural argument is that `applicable()` returning `False` is a **design-time classification** baked into a classmethod, but the GUI has no way to distinguish it from a **runtime classification** (tool not found, DB missing) because both produce the same absence of a `PrimitiveResult` in `samples.json`. The contract in `common/contract.py` has no machine-readable reason code for non-applicability. Distinguishing "not applicable" from "not run" from "failed" requires querying three different sources (the classmethod, the samples.json key existence, the provenance exit_code sentinel) — and the design specifies no API endpoint that aggregates these into a single sample status response. The UI cannot render three distinct states without a new API contract.
- Evidence: PIPELINES_PACKAGE.md §4 `applicable()` contract, §11.6 open question on error surface; R1-ux §3 recommendation

### UX-AV4: `web()` returns an untyped dict; each card must re-implement per-primitive rendering
- Verdict: RATIFY
- Argument: The R1 reviewer identifies the absence of a `kind` discriminator. The sharper architectural point: the design claims "no per-tool special-casing in the GUI" (§8) while simultaneously citing the kraken_id_parse_gui's existing report pattern as a model (§4). The existing kraken codebase's `.latex()` and `.excel()` methods work because LaTeX and Excel have well-defined cell/section primitives — the caller does not need to know the tool's output shape, only where to write. `.web()` inverts this: the caller (React) must interpret arbitrary JSON, which means a React component tree that is either generic-but-useless (raw JSON dump) or per-primitive-and-duplicated. The contract as written cannot achieve the stated goal; the fix (a `kind` envelope) is simple but must be in `common/contract.py` as an enforced field, not a convention. The R1 reviewer called this a documentation gap; it is a contract gap.
- Evidence: PIPELINES_PACKAGE.md §4, §8 sketch; amrfinder.py lines 384-393 (`web()` returns free-form dict with no `kind` field)

### UX-AV5: `samples.json` is a mutable shared file with no locking, no edit UI, and no recovery
- Verdict: ESCALATE
- Argument: The R1 reviewer focuses on the UX of correcting a wrong organism field. The larger architectural failure is that `samples.json` serves two incompatible roles: it is both a **metadata store** (user-supplied `organism`, `host`, `isolation_source`) and an **accumulator of computed findings** (`step1`, `kraken`, `amr` keys). These have different write patterns, different staleness semantics, and different authority: metadata is user-authoritative, findings are tool-authoritative. Writing both into the same flat JSON under the same sample key means that `Project.record_finding()` and a hypothetical metadata edit operate on the same file with no coordination. When a user corrects `organism`, there is no invalidation sweep to recompute `applicable()` for already-persisted findings. The design would need either (a) separate metadata.json and findings.json files with explicit linking, or (b) a version counter on the metadata block that primitives record at run time so staleness is detectable. Neither is present.
- Evidence: PIPELINES_PACKAGE.md §3 `samples.json` description and `Project.record_finding()` method; §11 open questions list (does not mention metadata-findings coupling)

### UX-AV6: Error surfaces collapse all failure modes into a single red badge
- Verdict: RATIFY
- Argument: The R1 reviewer notes the badge `detail` field is unspecified for error cases. The additional architectural point: `PrimitiveResult` stores `exit_code` and `log_path`, but neither is exposed in the `/api/projects/{project}/samples/{sample}/badges` endpoint sketched in §8 — the endpoint returns `[p.badge().__dict__ for p in load_completed_primitives(...)]`, which surfaces only `label`, `verdict`, `detail`, and `icon`. The log path is a server-side filesystem path that cannot be served as a browser link without a separate log-streaming endpoint. The design has the data to distinguish failure modes but no route from the data layer to the user. This is not a missing convention; it is a missing API surface.
- Evidence: PIPELINES_PACKAGE.md §4 `PrimitiveResult`, §8 badges endpoint sketch; amrfinder.py lines 370-381 (`exit_code` and `log_path` in result, never forwarded to badge)

### UX-AV7: First-time user mental model is destroyed, not migrated
- Verdict: REBUT
- Argument: The R1 reviewer categorizes this as a retention risk. For a research lab GUI with a bioinformatician PI as the primary operator, the realistic onboarding path is a 5-minute demo from the PI — not a GUI wizard. The design's migration path (§9) is incremental: Step 2 adds one "Run AMR" button on an existing vsnp_gui page the user already knows. Users encounter the new primitive in the context they already have (vSNP step1 result), not by discovering a new card on the dashboard. The card proliferation scenario (four unfamiliar cards on the OOD dashboard) only materializes at migration Step 6 (kraken_gui OOD re-deploy), which is an estimated ~3 hours of effort explicitly marked as later. Treating a Step 6 concern as a launch blocker misprioritizes.
- Evidence: PIPELINES_PACKAGE.md §9 migration path Step 2 ("Run AMR button on step1 result pages"); §9 Step 6 ("Re-deploy kraken_gui as OOD batch_connect" — later step)

---

## On R1-user-lingling

### LL-F1: No clear entry point — which card do I start in?
- Verdict: RATIFY
- Argument: Lingling's confusion ("Is my project in the vSNP card or in the AMR card?") is a direct consequence of the design having no **home card** concept. The OOD dashboard shows peer cards with no hierarchy. The architectural fix is a designated entry-point card that owns project selection and dispatches to analysis cards — analogous to a project index page. Without this, every card must implement its own project selector for the cold-open (no `?project=X`) case, duplicating UI and leaving users without a canonical starting point. The design's Rule 5 ("Cards launch sister cards with context") only works if the user is already inside a card; it does not address the dashboard-entry problem.
- Evidence: PIPELINES_PACKAGE.md §3 rule 5; R1-lingling T+0:00 and T+0:45

### LL-F2: AMR card has no described UI for the cold-open (no URL parameter) case
- Verdict: RATIFY
- Argument: The design specifies what the AMR card receives (`?project=X&sample=Y`) but not what it renders when opened without parameters — a genuine spec gap. Architecturally, a card that is navigable from the OOD dashboard must handle the cold-open case or it will expose a blank/broken state to any user who opens it directly. The `GET /open?project=...&sample=...` route described in §3 implies a redirect flow, but no route handles the case where neither parameter is present. This needs a fallback to a project-picker UI, which the design does not spec.
- Evidence: PIPELINES_PACKAGE.md §3 rule 5 ("one route handler per card, GET /open?project=...&sample=...")

### LL-F3: No bulk run action — 16 samples requires 16 per-sample triggers
- Verdict: ESCALATE
- Argument: Lingling identifies no bulk run as the single biggest friction point, and she is correct. The architectural escalation: the per-sample URL scheme (`?project=X&sample=Y`) implies a per-sample subprocess launch, which means a bulk run requires either (a) a new batch endpoint that iterates samples server-side, or (b) 16 sequential OOD card opens. Neither is described. Option (a) is the right answer but conflicts with the design's stated primitive execution model (primitives are instantiated per-sample, not per-project). A `POST /api/projects/{project}/run-primitive` endpoint taking a list of sample IDs would fix this, but it is architecturally distinct from the single-sample `GET /open?project=X&sample=Y` pattern and requires the backend to manage concurrent subprocess lifecycles — which the existing `JobManager` (jobs.py) does for vSNP step1 but is not described as extended to arbitrary primitives.
- Evidence: PIPELINES_PACKAGE.md §3 rule 5; CLAUDE.md `backend/app/jobs.py` (JobManager exists for vSNP, not generalized)

### LL-F4: Badge says "AMR: none / pass" but does not answer the advisor's beta-lactam question
- Verdict: RATIFY
- Argument: This is a clinical communication failure with an architectural root cause: the badge contract encodes `verdict` as a pipeline-quality signal (`pass/review/fail`) but is being asked to serve as a **biological question-answering interface**. These are different functions. The badge detail lists gene names, not drug classes — a user who does not know that `blaTEM` is a beta-lactamase cannot answer "do these samples carry beta-lactam resistance?" from the badge alone. The fix is that `Badge.detail` for AMR results must include the class summary ("2 BETA-LACTAM genes: blaTEM-1, blaOXA-48"), not just gene names. This is a one-line change to `amrfinder.py:badge()` but requires acknowledging that badge detail is user-facing clinical text, not a debug string.
- Evidence: amrfinder.py lines 395-408 (`badge()` method — `detail` lists gene names only, not classes); PIPELINES_PACKAGE.md §4 `Badge.detail` described as "1-2 sentence tooltip explanation"

### LL-F5: No cross-sample matrix view in any GUI
- Verdict: ESCALATE
- Argument: Lingling cannot answer "which of my 16 samples have beta-lactam genes?" without a cross-sample matrix, which the design explicitly does not provide (the matrix existed only as a manual CSV). The R1 critique treats this as a missing feature. The architectural escalation: the `samples.json` accumulator is specifically designed to enable cross-sample aggregation — every primitive writes structured findings into the same file, making a matrix query trivial server-side. The absence of a matrix view is not a hard problem; it is an unfinished design decision. A `GET /api/projects/{project}/matrix?primitive=amr&field=class` endpoint returning a 16x5 JSON grid would answer Lingling's question and could be rendered as a sortable/filterable table with one React component reused across all primitives. The design has the data layer for this; it simply does not expose it. This gap should be a P0 for any panel-based workflow.
- Evidence: PIPELINES_PACKAGE.md §3 `samples.json` accumulated findings structure; §8 badges endpoint (aggregates badges, not findings matrices)

### LL-F6: No organism validity warning when running AMRFinder on MTBC
- Verdict: RATIFY
- Argument: `AMRFinder.applicable()` checks for assembly FASTA existence but not organism suitability (amrfinder.py lines 334-336). MTBC has an unusual AMR landscape (intrinsic resistance, efflux pumps) where AMRFinder generic mode produces misleading negatives. The architectural fix requires `applicable()` to consult `sample_context["organism"]` and return a new state — not `False` (blocked) but something like `applicability="warn"` — to signal "will run but with caveats." The current binary `bool` return type of `applicable()` cannot express this; the contract needs a third value. This is a contract change, not a cosmetic fix.
- Evidence: amrfinder.py lines 334-336; PIPELINES_PACKAGE.md §5 "Caveat: Mammaliicoccus is not in AMRFinder's -O organism list" (caveat documented for M. sciuri, not generalized to arbitrary organisms)

---

## Net delta

- **Findings that survived attack:** UX-AV1, UX-AV3, UX-AV4, UX-AV5, UX-AV6; LL-F1, LL-F2, LL-F3, LL-F4, LL-F5, LL-F6
- **Findings that failed attack:** UX-AV2 (badge density — inapplicability gates reduce real count to 2-3, not 5); UX-AV7 (mental model migration — incremental Step 2 delivery prevents dashboard proliferation at launch)
- **New escalations:** UX-AV1 (cross-card navigation is architecturally non-bidirectional, not merely undecorated); UX-AV5 (metadata/findings coupling in samples.json is a dual-role anti-pattern requiring file separation or version counters); LL-F3 (bulk run requires a new batch API endpoint and JobManager generalization, not just a UI button); LL-F5 (matrix view is architecturally trivial given samples.json but missing as a deliberate endpoint — should be P0 for panel workflows)
- **Unresolved:** none
