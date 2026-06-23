#!/usr/bin/env bash
# Inject Vercel env vars into frontend/config.js at build time.
# Set TWIQ_API_BASE_URL and TWIQ_API_KEY in Vercel project settings.

cat > frontend/config.js <<EOF
window.TWIQ_API_BASE_URL = "${TWIQ_API_BASE_URL:-http://127.0.0.1:8000}";
window.TWIQ_API_KEY = "${TWIQ_API_KEY:-}";
EOF
