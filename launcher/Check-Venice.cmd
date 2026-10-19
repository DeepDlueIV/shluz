@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto run_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto run_py
echo Для этой проверки нужен Python 3.12 или новее.
pause
exit /b 2

:run_python
python "%~dp0venice_check.py" --chat
goto finished

:run_py
py -3 "%~dp0venice_check.py" --chat
goto finished

:finished
set "result=%errorlevel%"
pause
exit /b %result%
