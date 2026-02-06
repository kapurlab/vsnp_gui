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
PY
}

mapfile -t CFG < <(read_config)
VSNP3_PATH="${CFG[0]}"
PROJECTS_ROOT="${CFG[1]}"

if [ -z "$VSNP3_PATH" ] || [ ! -d "$VSNP3_PATH" ]; then
  echo "vSNP3 path not set or does not exist: $VSNP3_PATH"
  exit 1
fi

CONDA_BIN="${VSNP3_PATH}/bin/conda"
if ! command -v "$CONDA_BIN" >/dev/null 2>&1; then
  echo "Conda not found at: $CONDA_BIN"
  exit 1
fi

echo "Installing vSNP3 dependencies into $VSNP3_PATH"
"$CONDA_BIN" install -y --prefix "$VSNP3_PATH" -c conda-forge \
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
