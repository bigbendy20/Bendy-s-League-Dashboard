@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Link a sign-in email to a profile
echo ============================================
echo.
echo This tells the site whose page is whose. Without it, a friend
echo signs in fine but lands on someone else's profile and can't set
echo their own climb goal.
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found. Run Setup.bat first.
    pause
    exit /b 1
)

python -m pip install --quiet "psycopg[binary]>=3.1"

echo Current links:
echo.
python link_email.py
echo.

set /p RIOTID="Riot ID to link (e.g. Name#TAG), or blank to quit: "
if "%RIOTID%"=="" exit /b 0
set /p EMAIL="Email they sign in with: "
if "%EMAIL%"=="" exit /b 0

echo.
python link_email.py "%RIOTID%" "%EMAIL%"

echo.
pause
