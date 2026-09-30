@echo off
chcp 65001 >nul
setlocal
title TC Manager Setup
pushd "%~dp0"
powershell.exe -NoProfile -File "%~dp0scripts\install_tc_manager.ps1" %*
set "tcInstallResult=%errorlevel%"
echo.
pause
popd
exit /b %tcInstallResult%
