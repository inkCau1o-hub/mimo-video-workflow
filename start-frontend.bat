@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
set "SCRIPT_PATH=%~dp0windows-app\启动开发版.ps1"
if not exist "%SCRIPT_PATH%" (
    echo [ERROR] Cannot find the Windows startup script.
    exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_PATH%" -FrontendOnly -NoBrowser %*
exit /b %ERRORLEVEL%
