@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul

where py.exe >nul 2>nul
if errorlevel 1 goto try_python
py -3 "%~dp0scripts\windows_deploy.py" probe >nul 2>nul
if errorlevel 1 goto try_python
py -3 "%~dp0scripts\windows_deploy.py" install %*
exit /b %errorlevel%

:try_python
where python.exe >nul 2>nul
if errorlevel 1 goto no_python
python.exe "%~dp0scripts\windows_deploy.py" probe >nul 2>nul
if errorlevel 1 goto no_python
python.exe "%~dp0scripts\windows_deploy.py" install %*
exit /b %errorlevel%

:no_python
echo No usable 64-bit Python 3.10 or newer was found.
exit /b 1
