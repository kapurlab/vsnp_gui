# ICAR-NIVEDI bdtools install — issues & fixes

> Field report from standing up the full bdtools suite (8 GUIs) on the
> ICAR-NIVEDI Open OnDemand host (Ubuntu 24.04, OOD 4.2.2, single-node
> `linux_host`, **shared institutional web server**). Captured 2026-06-19.
>
> Purpose: feed Tod + the repo maintainers so the toolkit (`kapurlab/
> bioinformatic_diagnostic_tools`) and `vsnp_gui` get fixed for the next site.
> Each item: **what we hit → root cause → concrete fix → where it lives**.
>
> Severity: **P1** breaks the install / leaves a broken card · **P2** costs
> hours or silent wrong state · **P3** polish / docs.

---

## A. Toolkit bugs & gaps (`bioinformatic_diagnostic_tools`)

### A1. [P1] Two pinned tools ship no build path → `install --server` renders a card with no env
`install-server.sh` `toolchain` phase only runs `<dir>/deploy/install.sh`. If a
tool lacks it, the phase **warns and continues**, then the `app` phase still
renders the OOD card. Result: a card that appears in the dashboard but 500s on
launch (no `env/bin/python`, no `frontend/dist`).

At the **currently pinned tags** this hits 2 of 8:
- **`kraken_id_parse_gui` v0.1.3** — has no `deploy/` directory at all. Builds
  from `conda_setup/environment.yml` + `backend/requirements.txt` + `npm`.
- **`vsnp_gui` v0.2.1** — builds via `deploy/install_ood.sh`, not
  `deploy/install.sh`; and its env lives at a **non-standard prefix**
  (`$TOOLS_ROOT/vsnp3`, not `<dir>/env`).

The other 6 (`amr_plus_gui`, `irma_gui`, `genoflu_gui`, `mlst_gui`, `ksnp_gui`,
`ncbi_submit_gui`) ship `deploy/install.sh` and build cleanly.

**Fixes:**
1. Add a `deploy/install.sh` to **kraken_id_parse_gui** mirroring the
   `mlst_gui` one (conda env at `<dir>/env` from `conda_setup/environment.yml`
   → pip `backend/requirements.txt` → `npm ci && npm run build`); cut a tag;
   bump the pin in `tools.yml`.
2. Add a `deploy/install.sh` to **vsnp_gui** (thin wrapper around the existing
   `install_ood.sh toolchain`, or a standalone no-sudo env+frontend build) so
   the umbrella installer handles it like the rest. Reconcile the env prefix
   (see A2).
3. Make `install-server.sh` **fail or skip the card** when no build path exists,
   instead of leaving a launch-broken card. Minimum: exit non-zero so
   `bdtools install --server all` reports the tool as failed (today it's a soft
   warn and the tool counts as success).

### A2. [P2] vsnp_gui env prefix is inconsistent with the suite convention
Every other tool's card resolves `<dir>/env`; vsnp_gui's `script.sh.erb`
resolves `$SITE_ROOT/tools/vsnp3` (a top-level `vsnp3` env, historically the
shared bioconda env). Two conventions = special-casing everywhere.
**Fix:** pick one. Either move vsnp_gui to `<dir>/env` like the rest, or
document `vsnp3` as a deliberate exception and teach `install-server.sh` +
`doctor` about it.

### A3. [P1] No `channel_priority: strict` → 30-min pathological conda solve
Fresh miniforge defaults to `channel_priority: flexible`. With a heavy
conda-forge + bioconda + `defaults` env (`amr_plus_gui`: amrfinderplus, spades,
shovill, kraken2, samtools…), libmamba thrashed the search space — **30 min
pegged at 100% CPU, no convergence**, blocking every subsequent tool (sequential
install). Setting `channel_priority: strict` (bioconda's official
recommendation) is the fix.
**Fix:** the conda/miniforge prereq step (or `bdtools` setup) must write
`channel_priority: strict` into the base `.condarc`. High value — it speeds and
*correctifies* every tool's solve and prevents the multi-tool stall.
Workaround applied at ICAR: `conda config --file <base>/.condarc --set
channel_priority strict`, then re-ran.

