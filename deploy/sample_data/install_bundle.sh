#!/usr/bin/env bash
# install_bundle.sh — place the staged sample bundle onto a target box.
#
# Offline (no network): copies bundle/ produced by build_bundle.sh into the
# site's reference + project locations, and registers the reference so vsnp3
# discovers it. Run AFTER install_ood.sh, as root.
#
#   Usage:
#     sudo ./install_bundle.sh --site-conf PATH (--user LOGIN | --shared) [options]
#
#   Options:
#     --site-conf PATH   site config (for SITE_ROOT, groups, VSNP3_PREFIX)
#     --user LOGIN       install the project under /home/LOGIN/projects/
#     --shared           install the project under ${SITE_ROOT}/projects/ instead
#     --bundle DIR       staged bundle dir (default ./bundle)
#     --dry-run
#     -h|--help
#
# T-50.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SITE_CONF=""; USER_LOGIN=""; SHARED=0; BUNDLE="${HERE}/bundle"; DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --site-conf) SITE_CONF="$2"; shift 2 ;;
    --user) USER_LOGIN="$2"; shift 2 ;;
    --shared) SHARED=1; shift ;;
    --bundle) BUNDLE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "${SITE_CONF}" && -f "${SITE_CONF}" ]] || { echo "error: --site-conf PATH required" >&2; exit 1; }
[[ -d "${BUNDLE}" ]] || { echo "error: bundle dir not found: ${BUNDLE} (run build_bundle.sh first)" >&2; exit 1; }
[[ ${SHARED} -eq 1 || -n "${USER_LOGIN}" ]] || { echo "error: pass --user LOGIN or --shared" >&2; exit 1; }
# shellcheck disable=SC1090
source "${SITE_CONF}"
: "${SITE_ROOT:?}"; : "${ADMINS_GROUP:?}"; : "${VSNP3_PREFIX:?}"

c_grn=$'\e[32m'; c_dim=$'\e[2m'; c_ylw=$'\e[33m'; c_rst=$'\e[0m'
ok() { echo "  ${c_grn}ok${c_rst} $*"; }
warn() { echo "  ${c_ylw}warn${c_rst} $*" >&2; }
run() { if [[ ${DRY_RUN} -eq 1 ]]; then echo "  ${c_dim}[dry-run] $*${c_rst}"; else "$@"; fi; }
[[ ${DRY_RUN} -eq 1 || "$(id -u)" -eq 0 ]] || { echo "error: run as root (sudo)" >&2; exit 1; }

REFS_DST="${SITE_ROOT}/refs/vsnp3/reference_options"
ROP="${VSNP3_PREFIX}/dependencies/reference_options_paths.txt"

echo "==> installing sample bundle from ${BUNDLE}"

# 1. reference set(s)
for refdir in "${BUNDLE}"/refs/*/; do
  [[ -d "${refdir}" ]] || continue
  local_name="$(basename "${refdir}")"
  run mkdir -p "${REFS_DST}/${local_name}"
  run rsync -a "${refdir}" "${REFS_DST}/${local_name}/"
  run chgrp -R "${ADMINS_GROUP}" "${REFS_DST}/${local_name}"
  run chmod -R g+rwX "${REFS_DST}/${local_name}"
  ok "reference ${local_name} -> ${REFS_DST}/${local_name}"
done

# 2. register the reference-options root (idempotent)
if [[ ${DRY_RUN} -eq 0 ]]; then
  run mkdir -p "$(dirname "${ROP}")"
  if grep -qxF "${REFS_DST}" "${ROP}" 2>/dev/null; then
    ok "reference path already registered"
  else
    echo "${REFS_DST}" >> "${ROP}"; ok "registered ${REFS_DST} in reference_options_paths.txt"
  fi
else
  echo "  ${c_dim}[dry-run] ensure ${REFS_DST} in ${ROP}${c_rst}"
fi

# 3. project(s) into the chosen projects root
if [[ ${SHARED} -eq 1 ]]; then
  PROJ_DST="${SITE_ROOT}/projects"; OWNER_GRP="${MEMBERS_GROUP:-${ADMINS_GROUP}}"; OWNER_USER="root"
else
  PROJ_DST="/home/${USER_LOGIN}/projects"
  id "${USER_LOGIN}" >/dev/null 2>&1 || warn "user ${USER_LOGIN} doesn't exist yet"
  OWNER_USER="${USER_LOGIN}"; OWNER_GRP="${USER_LOGIN}"
fi
run mkdir -p "${PROJ_DST}"
# The projects root itself must be owned by the user (the backend, running as
# that user, creates .jobs/ and new projects inside it). Creating it as root
# would block the user — chown the dir (not -R; subdirs handled per-project).
if [[ ${DRY_RUN} -eq 0 && ${SHARED} -eq 0 ]]; then
  chown "${OWNER_USER}:${OWNER_GRP}" "${PROJ_DST}"
fi
for projdir in "${BUNDLE}"/projects/*/; do
  [[ -d "${projdir}" ]] || continue
  pname="$(basename "${projdir}")"
  run rsync -a "${projdir}" "${PROJ_DST}/${pname}/"
  if [[ ${DRY_RUN} -eq 0 ]]; then
    if [[ ${SHARED} -eq 1 ]]; then
      run chgrp -R "${OWNER_GRP}" "${PROJ_DST}/${pname}"; run chmod -R g+rwX "${PROJ_DST}/${pname}"
    else
      run chown -R "${OWNER_USER}:${OWNER_GRP}" "${PROJ_DST}/${pname}"
    fi
  fi
  ok "project ${pname} -> ${PROJ_DST}/${pname}"
done

echo "==> sample bundle installed."
echo "    Launch the OOD vSNP GUI card as ${USER_LOGIN:-a member}, open the demo"
echo "    project, and Run Step 1 -> Step 2 (see sample_data/README.md acceptance test)."
