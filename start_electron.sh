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

cleanup() {
  for pid in "${BACK_PID:-}" "${FRONT_PID:-}"; do
    if [ -n "${pid}" ] && kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done
}

trap cleanup INT TERM EXIT

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

# Wait for Vite to be ready and detect port
echo "Waiting for Vite dev server..."
VITE_PORT=""
for i in {1..40}; do
  for p in {5173..5195}; do
    if curl -s "http://localhost:${p}" >/dev/null 2>&1; then
      VITE_PORT="${p}"
      break
    fi
  done
  if [ -n "${VITE_PORT}" ]; then
    echo "Vite is ready on port ${VITE_PORT}."
    break
  fi
  sleep 0.5
done
if [ -z "${VITE_PORT}" ]; then
  echo "Vite did not become ready on ports 5173-5195." >&2
  exit 1
fi

# Electron
(
  cd "$ROOT_DIR/electron"
  if [ ! -d node_modules ]; then
    npm install
  fi
  VITE_DEV_SERVER_URL="http://localhost:${VITE_PORT}" npm run dev
)
