# OOD app definitions

Source-of-truth copies of the Open OnDemand `batch_connect` app files
deployed on wgs3. These are NOT auto-synced from this repo — edits here
must be `sudo cp`'d to `/var/www/ood/apps/sys/<app>/` on wgs3 to take
effect. Conversely, edits made on wgs3 should be pulled back here.

## Layout

```
ood/apps/
├── vsnp_gui/             ← production card (always `main` branch)
│   ├── manifest.yml
│   ├── form.yml
│   ├── submit.yml.erb
│   ├── view.html.erb
│   └── template/
│       ├── before.sh
│       └── script.sh.erb
└── vsnp_gui_dev/         ← dev card (branch-picker + per-session worktree)
    ├── manifest.yml
    ├── form.yml          ← form has `branch` text input + duration
    ├── submit.yml.erb
    ├── view.html.erb
    └── template/
        ├── before.sh.erb ← creates /tmp/vsnp_gui_dev_<tag>/ worktree from origin/<branch>
        └── script.sh.erb ← uvicorn --reload pointed at the worktree
```

## Deploying changes to wgs3

```bash
# Single file
sudo cp ood/apps/vsnp_gui_dev/template/before.sh.erb \
        /var/www/ood/apps/sys/vsnp_gui_dev/template/before.sh.erb

# Whole app (preserves perms with -p; -a includes recursive + perms)
sudo rsync -av ood/apps/vsnp_gui_dev/ /var/www/ood/apps/sys/vsnp_gui_dev/
```

OOD picks up template-file edits on the next session launch — no service
restart needed. Changes to `manifest.yml` may need a dashboard reload to
refresh the cached card listing.

## How the dev branch-picker works

1. User picks `vSNP GUI (dev)` card → sees a text input for `Git branch`
2. On Launch, `before.sh.erb` runs in the OOD parent process:
   - Validates the branch name (alphanumerics, dots, dashes, underscores, slashes)
   - Fetches it on the prod checkout (`/srv/kapurlab/tools/vsnp_gui`)
   - Creates a detached worktree at `/tmp/vsnp_gui_dev_<timestamp>_<pid>/`
   - Symlinks `frontend/node_modules` from prod so vite doesn't re-install
   - Builds the frontend in the worktree (~3-4s)
   - Opportunistically removes worktrees older than 24h
3. `script.sh.erb` runs in the session container (Apptainer + tmux):
   - Reads `$worktree_dir` exported by before.sh.erb
   - Starts `uvicorn app.main:app --reload --reload-dir app` from `$worktree_dir/backend/`
4. User clicks "Open vSNP GUI" → hits `/rnode/<host>/<port>/` and the worktree's code serves

The dev session has `--reload` so backend `.py` edits hot-reload — useful
for iterating from your laptop without relaunching the OOD session.
Frontend edits still need a `vite build`.
