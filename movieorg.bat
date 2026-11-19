@echo off
REM ============================================================================
REM  Movie Folder Organizer (AI)  —  direct launcher
REM
REM  Finds loose video files, identifies each film on TMDB with PlexRename's
REM  code and moves it into a Plex-standard folder + file name, one film at a
REM  time after you accept the proposal.
REM
REM  Also reachable from:  Plex Utility.bat  ->  [3] Video Utilities  ->  [6]
REM ============================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================================
echo  Movie Folder Organizer (AI)
echo ============================================================================
echo   - Finds loose video files directly inside the folder you choose.
echo   - Finds each film on TMDB the way PlexRename does (Plex's match, TMDB search,
echo     DeepSeek choosing among TMDB results) and proposes the Plex-standard name:
echo       English Title (Year) {tmdb-ID} Native title [RES]\  - file renamed too,
echo       parts (CD1/CD2) kept together, subtitles follow with a language code.
echo   - One film at a time: Enter = accept, e = type a name, i = type a TMDB/IMDb id,
echo     s = skip, q = stop. Nothing moves until you accept.
echo   - Undo: PlexRename's 'Undo a rename...' button (logs in E:\PlexRename\logs).
echo   - Options: --preview (show only), --old-style (previous naming).
echo ============================================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    pause & exit /b 1
)

REM Ensure the single dependency is present (silent if already installed).
python -m pip install -q -r movieorg\requirements.txt >nul 2>&1

python "%~dp0movieorg\movie_folder_organizer.py" %*
set "EXIT_CODE=%errorlevel%"

echo.
if %EXIT_CODE% equ 0 (
    echo [+] Done.
) else (
    echo [!] Completed with items needing attention (code: %EXIT_CODE%)
)
echo.
pause
endlocal
