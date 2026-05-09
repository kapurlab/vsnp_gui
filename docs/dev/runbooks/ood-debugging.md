# OOD on wgs3 — debugging recipes

Practical recipes that came out of building T-11. Saving them here so we don't re-derive them.

## Architecture refresher (single-node OOD)

- Apache (`apache2.service`) is the front door at port 80.
- For each authenticated user, a per-user **PUN** (Per-User Nginx) is spawned (`nginx: master process (<user>)`). It mediates between the user's browser and that user's apps.
- Each interactive app session (vSNP GUI, etc.) is launched by the **`linux_host` adapter** as a `tmux new-session` running `singularity exec --pid /opt/ood/ondemand/ood_default.sif <user-script>`.
- So every uvicorn process the GUI spawns is **inside a Singularity (Apptainer) container** with a fixed bind-path.

This matters because: the container only sees host paths listed in the cluster config's `singularity_bindpath`. If you add a new mount like `/srv/kapurlab/`, the host sees it but containers don't unless that path is in the bindpath.

## Recipe: inspect a running process's environment

```bash
# 1. Find the PID (pick one form)
ss -tlnp | grep python          # by listening port
ps -ef | grep uvicorn           # by name

# 2. Dump environ (NUL-separated → newlines)
sudo cat /proc/<PID>/environ | tr '\0' '\n'

# 3. Filter
sudo cat /proc/<PID>/environ | tr '\0' '\n' | grep BIND
```

`/proc/<pid>/environ` is what the process inherited at launch — not what your shell currently has. Subsequent `export FOO=…` doesn't change a running process's view.

Related:

```bash
sudo cat /proc/<PID>/cmdline | tr '\0' ' '; echo  # exact argv
sudo readlink /proc/<PID>/cwd                      # current working dir
sudo readlink /proc/<PID>/ns/mnt                   # mount namespace ID
```

## Recipe: what does this container see?

```bash
sudo nsenter -t <PID> --mount ls /srv/             # list /srv from inside the container
sudo nsenter -t <PID> --mount stat /srv/kapurlab   # poke at a specific path
```

Use this when `/api/config` reports a path as invalid even though it exists on the host.

## Recipe: cluster-config didn't take effect

Symptom: edited `/etc/ood/config/clusters.d/wgs3.yml`, restarted Apache, launched a new session — still seeing old config (e.g. `SINGULARITY_BIND` missing the path you added).

Cause: the per-user PUN nginx caches cluster config when it starts. `apache2 restart` doesn't kill PUNs (they're independent nginx masters). New OOD sessions submitted via an old PUN inherit the old cluster config.

Fix:

```bash
# Option A: soft restart (next dashboard request triggers PUN reload)
touch ~/ondemand/restart

# Option B: hard restart (immediate)
sudo pkill -f "nginx.*master process .<user>."

# Then in the browser:
# 1. End any active interactive sessions (My Interactive Sessions → Delete)
# 2. Reload the dashboard tab (Cmd+R)
# 3. Launch a fresh session
```

Verify the new bind landed:

```bash
PID=$(ss -tlnp | awk '/python/{match($0,/pid=([0-9]+)/,a); print a[1]; exit}')
sudo cat /proc/$PID/environ | tr '\0' '\n' | grep BIND
```

## Recipe: editing live OOD config files

`/etc/ood/config/` is owned by root. Edits require sudo. Anything we put there should also be tracked under `deploy/ood/portal/` (lab-wide) or `deploy/ood/clusters.d/` (per-cluster) in the repo.

After edits:

| What changed | What to restart |
|---|---|
| `apps/sys/<app>/manifest.yml` or templates | nothing — read fresh per session |
| `clusters.d/*.yml` | per-user PUN (touch `~/ondemand/restart` or pkill the user's PUN nginx) |
| `ondemand.d/*.yml` | per-user PUN |
| `announcements.d/*.{yml,md}` | nothing — re-rendered on dashboard load |
| `locales/*.yml` | per-user PUN |

## Recipe: backup/rollback an OOD edit

Always copy the previous version *outside* `/var/www/ood/apps/sys/`. Anything under `apps/sys/` with a `manifest.yml` becomes a duplicate menu entry.

Convention:

```bash
sudo cp -a /var/www/ood/apps/sys/vsnp_gui /var/backups/ood/vsnp_gui/$(date +%Y%m%d)/
```

## Recipe: which config file is OOD reading right now?

```bash
ls -la /etc/ood/config/{ood_portal.yml,clusters.d,ondemand.d,announcements.d,apps,locales} 2>&1
```

If the path doesn't exist, OOD silently uses defaults. The dashboard's view-templates live at `/usr/share/ondemand-dex/...` and `/var/www/ood/apps/dashboard/` — read-only, ship with the package; don't edit in place.

## Recipe: the duplicate-menu-entry trap

If two `vSNP GUI` (or any app) entries appear in the menu, look for a stray dir under `/var/www/ood/apps/sys/`:

```bash
sudo ls /var/www/ood/apps/sys/
```

Anything with a `manifest.yml` shows up. Move stray dirs (backups, copies) out:

```bash
sudo mv /var/www/ood/apps/sys/<stray> /var/backups/ood/<app>/<date>/
```
