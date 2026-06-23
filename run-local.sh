#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting backend at http://127.0.0.1:8000"
cd "$ROOT_DIR/backend"
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "Starting frontend at http://127.0.0.1:4174"
cd "$ROOT_DIR/frontend"
python3 -m http.server 4174 &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" >/dev/null 2>&1 || true
}

trap cleanup EXIT

echo
echo "Open http://127.0.0.1:4174"
echo "Press Ctrl+C here to stop both services."
wait
