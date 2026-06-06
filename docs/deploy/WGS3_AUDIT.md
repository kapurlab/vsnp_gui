# wgs3 reference-install audit (for NIVEDI port)

> Captured 2026-06-05 by a live read-only sweep of the production wgs3
> server. This is the **ground truth** the NIVEDI install must reproduce —
> especially the parts that were done by hand and live nowhere in the repo
> (storage mounts, OS-level groups, OOD core install, sudoers, conda env).
>
> Companion to [`INSTALL_OOD.md`](INSTALL_OOD.md) (the runbook) and
> [`../../deploy/install_ood.sh`](../../deploy/install_ood.sh) (the vSNP-layer
> installer). Ticket: **T-49** (audit) + **T-50** (parameterized installer).
>
> NIVEDI target per the scoping conversation: 64 CPU / 500 GB RAM / 20 TB,
> local Linux users, internal-only network, weeks-preferred timeline.

---

## 0. The layer cake

wgs3 is **not** one install. It is four layers stacked, installed in this order:

| Layer | What | How it got there | In our repo? |
|---|---|---|---|
| 1. Base OS + storage | Ubuntu 24.04, XFS mounts with prjquota, swap | Manual (OS install + `T-19-storage-layout` runbook) | Runbook only |
| 2. OOD core | OnDemand 3.1.16, Apache, PAM auth, Singularity | OSC apt repo (`apt.osc.edu`) | No — upstream |
| 3. vSNP toolchain | conda `vsnp3` env, shared refs, vsnp_gui checkout | Manual + scripts | Partially |
| 4. vSNP OOD layer | app card, cluster cfg, portal, admin scripts, cron | Manual `sudo cp` from repo | **Yes** ← what we own |

**Our installer (`install_ood.sh`) automates layer 4 and the scriptable
parts of layer 3.** Layers 1–2 are prerequisites documented in the runbook
(OOD core is OSC's to install — we do not reinvent it).

---

## 1. Base OS + storage (layer 1)

| Property | wgs3 value | Notes for NIVEDI |
|---|---|---|
| OS | **Ubuntu 24.04.4 LTS** (noble), kernel 6.8.0 | CLAUDE.md still says 22.04 — **stale**. OOD apt repo is keyed to `noble`. |
| CPU / RAM | Threadripper PRO 7985WX 64c / 503 GB | NIVEDI: 64c / 500 GB — equivalent. |

### Mounts (`/etc/fstab`, by UUID)

```
/                     ext4   errors=remount-ro
/boot/efi             vfat   umask=0077
/home                 xfs    defaults,usrquota,prjquota
/srv/kapurlab         xfs    defaults,prjquota              ← shared lab root
/srv/kapurlab/backup  xfs    defaults,prjquota              ← separate disk
swap                  swap    sw
```

Critical detail: **`/home` and `/srv/<site>` are XFS with `prjquota`** (and
`/home` also `usrquota`). The per-project quota mechanism
(`kapurlab-setup-project.sh` → `xfs_quota project -s`) only works on an XFS
filesystem mounted with `prjquota`. If NIVEDI's `/srv` is ext4 or lacks the
mount option, the quota step degrades to a warning (the directory/group still
work, just no enforced cap).

Provisioning the disks/mounts themselves is **out of scope for the
installer** — it's a one-time hardware/partition step. See
[`../dev/runbooks/T-19-storage-layout.md`](../dev/runbooks/T-19-storage-layout.md)
for the wgs3 procedure to adapt.

---

## 2. OOD core (layer 2) — OSC's to install

| Property | wgs3 value |
|---|---|
| OnDemand | **3.1.16** |
| Install source | OSC apt repo: `deb https://apt.osc.edu/ondemand/3.1/web/apt noble main` (`/etc/apt/sources.list.d/ondemand-web.list`) |
| Web server | Apache 2.4.58 + `ondemand-nginx` (PUN) + `ondemand-passenger` |
| Auth | **Basic auth over PAM** (`AuthPAMService ood`), `Require valid-user` |
| Port | 80 (plain HTTP — internal-only network; see §7 hardening note) |
| Job adapter | `linux_host` (single-node; no Slurm/PBS) |
| Container runtime | Singularity 3.x / Apptainer both present at `/usr/bin/` |
| Session image | `/opt/ood/ondemand/ood_default.sif` (30 MB stock OOD image) |

### `ood_portal.yml` (the bits that matter)

```yaml
servername: 100.68.171.59          # ← site IP/hostname
server_aliases: [172.29.62.6, a8-an-vxk1-u5]
port: 80
auth:
  - AuthType Basic
  - AuthName "KapurLab WGS3 — Open OnDemand"   # ← branding
  - AuthBasicProvider PAM
  - AuthPAMService ood
  - Require valid-user
node_uri:  /node
rnode_uri: /rnode                  # ← the reverse-proxy prefix the app sees
```

### `/etc/pam.d/ood` (PAM service backing basic-auth)

```
auth    required pam_unix.so
account required pam_unix.so
```

Plain local-Unix auth. **This is exactly NIVEDI's model** (local Linux users,
no LDAP/Kerberos), so it ports directly.

