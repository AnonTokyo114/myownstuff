@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Pack the project for another computer (e.g. the lab PC).
rem
rem  Excludes (at every level):
rem    .venv\        bound to THIS machine's paths, useless elsewhere
rem    wheels\       large; only needed for offline installs
rem    __pycache__\  compiled cache
rem    *.pyc
rem
rem  Output: ..\research-gui-portable.zip  (next to the project)
rem ============================================================

set "OUT=%~dp0..\research-gui-portable.zip"
set "TMPDIR=%TEMP%\research-gui-pack"

echo Packing the project ...
echo.

if exist "%TMPDIR%" rd /s /q "%TMPDIR%"
mkdir "%TMPDIR%"

rem robocopy returns 0-7 on success, >=8 on failure
robocopy "." "%TMPDIR%" /E /XD ".venv" "wheels" "__pycache__" ".git" ^
         /XF "*.pyc" /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :fail

if exist "%OUT%" del "%OUT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path '%TMPDIR%\*' -DestinationPath '%OUT%' -Force"
if errorlevel 1 goto :fail

rd /s /q "%TMPDIR%"

powershell -NoProfile -Command ^
  "$z = Get-Item '%OUT%'; Write-Host ('  created: ' + $z.FullName); Write-Host ('  size   : ' + [math]::Round($z.Length/1KB,0) + ' KB (instead of ~634 MB with .venv)')"

echo.
echo ============================================================
echo  Next steps
echo ============================================================
echo  1. Copy research-gui-portable.zip to a USB stick / drive,
echo     or upload it anywhere you can reach from the lab PC.
echo  2. On the lab computer: extract it anywhere.
echo  3. Double-click setup.bat there (needs internet).
echo  4. Double-click run.bat.
echo.
echo  No internet on the lab PC?
echo     Run download_wheels.bat HERE first, then also copy the
echo     wheels\ folder. setup.bat detects it and installs offline.
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo [X] Packing failed.
if exist "%TMPDIR%" rd /s /q "%TMPDIR%"
echo.
pause
exit /b 1
