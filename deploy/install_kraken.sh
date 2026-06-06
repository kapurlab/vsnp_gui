#!/usr/bin/env bash
# install_kraken.sh — deploy the Kraken ID Parse app alongside vSNP GUI.
#
# A second OOD app (taxonomic classification / contamination screening). Shares
# the same site.conf, OOD core, groups, and project tree as vSNP GUI. Run AFTER
# bootstrap_ood_core.sh + install_ood.sh.
#
#   Usage:
#     sudo ./install_kraken.sh [--site-conf PATH] [--dry-run] [phase ...]
#
#   Phases (default: all, in order):
#     preflight  OOD core + conda + node present
#     app        place the kraken repo, build its conda env, build the frontend
#     card       install the Kraken OOD app card (substituted)
#     db         provision the kraken2 DB (download a pinned index, or mirror)
#     verify     smoke-check
#
# T-30. Idempotent; re-run any phase. First run on a new box: --dry-run first.

set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN=0
SITE_CONF="${DEPLOY_DIR}/site.conf"
PHASES=()

usage() { sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf) SITE_CONF="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    -*)          echo "unknown option: $1" >&2; exit 2 ;;
    *)           PHASES+=("$1"); shift ;;
  esac
done
[[ ${#PHASES[@]} -eq 0 ]] && PHASES=(preflight app card db verify)

[[ -f "${SITE_CONF}" ]] || { echo "error: ${SITE_CONF} not found (cp site.conf.example site.conf)" >&2; exit 1; }
# shellcheck disable=SC1090
source "${SITE_CONF}"
: "${SITE_NAME:?}"; : "${SITE_DISPLAY:?}"; : "${SITE_ROOT:?}"; : "${ADMINS_GROUP:?}"
: "${CLUSTER_NAME:?}"; : "${SERVERNAME:?}"
CONDA_BASE="${CONDA_BASE:-${SITE_ROOT}/tools/miniforge3}"
NPM_BIN="${NPM_BIN:-/usr/bin/npm}"
KRAKEN_DB_NAME="${KRAKEN_DB_NAME:-k2_standard_08gb}"

KRAKEN_DIR="${SITE_ROOT}/tools/kraken_id_parse_gui"
KRAKEN_ENV="${KRAKEN_DIR}/env"
KRAKEN_DB_DIR="${SITE_ROOT}/databases/kraken2/${KRAKEN_DB_NAME}"
OOD_APP_DIR="/var/www/ood/apps/sys/kraken_id_parse_gui"
OOD_CFG_DIR="/etc/ood/config"

c_bold=$'\e[1m'; c_dim=$'\e[2m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_red=$'\e[31m'; c_rst=$'\e[0m'
log(){ echo "${c_bold}==>${c_rst} $*"; }; info(){ echo "    $*"; }
ok(){ echo "    ${c_grn}ok${c_rst}   $*"; }; warn(){ echo "    ${c_ylw}warn${c_rst} $*" >&2; }
die(){ echo "${c_red}error:${c_rst} $*" >&2; exit 1; }
run(){ if [[ ${DRY_RUN} -eq 1 ]]; then echo "    ${c_dim}[dry-run] $*${c_rst}"; else "$@"; fi; }
need_root(){ [[ ${DRY_RUN} -eq 1 || "$(id -u)" -eq 0 ]] || die "phase mutates the system; run under sudo"; }

# Same site-token substitution as install_ood.sh (FQDN-last + IP handled by the
# OOD card's own logic; the kraken card only needs the path/cluster/site tokens).
subst() {
  sed -e "s|/srv/kapurlab|${SITE_ROOT}|g" \
      -e "s|kapurlab-admins|${ADMINS_GROUP}|g" \
      -e "s|kapurlab-members|${MEMBERS_GROUP:-${ADMINS_GROUP}}|g" \
      -e "s|WGS3|${SITE_DISPLAY}|g" \
      -e "s|wgs3|${CLUSTER_NAME}|g" \
      -e "s|kapurlab|${SITE_NAME}|g" \
      -e "s|100\.68\.171\.59|${SERVERNAME}|g" \
      "$1"
}
install_subst() {
  local src="$1" dest="$2" mode="$3" tmp
  if [[ ${DRY_RUN} -eq 1 ]]; then echo "    ${c_dim}[dry-run] subst ${src} -> ${dest}${c_rst}"; return 0; fi
  tmp="$(mktemp)"; subst "${src}" > "${tmp}"
  install -D -o root -g root -m "${mode}" "${tmp}" "${dest}"; rm -f "${tmp}"; ok "installed ${dest}"
}

phase_preflight() {
  log "preflight"
  [[ -d /etc/ood/config ]] && ok "OOD core present" || { warn "OOD core missing — run bootstrap_ood_core.sh"; }
  ([[ -x "${CONDA_BASE}/bin/conda" ]] || command -v conda >/dev/null 2>&1) && ok "conda available" || warn "no conda for the kraken env"
  [[ -x "${NPM_BIN}" ]] && ok "npm present" || warn "npm missing (frontend build)"
}

phase_app() {
  need_root
  log "app — place repo, build conda env + frontend at ${KRAKEN_DIR}"

  # 1. place the repo
  if [[ -d "${KRAKEN_DIR}/backend" ]]; then
    ok "kraken repo present at ${KRAKEN_DIR}"
  elif [[ -n "${KRAKEN_RSYNC_SOURCE:-}" ]]; then
    run mkdir -p "${KRAKEN_DIR}"
    run rsync -a --exclude env/ --exclude .git/ --exclude 'frontend/node_modules/' \
        "${KRAKEN_RSYNC_SOURCE%/}/" "${KRAKEN_DIR}/"
    ok "mirrored kraken repo from ${KRAKEN_RSYNC_SOURCE}"
  elif [[ -n "${KRAKEN_REPO_URL:-}" ]]; then
    run git clone "${KRAKEN_REPO_URL}" "${KRAKEN_DIR}"
    ok "cloned ${KRAKEN_REPO_URL}"
  else
    die "kraken repo absent and neither KRAKEN_RSYNC_SOURCE nor KRAKEN_REPO_URL set"
  fi
  run chgrp -R "${ADMINS_GROUP}" "${KRAKEN_DIR}" 2>/dev/null || true

  # 2. conda env from the repo's spec (kraken2/krona/tectonic/... ~5 GB)
  local conda="${CONDA_BASE}/bin/conda"; [[ -x "${conda}" ]] || conda="$(command -v conda 2>/dev/null || true)"
  if [[ -x "${KRAKEN_ENV}/bin/python" ]]; then
    ok "kraken env exists at ${KRAKEN_ENV}"
  elif [[ -z "${conda}" ]]; then
    warn "no conda; skipping env build"
  elif [[ -f "${KRAKEN_DIR}/conda_setup/environment.yml" ]]; then
    run "${conda}" env create -p "${KRAKEN_ENV}" -f "${KRAKEN_DIR}/conda_setup/environment.yml"
    ok "built kraken conda env (kraken2, krona, …)"
  else
    warn "no conda_setup/environment.yml in the repo"
  fi

  # The repo's environment.yml under-specifies the runtime env — the working
  # reference env (wgs3) has ~15 more packages added after create. Install the
  # backend requirements + the known runtime extras so the pipeline actually
  # runs (else it dies on `ModuleNotFoundError: humanize`, then cairosvg, …).
  # TODO(kraken repo): fold these into conda_setup/environment.yml upstream.
  if [[ -x "${KRAKEN_ENV}/bin/pip" ]]; then
    [[ -f "${KRAKEN_DIR}/backend/requirements.txt" ]] && \
      run "${KRAKEN_ENV}/bin/pip" install -q -r "${KRAKEN_DIR}/backend/requirements.txt"
    run "${KRAKEN_ENV}/bin/pip" install -q \
      humanize cairosvg cairocffi cssselect2 defusedxml svgwrite tinycss2 \
      webencodings pysam PySocks
    ok "installed backend reqs + runtime extras (env-spec gap)"
  fi

  # 3. frontend
  if [[ -f "${KRAKEN_DIR}/frontend/package.json" && -x "${NPM_BIN}" ]]; then
    run bash -c "cd '${KRAKEN_DIR}/frontend' && '${NPM_BIN}' ci && '${NPM_BIN}' run build"
    ok "frontend built -> ${KRAKEN_DIR}/frontend/dist"
  else
    warn "frontend build skipped (need ${KRAKEN_DIR}/frontend + npm)"
  fi
}

phase_card() {
  need_root
  log "card — Kraken OOD app at ${OOD_APP_DIR}"
  local src="${KRAKEN_DIR}/ood/apps/kraken_id_parse_gui"
  [[ -d "${src}" ]] || die "OOD card templates not found at ${src} (run the 'app' phase first)"
  if [[ -d "${OOD_APP_DIR}" && ${DRY_RUN} -eq 0 ]]; then
    local bak="/var/backups/ood/kraken_id_parse_gui/$(date +%Y%m%d_%H%M%S)"
    run mkdir -p "${bak}"; run cp -a "${OOD_APP_DIR}/." "${bak}/"
  fi
  install_subst "${src}/manifest.yml"            "${OOD_APP_DIR}/manifest.yml"            0644
  install_subst "${src}/form.yml"                "${OOD_APP_DIR}/form.yml"                0644
  install_subst "${src}/submit.yml.erb"          "${OOD_APP_DIR}/submit.yml.erb"          0644
  install_subst "${src}/view.html.erb"           "${OOD_APP_DIR}/view.html.erb"           0644
  install_subst "${src}/template/before.sh"      "${OOD_APP_DIR}/template/before.sh"      0755
  install_subst "${src}/template/script.sh.erb"  "${OOD_APP_DIR}/template/script.sh.erb"  0644
  ok "Kraken card installed (cluster=${CLUSTER_NAME})"
}

phase_db() {
  need_root
  log "db — kraken2 database at ${KRAKEN_DB_DIR}"
  run mkdir -p "${KRAKEN_DB_DIR}"
  if [[ -f "${KRAKEN_DB_DIR}/hash.k2d" ]]; then
    ok "kraken2 DB already present ($(du -sh "${KRAKEN_DB_DIR}" 2>/dev/null | cut -f1))"
  elif [[ -n "${KRAKEN_DB_RSYNC_SOURCE:-}" ]]; then
    run rsync -a "${KRAKEN_DB_RSYNC_SOURCE%/}/" "${KRAKEN_DB_DIR}/"
    ok "mirrored DB from ${KRAKEN_DB_RSYNC_SOURCE}"
  elif [[ -n "${KRAKEN_DB_URL:-}" ]]; then
    info "downloading ${KRAKEN_DB_URL} (~8 GB)"
    run bash -c "cd '${KRAKEN_DB_DIR}' && curl -fSL '${KRAKEN_DB_URL}' -o db.tar.gz && tar -xzf db.tar.gz && rm -f db.tar.gz"
    ok "downloaded + extracted kraken2 DB"
  else
    warn "no DB. Set KRAKEN_DB_URL (download) or KRAKEN_DB_RSYNC_SOURCE (mirror)."
  fi
  run chgrp -R "${MEMBERS_GROUP:-${ADMINS_GROUP}}" "${SITE_ROOT}/databases" 2>/dev/null || true
}

phase_verify() {
  log "verify"
  [[ -x "${KRAKEN_ENV}/bin/python" ]] && ok "kraken env python present" || warn "kraken env missing"
  "${KRAKEN_ENV}/bin/kraken2" --version >/dev/null 2>&1 && ok "kraken2 in env: $("${KRAKEN_ENV}/bin/kraken2" --version 2>/dev/null | head -1)" || warn "kraken2 not in env"
  [[ -f "${KRAKEN_DIR}/frontend/dist/index.html" ]] && ok "frontend built" || warn "frontend dist missing"
  [[ -f "${OOD_APP_DIR}/manifest.yml" ]] && ok "OOD card installed" || warn "OOD card missing"
  [[ -f "${KRAKEN_DB_DIR}/hash.k2d" ]] && ok "kraken2 DB present" || warn "kraken2 DB missing"
  info "Launch: OOD dashboard → Bioinformatics → Kraken ID Parse"
}

echo "${c_bold}Kraken ID Parse installer${c_rst}  (${SITE_DISPLAY}, ${KRAKEN_DIR})"
[[ ${DRY_RUN} -eq 1 ]] && echo "${c_ylw}DRY RUN${c_rst}"
echo "phases: ${PHASES[*]}"; echo
for ph in "${PHASES[@]}"; do
  case "${ph}" in
    preflight) phase_preflight ;;
    app) phase_app ;;
    card) phase_card ;;
    db) phase_db ;;
    verify) phase_verify ;;
    *) die "unknown phase: ${ph}" ;;
  esac
  echo
done
log "Kraken install done."
