@echo off
title Stop Academic Auto-Bot
echo Stopping all running instances of academic bot...
wmic process where "commandline like '%%src.main%%'" delete 2>nul || taskkill /f /im python.exe /fi "WINDOWTITLE eq Academic & Quiz Auto-Bot Daemon*" 2>nul
echo Bot stopped successfully.
pause
