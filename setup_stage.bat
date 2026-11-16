@echo off
rem ============================================================
rem  Minimal setup: only what the instrument GUIs need.
rem
rem  Use this when you just want to run a device window
rem  (stage / DAQ) and do not need the calculators.
rem
rem  Creates .venv with PySide6-Essentials + pyserial (~227 MB),
rem  which is much faster than the full requirements.txt.
rem
rem  Keep this file pure ASCII: cmd reads .bat in the ANSI code
rem  page, so non-ASCII comments can corrupt parsing.
rem ============================================================
setlocal
cd /d "%~dp0"

echo ============================================
echo  Device GUI - minimal setup
echo ============================================
echo.

if exist ".venv\Scripts\python.exe" goto have_venv
echo [1/2] Creating .venv ...
python -m venv .venv
if errorlevel 1 goto fail
goto install

:have_venv
echo [1/2] Reusing existing .venv

:install
echo [2/2] Installing PySide6-Essentials + pyserial ...
if exist "wheels\" goto offline
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-stage.txt
if errorlevel 1 goto fail
goto check

:offline
echo       using the local wheels\ folder
".venv\Scripts\python.exe" -m pip install --no-index --find-links wheels -r requirements-stage.txt
if errorlevel 1 goto fail

:check
echo.
echo Checking the stage GUI imports ...
".venv\Scripts\python.exe" -c "import devicegui.stage_app; from ui.pages.stage_page import StagePage; print('      OK')"
if errorlevel 1 goto fail

echo.
echo ============================================
echo  Done.
echo.
echo  Start a device window with:
echo      device.bat stage
echo      device.bat daq
echo.
echo  Remember: the ASI stage needs a USB-serial driver
echo  (CH340 / FTDI / PL2303 - check your cable).
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
