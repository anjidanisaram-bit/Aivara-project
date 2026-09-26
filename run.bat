@echo off
setlocal
cd /d "%~dp0"

title Aivara Analytics Platform

echo ======================================================
echo             Aivara Analytics Platform
echo ======================================================
echo.

:: Check for virtual environment in .venv or venv
if exist ".venv\Scripts\activate.bat" (
    echo Activating virtual environment .venv ...
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment venv ...
    call venv\Scripts\activate.bat
)

:: Verify python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in your system PATH.
    echo Please install Python 3.10+ and check Add Python to PATH.
    echo.
    pause
    exit /b 1
)

:: Check if requirements are satisfied, if streamlit is installed
python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo Streamlit not detected. Installing dependencies from requirements.txt ...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo Starting Aivara web interface on http://localhost:8501 ...
echo Press Ctrl+C in this window to stop the server.
echo.

python -m streamlit run app.py

if errorlevel 1 (
    echo.
    echo [ERROR] Application stopped or encountered an error.
    echo.
    pause
)
