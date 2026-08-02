@echo off
cd /d "%~dp0"
echo Installing required packages...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation failed. Review the error above.
  pause
  exit /b 1
)
echo.
echo Starting LeadLens V2...
python -m streamlit run app.py
pause
