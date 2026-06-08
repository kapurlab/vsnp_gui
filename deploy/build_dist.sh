#!/usr/bin/env bash
# build_dist.sh — produce the single distributable tarball a sysadmin installs
# from (INSTALL_OOD.md §8). Bundles the git source + the offline sample bundle
# + (optionally) the OOD session image, with a top-level INSTALL.md entry point.
#
#   Usage:
#     deploy/build_dist.sh [options]
#
#   Options:
#     --version V      version label for the artifact (default: git describe)
#     --out DIR        output directory (default: ./dist)
#     --sif PATH       include this ood_default.sif at the tarball top level
#                      (lets site.conf use OOD_SIF_SOURCE=<unpacked>/ood_default.sif)
#     --from USER@HOST refresh the sample bundle from a reference server first
#                      (runs sample_data/build_bundle.sh); default: use the
#                      bundle already staged on disk
#     --no-bundle      omit the sample bundle (smaller artifact, no demo)
#     -h | --help      this help
#
# The produced artifact:
#     vsnp_gui-platform-<version>.tar.gz
#       INSTALL.md                 <- start here
#       vsnp_gui/                  <- full source (deploy/, backend/, frontend/, docs/)
#         deploy/sample_data/bundle/   <- the offline demo (injected; untracked in git)
#       ood_default.sif            <- only if --sif given
#
# Self-contained: the recipient unpacks, copies vsnp_gui/deploy/site.conf.example
# to site.conf, edits it, and follows INSTALL.md. Network is still needed for OS
# packages, the OSC apt repo, conda/bioconda, and (optionally) the full refs.
#
# T-50.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_DIR}"

VERSION=""
OUT="${REPO_DIR}/dist"
SIF=""
FROM=""
WITH_BUNDLE=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)   VERSION="$2"; shift 2 ;;
    --out)       OUT="$2"; shift 2 ;;
    --sif)       SIF="$2"; shift 2 ;;
    --from)      FROM="$2"; shift 2 ;;
    --no-bundle) WITH_BUNDLE=0; shift ;;
    -h|--help)   sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v git >/dev/null || { echo "git required" >&2; exit 1; }
[[ -d "${REPO_DIR}/.git" ]] || { echo "not a git repo: ${REPO_DIR}" >&2; exit 1; }

[[ -n "${VERSION}" ]] || VERSION="$(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD)"
NAME="vsnp_gui-platform-${VERSION}"

c_grn=$'\e[32m'; c_dim=$'\e[2m'; c_ylw=$'\e[33m'; c_bold=$'\e[1m'; c_rst=$'\e[0m'
log() { echo "${c_bold}==>${c_rst} $*"; }
ok()  { echo "  ${c_grn}ok${c_rst} $*"; }
warn(){ echo "  ${c_ylw}warn${c_rst} $*" >&2; }

log "building ${NAME}"
if ! git diff --quiet HEAD 2>/dev/null; then
  warn "working tree has uncommitted changes — the source archive is HEAD, NOT your working tree."
  warn "commit first if you want those changes in the artifact."
fi

STAGE="${OUT}/${NAME}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}/vsnp_gui"

# 1. Source from HEAD (excludes gitignored: frontend/dist, venvs, the bundle).
log "git archive HEAD -> vsnp_gui/"
git archive --format=tar --prefix=vsnp_gui/ HEAD | tar -x -C "${STAGE}"
ok "source extracted"

# 2. Sample bundle (untracked in git — inject it so install_bundle.sh finds it
#    at its default ./bundle path).
if [[ ${WITH_BUNDLE} -eq 1 ]]; then
  if [[ -n "${FROM}" ]]; then
    log "refreshing sample bundle from ${FROM}"
    "${REPO_DIR}/deploy/sample_data/build_bundle.sh" --from "${FROM}"
  fi
  if [[ -d "${REPO_DIR}/deploy/sample_data/bundle" ]]; then
    log "injecting sample bundle"
    mkdir -p "${STAGE}/vsnp_gui/deploy/sample_data"
    cp -a "${REPO_DIR}/deploy/sample_data/bundle" "${STAGE}/vsnp_gui/deploy/sample_data/bundle"
    ok "bundle injected ($(du -sh "${STAGE}/vsnp_gui/deploy/sample_data/bundle" | cut -f1))"
  else
    warn "no sample bundle at deploy/sample_data/bundle — artifact will have no demo (run build_bundle.sh --from <server>)"
  fi
else
  log "--no-bundle: omitting sample data"
fi

# 3. Optional OOD session image at top level.
if [[ -n "${SIF}" ]]; then
  if [[ -f "${SIF}" ]]; then
    log "including session image"
    cp "${SIF}" "${STAGE}/ood_default.sif"
    ok "ood_default.sif included ($(du -sh "${STAGE}/ood_default.sif" | cut -f1))"
  else
    warn "--sif ${SIF} not found; skipping"
  fi
fi

# 4. Top-level INSTALL.md entry point.
log "writing INSTALL.md"
cat > "${STAGE}/INSTALL.md" <<EOF
# vSNP GUI platform — install

Version: \`${VERSION}\`

This artifact is self-contained. Full runbook:
**\`vsnp_gui/docs/deploy/INSTALL_OOD.md\`** (read it — this is just the quickstart).

## Quickstart

\`\`\`bash
# 0. unpack (you've done this)
cd vsnp_gui

# 1. configure the site
cp deploy/site.conf.example deploy/site.conf
\$EDITOR deploy/site.conf          # set SITE_NAME, SITE_DISPLAY, SERVERNAME, ADMIN_USER, ...
$([[ -n "${SIF}" ]] && echo "                                   # set OOD_SIF_SOURCE=\$(pwd)/../ood_default.sif")

# 2. OOD core (skip if the box already runs OOD)
sudo deploy/bootstrap_ood_core.sh --dry-run    # review, then drop --dry-run

# 3. the vSNP platform (layers 3-4)
sudo deploy/install_ood.sh --dry-run           # review, then drop --dry-run

# 4. optional: the Kraken ID Parse app
sudo deploy/install_kraken.sh --dry-run

# 5. the out-of-the-box demo
sudo deploy/sample_data/install_bundle.sh --site-conf deploy/site.conf --user <login>
\`\`\`

Then browse to \`http://<SERVERNAME>/\`, launch the vSNP GUI card, open the
\`demo_sars_cov_2\` project, and run Step 1 → Step 2 (acceptance test:
\`vsnp_gui/docs/deploy/INSTALL_OOD.md\` §7).

Prerequisites the playbook assumes (see runbook §1): a SITE_ROOT mount, conda/
miniforge installed, and (for bootstrap) outbound network to the OS + OSC + conda
package mirrors.
EOF
ok "INSTALL.md written"

# 5. Tar it up.
log "creating tarball"
mkdir -p "${OUT}"
tar -czf "${OUT}/${NAME}.tar.gz" -C "${OUT}" "${NAME}"
rm -rf "${STAGE}"

SIZE="$(du -sh "${OUT}/${NAME}.tar.gz" | cut -f1)"
echo
log "done: ${OUT}/${NAME}.tar.gz (${SIZE})"
echo "  ship it:   scp ${OUT}/${NAME}.tar.gz <user@host>:~/"
echo "  unpack:    tar xzf ${NAME}.tar.gz && cd ${NAME} && less INSTALL.md"
