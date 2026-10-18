@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
  python "%~dp0updater.py"
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    echo Для запуска нужен Python 3.12+ и Docker Desktop. Попросите локальный Codex подготовить их один раз.
    pause
    exit /b 1
  )
  py -3 "%~dp0updater.py"
)
if errorlevel 1 pause
