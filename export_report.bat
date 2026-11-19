@echo off
REM ============================================================================
REM  One-shot Plex CSV export - Movies & TV Episodes
REM  Reads the live Plex database on this PC (read-only) and writes
REM  reports\plex_report_<date>.csv
REM ============================================================================

setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"

echo.
echo ============================================================================
echo  Plex CSV Export
echo ============================================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    pause & exit /b 1
)

python "%~dp0scripts\export_report.py" %*
set "EXIT_CODE=%errorlevel%"

echo.
if %EXIT_CODE% equ 0 (
    echo [+] Done.
) else (
    echo [!] Completed with errors (code: %EXIT_CODE%)
)
echo.
pause
exit /b %EXIT_CODE%
