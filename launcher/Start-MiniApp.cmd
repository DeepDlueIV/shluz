@echo off
chcp 65001 >nul
cd /d "%~dp0"
python miniapp_trial.py
if errorlevel 1 pause
