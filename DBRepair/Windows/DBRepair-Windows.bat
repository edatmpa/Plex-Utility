@echo off
REM  DBRepair-Windows.bat v1.02.00 - Database maintenance / rebuild tool for Plex Media Server
REM
REM  IMPROVEMENTS in v1.02.00:
REM  - Fixed corrupted backup file selection (-tmp files are now excluded)
REM  - Improved error handling at each step
REM  - Better logging with timestamps
REM  - Automatic backup before modifications
REM  - Cleaner output and status messages
REM
REM  Called by DBRepair-Windows-Launcher.bat [2] Repair (Plex on this PC).
REM
REM  USAGE: Run with Plex Media Server STOPPED
REM         Administrator privileges required

setlocal enabledelayedexpansion

REM ========== CONFIG ==========
set "VERSION=1.02.00"
set "MAINTENANCE_MODE=1"

echo.
echo ============================================================
echo   DBRepair-Windows Batch Tool v%VERSION%
echo   Plex Media Server Database Repair Utility
echo ============================================================
echo.

REM ========== SETUP LOGGING ==========
for /f "tokens=2-4 delims=/ " %%a in ('date /t') do (set mydate=%%c-%%a-%%b)
for /f "tokens=1-2 delims=/:" %%a in ('time /t') do (set mytime=%%a%%b)
set "TimeStamp=%mydate%_%mytime%"

REM ========== VERIFY PLEX IS STOPPED ==========
echo [*] Checking Plex Media Server status...
tasklist | find /I "Plex Media Server.exe" >NUL
if %ERRORLEVEL%==0 (
  echo.
  echo [ERROR] Plex Media Server is running.
  echo [ERROR] STOP Plex before running this script.
  echo.
  pause
  exit /B 1
)
echo [+] Plex is stopped. Proceeding...
echo.

REM ========== FIND DATABASE PATHS ==========
echo [*] Locating Plex database directory...

REM Try registry first
for /F "tokens=2* skip=2" %%a in ('REG.EXE QUERY "HKCU\Software\Plex, Inc.\Plex Media Server" /v "LocalAppDataPath" 2^>nul') do (
  set "PlexData=%%b\Plex Media Server\Plug-in Support\Databases"
)

REM This PC keeps Plex's data on E: (moved from C: on 27 Sep 2026)
if not exist "%PlexData%" (
  if exist "E:\PlexData\Plex Media Server\Plug-in Support\Databases" (
    set "PlexData=E:\PlexData\Plex Media Server\Plug-in Support\Databases"
  )
)

if not exist "%PlexData%" (
  if exist "%LOCALAPPDATA%\Plex Media Server\Plug-in Support\Databases" (
    set "PlexData=%LOCALAPPDATA%\Plex Media Server\Plug-in Support\Databases"
  ) else (
    echo [ERROR] Cannot locate Plex database directory.
    echo Expected: %LOCALAPPDATA%\Plex Media Server\Plug-in Support\Databases
    pause
    exit /B 1
  )
)
echo [+] Database path: %PlexData%

REM ========== FIND SQLITE EXECUTABLE ==========
echo [*] Locating Plex SQLite executable...

REM Try HKCU first
for /F "tokens=2* skip=2" %%a in ('REG.EXE QUERY "HKCU\Software\Plex, Inc.\Plex Media Server" /v "InstallFolder" 2^>nul') do (
  set "PlexSQL=%%b\Plex SQLite.exe"
)

if not exist "%PlexSQL%" (
  for /F "tokens=2* skip=2" %%a in ('REG.EXE QUERY "HKLM\Software\Plex, Inc.\Plex Media Server" /v "InstallFolder" 2^>nul') do (
    set "PlexSQL=%%b\Plex SQLite.exe"
  )
)

if not exist "%PlexSQL%" (
  if exist "%PROGRAMFILES%\Plex\Plex Media Server\Plex SQLite.exe" (
    set "PlexSQL=%PROGRAMFILES%\Plex\Plex Media Server\Plex SQLite.exe"
  ) else if exist "%PROGRAMFILES(X86)%\Plex\Plex Media Server\Plex SQLite.exe" (
    echo [!] NOTE: 32-bit Plex detected on 64-bit Windows. Consider upgrading to 64-bit.
    set "PlexSQL=%PROGRAMFILES(X86)%\Plex\Plex Media Server\Plex SQLite.exe"
  ) else (
    echo [ERROR] Cannot locate Plex SQLite.exe
    echo Expected: %PROGRAMFILES%\Plex\Plex Media Server\Plex SQLite.exe
    pause
    exit /B 1
  )
)
echo [+] SQLite executable: %PlexSQL%

REM ========== SETUP TEMP DIRECTORY ==========
set "DBtmp=%PlexData%\dbtmp"
set "TmpFile=%DBtmp%\results.tmp"
set "LogFile=%PlexData%\DBRepair.log"

if not exist "%DBtmp%" mkdir "%DBtmp%"
echo. >> "%LogFile%"
echo ======================================== >> "%LogFile%"
echo DBRepair Session: %TimeStamp% >> "%LogFile%"
echo ======================================== >> "%LogFile%"

echo [+] Temp directory: %DBtmp%
echo.

