# Context

## What was being reviewed

[`sources/PIPELINES_PACKAGE.md`](sources/PIPELINES_PACKAGE.md) — a ~630-line design document proposing a shared Python package (`kapurlab-pipelines`) for the kapurlab's genomics pipelines. The core proposal: a single `AnalysisPrimitive` contract that all bioinformatics tools (vSNP, kraken2, AMRFinder, sourmash, MLST, etc.) conform to, consumed by multiple GUI front-ends — vsnp_gui (already OOD-native), kraken_gui (re-deployed from electron to OOD), and future OOD cards.

The design includes:

- An abstract base class with six methods (`run`, `latex`, `excel`, `web`, `badge`, `provenance`) + an `applicable()` classmethod
- A canonical project filesystem layout (`download/`, `step1/`, `kraken/`, `amr/`, `assembly/`, `_provenance/`, etc.) shared across cards
- A `samples.json` shared knowledge base, written by each primitive
- A worked example: `pipelines/amrfinder.py` (smoke-tested on 8 *M. sciuri* isolates 2026-05-12)
- A migration path: incremental, ~8 steps from "first primitive inside vsnp_gui" through "kraken_gui re-deployed as OOD card"
- Open questions deferred to §11 (sample context schema, error surfaces, contract evolution)

## Why a red-team now

The lab is about to begin **T-27** — implementation of `pipelines/common/`, the shared base layer the design specifies. Once T-27 ships, the contract becomes load-bearing: every primitive thereafter inherits its decisions. A refactor of a half-shipped contract is far more expensive than a pre-implementation design review.

The session that produced this review filed T-27 through T-35 against the design (see `../TICKETS.md`), then explicitly **blocked** them on red-team completion.

## Project context the reviewers had

The kapurlab is a veterinary infectious-disease genomics lab running WGS on outbreak pathogens (*M. bovis*, *Brucella*, *M. avium*, SARS-CoV-2). Their primary tooling is **vSNP3**, a phylogenomic SNP pipeline, exposed via a FastAPI + React web GUI deployed as an **Open OnDemand (OOD) batch_connect interactive application** on `kapurlab-wgs3` (Threadripper PRO 7985WX, 503 GB RAM, multi-user Linux server). The full deployment context is in [`sources/CLAUDE.md`](sources/CLAUDE.md).

Implementation state at review time:

- **T-07 (run provenance) shipped**: per-step `record.json` with conda env yaml hash, per-binary version probes, vsnp3 patch set, reference manifest, input/output SHA256s — implementation at [`sources/provenance_writer.py`](sources/provenance_writer.py). The new design's §6 schema is being compared against this baseline.
- **T-09 (QC badges) shipped**: three-tier `pass / review / fail` verdict per sample, threshold-layered config. The new design's `Badge` is meant to extend this pattern.
- **`posthoc/snp_analysis.py` shipped**: a working analysis primitive operating on sample groups (matrix, KDP, closest-neighbor). Predates the proposed contract. **Required falsification fixture** — see below.

Some R1 reviewers also had access to `frontend/src/App.jsx` (the existing React UI) and `CLAUDE.md` for deployment-model context. None had access to this conversation; every agent worked from files only.

## The falsification fixture

[`sources/snp_analysis.py`](sources/snp_analysis.py) is an existing, working analysis primitive in production on wgs3. It predates the `AnalysisPrimitive` contract.

Every Round 1 reviewer was **required to anchor at least one attack vector** in whether/how this code fits the proposed contract. The reasoning: a working prototype is the strongest empirical test of an abstract contract. If the existing prototype doesn't fit, the contract is wrong — not the prototype.

This requirement caught the central convergent finding (sample-shaped contract vs panel-shaped tools), the production NaN-fill bug (BLOCKER-11), and the format-drift point (BLOCKER-10) — all anchored in actual file:line evidence rather than abstract speculation.

## What's not in this review

- **A user walkthrough with a real human.** The F-persona agents (Lingling, Dev, Tod) simulate users; they are not users. The real version of this is a screen-share with the actual lab members. Friction surfaced by simulated personas should be treated as *plausible* friction, to be confirmed against real users — not as definitive.
- **Performance / benchmarking.** No reviewer was asked to evaluate computational cost.
- **Security / multi-tenant attack surface.** Considered out of scope for an internal lab tool with kerberos-equivalent auth via OOD.
- **A comparison against alternative designs (e.g., Nextflow, snakemake).** The design explicitly rules these out in §10; reviewers attacked the proposed design on its own terms.
