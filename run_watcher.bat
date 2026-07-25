@echo off
title BMS Odyssey IMAX Watcher
color 0A
echo.
echo  ======================================
echo   BookMyShow IMAX Odyssey Watcher
echo   PVR Palladium Mall, Ahmedabad
echo   Target Date: Saturday, 1 August 2026
echo  ======================================
echo.

REM Create and activate virtual environment if it doesn't exist
if not exist ".venv" (
    echo [1/4] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Python not found or venv creation failed.
        echo Make sure Python 3.10+ is installed: https://python.org
        pause
        exit /b 1
    )
)

echo [2/4] Activating virtual environment...
call .venv\Scripts\activate.bat

echo [3/4] Installing / updating dependencies...
pip install -q -r requirements.txt
playwright install chromium --with-deps

echo [4/4] Starting watcher...
echo.
echo  Checking every 30 minutes. Press Ctrl+C to stop.
echo  All checks are logged to: watcher.log
echo.

python watcher.py

pause
