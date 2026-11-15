@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Personal Research GUI - setup
echo ============================================
echo.

rem ---------------------------------------------------------------- 1) Python
where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python was not found on PATH.
    echo.
    echo     Install Python 3.10 or newer from https://www.python.org/downloads/
    echo     and tick "Add python.exe to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo Using Python %PYVER%
echo.

rem ---------------------------------------------------------------- 2) venv
if exist ".venv\Scripts\python.exe" (
    echo [1/3] Virtual environment already exists, reusing it.
) else (
    echo [1/3] Creating .venv ...
    python -m venv .venv
    if errorlevel 1 goto :fail
)

set "VPY=.venv\Scripts\python.exe"

rem ---------------------------------------------------------------- 3) deps
echo [2/3] Installing dependencies ...
if exist "wheels\" (
    echo       using the local wheels\ folder ^(offline install^)
    "%VPY%" -m pip install --no-index --find-links wheels -r requirements.txt
) else (
    echo       fetching from PyPI
    "%VPY%" -m pip install --upgrade pip
    "%VPY%" -m pip install -r requirements.txt
)
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------- 4) check
echo [3/3] Checking that the program imports ...
"%VPY%" -c "import core.transport, core.devices, core.stage, core.nidaq, core.optics, core.eigencalc, core.csvdata; import ui.main_window; print('      all modules OK')"
if errorlevel 1 goto :fail

echo.
echo ============================================
echo  Done. Double-click run.bat to start.
echo.
echo  Notes:
echo    * NI-DAQ hardware additionally needs the NI-DAQmx DRIVER
echo      ^(the pip package alone is not enough^).
echo    * Run download_wheels.bat on a machine with internet if the
echo      lab computer is offline.
echo ============================================
echo.
pause
exit /b 0

:fail
echo.
echo ============================================
echo  Setup FAILED - see the messages above.
echo ============================================
echo.
pause
exit /b 1
