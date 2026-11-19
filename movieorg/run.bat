@echo off
REM ============================================================
REM  Movie Organizer launcher
REM  Usage:
REM     run.bat                      (will prompt for folder)
REM     run.bat "U:\war (russia)"    (dry-run + confirm)
REM     run.bat "U:\war (russia)" --apply   (skip confirmation)
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- pick a python ---
set "PY="
for %%P in (python py) do (
    where %%P >nul 2>nul && ( set "PY=%%P" & goto :gotpy )
)
echo ERROR: Python was not found on PATH.
pause
exit /b 1

:gotpy
%PY% -m pip install -q -r requirements.txt 2>nul
%PY% movie_folder_organizer.py %*
echo.
pause
endlocal
