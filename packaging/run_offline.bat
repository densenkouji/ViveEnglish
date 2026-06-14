@echo off
REM Install dependencies from the bundled vendor\ folder (no internet) and run.
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul && (set PY=py) || (set PY=python)

if not exist .venv (
  echo [ViveEnglish] Creating virtual environment...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat

echo [ViveEnglish] Installing dependencies from vendor\ (offline)...
pip install --no-index --find-links vendor -r requirements.txt
pip install --no-index --find-links vendor foundry-local-sdk-winml

echo [ViveEnglish] Starting...
python run.py
endlocal
