@echo off
REM ============================================================================
REM Qubit Agent installer for Windows (CMD wrapper)
REM ============================================================================
REM This batch file launches the Qubit-owned PowerShell installer for users
REM running CMD. It never downloads or installs the upstream Hermes/OpenClaw
REM tree; the source is always the Qubit repository.
REM
REM Usage:
REM   curl -fsSL https://raw.githubusercontent.com/cgraph-dev/Qubit/production/scripts/install.cmd -o install.cmd && install.cmd && del install.cmd
REM
REM Or if you're already in PowerShell, use the direct command instead:
REM   iex (irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/install.ps1)
REM ============================================================================

setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
REM From a checkout, prefer the root installer beside this runtime tree.
set "ROOT_INSTALL=%SCRIPT_DIR%..\..\scripts\install.ps1"

echo.
echo  Qubit Agent installer
echo  Launching the PowerShell installer...
echo.

if exist "%ROOT_INSTALL%" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT_INSTALL%" %*
    exit /b !ERRORLEVEL!
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "iex (irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/install.ps1)"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Please try running PowerShell directly:
    echo    powershell -ExecutionPolicy Bypass -Command "iex (irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/install.ps1)"
    echo.
    pause
    exit /b 1
)
exit /b 0
