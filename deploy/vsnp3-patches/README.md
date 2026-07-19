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

`apply.sh` applies both patches in order, uses `VSNP3_MAX_CPUS` (from the second patch) as its idempotency sentinel, and uses `patch -N` so installs already carrying earlier hunks pick up only the new ones.

## Apply

```bash
deploy/vsnp3-patches/apply.sh /srv/kapurlab/tools/vsnp3
```

Idempotent (skips if already applied). Run after every `mamba install`/`conda update` of vsnp3.
