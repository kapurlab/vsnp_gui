# Installing the vSNP GUI OOD platform at a new site

> The roadmap for taking a **bare Linux box** to a **working multi-user vSNP
> GUI with a runnable sample project**. Written for a sysadmin. Built and
> validated against the wgs3 reference install (see
> [`WGS3_AUDIT.md`](WGS3_AUDIT.md)); first authored for the **NIVEDI** port
> (T-49 / T-50).
>
> **Status: DRAFT.** The scripts are reconstructed from wgs3 + the repo and
> have not yet been run end-to-end on a clean box. Do the **first install on
> a throwaway/test box** (and use `--dry-run` first). The Kapur Lab is
> validating this on a bare server (wgs1) before NIVEDI.

---

## 0. What you're installing — the four-layer cake

The platform is four layers stacked. **You install them bottom-up.** Only the
top two are "ours"; the bottom two are standard infrastructure.

| Layer | What | Tool | Needs root |
|---|---|---|---|
| 1. OS + storage | Ubuntu 24.04 + an XFS `prjquota` data disk | manual (§2) | yes |
| 2. OOD core | Open OnDemand 3.1, Apache+PAM, Apptainer | `bootstrap_ood_core.sh` (§3) | yes |
| 3. vSNP toolchain | conda `vsnp3` env, refs, frontend build | `install_ood.sh` (§4) | yes |
| 4. vSNP OOD layer | the app card, cluster cfg, portal, admin tools | `install_ood.sh` (§4) | yes |
| + demo | one reference + a runnable sample project | sample bundle (§5) | yes |

