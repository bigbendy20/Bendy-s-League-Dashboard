@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Push newly-added stats to the live site
echo ============================================
echo.
echo Run this after an update that adds a new statistic. It re-reads
echo your local match archive, rebuilds every stored row, and pushes
echo the result to Postgres.
echo.
echo Games are rewritten in place - none are added or deleted.
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found. Run Setup.bat first.
    pause
    exit /b 1
)

python -m pip install --quiet "psycopg[binary]>=3.1"

echo Step 1 of 2: rebuilding the local database from the match archive...
python import_cache.py --reparse
if %errorlevel% neq 0 (
    echo.
    echo Could not rebuild locally. Nothing was sent.
    pause
    exit /b 1
)

echo.
echo Step 2 of 2: pushing to the live site...
python upload_store.py --overwrite

echo.
pause
