#!/usr/bin/env bash
# 一键启动网页管理界面（Linux / macOS）。
# 用法：./start-webui.sh [端口]   （默认 8848）
#
# 依赖（含 Python 本身与 PyYAML）均可随发布包内置，无需联网、无需安装：
#   · 便携包：内置解释器位于 runtime/python/bin/python3，优先使用；
#   · 纯源码包：回退到系统 python3（要求 3.9+，PyYAML 已在 vendor/ 内置）。
set -e
cd "$(dirname "$0")"

PY=""
if [ -x "runtime/python/bin/python3" ]; then
  PY="runtime/python/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
elif command -v python >/dev/null 2>&1; then
  PY="python"
else
  echo "[错误] 未找到 Python。"
  echo "       便携包请确认已完整解压（不要只拖出启动脚本）；"
  echo "       纯源码包请安装 Python 3.9+：https://www.python.org/"
  exit 1
fi

# 统一使用 UTF-8，避免精简运行时在非 UTF-8 locale 下中文输出报错。
export PYTHONUTF8=1

if ! "$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"; then
  echo "[错误] 需要 Python 3.9 或更高版本，当前：$("$PY" --version 2>&1)"
  exit 1
fi

PORT="${1:-8848}"
echo "启动后用浏览器打开： http://<本机IP>:$PORT"
exec "$PY" tools/webui.py --port "$PORT"
