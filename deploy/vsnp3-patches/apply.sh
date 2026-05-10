#!/bin/bash
# Apply Kapur Lab patches to a vsnp3 install.
# Usage: apply.sh <vsnp3-install-prefix>   (e.g. /srv/kapurlab/tools/vsnp3)

set -euo pipefail

PREFIX="${1:-/srv/kapurlab/tools/vsnp3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="${SCRIPT_DIR}/v3.16-kapurlab.patch"

if [ ! -d "${PREFIX}/bin" ]; then
  echo "error: ${PREFIX}/bin not found" >&2
  exit 1
fi

# Detect "fully applied" by looking for the latest sentinel: the raw-string
# regex fix in vsnp3_step1.py (added when we picked up the SyntaxWarning hunks).
# Older sentinel (VSNP3_BOOTSTRAP in vsnp3_fasta_to_snps_table.py) only confirms
# the original patch set, not the newer additions.
if grep -q "fasta_name.append(re.sub(r'" "${PREFIX}/bin/vsnp3_step1.py" 2>/dev/null; then
  echo "patches already applied at ${PREFIX}; nothing to do"
  exit 0
fi

echo "applying ${PATCH} to ${PREFIX}"
# -N skips hunks already applied. patch returns 1 in that case even with -N,
# which is fine — only treat real "FAILED" hunks as errors. Strip the .rej
# files that idempotent skips leave behind.
PATCH_OUT="$(patch -N -p1 -d "${PREFIX}" < "${PATCH}" 2>&1)" || true
echo "${PATCH_OUT}"
if echo "${PATCH_OUT}" | grep -q "FAILED"; then
  echo "error: real patch failures (see output above)" >&2
  exit 1
fi
find "${PREFIX}/bin" -maxdepth 1 -name "*.rej" -delete 2>/dev/null || true
echo "ok"
