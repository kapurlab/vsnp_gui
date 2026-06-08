# Clean-room install test — wgs1 (NIVEDI dress rehearsal)

> 2026-06-06. First end-to-end run of the install playbook on a **bare** box
> (wgs1: Ubuntu 24.04, no OOD, empty `/srv`), using `SITE_NAME=nivedi` /
> `SITE_ROOT=/srv/nivedi` to rehearse the NIVEDI parameterization. This is the
> validation that [`INSTALL_OOD.md`](INSTALL_OOD.md) and the installers
> actually work, and the source of the fixes below.

## Result: PASS (functional)

Bare box → working multi-user OOD platform with a runnable demo, driven entirely
by the scripts. Every layer verified:

| Layer | Check | Result |
|---|---|---|
| 2. OOD core | `bootstrap_ood_core.sh` → portal auth challenge | ✅ 401 with `realm="NIVEDI (test)…"` |
| 3. groups/storage | `install_ood.sh groups storage` | ✅ `/srv/nivedi` tree, setgid, `nivedi-{members,admins}` |
| 3. toolchain | conda `vsnp3=3.16` + web deps + patches + frontend | ✅ env built, patches applied, `dist/` built |
| 4. app/portal/admin/cron | substituted + installed | ✅ `clusters.d/nivedi.yml`, `nivedi-*` scripts |
| demo | sample bundle install | ✅ reference + project placed |
| serve | uvicorn from the env | ✅ `/` 200, `/api/config` site-correct, `/api/projects` lists demo, `/api/references` lists NC_045512 |
| **pipeline** | `vsnp3_step1.py` on one demo sample | ✅ **BAM + `_zc.vcf` (113 variants) + annotated VCF + stats.xlsx**; freebayes 1.3.10 / samtools 1.23.1 from the conda env |

The full interactive GUI click-through (login → launch OOD session → Run Step
1/2 in the browser) was then exercised and surfaced a second wave of bugs (#7–12
below), all fixed.

> **Caveat — this run was hand-patched ~12 times mid-install.** It proves the
> *result* is achievable and is the source of the fixes; it is NOT proof the
> corrected scripts install unattended. That proof is a from-scratch reinstall
> using only the committed scripts (the planned next step).

## Bugs found & fixed (the point of the exercise)

### Install phase (scripted layers, before the browser)

All six were caught only by running on a clean box — none showed up in dry-runs.

1. **`bootstrap_ood_core.sh` verify too strict.** OOD redirects `/` → canonical
   host → `/pun/sys/dashboard` (301/302) before the 401. The check only looked
   at the first hop. Fixed to follow redirects and look for the 401.

2. **Toolchain pip built numpy from source.** `pip install -r requirements.txt`
   into the conda env tried to install the *pinned, older* scientific stack
   (numpy/pandas/scipy/matplotlib) — no wheels for the env's Python 3.14, so it
   fell back to a slow source build. Conda already provides newer versions.
   Fixed: pip installs only the web layer (`fastapi uvicorn pydantic
   python-multipart aiofiles`); conda owns the scientific stack (matches wgs3).

3. **`install_bundle.sh` left projects root root-owned.** It created
   `/home/<user>/projects` as root and only chowned the project subdir, so the
   user's uvicorn couldn't `mkdir .jobs`. Fixed: chown the projects root to the
   user in `--user` mode.

4. **`backend/app/config.py` hard-coded `/srv/kapurlab`.** The shared-root
   defaults (`vsnp3_path`, `shared_projects_root`, `vcf_db_folders_root`) were
   literal `/srv/kapurlab` in *application* code — invisible to the deploy-file
   substitution — so on `/srv/nivedi` they fell back to per-user paths and
   references came back empty. Fixed: derive from `VSNP_GUI_SITE_ROOT`
   (default `/srv/kapurlab`, so wgs3 is unchanged).

