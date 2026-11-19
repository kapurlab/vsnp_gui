#!/usr/bin/env bash
# fmdv_library_build.sh — build the FMDV reference library, in one command.
#
#   fmdv_library_build.sh [--lib DIR] [--nanofmdv DIR] [--threads N]
#                         [--offline] [--force]
#
# FMDV mode reads a reference library: the 79 NanoFMDV lineage genomes as a
# hierarchy, their VP1 panel, the per-serotype GenBank VP1 sets the typing and
# the trees use, the primer scheme, and each genome's GenBank annotation. None
# of it is in this repository (NanoFMDV carries no licence file, so its data is
# not redistributed here); this script fetches NanoFMDV from its own repository
# and runs the four build steps docs/FMDV_MODE_HANDOFF.md lists under "How to
# run it", so a site runs one thing.
#
#   --lib DIR        where the library is written. Default: $FMDV_LIB, else
#                    ~/fmdv_refs. A site keeps one copy at <site>/refs/fmdv,
#                    which the GUI finds on its own (backend/app/config.py).
#   --nanofmdv DIR   an existing NanoFMDV checkout. Default: clone
#                    github.com/rjorton/nanofmdv into <lib>/.source/nanofmdv.
#   --threads N      blastn threads for the VP1 slicing (default 8).
#   --offline        skip the one step that needs NCBI (the GenBank and GFF
#                    annotations: Step 2's annotation row and IGV's gene track).
#                    Run it again without --offline when the machine is online.
#   --force          rebuild steps whose output is already there.
#
# Needs blastn, makeblastdb and sourmash on PATH. A checkout that carries its
# own env (bdtools: <checkout>/env) is used without being activated; anywhere
# else, run this in the environment the GUI backend runs in.
#
# Every step is skipped when its output exists, so a run that stopped half way
# is finished by running the same command again.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NANOFMDV_REPO="https://github.com/rjorton/nanofmdv.git"

LIB="${FMDV_LIB:-${HOME}/fmdv_refs}"
SRC=""
THREADS=8
OFFLINE=0
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --lib)       LIB="${2:?--lib needs a folder}"; shift 2;;
    --lib=*)     LIB="${1#--lib=}"; shift;;
    --nanofmdv)  SRC="${2:?--nanofmdv needs a folder}"; shift 2;;
    --nanofmdv=*) SRC="${1#--nanofmdv=}"; shift;;
    --threads)   THREADS="${2:?--threads needs a number}"; shift 2;;
    --threads=*) THREADS="${1#--threads=}"; shift;;
    --offline)   OFFLINE=1; shift;;
    --force)     FORCE=1; shift;;
    -h|--help)   sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "fmdv_library_build: unknown option: $1" >&2; exit 2;;
  esac
done

say()  { printf '[fmdv library] %s\n' "$*"; }
fail() { printf '[fmdv library] %s\n' "$*" >&2; exit 1; }

# The env this checkout runs from, when it has one of its own.
if [[ -x "${HERE}/../env/bin/python" ]]; then
  export PATH="${HERE}/../env/bin:${PATH}"
fi
PY="${FMDV_PYTHON:-}"
if [[ -z "${PY}" ]]; then
  PY="$(command -v python3 || command -v python || true)"
fi
[[ -n "${PY}" ]] || fail "no python on PATH"
missing=()
for tool in blastn makeblastdb sourmash; do
  command -v "${tool}" > /dev/null 2>&1 || missing+=("${tool}")
