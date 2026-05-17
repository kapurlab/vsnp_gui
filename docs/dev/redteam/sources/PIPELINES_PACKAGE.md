# PIPELINES_PACKAGE — design for shared analysis primitives

**Status:** design doc, not yet implemented
**Owner:** vk + lab
**Last updated:** 2026-05-12
**Worked example:** AMRFinderPlus (smoke-tested on 8 *M. sciuri* assemblies, 2026-05-12)

---

## 1. Why this exists

Over a single session, we ran the following bespoke wrappers, each implemented from scratch:

- kraken2 + bracken + krona (from `~/kraken/pipeline/bin/`)
- sourmash species check (ad-hoc bash + vsnp3 env)
- AMRFinderPlus (ad-hoc `mamba install` on wgs3, manual parsing into a matrix.csv)

Each of these has the same logical shape: take a FASTA (or FASTQ + an assembly step) → produce a TSV → surface a per-sample summary in a report + a panel-level matrix. We have three places that will all want to call them:

| Front-end | Where | Today | Long-term |
|---|---|---|---|
| **vsnp_gui** | `~/vsnp_gui` (FastAPI + React, served via OOD on wgs3) | doesn't run any of these yet | already OOD-native |
| **kraken_gui** | `~/kraken/gui` (FastAPI + React + **electron** wrapper, local-only) | has kraken/bracken/krona built-in; no AMR / sourmash | **re-deploy as OOD batch_connect app on wgs3**, drop electron |
| Future tools | `deploy/ood/...` | none yet | always OOD from day 1 |

**All long-term front-ends are OOD batch_connect apps on wgs3.** Electron was a transitional choice for the kraken GUI and will be retired. This means every front-end speaks the same deployment story (batch_connect template, Apptainer container, OOD dashboard card), every front-end runs on the lab's central GPU/CPU host instead of individual laptops, and every front-end gets the same auth/file-sharing/monitoring story for free. The shared pipelines package is what makes this consolidation cheap: the same Python `import` works whether the front-end ships as electron or OOD, so the deployment-target switch is independent of the analysis layer.

