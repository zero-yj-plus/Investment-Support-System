@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

set "MANAGER=%~dp0scripts\tdx-login-guard\manage-tdx-login-guard.ps1"
if not exist "%MANAGER%" (
    echo TDX login guard manager is missing: %MANAGER%
    exit /b 1
)

where pwsh.exe >nul 2>nul
if errorlevel 1 goto use_windows_powershell
set "POWERSHELL_EXE=pwsh.exe"
goto dispatch

:use_windows_powershell
where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo PowerShell is unavailable.
    exit /b 1
)
set "POWERSHELL_EXE=powershell.exe"

:dispatch
if /i "%~1"=="setup" goto setup_arg
if /i "%~1"=="credential" goto credential
if /i "%~1"=="inspect" goto inspect
if /i "%~1"=="test" goto test_arg
if /i "%~1"=="start" goto start
if /i "%~1"=="stop" goto stop
if /i "%~1"=="status" goto status
if /i "%~1"=="config" goto config
if /i "%~1"=="uninstall" goto uninstall_arg
if not "%~1"=="" goto usage

:menu
cls
echo TDX Login Guard
echo.
echo   1. Install or update and enable automatic login
echo   2. Replace the encrypted login credential
echo   3. Inspect login controls (read-only)
echo   4. Run one real automatic-login test
echo   5. Start the guard task
echo   6. Stop the guard task
echo   7. Show status and recent log
echo   8. Edit guard configuration
echo   9. Uninstall task but keep configuration and credential
echo   0. Exit
echo.
set "MENU_CHOICE="
set /p "MENU_CHOICE=Select: "
if "%MENU_CHOICE%"=="1" goto setup_prompt
if "%MENU_CHOICE%"=="2" goto credential
if "%MENU_CHOICE%"=="3" goto inspect
if "%MENU_CHOICE%"=="4" goto test_prompt
if "%MENU_CHOICE%"=="5" goto start
if "%MENU_CHOICE%"=="6" goto stop
if "%MENU_CHOICE%"=="7" goto status
if "%MENU_CHOICE%"=="8" goto config
if "%MENU_CHOICE%"=="9" goto uninstall_prompt
if "%MENU_CHOICE%"=="0" exit /b 0
goto menu

:setup_prompt
set "TDX_PATH=D:\tdx\TdxW.exe"
set /p "TDX_PATH=Full path to TdxW.exe [D:\tdx\TdxW.exe]: "
goto setup_run

:setup_arg
set "TDX_PATH=%~2"
if not defined TDX_PATH set "TDX_PATH=D:\tdx\TdxW.exe"

:setup_run
call :run Setup -TdxPath "%TDX_PATH%"
goto result

:credential
call :run SetCredential
goto result

:inspect
call :run Inspect
goto result

:test_prompt
echo.
echo This action will fill the stored credential and click the login button.
set "CONFIRM_TEST="
set /p "CONFIRM_TEST=Continue [y/N]? "
if /i not "%CONFIRM_TEST%"=="y" goto menu

:test_arg
call :run Test
goto result

:start
call :run Start
goto result

:stop
call :run Stop
goto result

:status
call :run Status
goto result

:config
call :run EditConfig
goto result

:uninstall_prompt
echo.
echo This removes only the scheduled task. Files and encrypted credential are kept.
set "CONFIRM_UNINSTALL="
set /p "CONFIRM_UNINSTALL=Continue [y/N]? "
if /i not "%CONFIRM_UNINSTALL%"=="y" goto menu

:uninstall_arg
call :run Uninstall
goto result

:result
set "RESULT_CODE=%errorlevel%"
echo.
if "%RESULT_CODE%"=="0" (
    echo Operation completed.
) else (
    echo Operation failed with exit code %RESULT_CODE%.
)
if not "%~1"=="" exit /b %RESULT_CODE%
pause
goto menu

:run
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%MANAGER%" -Action %*
exit /b %errorlevel%

:usage
echo Usage:
echo   tdx-login-guard.bat
echo   tdx-login-guard.bat setup "D:\tdx\TdxW.exe"
echo   tdx-login-guard.bat credential^|inspect^|test^|start^|stop^|status^|config^|uninstall
exit /b 2
