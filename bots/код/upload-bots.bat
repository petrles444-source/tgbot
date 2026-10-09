@echo off
REM ============================================================
REM  Upload bot files to PythonAnywhere
REM
REM  Run this after changing any .py or .json in this folder.
REM  It uploads via the PythonAnywhere API - NOT via FTP.
REM  PythonAnywhere has no FTP for free accounts, and the API
REM  token already lives in credentials.json next to this script.
REM
REM  Tokens are NOT in this file and must never be added here.
REM  Each bot gets its token from an environment variable at
REM  runtime; see bots.json.
REM ============================================================

REM Switch console to UTF-8 so the Russian output from Python is
REM readable instead of mojibake.
chcp 65001 >nul
setlocal

REM %~dp0 is this folder. The virtualenv is three levels up:
REM   bots\ -> pythonanywhere\ -> hosting\ -> project root
set "HERE=%~dp0"
set "PY=%HERE%..\..\..\.venv\Scripts\python.exe"

echo.
echo === Bot upload to PythonAnywhere ===
echo.

if not exist "%PY%" (
    echo ERROR: python not found at
    echo   %PY%
    echo.
    echo The virtualenv is missing. Create it with:
    echo   python -m venv .venv
    echo.
    pause
    exit /b 1
)

REM Without this Python raises UnicodeEncodeError on Cyrillic.
set PYTHONIOENCODING=utf-8

REM Step 1: verify the upload list matches this folder. A file
REM that exists here but is missing from UPLOAD_NAMES would be
REM silently skipped, and the bot would fail on the server.
echo --- Step 1/3: check what will be uploaded ---
"%PY%" "%HERE%..\deploy.py" --check
if errorlevel 1 (
    echo.
    echo Check failed. See the message above.
    pause
    exit /b 1
)

echo.
echo --- Step 2/3: uploading files ---
"%PY%" "%HERE%..\deploy.py" --upload
if errorlevel 1 (
    echo.
    echo Upload failed. See the message above.
    pause
    exit /b 1
)

echo.
echo --- Step 3/3: reloading the web app ---
REM The reload restarts the running bot so it picks up the new
REM files without anyone editing anything by hand. If the web app
REM fails, the upload still succeeded - only the restart is lost.
"%PY%" "%HERE%..\deploy.py" --webapp
if errorlevel 1 (
    echo.
    echo NOTE: files are uploaded, but the web app did not reload.
    echo The console bot is unaffected - it restarts on its own.
)

echo.
echo ============================================================
echo  Done. Restart the bots in the PythonAnywhere console:
echo.
echo    cd /home/HostMoon6/zstatus
echo    python3 -u run_all.py
echo.
echo  Tokens are read from environment variables, so export them
echo  again in the console if this is a new session.
echo ============================================================
echo.

pause