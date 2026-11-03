@echo off
title Stop Academic Auto-Bot & Watchdog
echo Stopping all running instances of academic bot and watchdog...
powershell -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*src.main*' -or $_.CommandLine -like '*watchdog.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Bot and watchdog stopped successfully.
pause
