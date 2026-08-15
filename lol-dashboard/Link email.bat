@echo off
rem NOTE: deliberately NOT `setlocal enabledelayedexpansion`.
rem With delayed expansion on, cmd strips `!` from anything typed at a `set /p`
rem prompt — which silently mangles any email or Riot ID containing one. This
rem script reads user input, so it must stay off.
setlocal
cd /d "%~dp0"

echo ============================================
echo   Link a sign-in email to a profile
echo ============================================
echo.
echo This tells the site whose page is whose. Without it a friend
echo signs in fine, lands on someone else's profile, and can't set
echo their own climb goal.
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python was not found. Run Setup.bat first.
    echo.
    pause
    exit /b 1
)

echo Making sure the Postgres driver is installed...
python -m pip install --quiet "psycopg[binary]>=3.1"
if %errorlevel% neq 0 (
    echo.
    echo WARNING: the Postgres driver could not be installed. The link will
    echo be saved locally but the live site will not see it.
    echo.
)

echo.
echo Current links:
echo.
python link_email.py
echo.

set "RIOTID="
set "EMAIL="
set /p "RIOTID=Riot ID to link (e.g. Name#TAG), or press Enter to quit: "

rem Every exit below pauses. The first version used a bare `exit /b 0` here,
rem so pressing Enter made the window vanish instantly — which looks exactly
rem like the script crashing, and is the most likely thing to do by accident.
if not defined RIOTID (
    echo.
    echo Nothing entered - quitting without changes.
    echo.
    pause
    exit /b 0
)

set /p "EMAIL=Email they sign in with: "
if not defined EMAIL (
    echo.
    echo No email entered - quitting without changes.
    echo.
    pause
    exit /b 0
)

echo.
echo Running: python link_email.py "%RIOTID%" "%EMAIL%"
echo.
python link_email.py "%RIOTID%" "%EMAIL%"
set "RESULT=%errorlevel%"

echo.
if "%RESULT%"=="0" (
    echo Done. Re-run this any time to see the current links.
) else (
    echo That did not work - the reason is printed above.
    echo Common causes: the Riot ID does not match a profile exactly,
    echo or that email is already linked to someone else.
)
echo.
pause
