@echo off
title Academic & Quiz Auto-Bot Daemon (24/7 Watchdog)
color 0A
echo ===================================================
echo     ACADEMIC & QUIZ AUTONOMOUS ASSISTANT BOT
echo ===================================================
echo Portal: https://meu.gnums.co.in/Login.aspx
echo Mode  : 24/7 Continuous Monitoring & Auto-Healing Watchdog
echo AI    : Google Gemini AI
echo ===================================================
echo.

cd /d "%~dp0"
python watchdog.py

pause
