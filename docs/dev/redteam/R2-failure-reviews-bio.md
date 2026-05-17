# R2 — Failure-mode cross-examination of Bioinformatics (R1)

---

### BIO-AV1: Contract is sample-shaped; SNP analysis is panel-shaped
- Verdict: RATIFY
- Argument: `snp_analysis.py:291` `run()` takes a `group_dir` containing N sequences and returns one distance matrix, one KDP plot, and one closest-neighbor plot — all cross-sample artifacts. Forcing this into `PrimitiveResult.sample: str` means the composer must use either an empty string or an ad-hoc comma-join, both of which break the `sample_badges` endpoint in §8 (`GET /api/projects/{project}/samples/{sample}/badges`) — that route is parameterized by a single sample name and returns per-sample badge objects. There is no route shape that serves a panel-level badge under this URL structure.
- Evidence: `snp_analysis.py:291`; `PIPELINES_PACKAGE.md §4 PrimitiveResult.sample: str`; `§8 sample_badges endpoint`
- System end-state on failure: Panel SNP analysis runs successfully but its result cannot be surfaced through the badge API; the GUI silently shows no SNP-analysis badge for any sample in the panel.

---

### BIO-AV2: Kraken + Bracken + Krona is one chained pipeline, not three primitives
- Verdict: ESCALATE
- Argument: The bio reviewer correctly identifies the dependency chain (Bracken requires Kraken report; Krona requires Bracken output). The reliability failure is worse than described: the three primitives share one conda env (`conda:kraken_report`, §7) and one output directory (`kraken/<sample>/`, §3). If Kraken succeeds but Bracken fails mid-run, partial outputs land in `kraken/<sample>/` with a valid Kraken report but a zero-byte or absent Bracken file. The `applicable()` check for any downstream primitive reads `samples.json`, not the filesystem — so the next run will call `Bracken.applicable()`, find `"kraken"` present in `samples.json` (written on Kraken success), judge it applicable, and skip re-running Kraken. The system enters a state where Kraken findings are committed to `samples.json` but Bracken and Krona outputs are permanently absent, with no structured error surfaced unless the caller inspects individual provenance exit codes.
- Evidence: `PIPELINES_PACKAGE.md §3 kraken/<sample>/ layout`; `§7 one shared env`; `§3 rule 3 samples.json accumulation`
- System end-state on failure: `samples.json[sample].kraken` is populated with top-species data (written by Kraken), Bracken and Krona outputs are absent, and subsequent runs skip Kraken because the finding key already exists — Bracken and Krona are never retried without manual intervention.

---

### BIO-AV3: `ensure_assembly` caches on path existence, not assembler identity
- Verdict: ESCALATE
- Argument: The bio reviewer frames this as a correctness concern — wrong assembler, wrong FASTA. The reliability failure is silent wrong-answer propagation across multiple downstream primitives. `ensure_assembly` returns the cached FASTA, AMRFinder, MLST, and SeqSero2 all run to exit-code 0, all write to `samples.json`, all write provenance records with `exit_code: 0`. Nothing in the pipeline surfaces a failure signal. The SHA256 in each primitive's provenance matches the FASTA that was actually consumed — it just matches the wrong one. Six months later there is no way to know that IN107's MLST call was made on a Shovill assembly when the AMR card expected SPAdes scaffolds, because `parent_primitive_run` is marked optional in §6 and `ensure_assembly` has no mechanism to populate it.
- Evidence: `PIPELINES_PACKAGE.md §3 rule 2`; `§6 parent_primitive_run: optional`; `amrfinder.py sketch run() line: fa = proj.ensure_assembly(self.sample)`
- System end-state on failure: All assembly-dependent primitives complete with exit-code 0 and valid provenance SHA256s. `samples.json` contains findings derived from a mismatched assembly. Audit trail is complete but undetectable as wrong.

---

### BIO-AV4: AMRFinder `-O` is a runtime parameter, not a classmethod gate — `.applicable()` cannot express it
- Verdict: RATIFY
- Argument: `applicable()` tests only `assembly_fasta` existence. The `-O` organism selection is deferred to `build_kwargs()`, a method the abstract contract does not define — it appears only in the §8 composer sketch without a signature. Every front-end (vsnp_gui, kraken_gui, future OOD cards) must independently implement organism-to-`-O` resolution. The lab's *Mammaliicoccus sciuri* case is already documented: passing `-O Staphylococcus_aureus` produces point-mutation calls that are misleading on a different genus. If any composer gets this wrong — or if a future DB update adds *Mammaliicoccus* to the supported list and the hardcoded organism map is not updated — AMRFinder runs in the wrong mode and reports spurious or missing resistance calls without error.
- Evidence: `PIPELINES_PACKAGE.md §5 caveat`; `amrfinder.py sketch applicable()`; `§8 build_kwargs(ctx)` (undefined in contract)
- System end-state on failure: AMRFinder completes exit-code 0; `samples.json[sample].amr` is populated with resistance gene calls derived from the wrong organism context; badge renders normally with potentially inflated or deflated gene counts; no error logged.

