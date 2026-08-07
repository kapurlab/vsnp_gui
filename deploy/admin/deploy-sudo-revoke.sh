#!/usr/bin/env bash
# deploy-sudo-revoke.sh — remove the scoped deploy sudo grant installed by
# deploy-sudo-grant.sh, returning the box to a clean steady state (no
# passwordless sudo). Run this at the end of a deploy session.
#
#   Usage:  sudo ./deploy-sudo-revoke.sh
#
# Also removes the grant files earlier versions installed, so a rename can
# never strand an active grant on a box.

set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "must run as root (use sudo)" >&2
  exit 1
fi

removed=0
for f in /etc/sudoers.d/vsnp-deploy /etc/sudoers.d/claude-deploy /etc/sudoers.d/vkapur-temp; do
  if [[ -e "$f" ]]; then
    rm -f "$f"
    echo "Removed $f"
    removed=1
  fi
done

if [[ $removed -eq 0 ]]; then
  echo "No scoped/temp deploy grant present — already clean."
fi
