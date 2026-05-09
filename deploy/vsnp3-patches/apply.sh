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

# Detect "already applied" by looking for VSNP3_BOOTSTRAP in the target.
if grep -q "VSNP3_BOOTSTRAP" "${PREFIX}/bin/vsnp3_fasta_to_snps_table.py" 2>/dev/null; then
  echo "patches already applied at ${PREFIX}; nothing to do"
  exit 0
fi

echo "applying ${PATCH} to ${PREFIX}"
patch -p1 -d "${PREFIX}" < "${PATCH}"
echo "ok"
