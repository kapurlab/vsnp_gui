#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONDA_BASE="/Users/vivekkapur/anaconda3"

if [ -f "$CONDA_BASE/etc/profile.d/conda.sh" ]; then
  source "$CONDA_BASE/etc/profile.d/conda.sh"
  export PATH="$CONDA_BASE/bin:$PATH"
fi

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

# Wait for Vite to be ready
echo "Waiting for Vite dev server..."
for i in {1..40}; do
  if curl -s "http://localhost:5173" >/dev/null 2>&1; then
    echo "Vite is ready."
    break
  fi
  sleep 0.5
done

# Electron
(
  cd "$ROOT_DIR/electron"
  if [ ! -d node_modules ]; then
    npm install
  fi
  VITE_DEV_SERVER_URL="http://localhost:5173" npm run dev
)

trap 'kill $BACK_PID $FRONT_PID' INT TERM
kill $BACK_PID $FRONT_PID
