@echo off
rem Double-click to make a LogicMonitor health check report on Windows.
rem Finds Python 3.9+, sets up a private .venv in this folder, then runs the guided start.
setlocal
title LogicMonitor Health Check
cd /d "%~dp0"

if not exist "app\easy_start.py" goto notunzipped

set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY goto nopython

".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo Getting ready. This is only slow the first time...
    if exist ".venv" rmdir /s /q ".venv"
    %PY% -m venv .venv >nul 2>nul
    if errorlevel 1 goto setupfailed
)

fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul
if errorlevel 1 (
    echo Installing what the report needs. Please wait...
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check --upgrade pip >nul 2>nul
    ".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto installfailed
    copy /y requirements.txt ".venv\requirements.installed" >nul
)

cls
".venv\Scripts\python.exe" app\easy_start.py
goto finish

:notunzipped
echo Please unzip first: right-click the zip file, click "Extract All", then click "Extract".
echo Then open the new folder and double-click Start-Windows.bat.
goto finish

:nopython
echo Python is needed. The download page will open now. Install it, then double-click this file again.
start "" "https://www.python.org/downloads/"
echo.
echo Not finished: Python is not installed yet.
goto finish

:setupfailed
echo Not finished: could not set up Python in this folder.
goto finish

:installfailed
echo Not finished: the install failed. Check your internet connection and try again.
goto finish

:finish
echo.
echo Press any key to close this window.
pause >nul
