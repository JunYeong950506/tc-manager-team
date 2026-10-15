@echo off
chcp 65001 >nul
setlocal
title Zephyr API Token Setup
pushd "%~dp0"
call "%~dp0scripts\check_tc_startup.cmd" "%~dp0scripts\connect_zephyr.ps1"
if errorlevel 1 goto startup_failed
powershell.exe -NoProfile -File "%~dp0scripts\connect_zephyr.ps1" %*
set "tcConnectResult=%errorlevel%"
goto finish
:startup_failed
set "tcConnectResult=1"
:finish
if not "%tcConnectResult%"=="0" echo [TC_CONNECT_FAILED] Exit code: %tcConnectResult%. Please share this error screen, not API tokens.
echo.
pause
popd
exit /b %tcConnectResult%
