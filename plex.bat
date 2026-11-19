@echo off
REM ============================================================================
REM  Plex Database Tool  (Plex Media Server runs on this PC)
REM
REM  Read-only commands  -> plex_search.py  (safe while Plex is running)
REM  Write commands      -> plex_db.py      (Plex must be stopped; checked)
REM
REM  The live database is used in place - nothing is mounted or copied.
REM  Location: scripts\plex_config.py (override with PLEX_DATA in .env).
REM  Run with no arguments for interactive mode with branch selection.
REM ============================================================================

setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "SEARCH_SCRIPT=%~dp0scripts\plex_search.py"
set "DB_SCRIPT=%~dp0scripts\plex_db.py"

echo.
echo ============================================================================
echo  Plex Database Tool
echo ============================================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    pause & exit /b 1
)

set "CMD=%~1"
if /i "%CMD%"=="help" goto :show_help
if /i "%CMD%"=="delete-part"     set "ROUTE=write" & goto :run
if /i "%CMD%"=="cleanup-backups" set "ROUTE=write" & goto :run
if not "%CMD%"=="" set "ROUTE=read" & goto :run

echo  What would you like to do?
echo.
echo   [1] Search ^& Reports      (read-only - safe while Plex is running)
echo         Title/file/tag search, recent additions, item details, library
echo         stats, duplicates, orphaned parts, integrity check, schema browser.
echo.
echo   [2] Database Management   (write operations - STOP PLEX FIRST)
echo         Delete a stale/orphaned media part (backup + dry-run + typed YES
echo         confirmation), or clean up old .backup.* files.
echo.
echo   [q] Quit
echo.
set /p "BRANCH=  Choice: "
echo.
if /i "!BRANCH!"=="q" exit /b 0
if "!BRANCH!"=="1" set "ROUTE=read"  & goto :run
if "!BRANCH!"=="2" set "ROUTE=write" & goto :run
echo [!] Invalid choice.
pause & exit /b 1

:run
if "!ROUTE!"=="write" (
    if "%CMD%"=="" (
        python "%DB_SCRIPT%" --interactive
    ) else (
        python "%DB_SCRIPT%" %*
    )
) else (
    python "%SEARCH_SCRIPT%" %*
)
set "EXIT_CODE=!errorlevel!"

echo.
if !EXIT_CODE! equ 0 (
    echo [+] Done.
) else (
    echo [!] Completed with errors (code: !EXIT_CODE!)
)
echo.
pause
exit /b !EXIT_CODE!

:show_help
echo.
echo INTERACTIVE MODE (no arguments):
echo   plex.bat                            Prompts for Search or Management branch
echo.
echo READ-ONLY commands  (plex_search.py - safe while Plex is running):
echo   plex.bat search "title"
echo   plex.bat search --type movie --year 1961 "Ronin"
echo   plex.bat find-file "V:\Japanese\Samurai\47 Ronin"
echo   plex.bat recent --limit 30
echo   plex.bat info ^<metadata ID^>
echo   plex.bat tag --type director "Kurosawa"
echo   plex.bat stats
echo   plex.bat duplicates
echo   plex.bat orphaned
echo   plex.bat check
echo   plex.bat list-sections
echo.
echo WRITE commands  (plex_db.py - STOP PLEX FIRST):
echo   plex.bat delete-part ^<Part ID^>
echo   plex.bat cleanup-backups
echo.
pause
exit /b 0
