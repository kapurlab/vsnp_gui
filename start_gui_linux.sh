#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Detect conda/miniforge env ---
# Look for miniforge3 or miniconda3 in the user's home
CONDA_PREFIX=""
for candidate in "$HOME/miniforge3" "$HOME/miniconda3"; do
  if [ -d "$candidate/envs/vsnp3" ]; then
    CONDA_PREFIX="$candidate/envs/vsnp3"
    break
  fi
done

if [ -z "$CONDA_PREFIX" ]; then
  echo "ERROR: Cannot find vsnp3 conda env in ~/miniforge3 or ~/miniconda3" >&2
  exit 1
fi

PYTHON="$CONDA_PREFIX/bin/python"
echo "Using vsnp3 env: $CONDA_PREFIX"

if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not found. Install Node.js (e.g., sudo apt install nodejs)." >&2
  exit 1
fi

# --- Backend (uses conda env python, not a venv) ---
(
  cd "$ROOT_DIR/backend"
  "$PYTHON" -m pip install -q -r requirements.txt 2>/dev/null
  "$PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
) &
BACK_PID=$!

# --- Frontend ---
(
  cd "$ROOT_DIR/frontend"
  if [ ! -d node_modules ]; then
    npm install
  fi
  npm run dev -- --host
) &
FRONT_PID=$!

sleep 4

echo ""
echo "============================================"
echo "  vSNP GUI is running!"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo ""
echo "  For remote access via SSH tunnel:"
echo "    ssh -L 5173:localhost:5173 -L 8000:localhost:8000 <this-host>"
echo "    Then open http://localhost:5173 in your browser"
echo ""
echo "  Press Ctrl+C to stop"
echo "============================================"
echo ""

trap 'echo "Shutting down..."; kill $BACK_PID $FRONT_PID 2>/dev/null; wait' INT TERM
wait
