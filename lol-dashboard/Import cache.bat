@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Import matches from the local cache
echo ============================================
echo.
echo Riot's match LIST only goes back a few months, but this PC has
echo years of matches cached from earlier use. This loads them into
echo the database.
echo.
echo No API calls. Nothing is downloaded. Safe to run repeatedly.
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found. Run Setup.bat first.
    pause
    exit /b 1
)

python import_cache.py %1

echo.
pause
