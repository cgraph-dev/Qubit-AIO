@echo off
REM ============================================================================
REM Qubit Windows release bootstrap (CMD wrapper)
REM ============================================================================
REM Launches the Qubit-owned PowerShell bootstrap (scripts\bootstrap.ps1). It
REM verifies install.ps1 + qubit-release.zip against SHA256SUMS and runs only
REM the extracted installer copy. It never reads a source checkout and never
REM downloads or installs the upstream Hermes/OpenClaw tree.
REM
REM Usage (from a checkout or beside a downloaded bootstrap.ps1):
REM   scripts\bootstrap.cmd -Help
REM   scripts\bootstrap.cmd -DryRun C:\releases\v0.0.7
REM   scripts\bootstrap.cmd -QubitHome "%%LOCALAPPDATA%%\qubit" C:\releases\v0.0.7
REM
REM Fetched form (a release base is required):
REM   powershell -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/bootstrap.ps1))) -Help"
REM ============================================================================

setlocal EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
set "BOOTSTRAP_PS1=%SCRIPT_DIR%bootstrap.ps1"

if exist "%BOOTSTRAP_PS1%" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%BOOTSTRAP_PS1%" %*
    exit /b !ERRORLEVEL!
)

echo.
echo  Qubit bootstrap installer
echo  No local scripts\bootstrap.ps1 found; fetching the Qubit bootstrap...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/bootstrap.ps1))) %*"

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo  Bootstrap failed. Try running PowerShell directly:
    echo    powershell -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm https://github.com/cgraph-dev/Qubit/raw/production/scripts/bootstrap.ps1))) -Help"
    echo.
    exit /b 1
)
exit /b 0
