@echo off
chcp 65001 >nul
rem ============================================================
rem  Start the ClassIsland Server web UI on Windows (background).
rem
rem  Double-click behavior:
rem    * the server starts with pythonw.exe (no console window),
rem    * the default browser opens automatically,
rem    * this black window closes itself when the launch finishes.
rem
rem  Usage:
rem    start-webui.bat            default port 8848, background
rem    start-webui.bat 9000       custom port
rem    start-webui.bat debug      foreground mode with visible logs
rem
rem  Stop the background server: run stop-webui.bat
rem  Logs: logs\webui.log
rem
rem  Maintainers: keep comments and control flow ASCII-only.
rem  A UTF-8 .bat misread as GBK (cp936) corrupts parenthesized
rem  blocks on Chinese Windows. Put Chinese only on plain echo
rem  lines AFTER "chcp 65001", never inside if(...) blocks.
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

rem UTF-8 for the bundled/runtime Python so Chinese output is safe.
set "PYTHONUTF8=1"

"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)"
if errorlevel 1 goto :badver

set "PORT=8848"
if /i "%~1"=="debug" goto :foreground
if not "%~1"=="" set "PORT=%~1"

rem Background launch: webui_bg.py spawns pythonw.exe (detached,
rem no window), waits until the server answers, then this script
rem exits and the console closes. Bind loopback only by default:
rem this is a local admin tool; 0.0.0.0 would pop the firewall
rem prompt and expose an unauthenticated page to the LAN.
"%PY%" tools\webui_bg.py --host 127.0.0.1 --port %PORT%
if errorlevel 1 goto :launchfail
echo.
echo 本窗口即将自动关闭。停止服务请运行 stop-webui.bat
timeout /t 3 >nul
exit /b 0

:foreground
rem Debug mode: run in this console so logs stay visible; Ctrl-C stops it.
echo [调试模式] 前台运行，日志直接显示在本窗口，Ctrl-C 停止。
"%PY%" tools\webui.py --host 127.0.0.1 --port %PORT%
pause
exit /b 0

:launchfail
echo.
echo [错误] 后台服务启动失败，详见上方提示或日志 logs\webui.log
pause
exit /b 1

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
