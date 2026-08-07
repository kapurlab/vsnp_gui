#!/usr/bin/env bash
# deploy-sudo-grant.sh — grant a deploy operator NOPASSWD sudo, scoped to the
# vetted vSNP-GUI install/teardown scripts only (NOT blanket ALL).
#
# This replaces a standing blanket `ALL=(ALL) NOPASSWD:ALL` grant with a
# non-standing, least-privilege one that an admin re-installs on demand at the
# start of a deploy session and removes afterwards (deploy-sudo-revoke.sh).
#
#   Usage:
#     sudo ./deploy-sudo-grant.sh [options]
#
#   Options:
#     --user NAME      operator to grant (default: $SUDO_USER; required if
#                      this is not run through sudo)
#     --deploy-dir DIR vsnp_gui/deploy directory holding the scripts
#                      (default: auto-detect under /srv/*/tools/vsnp_gui/deploy)
#     --dry-run        print the sudoers file that would be installed; touch nothing
#     -h | --help      this help
#
#   What it permits (NOPASSWD, run as root):
#     <deploy>/bootstrap_ood_core.sh   <deploy>/install_ood.sh
#     <deploy>/install_kraken.sh       <deploy>/teardown_ood.sh
#     plus /usr/bin/apt-get and `systemctl reload apache2` (raw ops the
#     install/teardown scripts can't wrap when re-bootstrapping core).
#
#   SECURITY NOTE: NOPASSWD on a script the operator can EDIT is equivalent to
#   full root (they could rewrite the script). This script therefore chowns the
#   listed scripts to root:root 0755 so they can't be edited without sudo. The
#   containing directory is left as-is; if the operator owns it they could still
#   swap a script file. For true isolation, stage the scripts under a root-owned
#   path (e.g. /opt/vsnp-deploy) and point --deploy-dir there. On a single
#   single-admin box this grant's main win is being NON-STANDING, not blanket.

set -euo pipefail

SUDOERS_FILE=/etc/sudoers.d/vsnp-deploy
GRANT_USER="${SUDO_USER:-}"
DEPLOY_DIR=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)       GRANT_USER="$2"; shift 2 ;;
    --deploy-dir) DEPLOY_DIR="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)    sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "must run as root (use sudo)" >&2
  exit 1
fi

# No default operator: guessing one would grant sudo to an account nobody named.
# $SUDO_USER covers the normal `sudo ./deploy-sudo-grant.sh` case; a direct root
# shell has to say who.
if [[ -z "$GRANT_USER" ]]; then
  echo "no operator to grant: pass --user NAME (or run this through sudo)" >&2
  exit 1
fi
if ! id -u "$GRANT_USER" >/dev/null 2>&1; then
  echo "no such user: $GRANT_USER" >&2
  exit 1
fi

# Auto-detect the deploy dir if not given.
if [[ -z "$DEPLOY_DIR" ]]; then
  for d in /srv/*/tools/vsnp_gui/deploy; do
    [[ -f "$d/install_ood.sh" ]] && DEPLOY_DIR="$d" && break
  done
fi
if [[ -z "$DEPLOY_DIR" || ! -d "$DEPLOY_DIR" ]]; then
  echo "deploy dir not found; pass --deploy-dir /path/to/vsnp_gui/deploy" >&2
  exit 1
fi
DEPLOY_DIR="$(cd "$DEPLOY_DIR" && pwd)"   # normalize to absolute

# The vetted scripts. teardown_ood.sh may not exist yet — listing a path that
# doesn't exist is harmless (sudo just never matches it until it's created).
SCRIPTS=(
  "$DEPLOY_DIR/bootstrap_ood_core.sh"
  "$DEPLOY_DIR/install_ood.sh"
  "$DEPLOY_DIR/install_kraken.sh"
  "$DEPLOY_DIR/teardown_ood.sh"
)

# Harden: root-own each existing script so the operator can't edit-then-run as
# root. (Closes the easy escalation; see SECURITY NOTE re: the parent dir.)
if [[ $DRY_RUN -eq 0 ]]; then
  for s in "${SCRIPTS[@]}"; do
    if [[ -f "$s" ]]; then
      chown root:root "$s"
      chmod 0755 "$s"
    fi
  done
fi

# Build the sudoers content.
alias_lines=""
for s in "${SCRIPTS[@]}"; do
  alias_lines+="    $s, \\
"
done
alias_lines="${alias_lines%, \\
}"   # strip trailing comma+continuation

CONTENT="# Managed by deploy-sudo-grant.sh — non-standing, scoped deploy grant.
# Remove with deploy-sudo-revoke.sh.
Cmnd_Alias VSNP_DEPLOY = \\
${alias_lines}, \\
    /usr/bin/apt-get, \\
    /usr/bin/systemctl reload apache2
${GRANT_USER} ALL=(root) NOPASSWD: VSNP_DEPLOY
"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "# would install $SUDOERS_FILE for user '$GRANT_USER' (deploy dir: $DEPLOY_DIR):"
  echo "$CONTENT"
  exit 0
fi

# Validate before installing — never write an unparsable sudoers file.
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s' "$CONTENT" > "$tmp"
if ! visudo -cf "$tmp" >/dev/null; then
  echo "sudoers validation FAILED — not installing. Content was:" >&2
  cat "$tmp" >&2
  exit 1
fi

install -m 0440 -o root -g root "$tmp" "$SUDOERS_FILE"

echo "Installed $SUDOERS_FILE"
echo "  operator : $GRANT_USER"
echo "  scope    : $DEPLOY_DIR/{bootstrap_ood_core,install_ood,install_kraken,teardown_ood}.sh"
echo "             + apt-get + 'systemctl reload apache2'"
if [[ -n "$(find "$DEPLOY_DIR" -maxdepth 0 -writable -user "$GRANT_USER" 2>/dev/null)" ]]; then
  echo "  NOTE     : $DEPLOY_DIR is writable by $GRANT_USER — they could swap a"
  echo "             script file. Acceptable on a single-admin box; for true"
  echo "             isolation stage the scripts under a root-owned path."
fi
echo "Revoke when done:  sudo $(dirname "$0")/deploy-sudo-revoke.sh"
