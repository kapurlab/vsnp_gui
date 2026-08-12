#!/usr/bin/env bash
# install_ood.sh — deploy the vSNP GUI OOD layer at a new site.
#
# Automates layer 4 (the vSNP OOD card, cluster cfg, portal, admin scripts,
# cron) and the scriptable parts of layer 3 (conda vsnp3 env, vsnp3 patches,
# frontend build, reference registration). It assumes OOD CORE is already
# installed — run deploy/bootstrap_ood_core.sh first on a bare box. It does
# NOT provision storage mounts or ship reference data (prerequisites).
# See docs/deploy/INSTALL_OOD.md.
#
#   Usage:
#     sudo ./install_ood.sh [options] [phase ...]
#
#   Options:
#     --site-conf PATH   site config to source (default: ./site.conf)
#     --dry-run          print what would change; touch nothing
#     -h | --help        this help
#
#   Phases (default: all, in this order):
#     preflight  verify OS / OOD core / singularity / xfs / node / conda
#     groups     create members + admins Unix groups
#     storage    create ${SITE_ROOT} subtree (setgid, right groups)
#     toolchain  conda vsnp3 env + backend deps + patches + frontend build
#     refs       print the reference-data rsync command (no data pulled)
#     app        install the OOD app card + render the cluster config
#     portal     install dashboard / pipelines / home-page override
#     admin      install ${SITE_NAME}-* admin scripts to /usr/local/sbin
#     cron       install the provenance cron
#     verify     smoke-check the result
#
#   Re-run any phase independently; every phase is idempotent.
#
# T-50. STATUS: DRAFT — authored against wgs3 + the repo. First run on a new
# box MUST be `--dry-run` with review.

set -euo pipefail

# ---------------------------------------------------------------------------
# Bootstrap: locate the repo, parse args, source site.conf
# ---------------------------------------------------------------------------

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${DEPLOY_DIR}/.." && pwd)"

DRY_RUN=0
SITE_CONF="${DEPLOY_DIR}/site.conf"
PHASES=()

usage() { sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf) SITE_CONF="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    -*)          echo "unknown option: $1" >&2; exit 2 ;;
    *)           PHASES+=("$1"); shift ;;
  esac
done

