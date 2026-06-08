#!/usr/bin/env bash
# teardown_ood.sh — reverse a vSNP-GUI + Kraken OOD install at a site.
#
# Removes everything install_ood.sh / install_kraken.sh / install_bundle.sh
# create, so the box can be reinstalled from a clean state to validate the
# install playbook (the distributable tarball). Parameterized from the same
# site.conf as the installers.
#
# By DEFAULT it tears down the vSNP/Kraken SITE+APP layer (layers 3-4): the OOD
# app cards, cluster + portal config, admin scripts, cron, the ${SITE_ROOT}
# tree (tools/refs/projects/audit/databases incl. the kraken DB), and the lab
# groups. It does NOT touch OOD CORE (apache/ondemand/pam) unless --include-core
# is given — purging core can affect other services on a shared box, and the
# playbook's bootstrap_ood_core.sh is idempotent (a reinstall skips a present
# core), so core teardown is opt-in.
#
#   Usage:
#     sudo ./teardown_ood.sh --yes [options] [phase ...]
#
#   Options:
#     --site-conf PATH  site config to source (default: ./site.conf)
#     --yes             actually perform destructive removal (REQUIRED; without
#                       it the destructive phases only print what they'd remove)
#     --keep-conda      preserve ${CONDA_BASE} (miniforge + pkg cache) so the
#                       reinstall rebuilds the vsnp3/kraken envs fast via
#                       hardlinks. The envs themselves are still removed.
#                       (conda is a documented prerequisite, not installed by
#                       the playbook — keeping it tests what the playbook owns.)
#     --include-core    ALSO purge OOD core (apache vhost, ondemand, pam.d/ood,
#                       ood_portal.yml, the .sif, OSC apt repo). DANGEROUS on a
#                       shared box. Reinstall then starts from bootstrap_ood_core.
#     --user LOGIN      also remove the bundle demo project(s) from
#                       /home/LOGIN/projects (home-dir user data; off by default)
#     --keep-backups    keep /var/backups/ood/* (installer card backups)
#     --dry-run         print everything; remove nothing (implies not --yes)
#     -h | --help       this help
#
#   Phases (default: all, in this order — reverse of install):
#     cron app portal admin demo tree groups [core] verify
#   Re-run any phase independently; every phase is idempotent.
#
# T-50. Companion to install_ood.sh / install_kraken.sh.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DRY_RUN=0
YES=0
KEEP_CONDA=0
INCLUDE_CORE=0
KEEP_BACKUPS=0
DEMO_USER=""
SITE_CONF="${DEPLOY_DIR}/site.conf"
PHASES=()

usage() { sed -n '2,46p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf)    SITE_CONF="$2"; shift 2 ;;
    --yes)          YES=1; shift ;;
    --keep-conda)   KEEP_CONDA=1; shift ;;
    --include-core) INCLUDE_CORE=1; shift ;;
    --keep-backups) KEEP_BACKUPS=1; shift ;;
    --user)         DEMO_USER="$2"; shift 2 ;;
    --dry-run)      DRY_RUN=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    -*)             echo "unknown option: $1" >&2; exit 2 ;;
    *)              PHASES+=("$1"); shift ;;
  esac
done

if [[ ! -f "${SITE_CONF}" ]]; then
  echo "error: site config not found: ${SITE_CONF}" >&2
  exit 1
fi
# shellcheck disable=SC1090
source "${SITE_CONF}"

# ---- Required-var validation + safety rails -------------------------------
for v in SITE_NAME SITE_ROOT CLUSTER_NAME; do
  [[ -z "${!v:-}" ]] && { echo "error: required site.conf var unset: $v" >&2; exit 1; }
done

# Hard guard: SITE_ROOT must look like a real per-site root, never '/', '/srv',
# a bare home, etc. This script runs 'rm -rf' as root against it.
case "${SITE_ROOT}" in
  /|/srv|/srv/|/home|/usr|/var|/etc|/opt|"")
    echo "error: refusing to operate on SITE_ROOT='${SITE_ROOT}' (too broad)" >&2; exit 1 ;;
