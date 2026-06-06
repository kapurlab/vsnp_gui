#!/usr/bin/env bash
# bootstrap_ood_core.sh — install Open OnDemand core on a bare Ubuntu box.
#
# This is LAYER 1-2 of the install (see docs/deploy/WGS3_AUDIT.md §0): it
# reproduces how wgs3's OOD core was built so install_ood.sh has a platform
# to deploy the vSNP layer onto. Run this FIRST on a fresh box, then run
# install_ood.sh.
#
# It does NOT provision storage mounts (the SITE_ROOT XFS+prjquota disk is a
# one-time hardware step — see runbooks/T-19-storage-layout.md).
#
#   Usage:
#     sudo ./bootstrap_ood_core.sh [--site-conf PATH] [--dry-run] [step ...]
#
#   Steps (default: all, in order):
#     base       apt prerequisites (apache, tmux, node/npm, pam module)
#     apptainer  install Apptainer/Singularity
#     ondemand   add OSC apt repo + install the `ondemand` package
#     image      place the ood_default.sif session image
#     portal     write ood_portal.yml + pam.d/ood, enable apache, apply
#     verify     curl the portal, expect a 401 (auth challenge = working)
#
# Reference: OSC docs https://osc.github.io/ood-documentation/latest/
#
# T-50. STATUS: DRAFT — reconstructed from wgs3 (OnDemand 3.1.16, Ubuntu
# noble). First run on a new box MUST be --dry-run with review.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
SITE_CONF="${DEPLOY_DIR}/site.conf"
STEPS=()

usage() { sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf) SITE_CONF="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    -*)          echo "unknown option: $1" >&2; exit 2 ;;
    *)           STEPS+=("$1"); shift ;;
  esac
