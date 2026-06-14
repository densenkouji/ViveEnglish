@echo off
REM ViveEnglish launcher for Windows.
setlocal

where py >nul 2>nul && (set PY=py) || (set PY=python)

if not exist .venv (
  echo [ViveEnglish] Creating virtual environment...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat

echo [ViveEnglish] Upgrading pip (needed to find prebuilt wheels)...
python -m pip install --upgrade pip --quiet

echo [ViveEnglish] Installing dependencies...
pip install -r requirements.txt

echo [ViveEnglish] Starting server on http://localhost:8000 ...
python run.py
