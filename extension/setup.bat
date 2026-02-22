@echo off
echo ============================================
echo  Claude Bridge for Zen Browser - Setup
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ first.
    pause
    exit /b 1
)

:: Install websockets
echo Installing Python dependencies...
pip install websockets --quiet
echo Done.
echo.

:: Create screenshot directory
if not exist "%USERPROFILE%\claude-zen-screenshots" mkdir "%USERPROFILE%\claude-zen-screenshots"

echo ============================================
echo  Setup Complete!
echo ============================================
echo.
echo Next steps:
echo   1. Load the extension in Zen Browser:
echo      - Open about:debugging#/runtime/this-firefox
echo      - Click "Load Temporary Add-on"
echo      - Select manifest.json from this folder
echo.
echo   2. Start the bridge server:
echo      - Run: python native\bridge.py
echo      - Or double-click start-bridge.bat
echo.
echo   3. Claude Desktop can now control Zen via:
echo      - PowerShell: Invoke-RestMethod http://localhost:8765/api/status
echo      - Or load zen-bridge.ps1 for helper functions
echo.
pause
