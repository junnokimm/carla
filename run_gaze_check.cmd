@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    echo Missing CARLA Python: .venv\Scripts\python.exe
    exit /b 2
)
".venv\Scripts\python.exe" -B -m tools.tobii.recheck %*
exit /b %errorlevel%
