# Session handover — June 7–8 2026

> **NEXT-SESSION CLAUDE:** Read this, then **ask Vivek what to prioritize and
> confirm before making changes.** Don't start a big thread autonomously. The
> June 6–7 handover (below) remains valid for the deeper backlog.

This session did three things, all validated live with Vivek:

## 1. wgs1 test-artifact cleanup — DONE
The three owed cleanups (see June 6–7 section) are cleared: blanket `vkapur-temp`
NOPASSWD:ALL removed, PAM password rotated, wgs3 stray transfer key stripped.
New **non-standing scoped sudo** workflow: `deploy/admin/claude-sudo-grant.sh`
(re-grant on demand; needs Vivek's password now) + `claude-sudo-revoke.sh`.
wgs1 ends in a **clean steady state — no standing passwordless sudo**.

## 2. Teardown + tarball reinstall — VALIDATED end-to-end
- New `deploy/teardown_ood.sh` (phase-based, `--dry-run`/`--yes`, `--keep-conda`,
  `--include-core`, self-relocating) and `deploy/build_dist.sh` (the §8 tarball:
  git archive + injected demo bundle + `INSTALL.md`).
- Full bare-tree teardown of `/srv/nivedi`, then reinstall **from the tarball
  alone** → vSNP GUI + Kraken both rebuilt, backend serves the SPA + discovers
  the reference, Kraken env + 8 GB DB in place. wgs1 is a fresh, working NIVEDI
  install right now.
- **5 findings** (`docs/deploy/WGS1_TEST_RESULTS.md` Round 2): #7 exit-1-on-success
  in `install_ood.sh`/`bootstrap_ood_core.sh`/`teardown_ood.sh` (**fixed**); #8
  tarball not one-command → `install_ood.sh` **self-populate added** (fixed); #9
  `install_kraken.sh` can't clone a private repo under sudo (**NOT fixed** — clone
  as `$SUDO_USER`; workaround = `KRAKEN_RSYNC_SOURCE`); #10 stale Kraken DB URL in
  `site.conf.example` (**fixed** → 20250402).

## 3. vSNP GUI file-listing bug — FIXED
The inline project→sample expansion only showed Kraken outputs and only for
Kraken'd samples. `frontend/src/App.jsx`: every sample is now expandable and
shows a **vSNP outputs** section (Step 1/2 files) + a **Kraken outputs** section.
Rebuilt + deployed to wgs1; Vivek confirmed it works.

## wgs1 access (corrects the June 6–7 note)
- SSH: **`ssh -i ~/.ssh/gb10_key vkapur@100.78.29.2`** (the old handoff named the
  wrong key — `gb10_key`, not `kapur_wgs_key`).
- OOD: `http://100.78.29.2/`, login `vkapur` + the password Vivek set this session
  (NOT in git). vkapur is now a `nivedi-admins` member; loopback SSH verified.
