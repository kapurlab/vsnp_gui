#!/bin/bash
# Apply Kapur Lab patches to a vsnp3 install.
# Usage: apply.sh <vsnp3-install-prefix>   (e.g. /srv/kapurlab/tools/vsnp3)

set -euo pipefail

PREFIX="${1:-/srv/kapurlab/tools/vsnp3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Applied in order. The robustness/CPU-cap patch applies on top of the base one.
PATCHES=(
  "${SCRIPT_DIR}/v3.16-kapurlab.patch"
  "${SCRIPT_DIR}/v3.16-kapurlab-step2-robustness.patch"
)

if [ ! -d "${PREFIX}/bin" ]; then
  echo "error: ${PREFIX}/bin not found" >&2
  exit 1
fi

# Detect "fully applied" via the newest sentinel: VSNP3_MAX_CPUS, added by the
# step2-robustness patch (the last one in the list). If it's present, the whole
# set — including all earlier patches — is applied.
if grep -q "VSNP3_MAX_CPUS" "${PREFIX}/bin/vsnp3_step2.py" 2>/dev/null; then
  echo "patches already applied at ${PREFIX}; nothing to do"
  exit 0
fi

# -N skips hunks already applied. patch returns 1 in that case even with -N,
# which is fine — only treat real "FAILED" hunks as errors. Strip the .rej
# files that idempotent skips leave behind.
for PATCH in "${PATCHES[@]}"; do
  echo "applying $(basename "${PATCH}") to ${PREFIX}"
  PATCH_OUT="$(patch -N -p1 -d "${PREFIX}" < "${PATCH}" 2>&1)" || true
  echo "${PATCH_OUT}"
  if echo "${PATCH_OUT}" | grep -q "FAILED"; then
    echo "error: real patch failures in $(basename "${PATCH}") (see output above)" >&2
    exit 1
  fi
done
find "${PREFIX}/bin" -maxdepth 1 -name "*.rej" -delete 2>/dev/null || true
echo "ok"
