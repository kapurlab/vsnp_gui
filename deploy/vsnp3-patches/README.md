# vsnp3 patches

Local patches we apply on top of the bioconda vsnp3 v3.16 install. These belong upstream and are filed under [USDA-VS/vSNP3#22](https://github.com/USDA-VS/vSNP3/issues/22) and [#23](https://github.com/USDA-VS/vSNP3/issues/23); until those merge, we carry them here.

`mamba install` / `conda update` will overwrite the patched files. Reapply with `apply.sh`.

## Patches

- `v3.16-kapurlab.patch` — unified diff against pristine v3.16:
  - **column[0] → column.iloc[0]** (3 sites in `bin/vsnp3_fasta_to_snps_table.py`). Fixes `KeyError: 0` on pandas 2.x. Tracked at USDA-VS/vSNP3#22.
  - **`VSNP3_BOOTSTRAP` env var support**. When set (>0), RAxML runs as `-f a -x 7777 -N $VSNP3_BOOTSTRAP` and the pipeline picks `RAxML_bipartitions.raxml` (best tree with bootstrap proportions on internal branches) instead of `RAxML_bestTree.raxml`. Tracked at USDA-VS/vSNP3#23.

## Apply

```bash
deploy/vsnp3-patches/apply.sh /srv/kapurlab/tools/vsnp3
```

Idempotent (skips if already applied). Run after every `mamba install`/`conda update` of vsnp3.
