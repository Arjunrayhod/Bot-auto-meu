@echo off
title Install Academic Bot to Windows Startup
echo ========================================================
echo   Setting up Academic Auto-Bot to start on Windows Boot
echo ========================================================
echo.

set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_VBS=%~dp0run_silent_background.vbs"

if not exist "%STARTUP_DIR%" (
    echo Error: Startup directory not found.
    pause
    exit /b 1
)

echo Creating silent startup shortcut...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%STARTUP_DIR%\AcademicAutoBot.lnk'); $s.TargetPath = 'wscript.exe'; $s.Arguments = '\"%TARGET_VBS%\"'; $s.WorkingDirectory = '%~dp0'; $s.Save()"

if exist "%STARTUP_DIR%\AcademicAutoBot.lnk" (
    echo [SUCCESS] Bot added to Windows Startup!
    echo It will now run automatically and silently whenever your computer starts.
) else (
    echo [ERROR] Failed to create shortcut in Startup.
)

echo.
pause
