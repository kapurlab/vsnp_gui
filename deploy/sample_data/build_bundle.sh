#!/usr/bin/env bash
# build_bundle.sh — package the out-of-the-box sample dataset.
#
# Pulls the SARS-CoV-2 reference + 6 raw deer fastqs from a reference install
# (default: wgs3) into a self-contained bundle/ that ships in the
# distributable. Run this ONCE; install_bundle.sh consumes the result offline
# on the target box.
#
#   Usage:
#     ./build_bundle.sh --from <user@host> [options]
#
#   Options:
#     --from USER@HOST     reference server to pull from (required)
#     --src-root PATH      shared root on that server (default /srv/kapurlab)
#     --ref NAME           reference set (default NC_045512_wuhan-hu-1)
#     --project NAME       sample project (default demo_sars_cov_2)
#     --out DIR            stage dir (default ./bundle)
#     --with-results       also pull pre-built step1/step2 (view-only; carries
#                          the source server's absolute paths — not for a clean run)
#     --tar                also produce vsnp-sample-bundle.tar.gz
#     -h|--help
#
# T-50.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FROM=""; SRC_ROOT="/srv/kapurlab"
REF="NC_045512_wuhan-hu-1"; PROJECT="demo_sars_cov_2"
OUT="${HERE}/bundle"; WITH_RESULTS=0; MAKE_TAR=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --src-root) SRC_ROOT="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --with-results) WITH_RESULTS=1; shift ;;
    --tar) MAKE_TAR=1; shift ;;
    -h|--help) sed -n '2,28p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ -n "${FROM}" ]] || { echo "error: --from USER@HOST required" >&2; exit 1; }

c_grn=$'\e[32m'; c_rst=$'\e[0m'
ok() { echo "  ${c_grn}ok${c_rst} $*"; }

SRC_REF="${FROM}:${SRC_ROOT}/refs/vsnp3/reference_options/${REF}/"
SRC_PROJ="${FROM}:${SRC_ROOT}/projects/${PROJECT}"
DST_REF="${OUT}/refs/${REF}"
DST_PROJ="${OUT}/projects/${PROJECT}"

echo "==> staging bundle into ${OUT}"
mkdir -p "${DST_REF}" "${DST_PROJ}/download"

# 1. reference set (small; whole dir)
rsync -aL -q "${SRC_REF}" "${DST_REF}/"
ok "reference ${REF} ($(du -sh "${DST_REF}" | cut -f1))"

# 2. raw fastqs + project.json (the runnable inputs)
rsync -aL -q "${SRC_PROJ}/download/" "${DST_PROJ}/download/"
rsync -aL -q "${SRC_PROJ}/project.json" "${DST_PROJ}/project.json"
ok "fastqs ($(ls "${DST_PROJ}/download/"*.fastq.gz 2>/dev/null | wc -l | tr -d ' ') files, $(du -sh "${DST_PROJ}/download" | cut -f1))"

# 3. optional pre-built outputs (view-only; absolute-path caveat)
if [[ ${WITH_RESULTS} -eq 1 ]]; then
  rsync -aL -q "${SRC_PROJ}/step1/" "${DST_PROJ}/step1/" 2>/dev/null || true
  rsync -aL -q "${SRC_PROJ}/step2/" "${DST_PROJ}/step2/" 2>/dev/null || true
  ok "pre-built step1/step2 included (carry source absolute paths)"
fi

# 4. sanitize project.json: ready-to-run, site-neutral
PJ="${DST_PROJ}/project.json"
if command -v python3 >/dev/null 2>&1 && [[ -f "${PJ}" ]]; then
  python3 - "${PJ}" "${PROJECT}" "${REF}" <<'PY'
import json, sys
path, name, ref = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(path))
d["name"] = name
d.setdefault("display_name", f"{name}_{ref}")
d["reference"] = ref
d["status"] = "created"          # so the GUI shows it as ready to Run Step 1
d.pop("scope", None)             # let install_bundle decide personal/shared
json.dump(d, open(path, "w"), indent=2)
print("  sanitized project.json")
PY
fi

# 5. provenance stamp for the bundle itself
cat > "${OUT}/BUNDLE_INFO.txt" <<EOF
source: ${FROM}:${SRC_ROOT}
reference: ${REF}
project: ${PROJECT}
with_results: ${WITH_RESULTS}
staged_on_host: $(hostname 2>/dev/null || echo unknown)
EOF
ok "wrote BUNDLE_INFO.txt"

echo "==> bundle staged: $(du -sh "${OUT}" | cut -f1) at ${OUT}"
if [[ ${MAKE_TAR} -eq 1 ]]; then
  TARBALL="${HERE}/vsnp-sample-bundle.tar.gz"
  tar -C "${OUT}" -czf "${TARBALL}" .
  ok "tarball: ${TARBALL} ($(du -sh "${TARBALL}" | cut -f1))"
fi
echo "Next: sudo ./install_bundle.sh --site-conf ../site.conf --user <login>"
