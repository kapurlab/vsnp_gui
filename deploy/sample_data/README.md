# Sample data bundle — out-of-the-box demo

A tiny, self-contained SARS-CoV-2 dataset so a fresh install has something
**runnable** immediately. It is the install's acceptance test: if you can run
this project's Step 1 → Step 2 and see a tree, the platform works.

## What's in it

| Item | Size | Source |
|---|---|---|
| `bundle/refs/NC_045512_wuhan-hu-1/` | ~150 KB | wgs3 reference set (SARS-CoV-2 Wuhan-Hu-1) |
| `bundle/projects/demo_sars_cov_2/download/` | ~2.4 MB | 6 deer SARS-CoV-2 samples, paired fastq.gz (~200 KB each) |
| `bundle/projects/demo_sars_cov_2/project.json` | — | points the project at the reference above |

Total ≈ 2.5 MB. **Raw fastqs only** by default — no pre-built outputs, so the
user runs the pipeline fresh and the result carries correct local paths.
(The wgs3 pre-built step1/step2 bake in absolute `/srv/kapurlab/...` paths, so
they aren't shipped unless you pass `--with-results`, which is for a
view-only demo, not a clean run.)

## Two scripts

### `build_bundle.sh` — package it (run once, needs the reference server)

Pulls the reference + raw fastqs from a reference install (wgs3), sanitizes
`project.json`, and stages `bundle/` (+ optional tarball). You run this; the
result ships inside the distributable.

```bash
./build_bundle.sh --from vxk1@kapurlab-wgs3.tailf38ff4.ts.net
# -> deploy/sample_data/bundle/ and vsnp-sample-bundle.tar.gz
```

### `install_bundle.sh` — place it on the target (offline, needs no network)

Copies the staged `bundle/` onto a freshly-installed box: reference into
`${SITE_ROOT}/refs/...` (and registers it), project into a user's projects
dir. Run after `install_ood.sh`.

```bash
sudo ./install_bundle.sh --site-conf ../site.conf --user <login>
```

## Acceptance test (the whole point)

1. Log into OOD as `<login>`, launch the vSNP GUI card.
2. Open project **demo_sars_cov_2** → it's pre-pointed at `NC_045512_wuhan-hu-1`.
3. **Run Step 1** on all 6 samples (~1–2 min — tiny amplicon genomes).
4. **Run Step 2** → cascade/sorted tables + a 6-leaf tree render.
5. Hover a variant cell → IGV opens with reads + calls.

All green ⇒ layers 1–4 are working end to end.
