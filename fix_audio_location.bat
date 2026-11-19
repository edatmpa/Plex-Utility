@echo off
REM ============================================================================
REM  Fix Audio Location - films that stall on the Shield ("network too slow")
REM
REM  Some MP4 files store all the picture first and the whole soundtrack after
REM  it; players then jump back and forth inside the file and stall. The fix
REM  copies picture and sound unchanged (no quality loss) into a new file with
REM  both mixed together. Originals are never changed.
REM
REM  [1] Scan       -> reports\fix_audio_timing_plan.xlsx  (read-only, ~15 min)
REM  [2] Open plan  -> review in Excel (Y = fix)
REM  [3] Dry run    -> what would be copied, how long
REM  [4] Fix in the background (low priority, pauses while someone watches)
REM  [5] Open the newest log
REM  [6] Fix one film now (drag the file or folder into the window)
REM
REM  Double-click opens the Fix Audio Location WINDOW (same steps as buttons).
REM  "fix_audio_location.bat menu" opens this text menu instead (backup).
REM ============================================================================

setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONNOUSERSITE=1"
set "SCRIPT=%~dp0scripts\fix_audio_timing.py"

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    pause & exit /b 1
)

REM The window (scripts\fix_audio_timing_gui.py) is the main way in; "fix_audio_location.bat menu" = the old text menu below.
if /i not "%~1"=="menu" (
    start "" pythonw "%~dp0scripts\fix_audio_timing_gui.py"
    exit /b 0
)

:Menu
cls
echo.
echo ============================================================================
echo  Fix Audio Location - films that stall ("network too slow")
echo ============================================================================
echo.
echo  [1]  Scan all Plex libraries     (read-only, about 15 minutes)
echo         Checks every MP4 film and episode Plex knows and writes
echo         reports\fix_audio_timing_plan.xlsx. Clear cases are marked Y already.
echo.
echo  [2]  Open the plan in Excel      Y = fix, blank or n = leave alone.
echo         Save with Ctrl+S and close Excel before step 3 or 4.
echo.
echo  [3]  Dry run                     What would be copied, and how long.
echo.
echo  [4]  Fix in the background       Opens a minimised window at lowest
echo         priority. Pauses while anyone is watching Plex. Close that
echo         window to stop; starting again skips films already done.
echo         Copies go to "^<library folder^> (mp4 fixed)" - originals stay.
echo.
echo  [5]  Open the newest log
echo.
echo  [6]  Fix one film now        Drag the film (file or folder) into this
echo         window. Checks it, then makes the fixed copy right away.
echo.
echo  [q]  Back
echo.
set "CHOICE="
set /p CHOICE="  Choice: "

if /i "%CHOICE%"=="1" goto :Scan
if /i "%CHOICE%"=="2" goto :Open
if /i "%CHOICE%"=="3" goto :DryRun
if /i "%CHOICE%"=="4" goto :Fix
if /i "%CHOICE%"=="5" goto :Log
if /i "%CHOICE%"=="6" goto :One
if /i "%CHOICE%"=="q" exit /b 0
goto :Menu

:Scan
python "%SCRIPT%" scan
pause
goto :Menu

:Open
if not exist "%~dp0reports\fix_audio_timing_plan.xlsx" (
    echo  No plan yet - run [1] first.
    pause & goto :Menu
)
start "" "%~dp0reports\fix_audio_timing_plan.xlsx"
goto :Menu

:DryRun
python "%SCRIPT%" fix
pause
goto :Menu

:Fix
python "%SCRIPT%" fix
echo.
set "OK="
set /p OK="  Start fixing these films in the background? (y/N): "
if /i not "%OK%"=="y" goto :Menu
start "Fix Audio Location - running (close to stop)" /low /min cmd /k python "%SCRIPT%" fix --go
echo.
echo  Started in a minimised window ("Fix Audio Location - running").
pause
goto :Menu

:Log
set "LOG="
for /f "delims=" %%F in ('dir /b /o-d "%~dp0reports\fix_audio_timing_log_*.csv" 2^>nul') do if not defined LOG set "LOG=%%F"
if not defined LOG (
    echo  No log yet.
    pause & goto :Menu
)
start "" "%~dp0reports\%LOG%"
goto :Menu

:One
echo.
set "FILM="
set /p FILM="  Drag the film file or folder here, then press Enter: "
if not defined FILM goto :Menu
set "FILM=%FILM:"=%"
echo.
python "%SCRIPT%" one "%FILM%"
if errorlevel 1 (pause & goto :Menu)
echo.
set "OK="
set /p OK="  Make the fixed copy now? (y/N, f = copy even if it looks fine): "
if /i "%OK%"=="y" python "%SCRIPT%" one "%FILM%" --go
if /i "%OK%"=="f" python "%SCRIPT%" one "%FILM%" --force --go
pause
goto :Menu
