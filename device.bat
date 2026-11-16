@echo off
rem ============================================================
rem  Start a single device GUI on its own.
rem
rem  Usage:  device.bat stage
rem          device.bat daq
rem          device.bat camera, laser, scope or others
rem
rem  Keep this file pure ASCII: cmd reads .bat in the ANSI code
rem  page, so non-ASCII comments can corrupt parsing.
rem ============================================================
setlocal
cd /d "%~dp0"

if "%~1"=="" goto usage

set "TARGET=devicegui.%~1_app"
set "HERE=%~dp0"

if exist "%HERE%.venv\Scripts\pythonw.exe" goto use_venv
goto use_system

:use_venv
start "" /D "%HERE%" "%HERE%.venv\Scripts\pythonw.exe" -m %TARGET%
goto done

:use_system
start "" /D "%HERE%" pythonw -m %TARGET%
goto done

:usage
echo Usage: device.bat NAME
echo.
echo   stage   - Stage Controller  (ASI LX-4000)
echo   camera  - Camera
echo   laser   - Laser
echo   scope   - Oscilloscope
echo   daq     - Data Acquisition (NI-DAQmx)
echo   others  - Other instruments
echo.
pause
exit /b 1

:done
