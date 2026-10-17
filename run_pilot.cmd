@echo off
setlocal
cd /d "%~dp0"
set "P8_PILOT_PYTHON=C:\dev\carla-native-tm-cancel-20261012\.venv-p8\Scripts\python.exe"
if not exist "%P8_PILOT_PYTHON%" (
    echo P8 Python was not found: %P8_PILOT_PYTHON%
    echo Use the existing verified P8 Python environment. See docs\P8_Pilot_Fix_20261017.md.
    exit /b 2
)
"%P8_PILOT_PYTHON%" -B -u -m tools.run_pilot %*
exit /b %ERRORLEVEL%
