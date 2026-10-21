#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_BUNDLE="$ROOT_DIR/macos-app/build/MiMo Workflow Dev.app"

cd "$ROOT_DIR"

if [ ! -d "$APP_BUNDLE" ]; then
  echo "未找到开发版 App，正在构建..."
  ./macos-app/build_dev_app.sh
fi

open -n "$APP_BUNDLE"
