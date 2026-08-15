@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Upload the local database to Postgres
echo ============================================
echo.
echo Reads POSTGRES_URL from .env. Safe to re-run - nothing is
echo duplicated and nothing in the destination is ever deleted.
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found. Run Setup.bat first.
    pause
    exit /b 1
)

echo Installing the Postgres driver if needed...
python -m pip install --quiet "psycopg[binary]>=3.1"

echo.
echo Step 1 of 2: dry run - showing what would be copied.
python upload_store.py --dry-run
if %errorlevel% neq 0 (
    echo.
    echo Could not connect. Check POSTGRES_URL in .env.
    pause
    exit /b 1
)

echo.
set /p GO="Look right? Type Y to upload for real: "
if /i not "%GO%"=="Y" (
    echo Cancelled. Nothing was written.
    pause
    exit /b 0
)

echo.
python upload_store.py

echo.
pause