Everything site-specific (paths, group names, hostnames, branding) lives in
**one file: `deploy/site.conf`**. Nothing in the repo is edited per-site —
the installers substitute your values on the way out. The full literal→knob
map is [`WGS3_AUDIT.md` §5](WGS3_AUDIT.md#5-parameterization-map-the-literals--siteconf-variables).

---

## 1. Prerequisites & decisions

**Hardware (NIVEDI target):** 64 CPU / 500 GB RAM / 20 TB. vSNP/SPAdes is
RAM-hungry on large genomes (TB/Brucella) — plan ≥8 GB/concurrent-sample.

**OS:** Ubuntu **24.04 LTS (noble)**. The OOD apt repo is codename-keyed; a
different release means a different repo URL (the bootstrap warns you).

**Network / auth — a decision you must make:**
- wgs3 runs **plain HTTP (port 80) + PAM basic-auth** over a trusted tailnet.
  Local Linux accounts authenticate directly (`pam_unix`).
- If your network is less trusted, **add TLS** (`ssl:` block in
  `ood_portal.yml`) before go-live. Basic-auth over plain HTTP sends
  credentials in the clear. See [`WGS3_AUDIT.md` §7](WGS3_AUDIT.md).

**Already have OOD?** (e.g. an institutional cluster like PSU Roar.) Then
**skip layers 1–2** — you don't own them. You'd deploy only layers 3–4, and
likely as a *user-space* app under a different job adapter (Slurm) and
institutional auth. That path is a separate port, not covered here; this
runbook assumes a box you control.

**Get the code + a site.conf:**

```bash
git clone https://github.com/kapurlab/vsnp_gui.git
cd vsnp_gui/deploy
cp site.conf.example site.conf
$EDITOR site.conf       # set SITE_NAME, SITE_ROOT, ADMIN_USER, SERVERNAME, ...
```

Minimum you must set: `SITE_NAME`, `SITE_DISPLAY`, `SITE_ROOT`, `ADMIN_USER`,
`SERVERNAME`. Everything else has a sensible default.

---

## 2. Layer 1 — storage (manual, one-time)

The shared data root (`SITE_ROOT`, e.g. `/srv/nivedi`) should be its **own
XFS filesystem mounted with `prjquota`** — that's what per-project disk quotas
need. On wgs3:

```
/etc/fstab:
UUID=...  /srv/nivedi         xfs  defaults,prjquota  0 2
UUID=...  /srv/nivedi/backup  xfs  defaults,prjquota  0 2   # separate disk
/home also XFS with usrquota,prjquota
```

Provisioning disks/partitions is hardware-specific — adapt the wgs3 procedure
in [`../dev/runbooks/T-19-storage-layout.md`](../dev/runbooks/T-19-storage-layout.md).
If `SITE_ROOT` isn't XFS+prjquota the install still works; project quotas just
degrade to a no-op (the scripts warn).

> The installer's `preflight` phase checks this and tells you what's missing.

---

## 3. Layer 2 — OOD core (`bootstrap_ood_core.sh`)

Reproduces wgs3's OnDemand 3.1.16 install: OSC apt repo → `ondemand` package,
Apache + PAM basic-auth, Apptainer, the session image, and the portal config.

```bash
cd vsnp_gui/deploy
sudo ./bootstrap_ood_core.sh --dry-run        # review every step
sudo ./bootstrap_ood_core.sh                  # apply
```

Steps (re-runnable individually): `base apptainer ondemand image portal verify`.

**One manual input it needs:** the 30 MB `ood_default.sif` session image is
**not** shipped by the `ondemand` package. Easiest reliable path — copy the
proven one from a working install by setting `OOD_SIF_SOURCE` in `site.conf`:

```ini
OOD_SIF_SOURCE=vxk1@kapurlab-wgs3.tailf38ff4.ts.net:/opt/ood/ondemand/ood_default.sif
```

(Or include it in the distributable tarball and point `OOD_SIF_SOURCE` at the
local path.)

**Success check:** the `verify` step curls `http://localhost/` and expects a
**401** (the basic-auth challenge) — that means OOD core is up. Then create
the admin account:

```bash
sudo useradd -m -s /bin/bash <admin> && sudo passwd <admin>
```

(Make this `ADMIN_USER` from your `site.conf`.)

---

## 4. Layers 3–4 — the vSNP platform (`install_ood.sh`)

```bash
cd vsnp_gui/deploy
sudo ./install_ood.sh --dry-run               # review
sudo ./install_ood.sh                         # apply
```

Phases (re-runnable individually, all idempotent):

| Phase | Does |
|---|---|
| `preflight` | verifies layers 1–2, node, conda, mount/quota |
| `groups` | creates `${SITE_NAME}-members` + `${SITE_NAME}-admins` |
| `storage` | creates the `${SITE_ROOT}` subtree (setgid, right groups) |
| `toolchain` | `conda create vsnp3` + backend pip deps + vsnp3 patches + registers refs + builds the frontend |
| `refs` | prints the rsync command for the full 27-reference set (data, not auto-pulled) |
| `app` | installs the OOD card + renders `clusters.d/<cluster>.yml` |
| `portal` | installs the dashboard branding + home-page override |
| `admin` | installs `${SITE_NAME}-add-user.sh` / `-setup-project.sh` / `-rename-project.sh` to `/usr/local/sbin` |
| `cron` | installs the T-07 provenance index cron (runs as `ADMIN_USER`) |
| `verify` | smoke-checks the result |

> **The `toolchain` phase needs conda.** Install miniforge first (e.g. into
> `${SITE_ROOT}/tools/miniforge3`, matching `CONDA_BASE` in `site.conf`) if
> the box doesn't have it.

> **Branding review.** The `portal` phase substitutes structural tokens but
> deliberately leaves prose ("Kapur Lab", Penn State footer) for you to
> rewrite — `${SITE_ROOT}/.../wgs_pipelines.yml` and the dashboard
> `index.html.erb`. It warns you which files.

The full reference set (27 organisms) + curated VCF DBs are **bulk data**, not
in git. Pull them from the reference server when ready:

```bash
rsync -aAX --info=progress2 \
  vxk1@kapurlab-wgs3.tailf38ff4.ts.net:/srv/kapurlab/refs/vsnp3/ \
  ${SITE_ROOT}/refs/vsnp3/
```

(Set `REFS_RSYNC_SOURCE` in `site.conf` and the `refs` phase prints this for
you. The demo in §5 only needs one small reference, so you can defer the full
pull.)

---

## 5. The out-of-the-box demo (sample bundle)

So the install has something runnable immediately. The bundle = the
SARS-CoV-2 reference + 6 raw deer fastqs + a project pointed at them (~2.5 MB).

**Package it once** (from a machine that can reach the reference server):

```bash
cd vsnp_gui/deploy/sample_data
./build_bundle.sh --from vxk1@kapurlab-wgs3.tailf38ff4.ts.net --tar
```

**Install it on the target** (offline, after `install_ood.sh`):

```bash
sudo ./install_bundle.sh --site-conf ../site.conf --user <admin>
```

This places the reference (and registers it) and drops the `demo_sars_cov_2`
project into the user's `~/projects/`.

---

## 6. First user & projects

```bash
# an admin (write access to refs/tools/audit; NOT OS sudo unless you add it)
sudo ${SITE_NAME}-add-user.sh <login> --admin

# a regular member
sudo ${SITE_NAME}-add-user.sh <login>

# a shared project with a per-project group + XFS quota
sudo ${SITE_NAME}-setup-project.sh <project> <user1> <user2>
```

`add-user.sh` also generates the **SSH self-loopback key** OOD's `linux_host`
adapter needs (it spawns sessions via `ssh user@localhost`). New group
memberships only apply to **new** sessions — users log out/in after being added.

---

## 7. Acceptance test (proves all four layers)

1. Browse to `http://<SERVERNAME>/`, log in as `<login>`.
2. Pipelines → **vSNP GUI** → launch a session → **Open vSNP GUI**.
3. Open project **demo_sars_cov_2** (pre-pointed at `NC_045512_wuhan-hu-1`).
4. **Run Step 1** on all 6 samples (~1–2 min).
5. **Run Step 2** → cascade/sorted tables + a 6-leaf tree.
6. Hover a variant cell → IGV opens with reads + calls.

All green ⇒ done.

---

## 8. Producing the distributable tarball

To hand a sysadmin a single self-contained artifact:

```bash
cd vsnp_gui
deploy/sample_data/build_bundle.sh --from <ref-server> --tar   # stage the demo
git archive --format=tar.gz --prefix=vsnp_gui/ -o /tmp/vsnp_gui-src.tgz HEAD
# bundle: the source tgz + deploy/sample_data/vsnp-sample-bundle.tar.gz
#         + the ood_default.sif (for OOD_SIF_SOURCE=local path)
#         + this docs/deploy/ directory
```

The recipient: unpack, `cp site.conf.example site.conf` + edit, then follow
§2 → §7. The only things that still need the network are the OS package
mirrors, the OSC apt repo, conda/bioconda, and (optionally) the full
reference-set rsync.

---

## 9. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `bootstrap` 404 on the release `.deb` | Wrong OS codename in the URL. Browse `https://apt.osc.edu/ondemand/3.1/` and set the right file; confirm the box is `noble`. |
| Session launches but "Open vSNP GUI" 404s | App can't be seen through the proxy. Confirm all frontend URLs are relative (they are) and `rnode_uri: /rnode` is in `ood_portal.yml`. |
| In-session: "Temporary failure in name resolution" (SRA/NCBI) | `/run/systemd/resolve` missing from `singularity_bindpath` — it's in the rendered cluster config; confirm it survived. |
| vsnp3 tools / shared refs invisible in-session | `SITE_ROOT` missing from `singularity_bindpath`. |
| Project quotas don't enforce | `SITE_ROOT` isn't XFS+prjquota (§2). Functional otherwise. |
| `fastapi`/`uvicorn` import error | The `toolchain` phase pip-installs them into the vsnp3 env; re-run it. Prod runs uvicorn from `${VSNP3_PREFIX}/bin/python`. |
| Sessions fail with no `.sif` | `ood_default.sif` not placed (§3). |
| vsnp3 patches lost after `conda update` | Re-run `deploy/vsnp3-patches/apply.sh ${VSNP3_PREFIX}`. |

---

## 10. NIVEDI-specific notes

- Internal-only, weeks-preferred timeline, local Linux users → the wgs3 model
  (PAM basic-auth, linux_host adapter) ports directly. Revisit TLS (§1) for
  their network.
- No NIVEDI SSH was available when this was written — **dry-run everything**
  on first contact and expect to fix 2–3 environment edge cases (that's the
  pattern with every OOD-meets-bioinformatics deploy).
- Site values to confirm with NIVEDI before install: `SITE_ROOT` mount,
  `SERVERNAME`, the admin login (`ADMIN_USER`), and group-name preferences
  (`nivedi-members` / `nivedi-admins`).
