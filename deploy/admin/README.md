# Admin scripts

System-level admin scripts that live at `/usr/local/sbin/` on wgs3. Canonical copies are tracked here; install with `sudo install` per the table below.

## kapurlab-setup-project.sh

Provisions a new shared project: Unix group, directory tree under `/srv/kapurlab/projects/<name>/`, XFS project quota, append-only audit ledger.

```bash
sudo install -m 0750 -o root -g kapurlab-admins \
  deploy/admin/kapurlab-setup-project.sh \
  /usr/local/sbin/kapurlab-setup-project.sh
```

### Usage

```bash
sudo kapurlab-setup-project.sh <project-name> [user1] [user2] ...
```

Examples:

```bash
# New project, three initial members
sudo kapurlab-setup-project.sh btb_india vivek tod ro

# Add a user to an existing project (idempotent — re-runs are safe)
sudo kapurlab-setup-project.sh btb_india dev
```

### What it does

| Step | Effect |
|---|---|
| 1 | Creates Unix group `proj-<name>` (no-op if it exists) |
| 2 | Adds each named user to `proj-<name>` (no-op if they're already a member) |
| 3 | Creates `/srv/kapurlab/projects/<name>/{download,step1,step2/vcf_source,audit}/` with mode `2770` (setgid) and group `proj-<name>` |
| 4 | Touches `audit/edits.jsonl` and sets `chattr +a` (append-only) |
| 5 | Allocates an XFS project ID in 1000–1999, writes `/etc/projects` and `/etc/projid`, runs `xfs_quota project -s` to associate the directory, sets soft 5 TB / hard 7 TB |

### Conventions

- Project name: lowercase alphanumeric + `_` `-`, starts with a letter, 2–31 chars (e.g. `btb_india`, `brucella_q3`, `lsdv`).
- Disk quota defaults: 5 TB soft / 7 TB hard. Bump per project with `xfs_quota -x -c "limit -p bsoft=… bhard=… proj-<name>" /srv/kapurlab`.
- New group memberships **don't apply to existing OOD sessions**. Users have to log out / log back in (or the admin restarts their PUN) before the new group is visible.

### Removing a project

There's deliberately no `kapurlab-remove-project.sh` — deletion at the lab scale should be a deliberate four-step manual operation:

```bash
# 1. Confirm nobody's using it (no running OOD sessions touching it)
# 2. Snapshot
sudo rsync -aAX --delete /srv/kapurlab/projects/<name>/ /srv/kapurlab/backup/retired/<name>-<date>/
# 3. Remove the data
sudo rm -rf /srv/kapurlab/projects/<name>/
# 4. Drop the quota + group
sudo sed -i "/^proj-<name>:/d" /etc/projid /etc/projects
sudo groupdel proj-<name>
```

If we find ourselves doing this often, write the helper.
