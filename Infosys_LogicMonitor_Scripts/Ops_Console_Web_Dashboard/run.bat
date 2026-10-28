@echo off
REM LogicMonitor Ops Console - Windows
cd /d "%~dp0"
if not exist ".venv" (
  echo First run: setting up a local environment...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
)
".venv\Scripts\python.exe" -m lm_console %*
pause
