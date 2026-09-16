@echo off
rem 一键启动网页管理界面（Windows）。
rem 用法：双击本文件，或 start-webui.bat [端口]   （默认 8848）
rem
rem 依赖（含 Python 本身与 PyYAML）均可随发布包内置，无需联网、无需安装：
rem   · 便携包：内置解释器位于 runtime\python\python.exe，优先使用；
rem   · 纯源码包：回退到系统 python / py -3（要求 3.9+，PyYAML 已在 vendor\ 内置）。
setlocal
cd /d "%~dp0"

set "PY="
if exist "runtime\python\python.exe" set "PY=runtime\python\python.exe"

if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
  py -3 --version >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  echo [错误] 未找到 Python。
  echo        便携包请确认已完整解压（不要只拖出启动脚本）；
  echo        纯源码包请到 https://www.python.org 安装 Python 3.9+，
  echo        安装时勾选 "Add Python to PATH"。
  pause
  exit /b 1
)

rem 统一使用 UTF-8，避免精简运行时在 GBK 控制台下中文输出报错。
set "PYTHONUTF8=1"

"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)"
if errorlevel 1 (
  echo [错误] 需要 Python 3.9 或更高版本。
  pause
  exit /b 1
)

set "PORT=8848"
if not "%~1"=="" set "PORT=%~1"
echo 启动后用浏览器打开： http://localhost:%PORT%
"%PY%" tools\webui.py --port %PORT%
pause
