@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Module of prompts for the Motion Pr site: creates .venv and installs requirements-web.txt (Python 3.11+).
rem Run under the Windows user "footage": the worker uses that user's agy login.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if not exist ".venv\Scripts\python.exe" (
  echo Creating .venv ...
  %PY% -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements-web.txt || goto :fail
if not exist ".env" copy ".env.example" ".env" >nul && echo Created .env - put API_TOKEN into it.
echo.
echo Done. Start two windows: start_worker.bat and start_api.bat
pause
exit /b 0
:fail
echo Setup failed, see the messages above.
pause
exit /b 1