esac
if [[ ! "${SITE_ROOT}" =~ ^/[A-Za-z0-9._/-]+$ || "${SITE_ROOT}" == *".."* ]]; then
  echo "error: SITE_ROOT='${SITE_ROOT}' is not a safe absolute path" >&2; exit 1
fi

# ---- Self-relocate so we don't delete the running script ------------------
# This script installs at ${SITE_ROOT}/tools/vsnp_gui/deploy/teardown_ood.sh —
# inside the tree the 'tree' phase wipes. If we're running from under
# SITE_ROOT, copy ourselves out and re-exec (still root) so the rm can't pull
# the file out from under bash mid-read.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
if [[ "${SELF}" == "${SITE_ROOT}/"* && -z "${_TEARDOWN_RELOCATED:-}" ]]; then
  _relo="$(mktemp /tmp/teardown_ood.XXXXXX.sh)"
  cp "${SELF}" "${_relo}"
  chmod 0700 "${_relo}"
  export _TEARDOWN_RELOCATED=1
  exec bash "${_relo}" --site-conf "${SITE_CONF}" \
       $([[ ${DRY_RUN} -eq 1 ]] && echo --dry-run) \
       $([[ ${YES} -eq 1 ]] && echo --yes) \
       $([[ ${KEEP_CONDA} -eq 1 ]] && echo --keep-conda) \
       $([[ ${INCLUDE_CORE} -eq 1 ]] && echo --include-core) \
       $([[ ${KEEP_BACKUPS} -eq 1 ]] && echo --keep-backups) \
       $([[ -n "${DEMO_USER}" ]] && echo --user "${DEMO_USER}") \
       "${PHASES[@]}"
fi

[[ ${DRY_RUN} -eq 1 ]] && YES=0   # dry-run never destroys

