# deploy/ — installing the vSNP GUI platform

**Installing at a new site? Start with the roadmap:**
[`../docs/deploy/INSTALL_OOD.md`](../docs/deploy/INSTALL_OOD.md).

The platform is a four-layer stack (OS+storage → OOD core → vSNP toolchain →
vSNP OOD app). Everything site-specific lives in one file, `site.conf`; the
installers substitute your values — the repo files are never edited per-site.

## The install path (bare box → working GUI with a demo project)

```bash
cp site.conf.example site.conf && $EDITOR site.conf

sudo ./bootstrap_ood_core.sh --dry-run   # layers 1–2: Open OnDemand core
sudo ./bootstrap_ood_core.sh

sudo ./install_ood.sh --dry-run          # layers 3–4: vSNP toolchain + OOD app
sudo ./install_ood.sh

cd sample_data
./build_bundle.sh --from <ref-server> --tar          # package the demo dataset
sudo ./install_bundle.sh --site-conf ../site.conf --user <admin>
```

Then run the acceptance test in
[`../docs/deploy/INSTALL_OOD.md` §7](../docs/deploy/INSTALL_OOD.md).

## What's here

| Path | Role |
|---|---|
| `site.conf.example` | every site-specific knob (copy → `site.conf`) |
| `bootstrap_ood_core.sh` | installs Open OnDemand core on a bare Ubuntu box (layers 1–2) |
| `install_ood.sh` | installs the vSNP toolchain + OOD app, parameterized (layers 3–4) |
| `sample_data/` | the out-of-the-box demo dataset (reference + runnable project) |
| `ood/` | the OOD app card + cluster/portal config (source of truth; `install_ood.sh` deploys these) |
| `admin/` | per-user / per-project admin scripts (deployed renamed as `${SITE_NAME}-*`) |
| `vsnp3-patches/` | Kapur Lab patches applied to the bioconda vsnp3 env |

## Reference docs

- [`../docs/deploy/INSTALL_OOD.md`](../docs/deploy/INSTALL_OOD.md) — the full runbook.
- [`../docs/deploy/WGS3_AUDIT.md`](../docs/deploy/WGS3_AUDIT.md) — the reference-install
  audit + the complete literal→`site.conf` parameterization map.
- `ood/README.md`, `admin/README.md`, `vsnp3-patches/README.md` — per-subsystem detail.

> **Status: DRAFT** — the installers are reconstructed from the wgs3 reference
> install and the repo, and have not yet been run end-to-end on a clean box.
> First install on a test box, `--dry-run` first.
