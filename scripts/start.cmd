@echo off
setlocal
set "AFDS_LAUNCH_ROOT=%~dp0.."
if not exist "%AFDS_LAUNCH_ROOT%\.venv\Scripts\python.exe" (
  echo Install the Python and frontend dependencies using README.md first.
  pause
  exit /b 1
)
"%AFDS_LAUNCH_ROOT%\.venv\Scripts\python.exe" "%~dp0dev.py" --open-browser
if errorlevel 1 pause