done
if [[ ${#missing[@]} -gt 0 ]]; then
  fail "not on PATH: ${missing[*]}. Run this in the environment the GUI backend runs in (bdtools: bin/bdtools install vsnp_gui adds them)."
fi

mkdir -p "${LIB}"
LIB="$(cd "${LIB}" && pwd)"
say "library: ${LIB}"

# ---- NanoFMDV: the genomes, the VP1 panel, the GenBank collection, the primers
if [[ -z "${SRC}" ]]; then
  SRC="${LIB}/.source/nanofmdv"
  if [[ ! -d "${SRC}/.git" ]]; then
    command -v git > /dev/null 2>&1 || fail "git is not on PATH, and no --nanofmdv checkout was given"
    say "fetching NanoFMDV (${NANOFMDV_REPO})"
    mkdir -p "${LIB}/.source"
    git clone --quiet --depth 1 "${NANOFMDV_REPO}" "${SRC}" \
      || fail "could not clone ${NANOFMDV_REPO}. Offline? Copy a NanoFMDV checkout here and pass --nanofmdv DIR."
  fi
fi
[[ -d "${SRC}" ]] || fail "no NanoFMDV checkout at ${SRC}"
REFS=""
for cand in "${SRC}/NANOFMDV-PIPELINE/Refs" "${SRC}/pipeline_exec/Refs" "${SRC}/Refs" "${SRC}"; do
  if [[ -f "${cand}/fmdv.fasta" && -f "${cand}/fmdv_vp1.fasta" ]]; then REFS="${cand}"; break; fi
done
[[ -n "${REFS}" ]] || fail "no Refs/fmdv.fasta and Refs/fmdv_vp1.fasta under ${SRC}: not a NanoFMDV checkout?"
GBBASE="${REFS}/BLAST/fmdv-gbbase.fasta"
[[ -f "${GBBASE}" ]] || fail "missing ${GBBASE}"
PRIMERS=""
for cand in "$(dirname "${REFS}")/primers.fasta" "${REFS}/primers.fasta" "${SRC}/primers.fasta"; do
  if [[ -f "${cand}" ]]; then PRIMERS="${cand}"; break; fi
done

# ---- 1. the genomes, the hierarchy, the VP1 panel -----------------------------
if [[ ${FORCE} -eq 1 || ! -f "${LIB}/lineages.json" || ! -d "${LIB}/panels" ]]; then
  say "1/4 lineage genomes and VP1 panel"
  "${PY}" "${HERE}/fmdv_reflib.py" build --refs "${REFS}/fmdv.fasta" --lib "${LIB}" --vp1 "${REFS}/fmdv_vp1.fasta"
else
  say "1/4 lineage genomes and VP1 panel: present"
fi

# ---- 2. the primer scheme ------------------------------------------------------
if [[ -n "${PRIMERS}" ]]; then
  if [[ ${FORCE} -eq 1 || ! -f "${LIB}/primers/nanofmdv_L.fasta" ]]; then
    say "2/4 primer scheme"
    mkdir -p "${LIB}/primers"
    cp "${PRIMERS}" "${LIB}/primers/nanofmdv_L.fasta"
  else
    say "2/4 primer scheme: present"
  fi
else
  say "2/4 primer scheme: no primers.fasta in the NanoFMDV checkout; primer clipping stays off"
fi

# ---- 3. the per-serotype GenBank VP1 sets --------------------------------------
if [[ ${FORCE} -eq 1 || ! -f "${LIB}/vp1_context/SOURCE.json" ]]; then
  say "3/4 GenBank VP1 context sets (a few minutes)"
  "${PY}" "${HERE}/fmdv_vp1_context.py" --source "${GBBASE}" --lib "${LIB}" --threads "${THREADS}"
else
  say "3/4 GenBank VP1 context sets: present"
fi

# ---- 4. annotations, the one step that needs NCBI ------------------------------
ANNOT="done"
n_genomes="$(find "${LIB}/genomes" -maxdepth 1 -name '*.fasta' | wc -l | tr -d ' ')"
n_annot="$(find "${LIB}/annotations" -maxdepth 1 -name '*.gbk' 2> /dev/null | wc -l | tr -d ' ')"
if [[ ${FORCE} -eq 0 && "${n_annot}" -ge "${n_genomes}" && "${n_genomes}" -gt 0 ]]; then
  say "4/4 GenBank annotations: present"
elif [[ ${OFFLINE} -eq 1 ]]; then
  say "4/4 GenBank annotations: skipped (--offline). Step 2 tables have no annotation row and IGV no gene track until this runs online."
  ANNOT="skipped"
else
  say "4/4 GenBank annotations (NCBI, about two minutes)"
  if ! "${PY}" "${HERE}/fmdv_reflib.py" annotations --lib "${LIB}"; then
    say "the annotations step failed. The library is usable without it; run this command again when NCBI is reachable."
    ANNOT="failed"
  fi
fi

# ---- every reference folder, made now ------------------------------------------
# The GUI makes a lineage's vsnp3 reference folder the first time a sample
# lands on it. On a site library the people running samples cannot write, that
# first time fails, so all of them are made here by the account that can.
say "reference folders for every lineage genome"
made=0
while IFS= read -r fasta; do
  safe="$(basename "${fasta}" .fasta)"
  "${PY}" "${HERE}/fmdv_reflib.py" materialize --lib "${LIB}" --genome "${safe}" > /dev/null
  made=$((made + 1))
done < <(find "${LIB}/genomes" -maxdepth 1 -name '*.fasta' | sort)

say "ready: ${n_genomes} lineage genomes, ${made} reference folders, annotations ${ANNOT}"
say "the GUI reads it from <site>/refs/fmdv, from ~/fmdv_refs, or from \"fmdv_reflib\" in its config.json"
