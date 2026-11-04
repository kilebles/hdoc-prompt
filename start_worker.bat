@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "PYTHONPATH=%~dp0src"
rem Executor of the prompts module: brief and prompts through agy (Claude Code later). One window is enough.
rem Run under the Windows user "footage" - agy works with that user's Google login.
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not exist ".venv\Scripts\python.exe" echo No .venv here - double-click setup_web.bat first.
echo Python: %PY%
:loop
"%PY%" -u -m promptgen.worker
echo.
echo Worker stopped. Restart in 15 seconds. Close this window to stop it completely.
timeout /t 15 >nul
goto loop
