@echo off
REM ============================================================================
REM Qubit Agent installer for Windows (CMD wrapper)
REM ============================================================================
REM Launches the Qubit-owned PowerShell installer (scripts\install.ps1).
REM The canonical entry is PowerShell; this file only exists for users who run
REM CMD. It never downloads or installs the upstream Hermes/OpenClaw tree.
REM
REM Usage (from a checkout):
REM   scripts\install.cmd -Help
REM   scripts\install.cmd -DryRun
REM   scripts\install.cmd -QubitHome "%%LOCALAPPDATA%%\qubit" -Channel stable
REM
REM Fetched form:
REM   powershell -ExecutionPolicy Bypass -Command "iex (irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/install.ps1)"
REM ============================================================================

setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "INSTALL_PS1=%SCRIPT_DIR%install.ps1"

if exist "%INSTALL_PS1%" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_PS1%" %*
    exit /b !ERRORLEVEL!
)

echo.
echo  Qubit Agent installer
echo  No local scripts\install.ps1 found; fetching the Qubit installer...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "iex (irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/install.ps1)"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Installation failed. Try running PowerShell directly:
    echo    powershell -ExecutionPolicy Bypass -Command "iex (irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/install.ps1)"
    echo.
    exit /b 1
)
exit /b 0