---

### BIO-AV5: `findings: list[dict]` is schemaless — TSV column rename produces silent empty findings
- Verdict: ESCALATE
- Argument: The bio reviewer describes the column-rename risk. The reliability failure is quantifiable: `amrfinder.py` `run()` accesses `row["Gene symbol"]`, `row["Class"]`, `row["Subclass"]`, `row["% Identity to reference sequence"]`, `row["% Coverage of reference sequence"]` by exact string key from `csv.DictReader`. A column rename (e.g. AMRFinder 3.x → 4.x renamed "Gene symbol" to "Gene Symbol" with capital S) causes `KeyError` at parse time — the except block does not exist, so `findings` is never populated but `tsv.exists()` is True, `exit_code` is 0, and `PrimitiveResult.findings = []`. `badge()` then returns `Badge(label="AMR: none", verdict="pass")` — a clean-bill-of-health badge on a sample that may carry `mecA1`. The failure is operationally worse than the bio reviewer described: it is not just a type-checking miss, it is a `verdict="pass"` badge on an infected sample.
- Evidence: `amrfinder.py sketch run() findings parsing`; `badge() L: if not f: return Badge(..., verdict="pass")`; `PIPELINES_PACKAGE.md §4 findings: list[dict[str, Any]]`; `§11 open question: findings schema not listed`
- System end-state on failure: TSV exists, exit-code 0, `findings=[]`, `samples.json[sample].amr = {"n_genes": 0, "genes": [], "classes": []}`, badge shows "AMR: none / pass" — a false negative that survives into the report without error or warning.

---

### BIO-AV6: Sourmash sketch and gather have different reuse profiles under one primitive
- Verdict: REBUT
- Argument: The bio reviewer is correct that sketch is DB-independent and gather is DB-dependent. However, the reliability consequence is bounded: the worst outcome is unnecessary recomputation of the sketch when the gather must rerun. This is slow (extra minutes per sample) but produces no wrong answer and no corrupt state. The primitive's single exit code still correctly represents success/failure, and provenance is still complete. The bio concern (split artifact reuse) is real but it does not create a failure mode that degrades result quality or produces silent errors. Operationally, until the lab actually changes from GTDB to NCBI refseq, this is theoretical overhead, not a reliability gap.
- Evidence: `PIPELINES_PACKAGE.md §3 sourmash/<sample>/ layout`; attack vector severity rated "minor" by R1 reviewer
- System end-state on failure: Sourmash re-sketches unnecessarily; runtime is extended; results are correct.

---

### BIO-AV7: Provenance records FASTA SHA256 but not the assembler that produced it
- Verdict: RATIFY
- Argument: This is the audit-trail half of AV3 (assembler cache) and survives independent of it. `parent_primitive_run` is marked optional in §6, and `ensure_assembly()` has no mechanism to record which primitive produced the assembly or to populate that field. The `verify_provenance.py` script checks that declared input SHA256s still resolve (file exists and matches) — it does not walk the `parent_primitive_run` chain. This means the provenance audit for an assembly-dependent primitive proves "the FASTA we ran on was this byte sequence" but cannot prove "this byte sequence was produced from these FASTQs by this assembler at these parameters." The gap is not theoretical: the lab already has one changed Shovill version across a multi-month study as a real scenario.
- Evidence: `PIPELINES_PACKAGE.md §6 parent_primitive_run: null (optional)`; `§3 rule 2 ensure_assembly`; `§6 verify_provenance.py audit description`
- System end-state on failure: Provenance audit passes; SHA256 resolves; assembler identity and parameters are unrecoverable from the record; reproducibility claim for assembly-dependent primitive runs is incomplete.

---

## Net delta

- Findings that survived: AV1 (panel abstraction mismatch), AV3 (ensure_assembly cache), AV4 (AMRFinder -O runtime), AV5 (schemaless findings), AV7 (provenance assembler gap)
- Findings that failed: AV6 (sourmash sketch/gather split) — real bio concern, no reliability consequence
- Escalations: AV2 (Kraken chain partial-failure → permanent Bracken skip), AV3 (silent wrong-answer across all assembly-dependent primitives), AV5 (column-rename → false-negative "pass" badge)
- Unresolved: none

- Worst combined failure (bio × reliability): AV2 + AV5 compounding. Kraken partially fails (Bracken aborts after a valid Kraken report is written). `samples.json` records a Kraken top-species hit — say, *Proteus mirabilis* at 57% — which `AMRFinder.applicable()` does not consult because it only checks `assembly_fasta`. AMRFinder runs against the Shovill assembly (AV3 cache hit). Meanwhile, a concurrent AMRFinder DB update renames a TSV column. `findings=[]` is returned, badge is "AMR: none / pass." The final report for a polymicrobial sample with beta-lactam resistance shows a clean AMR badge, an incomplete Kraken card (no Bracken/Krona), and a provenance record with `exit_code: 0` throughout. No alert is raised. A biosurveillance decision on that sample is made on three independently wrong signals, each of which individually looks like a successful run.
