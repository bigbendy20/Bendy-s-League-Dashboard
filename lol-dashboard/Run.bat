@echo off
cd /d "%~dp0"
echo Starting Bendy's League Board...
echo A browser tab will open automatically. Closing this window stops the app.
echo.
python -m streamlit run app.py
pause
