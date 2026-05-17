# R1 — Bioinformatics workflow realism (adversarial)

## Attack vectors

### 1. The contract is sample-shaped; SNP analysis is panel-shaped — the abstraction is the wrong unit.

- Evidence: `snp_analysis.py:291` — `run(group_dir, group_name, out_dir, ...)` takes a directory of samples and produces a cross-sample SNP distance matrix, KDP, and closest-neighbor plots. `PrimitiveResult.sample: str` (§4) is a single string with no slot for a list of samples and no mechanism for a group-level badge.
- Severity: blocker
- Falsification: Show a `SnpAnalysis` class implementing `AnalysisPrimitive` where `PrimitiveResult.sample` is either a comma-joined string or an empty sentinel, and explain why per-sample badge rendering (`sample_badges` endpoint, §8) is still coherent.
- Detail: `snp_analysis.py` computes pairwise distances over an entire group FASTA, filters by step1 allowlist, clusters, and plots distributions — nothing maps to one sample. The "panel" is the primitive's natural granularity. Forcing it into `sample: str` either breaks the abstraction or produces one PrimitiveResult per pair. The contract paper-overs this with one field name.

### 2. Kraken + Bracken + Krona is one chained pipeline, not three independently applicable primitives.

- Evidence: §2 lists `kraken.py`, `bracken.py`, `krona.py` as separate modules. §3 puts all three outputs under `kraken/<sample>/`. §7 assigns them one shared conda env. Bracken requires the Kraken report to exist; Krona requires the Bracken output.
- Severity: major
- Falsification: Write `Bracken.applicable(sample_context)` without referencing whether a Kraken report already exists on disk. If you cannot, the three tools are a pipeline, not three primitives.
- Detail: The contract provides no mechanism for inter-primitive dependencies — `applicable()` takes a `sample_context` dict from `samples.json`, not live filesystem state. A composer calling primitives in an arbitrary order will fail mid-chain with no structured error. The doc collapses this into one card (§3 table) because operationally they are one unit; the module split then contradicts that.

### 3. `ensure_assembly` caches on "FASTA exists" — not on "FASTA produced by the right tool and parameters."

- Evidence: §3 rule 2 docstring: "Returns the assembly FASTA, running SPAdes/Shovill if it doesn't exist." The helper takes only `threads` as a parameter — no assembler selection, no k-mer size. AMR, MLST, and SeqSero2 all consume the same `assembly/<sample>/<sample>.fasta`.
- Severity: major
- Falsification: Demonstrate that SPAdes default parameters and Shovill default parameters produce assemblies that give identical calls for AMRFinder, MLST, and SeqSero2 on the same sample. Then explain what happens when the first card ran Shovill and a second card needs SPAdes scaffolds.
- Detail: If AMR runs first (Shovill) and MLST runs second, `ensure_assembly` returns the Shovill output silently. The provenance record in `assembly/<sample>/.provenance/` records which assembler ran, but `ensure_assembly` does not consult it — it returns whatever FASTA is present. Cache hit on path existence is not cache hit on correctness.

### 4. AMRFinder's organism flag is a run-time parameter, not a go/no-go gate — `.applicable()` cannot express it.

- Evidence: §5 caveat: "do not pass `-O Staphylococcus_aureus` even though it's the closest relative." `AMRFinder.applicable()` checks only `assembly_fasta` existence. The `-O` decision is left to whoever instantiates `AMRFinder`. §8 composer sketch calls `cls.build_kwargs(ctx)` — a method the contract does not define.
- Severity: major
- Falsification: Show a concrete `build_kwargs()` classmethod that reads `sample_context["organism"]`, queries `amrfinder --list_organisms` for the current DB's supported list, and sets `organism=None` for *Mammaliicoccus* — without hardcoding organism strings in the primitive.
- Detail: The whitelist of organisms AMRFinder supports for `-O` is embedded in the binary and changes with each DB release. The design doc acknowledges the caveat but puts resolution burden on "whoever constructs the primitive." Every caller reimplements organism resolution independently, and the implementations will drift.

