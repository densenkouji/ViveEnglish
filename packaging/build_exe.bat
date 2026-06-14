@echo off
REM Build a standalone Windows executable for ViveEnglish (no Python/pip for end users).
REM Run this once on a Windows build machine that has Python installed.
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul && (set PY=py) || (set PY=python)

echo [build] Creating build virtual environment...
%PY% -m venv .build-venv
call .build-venv\Scripts\activate.bat

echo [build] Installing build + runtime dependencies...
python -m pip install --upgrade pip
pip install pyinstaller
pip install -r requirements.txt
REM Foundry Local SDK + native acceleration (Windows / WinML build):
pip install foundry-local-sdk-winml

echo [build] Running PyInstaller...
pyinstaller --noconfirm packaging\viveenglish.spec

echo.
echo [build] Done. Distributable folder: dist\ViveEnglish\
echo         Ship the whole dist\ViveEnglish\ folder; users run ViveEnglish.exe
endlocal
