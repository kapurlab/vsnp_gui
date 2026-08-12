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

# CPU cap. vsnp3 sizes its worker pools at int(cpu_count()/1.2) — 106 on the
# 128-core shared box — which hammers everyone else on the machine. This started
# life inside the v3.16-only .patch set, but the expression is character-for-
# character identical in v3.16 and v3.35, and the .patch set does not apply to
# v3.35 at all. Promoted to a content fix so the cap survives the move to a
# newer vsnp3 instead of silently disappearing with it. Override with
# VSNP3_MAX_CPUS; never bites on a laptop (8/1.2 = 6 < 32).
for CPU_FILE in vsnp3_step2.py vsnp3_group_on_defining_snps.py vsnp3_fasta_to_snps_table.py; do
  TARGET="${PREFIX}/bin/${CPU_FILE}"
  # The capped expression still CONTAINS the uncapped one as a substring, so the
  # VSNP3_MAX_CPUS check is what makes this idempotent — without it a re-run
  # nests min(min(x, c), c) one level deeper each time. Installs that got the cap
  # from the old .patch already carry the marker and are skipped here.
  if [ -f "${TARGET}" ] \
     && grep -qF "int(multiprocessing.cpu_count() / 1.2)" "${TARGET}" \
     && ! grep -qF "VSNP3_MAX_CPUS" "${TARGET}"; then
    if ! grep -q "^import os" "${TARGET}"; then
      echo "warning: ${CPU_FILE} has no 'import os'; skipping CPU cap there" >&2
      continue
    fi
    sed -i \
      "s|int(multiprocessing\.cpu_count() / 1\.2)|max(1, min(int(multiprocessing.cpu_count() / 1.2), int(os.environ.get('VSNP3_MAX_CPUS') or 32)))|g" \
      "${TARGET}"
    echo "applied CPU cap to ${TARGET}"
  fi
done

# Minus-strand amino acid calls: vsnp3_annotation.py translates the plus-strand
# codon as-is, so every minus-strand ref/alt AA is wrong and the silent vs
# nonsynonymous call is close to a coin flip (~half of MTBC genes are minus
# strand). Reported by Vivek Kapur 2026-08-01. v3.16 and v3.35 restructured this
# function completely, so this is a small content-anchored rewriter rather than
# a sed one-liner or a line-context .patch — see strandfix.py. Idempotent, and
# it refuses rather than guesses on an unrecognised release.
ANNOT_PY="${PREFIX}/bin/vsnp3_annotation.py"
if [ -f "${ANNOT_PY}" ]; then
  PYBIN="${PREFIX}/bin/python3"
  [ -x "${PYBIN}" ] || PYBIN="${PREFIX}/bin/python"
  [ -x "${PYBIN}" ] || PYBIN="$(command -v python3)"
  "${PYBIN}" "${SCRIPT_DIR}/strandfix.py" "${ANNOT_PY}"
fi

# The .patch set below is unified-diff against v3.16 and does not apply to any
# other release — v3.35 restructured all four target files, so every hunk fails.
# We deploy both (/srv carries v3.16, the bdtools checkout env carries v3.35),
# so bail out cleanly here instead of dumping a wall of FAILED hunks and .rej
# files. The content fixes above are version-agnostic and have already run.
# Which release is this? conda-meta FIRST: it is the package manager's own
# record, it is right for every release, and it does not move. Reading the
# source was fragile and broke exactly as you would expect — 3.36 moved the
# string into vsnp3_version.py ("from vsnp3_version import __version__"), so
# the old regex found nothing.
VSNP3_VER="$(ls "${PREFIX}"/conda-meta/vsnp3-*.json 2>/dev/null | head -1 \
  | sed 's|.*/vsnp3-||; s|-[^-]*\.json$||')"
if [ -z "${VSNP3_VER}" ]; then
  for VER_SRC in vsnp3_version.py vsnp3_step1.py; do
    [ -f "${PREFIX}/bin/${VER_SRC}" ] || continue
    VSNP3_VER="$(sed -n 's/^__version__[[:space:]]*=[[:space:]]*["'"'"']\([^"'"'"']*\)["'"'"'].*/\1/p' \
      "${PREFIX}/bin/${VER_SRC}" 2>/dev/null | head -1)"
    [ -n "${VSNP3_VER}" ] && break
  done
fi
case "${VSNP3_VER}" in
  3.16*) ;;
  "")
    # Unknown version: do NOT try the v3.16 diffs regardless. That fallback
    # turned an unreadable version into six FAILED hunks and a litter of
    # .orig/.rej files in a perfectly good 3.36 install. The content fixes
    # above are version-agnostic and have already run.
    echo "warning: cannot determine the vsnp3 version at ${PREFIX};" >&2
    echo "         content fixes applied; skipping the v3.16-only .patch set." >&2
    exit 0
    ;;
  *)
    echo "vsnp3 ${VSNP3_VER} at ${PREFIX}: content fixes applied; skipping the v3.16-only .patch set"
    exit 0
    ;;
esac

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
