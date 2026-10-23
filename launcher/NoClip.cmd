@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "SHLUZ_HOME=%~dp0"
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto run_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto run_py
echo Требуется Python 3.12 или новее.
pause
exit /b 2

:run_python
python "%~dp0owner_control.py"
goto finished

:run_py
py -3 "%~dp0owner_control.py"
goto finished

:finished
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
