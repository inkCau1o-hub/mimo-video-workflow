#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_BUNDLE="$SCRIPT_DIR/build/MiMo Workflow Dev.app"
BACKEND_PORT="${BACKEND_PORT:-8765}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

if [[ ! -d "$APP_BUNDLE" ]]; then
  "$SCRIPT_DIR/build_dev_app.sh"
fi

osascript -e 'tell application "MiMo Workflow Dev" to quit' 2>/dev/null || true
pkill -x MiMoWorkflowDev 2>/dev/null || true
sleep 1

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  lsof -t -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | xargs -r kill || true
done

open -n "$APP_BUNDLE"

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null \
    && curl -fsSI "http://127.0.0.1:${FRONTEND_PORT}/" >/dev/null; then
    echo "dev app smoke: services ready"
    exit 0
  fi
  sleep 1
done

echo "dev app smoke: services did not become ready" >&2
echo "--- backend log ---" >&2
tail -80 "$SCRIPT_DIR/build/logs/backend.log" >&2 || true
echo "--- frontend log ---" >&2
tail -80 "$SCRIPT_DIR/build/logs/frontend.log" >&2 || true
exit 1
