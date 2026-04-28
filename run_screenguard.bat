@echo off
setlocal enabledelayedexpansion

echo ========================================================
echo   ScreenGuard - IoT Safety System | Launching...
echo ========================================================

cd server

:: 1. Check for Virtual Environment
if not exist "venv" (
    echo [INFO] Creating Virtual Environment...
    python -m venv venv
)

:: 2. Activate Environment
echo [INFO] Activating Environment...
call venv\Scripts\activate

:: 3. Install Requirements
echo [INFO] Checking Dependencies...
pip install -r requirements.txt --quiet

:: 4. Check for AI Models
if not exist "models\age_deploy.prototxt" (
    echo [INFO] AI Models missing. Downloading now...
    python download_weights.py
)

:: 5. Launch Dashboard in Browser
echo [INFO] Launching Dashboard...
start http://localhost:5000

:: 6. Start Server
echo [INFO] Starting Server...
echo [TIP] Keep this window open while using ScreenGuard.
python server.py

pause
