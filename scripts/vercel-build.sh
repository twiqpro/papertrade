#!/usr/bin/env bash
set -euo pipefail
# Injects Vercel env vars into frontend/config.js at build time.

cat > frontend/config.js <<EOF
window.TWIQ_API_BASE_URL = "${TWIQ_API_BASE_URL:-https://papertrade-absj.onrender.com}";
window.TWIQ_API_KEY = "${TWIQ_API_KEY:-}";
EOF
