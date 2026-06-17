@echo off
REM ViveEnglish launcher for Windows.
setlocal EnableDelayedExpansion

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo [ViveEnglish] Existing virtual environment is broken; recreating...
    rmdir /s /q .venv
  )
)

if not exist .venv (
  where py >nul 2>nul
  if not errorlevel 1 (
    set PY=py
  ) else (
    where python >nul 2>nul
    if errorlevel 1 (
      echo [ViveEnglish] Python was not found. Install Python and add it to PATH, then run this file again.
      pause
      exit /b 1
    )
    set PY=python
  )
  echo [ViveEnglish] Creating virtual environment...
  !PY! -m venv .venv
  if errorlevel 1 (
    echo [ViveEnglish] Failed to create the virtual environment.
    pause
    exit /b 1
  )
)
call .venv\Scripts\activate.bat
if errorlevel 1 (
  echo [ViveEnglish] Failed to activate the virtual environment.
  pause
  exit /b 1
)

echo [ViveEnglish] Upgrading pip (needed to find prebuilt wheels)...
python -m pip install --upgrade pip --quiet

echo [ViveEnglish] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
  echo [ViveEnglish] Failed to install dependencies.
  pause
  exit /b 1
)

python -c "import foundry_local_sdk" >nul 2>nul
if errorlevel 1 (
  echo [ViveEnglish] Installing Foundry Local SDK for AI speech features...
  python -m pip install foundry-local-sdk-winml
)

python -c "import foundry_local_sdk" >nul 2>nul
if errorlevel 1 (
  echo [ViveEnglish] Foundry Local SDK is unavailable; recording transcription will ask for typed input.
)

echo [ViveEnglish] Starting server on http://localhost:8000 ...
python run.py
