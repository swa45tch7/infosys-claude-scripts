@echo off
rem Windows launcher: double-click, or run from Command Prompt with extra options,
rem for example:  run_health_check.bat --customer "Contoso" --contract-devices 1500
setlocal
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    echo Creating a private Python environment in .venv ...
    if exist ".venv" rmdir /s /q ".venv"
    %PY% -m venv .venv
    if errorlevel 1 goto nopython
)

".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto piperror

".venv\Scripts\python.exe" lm_health_report.py %*
echo.
pause
exit /b 0

:nopython
echo.
echo Python 3.9 or newer was not found. Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup. See README.md, step 1.
pause
exit /b 1

:piperror
echo.
echo Could not install the required packages. Check your internet or proxy settings.
echo See README.md, Troubleshooting.
pause
exit /b 1
