@echo off
chcp 65001 >nul
rem ============================================================
rem  Stop the background ClassIsland Server web UI on Windows.
rem
rem  The background launcher (start-webui.bat / webui_bg.py)
rem  writes its pid to logs\webui.pid. This script ends it.
rem
rem  Maintainers: comments and control flow stay ASCII-only;
rem  Chinese is allowed only on standalone echo lines below.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PIDFILE=logs\webui.pid"
if not exist "%PIDFILE%" goto :nopid

set "PID="
set /p PID=<"%PIDFILE%"
if "%PID%"=="" goto :badpid

tasklist /FI "PID eq %PID%" /NH 2>nul | find " %PID% " >nul
if errorlevel 1 goto :stale

taskkill /PID %PID% /T /F >nul 2>&1
if errorlevel 1 goto :killfail
del /q "%PIDFILE%" >nul 2>&1
echo 已停止后台服务（进程 %PID%）。
timeout /t 2 >nul
exit /b 0

:stale
rem pid file points at a process that no longer exists
del /q "%PIDFILE%" >nul 2>&1
echo 后台服务并未在运行（残留的 pid 文件已清理）。
timeout /t 2 >nul
exit /b 0

:killfail
echo [错误] 无法结束进程 %PID%，可能需要管理员权限，
echo        或在任务管理器中结束 pythonw.exe。
pause
exit /b 1

:nopid
echo 没有找到后台服务的 pid 文件（logs\webui.pid）。
echo 如果服务是以调试模式或其它方式启动的，请在任务管理器中
echo 结束对应的 python.exe / pythonw.exe 进程。
pause
exit /b 1

:badpid
echo [错误] pid 文件内容异常：%PIDFILE%
pause
exit /b 1
