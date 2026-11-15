@echo off
rem ============================================
rem  Personal Research GUI - one click start
rem  Uses the project's own .venv when present,
rem  otherwise falls back to the system Python.
rem ============================================
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    ".venv\Scripts\pythonw.exe" main.py
) else (
    pythonw main.py
)
