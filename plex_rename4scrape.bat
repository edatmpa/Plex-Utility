@echo off
REM =====================================================================
REM  plex_rename4scrape.bat
REM  Runs scripts\plex_rename4scrape.py inside a project venv.
REM  Venv is auto-created in this folder (venv\) on first run.
REM =====================================================================
setlocal

set "PLEX_ROOT=%~dp0."
set VENV_DIR=%PLEX_ROOT%\venv
set SCRIPT=%PLEX_ROOT%\scripts\plex_rename4scrape.py
set REQUIREMENTS=%PLEX_ROOT%\requirements_rename4scrape.txt
set VENV_PYTHON=%VENV_DIR%\Scripts\python.exe
set VENV_PIP=%VENV_DIR%\Scripts\pip.exe

REM -- Work from the project root so .env is always found correctly ----
E:
cd /d "%PLEX_ROOT%"

REM -- Create venv if it doesn't exist ---------------------------------
if not exist "%VENV_PYTHON%" (
    echo [INFO] Virtual environment not found. Creating at %VENV_DIR% ...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        echo         Make sure Python 3.9+ is installed and on PATH.
        pause
        exit /b 1
    )
    echo [INFO] Virtual environment created.
)

REM -- Activate venv ---------------------------------------------------
call "%VENV_DIR%\Scripts\activate.bat"

REM -- Install / refresh dependencies ----------------------------------
if exist "%REQUIREMENTS%" (
    echo [INFO] Installing/updating requirements from %REQUIREMENTS% ...
    "%VENV_PIP%" install -q -r "%REQUIREMENTS%"
    if errorlevel 1 (
        echo [WARN] pip install returned errors — continuing anyway.
    )
) else (
    echo [WARN] Requirements file not found: %REQUIREMENTS%
)

REM -- Run the script --------------------------------------------------
echo [INFO] Starting Plex Media Renamer...
echo.
"%VENV_PYTHON%" "%SCRIPT%"

echo.
echo [INFO] Process finished.
pause
endlocal
