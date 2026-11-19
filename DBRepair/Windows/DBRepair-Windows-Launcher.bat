@echo off
REM Plex Media Server Database Health ^& Repair - Launcher v5
REM Plex runs on this PC: the live database is checked in place (nothing copied).
REM   [1] Check     -> scripts\plex_health.py check    (read-only, Plex may run)
REM   [2] Repair    -> DBRepair-Windows.bat             (Plex must be stopped)
REM   [3] Missing   -> scripts\plex_health.py missing  (read-only report)
REM   [4] Trash     -> scripts\plex_health.py trash    (Plex API, asks first)

SetLocal EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "HEALTH=%~dp0..\..\scripts\plex_health.py"
set "REPORTS=%~dp0..\..\reports"

:Menu
cls
echo.
echo ============================================================
echo  Plex Database Health ^& Repair  (Plex on this PC)
echo ============================================================
echo.
echo  [1] Check database        read-only, safe while Plex runs
echo  [2] Repair database       rebuilds the database - STOP PLEX FIRST
echo  [3] Find missing files    files in Plex that are gone from the NAS
echo  [4] Empty Plex trash      remove items Plex marked as deleted
echo  [5] Back
echo.
set "CHOICE="
set /p CHOICE="Enter your choice (1-5): "

if "%CHOICE%"=="1" goto Check
if "%CHOICE%"=="2" goto Repair
if "%CHOICE%"=="3" goto Missing
if "%CHOICE%"=="4" goto Trash
if "%CHOICE%"=="5" exit /b 0

echo.
echo [ERROR] Invalid choice. Enter 1-5 and press Enter.
pause
goto Menu

:Check
cls
echo.
echo ============================================================
echo  1: Check database
echo ============================================================
echo  - Runs quick_check and integrity_check on the library and blobs
echo    databases with Plex's own SQLite, read-only.
echo  - Takes a minute or two. Report saved in: reports\
echo.
python "%HEALTH%" check
pause
goto Menu

:Repair
cls
echo.
echo ============================================================
echo  2: Repair database
echo ============================================================
echo  - Exports both Plex databases, rebuilds them, verifies and
echo    reindexes, then swaps the rebuilt ones in.
echo  - The old databases are kept in the Databases\dbtmp folder.
echo.
echo  IMPORTANT: Exit Plex Media Server first (tray icon -^> Exit).
echo  Only needed when [1] Check reports a FAIL.
echo.
set "CONFIRM="
set /p CONFIRM="Continue? (Y/N): "
if /i not "%CONFIRM%"=="Y" (
    echo Operation cancelled.
    pause
    goto Menu
)
call "%~dp0DBRepair-Windows.bat"
goto Menu

:Missing
cls
echo.
echo ============================================================
echo  3: Find missing files
echo ============================================================
echo  - Checks every film/episode file and external subtitle Plex knows
echo    about and lists the ones gone from the NAS (dead links). Read-only.
echo  - Enter below = choose a library from a list (Enter there = all).
echo  - Report saved in: reports\
echo.
set "ONLY="
set /p ONLY="Limit to a folder (e.g. Japanese\Samurai or V:\Russian) or Enter for ALL: "
echo.
if "%ONLY%"=="" (
    python "%HEALTH%" missing --library ?
) else (
    python "%HEALTH%" missing --only "%ONLY%"
)
pause
goto Menu

:Trash
cls
echo.
echo ============================================================
echo  4: Empty Plex trash
echo ============================================================
echo  - Lists, per library, the items Plex has marked as deleted.
echo  - You choose the libraries; Plex itself removes them (web API).
echo  - The database is never edited directly. Plex must be running.
echo.
python "%HEALTH%" trash
pause
goto Menu