- Re-grant sudo for deploy work: `sudo ~vkapur/claude-sudo-grant.sh` (kept in
  vkapur's home, plus `claude-sudo-revoke.sh`, `teardown_ood.sh`, `site.conf.wgs1-test`).

## Open for next session
- **Commit/PR:** this session's work — `deploy/teardown_ood.sh`, `deploy/build_dist.sh`,
  `deploy/admin/claude-sudo-{grant,revoke}.sh`, fixes in `deploy/install_ood.sh` /
  `deploy/bootstrap_ood_core.sh` / `deploy/site.conf.example`, `frontend/src/App.jsx`,
  and the doc updates. (Check whether it landed in git or is still uncommitted.)
- **Finding #9** (kraken clone-as-`$SUDO_USER`) — needs a fix + its own test cycle.
- **Roar import** — the next big thread (vSNP3 `_zc.vcf` import without re-running;
  Phase-0 inventory scan first). Access to Roar still to be sorted.
- Plus the June 6–7 backlog below (packaging tarball→release, PR #2 merge, branding,
  dead-code review, T-27, PLATFORM_PROPOSAL).

---

# Session handover — June 6–7 2026

Continuation notes. Read this first, then [`TICKETS.md`](TICKETS.md),
[`TOD_QUEUE.md`](TOD_QUEUE.md), and the new
[`../deploy/INSTALL_OOD.md`](../deploy/INSTALL_OOD.md).

Prior handoff (June 4) is preserved at commit `54cc2cd` in git history. This
session was very long and did one big thing: built and **bare-metal-validated**
a site-parameterized OOD install playbook so the whole platform (OOD core +
vSNP GUI + Kraken) can be stood up at a new site (NIVEDI) from a bare box — and
fixed ~16 real bugs that the hands-on testing surfaced along the way.

---

## TL;DR — what landed

Everything is on branch **`feature/ood-install-playbook`** → **PR #2**
(`kapurlab/vsnp_gui`), HEAD **`cfb865b`**. **Not merged.** CodeRabbit will
review. The branch was rebased onto current `origin/main` (folded in the 17
dev-server commits that prod was behind: Kraken-in-vSNP, Step 2 `vcf_database`
rename, server-side folder browser, the fastq-symlink-500 fix).

**The install playbook** (`deploy/`):
- `bootstrap_ood_core.sh` — OOD core on a bare Ubuntu noble box (OSC apt repo,
  Apache + PAM basic-auth, Apptainer, portal). Layers 1–2.
- `install_ood.sh` — vSNP toolchain + OOD app layer, parameterized via
  `site.conf` token substitution. Idempotent, `--dry-run`, phase-based. Layers 3–4.
- `install_kraken.sh` — the Kraken ID Parse app as a second OOD card (T-30).
- `site.conf.example` — every site-specific knob (neutral template).
- `sample_data/` — `build_bundle.sh` + `install_bundle.sh` + the runnable
  SARS-CoV-2 demo (1 reference + 6 fastqs).
- `kraken/environment-full.yml` — the COMPLETE kraken conda env captured from
  wgs3 (the repo's own `conda_setup/environment.yml` is incomplete).

**Docs** (`docs/deploy/`): `INSTALL_OOD.md` (the runbook), `WGS3_AUDIT.md` (the
reference-install audit + literal→variable map), `WGS1_TEST_RESULTS.md`.

**Validated end-to-end on a bare wgs1** (Ubuntu 24.04, no OOD, empty `/srv`)
as `SITE_NAME=nivedi` / `/srv/nivedi` — TWICE:
1. A first install (hand-patched mid-stream — the _source_ of the fixes).
2. A full **bare-metal teardown + hands-off reinstall** from the committed
   branch — OOD core + vSNP + Kraken, both apps launching and running.

---

## The ~16 bugs found & fixed (the real value of the test)

All are in the branch. The deeper write-up is in `docs/deploy/WGS1_TEST_RESULTS.md`.

**Install phase:** bootstrap portal-verify now follows redirects (301→401);
toolchain pip-installs only the web layer so conda owns the scientific stack
(was building numpy from source on py3.14); `install_bundle` chowns the user's
projects root; `config.py` derives the shared root from `$VSNP_GUI_SITE_ROOT`
(default `/srv/kapurlab`, so wgs3 is unchanged); the OOD launcher exports it;
`100.68.171.59`→`SERVERNAME` added to the substitution map.

**Runtime (found by actually using the GUI):**
- **PAM login** — `www-data` added to the `shadow` group (else _every_
  basic-auth login fails on a fresh box).
- **Session showed "Completed", no Connect button** — the linux_host adapter's
  `parse_hostname` only accepts dotted FQDNs; `render_cluster_yml` now emits
  FQDNs **last** in `ssh_hosts` (a bare short hostname parsed to an empty job
  host → OOD polled nothing).
- `snp-dists` installed into the vsnp3 env (Step 2 needs it; not a vsnp3 dep).
- Frontend "Pick a folder on wgs3" → "...on the server".
- **Backend `/srv/kapurlab` defaults** in `provenance_writer.py` / `jobs.py` /
  `main.py` (Step 1 500'd) now derive from `config.SITE_ROOT`.
- Provenance degrades gracefully on a non-git (tarball) deploy instead of
  hard-failing Step 1.
- **Kraken app's own `/srv/kapurlab`** paths (its `config.py` DB defaults etc.)
  parameterized at install time.
- **Kraken env completeness** — build from the full export, not the repo's
  incomplete spec; plus always pip-install the kraken backend web deps
  (`aiofiles` wasn't captured by `conda env export`).
- **T-46 junk-fastq floor 1 MB → 50 KB, configurable** (`step1_min_fastq_bytes`)
  — the 1 MB floor wrongly auto-skipped legitimate ~200 KB viral/amplicon reads,
  blocking Step 1 on the demo (and on any small-genome data).
- **Step 1 double-dispatch race** — a fast double-click started two batches
  racing in the same dirs (corrupt BAM, missing temp files). Fixed with an
  atomic dispatch lock (backend) + a `useRef` re-entry guard (frontend).

---

## wgs1 state RIGHT NOW (a standing NIVEDI-parameterized install)

wgs1 currently runs a full working install. Decide next session whether to keep
it as a reference or tear it down.

- OOD 3.1.16 + Apache/PAM on `http://100.78.29.2/`; Apptainer 1.5.0.
- `/srv/nivedi/tools/{vsnp3,vsnp_gui,kraken_id_parse_gui}`,
  `/srv/nivedi/databases/kraken2/k2_standard_08gb` (8 GB), `/srv/nivedi/refs`,
  `/srv/nivedi/projects`, groups `nivedi-{members,admins}`.
- Both OOD cards live: **vSNP GUI** + **Kraken ID Parse**. Demo project
  `demo_sars_cov_2` under `/home/vkapur/projects`.
- vsnp3 env = Python 3.14; kraken env = Python 3.10 (separate by necessity —
  incompatible interpreters; they share the conda pkg cache so identical-version
  tools are hardlinked, not duplicated).

### ✅ Cleanup DONE (June 6 late session)
All three temporary test artifacts cleared:
- `/etc/sudoers.d/vkapur-temp` (blanket `vkapur ALL=(ALL) NOPASSWD:ALL`) **removed**
  on wgs1, replaced by a non-standing, scoped grant managed by
  `deploy/admin/claude-sudo-grant.sh` / `claude-sudo-revoke.sh`. The scoped grant
  (`/etc/sudoers.d/claude-deploy`) permits NOPASSWD only on the vetted deploy
  scripts (`bootstrap_ood_core.sh`, `install_ood.sh`, `install_kraken.sh`,
  `teardown_ood.sh`) + `apt-get` + `systemctl reload apache2`. Those scripts are
  `root:root 0755` in a `root:nivedi-admins drwxr-sr-x` dir (vkapur can't edit or
  swap them), so the scoping is real least-privilege. Re-grant on demand:
  `sudo ~/claude-sudo-grant.sh` (needs vkapur's password now that blanket is gone).
- vkapur's PAM password **rotated** off the old browser-test value (which was
  committed here — do not reuse it). The new value is shared only in the session
  transcript, NOT recorded in git; store it in a password manager.
- wgs1→wgs3 transfer key **removed** from wgs3 `~vxk1/.ssh/authorized_keys`
  (line was `vkapur@a8-an-vxk1-u3`, mislabeled "OOD localhost loopback"). Backup
  at `~vxk1/.ssh/authorized_keys.bak-20260606-235615`; 3 legit keys remain.

---

## Open items for the next session (in rough priority — CONFIRM with Vivek first)

1. ~~**Cleanup** the three test artifacts above.~~ ✅ DONE (see above).
2. **Packaging** — the original ask was a "tarball + roadmap" a sysadmin can
   install out-of-the-box. The scripts + docs + sample bundle exist; what's NOT
   done is bundling them into an actual distributable artifact (a `make dist` /
   release tarball with the repo + sample data + `INSTALL.md` at the top). The
   `git archive` recipe is sketched in `INSTALL_OOD.md §8` but not built.
3. **Review + merge PR #2.** It's large; CodeRabbit will pre-review. Decide the
   merge gate (it touches main backend files — config.py, main.py, etc.).
4. **Decide wgs1's fate** — keep the nivedi install as a demo/reference, or tear
   it down (the bare-metal procedure is proven).
5. **Branding** — `wgs_pipelines.yml` + dashboard `index.html.erb` still carry
   KapurLab/Penn State prose; `install_ood`'s portal phase flags them for manual
   review. NIVEDI will want their own copy.
6. **NIVEDI actual deployment** — still no NIVEDI SSH. Everything is a dry-run
   against wgs1; first NIVEDI contact should `--dry-run` and expect a few
   environment edge cases.
7. **The two T-27 red-team UNRESOLVEDs** (`redteam/DECISIONS.md`) — untouched
   again this session; still blocking T-27.
8. **PLATFORM_PROPOSAL** — Tod's six drafted edits still not applied / doc still
   not in the repo (carried over from the June 4 handoff).

### For Tod (add to TOD_QUEUE.md when you message him)
- `kraken_id_parse_gui` `conda_setup/environment.yml` is **incomplete** — the
  working env has ~40 more conda packages + the FastAPI web deps (aiofiles) that
  were added after `env create`. A fresh env-create from it yields a broken
  pipeline. He should reconcile it upstream (our installer works around it by
  shipping `deploy/kraken/environment-full.yml`).
- `tectonic` is **stale** in the kraken pipeline's `REQUIRED_TOOLS` — wgs3
  doesn't have it and the reports are HTML/PDF (`render_pdf_report`), so it just
  prints a spurious "missing" warning. Drop it or make it a soft warning.
- The `--kraken-only` "Krona graph generated at None" cosmetic (output-path var
  not threaded) — still open from June 4.

---

## Commits on the branch (newest first)

```
cfb865b step1: make dispatch atomic (lock) + frontend re-entry guard
63bb2ff step1: lower the T-46 junk-fastq floor 1MB->50KB, make it configurable
aa1455c deploy: kraken installer always installs backend web requirements
ad61ac5 deploy: kraken installer builds from complete env export + parameterizes app
8d95492 deploy: kraken runtime extras + dashboard tile available
0dab9b9 deploy: add Kraken ID Parse app installer (T-30)
cd57544 deploy: site-parameterized OOD install playbook (T-49/T-50)
```

(`cd57544` was rebased onto current `origin/main` mid-session — it carries the
vSNP backend parameterization + the playbook; the later commits add Kraken and
the runtime fixes.)

## Verify when picking up

```sh
git fetch origin --prune
gh pr view 2 --repo kapurlab/vsnp_gui            # PR #2, the playbook
git log --oneline origin/main..origin/feature/ood-install-playbook

# wgs1 standing install (read-only checks)
ssh wgs1 'curl -s -o /dev/null -w "%{http_code}\n" http://100.78.29.2/pun/sys/dashboard/'  # 302/200 chain
ssh wgs1 'sudo ls /var/www/ood/apps/sys/ | grep -E "vsnp_gui|kraken_id_parse_gui"'
ssh wgs1 'ls /etc/sudoers.d/vkapur-temp'         # the grant to remove
```

> wgs1 SSH from Vivek's Mac is `ssh wgs1` (alias in `~/.ssh/config`, key
> `~/.ssh/kapur_wgs_key`, user vkapur). vkapur has passwordless sudo there *only*
> because of the temp grant above — removing it reverts to password sudo.

## Anchor docs (read next, in order)
- [`../deploy/INSTALL_OOD.md`](../deploy/INSTALL_OOD.md) — the install runbook.
- [`../deploy/WGS3_AUDIT.md`](../deploy/WGS3_AUDIT.md) — reference-install audit +
  the literal→`site.conf` parameterization map.
- [`../deploy/WGS1_TEST_RESULTS.md`](../deploy/WGS1_TEST_RESULTS.md) — the bug log.
- [`TICKETS.md`](TICKETS.md), [`TOD_QUEUE.md`](TOD_QUEUE.md),
  [`redteam/DECISIONS.md`](redteam/DECISIONS.md).
