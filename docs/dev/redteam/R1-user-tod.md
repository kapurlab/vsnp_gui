# R1 — User walkthrough: Tod (senior researcher, handoff scenario)

**Persona:** Tod, 20+ years veterinary infectious disease genomics, USDA pipeline background, regular vSNP3/vsnp_gui user. Methodical. Suspicious of redesigns. Results go into publications and regulatory submissions.

**Scenario:** Hand off project "Brucella_2025_outbreak" (47 samples, 3 months) to a collaborator at another USDA lab. 45 minutes. They need results, provenance, tool versions, raw outputs.

---

## Session narration

OK. 47 Brucella samples, three months of work. First question — where does this all live? Design §3 shows a single project tree: `download/`, `step1/`, `step2/`, `kraken/`, `amr/`, `sourmash/`, `assembly/`, `_provenance/`. If everything is genuinely under one root, I can `tar -czf Brucella_2025_outbreak.tar.gz /home/vxk1/projects/Brucella_2025_outbreak/` and hand it over. That's a real improvement over the current situation where I'd be hunting through my home directory and a temp folder. Good.

But wait — `download/` holds the raw FASTQs. For 47 Brucella samples, that's probably 50–150 GB. My collaborator may have access to the same SRA accessions already. If I blindly `tar` the whole tree, I'm shipping 100 GB they don't need. And if I exclude `download/`, the `inputs.fasta` paths in each `.provenance/record.json` point to absolute paths on wgs3 that won't resolve on their system. The design has no export mode, no "handoff package" button, no manifest, no relative-path rewriting.

**Handoff gap 1:** No export workflow that distinguishes "results + provenance only" from "full project including FASTQs." A collaborator-safe package would need relative paths, a manifest, and an optional FASTQ-omit flag. None of this exists.

Alright. Let me look at what the provenance actually contains. I go to `amr/Brucella_2025_NE_004/.provenance/record.json`. The §6 schema shows: tool version (4.2.7), DB version (2026-03-24.1), exact command, input FASTA SHA256, outputs, exit code, timestamp, hostname, env name. For a single-tool re-run question — "what version of AMRFinder did you use?" — this is actually decent. My collaborator can compare their version against mine and know whether there's a mismatch.

But I'm comparing this to what T-07 already captures for vSNP step1, and there's a meaningful regression. `provenance_writer.py` (lines 398–489) captures: a full conda env yaml (content-hashed, stored at `/srv/kapurlab/audit/env_snapshots/<sha256>.yaml`), a pip freeze, and per-binary version probes for samtools, bcftools, bwa, mafft, raxml, iqtree. Not theoretical — implemented, running in production on wgs3.

The §6 schema for new primitives has `"env": "conda:amrfinder"`. That's the conda env name. It is not a yaml. It is not a pip freeze. It is not a content hash of the environment state. A collaborator who reads `"env": "conda:amrfinder"` knows I used a conda env called "amrfinder." They do not know what version of libdeflate or ncbi-datasets-pylib was installed. In six months when they run `conda create -n amrfinder ncbi-amrfinderplus` and get version 4.3.1, they have no way to detect the environment diverged from mine except by comparing raw TSV outputs manually.

**Provenance regression 1 (vs. T-07):** T-07 captures `conda_env_yaml_sha256`, `conda_env_yaml_path`, `pip_freeze_sha256`, and system tool version strings. The §6 schema captures only the env name string. This is strictly worse than what vsnp_gui already records for step1. The "why did you get different AMR calls?" question is answerable from T-07 provenance; from §6 provenance alone it is not.

Now the collaborator's amrfinder version question. My record says `"version": "4.2.7"`. They can see a mismatch if their version differs. But there's no `verify_provenance.py` behavior that checks "does my installed amrfinder match record.json.version?" The design mentions `verify_provenance.py` (§6) checking: record.json present, exit_code sentinel matches, input SHA256 still resolves. Data integrity checks. Not environment integrity. My collaborator has to read the record manually.

**Provenance gap 1:** `verify_provenance.py` validates data integrity but not environment integrity. No automated flag when a re-running collaborator's tool version or DB version diverges from the original run record.

Now: do I include `samples.json` in the handoff? Looking at the §3 example, it contains organism, host, isolation_source, and accumulated findings per primitive: step1 reference, mapped_pct, kraken top_species, amr gene counts. For my collaborator, this is actually the most useful artifact — a compact per-sample summary. But there's no schema documentation. No `samples.json.schema.json`, no markdown field table. My collaborator receives a JSON file with nested keys and has to reverse-engineer semantics from the vsnp_gui source code (which they don't have).

And there's a worse problem. If my collaborator imports my `samples.json` and re-runs AMR with their updated DB, `record_finding()` overwrites `samples.json[sample]["amr"]` in place. My original findings are gone from the shared knowledge base. The per-tool `.provenance/record.json` survives, but the canonical per-sample summary now reflects their run, not mine.

**Provenance gap 2:** `samples.json` findings are last-write-wins with no versioning. A collaborator who re-runs one primitive overwrites the original findings in the shared summary permanently. The per-tool provenance record survives but the shared knowledge base is corrupted.

**Handoff gap 2:** `samples.json` has no schema documentation. The design lists its schema definition as an open question (§11, Q3: "define a `SampleContext` dataclass"). A collaborator receiving this file cannot reliably import it without reverse-engineering the field semantics.

