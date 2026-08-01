# vsnp3 patches

Local patches we apply on top of the bioconda vsnp3 v3.16 install. These belong upstream and are filed under [USDA-VS/vSNP3#22](https://github.com/USDA-VS/vSNP3/issues/22) and [#23](https://github.com/USDA-VS/vSNP3/issues/23); until those merge, we carry them here.

`mamba install` / `conda update` will overwrite the patched files. Reapply with `apply.sh`.

## Patches

- `v3.16-kapurlab.patch` — unified diff against pristine v3.16:
  - **column[0] → column.iloc[0]** (3 sites in `bin/vsnp3_fasta_to_snps_table.py`). Fixes `KeyError: 0` on pandas 2.x. Tracked at USDA-VS/vSNP3#22.
  - **`VSNP3_BOOTSTRAP` env var support**. When set (>0), RAxML runs as `-f a -x 7777 -N $VSNP3_BOOTSTRAP` and the pipeline picks `RAxML_bipartitions.raxml` (best tree with bootstrap proportions on internal branches) instead of `RAxML_bestTree.raxml`. Tracked at USDA-VS/vSNP3#23.
  - **SyntaxWarning fixes** (one site each in `bin/vsnp3_step1.py` and `bin/vsnp3_step2.py`): convert the affected regex literals to raw strings and drop the bogus `\/` escape. Cosmetic — Python still ran the patterns — but every parallel worker emitted a noisy warning at module load. Will become a hard error in a future Python release; raw-string is the correct fix.

- `v3.16-kapurlab-step2-robustness.patch` — applies on top of the base patch:
  - **Degenerate-group guards** (`bin/vsnp3_group_on_defining_snps.py`). On a dataset with a rare/singleton lineage, a group can end up with a sample whose per-group dataframe is empty. Upstream crashes the *entire* Step 2 run at the FASTA/RAxML stage — first `KeyError: "None of ['abs_pos']..."` in `dict_to_dataframe` (its sibling `dict_to_fasta` already guards this), then `ValueError: No objects to concatenate` when a whole group filters out, then `AttributeError: 'NoneType' object has no attribute 'read'` in `raxml_table_build` when a group has no alignment. Patch: skip samples with no `abs_pos`, return `None` for an empty group, and skip degenerate groups in `raxml_table_build` (vsnp3 already writes them `TOO_FEW_SAMPLES` markers). Verified: full 1024-sample MTBC run that previously died at ~32 min now builds all group SNP tables + trees.
  - **CPU cap** (`vsnp3_step2.py`, `vsnp3_group_on_defining_snps.py`, `vsnp3_fasta_to_snps_table.py`). vsnp3 sizes its worker pools at `int(cpu_count()/1.2)` — 106 on a 128-core box, which hammers a shared server. Now capped at `min(cpu_count()/1.2, VSNP3_MAX_CPUS or 32)`. Cross-platform (Mac/Linux/WSL); never bites on laptops (`8/1.2 = 6 < 32`). Set `VSNP3_MAX_CPUS` to override.
  - File upstream alongside #22/#23.

## Content fixes (version-agnostic)

Applied by `apply.sh` before the `.patch` loop, against expressions that are identical across point releases but sit at different line numbers. A line-context diff is too fragile for these; a content match is version-proof.

- **passQ comma fix** (`bin/vsnp3_step1.py`). `float(fastq_stats.R1.passQ20)` raises `ValueError` on comma-formatted counts (`'9,177'`), failing the whole sample — and it looks like a bcftools error, because the traceback lands after the mpileup log. Strips the thousands separator first.

- **Minus-strand amino acid calls** (`bin/vsnp3_annotation.py`, via `strandfix.py`). Reported by Vivek Kapur, 2026-08-01. There is no `reverse_complement()` anywhere in the module in either v3.16 or v3.35: the codon is sliced from the plus strand and translated as-is, and the variant base is substituted at the plus-strand index within the codon. For a gene on the minus strand that yields the wrong reference and variant amino acid, and the silent/nonsynonymous call lands near chance. Roughly half of MTBC genes are minus strand, so this touches about half of all coding SNPs in every TB run, with no signature that anything is wrong.

  Measured on 600 sampled H37Rv SNPs (`sim_strandfix.py`, positions covered by exactly one single-part CDS):

  | | n | ref+alt AA correct | silent/nonsyn correct |
  |---|---|---|---|
  | plus, before and after | 300 | 97.0% | 99.3% |
  | minus, **before** | 300 | **0.0%** | **53.3%** |
  | minus, **after** | 300 | 95.3% | 100.0% |

  The residual few percent on both strands is the truth model in `sim_strandfix.py` (start codons read from `/translation` as `M` where a naive `translate()` gives `V`/`L`), not the patch — the plus-strand figure is unchanged by it.

  The two releases restructured this function completely, so `strandfix.py` is a small content-anchored rewriter rather than a diff. It recognises both shapes, emits the correction each needs, stamps `__version__` with `+kl.strandfix1`, keeps a `.pre-strandfix` backup, and **refuses** rather than guesses on an unrecognised release. It writes via rename, not in place: conda hardlinks env files to the package cache under `pkgs/`, so an in-place write would silently patch the cache and every future env unpacked from it.

  v3.35 needs only the codon handling — it already derives `aa_residue_pos` correctly on the minus strand and takes `ref_aa` from the codon table. v3.16 needs the residue index too, since it indexes into `/translation` from the wrong end.

  Verify with `test_strandfix.py` (Vivek's two positive controls, rpoB S450L on the plus strand and katG S315T on the minus) or `sim_strandfix.py [n]` for the sampled run. Both take the module from `PYTHONPATH`:

  ```bash
  PYTHONPATH=/srv/kapurlab/tools/vsnp3/bin /srv/kapurlab/tools/vsnp3/bin/python3 deploy/vsnp3-patches/test_strandfix.py
  ```

  Still to do: file upstream alongside #22/#23.

## Notes

`apply.sh` applies the patches in order, uses `VSNP3_MAX_CPUS` (from the second patch) as its idempotency sentinel, and uses `patch -N` so installs already carrying earlier hunks pick up only the new ones.

The `.patch` set is v3.16-only. `apply.sh` reads `__version__` from `vsnp3_step1.py` and skips the whole set on any other release rather than emitting a wall of FAILED hunks and `.rej` files — the content fixes above still run. We deploy two different releases: `/srv/kapurlab/tools/vsnp3` is v3.16, and the bdtools checkout env is v3.35.

**Which install actually runs matters.** `build_env()` in `backend/app/main.py` prepends `dirname(bcftools_path)` to `PATH`, and the Step 1 script invokes bare `vsnp3_step1.py` — so the install selected by `bcftools_path` is the one that runs, *not* the one named by `vsnp3_path`. Patch both.

## Apply

```bash
deploy/vsnp3-patches/apply.sh /srv/kapurlab/tools/vsnp3
```

Idempotent (skips if already applied). Run after every `mamba install`/`conda update` of vsnp3.
