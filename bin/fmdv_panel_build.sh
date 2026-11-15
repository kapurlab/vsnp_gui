#!/bin/bash
# Step 2 context database: call every OTHER library genome against one lineage reference with vsnp3 step1 (FASTA mode),
# so Step 2 can place samples among the 79 lineage genomes. Usage: fmdv_panel_build.sh <ref_safe>
#
# Paths come from the environment, not from this file. Put vsnp3 on PATH
# before running (the same environment the GUI backend is started with), or
# set VSNP3_BIN. The library defaults to ~/fmdv_refs and FMDV_LIB moves it.
set -u
if ! command -v vsnp3_step1.py > /dev/null; then
  if [ -n "${VSNP3_BIN:-}" ]; then export PATH="$VSNP3_BIN:$PATH"; fi
fi
command -v vsnp3_step1.py > /dev/null || { echo "vsnp3_step1.py is not on PATH; set VSNP3_BIN or activate the vsnp3 environment" >&2; exit 1; }
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REF=$1; LIB=${FMDV_LIB:-$HOME/fmdv_refs}; OUT=$LIB/panel_build/$REF; mkdir -p "$OUT"; cd "$OUT"
REFDIR=$(python3 "$HERE/fmdv_reflib.py" materialize --lib "$LIB" --genome "$REF")
one () {
  g=$(basename "$1" .fasta); [ "$g" = "$REF" ] && return 0
  [ -n "$(ls "$OUT/$g"/alignment_*/*_zc.vcf* 2>/dev/null)" ] && return 0
  mkdir -p "$OUT/$g" && cd "$OUT/$g" && vsnp3_step1.py -f "$1" -t "$REFDIR" -n "$g" > step1.out 2>&1
  echo "$g exit=$?"
}
export -f one; export OUT REF REFDIR
ls $LIB/genomes/*.fasta | xargs -P ${FMDV_THREADS:-8} -I{} bash -c 'one {}'
echo DONE > "$OUT/BUILD_DONE"
