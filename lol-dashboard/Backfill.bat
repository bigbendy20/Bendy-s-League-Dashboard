@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Bendy's League Board - One-Time Backfill
echo ============================================
echo.
echo This fetches every tracked player's match history into a local
echo database file. It takes a few hours and can be stopped and restarted
echo at any time - it always picks up where it left off.
echo.
echo Leave this window open. You can keep using your PC.
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found. Run Setup.bat first.
    pause
    exit /b 1
)

echo Step 1 of 2: checking the roster...
python seed_profiles.py --dry-run
if %errorlevel% neq 0 (
    echo.
    echo The roster has a problem - see the message above. Fix roster.txt
    echo and run this again. Nothing was fetched.
    pause
    exit /b 1
)

echo.
echo Step 2 of 2: resolving accounts and fetching...
python seed_profiles.py
if %errorlevel% neq 0 (
    echo.
    echo Could not resolve the accounts. Check your API key in .env.
    pause
    exit /b 1
)

echo.
python refresh_job.py --backfill

echo.
echo ============================================
echo Done. The database is at:
echo   %~dp0data\board.db
echo.
echo Nothing has been uploaded anywhere - that's a separate step.
echo ============================================
pause
