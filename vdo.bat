@echo off
REM ============================================================================
REM  Video Utility Tools
REM  Launches the interactive video maintenance menu (vdo_util.py)
REM ============================================================================

chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================================
echo  Video Utility Tools
echo ============================================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    pause & exit /b 1
)

python "%~dp0scripts\vdo_util.py" %*
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
