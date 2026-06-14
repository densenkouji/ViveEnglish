@echo off
REM Alternative to a full .exe: pre-download all Python wheels into vendor\ so the
REM target machine can install WITHOUT internet and without hunting for packages.
REM (Python itself is still required on the target — for a fully Python-free
REM distribution use build_exe.bat / PyInstaller instead.)
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul && (set PY=py) || (set PY=python)

echo [vendor] Downloading wheels into vendor\ ...
%PY% -m pip download -r requirements.txt -d vendor
%PY% -m pip download foundry-local-sdk-winml -d vendor

echo [vendor] Done. Ship the project together with the vendor\ folder.
echo          On the target machine run: packaging\run_offline.bat
endlocal
