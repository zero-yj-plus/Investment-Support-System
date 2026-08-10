@echo off
setlocal
cd /d "%~dp0"
set "APP_ROOT=%~dp0"
set "APP_ROOT=%APP_ROOT:~0,-1%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
    set "PYTHON_ARGS="
    goto run_upgrade
)

where py.exe >nul 2>nul
if errorlevel 1 goto try_python
set "PYTHON_EXE=py.exe"
set "PYTHON_ARGS=-3"
goto run_upgrade

:try_python
where python.exe >nul 2>nul
if errorlevel 1 goto no_python
set "PYTHON_EXE=python.exe"
set "PYTHON_ARGS="
goto run_upgrade

:run_upgrade
set "PACKAGE_FROM_ARG=0"
set "PACKAGE=%~1"
if defined PACKAGE if exist "%PACKAGE%" set "PACKAGE_FROM_ARG=1"
if "%PACKAGE_FROM_ARG%"=="1" goto extract_helper
set "PACKAGE="
for /f "delims=" %%F in ('dir /b /a-d /o-d "%~dp0WUDI-*-Windows-deploy-*.zip" 2^>nul') do if not defined PACKAGE set "PACKAGE=%~dp0%%F"
if not defined PACKAGE (
    echo No Windows deployment ZIP was found in the current installation directory.
    exit /b 1
)

:extract_helper
set "UPGRADE_TEMP=%TEMP%\wudi-upgrade-%RANDOM%-%RANDOM%"
mkdir "%UPGRADE_TEMP%" >nul 2>nul
"%PYTHON_EXE%" %PYTHON_ARGS% -c "import hashlib,pathlib,sys,zipfile; p=pathlib.Path(sys.argv[1]); s=pathlib.Path(str(p)+'.sha256'); expected=s.read_text(encoding='ascii').split()[0].lower(); actual=hashlib.sha256(p.read_bytes()).hexdigest(); assert actual==expected, 'SHA-256 mismatch'; zipfile.ZipFile(p).extract('scripts/windows_upgrade.py',sys.argv[2])" "%PACKAGE%" "%UPGRADE_TEMP%"
if errorlevel 1 (
    echo Failed to verify the ZIP or extract scripts\windows_upgrade.py.
    exit /b 1
)
set "UPGRADE_HELPER=%UPGRADE_TEMP%\scripts\windows_upgrade.py"

:invoke_upgrade
if "%PACKAGE_FROM_ARG%"=="1" (
    "%PYTHON_EXE%" %PYTHON_ARGS% "%UPGRADE_HELPER%" "%PACKAGE%" --app-root "%APP_ROOT%" %2 %3 %4 %5 %6 %7 %8 %9
) else (
    "%PYTHON_EXE%" %PYTHON_ARGS% "%UPGRADE_HELPER%" "%PACKAGE%" --app-root "%APP_ROOT%" %*
)
set "UPGRADE_EXIT=%errorlevel%"
if defined UPGRADE_TEMP rmdir /s /q "%UPGRADE_TEMP%"
exit /b %UPGRADE_EXIT%

:no_python
echo No usable Python was found.
exit /b 1
