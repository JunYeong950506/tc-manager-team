@echo off
chcp 65001 >nul
setlocal
title TC Manager Update
pushd "%~dp0"
powershell.exe -NoProfile -File "%~dp0scripts\update_tc_manager.ps1" %*
set "tcUpdateResult=%errorlevel%"
echo.
pause
popd
exit /b %tcUpdateResult%
