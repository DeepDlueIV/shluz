@echo off
chcp 65001 >nul
setlocal
title NoClip - Website

where node >nul 2>nul
if errorlevel 1 (
  echo Не найден Node.js. Установите Node.js версии 24 или новее с сайта https://nodejs.org/
  echo Затем заново откройте START-WEBSITE.cmd.
  pause
  exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
  echo Не найден npm. Переустановите Node.js 24+ с включённым компонентом npm.
  pause
  exit /b 1
)

node "%~dp0web\scripts\start-website.mjs"
set "website_exit=%ERRORLEVEL%"
if not "%website_exit%"=="0" (
  echo Сообщения об ошибке находятся выше. После исправления запустите этот файл снова.
  pause
)
exit /b %website_exit%
