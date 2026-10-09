@echo off
REM ============================================================
REM  Start the three Telegram bots on this computer
REM
REM  Double-click. No input needed: tokens and the model key
REM  are read from the secrets folder next to this file.
REM ============================================================

chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if not exist "секреты\ключи-ботов.local.json" (
    echo ERROR: secrets file not found:
    echo   секреты\ключи-ботов.local.json
    pause
    exit /b 1
)

REM Read the tokens from the local secrets file.
for /f "usebackq tokens=1,* delims==" %%A in (
    "python -c "import json,os;d=json.load(open('секреты/ключи-ботов.local.json',encoding='utf-8'));[print(k+'='+v) for k,v in d['tokens'].items()]""
) do (
    if /i "%%A"=="ADA_TOKEN" set "ADA_TOKEN=%%B"
    if /i "%%A"=="ANATOLY_TOKEN" set "ANATOLY_TOKEN=%%B"
    if /i "%%A"=="Social_TOKEN" set "Social_TOKEN=%%B"
)

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo.
echo === Starting bots ===
echo.
"%PY%" "код\run_all.py"
pause
