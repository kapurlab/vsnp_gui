#!/bin/bash
set -euo pipefail

ROOT_DIR="$(dirname "$0")"

"$ROOT_DIR/start_backend.sh" &
BACK_PID=$!

"$ROOT_DIR/start_frontend.sh" &
FRONT_PID=$!

sleep 4
open "http://localhost:5173" || true

trap 'kill $BACK_PID $FRONT_PID' INT TERM
wait
