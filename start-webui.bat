@echo off
chcp 65001 >nul
rem ============================================================
rem  Start the ClassIsland Server web UI on Windows.
rem  Usage: double-click, or  start-webui.bat [port]   (default 8848)
rem
rem  All runtime deps (Python + PyYAML) ship inside the portable
rem  package, so there is nothing to install and no network needed.
rem  Notes for maintainers:
rem   * Keep this file ASCII-only for comments and control flow.
rem     A UTF-8 .bat misread as GBK (cp936) corrupts parenthesized
rem     blocks and breaks startup on Chinese Windows. User-facing
rem     Chinese text is allowed ONLY on standalone echo lines AFTER
rem     the "chcp 65001" above (never inside if(...) blocks).
rem ============================================================
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
if not defined PY goto :nopy

rem UTF-8 for the bundled/runtime Python so Chinese console output
rem never crashes under a GBK console.
set "PYTHONUTF8=1"

"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)"
if errorlevel 1 goto :badver

set "PORT=8848"
if not "%~1"=="" set "PORT=%~1"
echo 启动后请用浏览器打开： http://localhost:%PORT%
rem Bind loopback only: this is a local admin tool. Binding 0.0.0.0
rem pops the Windows Firewall dialog on every start and exposes an
rem unauthenticated page to the LAN. Override with --host 0.0.0.0.
"%PY%" tools\webui.py --host 127.0.0.1 --port %PORT%
pause
exit /b 0

:nopy
echo [错误] 未找到 Python。
echo        便携包请确认已完整解压（不要只拖出启动脚本）；
echo        纯源码包请到 https://www.python.org 安装 Python 3.9+，
echo        安装时勾选 "Add Python to PATH"。
pause
exit /b 1

:badver
echo [错误] 需要 Python 3.9 或更高版本。
pause
exit /b 1
