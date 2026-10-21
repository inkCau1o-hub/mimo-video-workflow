#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "== MiMo Workflow 环境安装 =="
echo "项目目录: $ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装 Python 3.10+。"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "未找到 npm，请先安装 Node.js。"
  exit 1
fi

echo
echo "== Python 环境 =="
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source ".venv/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo
echo "== 前端依赖 =="
cd "$ROOT_DIR/apps/desktop"
npm install

echo
echo "== 构建开发版 macOS App =="
cd "$ROOT_DIR"
if command -v swiftc >/dev/null 2>&1; then
  ./macos-app/build_dev_app.sh
else
  echo "未找到 swiftc，跳过 macOS App 重建。已复制的 macos-app/build/MiMo Workflow Dev.app 仍可尝试打开。"
fi

echo
echo "== 基础验证 =="
python -m compileall backend workflow_state.py production_schema.py tests
python tests/frontend_prototype_smoke.py
python tests/backend_api_smoke.py

echo
echo "安装完成。"
echo "启动方式:"
echo "  双击: $ROOT_DIR/启动开发版App.command"
echo "  或打开: $ROOT_DIR/macos-app/build/MiMo Workflow Dev.app"
