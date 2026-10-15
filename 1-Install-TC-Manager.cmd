@echo off
chcp 65001 >nul
setlocal
title TC Manager Setup
pushd "%~dp0"
call "%~dp0scripts\check_tc_startup.cmd" "%~dp0scripts\install_tc_manager.ps1"
if errorlevel 1 goto startup_failed
powershell.exe -NoProfile -File "%~dp0scripts\install_tc_manager.ps1" %*
set "tcInstallResult=%errorlevel%"
goto finish
:startup_failed
set "tcInstallResult=1"
:finish
if not "%tcInstallResult%"=="0" echo [TC_SETUP_FAILED] Exit code: %tcInstallResult%. Please share this error screen, not API tokens.
echo.
pause
popd
exit /b %tcInstallResult%
