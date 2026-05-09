#!/bin/bash
set -euo pipefail

# Load nvm
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && source "$NVM_DIR/nvm.sh"

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Allow overriding backend port and API URL to avoid conflicts.
BACKEND_PORT="${BACKEND_PORT:-8000}"
API_URL="${VITE_API_URL:-http://localhost:${BACKEND_PORT}}"
FRONTEND_PORT="${FRONTEND_PORT:-}"

# Pick a free frontend port if not provided.
if [ -z "$FRONTEND_PORT" ]; then
  for p in {5173..5195}; do
    if ! lsof -i :"$p" >/dev/null 2>&1; then
      FRONTEND_PORT="$p"
      break
    fi
  done
fi
if [ -z "$FRONTEND_PORT" ]; then
  echo "No free frontend port found in range 5173-5195." >&2
  exit 1
fi

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
echo "  Backend port:    $BACKEND_PORT"
echo "  API base URL:    $API_URL"
echo "  Frontend port:   $FRONTEND_PORT"
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

cleanup() {
  for pid in "${BACK_PID:-}" "${FRONT_PID:-}"; do
    if [ -n "${pid}" ] && kill -0 "${pid}" >/dev/null 2>&1; then
      # Kill the whole process group to stop reload/child processes.
      kill -- "-${pid}" >/dev/null 2>&1 || true
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
  uvicorn app.main:app --reload --port "${BACKEND_PORT}"
) &
BACK_PID=$!

# Frontend
(
  cd "$ROOT_DIR/frontend"
  if [ ! -d node_modules ]; then
    npm install
  fi
  VITE_API_URL="${API_URL}" npm run dev -- --port "${FRONTEND_PORT}" --strictPort
) &
FRONT_PID=$!

# Wait for Vite to be ready and detect port
echo "Waiting for Vite dev server..."
VITE_PORT=""
for i in {1..40}; do
  if curl -s "http://localhost:${FRONTEND_PORT}" >/dev/null 2>&1; then
    VITE_PORT="${FRONTEND_PORT}"
    echo "Vite is ready on port ${VITE_PORT}."
    break
  fi
  sleep 0.5
done
if [ -z "${VITE_PORT}" ]; then
  echo "Vite did not become ready on port ${FRONTEND_PORT}." >&2
  exit 1
fi

# Electron
(
  cd "$ROOT_DIR/electron"
  if [ ! -d node_modules ]; then
    npm install
  fi
  VITE_DEV_SERVER_URL="http://localhost:${VITE_PORT}" VITE_API_URL="${API_URL}" npm run dev
)