Let me think about the NAHLN_AMR question directly. NAHLN_AMR is Nextflow + Apptainer. The design (§9, step 5) proposes porting MLST, SeqSero2, ABRicate from NAHLN_AMR into individual conda-env primitives, explicitly dropping Nextflow (§10). The argument is that Nextflow adds a DSL and runtime the team doesn't need.

I understand the argument. But NAHLN_AMR's Apptainer containers give you something conda envs don't: a frozen filesystem image. When the `kapurlab-pipelines` package runs MLST from `conda:mlst` in six months, it's running whatever that env contains after the last `mamba update`. When a collaborator runs from NAHLN_AMR's Apptainer, they run a byte-identical environment to what was validated. The §6 schema includes a `"container": null` field — Apptainer support was considered — but the migration path always leaves it null.

**Reproducibility gap 1:** The design replaces NAHLN_AMR's Apptainer-containerized MLST/SeqSero2/ABRicate with conda-env wrappers and provides no path to locking those envs as Apptainer images. For tools used in regulatory submissions, a conda env snapshot is a documentation artifact; an Apptainer image is a reproducibility guarantee. This is a downgrade.

Back to my six-months-later scenario: "we got different AMR calls when re-running." From §6 I can tell them: tool 4.2.7, DB 2026-03-24.1, input FASTA SHA256 matches. That narrows it to: did they assemble with the same SPAdes run? The AMR primitive depends on the assembly from `ensure_assembly()` (§3, rule 2). The design shows `assembly/<sample>/.provenance/` exists. But the assembly primitive's `record.json` schema is never specified. SPAdes is a stochastic assembler — seed, k-mer parameters, SPAdes version all matter for bit-for-bit reproducibility. The AMR record hashes the input assembly FASTA; if the assembly differs, the hash differs and we know. But we still can't reproduce the assembly without the SPAdes parameters, and those may not be in `assembly/.provenance/record.json` because that schema is unspecified.

**Provenance gap 3:** The assembly primitive's provenance schema is not defined. SPAdes/Shovill parameters, version, and seed are not guaranteed to be captured. A "different AMR calls" investigation that traces back to a different assembly cannot be resolved from provenance alone.

The 45-minute clock is running. I have enough gaps to know I'm not handing this package over and calling it reproducible. Per-tool version records? Yes. One canonical project directory? Yes. Conda env content for new primitives? No. Verified-environment check on collaborator's system? No. Schema documentation for samples.json? No. Apptainer pinning for NAHLN_AMR-derived tools? No.

For an internal USDA-to-USDA transfer where both parties can compare tool versions manually and tolerate some ambiguity, survivable. For a regulatory submission or a paper with reviewers who demand independent reproduction, not sufficient.

---

## Verdict (out of character)

**Would Tod trust this for his handoff? Conditional.** Internal USDA-to-USDA transfer: workable with manual version comparison. Regulatory submission or publication: insufficient as specified.

**Provenance gaps that would block the handoff:**

1. **No conda env snapshot for new primitives.** §6 records the conda env name, not its content. T-07 already captures `conda_env_yaml_sha256` + system tool versions for step1. New primitives regress to name-only. Collaborators cannot verify their environment matches without manual comparison.
2. **No automated environment version check.** `verify_provenance.py` validates data integrity (SHA256s, exit codes) but not environment integrity (tool versions, DB versions). A collaborator running with a different tool version gets no warning.
3. **Assembly provenance schema unspecified.** SPAdes parameters and version are not guaranteed to appear in `assembly/.provenance/record.json`. A "different downstream calls" investigation can stall at the assembly step.
4. **`samples.json` is overwrite-on-rerun with no versioning.** Re-running any primitive corrupts the original findings in the shared summary.

**Where the design improves on the status quo:**

- Single project root for all tool outputs is a genuine win. Currently I'd be assembling results from multiple ad-hoc locations.
- Per-tool `record.json` with input SHA256, DB version, and exact command is better than the current state (step1 has T-07; everything else has nothing).
- `parent_primitive_run` linking connects AMR results to the step1 run that produced the assembly — useful for audit chains.
- `_provenance/pipeline_runs/` project-level index provides a cross-tool run history that doesn't currently exist.

**Where the design regresses from the status quo (vs. T-07):**

T-07's `provenance_writer.py` captures: `conda_env_yaml_sha256`, `pip_freeze_sha256`, system package versions for every tool vsnp3 shells out to, vsnp_gui git SHA + dirty flag, vsnp3 version + applied patches with patch SHA256, reference folder manifest with per-file hashes, and a `trust_scope` block. The §6 schema for new primitives captures none of this except tool version and DB version. Any implementation that treats §6 as the new standard produces a two-tier provenance system: step1 (T-07 quality, high) versus everything else (§6 quality, minimal). That inconsistency is worse than a uniform lower bar.

**The single change that would most increase Tod's trust:**

Add a `conda_env_snapshot` block to the §6 schema, defined identically to T-07's pattern: content-hash of `conda env export --no-builds`, stored once per (env name + hash) pair in a shared snapshots directory, referenced by hash in each record. This is not new work — the implementation exists in `provenance_writer.py` starting at line 398. Porting it to `common/provenance.py` takes an afternoon and closes the most significant regression.

**One question Tod would ask the designer:**

T-07's environment capture is already implemented in this codebase. Why does §6 define a reduced schema that drops it? If the answer is "§6 is a sketch and the real implementation will use provenance_writer.py," the design doc needs to say that explicitly — because right now §6 reads as the canonical schema definition, and any developer implementing the new primitives will follow it and ship the regression.