**Hybrid recommendation stands:** install OOD core on NIVEDI via OSC's
official path (their [Ansible playbook](https://osc.github.io/ood-documentation/latest/installation.html)
*or* the apt repo wgs3 used). The apt route is what wgs3 actually ran and is
simpler for a single box; the Ansible route is more reproducible. Either way
it produces layer 2; our installer assumes it's already there and refuses to
run if it isn't (preflight check).

---

## 3. vSNP toolchain (layer 3)

### Shared root: `/srv/kapurlab/` → NIVEDI `/srv/nivedi/`

```
/srv/kapurlab/                  root:kapurlab-members 0755
├── tools/                      root:kapurlab-admins  2775 (setgid)
│   ├── vsnp3/                  conda env (bioconda vsnp3-3.16)
│   ├── vsnp_gui/               git checkout (this repo), owner vxk1
│   ├── miniforge3/             shared conda base (owner tks5563)
│   ├── kraken_id_parse_gui/    Tod's second app (out of scope for vSNP port)
│   └── .claude/
├── refs/                       root:kapurlab-admins  2775
│   ├── vsnp3/
│   │   ├── reference_options/  27 reference sets (DATA — rsync, not in repo)
│   │   └── vcf_db_folders/     curated VCF DBs (DATA)
│   ├── kraken/                 (kraken app)
│   └── mhc/                    (future)
├── databases/                  root:kapurlab-members 2775  (blast, kraken2 — DATA)
├── projects/                   shared projects (per-project groups)
├── audit/                      root:kapurlab-admins  2775
│   ├── runs.sqlite             T-07 provenance index (owner = ADMIN_USER)
│   ├── runs.sqlite.jsonl       nightly export
│   ├── env_snapshots/          content-addressed env captures
│   └── metadata_failures.jsonl
└── backup/                     separate disk, retired-project snapshots
```

Directory group/mode pattern is consistent: shared dirs are **setgid
(`2775`/`2770`)** so new files inherit the group. Admin-curated trees
(`tools`, `refs`, `audit`) are group `kapurlab-admins`; broadly-readable
trees (`databases`, top-level) are `kapurlab-members`.

### conda `vsnp3` env

| Property | wgs3 value |
|---|---|
| Path | `/srv/kapurlab/tools/vsnp3` (a full conda env, used as the prefix) |
| Built via | bioconda — `vsnp3-3.16-hdfd78af_0` |
| Python | 3.14.4 (whatever bioconda pulled) |
| Bundled tools | samtools 1.23, bcftools 1.23, bwa, raxml, mafft, iqtree, spades 4.2, freebayes 1.3.10, seqkit (bioconda deps of vsnp3) |
| **Backend deps** | **fastapi 0.136 / uvicorn 0.46 installed into the *same* env** — the OOD `script.sh` runs `${vsnp3}/bin/python -m uvicorn`. NOTE this is *newer* than `backend/requirements.txt` (pins fastapi 0.115); the pins are for a standalone venv, prod uses the conda env's newer versions. |

So on wgs3 there is **one** Python env that serves both vsnp3 CLI tools *and*
the FastAPI backend. The installer creates `conda create -n vsnp3 vsnp3` then
`pip install`s the backend requirements into it.

### vsnp3 patches

The conda env is patched in place with `deploy/vsnp3-patches/apply.sh`
(idempotent; sentinel-detected). Patch `v3.16-kapurlab.patch` carries the
pandas-2 `column[0]` fix, `VSNP3_BOOTSTRAP` support, and SyntaxWarning
raw-string fixes. **Must be re-applied after any `conda update` of vsnp3.**

### Reference path registration

`vsnp3` discovers reference sets by reading **one** file:

```
/srv/kapurlab/tools/vsnp3/dependencies/reference_options_paths.txt
  └─ contents: /srv/kapurlab/refs/vsnp3/reference_options
```

Written by `vsnp3_path_adder.py -d <refs-dir>` (or just `echo`'d). The 27
reference sets and the VCF DB folders under `refs/` are **bulk data** — they
rsync from wgs3 to NIVEDI, they are not in git.

### Frontend build toolchain

| Property | wgs3 value | Note |
|---|---|---|
| node / npm | **system** `/usr/bin/node` v18.19.1, npm 9.2.0 | NOT in the conda env. `apt install nodejs npm` or NodeSource. |
| Build | `cd frontend && npm ci && npm run build` → `frontend/dist/` | uvicorn serves `dist/` as StaticFiles. Rebuild after every frontend change. |

---

## 4. vSNP OOD layer (layer 4) — what `install_ood.sh` owns

### App card: `/var/www/ood/apps/sys/vsnp_gui/`

Deployed files (root-owned, installed by `sudo cp`/`install` from
`deploy/ood/`):

```
manifest.yml          app identity (name, icon, Bioinformatics/SNP Analysis)
form.yml              session form — cluster: "wgs3"  ← cluster name literal
submit.yml.erb        batch_connect basic template, conn param: port
view.html.erb         "Open vSNP GUI" button → /rnode/<host>/<port>/
template/before.sh    parent process: find_port → $port
template/script.sh.erb container: PATH=<vsnp3>/bin; uvicorn app.main:app
```

`script.sh.erb` already has a **fallback ladder** (shared `/srv/kapurlab/tools`
→ per-user `~/miniforge3`), so it's partly site-aware, but the
`/srv/kapurlab` literal is the shared path it prefers.

### Cluster config: `/etc/ood/config/clusters.d/wgs3.yml`

The linux_host adapter definition. Key site-specific values:

- cluster filename + `metadata.title: "WGS3 (Local)"`
- `ssh_hosts: [localhost, a8-an-vxk1-u5, kapurlab-wgs3.tailf38ff4.ts.net]`
- `singularity_bindpath: /etc,/home,/tmp,/opt,/usr,/var,/srv,/run/systemd/resolve`
  — **`/srv` must be in the bindpath** or the container can't see shared tools/refs.
  `/run/systemd/resolve` is bound so in-container DNS works (NCBI/ENA/SRA fetches).
- `singularity_image: /opt/ood/ondemand/ood_default.sif`

### Portal config: `/etc/ood/config/`

| File | Purpose |
|---|---|
| `ondemand.d/dashboard.yml` | dashboard title + brand color, `pinned_apps: []` |
| `wgs_pipelines.yml` | declarative pipeline cards / footer / onboarding copy (heavy branding) |
| `apps/dashboard/views/dashboard/index.html.erb` | full home-page override (T-16) — three-pane Data\|Pipelines\|System, lots of "KapurLab"/WGS3 branding, group-gated data walk |

The dashboard override **reads `kapurlab-admins`/`kapurlab-members`** to
compute the account-role pane and project visibility. Those group names are
embedded in the ERB.

### Admin scripts: `/usr/local/sbin/` (mode 0750, group kapurlab-admins)

- `kapurlab-add-user.sh` — useradd + groups + **SSH self-loopback key** (the
  linux_host adapter requires passwordless `ssh user@localhost`). PAM password.
- `kapurlab-setup-project.sh` — `proj-<name>` group + setgid dir tree + XFS
  project quota (ID range **1000–1999**, default **5T soft / 7T hard**) +
  append-only audit ledger.
- `kapurlab-rename-project.sh`, `verify_provenance.py` — provenance ops.

### Cron: `/etc/cron.d/vsnp_gui-provenance`

Three jobs (hourly gc, nightly crawl 02:00, nightly export 02:05) all running
**as user `vxk1`** via `/srv/kapurlab/tools/vsnp_gui/deploy/admin/provenance-cron.sh`.
The script hardcodes `/srv/kapurlab` paths and `PYTHONPATH`.

### sudoers

- `/etc/sudoers.d/ood` — OSC-shipped, lets `www-data` run `nginx_stage`. **Do not touch** (layer 2).
- `/etc/sudoers.d/vxk1-temp` — `vxk1 ALL=(ALL) NOPASSWD:ALL` — a convenience
  grant for the admin user. NIVEDI's first admin needs equivalent sudo to run
  the installer; whether it stays NOPASSWD-ALL or gets scoped is a site call.

### Upload limit

`/etc/ood/config/nginx_stage.yml` is **empty** (`---`) → OOD default 10 GB max
upload (T-26). Bump there if NIVEDI needs larger browser uploads.

---

## 5. Parameterization map (the literals → site.conf variables)

Every site-specific literal found across `deploy/`, and the `site.conf`
variable that replaces it. Order matters — **longest match first** so
`/srv/kapurlab` is replaced before a bare `kapurlab`.

| Literal on wgs3 | `site.conf` variable | NIVEDI value (example) | Files |
|---|---|---|---|
| `/srv/kapurlab` | `SITE_ROOT` | `/srv/nivedi` | 58 hits across deploy/ + config.py |
| `kapurlab-admins` | `ADMINS_GROUP` | `nivedi-admins` | add-user, dashboard erb, README |
| `kapurlab-members` | `MEMBERS_GROUP` | `nivedi-members` | add-user, dashboard erb |
| `proj-` (prefix) | `PROJ_PREFIX` | `proj-` (keep) | setup-project, add-user |
| `kapurlab` (script names, branding) | `SITE_NAME` | `nivedi` | script filenames, branding strings |
| `wgs3` (cluster id + filename) | `CLUSTER_NAME` | `nivedi` | form.yml, clusters.d/`<cluster>`.yml |
| `WGS3` / "KapurLab" (display) | `SITE_DISPLAY` | `NIVEDI` | dashboard erb, wgs_pipelines, ood_portal AuthName |
| `vxk1` (cron/provenance owner) | `ADMIN_USER` | first NIVEDI admin login | provenance cron, provenance-cron.sh |
| `100.68.171.59` / tailnet host | `SERVERNAME` | NIVEDI IP/hostname | clusters.d ssh_hosts, ood_portal |
| `/opt/ood/ondemand/ood_default.sif` | `SINGULARITY_IMAGE` | same (keep) | clusters.d |
| bindpath incl. `/srv` | `SINGULARITY_BINDPATH` | include `${SITE_ROOT}` | clusters.d |
| projid `1000-1999` | `PROJID_LO/HI` | keep | setup-project |
| quota `5t/7t` | `QUOTA_SOFT/HARD` | site call | setup-project |

**Branding caveat:** `wgs_pipelines.yml` and the dashboard `index.html.erb`
carry a lot of prose branding ("Kapur Lab Pipelines", Penn State footer,
onboarding links). Blind sed on these would produce awkward copy. The
installer does the *structural* substitutions (group names, paths it reads)
but flags the branding files for **manual review** — NIVEDI will want to write
their own welcome copy anyway.

---

## 6. What the installer does NOT do (deliberately)

1. **Install OOD core** (layer 2) — OSC owns that; we preflight-check it.
2. **Provision storage/mounts** (layer 1) — one-time hardware step; runbook only.
3. **Ship reference data** — the 27 reference sets + VCF DBs are bulk data;
   rsync them wgs3 → NIVEDI as a separate step (documented in the runbook).
4. **Create user accounts** — `kapurlab-add-user.sh` (renamed per site) does
   that interactively, post-install, one user at a time.
5. **Touch `/etc/sudoers.d/ood`** or any OSC-managed file.

---

## 7. Gaps / risks to flag before NIVEDI go-live

- **Plain HTTP (port 80).** wgs3 runs HTTP because it's internal/Tailscale-only.
  NIVEDI is also internal-only, so HTTP is defensible, but basic-auth
  credentials cross the wire in the clear. If NIVEDI's internal network is less
  trusted than a tailnet, put TLS in front (OOD supports `ssl:` in
  `ood_portal.yml`). **Decision for NIVEDI admins.**
- **Single Python env serves vsnp3 + FastAPI.** A `conda update vsnp3` can bump
  fastapi/uvicorn unexpectedly. Pin or snapshot the env after install; re-apply
  vsnp3 patches after any update.
- **prjquota dependency.** If `/srv/nivedi` isn't XFS+prjquota, project quotas
  silently no-op (script warns). Confirm the mount before relying on caps.
- **Reference-data drift.** wgs3's `reference_options/` has been hand-curated
  (T-39 re-uploads, mtbc0_v1.1, etc.). The rsync snapshot is a point-in-time
  copy; record its date so NIVEDI knows what vintage it has.
- **No NIVEDI SSH yet.** Everything here is authored against wgs3 + the repo.
  The installer and runbook are **drafts to dry-run on NIVEDI**, not yet
  validated end-to-end on the target. First NIVEDI run should be
  `install_ood.sh --dry-run` with eyes on every step.
