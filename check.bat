@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul

if not exist "%~dp0.venv\Scripts\python.exe" goto try_py
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\windows_deploy.py" check %*
exit /b %errorlevel%

:try_py
where py.exe >nul 2>nul
if errorlevel 1 goto try_python
py -3 "%~dp0scripts\windows_deploy.py" check %*
exit /b %errorlevel%

:try_python
where python.exe >nul 2>nul
if errorlevel 1 goto no_python
python.exe "%~dp0scripts\windows_deploy.py" check %*
exit /b %errorlevel%

:no_python
echo Python is unavailable, so deployment checks cannot run.
exit /b 1
