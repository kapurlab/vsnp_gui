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
sudo mkdir -p /etc/ood/config/announcements.d
sudo install -m 0644 deploy/ood/portal/announcements.d/welcome.yml /etc/ood/config/announcements.d/welcome.yml
```

Files:
- `portal/ondemand.d/dashboard.yml` — dashboard title (`Kapur Lab Pipelines`),
  brand color, pinned-apps config.
- `portal/announcements.d/welcome.yml` — YAML banner (use .yml not .md — OOD 3.1.16 parses YAML frontmatter inconsistently from .md) above the dashboard.

## Backups

When updating live OOD files, back up the previous version *outside*
`/var/www/ood/apps/sys/` so OOD doesn't list it as a duplicate app
(any directory under `apps/sys/` with a `manifest.yml` shows up).
Convention: `/var/backups/ood/<app>/<YYYYMMDD>/`.
