@echo off
rem Double-click to start Modsmith on Windows. Needs Python 3.11+ from python.org.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -c "import sys; sys.exit(sys.version_info < (3, 11))" || (
  echo Python 3.11 or newer is required: https://www.python.org/downloads/
  pause
  exit /b 1
)
set PYTHONPATH=%~dp0src
%PY% -m modsmith gui
if errorlevel 1 pause
