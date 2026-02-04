#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_PATH="${ROOT_DIR}/backend/data/config.json"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "Config not found at ${CONFIG_PATH}. Run the GUI once and save settings."
  exit 1
fi

read_config() {
  python3 - <<'PY' "$CONFIG_PATH"
import json, sys
cfg = json.load(open(sys.argv[1]))
print(cfg.get("vsnp3_path",""))
print(cfg.get("projects_root",""))
print(cfg.get("conda_env",""))
print(cfg.get("conda_exe",""))
print(cfg.get("conda_env_path",""))
PY
}

mapfile -t CFG < <(read_config)
VSNP3_PATH="${CFG[0]}"
PROJECTS_ROOT="${CFG[1]}"
CONDA_ENV="${CFG[2]:-vsnp3}"
CONDA_EXE="${CFG[3]:-conda}"
CONDA_ENV_PATH="${CFG[4]}"

if [ -n "$CONDA_ENV_PATH" ]; then
  CONDA_BIN="${CONDA_ENV_PATH}/bin/conda"
else
  CONDA_BIN="$CONDA_EXE"
fi

if ! command -v "$CONDA_BIN" >/dev/null 2>&1; then
  echo "Conda not found: $CONDA_BIN"
  exit 1
fi

if ! "$CONDA_BIN" env list | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
  echo "Creating conda env: $CONDA_ENV"
  "$CONDA_BIN" create -y -n "$CONDA_ENV" python=3.9
fi

echo "Installing vSNP3 dependencies into $CONDA_ENV"
"$CONDA_BIN" install -y -n "$CONDA_ENV" -c conda-forge \
  biopython minimap2 cairosvg dask freebayes humanize numpy openpyxl pandas \
  parallel pigz regex samtools=1.14 seqkit sourmash spades svgwrite pyvcf \
  py-cpuinfo scikit-allel vcflib

if [ -n "$PROJECTS_ROOT" ]; then
  mkdir -p "$PROJECTS_ROOT"
fi

if [ -n "$VSNP3_PATH" ] && [ ! -d "$VSNP3_PATH" ]; then
  echo "Warning: vSNP3 path does not exist: $VSNP3_PATH"
fi

echo "Bootstrap complete."
