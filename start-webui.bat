@echo off
rem 一键启动网页管理界面（Windows）。
rem 用法：双击本文件，或 start-webui.bat [端口]   （默认 8848）
setlocal
cd /d "%~dp0"

set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
  set "PY=py -3"
  %PY% --version >nul 2>&1
  if errorlevel 1 (
    echo [错误] 未找到 Python。请先到 https://www.python.org 安装 Python 3.9 或更高版本，
    echo        安装时勾选 "Add Python to PATH"。
    pause
    exit /b 1
  )
)

%PY% -c "import yaml" >nul 2>&1
if errorlevel 1 (
  echo 首次运行：正在安装唯一依赖 PyYAML ...
  %PY% -m pip install -r requirements.txt || (
    echo [错误] 依赖安装失败，请检查网络 / pip。
    pause
    exit /b 1
  )
)

set "PORT=8848"
if not "%~1"=="" set "PORT=%~1"
echo 启动后用浏览器打开： http://localhost:%PORT%
%PY% tools\webui.py --port %PORT%
pause