5. **OOD launcher didn't pass the site root.** `template/script.sh.erb` now
   `export VSNP_GUI_SITE_ROOT=/srv/kapurlab` (substituted per site) so the
   backend gets the right shared root in an OOD session.

6. **`SERVERNAME`/IP literal not substituted.** `kapurlab-add-user.sh`'s final
   "access OOD at http://100.68.171.59/" kept the wgs3 IP. Added
   `100.68.171.59 → SERVERNAME` to the substitution map.

### Runtime phase (found by actually using the GUI in the browser)

7. **Nobody could log in (PAM).** On a fresh box `www-data` isn't in the
   `shadow` group, so `pam_unix` can't verify any password → every basic-auth
   login fails. The single most common "OOD installed but no one can log in"
   cause. Fixed: `bootstrap_ood_core.sh` adds `www-data` to `shadow`.

8. **Every session showed "Completed" — no Connect button.** The linux_host
   adapter records the *last* `ssh_hosts` entry matching `hostname -A` as the
   job host, and `parse_hostname`'s regex only accepts **dotted FQDNs**; a bare
   short hostname (e.g. `a8-an-vxk1-u3`) parses to an empty host, so OOD polls
   nothing and marks the session Completed. Fixed: `render_cluster_yml` emits
   dotted FQDNs **last** in `ssh_hosts`.

9. **Step 2 "missing snp-dists".** `snp-dists` is not a vsnp3 dependency but the
   SNP-distance analysis needs it (wgs3 had it installed separately). Fixed:
   added to the `conda create` in the toolchain phase.

10. **"Pick a folder on wgs3" in the UI.** Hard-coded string in `App.jsx` →
    "Pick a folder on the server".

11. **Step 1 → HTTP 500 (backend `/srv/kapurlab`).** `provenance_writer.py`,
    `jobs.py`, and `main.py` defaulted `vsnp_gui_deploy_path` / `audit_root` /
    audit-log paths to literal `/srv/kapurlab` when config lacked the key →
    `FileNotFoundError`. Same class as #4. Fixed: `config.py` exposes
    `SITE_ROOT` and adds site-derived `DEFAULTS`; the module-level constants now
    use `config.SITE_ROOT`.

12. **Provenance hard-failed on a non-git deploy.** `capture_vsnp_gui_state`
    raised `DispatchFailed` when `git rev-parse HEAD` failed (no `.git`), so a
    **tarball/rsync install couldn't run Step 1 at all**. Fixed: degrade
    gracefully — record `git_sha` from a shipped `VERSION` file or `"unknown"`,
    log a warning, and proceed. `git clone` installs still capture the real SHA.

## Test-shortcut caveats (not installer bugs, but worth knowing)

- We **rsynced the working tree** instead of `git clone`-ing, which carried a
  gitignored Mac `backend/data/config.json` (the trigger for #4's discovery via
  a leaked legacy config). A real `git clone` to `VSNP_GUI_DIR` (as INSTALL_OOD.md
  says) wouldn't include it. Still, removing any stale `backend/data/config.json`
  at the placement step is a cheap safety net.
- Storage was **light** (mkdir on the existing ext4, no XFS prjquota), per the
  test plan — quotas no-op, everything else works.
- **node 18** on wgs1 (Ubuntu noble default) emits a vite EBADENGINE warning but
  builds fine; node 20 is preferred but not required.

## Install snapshot on wgs1

- OOD 3.1.16, Apptainer 1.5.0, Apache+PAM on `http://100.78.29.2/`
- `/srv/nivedi/tools/vsnp3` (vsnp3 3.16, fastapi 0.136 / uvicorn 0.49)
- demo project at `/home/vkapur/projects/demo_sars_cov_2`
- temp passwordless sudo at `/etc/sudoers.d/vkapur-temp` — **remove when done**

---

# Round 2 — full bare-tree teardown + tarball reinstall (June 7 2026)