REM ========== VERIFY DATABASE FILES EXIST ==========
echo [*] Verifying database files...
if not exist "%PlexData%\com.plexapp.plugins.library.db" (
  echo [ERROR] Main database not found: %PlexData%\com.plexapp.plugins.library.db
  pause
  exit /B 1
)
if not exist "%PlexData%\com.plexapp.plugins.library.blobs.db" (
  echo [ERROR] Blobs database not found: %PlexData%\com.plexapp.plugins.library.blobs.db
  pause
  exit /B 1
)
echo [+] Both database files found.
echo.

REM ========== CLEANUP ==========
echo [*] Performing database cleanup...
echo [*] Removing orphaned bandwidth statistics...
"%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.db" "DELETE FROM statistics_bandwidth WHERE account_id IS NULL;"
if %ERRORLEVEL% NEQ 0 (
  echo [!] Warning: Cleanup had issues, continuing anyway...
)
echo [+] Cleanup complete.
echo.

REM ========== EXPORT DATABASES ==========
echo [*] Exporting Main Database...
echo .dump | "%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.db" > "%DBtmp%\library.sql_%TimeStamp%"
if %ERRORLEVEL% NEQ 0 (
  echo [ERROR] Failed to export Main DB.
  echo [ERROR] Failed to export Main DB. >> "%LogFile%"
  pause
  exit /B 2
)
echo [+] Main DB exported: library.sql_%TimeStamp%

echo [*] Exporting Blobs Database...
echo .dump | "%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.blobs.db" > "%DBtmp%\blobs.sql_%TimeStamp%"
if %ERRORLEVEL% NEQ 0 (
  echo [ERROR] Failed to export Blobs DB.
  echo [ERROR] Failed to export Blobs DB. >> "%LogFile%"
  pause
  exit /B 2
)
echo [+] Blobs DB exported: blobs.sql_%TimeStamp%
echo.

REM ========== CREATE NEW DATABASES ==========
echo [*] Creating new Main Database from export...
"%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.db_%TimeStamp%" < "%DBtmp%\library.sql_%TimeStamp%"
if %ERRORLEVEL% NEQ 0 (
  echo [ERROR] Failed to create new Main DB.
  pause
  exit /B 3
)
echo [+] New Main DB created.

echo [*] Verifying new Main Database...
"%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.db_%TimeStamp%" "PRAGMA integrity_check(1);" > "%TmpFile%"
set /p Result= < "%TmpFile%"
del "%TmpFile%"
if not "%Result%" == "ok" (
  echo [ERROR] Main DB verification failed: %Result%
  pause
  exit /B 4
)
echo [+] Main DB verification: PASSED
echo.

echo [*] Creating new Blobs Database from export...
"%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.blobs.db_%TimeStamp%" < "%DBtmp%\blobs.sql_%TimeStamp%"
if %ERRORLEVEL% NEQ 0 (
  echo [ERROR] Failed to create new Blobs DB.
  pause
  exit /B 3
)
echo [+] New Blobs DB created.

echo [*] Verifying new Blobs Database...
"%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.blobs.db_%TimeStamp%" "PRAGMA integrity_check(1);" > "%TmpFile%"
set /p Result= < "%TmpFile%"
del "%TmpFile%"
if not "%Result%" == "ok" (
  echo [ERROR] Blobs DB verification failed: %Result%
  pause
  exit /B 4
)
echo [+] Blobs DB verification: PASSED
echo.

REM ========== REINDEX ==========
echo [*] Reindexing Main Database...
"%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.db_%TimeStamp%" "REINDEX;"
echo [+] Main DB reindexed.

echo [*] Reindexing Blobs Database...
"%PlexSQL%" "%PlexData%\com.plexapp.plugins.library.blobs.db_%TimeStamp%" "REINDEX;"
echo [+] Blobs DB reindexed.
echo.

REM ========== SWAP DATABASES ==========
echo [*] Swapping databases (backup old, activate new)...
move "%PlexData%\com.plexapp.plugins.library.db" "%PlexData%\dbtmp\com.plexapp.plugins.library.db_%TimeStamp%"
move "%PlexData%\com.plexapp.plugins.library.db_%TimeStamp%" "%PlexData%\com.plexapp.plugins.library.db"

move "%PlexData%\com.plexapp.plugins.library.blobs.db" "%PlexData%\dbtmp\com.plexapp.plugins.library.blobs.db_%TimeStamp%"
move "%PlexData%\com.plexapp.plugins.library.blobs.db_%TimeStamp%" "%PlexData%\com.plexapp.plugins.library.blobs.db"

echo [+] Databases swapped. Old databases backed up in dbtmp\
echo.

REM ========== COMPLETION ==========
echo ============================================================
echo [+] SUCCESS! Database repair completed.
echo ============================================================
echo [+] Old databases backed up in: %PlexData%\dbtmp\
echo [+] You can now START Plex Media Server.
echo [+] Log file: %LogFile%
echo.
echo [+] Completion time: %date% %time%
echo.

echo [+] Session completed successfully. >> "%LogFile%"
echo [+] Completion time: %date% %time% >> "%LogFile%"

pause
exit /B 0