### A3b. [P1] `amr_plus_gui` env is effectively unsolvable from `environment.yml` — needs a lock file
Even with strict priority + `defaults` removed, `amr_plus_gui`'s spec did **not
converge** — libmamba ran **>41 min at 98% CPU** and never finished (the other 7
envs solved in minutes once cleaned). The spec is too loose/dense:
`ncbi-amrfinderplus` + `shovill` (which itself pulls spades/skesa/megahit/bwa/
samtools/mash/pilon/trimmomatic) + `kraken2` + `mlst` + the web stack, almost
all unpinned. **Fix:** ship a **per-platform lock / explicit spec** for the hard
envs (at least amr_plus, ideally all) so `--server` does a deterministic
download+link, not a multi-channel SAT solve. We unblocked ICAR by exporting
wgs3's validated env (`conda list -p <env> --explicit`, 330 pkgs) and recreating
it on ICAR with `conda create --file` (no solve) — sub-minute vs. >41 min and
guaranteed parity with the validated box. The suite already aims for
reproducibility via pinned tags; lock files are the missing piece for the conda
layer. Recommend `conda-lock` (or checked-in `--explicit` files) per tool, per
platform (linux-64 at minimum), regenerated at release time.

### A3c. [P2] `doctor` reports vsnp_gui "environment not built" (false negative)
Consequence of A2: `bdtools doctor` checks `<dir>/env` for every tool, but
vsnp_gui's env is the shared `vsnp3` prefix (`$TOOLS_ROOT/vsnp3`). So on a
correctly-installed site doctor prints `✗ environment not built / fix: bdtools
install vsnp_gui` even though the env is present and the card runs (verified:
vsnp3 Python 3.12, `vsnp3_step1.py`, `snp-dists`, fastapi all present). **Fix:**
teach `requirements.py`/doctor that vsnp_gui's env is the `vsnp3` prefix (ties to
the A2 decision).

### A3d. [P2] `doctor`/`requirements.py` DB paths are kapurlab-hardcoded
`bin/lib/requirements.py:37,41` hardcode the DB defaults
`/srv/kapurlab/databases/kraken2/k2_standard_08gb` and
`/srv/kapurlab/databases/blast/ref_prok_rep_genomes`. On an `icar` site doctor
reports those kapurlab paths as missing instead of `$DATABASES_ROOT/...` from
site.conf. **Fix:** derive DB default paths from `site.conf`
(`DATABASES_ROOT`), not a hardcoded `/srv/kapurlab`.

