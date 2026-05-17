# R2 — Ops cross-examination of Failure-modes (R1) + Tod (R1)

**Reviewer:** Senior ops/SRE  
**Source:** R1-failure-modes.md, R1-user-tod.md, PIPELINES_PACKAGE.md §3/§6/§8/§11, snp_analysis.py, provenance_writer.py (lines 120–597 read)

---

## On R1-failure-modes

### FM-AV1: `applicable()` / `.run()` TOCTOU on filesystem state
- Verdict: RATIFY
- Argument: `applicable()` at amrfinder.py:336 does a bare `Path.exists()` with no re-validation inside `.run()`. The §8 call-site sketch has no `except PrimitiveError` block, and `PrimitiveError` is an open question per §11.6. On wgs3 with NFS-mounted project roots and concurrent OOD sessions, a `FileNotFoundError` inside `.run()` propagates to uvicorn as a 500 — no badge emitted, `samples.json` in an unknown state.
- Evidence: PIPELINES_PACKAGE.md §11.6 (PrimitiveError listed as open question); §8 sketch (bare `primitive.run()`); amrfinder.py:336.

### FM-AV2: `ensure_assembly()` check-then-act race, no per-sample lock
- Verdict: ESCALATE
- Argument: R1 calls this major; it is a blocker. The entire §3 shared-artifact design is predicated on `ensure_assembly()` being safe to call concurrently across cards. It is not. Two OOD cards (AMR + MLST) launched seconds apart on the same project both pass the `Path.exists()` check before either SPAdes creates the file. Two concurrent SPAdes invocations on a 47-sample Brucella project can saturate RAM and produce a last-writer-wins corrupted assembly. Both provenance records claim ownership of that assembly path.
- Evidence: PIPELINES_PACKAGE.md §3 rule 2 ("First card that needs `<sample>.fasta` runs SPAdes"); Project helper sketch (no locking in `ensure_assembly()` signature); CLAUDE.md (wgs3: 64 cores, concurrent OOD sessions by design).

### FM-AV3: `record_finding()` + `write_provenance()` leave findings/provenance out of sync
- Verdict: REBUT (ordering claim fails; NaN risk survives on different path)
- Argument: In the amrfinder.py sketch, `record_finding()` is not called inside `.run()` — it is a caller responsibility per the §8 sketch. `write_provenance()` is the last call in `.run()`. A `ValueError` from a malformed TSV float field fires before `write_provenance()` is reached, so the failure is clean. The NaN serialization risk is real but lands in `snp_analysis.py` (see FM-AV5), not in the ordering R1 describes.
- Evidence: amrfinder.py lines 370–381 (write_provenance final, record_finding absent from .run()); PIPELINES_PACKAGE.md §8 (record_finding is caller responsibility).

### FM-AV4: `samples.json` corruption after mid-write kill, no recovery path
- Verdict: RATIFY
- Argument: `provenance_writer.py` lines 120–131 confirm the production atomic-write pattern (`mkstemp` + `os.replace` + `unlink` on error) is implemented for T-07 records. That pattern is not specified for `Project.record_finding()` in §3. No stale-tempfile scan at `Project.from_path()` is specified. Unless `record_finding()` explicitly ports `_atomic_json_write` from `provenance_writer.py`, the new code ships without the protection the existing code already has.
- Evidence: provenance_writer.py lines 120–131 (_atomic_json_write confirmed); PIPELINES_PACKAGE.md §3 (record_finding sketch has no atomicity spec).

### FM-AV5: `snp_analysis.py` emits `status: ok` for NaN-filled matrix
- Verdict: RATIFY
- Argument: Confirmed code, not a sketch. `sanitize_matrix()` lines 181–186 fills NaN with 0, records `nan_filled: N`, and leaves status as `"ok"`. KDP and closest-neighbor plots render unconditionally on the corrupted distances (lines 340–341). The Badge taxonomy has no `degraded` verdict. This is the only implemented primitive in the codebase and it already exhibits the silent-corruption pattern the badge system is supposed to prevent.
- Evidence: snp_analysis.py lines 181–186, 340–341, 343–360.

### FM-AV6: Schema drift in `samples.json` silently disqualifies old projects
- Verdict: RATIFY
- Argument: `SampleContext` fields are an open question per §11.3. No `schema_version` field in the §3 example JSON. On wgs3 with multi-month projects, every project created at Step 1 will be consulted by Step 5 primitives (ConFindr, CheckM) expecting fields not yet written. `applicable()` returning `False` on a missing key is indistinguishable from "tool not applicable for organism" — failure is silent by design.
- Evidence: PIPELINES_PACKAGE.md §11.3 (SampleContext open question), §3 example JSON (no schema_version), §9 migration table (no version pinning across 8 steps).

### FM-AV7: Exit-code dual-representation has no defined authority
- Verdict: REBUT
- Argument: `provenance_writer.py` lines 120–131 implement a single `_atomic_json_write` producing one JSON with all fields including exit code. There is no separate sentinel text file in the T-07 implementation. The "exit_code text file" in §6 is design-doc shorthand that does not match the actual implementation. The real risk is a developer reading §6 literally and implementing the dual-write from scratch. The fix is a one-line reference in §6 to `provenance_writer.py`.
- Evidence: provenance_writer.py lines 120–131 (single atomic JSON write, no sentinel file); PIPELINES_PACKAGE.md §6 (describes "exit_code" text file as separate artifact — design-doc fiction).

---

## On R1-user-tod

