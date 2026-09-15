#!/usr/bin/env bash
# 一键启动网页管理界面（Linux / macOS）。
# 用法：./start-webui.sh [端口]   （默认 8848）
set -e
cd "$(dirname "$0")"

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then PY=python; fi

# 首次运行：若缺 PyYAML，给出清晰提示而不是一坨 ImportError
if ! "$PY" -c "import yaml" >/dev/null 2>&1; then
  echo "首次运行：正在安装唯一依赖 PyYAML …"
  "$PY" -m pip install -r requirements.txt
fi

PORT="${1:-8848}"
echo "启动后用浏览器打开： http://<本机IP>:$PORT"
exec "$PY" tools/webui.py --port "$PORT"