### 5. `findings: list[dict]` is schemaless — badge and report layers are silently coupled to undocumented key names.

- Evidence: `PrimitiveResult.findings: list[dict[str, Any]]` (§4). `AMRFinder.badge()` accesses `x["gene"]` by string literal. §11 lists `SampleContext` schema as an open question; `findings` schema is not even listed as an open question.
- Severity: major
- Falsification: Show a `findings` type definition in `common/contract.py` that covers AMR, MLST, Kraken, and Sourmash without degenerating to `Any`.
- Detail: When AMRFinder renames a TSV column (it has done this across versions), the parsing in §5 silently produces empty `gene` fields. Type checkers catch nothing. MLST findings (ST number, per-locus allele calls) and Kraken findings (species, percentage, read counts) have completely different shapes under one field name. The report layer cannot be tested without a live tool run.

### 6. Sourmash wraps three operationally distinct steps that have different reuse profiles.

- Evidence: §3 layout shows `<sample>.sig` and `<sample>.containment.tsv` — two output files from `sourmash sketch` and `sourmash gather` — under one `sourmash/<sample>/` directory, implying one primitive. DB change (GTDB → NCBI refseq) requires rerunning gather but not re-sketching.
- Severity: minor
- Falsification: Show that `sourmash sketch` + `sourmash gather` + taxonomy parse can run atomically in one subprocess call with one exit code, making the step distinction irrelevant at the primitive boundary.
- Detail: The sketch is a reusable sample artifact; the gather is DB-dependent. One primitive forces re-sketching on every DB update. The natural split — `SourmashSketch` as a shared artifact (like `ensure_assembly`) and `SourmashGather` as the primitive — is invisible in the current design.

### 7. Provenance records the FASTA SHA256 but not the assembler that produced it — re-running from provenance is incomplete for assembly-dependent primitives.

- Evidence: §6 schema: `fasta_sha256` present; `parent_primitive_run` marked "optional." `ensure_assembly` has no mechanism to populate that field. AMR provenance does not reference `assembly/<sample>/.provenance/` by path.
- Severity: minor
- Falsification: Show that `verify_provenance.py` follows the `parent_primitive_run` chain from AMR into assembly provenance and re-derives the full assembler parameter set needed to reproduce the FASTA from raw FASTQs.
- Detail: Six months later with a different Shovill version and no assembly provenance link, the SHA256 proves the FASTA matched at run time but cannot tell you what produced it. The doc claims provenance makes runs reproducible — for assembly-dependent primitives, it makes them auditable at best.

---

## Recommendations

1. **Split the contract into `SamplePrimitive` and `PanelPrimitive`.** `PanelPrimitive` takes `samples: list[str]`, returns `PanelResult` with per-sample findings. `snp_analysis.py` is a `PanelPrimitive`. The badge endpoint emits one badge per sample from one panel run. This makes the sequencing requirement explicit rather than hidden.

2. **Define `findings` as a tagged union of typed dataclasses.** Create `AMRFinding`, `MLSTFinding`, `KrakenFinding`, `SourmashFinding` in `common/findings.py`. Type checkers catch column renames at the next DB update instead of at report generation time.

3. **Make `ensure_assembly` consult provenance before returning a cached FASTA.** Accept `assembler: Literal["spades", "shovill"]` and `params: dict`. Read `assembly/<sample>/.provenance/record.json` and raise `AssemblyMismatchError` if the cached assembly was produced by a different assembler or parameter set. One extra JSON read; the cost is negligible.

---

## Where I'd be wrong

1. If all assembly-consuming primitives tolerate either assembler's output at default settings, the cache-key critique is theoretical and path existence is a sufficient cache key.

2. If kraken + bracken + krona are always run as an indivisible unit with no intermediate outputs exposed to the user, a single `KrakenPipeline` primitive is correct and the three-module split is an internal detail that does not affect the contract.

3. If the lab's sample universe remains Illumina paired-end only, `Project.fastqs()` returning `tuple[Path, Optional[Path]]` is adequate and the long-read / multi-library concern is premature.
