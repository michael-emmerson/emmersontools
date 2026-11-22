@echo off
rem Double-click on Windows to start RRR Coffee Inventory. Keep this window open while you use the app.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist .venv (
  echo First run: setting up...
  %PY% -m venv .venv || goto fail
  .venv\Scripts\python -m pip install -q -r requirements.txt || goto fail
)
.venv\Scripts\python app.py %*
goto end
:fail
echo Setup failed. Install Python 3 from https://www.python.org/downloads/ (tick "Add python.exe to PATH") and try again.
pause
:end
