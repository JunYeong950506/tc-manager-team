@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 -X utf8 "plugins\tc-manager\scripts\tc_shared_setup.py" %*
pause