if [[ ${#PHASES[@]} -eq 0 ]]; then
  PHASES=(cron app portal admin demo tree groups verify)
  [[ ${INCLUDE_CORE} -eq 1 ]] && PHASES=(cron app portal admin demo tree groups core verify)
fi

# ---- Derived paths (mirror install_ood.sh / install_kraken.sh) ------------
CONDA_BASE="${CONDA_BASE:-${SITE_ROOT}/tools/miniforge3}"
MEMBERS_GROUP="${MEMBERS_GROUP:-${SITE_NAME}-members}"
ADMINS_GROUP="${ADMINS_GROUP:-${SITE_NAME}-admins}"
OOD_CFG_DIR="/etc/ood/config"
SBIN_DIR="/usr/local/sbin"

c_bold=$'\e[1m'; c_dim=$'\e[2m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_red=$'\e[31m'; c_rst=$'\e[0m'
log()  { echo "${c_bold}==>${c_rst} $*"; }
info() { echo "    $*"; }
ok()   { echo "    ${c_grn}ok${c_rst}   $*"; }
warn() { echo "    ${c_ylw}warn${c_rst} $*" >&2; }
die()  { echo "${c_red}error:${c_rst} $*" >&2; exit 1; }

need_root() { [[ ${DRY_RUN} -eq 1 || "$(id -u)" -eq 0 ]] || die "phase '$1' mutates the system; run under sudo"; }

# rm PATH...  — remove, or print under dry-run / non-confirmed run.
rm_path() {
  local destructive_ok=1
  # destructive phases require --yes; non-destructive cleanups always run
  if [[ ${DRY_RUN} -eq 1 || ${YES} -eq 0 ]]; then destructive_ok=0; fi
  for p in "$@"; do
    [[ -z "${p}" || ! -e "${p}" && ! -L "${p}" ]] && continue
    if [[ ${destructive_ok} -eq 1 ]]; then
      rm -rf -- "${p}"; ok "removed ${p}"
    else
      echo "    ${c_dim}[would remove] ${p}${c_rst}"
    fi
  done
}

# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

phase_cron() {
  need_root cron
  log "cron — provenance janitor"
  rm_path /etc/cron.d/vsnp_gui-provenance
}

phase_app() {
  need_root app
  log "app — OOD app cards + cluster config"
  rm_path /var/www/ood/apps/sys/vsnp_gui
  rm_path /var/www/ood/apps/sys/kraken_id_parse_gui
  rm_path "${OOD_CFG_DIR}/clusters.d/${CLUSTER_NAME}.yml"
  if [[ ${KEEP_BACKUPS} -eq 0 ]]; then
    rm_path /var/backups/ood/vsnp_gui /var/backups/ood/kraken_id_parse_gui
  else
    info "keeping /var/backups/ood/* (--keep-backups)"
  fi
}

phase_portal() {
  need_root portal
  log "portal — dashboard branding overrides (reverts to stock OOD)"
  rm_path "${OOD_CFG_DIR}/ondemand.d/dashboard.yml"
  rm_path "${OOD_CFG_DIR}/wgs_pipelines.yml"
  rm_path "${OOD_CFG_DIR}/apps/dashboard/views/dashboard/index.html.erb"
}

phase_admin() {
  need_root admin
  log "admin — ${SITE_NAME}-* scripts in ${SBIN_DIR}"
  rm_path "${SBIN_DIR}/${SITE_NAME}-add-user.sh" \
          "${SBIN_DIR}/${SITE_NAME}-setup-project.sh" \
          "${SBIN_DIR}/${SITE_NAME}-rename-project.sh"
}

phase_demo() {
  need_root demo
  log "demo — bundle demo project(s) in a user's home"
  if [[ -z "${DEMO_USER}" ]]; then
    info "no --user given; skipping home-dir demo projects (site-tree projects handled by 'tree')"
    return 0
  fi
  local pdir="/home/${DEMO_USER}/projects"
  if [[ -d "${pdir}" ]]; then
    # Only the known demo project name — never the whole projects dir.
    rm_path "${pdir}/demo_sars_cov_2"
  else
    info "${pdir} not present"
  fi
}

phase_tree() {
  need_root tree
  log "tree — ${SITE_ROOT} shared subtree"
  if [[ ! -d "${SITE_ROOT}" ]]; then info "${SITE_ROOT} not present"; return 0; fi

  # Remove every child of SITE_ROOT (handles SITE_ROOT being a mountpoint: the
  # mount dir itself stays, its contents go). Optionally preserve CONDA_BASE.
  shopt -s dotglob nullglob
  local child base_conda
  base_conda="$(basename "${CONDA_BASE}")"
  for child in "${SITE_ROOT}"/*; do
    if [[ ${KEEP_CONDA} -eq 1 && "${child}" == "${CONDA_BASE}" ]]; then
      info "keeping ${child} (--keep-conda; pkg cache speeds env rebuild)"
      continue
    fi
    rm_path "${child}"
  done
  shopt -u dotglob nullglob

  if [[ ${KEEP_CONDA} -eq 1 ]]; then
    # The vsnp3 + kraken envs live under tools/ as siblings of miniforge3 and
    # were just removed above; nothing else to do. Note any stale conda env
    # registry entries are harmless (conda just lists missing paths).
    info "conda base preserved at ${CONDA_BASE}; vsnp3 + kraken envs removed and will rebuild from spec"
  fi
}

phase_groups() {
  need_root groups
  log "groups — lab Unix groups"
  if [[ ${DRY_RUN} -eq 1 || ${YES} -eq 0 ]]; then
    for g in "${MEMBERS_GROUP}" "${ADMINS_GROUP}"; do
      getent group "${g}" >/dev/null 2>&1 && echo "    ${c_dim}[would delete group] ${g}${c_rst}"
    done
    return 0
  fi
  for g in "${MEMBERS_GROUP}" "${ADMINS_GROUP}"; do
    if getent group "${g}" >/dev/null 2>&1; then
      groupdel "${g}" 2>/dev/null && ok "deleted group ${g}" \
        || warn "could not delete ${g} (still a primary group for some user?)"
    fi
  done
}

phase_core() {
  need_root core
  log "core — OOD core (apache/ondemand/pam) ${c_red}[--include-core]${c_rst}"
  warn "This purges OOD core. On a shared box this can affect other web services."
  if [[ ${DRY_RUN} -eq 1 || ${YES} -eq 0 ]]; then
    info "[would] a2dissite ood-portal; rm pam.d/ood, ood_portal.yml, ${OOD_CFG_DIR}/ondemand.d/*"
    info "[would] apt-get purge -y ondemand; rm /opt/ood, the .sif, OSC apt repo"
    info "[would] remove www-data from the shadow group"
    return 0
  fi
  a2dissite ood-portal 2>/dev/null || true
  systemctl reload apache2 2>/dev/null || true
  rm_path /etc/pam.d/ood "${OOD_CFG_DIR}/ood_portal.yml"
  apt-get purge -y ondemand 2>/dev/null || warn "apt purge ondemand failed (already gone?)"
  rm_path /opt/ood /etc/apt/sources.list.d/ondemand-web.list
  gpasswd -d www-data shadow 2>/dev/null || true
  info "apache2 + apptainer packages left installed (shared base packages); remove by hand if truly bare-metal."
}

phase_verify() {
  log "verify — what remains"
  local left=0
  for p in /var/www/ood/apps/sys/vsnp_gui /var/www/ood/apps/sys/kraken_id_parse_gui \
           "${OOD_CFG_DIR}/clusters.d/${CLUSTER_NAME}.yml" \
           "${OOD_CFG_DIR}/wgs_pipelines.yml" /etc/cron.d/vsnp_gui-provenance; do
    [[ -e "${p}" ]] && { warn "still present: ${p}"; left=1; }
  done
  if [[ -d "${SITE_ROOT}" ]]; then
    local n; n="$(find "${SITE_ROOT}" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' ')"
    if [[ ${KEEP_CONDA} -eq 1 ]]; then
      info "${SITE_ROOT} has ${n} top-level entr$([[ $n == 1 ]] && echo y || echo ies) (expect just miniforge3)"
    else
      [[ "${n}" -gt 0 ]] && { warn "${SITE_ROOT} still has ${n} entries"; left=1; } || ok "${SITE_ROOT} emptied"
    fi
  fi
  for g in "${MEMBERS_GROUP}" "${ADMINS_GROUP}"; do
    getent group "${g}" >/dev/null 2>&1 && { warn "group still present: ${g}"; left=1; }
  done
  [[ ${left} -eq 0 ]] && ok "teardown clean (for the selected phases)" || warn "some artifacts remain (see above)"
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

echo "${c_bold}vSNP GUI OOD teardown${c_rst}  (site: ${SITE_NAME} / ${SITE_ROOT})"
if [[ ${DRY_RUN} -eq 1 ]]; then
  echo "${c_ylw}DRY RUN — nothing will be modified${c_rst}"
elif [[ ${YES} -eq 0 ]]; then
  echo "${c_ylw}PREVIEW — destructive phases need --yes; printing what would be removed${c_rst}"
fi
echo "phases: ${PHASES[*]}   $([[ ${KEEP_CONDA} -eq 1 ]] && echo '(--keep-conda)')$([[ ${INCLUDE_CORE} -eq 1 ]] && echo ' (--include-core)')"
echo

for ph in "${PHASES[@]}"; do
  case "${ph}" in
    cron)   phase_cron ;;
    app)    phase_app ;;
    portal) phase_portal ;;
    admin)  phase_admin ;;
    demo)   phase_demo ;;
    tree)   phase_tree ;;
    groups) phase_groups ;;
    core)   phase_core ;;
    verify) phase_verify ;;
    *)      die "unknown phase: ${ph} (see --help)" ;;
  esac
  echo
done

log "teardown done."
if [[ ${YES} -eq 0 && ${DRY_RUN} -eq 0 ]]; then
  echo "Re-run with --yes to actually remove."
fi
exit 0
