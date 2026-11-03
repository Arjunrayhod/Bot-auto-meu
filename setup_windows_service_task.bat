@echo off
title Setup 24/7 Windows Scheduled Task for Academic Bot
echo ========================================================
echo   Configuring Windows Task Scheduler for 24/7 Resilience
echo ========================================================
echo.

set "TASK_NAME=AcademicBot24x7"
set "TARGET_VBS=%~dp0run_silent_background.vbs"

echo Creating Windows Scheduled Task '%TASK_NAME%'...
schtasks /create /tn "%TASK_NAME%" /tr "wscript.exe \"%TARGET_VBS%\"" /sc onlogon /rl highest /f

if %ERRORLEVEL% equ 0 (
    echo.
    echo [SUCCESS] Windows Scheduled Task '%TASK_NAME%' created successfully!
    echo The bot is guaranteed to launch on every Windows logon with highest priority.
) else (
    echo.
    echo [NOTE] Task creation with highest privileges required admin. Retrying without /rl highest...
    schtasks /create /tn "%TASK_NAME%" /tr "wscript.exe \"%TARGET_VBS%\"" /sc onlogon /f
)

echo.
pause
