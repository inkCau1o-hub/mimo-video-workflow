#!/bin/zsh
set -e

SCRIPT_DIR="${0:A:h}"
cd "$SCRIPT_DIR"

if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python3.11}"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="${PYTHON_BIN:-python3.12}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

if [ ! -d ".venv" ]; then
  echo "正在创建 Python 虚拟环境..."
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate

echo "正在检查依赖..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo "正在启动程序..."
python main.py --gui
