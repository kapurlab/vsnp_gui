# vSNP GUI — OOD sandbox app (Slurm, no admin)

Deploy the vSNP GUI on **any** Open OnDemand HPC as a **per-user sandbox
app** — no root, no `/var/www/` access. The card shows up under
**Develop → My Sandbox Apps** for your account only.

This differs from the production `vsnp_gui` card (which targets wgs3's
`linux_host` adapter and shared `/srv/kapurlab` tree). Here:

- The session runs on a **Slurm compute node** (`submit.yml.erb` has the
  sbatch directives; `form.yml` collects partition/account/cores/mem).
- All paths resolve under **`$HOME`** (or an optional shared group tree via
  `--site-root`). The backend already supports this — see
  `backend/app/config.py` (`VSNP_GUI_SITE_ROOT`, XDG per-user config).

## One-time install on a new HPC

```bash
# 1. Clone the repo anywhere in your $HOME (NOT necessarily ~/ondemand)
git clone <repo-url> ~/vsnp_gui
cd ~/vsnp_gui

# 2. Run the per-user installer (conda env + frontend build + card link)
deploy/setup-sandbox.sh --dry-run        # preview
deploy/setup-sandbox.sh                   # do it
#   add --refs-from user@refserver:/path/vSNP_reference_options  to pull refs
#   add --site-root /path/to/group/space  if you have a shared writable tree

# 3. Set the cluster name (the one site-specific edit)
$EDITOR ood/apps/vsnp_gui_sandbox/form.yml   # cluster: "CHANGE_ME" -> your cluster

# 4. OOD dashboard -> Develop -> My Sandbox Apps -> "vSNP GUI (sandbox)" -> Launch
```

`setup-sandbox.sh` symlinks `ood/apps/vsnp_gui_sandbox/` into
`~/ondemand/dev/vsnp_gui`, so the card appears automatically. (App
development must be enabled for your account — if there's no "Develop"
menu in the OOD top nav, ask your admin to enable it.)

## How the pieces fit

| File | Runs where | Job |
|---|---|---|
| `form.yml` | dashboard | cluster name + Slurm resource fields |
| `submit.yml.erb` | OOD submit | sbatch directives (`--partition`, `--mem`, …) |
| `template/before.sh` | compute node | `find_port` for uvicorn |
| `template/script.sh.erb` | compute node | sources `~/.config/vsnp_gui/sandbox.env`, starts uvicorn |
| `view.html.erb` | dashboard | the "Open vSNP GUI" button |

`~/.config/vsnp_gui/sandbox.env` (written by the installer) tells the
launcher where the code (`VSNP_GUI_DIR`) and conda env (`VSNP3_ENV`) live,
so the repo checkout can sit anywhere and the OOD staged copy still finds it.

## To make it shared later (when you get a group allocation)

Install once into the group space and point everyone at it:

```bash
deploy/setup-sandbox.sh --conda-base /group/space/miniforge3 \
                        --site-root  /group/space/vsnp \
                        --refs-dir   /group/space/vsnp/refs/reference_options
```

`VSNP_GUI_SITE_ROOT=/group/space/vsnp` makes the backend resolve shared
projects/refs/tools there; it falls back to `$HOME` for anything absent.
