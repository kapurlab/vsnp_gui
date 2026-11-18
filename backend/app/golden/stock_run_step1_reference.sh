#!/bin/bash
set -uo pipefail
FAIL=0
MAX_PARALLEL=2
OVERALL_START=$(date +%s)
OVERALL_LOG="step1_run_summary.log"
echo "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$OVERALL_LOG"
run_sample() {
  local d="$1"
  if [ ! -d "$d" ]; then
    return 0
  fi
  # Skip writer/janitor scaffolding (T-07 _provenance, .git, etc.).
  case "$d" in
    _*/|.*/|_*|.*) return 0 ;;
  esac
  echo "== Running step1 in $d =="
  cd "$d"
  LOG=run_step1.log
  echo "== Running step1 in $d ==" | tee -a "$LOG"
  START_TS=$(date +%s)
  echo "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
  if [ "0" = "0" ]; then
    for dir in alignment_*; do
      if [ -d "$dir" ]; then
        rm -rf "$dir"
      fi
    done
    if [ -d "unmapped_reads" ]; then
      rm -rf "unmapped_reads"
    fi
    if [ -d "sourmash" ]; then
      rm -rf "sourmash"
    fi
  fi
  R1=$(ls *_R1*.fastq.gz 2>/dev/null | head -n1 || true)
  R2=$(ls *_R2*.fastq.gz 2>/dev/null | head -n1 || true)
  if [ -z "$R1" ]; then R1=$(ls *_1*.fastq.gz 2>/dev/null | head -n1 || true); fi
  if [ -z "$R2" ]; then R2=$(ls *_2*.fastq.gz 2>/dev/null | head -n1 || true); fi
  if [ -z "$R1" ]; then R1=$(ls *.fastq.gz 2>/dev/null | head -n1 || true); fi
  if [ -z "$R1" ]; then
    echo "Missing R1 in $d" | tee -a "$LOG"
    cd ..
    return 0
  fi
  NP=""
  if [ -z "$NP" ]; then
    AVG=$(zcat "$R1" 2>/dev/null | head -n 1600 | awk 'NR%4==2{n++;s+=length($0)} END{if(n>0)printf "%d", s/n; else print 0}')
    if [ "${AVG:-0}" -gt 600 ]; then
      NP="--nanopore"
      echo "Auto-detected long reads (avg ${AVG}bp) — using --nanopore" | tee -a "$LOG"
    fi
  fi
  mkdir -p .provenance
  if [ -n "$R2" ]; then echo paired > .provenance/read_type; elif [ -n "$NP" ]; then echo ont > .provenance/read_type; else echo single > .provenance/read_type; fi
  date -u +%s.%N > .provenance/started_at
  RUN_ARGS=(<TMP>/vsnp3/bin/vsnp3_step1.py -r1 "$R1")
  if [ -n "$R2" ]; then RUN_ARGS+=(-r2 "$R2"); fi
  RUN_ARGS+=(-t '<TMP>/ref locs/Mycobacterium_AF2122'   $NP)
  PROV_TS=$(date '+%Y-%m-%d %H:%M:%S %z')
  PROV_SAMPLE=$(basename "$d")
  PROV_CWD=$(pwd)
  PROV_VSNP3=<TMP>/vsnp3/bin/vsnp3_step1.py
  {
    echo "# ================================================================"
    echo "# vsnp_gui - Step 1 (per-sample alignment)"
    echo "# run at:      $PROV_TS"
    echo "# sample:      $PROV_SAMPLE"
    echo "# working dir: $PROV_CWD"
    echo "# tool:        $PROV_VSNP3"
    echo "# ----------------------------------------------------------------"
    echo "# Command executed (copy/paste to reproduce):"
    printf '%q ' "${RUN_ARGS[@]}"; echo ""
    echo ""
  } >> .provenance/vsnp_gui_step1_run_cmd.txt 2>/dev/null || true
  "${RUN_ARGS[@]}" >> "$LOG" 2>&1
  STATUS=$?
  echo $STATUS > .provenance/exit_code
  date -u +%s.%N > .provenance/finished_at
  touch . 2>/dev/null || true
  if [ "$STATUS" -eq 0 ]; then
    END_TS=$(date +%s)
    DURATION=$((END_TS-START_TS))
    echo "Complete: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG"
    echo "Duration: ${DURATION}s" | tee -a "$LOG"
  else
    echo "Error: exit $STATUS" | tee -a "$LOG"
    FAIL=1
  fi
  cd ..
  return $STATUS
}
pids=()
SAMPLES=()
for d in "${SAMPLES[@]}"; do
  while [ "$(jobs -r -p | wc -l | tr -d ' ')" -ge "$MAX_PARALLEL" ]; do
    sleep 1
  done
  run_sample "$d" &
  pids+=("$!")
done
for p in "${pids[@]}"; do
  wait "$p" || FAIL=1
done
OVERALL_END=$(date +%s)
OVERALL_DURATION=$((OVERALL_END-OVERALL_START))
echo "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$OVERALL_LOG"
echo "Duration: ${OVERALL_DURATION}s" >> "$OVERALL_LOG"
exit $FAIL