@echo off
setlocal
cd /d "%~dp0"
if "%1"=="login" (
    "%~dp0.venv\Scripts\python.exe" login.py
) else (
    "%~dp0.venv\Scripts\python.exe" main.py %*
)
