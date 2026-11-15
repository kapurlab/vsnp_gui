#!/bin/bash
# Step 2 context database: call every OTHER library genome against one lineage reference with vsnp3 step1 (FASTA mode),
# so Step 2 can place samples among the 79 lineage genomes. Usage: build_panel_vcfs.sh <ref_safe>
set -u
export PATH=/srv/kapurlab/tools/vsnp3/bin:$PATH
REF=$1; LIB=~/fmdv_refs; OUT=$LIB/panel_build/$REF; mkdir -p "$OUT"; cd "$OUT"
REFDIR=$(python $HOME/fmdv_gui/bin/fmdv_reflib.py materialize --lib $LIB --genome $REF)
one () {
  g=$(basename "$1" .fasta); [ "$g" = "$REF" ] && return 0
  [ -n "$(ls "$OUT/$g"/alignment_*/*_zc.vcf* 2>/dev/null)" ] && return 0
  mkdir -p "$OUT/$g" && cd "$OUT/$g" && vsnp3_step1.py -f "$1" -t "$REFDIR" -n "$g" > step1.out 2>&1
  echo "$g exit=$?"
}
export -f one; export OUT REF REFDIR
ls $LIB/genomes/*.fasta | xargs -P 8 -I{} bash -c 'one {}'
echo DONE > "$OUT/BUILD_DONE"
