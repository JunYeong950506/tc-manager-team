@echo off
chcp 65001 >nul
setlocal
title Zephyr API Token Setup
pushd "%~dp0"
powershell.exe -NoProfile -File "%~dp0scripts\connect_zephyr.ps1" %*
set "tcConnectResult=%errorlevel%"
echo.
pause
popd
exit /b %tcConnectResult%
