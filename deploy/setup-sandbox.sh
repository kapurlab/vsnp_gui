#!/usr/bin/env bash
# setup-sandbox.sh — per-user, NO-SUDO install of the vSNP GUI for an
# Open OnDemand sandbox app on a Slurm HPC.
#
# What it does, all under $HOME (nothing system-wide):
#   1. Ensures a conda (miniforge) is available.
#   2. Creates the vsnp3 conda env  (bioconda vsnp3 + snp-dists + nodejs).
#   3. pip-installs the web layer (fastapi/uvicorn/…) into that env.
#   4. Applies the Kapur Lab vsnp3 patches.
#   5. Registers the reference-options path vsnp3 reads at runtime.
#   6. Builds the React frontend (frontend/dist/).
#   7. Writes ~/.config/vsnp_gui/sandbox.env for the OOD script to source.
#   8. Symlinks the sandbox OOD app into ~/ondemand/dev/ so the card appears.
#
# It does NOT pull reference genomes — those are large and site-specific.
# Use --refs-from <rsync-source> to copy them, or place them manually and
# point REFS_DIR at them.
#
# Usage:
#   deploy/setup-sandbox.sh [--site-root <dir>] [--refs-from <rsync src>]
#                           [--vsnp3-version <ver>] [--dry-run] [--no-link]
set -euo pipefail

# ---- resolve repo dir (this script lives in <repo>/deploy/) ----
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ---- defaults ----
VSNP3_ENV="${HOME}/miniforge3/envs/vsnp3"
CONDA_BASE="${HOME}/miniforge3"
SITE_ROOT=""               # optional shared group tree; blank => per-user $HOME
REFS_FROM=""               # optional rsync source for reference_options
REFS_DIR="${HOME}/vSNP_reference_options"
VSNP3_VERSION=""
DRY_RUN=0
DO_LINK=1
DEV_APP_NAME="vsnp_gui"    # name of the card dir under ~/ondemand/dev/

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }
run()  { if [[ ${DRY_RUN} -eq 1 ]]; then echo "  [dry-run] $*"; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-root)      SITE_ROOT="$2"; shift 2;;
    --refs-from)      REFS_FROM="$2"; shift 2;;
    --refs-dir)       REFS_DIR="$2"; shift 2;;
    --vsnp3-version)  VSNP3_VERSION="$2"; shift 2;;
    --conda-base)     CONDA_BASE="$2"; VSNP3_ENV="${CONDA_BASE}/envs/vsnp3"; shift 2;;
    --dry-run)        DRY_RUN=1; shift;;
    --no-link)        DO_LINK=0; shift;;
    -h|--help)        sed -n '2,30p' "$0"; exit 0;;
    *) die "unknown arg: $1";;
  esac
done

log "vSNP GUI sandbox setup"
echo "  repo:      ${REPO_DIR}"
echo "  conda:     ${CONDA_BASE}"
echo "  vsnp3 env: ${VSNP3_ENV}"
[[ -n "${SITE_ROOT}" ]] && echo "  site root: ${SITE_ROOT} (shared)"
echo "  refs dir:  ${REFS_DIR}"
[[ ${DRY_RUN} -eq 1 ]] && warn "DRY RUN — no changes will be made"

# ---- 1. conda ----
CONDA="${CONDA_BASE}/bin/conda"
if [[ ! -x "${CONDA}" ]]; then
  CONDA="$(command -v conda 2>/dev/null || true)"
fi
if [[ -z "${CONDA}" || ! -x "${CONDA}" ]]; then
  log "installing miniforge to ${CONDA_BASE} (no sudo)"
  tmp_inst="${TMPDIR:-/tmp}/miniforge_$$.sh"
  run bash -c "curl -fsSL -o '${tmp_inst}' https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-\$(uname)-\$(uname -m).sh"
  run bash "${tmp_inst}" -b -p "${CONDA_BASE}"
  run rm -f "${tmp_inst}"
  CONDA="${CONDA_BASE}/bin/conda"
fi
[[ ${DRY_RUN} -eq 1 || -x "${CONDA}" ]] && ok "conda: ${CONDA}"

# ---- 2. vsnp3 env (+ snp-dists for Step 2 SNP distances, + nodejs for build) ----
if [[ -d "${VSNP3_ENV}" ]]; then
  ok "vsnp3 env already exists at ${VSNP3_ENV}"
else
  spec="vsnp3"; [[ -n "${VSNP3_VERSION}" ]] && spec="vsnp3=${VSNP3_VERSION}"
  log "creating vsnp3 env (${spec} + snp-dists + nodejs)"
  run "${CONDA}" create -y -p "${VSNP3_ENV}" -c conda-forge -c bioconda "${spec}" snp-dists nodejs
  ok "created ${VSNP3_ENV}"
fi
PIP="${VSNP3_ENV}/bin/pip"
PYTHON="${VSNP3_ENV}/bin/python"

