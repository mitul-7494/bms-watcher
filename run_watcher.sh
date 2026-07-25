#!/usr/bin/env bash

# BookMyShow IMAX Odyssey Watcher
# PVR Palladium Mall, Ahmedabad
# Target Date: Saturday, 1 August 2026

echo "======================================"
echo " BookMyShow IMAX Odyssey Watcher      "
echo " PVR Palladium Mall, Ahmedabad        "
echo " Target Date: Saturday, 1 August 2026 "
echo "======================================"
echo ""

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "[1/4] Creating Python virtual environment..."
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "ERROR: Python 3 not found or venv creation failed."
        echo "Make sure Python 3.10+ is installed."
        exit 1
    fi
fi

echo "[2/4] Activating virtual environment..."
source .venv/bin/activate

echo "[3/4] Installing / updating dependencies..."
pip install -q -r requirements.txt
playwright install chromium --with-deps

echo "[4/4] Starting watcher..."
echo ""
echo " Checking every 30 minutes. Press Ctrl+C to stop."
echo " All checks are logged to: watcher.log"
echo ""

python watcher.py
