@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto python_ok
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto py_ok
echo NoClip requires Python 3.12+ on this Windows PC.
pause
exit /b 2
:python_ok
python "%~dp0bootstrap.py"
goto finished
:py_ok
py -3 "%~dp0bootstrap.py"
:finished
set "code=%errorlevel%"
if not "%code%"=="0" pause
exit /b %code%
