@echo off
rem One-click launcher for the Maple control panel.
rem Double-click this file (or a desktop shortcut to it) -- no terminal/cd needed.
rem cd /d "%~dp0" makes it work from anywhere (a desktop shortcut, the Start menu, etc.).
cd /d "%~dp0"
title Maple Control Panel
:start
python panel.py --open
if %errorlevel% neq 0 (
    echo.
    echo Panel exited with an error. Restarting in 5 seconds... (close this window to stop)
    timeout /t 5 /nobreak
    goto start
)
echo Panel closed.
pause
