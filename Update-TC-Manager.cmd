@echo off
chcp 65001 >nul
setlocal
title TC Manager Update
pushd "%~dp0"
call "%~dp0scripts\check_tc_startup.cmd" "%~dp0scripts\update_tc_manager.ps1"
if errorlevel 1 goto startup_failed
powershell.exe -NoProfile -File "%~dp0scripts\update_tc_manager.ps1" %*
set "tcUpdateResult=%errorlevel%"
goto finish
:startup_failed
set "tcUpdateResult=1"
:finish
if not "%tcUpdateResult%"=="0" echo [TC_UPDATE_FAILED] Exit code: %tcUpdateResult%. Please share this error screen, not API tokens.
echo.
pause
popd
exit /b %tcUpdateResult%
