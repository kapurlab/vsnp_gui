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
  "${SCRIPT_DIR}/v3.16-kapurlab-step2-read-length.patch"
  "${SCRIPT_DIR}/v3.16-kapurlab-step2-per-group-isolation.patch"
)

if [ ! -d "${PREFIX}/bin" ]; then
  echo "error: ${PREFIX}/bin not found" >&2
  exit 1
fi

# Content-based fixes (applied before the line-context .patch loop below).
# These target expressions that are IDENTICAL across vsnp3 point releases but
# sit at different line numbers / next to different comments, so a line-context
# .patch is too fragile — a plain idempotent content replace is version-proof.
#
# passQ crash: vsnp3_step1.py gates FASTQ usability with
#   float(fastq_stats.R1.passQ20) < 50.0   (and passQ30 < 70.0)
# but those stats can be comma-formatted counts (e.g. '9,177'), so float()
# raises ValueError -> the whole sample fails (looks like a bcftools error
# because the traceback lands after the mpileup log). Strip the thousands
# separator first, matching vsnp3's own max_len handling.
STEP1_PY="${PREFIX}/bin/vsnp3_step1.py"
if [ -f "${STEP1_PY}" ] && grep -qF "float(fastq_stats.R1.passQ20)" "${STEP1_PY}"; then
  sed -i \
    -e "s/float(fastq_stats\.R1\.passQ20)/float(str(fastq_stats.R1.passQ20).replace(',', ''))/g" \
    -e "s/float(fastq_stats\.R1\.passQ30)/float(str(fastq_stats.R1.passQ30).replace(',', ''))/g" \
    "${STEP1_PY}"
  echo "applied passQ comma fix to ${STEP1_PY}"
fi

# Detect "fully applied" via a sentinel unique to the NEWEST .patch (the last one
# in the list) — the isolate-each-group marker in group_on_defining_snps. If it's
# present, the .patch set is applied. Bump this marker whenever a newer patch is
# added, so existing installs (which already carry the older sentinels) still
# re-run apply and pick up the new hunk (patch -N harmlessly skips old ones).
# (Content fixes above always run — they're idempotent — regardless of this.)
if grep -q "kapurlab: isolate each group" "${PREFIX}/bin/vsnp3_group_on_defining_snps.py" 2>/dev/null; then
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
