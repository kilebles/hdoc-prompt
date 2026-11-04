@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "PYTHONPATH=%~dp0src"
rem Web API for the Motion Pr site (/api/prompts, port 8040). Jobs are run by start_worker.bat; settings in .env
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
echo Python: %PY%
:loop
"%PY%" -u -m promptgen.api
echo.
echo API stopped. Restart in 15 seconds. Close this window to stop it.
timeout /t 15 >nul
goto loop