if [[ ${#PHASES[@]} -eq 0 ]]; then
  PHASES=(preflight groups storage toolchain refs app portal admin cron verify)
fi

if [[ ! -f "${SITE_CONF}" ]]; then
  echo "error: site config not found: ${SITE_CONF}" >&2
  echo "       cp ${DEPLOY_DIR}/site.conf.example ${DEPLOY_DIR}/site.conf and edit it." >&2
  exit 1
fi
# shellcheck disable=SC1090
source "${SITE_CONF}"

# ---- Required-var validation ----------------------------------------------
_missing=()
for v in SITE_NAME SITE_DISPLAY SITE_ROOT MEMBERS_GROUP ADMINS_GROUP \
         ADMIN_USER CLUSTER_NAME SERVERNAME VSNP3_PREFIX VSNP_GUI_DIR; do
  [[ -z "${!v:-}" ]] && _missing+=("$v")
done
if [[ ${#_missing[@]} -gt 0 ]]; then
  echo "error: required site.conf vars unset: ${_missing[*]}" >&2
  exit 1
fi

# ---- Derived paths ---------------------------------------------------------
TOOLS_DIR="${SITE_ROOT}/tools"
REFS_DIR="${SITE_ROOT}/refs/vsnp3/reference_options"
VCF_DB_DIR="${SITE_ROOT}/refs/vsnp3/vcf_db_folders"
PROJECTS_DIR="${SITE_ROOT}/projects"
AUDIT_DIR="${SITE_ROOT}/audit"
BACKUP_DIR="${SITE_ROOT}/backup"
DATABASES_DIR="${SITE_ROOT}/databases"
OOD_APP_DIR="/var/www/ood/apps/sys/vsnp_gui"
OOD_CFG_DIR="/etc/ood/config"
SBIN_DIR="/usr/local/sbin"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

c_bold=$'\e[1m'; c_dim=$'\e[2m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_red=$'\e[31m'; c_rst=$'\e[0m'
log()   { echo "${c_bold}==>${c_rst} $*"; }
info()  { echo "    $*"; }
ok()    { echo "    ${c_grn}ok${c_rst}   $*"; }
warn()  { echo "    ${c_ylw}warn${c_rst} $*" >&2; }
die()   { echo "${c_red}error:${c_rst} $*" >&2; exit 1; }

# run CMD...  — execute, or print under --dry-run.
run() {
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "    ${c_dim}[dry-run] $*${c_rst}"
  else
    "$@"
  fi
}

need_root() {
  [[ ${DRY_RUN} -eq 1 ]] && return 0
  [[ "$(id -u)" -eq 0 ]] || die "phase '$1' mutates the system; run under sudo"
}

# require_group GROUP — precondition that a group exists. Fatal in a real run;
# a warning under --dry-run (where the 'groups' phase only printed, so the
# group isn't actually there yet).
require_group() {
  getent group "$1" >/dev/null 2>&1 && return 0
  [[ ${DRY_RUN} -eq 1 ]] && { warn "group $1 not present yet (the 'groups' phase creates it)"; return 0; }
  die "group $1 missing — run the 'groups' phase first"
}

# subst FILE  — emit FILE with every wgs3 literal rewritten to this site's
# values. Order is longest-match-first so /srv/kapurlab and the group names
# are consumed before a bare 'kapurlab'. Branding PROSE ("Kapur Lab", with a
# space) is intentionally NOT touched — that's manual-review copy.
subst() {
  sed -e "s|/srv/kapurlab|${SITE_ROOT}|g" \
      -e "s|kapurlab-admins|${ADMINS_GROUP}|g" \
      -e "s|kapurlab-members|${MEMBERS_GROUP}|g" \
      -e "s|WGS3|${SITE_DISPLAY}|g" \
      -e "s|wgs3|${CLUSTER_NAME}|g" \
      -e "s|kapurlab|${SITE_NAME}|g" \
      -e "s|vxk1|${ADMIN_USER}|g" \
      -e "s|100\.68\.171\.59|${SERVERNAME}|g" \
      "$1"
}

# install_subst SRC DEST MODE  — transform SRC and install to DEST (root:root).
install_subst() {
  local src="$1" dest="$2" mode="$3" tmp
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "    ${c_dim}[dry-run] subst ${src} -> ${dest} (mode ${mode})${c_rst}"
    return 0
  fi
  tmp="$(mktemp)"
  subst "${src}" > "${tmp}"
  install -D -o root -g root -m "${mode}" "${tmp}" "${dest}"
  rm -f "${tmp}"
  ok "installed ${dest}"
}

# mkdir_grp DIR GROUP MODE  — idempotent setgid shared dir.
mkdir_grp() {
  local dir="$1" grp="$2" mode="$3"
  run mkdir -p "${dir}"
  run chgrp "${grp}" "${dir}"
  run chmod "${mode}" "${dir}"
}

# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

phase_preflight() {
  log "preflight — checking layers 1 & 2 are in place"
  local fail=0

  if [[ -r /etc/os-release ]]; then
    . /etc/os-release
    info "OS: ${PRETTY_NAME:-unknown}"
    [[ "${VERSION_CODENAME:-}" == "noble" ]] || warn "wgs3 reference is Ubuntu noble (24.04); this is ${VERSION_CODENAME:-?}. OOD apt repo is codename-keyed."
  fi

  if [[ -d /etc/ood/config ]]; then
    ok "OOD core present (/etc/ood/config exists)"
  else
    warn "OOD core not detected. Run deploy/bootstrap_ood_core.sh first (INSTALL_OOD.md §2)."
    fail=1
  fi

  if command -v singularity >/dev/null 2>&1 || command -v apptainer >/dev/null 2>&1; then
    ok "container runtime present ($(command -v singularity apptainer 2>/dev/null | head -1))"
  else
    warn "no singularity/apptainer on PATH — OOD batch_connect sessions need it"; fail=1
  fi
  [[ -f "${SINGULARITY_IMAGE}" ]] || warn "session image missing: ${SINGULARITY_IMAGE}"

  if mountpoint -q "${SITE_ROOT}" 2>/dev/null; then
    local fstype; fstype="$(findmnt -rno FSTYPE "${SITE_ROOT}" 2>/dev/null || true)"
    info "${SITE_ROOT} is a ${fstype} mountpoint"
    [[ "${fstype}" == "xfs" ]] || warn "${SITE_ROOT} is not XFS — project quotas will no-op"
    findmnt -rno OPTIONS "${SITE_ROOT}" 2>/dev/null | grep -q prjquota \
      || warn "${SITE_ROOT} lacks prjquota mount option — project quotas will no-op"
  else
    warn "${SITE_ROOT} is not a separate mountpoint. Storage layout (layer 1) should provision it; see runbooks/T-19-storage-layout.md"
  fi

  if [[ -x "${NODE_BIN}" && -x "${NPM_BIN}" ]]; then
    ok "node $("${NODE_BIN}" --version 2>/dev/null) / npm $("${NPM_BIN}" --version 2>/dev/null)"
  else
    warn "node/npm not found at ${NODE_BIN} / ${NPM_BIN} — needed for the frontend build (apt install nodejs npm)"; fail=1
  fi

  if [[ -x "${CONDA_BASE}/bin/conda" ]] || command -v conda >/dev/null 2>&1; then
    ok "conda available for vsnp3 env creation"
  else
    warn "no conda at ${CONDA_BASE} — install miniforge before the toolchain phase"
  fi

  id "${ADMIN_USER}" >/dev/null 2>&1 || warn "ADMIN_USER '${ADMIN_USER}' is not (yet) a real account — create it before the cron phase"

  [[ ${fail} -eq 0 ]] && ok "preflight passed" || warn "preflight found gaps (above) — resolve before a real (non-dry-run) install"
}

phase_groups() {
  need_root groups
  log "groups — lab Unix groups"
  for g in "${MEMBERS_GROUP}" "${ADMINS_GROUP}"; do
    if getent group "${g}" >/dev/null; then
      ok "group ${g} exists"
    else
      run groupadd "${g}"; ok "created group ${g}"
    fi
  done
  info "add users later with: ${SITE_NAME}-add-user <name> [--admin]"
}

phase_storage() {
  need_root storage
  log "storage — ${SITE_ROOT} shared subtree"
  require_group "${MEMBERS_GROUP}"
  mkdir_grp "${SITE_ROOT}"                 "${MEMBERS_GROUP}" 0755
  mkdir_grp "${TOOLS_DIR}"                 "${ADMINS_GROUP}"  2775
  mkdir_grp "${SITE_ROOT}/refs"            "${ADMINS_GROUP}"  2775
  mkdir_grp "${SITE_ROOT}/refs/vsnp3"      "${ADMINS_GROUP}"  2775
  mkdir_grp "${REFS_DIR}"                  "${ADMINS_GROUP}"  2775
  mkdir_grp "${VCF_DB_DIR}"                "${ADMINS_GROUP}"  2775
  mkdir_grp "${PROJECTS_DIR}"              "${MEMBERS_GROUP}" 2775
  mkdir_grp "${AUDIT_DIR}"                 "${ADMINS_GROUP}"  2775
  mkdir_grp "${AUDIT_DIR}/env_snapshots"   "${ADMINS_GROUP}"  2775
  mkdir_grp "${DATABASES_DIR}"             "${MEMBERS_GROUP}" 2775
  mkdir_grp "${BACKUP_DIR}"                "${MEMBERS_GROUP}" 0755
  ok "shared tree created (setgid; new files inherit group)"
}

phase_toolchain() {
  need_root toolchain
  log "toolchain — vsnp3 conda env, patches, frontend build"

  local conda="${CONDA_BASE}/bin/conda"
  [[ -x "${conda}" ]] || conda="$(command -v conda 2>/dev/null || true)"

  # 1. vsnp3 conda env (also hosts the FastAPI backend deps — see audit §3)
  if [[ -x "${VSNP3_PREFIX}/bin/python" ]]; then
    ok "vsnp3 env exists at ${VSNP3_PREFIX}"
  elif [[ -z "${conda}" ]]; then
    warn "no conda found; skipping vsnp3 env creation. Install miniforge then re-run."
  else
    # An UNPINNED create is not "take the newest vsnp3" — it is "take the newest
    # python, then whatever vsnp3 still fits it", and the oldest release has the
    # loosest python bound. That is how this site built vsnp3 3.16 (python >=3.8)
    # on python 3.14 while the manifest pinned 3.35 (python <=3.12), reported
    # success, and served the old analysis code until someone read the version on
    # a dashboard card. Refuse to repeat it silently.
    local spec="vsnp3"
    if [[ -n "${VSNP3_VERSION:-}" ]]; then
      spec="vsnp3=${VSNP3_VERSION}"
    else
      warn "VSNP3_VERSION is not set in ${SITE_CONF}."
      warn "  Creating the env unpinned lets conda pick the newest python and then"
      warn "  fall back to whatever vsnp3 still supports it — typically 3.16 (2023)."
      warn "  Set VSNP3_VERSION to the suite manifest's pin and re-run this phase."
    fi
    # snp-dists is NOT a vsnp3 dependency but the Step 2 SNP-distance analysis
    # needs it (wgs3's env has it); install it alongside or Step 2 fails with
    # "SNP Analysis unavailable: missing snp-dists".
    run "${conda}" create -y -p "${VSNP3_PREFIX}" -c conda-forge -c bioconda "${spec}" snp-dists
    ok "created vsnp3 env (${spec} + snp-dists)"
  fi

  # 2. Web-layer deps into the same env (prod runs uvicorn from this python).
  #    The vsnp3 conda env ALREADY provides the scientific stack
  #    (numpy/pandas/scipy/matplotlib/openpyxl) on its own Python. The pins in
  #    backend/requirements.txt are OLDER and have no wheels for that Python, so
  #    `pip install -r requirements.txt` would trigger slow, fragile source
  #    builds (numpy from sdist on py3.14). Install ONLY what conda doesn't ship
  #    — matching wgs3 prod (conda scientific stack + pip'd web layer). The
  #    pinned requirements.txt stays as-is for a standalone (non-conda) venv.
  if [[ -x "${VSNP3_PREFIX}/bin/pip" ]]; then
    run "${VSNP3_PREFIX}/bin/pip" install --upgrade \
      fastapi uvicorn pydantic python-multipart aiofiles
    ok "web-layer deps installed into vsnp3 env (scientific stack from conda)"
  fi

  # 3. apply vsnp3 patches (idempotent, sentinel-detected)
  if [[ -d "${VSNP3_PREFIX}/bin" ]]; then
    run "${REPO_DIR}/deploy/vsnp3-patches/apply.sh" "${VSNP3_PREFIX}"
  fi

  # 4. register the reference-options path vsnp3 reads at runtime
  local rop="${VSNP3_PREFIX}/dependencies/reference_options_paths.txt"
  if [[ ${DRY_RUN} -eq 1 ]]; then
    info "[dry-run] write ${REFS_DIR} -> ${rop}"
  else
    run mkdir -p "${VSNP3_PREFIX}/dependencies"
    if grep -qxF "${REFS_DIR}" "${rop}" 2>/dev/null; then
      ok "reference path already registered"
    else
      # tmp+mv, NOT >>: in a conda env this file is a hardlink into the
      # package cache, and an in-place append would seed every future vsnp3
      # env on this machine with this install's paths.
      _roptmp="$(mktemp "${rop}.XXXXXX")"
      { [[ -f "${rop}" ]] && cat "${rop}"; echo "${REFS_DIR}"; } > "${_roptmp}"
      mv -f "${_roptmp}" "${rop}"
      ok "registered ${REFS_DIR} in ${rop##*/}"
    fi
  fi

  # 4b. seed the lab's Step 2 VCF comparison databases (one-time). The marker
  #     means an admin who later removes a DB never has it re-added, and an
  #     existing entry of the same name is never overwritten. Clone failure is
  #     a warn — these are an enhancement, not a build dependency.
  local vcf_seed_marker="${VCF_DB_DIR}/.vcf-db-directories.seeded"
  if [[ ${DRY_RUN} -eq 1 ]]; then
    info "[dry-run] seed kapurlab/vcf_db_directories -> ${VCF_DB_DIR} (once)"
  elif [[ ! -f "${vcf_seed_marker}" ]]; then
    local vcf_clone="${TOOLS_DIR}/vcf_db_directories"
    if [[ ! -d "${vcf_clone}" ]]; then
      git clone --depth 1 https://github.com/kapurlab/vcf_db_directories.git "${vcf_clone}" 2>/dev/null \
        || warn "could not clone kapurlab/vcf_db_directories (offline?) — Step 2 VCF DBs not seeded"
    fi
    if [[ -d "${vcf_clone}" ]]; then
      mkdir -p "${VCF_DB_DIR}"
      local _vd _vn _vlinked=0
      for _vd in "${vcf_clone}"/*/; do
        [[ -d "${_vd}" ]] || continue
        _vn="$(basename "${_vd}")"
        case "${_vn}" in .*) continue;; esac
        [[ -e "${VCF_DB_DIR}/${_vn}" ]] || { ln -sfn "${_vd%/}" "${VCF_DB_DIR}/${_vn}"; _vlinked=$((_vlinked+1)); }
      done
      printf 'when=%s\nclone=%s\nlinked=%s\n' "$(date -u +%FT%TZ)" "${vcf_clone}" "${_vlinked}" > "${vcf_seed_marker}"
      ok "Step 2 VCF databases seeded (${_vlinked} linked) -> ${VCF_DB_DIR}"
    fi
  else
    ok "Step 2 VCF databases already seeded (marker present)"
  fi

  # 5. ensure this repo is materialized at VSNP_GUI_DIR. The OOD card's
  #    before.sh/script.sh, the cron, and the frontend build (step 6) all read
  #    from VSNP_GUI_DIR — so when install_ood.sh is run from an unpacked
  #    distributable (REPO_DIR != VSNP_GUI_DIR) and the target isn't populated,
  #    copy the source there. A git checkout at VSNP_GUI_DIR is the other
  #    supported layout; we never clobber an existing checkout or copy.
  if [[ -d "${VSNP_GUI_DIR}/.git" ]]; then
    ok "vsnp_gui checkout present at ${VSNP_GUI_DIR}"
  elif [[ -f "${VSNP_GUI_DIR}/frontend/package.json" ]]; then
    ok "vsnp_gui source present at ${VSNP_GUI_DIR}"
  elif [[ "${REPO_DIR}" != "${VSNP_GUI_DIR}" ]]; then
    run mkdir -p "${VSNP_GUI_DIR}"
    run rsync -a \
        --exclude '.git' --exclude 'node_modules' --exclude 'frontend/dist' \
        --exclude '.venv' --exclude '.iconenv' --exclude '__pycache__' \
        --exclude '*.pyc' --exclude 'dist/' \
        "${REPO_DIR}/" "${VSNP_GUI_DIR}/"
    run chgrp -R "${ADMINS_GROUP}" "${VSNP_GUI_DIR}" 2>/dev/null || true
    ok "materialized vsnp_gui source -> ${VSNP_GUI_DIR} (from ${REPO_DIR})"
  else
    warn "vsnp_gui not present at ${VSNP_GUI_DIR} and no separate source to copy."
    info "  git clone https://github.com/kapurlab/vsnp_gui.git ${VSNP_GUI_DIR}"
  fi

  # A copied (non-clone) tree may carry a gitignored backend/data/config.json
  # holding another machine's paths; load_config() would migrate it into the
  # first user's config (wrong projects_root/vsnp3_path). Remove it — the
  # built-in defaults are correct for a fresh site.
  if [[ -f "${VSNP_GUI_DIR}/backend/data/config.json" ]]; then
    run rm -f "${VSNP_GUI_DIR}/backend/data/config.json"
    ok "removed stale backend/data/config.json (would leak foreign paths)"
  fi

  # 6. frontend build (uvicorn serves frontend/dist as StaticFiles)
  if [[ -f "${VSNP_GUI_DIR}/frontend/package.json" && -x "${NPM_BIN}" ]]; then
    run bash -c "cd '${VSNP_GUI_DIR}/frontend' && '${NPM_BIN}' ci && '${NPM_BIN}' run build"
    ok "frontend built -> ${VSNP_GUI_DIR}/frontend/dist"
  else
    warn "frontend build skipped (need ${VSNP_GUI_DIR}/frontend + npm). Run 'npm ci && npm run build' there."
  fi
}

phase_refs() {
  log "refs — reference data (DATA; not pulled automatically)"
  info "The full 27 reference sets + VCF DBs are bulk data, not in git."
  info "The sample bundle (deploy/sample_data) installs ONE small reference for the out-of-box demo."
  if [[ -n "${REFS_RSYNC_SOURCE:-}" ]]; then
    info "To populate the full set, run (as a user that can write ${SITE_ROOT}/refs):"
    echo
    echo "      rsync -aAX --info=progress2 \\"
    echo "        ${REFS_RSYNC_SOURCE%/}/ \\"
    echo "        ${SITE_ROOT}/refs/vsnp3/"
    echo
    info "Record the snapshot date — this is a point-in-time copy of wgs3's curated refs."
  else
    info "Set REFS_RSYNC_SOURCE in site.conf to print the exact rsync command."
  fi
  local n=0
  [[ -d "${REFS_DIR}" ]] && n="$(find "${REFS_DIR}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${n}" -gt 0 ]] && ok "${n} reference sets currently present" || warn "no reference sets at ${REFS_DIR} yet"
}

phase_app() {
  need_root app
  log "app — OOD card at ${OOD_APP_DIR}"
  if [[ -d "${OOD_APP_DIR}" && ${DRY_RUN} -eq 0 ]]; then
    local bak
    bak="/var/backups/ood/vsnp_gui/$(date +%Y%m%d_%H%M%S)"
    run mkdir -p "${bak}"; run cp -a "${OOD_APP_DIR}/." "${bak}/"; ok "backed up existing card -> ${bak}"
  fi
  install_subst "${REPO_DIR}/deploy/ood/manifest.yml"           "${OOD_APP_DIR}/manifest.yml"            0644
  install_subst "${REPO_DIR}/deploy/ood/form.yml"               "${OOD_APP_DIR}/form.yml"                0644
  install_subst "${REPO_DIR}/deploy/ood/submit.yml.erb"         "${OOD_APP_DIR}/submit.yml.erb"          0644
  install_subst "${REPO_DIR}/deploy/ood/view.html.erb"          "${OOD_APP_DIR}/view.html.erb"           0644
  install_subst "${REPO_DIR}/deploy/ood/template/before.sh"     "${OOD_APP_DIR}/template/before.sh"      0755
  install_subst "${REPO_DIR}/deploy/ood/template/script.sh.erb" "${OOD_APP_DIR}/template/script.sh.erb"  0644
  render_cluster_yml
}

render_cluster_yml() {
  local dest="${OOD_CFG_DIR}/clusters.d/${CLUSTER_NAME}.yml"
  if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "    ${c_dim}[dry-run] render ${dest}${c_rst}"
    return 0
  fi
  # Build ssh_hosts. Two linux_host adapter constraints drive this:
  #  (1) It REFUSES to submit unless one of the box's own names (from
  #      `hostname -A`) is in ssh_hosts — so we auto-include them.
  #  (2) It records the LAST matching ssh_hosts entry as the job's host, and
  #      parses that host with a regex that ONLY accepts DOTTED FQDNs. A bare
  #      short hostname (e.g. `a8-an-vxk1-u3`, hyphens but no dot) parses to an
  #      EMPTY host, so OOD then polls nothing and marks every session
  #      "Completed" with no Connect button. So we emit dotted FQDNs LAST,
  #      guaranteeing the recorded host is a parseable FQDN.
  # Result: localhost + short names first, then SERVERNAME + every FQDN, deduped.
  local tmp h seen=" " nondot="" dotted=""
  for h in localhost "${SERVERNAME}" ${SSH_EXTRA_HOSTS:-} $(hostname -A 2>/dev/null) $(hostname 2>/dev/null); do
    [[ -z "${h}" || " ${seen}" == *" ${h} "* ]] && continue
    seen+="${h} "
    if [[ "${h}" == *.* ]]; then dotted+="${h} "; else nondot+="${h} "; fi
  done
  local host_lines=""
  for h in ${nondot} ${dotted}; do host_lines+="      - ${h}"$'\n'; done
  host_lines="${host_lines%$'\n'}"
  tmp="$(mktemp)"
  # Write the heredoc straight to the temp file (no command-substitution
  # wrapper — that form mis-parses under bash 3.2).
  cat > "${tmp}" <<YAML
---
v2:
  metadata:
    title: "${SITE_DISPLAY} (Local)"
  login:
    host: "localhost"
  job:
    adapter: "linux_host"
    submit_host: "localhost"
    ssh_hosts:
${host_lines}
    site_timeout: 7200
    debug: true
    singularity_bin: /usr/bin/singularity
    # Bind /run/systemd/resolve so the container's /etc/resolv.conf symlink
    # resolves and in-container DNS works (SRA/NCBI/ENA fetches). SITE_ROOT
    # MUST be in the bindpath or shared tools/refs are invisible in-session.
    singularity_bindpath: ${SINGULARITY_BINDPATH}
    singularity_image: ${SINGULARITY_IMAGE}
    strict_host_checking: false
    tmux_bin: /usr/bin/tmux
YAML
  install -D -o root -g root -m 0644 "${tmp}" "${dest}"; rm -f "${tmp}"
  ok "rendered cluster config ${dest}"
}

phase_portal() {
  need_root portal
  log "portal — dashboard branding + home-page override"
  install_subst "${REPO_DIR}/deploy/ood/portal/ondemand.d/dashboard.yml" "${OOD_CFG_DIR}/ondemand.d/dashboard.yml" 0644
  install_subst "${REPO_DIR}/deploy/ood/portal/wgs_pipelines.yml"        "${OOD_CFG_DIR}/wgs_pipelines.yml"        0644
  install_subst "${REPO_DIR}/deploy/ood/portal/apps/dashboard/views/dashboard/index.html.erb" \
                "${OOD_CFG_DIR}/apps/dashboard/views/dashboard/index.html.erb" 0644
  warn "Branding files (wgs_pipelines.yml, index.html.erb) carry KapurLab/Penn State prose."
  warn "Structural tokens were substituted, but REVIEW THEM BY HAND for ${SITE_DISPLAY} copy."
}

phase_admin() {
  need_root admin
  log "admin — ${SITE_NAME}-* scripts to ${SBIN_DIR}"
  require_group "${ADMINS_GROUP}"
  install_subst "${REPO_DIR}/deploy/admin/kapurlab-add-user.sh"        "${SBIN_DIR}/${SITE_NAME}-add-user.sh"        0750
  install_subst "${REPO_DIR}/deploy/admin/kapurlab-setup-project.sh"   "${SBIN_DIR}/${SITE_NAME}-setup-project.sh"   0750
  install_subst "${REPO_DIR}/deploy/admin/kapurlab-rename-project.sh"  "${SBIN_DIR}/${SITE_NAME}-rename-project.sh"  0750
  if [[ ${DRY_RUN} -eq 0 ]]; then
    run chgrp "${ADMINS_GROUP}" "${SBIN_DIR}/${SITE_NAME}-"*.sh 2>/dev/null || true
  fi
}

phase_cron() {
  need_root cron
  log "cron — T-07 provenance index janitor (runs as ${ADMIN_USER})"
  id "${ADMIN_USER}" >/dev/null 2>&1 || warn "ADMIN_USER ${ADMIN_USER} doesn't exist — cron will fail until it does"
  install_subst "${REPO_DIR}/deploy/admin/vsnp_gui-provenance.cron" "/etc/cron.d/vsnp_gui-provenance" 0644
  info "the cron calls ${VSNP_GUI_DIR}/deploy/admin/provenance-cron.sh (provided by the repo checkout)"
}

phase_verify() {
  log "verify — smoke checks"
  local p
  for p in "${VSNP3_PREFIX}/bin/python" "${VSNP_GUI_DIR}/frontend/dist/index.html"; do
    [[ -e "${p}" ]] && ok "present: ${p}" || warn "missing: ${p}"
  done
  [[ -f "${OOD_APP_DIR}/manifest.yml" ]] && ok "OOD card installed" || warn "OOD card missing"
  [[ -f "${OOD_CFG_DIR}/clusters.d/${CLUSTER_NAME}.yml" ]] && ok "cluster config installed" || warn "cluster config missing"
  for g in "${MEMBERS_GROUP}" "${ADMINS_GROUP}"; do
    getent group "${g}" >/dev/null && ok "group ${g}" || warn "group ${g} missing"
  done
  if [[ -x "${VSNP3_PREFIX}/bin/python" ]]; then
    "${VSNP3_PREFIX}/bin/python" -c 'import fastapi, uvicorn' 2>/dev/null \
      && ok "fastapi+uvicorn importable in vsnp3 env" \
      || warn "fastapi/uvicorn not importable — re-run the toolchain phase"
  fi
  info "Final manual check: launch the OOD card and confirm 'Open vSNP GUI' loads through /rnode."
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

echo "${c_bold}vSNP GUI OOD installer${c_rst}  (site: ${SITE_DISPLAY} / ${SITE_ROOT})"
[[ ${DRY_RUN} -eq 1 ]] && echo "${c_ylw}DRY RUN — nothing will be modified${c_rst}"
echo "phases: ${PHASES[*]}"
echo

for ph in "${PHASES[@]}"; do
  case "${ph}" in
    preflight) phase_preflight ;;
    groups)    phase_groups ;;
    storage)   phase_storage ;;
    toolchain) phase_toolchain ;;
    refs)      phase_refs ;;
    app)       phase_app ;;
    portal)    phase_portal ;;
    admin)     phase_admin ;;
    cron)      phase_cron ;;
    verify)    phase_verify ;;
    *)         die "unknown phase: ${ph} (see --help)" ;;
  esac
  echo
done

log "done."
if [[ ${DRY_RUN} -eq 1 ]]; then echo "Re-run without --dry-run as root to apply."; fi
exit 0