### A3e. [P2] `setup-databases` vsnp wiring doesn't reach OOD users / the env
`setup-databases vsnp-refs` wired the refs into **`/root/.config/vsnp_gui/
config.json`** (the invoking root user's per-user config) and printed "vsnp_gui
not installed locally yet". On an OOD `--server` site the GUI runs as each
end-user and discovers refs via the **vsnp3 env's
`dependencies/reference_options_paths.txt`** — which `setup-databases` does **not**
write (only `install_ood.sh` step 4 does). Net: refs present on disk (23 sets)
but invisible to the GUI until the path is hand-registered. **Fix:** have
`setup-databases vsnp-refs` register the refs dir in the vsnp3 env's
`reference_options_paths.txt` (system-wide), not just a per-user config.

### A3f. [P1] `install --server` doesn't provision writable jobs/projects dirs → every tool crashes on launch
The generic tools hardcode `_JOBS_DIR = _REPO_ROOT/backend/jobs` (no env
override) and are written to use a **group-writable shared** jobs dir ("must be
writable by all lab members"). But `install --server` installs the tool tree
**root-owned 0755** and does **not** create the jobs dir nor the members group /
perms (it explicitly "does not manage Unix groups/storage"). So when the OOD
session runs as a normal user, uvicorn dies at startup:
`PermissionError: [Errno 13] ... '/srv/icar/tools/mlst_gui/backend/jobs'` — the
session's Connect tab opens to a dead server (looks like "launches but no GUI").
This hits **all 7 generic tools** (amr_plus, irma, genoflu, mlst, kraken, ksnp,
ncbi_submit). vsnp_gui dodges it because `install_ood.sh` runs `groups`+`storage`
phases; the umbrella installer has no equivalent.
**Fix:** `install --server` must (a) create the members group from site.conf and
(b) create each tool's writable dir(s) `root:<members> 2775` (setgid). Or change
the apps to default the jobs dir to a per-user/OOD-session path with an env
override the card sets. Workaround at ICAR: `install -d -m 2775 -g icar-members
<tool>/backend/jobs` for all 7 + added `oodadmin` to `icar-members`. Verified
mlst then serves (HTTP 200).

### A3g. [P2] Generic tools' shared-projects default is kapurlab-hardcoded; cards don't set it
`mlst_gui` defaults `MLST_SHARED_PROJECTS=/srv/kapurlab/projects` and `vsnp_gui`
defaults `VSNP_GUI_SITE_ROOT=/srv/kapurlab` in **app code**. Only vsnp_gui's card
exports the override (`VSNP_GUI_SITE_ROOT`, subst'd to the site root); the generic
tools' cards don't export their projects var, so on a non-kapurlab site they fall
back to a `/srv/kapurlab/...` path that doesn't exist. **Fix:** each tool's card
`script.sh.erb` should export its projects/data root from the site root (a
subst'd literal), mirroring vsnp_gui. Workaround at ICAR: created
`/srv/icar/projects` (2775, icar-members); set the per-tool env where needed.

### A3h. [P1] vsnp_gui v0.2.1 OOD card omits `export VSNP_GUI_SITE_ROOT` → personal-path fallback
The production card at `vsnp_gui/ood/apps/vsnp_gui/template/script.sh.erb`
(v0.2.1) is **missing** the `export VSNP_GUI_SITE_ROOT=<site root>` line that
`deploy/ood/template/script.sh.erb` has — the two templates diverged. Without it,
`config.py` falls back to `_SITE_ROOT=/srv/kapurlab`; since that doesn't exist on
a non-kapurlab site, `_DEFAULT_VSNP3_PATH` falls back to the **personal**
`~/miniforge3/envs/vsnp3`, so the GUI Settings show `vsnp3_path` + `bcftools` as
broken (✗) and `vsnp3` can't be found → Step 1 won't run. The first launch also
persists those wrong paths into the user's `~/.config/vsnp_gui/config.json`.
**Fix:** restore the `export VSNP_GUI_SITE_ROOT` line in the `ood/apps` card
(subst'd from site.conf), and keep the two script.sh.erb templates in sync (or
generate one from the other). Workaround at ICAR: added the export to the
rendered card + deleted stale per-user config (regenerates correctly). Verified
all paths green + refs + projects visible after a fresh launch.

### A3i. [P2] vsnp refs: `setup-databases` layout vs vsnp_gui's expected `$SITE_ROOT/refs` layout
`setup-databases vsnp-refs` stages refs at `$DATABASES_ROOT/vsnp3/
reference_options` (`/srv/icar/databases/...`), but vsnp_gui's config defaults
`vsnp3_reference_options_root` to `$SITE_ROOT/refs/vsnp3/reference_options`
(`/srv/icar/refs/...`) — different trees. The vsnp3 env's
`reference_options_paths.txt` is a third discovery path. **Fix:** pick one
canonical refs location and make `setup-databases` + vsnp_gui agree. Workaround
at ICAR: symlinked `$SITE_ROOT/refs/vsnp3/reference_options` ->
`$DATABASES_ROOT/vsnp3/reference_options` so all discovery paths converge.

### A3j. [P1] AMRFinderPlus DB not downloaded by install → amr_plus can't run
After `install --server amr_plus_gui`, `amrfinder` 4.2.7 is present but its
**database is absent** (`amrfinder -l` → no data dir), so any AMR run fails.
amr_plus's `deploy/install.sh` is supposed to run `amrfinder -u` but it didn't
fire (and the explicit-env restore for A3b bypassed deploy/install.sh entirely —
so any lock-file path for amr_plus must ALSO trigger the DB download). **Fix:**
ensure `amrfinder -u` runs as a post-install DB step for amr_plus (and is part of
`setup-databases`/doctor coverage). Workaround at ICAR: ran `amrfinder -u`
(downloads version 2026-05-15.1 into the env's share dir).

### A3k. [P2] No `sra-tools` → SRA download only works for ENA-mirrored accessions
The box has no `fasterq-dump`/`prefetch`. vsnp_gui's `sra.py` falls back to
ENA/`curl`, so ENA-mirrored runs (e.g. SRR1791695) download fine, but
SRA-only accessions (e.g. the IRMA test SRR39145037, not on ENA's fastq mirror)
**can't be fetched** from any GUI. **Fix:** ship `sra-tools` (fasterq-dump) in
the tool envs (or a shared bin on the session PATH) so the SRA-download feature
works for all accessions, not just ENA ones. Surfaced while sourcing the
influenza demo reads.

### A3l. [P1] vsnp3 conda version unpinned → ICAR got 3.35 vs wgs3's 3.16 (behavior drift)
`install_ood.sh` builds the vsnp3 env with `conda create ... vsnp3` (unpinned),
so ICAR got **vsnp3 3.35** while the validated wgs3 box runs **3.16**. 3.35 added
a strict `vsnp3_input_validator.py` (run by `vsnp3_step1.py`) that rejects any
non-ACGTN character. The SARS demo reads (synthetic, generated from GISAID
consensus — uniform `I` quality, IUPAC codes like `K`) fail validation on 3.35
but run on 3.16. Real ACGTN reads are unaffected. **Fix:** pin the vsnp3 version
in the install (a `VSNP3_VERSION` already exists in install_ood.sh — set it, or
ship a lock file per A3b) so all sites match the validated version; and decide
whether 3.35's validator should tolerate IUPAC ambiguity codes (flag for Tod —
it would also reject consensus-derived inputs in real workflows).

### A3m. [P2] Kraken: default DB path mismatch + Krona taxonomy DB not built at install
Two Kraken/Krona gaps: (1) vsnp_gui's Kraken integration (and kraken_id_parse_gui)
default the DB to `$SITE_ROOT/databases/kraken2/k2_standard_08gb`, but if a site
deploys a *different* Kraken DB (here: `core_nt`), that default resolves to a
nonexistent path → Kraken produces no classification → "No Krona graph found."
(2) KronaTools is installed but its **taxonomy DB is never built**
(`ktUpdateTaxonomy.sh` not run at install) → `…/opt/krona/taxonomy/` is empty, so
Krona can't render even with Kraken output. **Fix:** (1) wire the configured
Kraken DB (whatever was staged) into the tool configs instead of a hardcoded
`k2_standard_08gb` default; (2) run `ktUpdateTaxonomy.sh` as part of
`setup-databases kraken` / install. Workaround at ICAR: ran ktUpdateTaxonomy;
will point kraken_db at `/srv/icar/databases/kraken2/core_nt` once it finishes.
(Note: `setup-databases` only knows std_08gb — see the resilient-DB / DB-choice
gap; core_nt was staged manually.)

### A3n. [P1] Kraken modal shows "Run failed" on success — SSE status cut by the /rnode proxy
The Kraken-ID modal (vsnp_gui) decides pass/fail **solely** from the job's SSE
event stream (`/api/jobs/{id}/events` emitting `[job:succeeded]`/`[job:failed]`).
The job succeeds (exit 0, backend `status:"succeeded"`, Krona written + served
HTTP 200), and the SSE emits `[job:succeeded]` **when read directly** — but
through the OOD `/rnode` reverse proxy the stream is truncated (browser receives a
partial `data:` and never the completion marker). So the modal mislabels a
successful run as "Run failed." Data is correct (Krona/classification fine); only
the status label is wrong. Step1/Step2 are unaffected because they derive status
from `.provenance/exit_code` files, not SSE. **Fixes:** (a) frontend should
fall back to polling `/api/jobs/{id}` (which returns the correct status) if the
SSE doesn't deliver a terminal marker; and/or (b) make the SSE proxy-friendly
(this deploy added `X-Accel-Buffering: no` + `Cache-Control: no-cache` to the
StreamingResponse as a candidate — verify it survives the apache `/rnode` hop).
High user-impact: it reads as a hard failure when the analysis actually worked.

### A4. [P2] Tool `environment.yml` files list the `defaults` channel
`amr_plus_gui/conda_setup/environment.yml` (and possibly others) include
`defaults` (repo.anaconda.com) → Anaconda **commercial-ToS** warnings on every
solve and a larger/slower repodata search. conda-forge + bioconda is sufficient
for the bioconda toolchain.
**Fix:** audit all tools' `environment.yml`; drop `defaults` unless a package
genuinely requires it. Pair with A3 (strict priority assumes
conda-forge > bioconda > defaults ordering).

### A5. [P2] `--server --dry-run` gives false confidence
Dry-run doesn't clone, so it can't see `deploy/install.sh` and prints
"no deploy/install.sh — build manually" for **all** tools, and can't detect that
A1's two tools genuinely lack a build path. The handoff's "dry-run validated"
missed the broken build paths.
**Fix:** add a pre-flight (or a `bdtools lint`/`check` subcommand) that inspects
each **pinned tag** (git ls-remote / archive / raw fetch) for a recognized build
path (`deploy/install.sh` or known alternative) + the `ood/apps/<tool>` card,
and reports per-tool BEFORE the multi-hour run. We did this by hand with
`raw.githubusercontent.com` HEAD checks — it should be a command.

### A6. [P1] bdtools doesn't stand up the OOD compute layer for `linux_host` sites
`ood-core/` is still a placeholder (README says "pending"); the real
`bootstrap_ood_core.sh` lives in `vsnp_gui/deploy`. On a box with OOD *core* but
no compute layer we had to do **Step 0 by hand**: install apptainer, place
`ood_default.sif`, write `clusters.d/<cluster>.yml`, set up the per-user
loopback ssh. None of this is in the toolkit.
**Fix:** promote `bootstrap_ood_core.sh` into `bdtools ood-core/` and add:
- a `clusters.d/<cluster>.yml` generator (linux_host + singularity) from a
  template + `site.conf` — **including the `/run/systemd/resolve` bindpath**
  (without it in-container DNS silently fails; see `vsnp_gui` wgs3.yml comment);
- the loopback-ssh bootstrap (it's in `kapurlab-add-user.sh` but only per-user).

### A7. [P1] `<site>-add-user` is not auth-aware and hardcodes site literals
`deploy/admin/kapurlab-add-user.sh` sets a **PAM/Linux** password (`chpasswd`).
ICAR's OOD authenticates against an **htpasswd file**
(`AuthBasicProvider file`, `/etc/ood/.htpasswd`) — so the script as-is creates a
Linux account that **cannot log into the web** (no htpasswd entry). It also
hardcodes `kapurlab-members` / `kapurlab-admins` and prints
`http://100.68.171.59/` (wgs3 tailscale IP) as the access URL.
**Fix:** make the add-user script:
- detect the OOD auth provider and update `/etc/ood/.htpasswd` when file-based
  (in addition to / instead of `chpasswd`);
- read group names + the access URL from `site.conf` (no hardcoded literals).
Workaround at ICAR: bespoke provisioning loop that did htpasswd + Linux user +
`icar-members`/`icar-admins` + loopback ssh.

### A8. [P3] Branded landing page is kapurlab-hardcoded and lists 4 tools
`vsnp_gui/deploy/ood/portal/wgs_pipelines.yml` hardcodes "Kapur Lab · Penn State
· WGS3 · OnDemand 3.1.16" and a **manual** card list of 4 (vsnp3, kraken,
desktop-coming, mhc-coming) — predates the 8-tool suite.
**Fix:** site-parameterize the branding (read `SITE_DISPLAY`) and
auto-generate the pipeline cards from `tools.yml` so it always reflects the
installed suite. (Belongs in the toolkit since it's now an 8-tool suite, even
though the files live in vsnp_gui today.)

---

## B. Deployment lessons / docs to update

### B1. [P2] "Shared institutional web server", not a dedicated OOD box
The host is ICAR-NIVEDI's **production** web server: the public NIVEDI site +
NADRES_v2 + NICRA run on apache `:80`; OOD was moved to **HTTPS `:443`** under
`/pun`, behind NAT (only ports 22 + 443 reachable externally; `:80` = public
site). Implications to document:
- The dashboard URL is **`https://<host>/`** (302 → `/pun/sys/dashboard`),
  **not** `http://…/`. The handoff/memory said http — wrong; cost confusion.
- **Never** touch apache / `ood_portal.yml` / the portal vhost on a shared box
  without the institution's admin — breaking it takes down the public site.
  `install --server` is app-only and safe; the *branding* follow-on overrides
  the **global** dashboard home, so it needs admin sign-off.

### B1b. [P1] ICAR `ood_portal.yml` lacked `node_uri`/`rnode_uri` → interactive apps 404 on Connect
The ICAR admin's minimal `ood_portal.yml` omitted `node_uri`/`rnode_uri`, so the
generated vhost had `/pun` + `/nginx` but **no `/node` or `/rnode`** reverse-proxy
Locations. Every interactive app launched fine (uvicorn served) but Connect went
to `/rnode/<host>/<port>/` → **Apache 404**. Also: the admin had hand-edited the
live vhost (added `ServerAlias 192.168.1.245` + `OOD_ALLOWED_HOSTS` for LAN
access), so `update_ood_portal` refused to overwrite (checksum mismatch) and only
wrote `ood-portal.conf.new`. Fix applied: added `node_uri`/`rnode_uri` to
`ood_portal.yml`, then **surgically inserted** the generated `/node`+`/rnode`
`<LocationMatch>` blocks into the live vhost (preserving the admin's LAN-IP
edits), config-tested, reloaded. Verified `/rnode/.../<port>/ → HTTP 200`.
**Deployment lesson:** on any site, verify `ood_portal.yml` defines
`node_uri`/`rnode_uri` before declaring interactive apps working — a stock app
that "launches" but 404s on Connect is this. (Belongs in the Step-0/verify
runbook B3.)

### B1c. [P2] Branded landing page: the wgs3 erb-override doesn't work on OOD 4.2; PUN-restart gotcha
The KapurLab rich 3-pane landing page is an `index.html.erb` **view override**
(`/etc/ood/config/apps/dashboard/views/dashboard/index.html.erb`) that worked on
wgs3 (OOD 3.1) but **OOD 4.2 does not load it** — 4.2's dashboard customization is
`ondemand.d/*.yml` + initializers only (no `views/` override). The supported 4.2
branding is: `dashboard_title`, `dashboard_header_img_logo` (nav logo — NOT
`dashboard_logo`, which is the home welcome logo), `brand_bg_color`, and
`pinned_apps` (renders the tools as home-page cards). Implemented that for ICAR
(ICAR-NIVEDI logo + NIVEDI blue #124983 + 8 pinned cards). The rich Data/Pipelines/
System layout would require patching the dashboard app's own views (upgrade-
fragile) — not advised on a shared institutional box. **Also (operational gotcha):**
`nginx_stage nginx_clean` did NOT pick up dashboard.yml changes for an
already-running user; had to `nginx_stage nginx -u <user> -s stop` per user to
force a fresh PUN. Document this for anyone editing dashboard branding.
**Fix for the suite:** ship an OOD-4.x-native branding recipe (yml + pinned_apps +
optional custom_css) instead of the 3.1 erb override; site-parameterize it.

### B2. [P3] htpasswd Basic Auth: no self-service password change AND no real logout
Stock OOD + `AuthBasicProvider file` can't prompt for a password change, has no
self-service reset (rotation is admin-only, `htpasswd -b`), and has **no working
logout** — the browser caches Basic-Auth credentials and re-sends them right
after the logout redirect, so the user appears permanently logged in until the
whole browser is closed (or an incognito window is used). Both limitations are
inherent to Basic Auth, not a misconfig. For multi-user institutional sites,
document the options: an external IdP (Keycloak/OIDC/SSO) is the real fix for
both; PAM+aging only helps password expiry. Decision belongs to the institution.
Interim user guidance: use incognito windows to switch users / "log out".

### B3. [P3] Update the install runbook with the verified Step-0 sequence
The exact, verified order for a `linux_host` site with OOD core already present:
apptainer (.deb, match the source box's version) → place `ood_default.sif` →
`clusters.d/<cluster>.yml` → per-user loopback ssh → **verify via `ood_core`
adapter job submit** (a stock VNC `bc_desktop` is NOT a valid smoke test if the
sif is the minimal web-only image — it has no VNC) → miniforge (strict
priority!) → `install --server all` → hand-resolve A1 tools → setup-databases →
doctor → test.

---

## C. Site-specific (ICAR) — not repo bugs, recorded for the handoff
- `.sif` copied from wgs3 (apptainer 1.5.0, sha `e9e740dc…b21c5a`); apptainer
  1.5.0 installed to match. `/usr/bin/singularity` symlink present.
- `clusters.d/icar.yml`: cluster id `icar`, linux_host, submit_host localhost,
  ssh_hosts [localhost, icar.localdomain], bindpath includes
  `/run/systemd/resolve`.
- 7 user accounts provisioned (htpasswd + Linux + loopback ssh + `icar-members`;
  `sureshkp`,`bgulati` also `icar-admins`).
- Scoped, non-standing sudo grant for `oodadmin`; revoke at end of session.

---

## Suggested ticket split
- **bdtools P1**: A1 (build paths + tag bumps), A3 (strict priority), A3b
  (lock files for hard envs — esp. amr_plus), A6 (compute-layer bring-up),
  A7 (auth-aware add-user).
- **bdtools P2**: A2 (env prefix), A4 (drop `defaults`), A5 (pinned-tag preflight/lint).
- **bdtools P3**: A8 (portal auto-gen).
- **docs**: B1, B2, B3.