Validated the **distributable tarball** end-to-end: built `vsnp_gui-platform-<ver>.tar.gz`
with `deploy/build_dist.sh`, fully tore down the wgs1 install with the new
`deploy/teardown_ood.sh`, then reinstalled the vSNP GUI + Kraken **from the
unpacked tarball alone**. conda base lives in `/home/vkapur/miniforge3` (outside
`/srv/nivedi`), so the tree wipe left the pkg cache intact and both env rebuilds
hardlinked (fast).

## Result: PASS
- vSNP GUI: one-command `install_ood.sh` from the unpacked tarball → all phases
  green; backend smoke test served the SPA (`GET /` 200) and discovered the
  bundle reference (`GET /api/references` → `NC_045512_wuhan-hu-1`).
- Kraken: `install_kraken.sh app card verify` → env built (kraken2 2.17.1),
  card installed. 8 GB DB deferred (not a code path under test).
- OOD core survived teardown (portal 401, dashboard 301); both cards re-registered.

## Bugs found & fixed this round
- **#7 exit-1-on-success.** `install_ood.sh`, `bootstrap_ood_core.sh`, and the new
  `teardown_ood.sh` all ended on `[[ ${DRY_RUN} -eq 1 ]] && echo ...`, which
  returns 1 when NOT a dry run — so a successful real install exited 1. Latent in
  every prior install. Fixed (explicit `if` + `exit 0`). A CI/wrapper checking the
  exit code would have read success as failure.
- **#8 tarball not one-command installable.** `install_ood.sh` expected the repo to
  already be at `VSNP_GUI_DIR` and only *warned* if absent → unpack-and-run gave a
  half-install (no frontend/backend at the install location). Added **self-populate**:
  when run from an unpacked distributable (`REPO_DIR != VSNP_GUI_DIR`) and the
  target isn't a checkout/copy yet, it rsyncs the source into place. Validated.

## Gaps found
- **#9 private-repo clone under sudo fails — FIXED (branch
  `fix/kraken-clone-as-sudo-user`, validated 2026-06-08).** `install_kraken.sh`
  cloned `KRAKEN_REPO_URL` as root; a private GitHub repo's creds live in the
  invoking user's credential helper, not root's → `fatal: could not read
  Username for https://github.com`. Fix: a `clone_repo()` helper clones as the
  invoking user (`$SUDO_USER`) via `runuser -u "$u" -- env HOME=<their home> git
  clone` — so their creds resolve (on wgs1, vkapur's gh credential helper
  `!~/miniforge3/bin/gh auth git-credential`) — into a tempdir, then relocates
  the tree into the root-owned `KRAKEN_DIR` (`cp -a --no-preserve=ownership`,
  `.git` stripped for parity with the rsync path; the existing `chgrp` sets the
  group). Falls back to a direct clone when not under sudo; emits an actionable
  error (check creds / use `KRAKEN_RSYNC_SOURCE`) on failure.
  - **Validated non-destructively on wgs1**: `install_kraken.sh app` against a
    throwaway `/tmp` site root (bogus `CONDA_BASE`/`NPM_BIN` so only the clone
    runs) → cloned the private repo *as vkapur* while the script ran as root,
    placed `root:nivedi-admins`, `.git` absent, temp dir cleaned. The live
    `/srv/nivedi` install was untouched. Full teardown→reinstall of the kraken
    app still available as a follow-up if end-to-end coverage is wanted.

## New deploy tooling added this round
- `deploy/teardown_ood.sh` — reverse the install (phase-based, `--dry-run`,
  `--yes` gate, `--keep-conda`, `--include-core`, self-relocates so it can't
  delete the running script). Default leaves OOD core intact.
- `deploy/build_dist.sh` — produce `vsnp_gui-platform-<ver>.tar.gz` (git archive +
  injected sample bundle + optional `.sif` + top-level `INSTALL.md`).
- `deploy/admin/claude-sudo-grant.sh` / `claude-sudo-revoke.sh` — non-standing,
  scoped deploy-sudo grant (replaced the old `vkapur-temp` blanket NOPASSWD:ALL).
