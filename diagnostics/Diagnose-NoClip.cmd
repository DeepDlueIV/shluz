@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto run_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)" >nul 2>nul
if not errorlevel 1 goto run_py
echo Для диагностики требуется установленный Python 3.12+.
pause
exit /b 2
:run_python
python "%~dp0telegram_menu.py"
goto finished
:run_py
py -3 "%~dp0telegram_menu.py"
:finished
set "code=%errorlevel%"
echo.
echo Диагностика закончена. Отправьте файл NoClip-Telegram-Diagnostic.txt.
pause
exit /b %code%
