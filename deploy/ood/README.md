# OOD batch_connect templates

These files mirror what is deployed at
`/var/www/ood/apps/sys/vsnp_gui/` on the wgs3 OOD server.

The deployed location is owned by root; OOD reads from there at session
launch. After editing here, sync to the deployed location with sudo.

## Sync script

```bash
sudo install -m 0644 deploy/ood/manifest.yml      /var/www/ood/apps/sys/vsnp_gui/manifest.yml
sudo install -m 0644 deploy/ood/form.yml          /var/www/ood/apps/sys/vsnp_gui/form.yml
sudo install -m 0644 deploy/ood/submit.yml.erb    /var/www/ood/apps/sys/vsnp_gui/submit.yml.erb
sudo install -m 0644 deploy/ood/view.html.erb     /var/www/ood/apps/sys/vsnp_gui/view.html.erb
sudo install -m 0755 deploy/ood/template/before.sh        /var/www/ood/apps/sys/vsnp_gui/template/before.sh
sudo install -m 0644 deploy/ood/template/script.sh.erb    /var/www/ood/apps/sys/vsnp_gui/template/script.sh.erb
```

A proper install script lands with T-08.

## Layout

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
