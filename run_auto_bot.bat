@echo off
title Academic & Quiz Auto-Bot Daemon
color 0A
echo ===================================================
echo     ACADEMIC & QUIZ AUTONOMOUS ASSISTANT BOT
echo ===================================================
echo Portal: https://meu.gnums.co.in/Login.aspx
echo Mode  : 24/7 Continuous Monitoring & Auto-Submit
echo AI    : Google Gemini 3.5 Flash
echo ===================================================
echo.

cd /d "%~dp0"
python -m src.main --daemon --interval 600

pause
