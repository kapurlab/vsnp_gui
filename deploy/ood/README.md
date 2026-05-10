# OOD deployment

Mirrors what is deployed on the wgs3 OOD server. The deployed
locations are owned by root; OOD reads from there at session launch
and dashboard render. After editing here, sync to the deployed
locations with sudo.

A proper install script lands with T-08.

## App config (per-app, lives under `/var/www/ood/apps/sys/vsnp_gui/`)

```bash
sudo install -m 0644 deploy/ood/manifest.yml      /var/www/ood/apps/sys/vsnp_gui/manifest.yml
sudo install -m 0644 deploy/ood/form.yml          /var/www/ood/apps/sys/vsnp_gui/form.yml
sudo install -m 0644 deploy/ood/submit.yml.erb    /var/www/ood/apps/sys/vsnp_gui/submit.yml.erb
sudo install -m 0644 deploy/ood/view.html.erb     /var/www/ood/apps/sys/vsnp_gui/view.html.erb
sudo install -m 0755 deploy/ood/template/before.sh        /var/www/ood/apps/sys/vsnp_gui/template/before.sh
sudo install -m 0644 deploy/ood/template/script.sh.erb    /var/www/ood/apps/sys/vsnp_gui/template/script.sh.erb
```

Files:
- `manifest.yml` — app identity (name, icon, category).
- `form.yml` — session form (just session duration today).
- `submit.yml.erb` — batch_connect template type and the params before.sh
  exports for the connection_yml. Currently just `port` (uvicorn).
- `view.html.erb` — the "Open vSNP GUI" button rendered after a session
  is up. Single button now (the noVNC desktop button was retired with
  T-02/T-03 once IGV and the tree viewer moved into the browser).
- `template/before.sh` — runs in the OOD parent process; allocates
  `$port` for uvicorn.
- `template/script.sh.erb` — runs inside the Apptainer/tmux container;
  starts uvicorn. No virtual desktop (Xvfb/x11vnc/websockify) anymore.

## Portal config (lab-wide, lives under `/etc/ood/config/`)

These customize the OOD dashboard for the whole portal. New apps
(kraken, MHC, …) inherit them automatically.

```bash
sudo install -m 0644 deploy/ood/portal/ondemand.d/dashboard.yml /etc/ood/config/ondemand.d/dashboard.yml
sudo install -m 0644 deploy/ood/portal/wgs_pipelines.yml         /etc/ood/config/wgs_pipelines.yml
sudo install -D -m 0644 \
  deploy/ood/portal/apps/dashboard/views/dashboard/index.html.erb \
  /etc/ood/config/apps/dashboard/views/dashboard/index.html.erb

# If migrating from the pre-T-16 setup, also drop the announcement banner —
# the brand strip in the new index.html.erb supersedes it.
sudo rm -f /etc/ood/config/announcements.d/welcome.yml
```

Files:
- `portal/ondemand.d/dashboard.yml` — dashboard title and nav-bar brand
  color. The `pinned_apps` key is intentionally `[]` since the override
  below renders its own Pipelines grid.
- `portal/wgs_pipelines.yml` — declarative source of truth for everything
  the landing page renders: pipeline cards, mocked job rows, data list,
  system metrics, footer copy. Phase 2 reads it as YAML; Phase 3 swaps
  the `jobs:` and `system:` blocks for live JobManager / df / loadavg
  readers.
- `portal/apps/dashboard/views/dashboard/index.html.erb` — full home
  page override (T-16 Phase 2). Replaces OOD's default welcome banner
  + pinned-apps grid with the A2 three-pane layout (Data |
  Pipelines + Active work | System). Rails reloads partials live so
  the change takes effect on the next browser refresh — no service
  restart needed.

## Cluster config (Singularity bind paths)

```bash
sudo install -m 0644 deploy/ood/clusters.d/wgs3.yml /etc/ood/config/clusters.d/wgs3.yml
```

Why: every OOD batch_connect job on wgs3 runs inside `singularity exec --pid /opt/ood/ondemand/ood_default.sif`. The `singularity_bindpath` in this file decides which host paths are visible *inside* the container. If a path isn't listed, the running uvicorn / vsnp3 cannot see it even though it exists on the host.

T-11 added `/srv` to the bindpath so the container can reach `/srv/kapurlab/tools/vsnp3/` and `/srv/kapurlab/refs/`. Future shared mounts under `/srv/kapurlab/projects/` (T-12a) come along for free.

After editing, **users must relaunch their OOD session**; the change applies to new sessions only.

Files:
- `clusters.d/wgs3.yml` — cluster definition for the linux_host adapter (single-node OOD).

## Backups

When updating live OOD files, back up the previous version *outside*
`/var/www/ood/apps/sys/` so OOD doesn't list it as a duplicate app
(any directory under `apps/sys/` with a `manifest.yml` shows up).
Convention: `/var/backups/ood/<app>/<YYYYMMDD>/`.
