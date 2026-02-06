#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Auto-detect conda base
CONDA_BASE=""
if [ -n "${CONDA_EXE:-}" ]; then
  CONDA_BASE="$(dirname "$(dirname "$CONDA_EXE")")"
elif command -v conda >/dev/null 2>&1; then
  CONDA_BASE="$(conda info --base 2>/dev/null || true)"
fi

if [ -n "$CONDA_BASE" ] && [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
  source "$CONDA_BASE/etc/profile.d/conda.sh"
  export PATH="$CONDA_BASE/bin:$PATH"
fi

# Print detected paths for easy copy/paste into Settings
echo "================================================"
echo "  vSNP GUI — Detected Paths"
echo "================================================"
echo "  GUI root:        $ROOT_DIR"
if [ -n "$CONDA_BASE" ]; then
  VSNP_ENV="$CONDA_BASE/envs/vsnp3"
  if [ -d "$VSNP_ENV" ]; then
    echo "  vSNP3 path:      $VSNP_ENV"
  else
    echo "  vSNP3 path:      (vsnp3 env not found — set in Settings)"
  fi
else
  echo "  vSNP3 path:      (conda not detected — open a conda-enabled shell)"
fi
echo "================================================"
echo ""

if ! command -v conda >/dev/null 2>&1; then
  echo "Conda not found in PATH. Please open a conda-enabled shell." >&2
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm not found. Install Node.js (brew install node)." >&2
fi

# Backend
(
  cd "$ROOT_DIR/backend"
  if [ ! -d .venv ]; then
    python3 -m venv .venv
  fi
  source .venv/bin/activate
  pip install -r requirements.txt
  uvicorn app.main:app --reload --port 8000
) &
BACK_PID=$!

# Frontend
(
  cd "$ROOT_DIR/frontend"
  if [ ! -d node_modules ]; then
    npm install
  fi
  npm run dev
) &
FRONT_PID=$!

sleep 4
open "http://localhost:5173" || true

echo "Backend PID: $BACK_PID"
echo "Frontend PID: $FRONT_PID"

echo "If the GUI reports missing deps, run in your conda env:"
echo "  conda install -n <env> pandas biopython pysam"

trap 'kill $BACK_PID $FRONT_PID' INT TERM
wait
