@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   What's in the hosted database?
echo ============================================
echo.
echo Read-only. Connects to Postgres and reports what the deployed
echo site will show.
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found. Run Setup.bat first.
    pause
    exit /b 1
)

python -m pip install --quiet "psycopg[binary]>=3.1"
python check_hosted.py

echo.
pause
