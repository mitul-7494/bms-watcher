@echo off
title BMS Watcher - Send Test Email
call .venv\Scripts\activate.bat 2>nul || (
    echo Run run_watcher.bat first to set up the environment.
    pause
    exit /b 1
)
echo Sending test email...
python watcher.py --test
pause