### TOD-HG1: No export workflow distinguishing results-only from full FASTQ tar
- Verdict: RATIFY
- Argument: No export API, no manifest generator, no relative-path rewriting. Every `record.json` embeds absolute paths (`str(self.FASTA)` at amrfinder.py:434). On USDA-to-USDA transfer, `verify_provenance.py`'s SHA256-resolves check fails immediately on import. A tar-without-download workaround produces a technically broken provenance record, not just a missing file.
- Evidence: PIPELINES_PACKAGE.md §3 (no export API); amrfinder.py line 434 (absolute path in provenance).

### TOD-PR1: Provenance regression vs. T-07 — env name only, not yaml content
- Verdict: RATIFY (empirically confirmed)
- Argument: T-07 captures: `conda_env_yaml_sha256`, content-addressed shared-store path, `pip_freeze_sha256`, per-binary version strings for samtools/bcftools/bwa/mafft/raxml/iqtree, `python_version`, `platform` (provenance_writer.py lines 476–487, confirmed by direct read). The §6 schema for new primitives captures only `"env": "conda:amrfinder"` — the name string. This is not a sketch approximation; it is the literal canonical schema in the doc. Any developer implementing new primitives from §6 alone ships a two-tier provenance system with no justification.
- Evidence: provenance_writer.py lines 476–487 (T-07 schema confirmed); PIPELINES_PACKAGE.md §6 (`"env": "conda:amrfinder"` only).

### TOD-PG1: `verify_provenance.py` validates data integrity, not environment integrity
- Verdict: RATIFY
- Argument: The three stated checks (record.json present, exit_code sentinel matches, input SHA256 resolves) are all data-integrity checks. None detect a collaborator running a different tool version or DB version. The stated value proposition — "answerable from provenance alone" — requires an environment-integrity check. This is a one-function addition to `verify_provenance.py`.
- Evidence: PIPELINES_PACKAGE.md §6 (three-check spec, none environment-integrity).

### TOD-PG2: `samples.json` findings are last-write-wins with no versioning
- Verdict: RATIFY
- Argument: `record_finding()` "merges into `samples.json[sample][primitive]`" — unconditional overwrite. A collaborator re-running AMR destroys Tod's original findings in the shared knowledge base. The per-tool `.provenance/record.json` survives but the shared summary does not. Requires a schema change now (append with run_id key) before data loss occurs.
- Evidence: PIPELINES_PACKAGE.md §3 record_finding docstring; §6 parent_primitive_run field (not applied to samples.json indexing).

### TOD-HG2: `samples.json` has no schema documentation
- Verdict: RATIFY
- Argument: §11.3 lists SampleContext schema as an open question. The §3 example JSON is the only specification and is labeled as an example, not a contract. A collaborator cannot validate an import without reading vsnp_gui source.
- Evidence: PIPELINES_PACKAGE.md §11.3 (open question), §3 (example only).

### TOD-RG1: Conda-env wrappers replace NAHLN_AMR Apptainer containers — reproducibility downgrade
- Verdict: RATIFY
- Argument: §10 explicitly drops Apptainer. `"container": null` in §6 is never exercised in §9. A conda env named `mlst` is mutable via `mamba update`. For MLST scheme assignment and SeqSero2 serotyping appearing in regulatory submissions, "I used conda:mlst" is documentation; "I used mlst.sif SHA256=abc..." is a reproducibility guarantee. This is a downgrade for the exact tool classes with legal weight.
- Evidence: PIPELINES_PACKAGE.md §10 (Apptainer explicitly excluded), §6 (`"container": null`), §9 (all steps use conda-env wrappers).

### TOD-PG3: Assembly provenance schema unspecified; SPAdes parameters not captured
- Verdict: RATIFY
- Argument: `assembly/<sample>/.provenance/record.json` schema is never defined. SPAdes is stochastic and version-sensitive. The AMR record hashes the input FASTA — divergent assemblies are detected but cannot be reproduced without SPAdes version, k-mer list, and randomization parameters. Tod's AMR-discrepancy investigation stalls at "assemblies differ" with no further resolution path.
- Evidence: PIPELINES_PACKAGE.md §3 (assembly/.provenance/ listed, schema undefined); amrfinder.py provenance() (hashes FASTA, no assembly parameters captured).

---

## Net delta

- **Findings that survived (RATIFY):** FM-AV1, FM-AV4, FM-AV5, FM-AV6; TOD-HG1, TOD-PR1, TOD-PG1, TOD-PG2, TOD-HG2, TOD-RG1, TOD-PG3 — 11 of 14 total
- **Findings that failed (REBUT):** FM-AV3 (ordering mechanism wrong; NaN risk survives via different path), FM-AV7 (dual-write is design-doc fiction; implementation uses single atomic JSON)
- **Escalations:** FM-AV2 (major → blocker; `ensure_assembly()` race invalidates the shared-artifact design's core architectural promise)
- **Unresolved:** None
- **Empirical verdict on T-07 regression claim:** CONFIRMED. provenance_writer.py lines 476–487 prove T-07 captures `conda_env_yaml_sha256`, content-addressed shared store path, `pip_freeze_sha256`, and per-binary version strings for six system tools. The §6 schema captures only `"env": "conda:amrfinder"`. The regression is the literal canonical schema in the design doc, not a simplification for illustration. The single-sentence fix: add a forward-reference in §6 stating "the env block must be populated via `capture_env_snapshot()` from `provenance_writer.py`, not the name string alone."
