@echo off
REM ============================================================================
REM  Plex Tools — Unified Launcher  (Plex Media Server runs on this PC)
REM
REM  [1] DB Health & Repair  — check, repair, missing files, empty trash
REM  [2] DB Query Tools      — search, stats, duplicates, delete parts
REM  [3] Video Utilities     — subtitle audit, sub scan, SRT sync/match, organiser
REM  [4] Movie Folder Org    — translate + IMDb-year + rename loose files (AI)
REM  [5] CSV Export          — all movies/episodes with resolution and path
REM  [6] Fix Audio Location    — films that stall ("network too slow"): scan + fixed copies
REM ============================================================================

setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"

REM Double-click opens the Plex Utility WINDOW (scripts\plex_utility_gui.py, 1 Nov 2026);
REM "Plex Utility.bat menu" opens the text menu below instead (backup).
if /i not "%~1"=="menu" (
    start "" pythonw "%~dp0scripts\plex_utility_gui.py"
    if not errorlevel 1 exit /b 0
)

:Menu
cls
echo.
echo ============================================================================
echo  Plex Tools
echo ============================================================================
echo.
echo  [1]  DB Health ^& Repair  Check / Repair / Missing files / Empty trash
echo         Checks the Plex database on this PC (read-only), rebuilds it if
echo         damaged (Plex stopped), lists files Plex has that are gone from
echo         the NAS, and lets Plex empty its trash. Each step asks first.
echo.
echo  [2]  DB Query Tools      Search / Stats / Duplicates / Delete Parts
echo         Search and report on the Plex library (read-only, safe anytime),
echo         or delete a stale media part / clean up backups (write — stop Plex).
echo.
echo  [3]  Video Utilities     Subtitle Audit / Sub Scan / SRT Sync+Match / Organiser
echo         NAS video library maintenance: audit subtitle coverage, find
echo         non-English-only subs, auto-sync or match SRTs to episodes via
echo         Whisper + DeepSeek, move loose files into folders.
echo.
echo  [4]  Movie Folder Org    Translate + IMDb-year + rename loose files (AI)
echo         Translates a foreign title to English, finds the release year,
echo         and files each loose movie into its own folder. Dry-run first.
echo.
echo  [5]  CSV Export          Movies ^& episodes -^> reports\plex_report_^<date^>.csv
echo.
echo  [6]  Fix Audio Location    Films that stall on the Shield ("network too slow")
echo         Finds MP4 files whose sound is stored apart from the picture and
echo         makes fixed copies in the background (originals never changed).
echo.
echo  [q]  Quit
echo.
set "CHOICE="
set /p CHOICE="  Choice: "

if /i "%CHOICE%"=="1" goto :Repair
if /i "%CHOICE%"=="2" goto :Query
if /i "%CHOICE%"=="3" goto :Video
if /i "%CHOICE%"=="4" goto :MovieOrg
if /i "%CHOICE%"=="5" goto :Export
if /i "%CHOICE%"=="6" goto :FixAudioTiming
if /i "%CHOICE%"=="q" goto :Exit

echo.
echo  [!] Invalid choice.
pause
goto :Menu

:Repair
call "%~dp0DBRepair\Windows\DBRepair-Windows-Launcher.bat"
goto :Menu

:Query
call "%~dp0plex.bat"
goto :Menu

:Video
call "%~dp0vdo.bat"
goto :Menu

:MovieOrg
call "%~dp0movieorg.bat"
goto :Menu

:Export
call "%~dp0export_report.bat"
goto :Menu

:FixAudioTiming
call "%~dp0fix_audio_location.bat"
goto :Menu

:Exit
echo.
echo  Goodbye.
echo.
exit /b 0