done
[[ ${#STEPS[@]} -eq 0 ]] && STEPS=(base apptainer ondemand image portal verify)

[[ -f "${SITE_CONF}" ]] || { echo "error: ${SITE_CONF} not found (cp site.conf.example site.conf)" >&2; exit 1; }
# shellcheck disable=SC1090
source "${SITE_CONF}"

: "${SERVERNAME:?set SERVERNAME in site.conf}"
: "${SITE_DISPLAY:?set SITE_DISPLAY in site.conf}"
OOD_VERSION="${OOD_VERSION:-3.1}"
AUTH_REALM="${AUTH_REALM:-${SITE_DISPLAY} — Open OnDemand}"
APPTAINER_VERSION="${APPTAINER_VERSION:-1.5.0}"
OOD_SIF_SOURCE="${OOD_SIF_SOURCE:-}"
SIF_DEST="/opt/ood/ondemand/ood_default.sif"

c_bold=$'\e[1m'; c_dim=$'\e[2m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_red=$'\e[31m'; c_rst=$'\e[0m'
log()  { echo "${c_bold}==>${c_rst} $*"; }
info() { echo "    $*"; }
ok()   { echo "    ${c_grn}ok${c_rst}   $*"; }
warn() { echo "    ${c_ylw}warn${c_rst} $*" >&2; }
die()  { echo "${c_red}error:${c_rst} $*" >&2; exit 1; }
run()  { if [[ ${DRY_RUN} -eq 1 ]]; then echo "    ${c_dim}[dry-run] $*${c_rst}"; else "$@"; fi; }
need_root() { [[ ${DRY_RUN} -eq 1 || "$(id -u)" -eq 0 ]] || die "must run as root (sudo)"; }

# Resolve OS codename (the OSC apt repo is codename-keyed: noble, jammy, ...).
if [[ -r /etc/os-release ]]; then . /etc/os-release; fi
CODENAME="${VERSION_CODENAME:-unknown}"
ARCH="$(dpkg --print-architecture 2>/dev/null || echo amd64)"

step_base() {
  need_root
  log "base — apt prerequisites"
  [[ "${CODENAME}" == "noble" ]] || warn "wgs3 reference is noble (24.04); this box is '${CODENAME}'. OOD apt repo must match the codename."
  run apt-get update
  # apache + PAM auth module + tmux (OOD session multiplexer) + node/npm
  # (frontend build) + fetch/build helpers.
  run apt-get install -y \
    apache2 tmux \
    libapache2-mod-authnz-pam \
    nodejs npm \
    curl wget ca-certificates gnupg lsb-release git xfsprogs
  ok "base packages installed"
}

step_apptainer() {
  need_root
  log "apptainer — container runtime"
  if command -v apptainer >/dev/null 2>&1 || command -v singularity >/dev/null 2>&1; then
    ok "apptainer/singularity already present ($(command -v apptainer singularity 2>/dev/null | head -1))"
    return 0
  fi
  if [[ -n "${APPTAINER_VERSION}" ]]; then
    # Official GitHub release .deb (how wgs3 got 1.5.0 — a local .deb, not a repo).
    local deb="apptainer_${APPTAINER_VERSION}_${ARCH}.deb"
    local url="https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/${deb}"
    info "fetching ${url}"
    run bash -c "cd /tmp && curl -fsSLO '${url}' && apt-get install -y './${deb}' && rm -f './${deb}'"
  else
    info "APPTAINER_VERSION empty — installing from the OS repo (older, but works)"
    run apt-get install -y apptainer
  fi
  command -v singularity >/dev/null 2>&1 || warn "no 'singularity' symlink — apptainer provides it; cluster config uses singularity_bin: /usr/bin/singularity"
  ok "apptainer installed"
}

step_ondemand() {
  need_root
  log "ondemand — OSC apt repo + ondemand package"
  if [[ -d /opt/ood ]]; then
    ok "OOD already installed ($(cat /opt/ood/VERSION 2>/dev/null || echo present))"
    return 0
  fi
  # OSC ships an `ondemand-release-web` .deb that drops the apt repo + gpg key.
  local rel="ondemand-release-web_${OOD_VERSION}.2-${CODENAME}_all.deb"
  local url="https://apt.osc.edu/ondemand/${OOD_VERSION}/${rel}"
  info "fetching release package: ${url}"
  info "(if the exact filename 404s, browse https://apt.osc.edu/ondemand/${OOD_VERSION}/ for the current ${CODENAME} build)"
  run bash -c "cd /tmp && curl -fsSLO '${url}' && apt-get install -y './${rel}' && rm -f './${rel}'"
  run apt-get update
  run apt-get install -y ondemand
  ok "ondemand installed"
}

step_image() {
  need_root
  log "image — OOD session container ${SIF_DEST}"
  run mkdir -p "$(dirname "${SIF_DEST}")"
  if [[ -f "${SIF_DEST}" ]]; then
    ok "session image already present"
    return 0
  fi
  if [[ -z "${OOD_SIF_SOURCE}" ]]; then
    warn "OOD_SIF_SOURCE unset. The 30 MB ood_default.sif is NOT shipped by the"
    warn "ondemand package — copy the proven one from wgs3, e.g.:"
    warn "  scp vxk1@kapurlab-wgs3...:/opt/ood/ondemand/ood_default.sif ${SIF_DEST}"
    return 0
  fi
  if [[ "${OOD_SIF_SOURCE}" == *:* ]]; then
    run scp "${OOD_SIF_SOURCE}" "${SIF_DEST}"          # remote (user@host:/path)
  else
    run install -m 0755 "${OOD_SIF_SOURCE}" "${SIF_DEST}"  # local path
  fi
  run chmod 0755 "${SIF_DEST}"
  ok "session image in place"
}

step_portal() {
  need_root
  log "portal — ood_portal.yml + PAM service + apache"

  # 1. PAM service backing basic-auth (local Unix users — pam_unix).
  if [[ ${DRY_RUN} -eq 0 ]]; then
    install -D -o root -g root -m 0644 /dev/stdin /etc/pam.d/ood <<'PAM'
auth    required pam_unix.so
account required pam_unix.so
PAM
    ok "wrote /etc/pam.d/ood"
  else
    echo "    ${c_dim}[dry-run] write /etc/pam.d/ood${c_rst}"
  fi

  # 1b. Apache (www-data) must be able to read /etc/shadow to verify passwords
  # via pam_unix — otherwise EVERY basic-auth login fails (pam_unix can't check
  # another user's password for a caller without shadow access). Add www-data
  # to the shadow group. This is the single most common "OOD installed but
  # nobody can log in" cause on a fresh Debian/Ubuntu box.
  if id -nG www-data 2>/dev/null | tr ' ' '\n' | grep -qx shadow; then
    ok "www-data already in the shadow group"
  else
    run usermod -aG shadow www-data
    ok "added www-data to the shadow group (required for PAM basic-auth)"
  fi

  # 2. ood_portal.yml — servername + basic-auth-over-PAM. Mirrors wgs3.
  if [[ ${DRY_RUN} -eq 0 ]]; then
    install -d -m 0755 /etc/ood/config
    cat > /etc/ood/config/ood_portal.yml <<YAML
---
servername: ${SERVERNAME}
port: 80

auth:
  - AuthType Basic
  - AuthName "${AUTH_REALM}"
  - AuthBasicProvider PAM
  - AuthPAMService ood
  - Require valid-user

logout_redirect: "/pun/sys/dashboard/logout"

node_uri: /node
rnode_uri: /rnode
YAML
    ok "wrote /etc/ood/config/ood_portal.yml (servername=${SERVERNAME})"
    warn "port 80 = plain HTTP. For an untrusted network add a TLS (ssl:) block — see audit §7."
  else
    echo "    ${c_dim}[dry-run] write /etc/ood/config/ood_portal.yml${c_rst}"
  fi

  # 3. apache mods OOD's reverse proxy + PAM auth need.
  run a2enmod proxy proxy_http proxy_wstunnel rewrite headers lua authnz_pam

  # 4. regenerate the vhost from ood_portal.yml, enable it, restart.
  if [[ -x /opt/ood/ood-portal-generator/sbin/update_ood_portal ]]; then
    run /opt/ood/ood-portal-generator/sbin/update_ood_portal
    run a2ensite ood-portal
    run a2dissite 000-default || true
    run systemctl restart apache2
    ok "apache configured + restarted"
  else
    warn "update_ood_portal not found — is the ondemand package installed? (run the 'ondemand' step)"
  fi
}

step_verify() {
  log "verify — portal answers with an auth challenge"
  if [[ ${DRY_RUN} -eq 1 ]]; then info "[dry-run] curl -sIL http://localhost/"; return 0; fi
  # OOD redirects / -> (canonical host) -> /pun/sys/dashboard, which then issues
  # the basic-auth challenge. Follow the chain and look for the 401.
  local headers
  headers="$(curl -sIL "http://localhost/" 2>/dev/null || true)"
  if echo "${headers}" | grep -qiE '^HTTP/[0-9.]+ 401'; then
    ok "portal returns a 401 basic-auth challenge — OOD core is up"
  elif echo "${headers}" | grep -qiE '^HTTP/[0-9.]+ (200|301|302)'; then
    warn "portal responds with redirects but no 401 was seen — check the auth block in ood_portal.yml"
  else
    warn "no response on http://localhost/ — check 'systemctl status apache2'"
  fi
  info "Next: create the first admin user, then run install_ood.sh."
  info "  sudo useradd -m -s /bin/bash <admin>; sudo passwd <admin>"
  info "  (after install_ood.sh's 'admin' phase: ${SITE_NAME:-<site>}-add-user <admin> --admin)"
}

echo "${c_bold}OOD core bootstrap${c_rst}  (${SITE_DISPLAY}, OnDemand ${OOD_VERSION}, ${CODENAME}/${ARCH})"
[[ ${DRY_RUN} -eq 1 ]] && echo "${c_ylw}DRY RUN — nothing will be modified${c_rst}"
echo "steps: ${STEPS[*]}"; echo

for s in "${STEPS[@]}"; do
  case "${s}" in
    base)      step_base ;;
    apptainer) step_apptainer ;;
    ondemand)  step_ondemand ;;
    image)     step_image ;;
    portal)    step_portal ;;
    verify)    step_verify ;;
    *)         die "unknown step: ${s} (see --help)" ;;
  esac
  echo
done

log "OOD core bootstrap done."
[[ ${DRY_RUN} -eq 1 ]] && echo "Re-run without --dry-run as root to apply, then run install_ood.sh."
