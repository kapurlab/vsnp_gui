# Session handover — May 9 2026

Continuation notes for the next Claude session. Read this first, then `docs/dev/TICKETS.md` for the broader plan.

## TL;DR

Milestone A is ~95% done. T-19, T-04, T-11, T-12a all shipped on `web` branch and verified live. **T-21 is mid-flight right now** — fastq-only Mac → wgs3 migration is rsync'ing in a tmux session on Vivek's Mac. **T-07** is parked pending Vivek's red-team review.

State to verify when you pick up:

```bash
# Is T-21 still running?
ssh wgs3 'sudo tail /srv/kapurlab/audit/t21-migration.jsonl'

# What's transferred per project?
ssh wgs3 'for p in linglnig m5_test nagalingam_02272026 nagalingam_03242026 nanopore_bovis quick6_mtbc0; do sudo du -sh /srv/kapurlab/projects/$p/download/ 2>/dev/null; done'

# tmux session on Mac
tmux ls
tmux attach -t t21    # pane shows the live python script tee'd to /tmp/t21-run.log
```

Expected end-state: each `download/` dir matches the source on the Mac (sizes in `/tmp/t21-edited-manifest.tsv`, `size` column).

## What landed this session

| Ticket | Commit | What |
|---|---|---|
| T-19 | post-recon during session | Storage layout: `/home` on nvme2n1 (3.7 TB XFS), `/srv/kapurlab` on sda (10.9 TB XFS), `/srv/kapurlab/backup` on sdb (5.5 TB XFS), swap on nvme1n1 (953 GB). NVMe enumeration shifted across reboot — irrelevant because fstab is UUID-based. |
| T-04 | `933ba9e` | Per-user `~/.config/vsnp_gui/config.json`. Removed `/home/vxk1/` literals from backend. |
| T-11 | `60bb85f` `a727790` | Shared vsnp3 + fastapi env at `/srv/kapurlab/tools/vsnp3` (also serves uvicorn now). Refs at `/srv/kapurlab/refs/vsnp3/reference_options/`. **Singularity bindpath gotcha**: `/etc/ood/config/clusters.d/wgs3.yml` had to add `/srv` so the OOD container could see it. |
| T-11 follow-up | `b635971` | Shared vsnp_gui clone at `/srv/kapurlab/tools/vsnp_gui/` so any user (not just vxk1) finds the source. |
| T-12a | `c546efb` | Linux groups (`kapurlab-members`, `kapurlab-admins`, `proj-*`), `kapurlab-setup-project.sh`, multi-root `list_projects` + `_project_dir_for`, `shared_projects_root` config field, "shared" badge in frontend. Also: backfill missing keys from DEFAULTS in `load_config`. |
| T-12a fixes | `5b06a04` `182ac91` | `kapurlab-add-user.sh` for SSH self-loopback bootstrap (OOD's `linux_host` adapter requires it). Always derive `name` from dirname in list_projects. setup-project writes `project.json`. |
| T-21 ph1 | `711dac4` | Mac project manifest script. |
| T-21 ph2 | `e5477db` `2681b40` | Migration script with `--fastq-only`. |
| Cosmetic | `2c10abf` | Welcome banner switched to `.yml` form (OOD 3.1.16 frontmatter parser was eating `.md`). |

`web` branch HEAD: see `git log --oneline -10 origin/web` — last commit was the `--fastq-only` flag (`2681b40`). **Newer fixes on the migration script are NOT yet committed** — see "Uncommitted on Mac" below.

## Uncommitted code

**On the Mac at `/tmp/t21-mac-migrate.py`** has fixes not yet in repo:
- macOS rsync (`openrsync`, advertises 2.6.9 compat) doesn't know `--info=stats` / `--append-verify` → use plain `-v`, no `--append-verify`.
- Vxk1 isn't owner of dest dirs → `-a` triggers "failed to set times/perms" warnings → final flag set:
  ```
  rsync -rltDHv --partial --omit-dir-times --no-perms --no-owner --no-group ...
  ```

**On wgs3 in repo at `deploy/admin/kapurlab-setup-project.sh`** has the chattr-+a workaround (drop +a before bulk chown, re-set after) — committed as `182ac91` already.

**Action for next session**: `scp` the updated `/tmp/t21-mac-migrate.py` from the Mac into the repo (`deploy/admin/t21-mac-migrate.py`), commit with the rsync-flags rationale.

## Open items, in priority order

1. **Verify T-21 finished** (or restart it if a network blip stopped tmux). When it's done, audit log shows 6 entries with `"status":"ok"` and remote `download/` sizes match source. If anything mismatches, `--partial` makes re-runs resumable.
2. **Commit the updated migration script** (see above).
3. **Remove the now-stale projects on the Mac** (Vivek's call — wait for him to verify the destination before he wipes the source). Don't touch Mac's `/Users/vivekkapur/vsnp3/projects/` from the script side.
4. **T-07 Run provenance** — Vivek wants to run the proposed scope through a red-team council before implementing. Don't start until he gives the go-ahead.
5. **Optional cleanup**:
   - `/root/home-pre-T19.tar.zst` on wgs3 (15 GB pre-migration backup) — fine to delete after a few stable days.
   - The earlier rsync-to-wgs2 of the home tar: `/tmp/wgs2-backup.log` exists but the rsync was killed mid-run during debugging; if you want lab-to-lab redundancy, restart it (`scripts/rsync-tar-to-wgs2.sh` doesn't exist yet — would be a small follow-up).

## Live system state

- OOD apps: only `vSNP GUI`. Pinned, "Bioinformatics" category.
- Users: `vxk1` (admin), `ro_test` (project member, in `proj-sanity_test`). Other accounts in `/home/` are pre-existing system users (`agitpsu`, `swi101`, `tks5563`, `install`) — not lab users.
- Test project: `sanity_test` under `/srv/kapurlab/projects/`, populated with the 7 SARS-CoV-2 deer samples for smoke testing. Verified end-to-end.
- vsnp3 install: `/srv/kapurlab/tools/vsnp3/` v3.16. **Patched** with our column[0] + VSNP3_BOOTSTRAP fixes (see `deploy/vsnp3-patches/`). `apply.sh` is idempotent — re-run after any conda update.
- Migration in flight: tmux session `t21` on Vivek's Mac running `t21-mac-migrate.py --fastq-only --execute`. Currently transferring `linglnig` (~844 MB / 1.1 GB at last check). Expected total: ~30 GB across 6 projects, ~20-30 min over Tailscale.

## Key recipes from this session not yet runbook'd

These belong in a `docs/dev/runbooks/` doc when there's time:

### macOS openrsync vs full rsync

Mac's bundled rsync is `openrsync` (BSD-licensed clone, advertises `rsync 2.6.9 compatible`). Doesn't know modern flags. For Mac-side scripts, use the lowest-common-denominator set:
```
rsync -rltDHv --partial --omit-dir-times --no-perms --no-owner --no-group
```
If you really need modern features, `brew install rsync` gets you 3.x at `/opt/homebrew/bin/rsync` — prepend that to PATH explicitly.

### chattr +a + chown -R interaction

`chown -R` against a directory containing chattr-+a files exits non-zero with `Operation not permitted`. Pattern: drop `+a`, do bulk ops, re-apply `+a`. See `deploy/admin/kapurlab-setup-project.sh` step 3 for the working version.

### OOD `linux_host` adapter requires SSH self-loopback

Every new lab user needs `~/.ssh/id_ed25519` and that pubkey in their own `authorized_keys`. `kapurlab-add-user.sh` handles this end-to-end — use it, don't `useradd` manually.

### OOD cluster config caching

PUNs cache `/etc/ood/config/clusters.d/*.yml` at PUN-spawn time. After editing the cluster YAML, **also** kill the affected user's PUN nginx (or `touch ~/<user>/ondemand/restart`). `apache2 restart` does NOT cycle PUNs.

### Singularity bindpath gotcha

OOD's `linux_host` adapter wraps every batch_connect session in `singularity exec --pid /opt/ood/ondemand/ood_default.sif`. The container only sees host paths in `singularity_bindpath` (cluster YAML). Adding a host mount under `/srv` requires `/srv` in the bindpath. Already done; if a future host mount lands somewhere else (`/data/`, `/scratch/`, etc.), bindpath needs that too.

## Files of note

- **Source of truth**: `docs/dev/MULTIUSER.md` + `docs/dev/TICKETS.md` on `web` branch. Read these before making architectural decisions.
- **Runbooks**: `docs/dev/runbooks/{T-19-storage-layout.md, ood-debugging.md}`.
- **Admin scripts**: `deploy/admin/kapurlab-{setup-project,add-user,t21-mac-manifest,t21-mac-migrate}.sh|.py`.
- **OOD deploy artifacts**: `deploy/ood/{template,portal,clusters.d}/`.
- **vsnp3 patches**: `deploy/vsnp3-patches/{v3.16-kapurlab.patch,apply.sh,README.md}`.
- **Live working tree on wgs3**: `/home/vxk1/vsnp_gui/` (vxk1's dev clone).
- **Live deploy clone on wgs3**: `/srv/kapurlab/tools/vsnp_gui/` (what every user's session runs from). After `git push origin web`, run `git pull && npm run build` here.

## What to do first when you pick up

1. `git -C /home/vxk1/vsnp_gui pull` — get latest committed state.
2. Check T-21 status (commands at top). If finished cleanly: commit the updated migration script, write a brief summary, mark T-21 complete in TICKETS.md.
3. If migration is stuck or failed mid-project: re-run with `--execute` again — `--partial` resumes. Re-check audit log for `"status":"ok"` per project.
4. Wait for Vivek's red-team review on T-07 before starting.

That's the lot. Good luck.
