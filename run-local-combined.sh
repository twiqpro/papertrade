#!/usr/bin/env bash
# Production-like local stack: paper API + options backtester on one port.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Starting combined API at http://127.0.0.1:8000"
echo "  Paper health:        http://127.0.0.1:8000/health"
echo "  Options backtester:  http://127.0.0.1:8000/options-backtest/"
echo "Starting static frontend at http://127.0.0.1:4174"

export ROOT_PATH=/options-backtest
export PYTHONPATH="${ROOT_DIR}/backend:${ROOT_DIR}/backtester"

cd "${ROOT_DIR}/backend"
python3 -m uvicorn app.combined:root --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

cd "${ROOT_DIR}/frontend"
python3 -m http.server 4174 &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait
