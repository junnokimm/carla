@echo off
setlocal
call "%~dp0run_pilot.cmd" --m2-only %*
exit /b %ERRORLEVEL%
