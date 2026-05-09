# Setting Up Claude Code (SSH Mode) for vSNP GUI

## Prerequisites

1. Tailscale installed and connected on your Mac
2. Claude Code installed: `npm install -g @anthropic-ai/claude-code`
3. SSH access to wgs3 (your key should already be in `~/.ssh/authorized_keys` on wgs3)

## 1. Add wgs3 to your SSH config (~/.ssh/config on your Mac)

```
Host wgs3
    HostName kapurlab-wgs3.tailf38ff4.ts.net
    User vxk1
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
    ServerAliveCountMax 10
```

Test it: `ssh wgs3 "echo connected"`

## 2. Transfer the architecture doc to the repo (first time only)

```bash
scp "/Users/vivekkapur/Desktop/INLEAD Folder/Surveillance and Monitoring/vsnp_gui_architecture_roadmap.docx" \
    wgs3:/home/vxk1/vsnp_gui/docs/dev/
```

## 3. Launch Claude Code in SSH mode

```bash
claude --ssh vxk1@kapurlab-wgs3.tailf38ff4.ts.net --ssh-dir /home/vxk1/vsnp_gui
```

Or using the SSH alias:
```bash
claude --ssh wgs3 --ssh-dir /home/vxk1/vsnp_gui
```

Claude Code will:
- SSH into wgs3
- Change to /home/vxk1/vsnp_gui
- Automatically read CLAUDE.md for project context
- Execute all commands on wgs3 (builds, file edits, tests)

## 4. Useful commands Claude Code will need

```bash
# Rebuild frontend after any frontend change
cd /home/vxk1/vsnp_gui/frontend && npm run build

# Syntax-check backend
/home/vxk1/miniforge3/envs/vsnp3/bin/python -m py_compile backend/app/main.py

# Copy OOD template files (requires sudo)
sudo cp template/before.sh /var/www/ood/apps/sys/vsnp_gui/template/before.sh
sudo cp template/script.sh.erb /var/www/ood/apps/sys/vsnp_gui/template/script.sh.erb
sudo cp view.html.erb /var/www/ood/apps/sys/vsnp_gui/view.html.erb

# Check what's listening (find active uvicorn port)
ss -tlnp

# Check reference options are visible
/home/vxk1/miniforge3/envs/vsnp3/bin/python -c "
import sys; sys.path.insert(0, 'backend')
from app.refs import list_references
from pathlib import Path
refs = list_references(Path('/home/vxk1/miniforge3/envs/vsnp3'))
print(len(refs), 'references:', [r['name'] for r in refs])
"
```

## 5. Testing a change

The fastest test loop:
1. Edit backend or frontend files in the repo
2. Rebuild frontend if needed (`npm run build`)
3. Start a new OOD session at http://100.68.171.59 (Tailscale must be on)
4. Click Connect — the session starts uvicorn fresh from the repo files
5. Test in the browser
6. Check uvicorn logs in the OOD session output if something breaks

For backend-only changes, no frontend rebuild needed.
For OOD template changes (before.sh, script.sh.erb), copy to /var/www/ first.
