@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   How far back will Riot go?
echo ============================================
echo.
echo This asks Riot directly how much of your match history it will
echo still list. It reads only - nothing is written, nothing is changed,
echo and it costs about 20 API calls.
echo.
echo Copy the whole output back to Claude.
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found. Run Setup.bat first.
    pause
    exit /b 1
)

python tools\check_history.py %1

echo.
echo ============================================
echo Done. Copy everything above this line.
echo ============================================
pause