# ---- 3. web layer (only what conda's scientific stack doesn't ship) ----
log "installing web-layer python deps into the vsnp3 env"
if [[ -f "${REPO_DIR}/backend/requirements.txt" ]]; then
  run "${PIP}" install --no-input fastapi "uvicorn[standard]" python-multipart websockets aiofiles
  ok "web layer installed"
else
  warn "no backend/requirements.txt found — installed the core web deps anyway"
fi

# ---- 4. vsnp3 patches (idempotent) ----
if [[ -x "${REPO_DIR}/deploy/vsnp3-patches/apply.sh" ]]; then
  log "applying Kapur Lab vsnp3 patches"
  run "${REPO_DIR}/deploy/vsnp3-patches/apply.sh" "${VSNP3_ENV}" || warn "patch step returned non-zero (may already be applied)"
fi

# ---- 5. register reference-options path ----
ROP="${VSNP3_ENV}/dependencies/reference_options_paths.txt"
log "registering reference-options path -> ${ROP}"
run mkdir -p "${VSNP3_ENV}/dependencies"
if [[ ${DRY_RUN} -eq 0 ]]; then
  if [[ ! -f "${ROP}" ]] || ! grep -qxF "${REFS_DIR}" "${ROP}" 2>/dev/null; then
    # tmp+mv, NOT >>: the file is a hardlink into the conda package cache;
    # appending in place would pre-seed every future vsnp3 env with it.
    _roptmp="$(mktemp "${ROP}.XXXXXX")"
    { [[ -f "${ROP}" ]] && cat "${ROP}"; printf '%s\n' "${REFS_DIR}"; } > "${_roptmp}"
    mv -f "${_roptmp}" "${ROP}"
  fi
fi
ok "vsnp3 will look for reference sets under ${REFS_DIR}"

# ---- 6. frontend build ----
log "building the React frontend"
export PATH="${VSNP3_ENV}/bin:${PATH}"
if [[ ${DRY_RUN} -eq 1 ]]; then
  echo "  [dry-run] (cd ${REPO_DIR}/frontend && npm ci && npm run build)"
else
  ( cd "${REPO_DIR}/frontend" && npm ci && npm run build ) \
    && ok "frontend built -> frontend/dist/" \
    || warn "frontend build failed — run 'npm ci && npm run build' in ${REPO_DIR}/frontend"
fi

# ---- 7. reference data (optional copy) ----
if [[ -n "${REFS_FROM}" ]]; then
  log "copying reference options from ${REFS_FROM}"
  run mkdir -p "${REFS_DIR}"
  run rsync -aAX --info=progress2 "${REFS_FROM}/" "${REFS_DIR}/"
  ok "reference options synced to ${REFS_DIR}"
else
  warn "no --refs-from given. Copy your reference sets into ${REFS_DIR} before running Step 1, e.g.:"
  echo "      rsync -aAX --info=progress2 user@refserver:/path/vSNP_reference_options/ ${REFS_DIR}/"
fi

# ---- 8. write sandbox.env for the OOD launcher to source ----
CFG_DIR="${HOME}/.config/vsnp_gui"
log "writing ${CFG_DIR}/sandbox.env"
run mkdir -p "${CFG_DIR}"
if [[ ${DRY_RUN} -eq 0 ]]; then
  {
    echo "# Written by deploy/setup-sandbox.sh — sourced by the OOD sandbox app."
    echo "VSNP_GUI_DIR=${REPO_DIR}"
    echo "VSNP3_ENV=${VSNP3_ENV}"
    [[ -n "${SITE_ROOT}" ]] && echo "VSNP_GUI_SITE_ROOT=${SITE_ROOT}"
  } > "${CFG_DIR}/sandbox.env"
fi
ok "sandbox.env written"

# ---- 9. link the OOD sandbox card into ~/ondemand/dev/ ----
if [[ ${DO_LINK} -eq 1 ]]; then
  DEV_DIR="${HOME}/ondemand/dev"
  APP_SRC="${REPO_DIR}/ood/apps/vsnp_gui_sandbox"
  log "linking sandbox card -> ${DEV_DIR}/${DEV_APP_NAME}"
  run mkdir -p "${DEV_DIR}"
  if [[ -e "${DEV_DIR}/${DEV_APP_NAME}" && ! -L "${DEV_DIR}/${DEV_APP_NAME}" ]]; then
    warn "${DEV_DIR}/${DEV_APP_NAME} exists and is not a symlink — leaving it alone"
  else
    run ln -sfn "${APP_SRC}" "${DEV_DIR}/${DEV_APP_NAME}"
    ok "card linked — it will appear under Develop -> My Sandbox Apps"
  fi
fi

echo
log "Next steps"
cat <<EOF
  1. Edit the cluster name in:
       ${REPO_DIR}/ood/apps/vsnp_gui_sandbox/form.yml
     Set  cluster: "<your HPC's cluster name>"  (ask your OOD admin).
  2. In OOD: Develop -> My Sandbox Apps -> vSNP GUI (sandbox) -> Launch.
     Fill in partition / account / cores / memory and submit.
  3. If reference data isn't in ${REFS_DIR} yet, copy it there (see above).
EOF
