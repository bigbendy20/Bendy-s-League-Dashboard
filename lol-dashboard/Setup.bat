@echo off
setlocal

echo ============================================
echo   Bendy's League Board - First-Time Setup
echo ============================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    goto no_python
)

echo Found Python. Checking version...
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo Found Python, but it looks older than 3.10, which this needs.
    echo Please install a newer version from https://www.python.org/downloads/
    echo IMPORTANT: on the first install screen, check "Add python.exe to PATH"
    echo before clicking Install. Then run Setup.bat again.
    echo.
    pause
    exit /b 1
)

echo.
echo Installing required packages (this can take a minute)...
python -m pip install --upgrade pip >nul 2>nul
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo Something went wrong installing packages. Copy the error above and
    echo send it over so it can get fixed.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo Creating your personal config file...
    copy .env.example .env >nul
)

echo.
echo ============================================
echo   Setup complete!
echo ============================================
echo.
echo Next: double-click Run.bat to start the dashboard.
echo The first time it opens, it'll ask for your Riot ID and API key
echo right there in the browser - nothing to edit by hand.
echo.
pause
exit /b 0

:no_python
echo Python wasn't found on this computer, so it needs to be installed first
echo (one-time, only takes a minute).
echo.
echo 1. Opening the download page for you now...
echo 2. Run the installer.
echo 3. IMPORTANT: on the first screen, check the box that says
echo    "Add python.exe to PATH" before clicking Install.
echo 4. Once it finishes, close this window and double-click Setup.bat again.
echo.
start https://www.python.org/downloads/
pause
exit /b 1
