@echo off
cd /d "%~dp0"
echo Running the dashboard's test suite...
echo.
python tests\run_tests.py -v
echo.
pause
