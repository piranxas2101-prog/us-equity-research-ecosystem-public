@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_py.ps1" %*
exit /b %ERRORLEVEL%
