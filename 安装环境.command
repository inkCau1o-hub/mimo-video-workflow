#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "== MiMo Workflow 环境安装 =="
echo "项目目录: $ROOT_DIR"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 "$HOME/.local/bin/python3.11"; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON_BIN="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON_BIN" ]; then
  echo "未找到 Python 3.10+，请先安装（例如 brew install python@3.11）。"
  exit 1
fi
echo "使用 Python: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"

if ! command -v npm >/dev/null 2>&1; then
  echo "未找到 npm，请先安装 Node.js。"
  exit 1
fi

echo
echo "== Python 环境 =="
# 从别处复制来的 .venv 路径会失效：不是在当前目录创建的或无法运行时，移到一边重建
if [ -d ".venv" ]; then
  if ! grep -qF "VIRTUAL_ENV=$ROOT_DIR/.venv" .venv/bin/activate 2>/dev/null \
     && ! grep -qF "VIRTUAL_ENV='$ROOT_DIR/.venv'" .venv/bin/activate 2>/dev/null \
     || ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
    BROKEN=".venv_失效_$(date +%Y%m%d_%H%M%S)"
    echo "现有 .venv 不是在当前目录创建的或无法运行，已移到 $BROKEN 并重新创建。"
    mv .venv "$BROKEN"
  fi
fi
if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

source ".venv/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo
echo "== 前端依赖 =="
cd "$ROOT_DIR/apps/desktop"
npm install

echo
echo "== AI 动画（可选）=="
ANIMATION_DIR="${MIMO_AI_ANIMATION_DIR:-}"
# 同级目录已有 AI 动画工作副本时直接用它；否则取出仓库里的子模块（私有仓库，需要有访问权限）。
if [ -z "$ANIMATION_DIR" ] && [ ! -f "$ROOT_DIR/../ai-animation/launcher.json" ] \
   && [ -f "$ROOT_DIR/.gitmodules" ] && command -v git >/dev/null 2>&1; then
  if ! (cd "$ROOT_DIR" && git submodule update --init modules/ai-animation); then
    echo "警告：无法取得 AI 动画子模块（可能没有该私有仓库的权限），跳过。"
  fi
fi
if [ -z "$ANIMATION_DIR" ]; then
  for candidate in "$ROOT_DIR/modules/ai-animation" "$ROOT_DIR/../ai-animation"; do
    if [ -f "$candidate/launcher.json" ]; then ANIMATION_DIR="$candidate"; break; fi
  done
fi
if [ -n "$ANIMATION_DIR" ] && [ -f "$ANIMATION_DIR/package.json" ]; then
  echo "AI 动画目录: $ANIMATION_DIR"
  if command -v pnpm >/dev/null 2>&1; then PNPM=(pnpm); else PNPM=(corepack pnpm); fi
  if (cd "$ANIMATION_DIR" && "${PNPM[@]}" install --frozen-lockfile); then
    echo "AI 动画依赖已安装。"
  else
    echo "警告：AI 动画依赖安装失败，首页的 AI 动画入口会显示为未安装；信息流剪辑不受影响。"
  fi
else
  echo "未找到 AI 动画目录（可设置 MIMO_AI_ANIMATION_DIR），跳过。"
fi

echo
echo "== 构建开发版 macOS App =="
cd "$ROOT_DIR"
if command -v swiftc >/dev/null 2>&1; then
  bash ./macos-app/build_dev_app.sh
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
