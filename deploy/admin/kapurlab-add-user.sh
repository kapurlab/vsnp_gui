#!/bin/bash
# kapurlab-add-user.sh — add a Linux user, set lab group memberships, and
# bootstrap SSH self-loopback (required by OOD's linux_host adapter).
#
# Usage:
#   sudo kapurlab-add-user.sh <username> [--admin] [--password <pw>] [--project <name>]...
#
# Example:
#   sudo kapurlab-add-user.sh tod --admin
#   sudo kapurlab-add-user.sh dev --project mtbc_v1 --project mhc_v1
#
# Idempotent: re-running adds groups / regenerates SSH key only if missing.

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "must run as root (or under sudo)" >&2
  exit 1
fi

if [ $# -lt 1 ]; then
  echo "usage: $0 <username> [--admin] [--password <pw>] [--project <name>]..." >&2
  exit 1
fi

USER_NAME="$1"; shift
IS_ADMIN=0
PASSWORD=""
PROJECTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --admin) IS_ADMIN=1; shift ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --project) PROJECTS+=("$2"); shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

if ! [[ "$USER_NAME" =~ ^[a-z][a-z0-9_-]{1,30}$ ]]; then
  echo "invalid username: must be lowercase alnum/_/-, start with a letter, 2-31 chars" >&2
  exit 1
fi

# 1. user
if id "$USER_NAME" >/dev/null 2>&1; then
  echo "[ok] user $USER_NAME exists"
else
  useradd -m -s /bin/bash -c "Kapur Lab user (provisioned $(date +%Y-%m-%d))" "$USER_NAME"
  echo "[new] user $USER_NAME created"
fi

# 2. password (PAM auth for OOD basic-auth)
if [ -n "$PASSWORD" ]; then
  echo "$USER_NAME:$PASSWORD" | chpasswd
  echo "[set] password set from --password"
elif ! sudo -u "$USER_NAME" bash -c 'true' >/dev/null 2>&1; then
  GENERATED="$(openssl rand -base64 12)"
  echo "$USER_NAME:$GENERATED" | chpasswd
  echo "[set] generated password: $GENERATED"
  echo "      (rotate with: sudo passwd $USER_NAME)"
fi

# 3. groups
for g in kapurlab-members "${PROJECTS[@]/#/proj-}"; do
  if [ -z "$g" ] || [ "$g" = "proj-" ]; then continue; fi
  if ! getent group "$g" >/dev/null; then
    echo "[warn] group $g doesn't exist; create the project first with kapurlab-setup-project.sh" >&2
    continue
  fi
  if id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$g"; then
    echo "[ok] $USER_NAME already in $g"
  else
    usermod -aG "$g" "$USER_NAME"
    echo "[add] $USER_NAME -> $g"
  fi
done

if [ "$IS_ADMIN" -eq 1 ]; then
  if id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "kapurlab-admins"; then
    echo "[ok] $USER_NAME already in kapurlab-admins"
  else
    usermod -aG kapurlab-admins "$USER_NAME"
    echo "[add] $USER_NAME -> kapurlab-admins"
  fi
fi

# 4. SSH self-loopback (required by OOD linux_host adapter)
HOMEDIR="$(getent passwd "$USER_NAME" | cut -d: -f6)"
SSH_DIR="$HOMEDIR/.ssh"
sudo -u "$USER_NAME" mkdir -p "$SSH_DIR"
sudo -u "$USER_NAME" chmod 700 "$SSH_DIR"
if [ ! -f "$SSH_DIR/id_ed25519" ]; then
  sudo -u "$USER_NAME" ssh-keygen -t ed25519 -N "" -f "$SSH_DIR/id_ed25519" \
    -C "$USER_NAME@$(hostname) (OOD localhost loopback)" >/dev/null
  echo "[new] SSH key generated for $USER_NAME"
fi
sudo -u "$USER_NAME" touch "$SSH_DIR/authorized_keys"
sudo -u "$USER_NAME" chmod 600 "$SSH_DIR/authorized_keys"
PUBKEY="$(cat "$SSH_DIR/id_ed25519.pub")"
if ! sudo -u "$USER_NAME" grep -qxF "$PUBKEY" "$SSH_DIR/authorized_keys"; then
  echo "$PUBKEY" | sudo -u "$USER_NAME" tee -a "$SSH_DIR/authorized_keys" >/dev/null
  echo "[set] pubkey added to authorized_keys"
fi

# 5. smoke
if sudo -iu "$USER_NAME" ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes localhost true 2>/dev/null; then
  echo "[ok] ssh $USER_NAME@localhost works (OOD linux_host adapter ready)"
else
  echo "[warn] ssh $USER_NAME@localhost failed — investigate before letting the user log in" >&2
fi

echo
echo "Done."
echo "  User:    $USER_NAME"
echo "  Groups:  $(id -nG "$USER_NAME")"
echo "  Tell the user to access OOD at http://100.68.171.59/ with their password."
