@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo Project virtual environment is missing. Run install.bat first.
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\windows_deploy.py" start %*
exit /b %errorlevel%