Plus the underlying pipeline scripts: [`kapurlab/kraken_id_parse_gui`](https://github.com/kapurlab/kraken_id_parse_gui) (fork of USDA-VS) and [`USDA-VS/NAHLN_AMR`](https://github.com/USDA-VS/NAHLN_AMR) (Nextflow AMR pipeline).

Without a shared package, every new tool ends up wrapped 3× — once per front-end. The wrappers drift. The reports diverge. Provenance is patchy.

## 2. Vision

A single pip-installable package `kapurlab-pipelines` containing one module per analysis tool. Each module implements a uniform `AnalysisPrimitive` contract. Every front-end (vsnp_gui, kraken_gui, future OOD apps) becomes a **composer** — it imports primitives, calls `.run()`, and renders the result through `.web()` / `.badge()` / `.latex()`.

The unit of reuse is **the analysis primitive**, not the GUI.

```
kapurlab-pipelines/
├── pipelines/
│   ├── __init__.py
│   ├── common/
│   │   ├── contract.py          # AnalysisPrimitive base class
│   │   ├── provenance.py        # T-07 provenance writer (shared with vsnp_gui)
│   │   ├── badge.py             # T-09 verdict enum + helpers
│   │   ├── report_latex.py      # shared LaTeX section helpers
│   │   ├── report_html.py       # shared HTML section helpers
│   │   └── runners.py           # subprocess / container wrappers
│   ├── kraken.py                # MOVE from kapurlab/kraken_id_parse_gui/bin
│   ├── bracken.py
│   ├── krona.py
│   ├── amrfinder.py             # NEW — port from NAHLN_AMR (this doc's exemplar)
│   ├── sourmash.py              # extract from current vsnp3 ad-hoc usage
│   ├── confindr.py              # NEW
│   ├── checkm.py                # NEW
│   ├── mlst.py                  # port from NAHLN_AMR
│   ├── seqsero2.py              # port from NAHLN_AMR
│   ├── abricate.py              # port from NAHLN_AMR
│   └── assembly.py              # SPAdes / Shovill wrapper, MOVE from kraken_id_parse_gui
└── tests/
    └── fixtures/                # small FASTAs + expected TSVs for each primitive
```

## 3. The shared project workspace

The pipelines package is the *code* abstraction; the **project filesystem** is the *data* abstraction. Both are required. Cards are stateless views over a shared project tree.

### Canonical project layout

There is one project root (`projects_root` in vsnp_gui's config, extended to be the global lab default on wgs3). Every OOD card knows this layout. Every card reads `download/` + `samples.json` + each other's outputs; each writes only its own subtree.

```
projects_root/<project>/
├── download/                       ← fastqs — SHARED INPUT, every card reads from here
├── samples.json                    ← per-sample metadata + accumulated findings,
│                                     SHARED (every card reads, some cards write into it)
│
├── step1/<sample>/                 ← vSNP card writes here
│   ├── alignment_*/                  (BAM, VCF, dedup stats)
│   ├── unmapped_reads/
│   └── .provenance/
├── step2/                          ← vSNP card writes here (tree)
│   └── vcf_source/
│
├── kraken/<sample>/                ← Kraken card writes here
│   ├── *.report
│   ├── *.bracken.S.txt
│   ├── *.bracken.xlsx
│   ├── *_krona.html
│   ├── *_bracken_pie.png
│   └── .provenance/
│
├── assembly/<sample>/              ← WHOEVER assembles writes here, OTHERS REUSE
│   ├── <sample>.fasta                (de novo, primary)
│   ├── <sample>_scaffolds.fasta
│   └── .provenance/
│
├── amr/<sample>/                   ← AMR card writes here
│   ├── <sample>.amr.tsv
│   ├── <sample>.amr.json
│   └── .provenance/
│
├── sourmash/<sample>/              ← Sourmash card writes here
│   ├── <sample>.sig
│   ├── <sample>.containment.tsv
│   └── .provenance/
│
├── mlst/<sample>/, confindr/<sample>/, ...   ← same shape per primitive
│
└── _provenance/                    ← project-level audit
    ├── pipeline_runs/
    └── project_meta.json
```

### Five rules that make sharing work

1. **Fastqs are universal input.** Every card reads from `download/`. Cards never copy or duplicate them; they symlink or pass paths.
2. **Assembly is produced once, consumed many times.** First card that needs `<sample>.fasta` runs SPAdes/Shovill and writes to `assembly/<sample>/`. AMR, MLST, Abricate, and Kraken parse-on-taxon all check `assembly/<sample>/<sample>.fasta` and reuse if present. The `pipelines.common.project.Project.ensure_assembly(sample)` helper encapsulates this — call it from any primitive's `.run()`.
3. **`samples.json` is the shared knowledge base.** Each card writes the structured findings it produces back into the sample's entry. Subsequent cards consult it via `Project.sample_context(sample)`. Example:

   ```json
   {
     "NivediIN107": {
       "fastqs": ["download/NivediIN107_R1.fastq.gz", "download/NivediIN107_R2.fastq.gz"],
       "organism": "Mammaliicoccus sciuri",     // user-supplied or auto-detected
       "host": "Dairy Cattle",
       "isolation_source": "milk",
       // ── accumulated findings (one key per primitive) ──
       "step1": {"reference": "Mammaliicoccus_sciuri_6942A_MS",
                 "mapped_pct": 7.3, "coverage_mean": 9.0},
       "kraken": {"top_species": "Proteus mirabilis",
                  "top_species_pct": 57.1, "polymicrobial": true,
                  "msciuri_pct": 4.3},
       "amr": {"n_genes": 2, "genes": ["mecA1", "sal(A)"],
               "classes": ["BETA-LACTAM", "LINCOSAMIDE/PLEUROMUTILIN/STREPTOGRAMIN"]},
       "sourmash": {"closest_match": "Mammaliicoccus sciuri", "containment": 0.83}
     }
   }
   ```

4. **Provenance is per-tool-dir, schema is uniform.** Each `<tool>/<sample>/.provenance/` directory holds the same shape as vSNP's T-07 work (`record.json`, `exit_code`, `command.sh`, `stdout.log`). A project-wide `verify_provenance.py` walks the whole tree and audits every run.

5. **Cards launch sister cards with context.** Standing in vSNP on project X, sample Y, a "Run AMR on this sample" button opens the AMR OOD card with `?project=X&sample=Y` pre-filled. No re-picking. The kraken_gui's per-sample view gets a "Run AMR" button that does the same. This is one route handler per card (`GET /open?project=...&sample=...`) — trivial to implement uniformly.

### The `Project` helper module

`pipelines.common.project.Project` is the one piece of code that knows the layout. Every primitive uses it; no primitive parses paths by hand.

```python
class Project:
    def __init__(self, path: Path): ...
    @classmethod
    def from_path(cls, path) -> "Project": ...

    # Discovery
    def list_samples(self) -> list[str]: ...
    def fastqs(self, sample) -> tuple[Path, Optional[Path]]: ...
    def sample_context(self, sample) -> dict[str, Any]:
        """Returns the dict that gets passed to Primitive.applicable()."""

    # Shared artifacts
    def assembly_fasta(self, sample) -> Optional[Path]: ...     # None if not assembled yet
    def ensure_assembly(self, sample, *, threads=8) -> Path:
        """Returns the assembly FASTA, running SPAdes/Shovill if it doesn't exist."""

    # Mutation
    def record_finding(self, sample, primitive: str, finding: dict): ...
        """Merges into samples.json[sample][primitive]."""

    # Provenance
    def provenance_dir(self, primitive: str, sample: str) -> Path: ...
    def write_provenance(self, primitive: str, sample: str, record: dict): ...
```

A primitive's `.run()` becomes:

```python
def run(self):
    proj = Project.from_path(self.project_root)
    fa = proj.ensure_assembly(self.sample)         # reuses if already there
    # ... run amrfinder ...
    proj.record_finding(self.sample, "amr",
                        {"n_genes": ..., "genes": ..., "classes": ...})
    proj.write_provenance("amr", self.sample, self.provenance())
```

### Why this matters for the OOD card layout

The dashboard ends up looking like this, and the implementation cost is low because every card is just a focused view over the same project tree:

| Card | What it does | Reads from | Writes to |
|---|---|---|---|
| **vSNP** | step1 alignment + step2 tree | `download/`, `samples.json` | `step1/`, `step2/`, `samples.json[*].step1` |
| **Kraken** | classification + bracken + krona | `download/`, `samples.json` | `kraken/`, `samples.json[*].kraken` |
| **AMR** | AMRFinderPlus on assemblies | `download/`, `assembly/`, `samples.json` | `assembly/` (if not yet present), `amr/`, `samples.json[*].amr` |
| **Sourmash** | k-mer species ID | `download/`, `samples.json` | `sourmash/`, `samples.json[*].sourmash` |
| **MLST** | MLST scheme assignment | `assembly/`, `samples.json` | `mlst/`, `samples.json[*].mlst` |

The user picks a project once. Every card sees it. Every card lights up its findings on the project's sample list (via `.badge()` on each completed primitive). One panel of data, many lenses.

## 4. The `AnalysisPrimitive` contract

Every analysis tool conforms to this interface. The existing kraken pipeline already implements `.run / .latex / .excel`; we extend it with `.web / .badge / .provenance`.

```python
# pipelines/common/contract.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, IO, Mapping

@dataclass
class PrimitiveResult:
    """What every primitive returns from .run().  Front-ends consume this."""
    primitive: str                 # e.g. "amrfinder"
    version: str                   # tool version
    db_version: Optional[str]      # reference DB version, if applicable
    sample: str                    # sample identifier
    outputs: dict[str, Path]       # named output paths (e.g. {"tsv": .../sample.amr.tsv})
    findings: list[dict[str, Any]] # tool-specific records (each row of the TSV, normalized)
    duration_seconds: float
    exit_code: int
    log_path: Optional[Path]

@dataclass
class Badge:
    """T-09 style summary chip rendered by every front-end."""
    label: str                     # short text (e.g. "MecA+")
    verdict: str                   # one of {pass, review, fail, info}
    detail: str                    # 1-2 sentence tooltip explanation
    icon: Optional[str] = None     # optional icon name


class AnalysisPrimitive(ABC):
    """The single contract every analysis tool implements."""

    name: str                      # canonical id, e.g. "amrfinder"
    version: str                   # tool version (filled in by .run())

    # ── lifecycle ────────────────────────────────────────────────────────────
    @abstractmethod
    def run(self) -> PrimitiveResult:
        """Execute the tool, parse outputs, write provenance, return findings."""

    # ── presentation: text / file artifacts ──────────────────────────────────
    @abstractmethod
    def latex(self, tex_handle: IO[str]) -> None:
        """Append a section to the per-sample LaTeX report."""

    @abstractmethod
    def excel(self, excel_dict: dict[str, Any]) -> None:
        """Contribute columns to the per-sample xlsx row."""

    # ── presentation: GUI surfaces (NEW) ─────────────────────────────────────
    @abstractmethod
    def web(self) -> dict[str, Any]:
        """Return a JSON-serializable summary for any GUI to render."""

    @abstractmethod
    def badge(self) -> Badge:
        """Return the per-sample QC badge (T-09 style)."""

    # ── audit (NEW) ──────────────────────────────────────────────────────────
    @abstractmethod
    def provenance(self) -> dict[str, Any]:
        """Return the T-07 provenance record. Written to disk by .run()."""

    # ── classmethods every implementation defines ────────────────────────────
    @classmethod
    @abstractmethod
    def applicable(cls, sample_context: Mapping[str, Any]) -> bool:
        """
        Return True if this primitive can/should run for this sample.
        E.g. amrfinder only runs if an assembly FASTA exists; mlst only runs
        if the organism is in the MLST scheme list.
        """
```

### Why these specific methods

- **`.run()` returns a structured `PrimitiveResult`** rather than just writing files. Front-ends should never have to re-parse a tool's TSV.
- **`.web()` and `.badge()` are new** — they make the same primitive usable in any GUI without duplicating parsing logic. `.badge()` matches the existing vsnp_gui T-09 verdict pattern (`pass / review / fail`).
- **`.provenance()` is non-optional** — every run, every tool, every sample. Same schema as vSNP step1's T-07 (see §6). This is what makes lab notebooks survive Claude sessions, environment changes, and "wait, when did we run this?" 6 months from now.
- **`.applicable(sample_context)` is a classmethod** — lets composers (vsnp_gui, kraken_gui) ask "should I offer to run this on this sample?" without instantiating the primitive. E.g. `MLST.applicable({"organism": "Mammaliicoccus sciuri"})` → False, because MLST has no *M. sciuri* scheme.

## 5. Worked example: AMRFinderPlus

Today (2026-05-12) we installed AMRFinderPlus 4.2.7 on wgs3, ran it on 8 *Mammaliicoccus sciuri* assemblies, and produced a cross-sample matrix manually. This is the first primitive to refactor into the new shape.

### State on wgs3 (smoke-tested, ready to wrap)

- Env: `~/miniforge3/envs/amrfinder/` (conda; `ncbi-amrfinderplus 4.2.7`)
- DB: `~/miniforge3/envs/amrfinder/share/amrfinderplus/data/2026-03-24.1/`
- Test fixtures: `/home/vxk1/projects/Shivasharanappa_panel/synthetic_from_assembly/fasta/{HW110,IN47,IN50,IN107,IN108,IN109,IN185,IN240}.fasta`
- Test outputs (the matrix we want to reproduce): `/home/vxk1/projects/Shivasharanappa_panel/amrfinder/*.amr.tsv` and `/Users/vivekkapur/Downloads/Shivasharanappa_amrfinder/amr_matrix.csv`
- Caveat: *Mammaliicoccus* is **not** in AMRFinder's `-O` organism list. Run in generic mode (acquired genes only); do **not** pass `-O Staphylococcus_aureus` even though it's the closest relative — point-mutation calls would be misleading on a different genus.

### Sketch: `pipelines/amrfinder.py`

```python
from pathlib import Path
import csv, json, time, subprocess
from typing import Any, Optional, IO, Mapping
from .common.contract import AnalysisPrimitive, PrimitiveResult, Badge
from .common.provenance import write_provenance
from .common.runners import run_in_conda_env

CLASS_COLORS = {
    "BETA-LACTAM": "#d1495b",
    "TETRACYCLINE": "#2b6cb0",
    "AMINOGLYCOSIDE": "#3a7d44",
    "LINCOSAMIDE/PLEUROMUTILIN/STREPTOGRAMIN": "#7a4e9c",
    "ARSENIC": "#7a4f00",
    "CADMIUM": "#5a5a5a",
    # ... full mapping in code
}

class AMRFinder(AnalysisPrimitive):
    name = "amrfinder"
    def __init__(self, FASTA: Path, *, threads: int = 16,
                 env: str = "amrfinder", db_dir: Optional[Path] = None,
                 out_dir: Optional[Path] = None, organism: Optional[str] = None):
        self.FASTA = Path(FASTA)
        self.sample = self.FASTA.stem
        self.threads = threads
        self.env = env
        self.db_dir = db_dir
        self.out_dir = Path(out_dir or self.FASTA.parent / "amrfinder")
        self.organism = organism   # None = generic mode (default for non-supported organisms)
        self.result: Optional[PrimitiveResult] = None

    @classmethod
    def applicable(cls, sample_context: Mapping[str, Any]) -> bool:
        # Need an assembly FASTA in the sample context.
        return "assembly_fasta" in sample_context and Path(sample_context["assembly_fasta"]).exists()

    def run(self) -> PrimitiveResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        tsv = self.out_dir / f"{self.sample}.amr.tsv"
        log = self.out_dir / f"{self.sample}.amr.log"
        cmd = ["amrfinder", "-n", str(self.FASTA), "--plus",
               "--threads", str(self.threads), "-o", str(tsv)]
        if self.organism:
            cmd += ["-O", self.organism]
        start = time.time()
        cp = run_in_conda_env(self.env, cmd, log_path=log)
        duration = time.time() - start

        findings = []
        if tsv.exists():
            with tsv.open() as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    findings.append({
                        "gene": row["Gene symbol"],
                        "name": row["Sequence name"],
                        "scope": row["Scope"],
                        "type": row["Element type"],
                        "class": row["Class"],
                        "subclass": row["Subclass"],
                        "method": row["Method"],
                        "identity": float(row["% Identity to reference sequence"]),
                        "coverage": float(row["% Coverage of reference sequence"]),
                    })

        # Version + DB version probe (run once per .run())
        self.version = self._tool_version()
        db_version = self._db_version()

        self.result = PrimitiveResult(
            primitive=self.name,
            version=self.version,
            db_version=db_version,
            sample=self.sample,
            outputs={"tsv": tsv},
            findings=findings,
            duration_seconds=duration,
            exit_code=cp.returncode,
            log_path=log,
        )
        write_provenance(self.out_dir, self.provenance())
        return self.result

    def web(self) -> dict[str, Any]:
        assert self.result, "call .run() first"
        return {
            "primitive": self.name,
            "sample": self.sample,
            "n_findings": len(self.result.findings),
            "findings": self.result.findings,
            "class_colors": CLASS_COLORS,
            "tsv_url": str(self.result.outputs["tsv"]),
        }

    def badge(self) -> Badge:
        assert self.result, "call .run() first"
        f = self.result.findings
        if not f:
            return Badge(label="AMR: none", verdict="pass",
                         detail="No acquired AMR genes detected.")
        # Highlight beta-lactam / methicillin specifically (clinically major)
        mec = [x for x in f if x["gene"].lower().startswith("meca")]
        if mec:
            return Badge(label=f"MRSA-like ({len(f)} genes)", verdict="review",
                         detail=f"MecA family hit ({mec[0]['gene']}) + "
                                f"{len(f)-1} other AMR gene(s) detected.")
        return Badge(label=f"AMR: {len(f)} gene(s)", verdict="review",
                     detail=", ".join(x["gene"] for x in f[:6]))

    def latex(self, tex: IO[str]) -> None:
        from .common.report_latex import section_banner, longtable
        section_banner(tex, "Antimicrobial Resistance (AMRFinderPlus)")
        if not self.result.findings:
            tex.write(r"\noindent No acquired AMR genes detected.\\[1em]" + "\n")
            return
        rows = [(f["gene"], f["class"], f["subclass"],
                 f"{f['identity']:.1f}%", f"{f['coverage']:.1f}%")
                for f in self.result.findings]
        longtable(tex, headers=["Gene","Class","Subclass","%ID","%Cov"], rows=rows)

    def excel(self, d: dict[str, Any]) -> None:
        d["AMR n_genes"] = len(self.result.findings)
        d["AMR genes"]   = "; ".join(f["gene"] for f in self.result.findings)
        d["AMR classes"] = "; ".join(sorted({f["class"] for f in self.result.findings}))

    def provenance(self) -> dict[str, Any]:
        return {
            "primitive": self.name,
            "version": self.version,
            "db_version": self.result.db_version if self.result else None,
            "sample": self.sample,
            "fasta": str(self.FASTA),
            "fasta_sha256": _sha256(self.FASTA),
            "command": "amrfinder -n ... --plus",
            "exit_code": self.result.exit_code if self.result else None,
            "duration_seconds": self.result.duration_seconds if self.result else None,
            "outputs": {k: str(v) for k,v in (self.result.outputs.items() if self.result else {})},
            "host": _hostname(),
            "ran_at_utc": _now_utc_iso(),
        }
```

### What today's manual run already proves

- Generic mode (no `-O`) gives clean acquired-gene calls on *M. sciuri*: `mecA1` and `sal(A)` universal across the 8 isolates, scattered heavy-metal genes.
- 5 seconds per genome on 16 threads with `--memory-mapping` (DB hot).
- The TSV format is stable across versions (Gene symbol / Class / Subclass / Method / %Identity / %Coverage / etc.) — safe to parse positionally.

These become the unit test for `pipelines/amrfinder.py`: feed the 8 NivediXXX FASTAs, assert the resulting `findings` list matches the matrix.csv we already produced.

## 6. Provenance schema (extends T-07)

vsnp_gui's T-07 work defined the per-step provenance pattern: a `.provenance/` directory inside each output folder containing a JSON record + an exit-code sentinel. Every primitive in this package follows the same schema:

```
<output_dir>/
├── .provenance/
│   ├── record.json            # the dict returned by .provenance()
│   ├── exit_code              # text file with the exit code (T-07 sentinel pattern)
│   ├── command.sh             # the actual shell command run, reproducible
│   └── stdout.log / stderr.log
├── {sample}.amr.tsv           # primary output
└── ... (other tool outputs)
```

`record.json` schema (one canonical shape across all primitives):

```json
{
  "primitive": "amrfinder",
  "version": "4.2.7",
  "db_version": "2026-03-24.1",
  "sample": "NivediIN109",
  "inputs": {
    "fasta": "/.../IN109.fasta",
    "fasta_sha256": "abc123..."
  },
  "command": "amrfinder -n /.../IN109.fasta --plus --threads 16 -o IN109.amr.tsv",
  "outputs": {
    "tsv": "/.../IN109.amr.tsv"
  },
  "exit_code": 0,
  "duration_seconds": 5.4,
  "host": "a8-an-vxk1-u5",
  "ran_at_utc": "2026-05-12T14:23:11Z",
  "container": null,                              // or "amrfinder.sif" if Apptainer
  "env": "conda:amrfinder",                       // or "apptainer:..."
  "parent_primitive_run": "vsnp_step1:<run_id>"   // optional, for chained workflows
}
```

A `verify_provenance.py` script (already exists in vsnp_gui for T-07) generalizes to scan all primitive output dirs and assert (a) record.json present, (b) exit_code sentinel matches `record.json.exit_code`, (c) the SHA256 of each declared input still resolves.

## 7. Container / env strategy

One conda env (or apptainer image) per primitive. Mixing tools in one mega-env is fragile; the AMRFinder install on wgs3 took 26s in its own env, no conflicts.

Recommended pattern (matches wgs3 today):

| Tool | Env name | Source |
|---|---|---|
| amrfinder | `conda:amrfinder` | `ncbi-amrfinderplus` (bioconda) |
| kraken / bracken / krona | `conda:kraken_report` | bioconda (already installed) |
| sourmash | `conda:sourmash` (or reuse `vsnp3`) | bioconda |
| mlst | `conda:mlst` | bioconda |
| seqsero2 | `conda:seqsero2` | bioconda |
| abricate | `conda:abricate` | bioconda |
| confindr | `conda:confindr` | bioconda |
| checkm | `conda:checkm` | bioconda |
| spades / shovill | `conda:assembly` | bioconda |

The `common/runners.py` module exposes one helper:

```python
def run_in_conda_env(env: str, cmd: list[str], *, log_path: Path) -> CompletedProcess:
    """Run cmd inside the named conda env. Captures stdout/stderr to log_path."""
```

This is the **one place** that knows how to activate envs. Switching to Apptainer later is a single-file change.

## 8. How front-ends consume primitives

### vsnp_gui (after step1 completes)

```python
# backend/app/main.py (sketch)
from pipelines import AMRFinder, Kraken, Sourmash
from pipelines.common.contract import AnalysisPrimitive

POST_STEP1_PRIMITIVES = [Sourmash, Kraken, AMRFinder]

def run_post_step1(project, sample):
    ctx = sample_context(project, sample)  # has assembly_fasta, fastqs, organism, etc.
    results = []
    for cls in POST_STEP1_PRIMITIVES:
        if cls.applicable(ctx):
            primitive = cls(**cls.build_kwargs(ctx))
            results.append(primitive.run())
    return results

@app.get("/api/projects/{project}/samples/{sample}/badges")
def sample_badges(project, sample):
    return [p.badge().__dict__ for p in load_completed_primitives(project, sample)]
```

The T-09 badge layer just iterates every completed primitive's `.badge()` — no per-tool special-casing in the GUI.

### kraken_gui (re-deployed as an OOD batch_connect app)

Same import. Same `.latex(tex)` and `.excel(d)` calls into each primitive. The only thing that changes vs. today's electron build is the deployment surface — the React frontend gets served by Apache/Passenger inside an OOD session instead of bundled into an electron binary, and the FastAPI backend runs as a regular batch_connect process on wgs3.

The existing per-sample LaTeX/Excel pipeline calls each primitive's `.latex(tex)` and `.excel(d)` — the kraken_gui's report code becomes:

```python
for primitive in selected_primitives:
    p = primitive(...)
    p.run()
    p.latex(latex_report.tex)
    p.excel(excel_stats.excel_dict)
```

### Standalone CLI

Every primitive should expose a `__main__` block so it remains a usable command-line tool (matches the kapurlab fork's current shape):

```bash
python -m pipelines.amrfinder --fasta IN109.fasta --threads 16 --out amr_results/
```

This is non-negotiable: nobody should have to fire up a GUI to run AMRFinder on one assembly.

## 9. Migration path (incremental, no big bang)

Each step is independently shippable. Each ends with the system *more* unified, never less.

| Step | Effort | Deliverable | Demonstrates |
|---|---|---|---|
| 1 | ~2 hr | `pipelines/amrfinder.py` implementing the full contract, plus tests using today's 8 NivediXXX FASTAs as fixtures. Lives initially **inside** `vsnp_gui/backend/app/pipelines/`. | The contract is implementable; LaTeX/Excel/web/badge methods are useful. |
| 2 | ~30 min | vsnp_gui exposes a "Run AMR" button on step1 result pages → calls `AMRFinder.run()` → renders `.web()` in the GUI + adds `.badge()` to the sample card. | A front-end consumes a primitive end-to-end. |
| 3 | ~1 hr | When primitive #2 (sourmash) follows the same contract, extract `pipelines/` into its own repo (`kapurlab-pipelines`), publish as editable pip install. | The package boundary holds; two primitives share `common/`. |
| 4 | ~2 hr | Move kraken / bracken / krona from `~/kraken/pipeline/bin/` into the shared package. The kraken_gui keeps running but `import`s them from the package. | Existing tools fit the contract; kapurlab fork can deprecate `bin/`. |
| 5 | ~1 hr each | Port mlst, seqsero2, abricate, confindr, checkm from NAHLN_AMR's wrappers into the package, one per primitive. | The pattern scales; NAHLN_AMR becomes a vendored reference rather than a runtime dep. |
| 6 | ~3 hr | Re-deploy kraken_gui as an OOD batch_connect app on wgs3 — same FastAPI/React code, dropped electron wrapper, new dashboard card alongside vsnp_gui. | OOD-native parity with vsnp_gui; electron build retired. |
| 7 | ~3 hr | Optional: standalone NAHLN_AMR-style OOD card driven by the shared package (no Nextflow needed once primitives are individually wrapped). | OOD layer is no harder than the GUI layer. |
| 8 | ongoing | Each new tool follows the template. ~1 hr / tool. | Net effort drops over time. |

## 10. What we explicitly don't do

- **Adopt Nextflow as the orchestration layer.** Tempting because NAHLN_AMR uses it, but adds a DSL + runtime your team doesn't otherwise need. Reach for it only if/when multi-step DAGs genuinely justify it.
- **Merge kraken_gui into vsnp_gui.** They serve different workflows. Let them be peer **OOD-deployed** consumers of the shared package — different cards on the same dashboard.
- **Ship any new electron builds.** All new GUIs are OOD batch_connect apps on wgs3 from day 1. The existing kraken_gui electron build is legacy and gets retired once the OOD version is up.
- **Rewrite the kapurlab fork from scratch.** The existing class structure already implements `.run / .latex / .excel`. Refactor in place, don't start over.
- **Treat the package as a separate product.** It's an internal library for our front-ends. No external docs, no PyPI publish, no versioning theatrics — just `pip install -e ./kapurlab-pipelines` from a git clone.

## 11. Open questions

These need to be answered as Step 1 lands; capturing them so we don't forget.

1. **Where does `pipelines/` initially live?** Options: (a) inside `vsnp_gui/backend/app/pipelines/`, then extract when a second front-end imports it. (b) Standalone repo from day 1. **Recommend (a)** — defer the package extraction until there's a real second consumer.
2. **How do primitives get their conda env names?** Hard-coded in each class today (`env: str = "amrfinder"`). Eventually parameterized via a config (`~/.config/kapurlab-pipelines/envs.yaml`) so a user with different env names doesn't have to patch code.
3. **Sample context schema.** What's the canonical dict passed to `applicable(sample_context)`? Needs at minimum: `assembly_fasta`, `fastqs`, `organism`, `coverage`, `kraken_top_species`. Define this in `common/contract.py` as a dataclass — call it `SampleContext`.
4. **Badge ordering / aggregation.** When a sample has 5 primitives' worth of badges, the GUI needs an order. T-09 already defines `pass < review < fail` priority. Carry that.
5. **Versioning the contract.** When we eventually add a method (e.g. `.html(handle)`), how do we avoid breaking older primitives? Recommendation: ABC method with a default no-op implementation; never remove an existing method.
6. **Error surface.** When a primitive fails (tool missing, DB missing, FASTA invalid), what shape of exception does it raise? Define `PrimitiveError` and `PrimitiveSetupError` in `common/contract.py`. Front-ends catch these and render a `verdict=fail` badge.

## 12. References

- USDA-VS: [vSNP3](https://github.com/USDA-VS/vSNP3), [NAHLN_AMR](https://github.com/USDA-VS/NAHLN_AMR) (Nextflow + Apptainer AMR pipeline — vendored reference for tool wrappers)
- Kapurlab: [kraken_id_parse_gui](https://github.com/kapurlab/kraken_id_parse_gui) (current pipeline scripts; source of `.run/.latex/.excel` pattern)
- This repo: T-07 (provenance writer), T-09 (QC badges) — already implement the patterns this doc generalizes.

## 13. Today's worked example, in one place

So the next session can pick up cleanly without re-running anything:

- **AMRFinder env on wgs3:** `~/miniforge3/envs/amrfinder/`, AMRFinder v4.2.7, DB 2026-03-24.1
- **Test fixtures (input):** `/home/vxk1/projects/Shivasharanappa_panel/synthetic_from_assembly/fasta/{HW110,IN47,IN50,IN107,IN108,IN109,IN185,IN240}.fasta`
- **Reference outputs:** `/home/vxk1/projects/Shivasharanappa_panel/amrfinder/{HW110,...}.amr.tsv` (8 files) and `/Users/vivekkapur/Downloads/Shivasharanappa_amrfinder/amr_matrix.csv`
- **Expected matrix (the target for `pipelines/amrfinder.py` regression tests):**

  ```
  Gene       Class           HW110  IN47   IN50   IN107  IN108  IN109  IN185  IN240
  mecA1      BETA-LACTAM      +      +      +      +      +      +      +      +
  sal(A)     LINC/PLEU/STREP  +      +      +      +      +      +      +      +
  arsB       ARSENIC          +      .      .      .      .      .      .      +
  arsC       ARSENIC          +      +      +      .      .      .      .      .
  cadD       CADMIUM          .      .      +      .      .      .      .      .
  ```

- **Caveat to encode in the wrapper:** *Mammaliicoccus* is not in AMRFinder's `-O` list. Run generic; do not pass `-O Staphylococcus_aureus`.

When Step 1 of the migration path lands, this matrix is what its tests should reproduce byte-for-byte (modulo column ordering).
