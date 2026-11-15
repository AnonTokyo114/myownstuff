@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Download dependency wheels for offline use
echo ============================================
echo.
echo This downloads every package in requirements.txt
echo into the wheels\ folder. Copy that folder together
echo with the project to a machine without internet,
echo then run setup.bat there.
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python was not found on PATH.
    pause
    exit /b 1
)

if not exist "wheels\" mkdir wheels

python -m pip download -r requirements.txt -d wheels
if errorlevel 1 (
    echo.
    echo [!] Some packages could not be downloaded as wheels.
    echo     Retry without the binary-only restriction by running:
    echo         python -m pip download -r requirements.txt -d wheels
    echo.
    pause
    exit /b 1
)

echo.
echo Done. wheels\ now contains:
dir /b wheels
echo.
echo Copy the whole project folder ^(including wheels\^) to the lab
echo computer, then run setup.bat there.
echo.
pause
