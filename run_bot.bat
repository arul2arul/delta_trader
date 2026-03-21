@echo off
setlocal

:: ─────────────────────────────────────────────────────────────────
:: Delta Trader — Windows Launcher
:: 1. Runs startup health check (API keys, Telegram, Gemini, Delta)
:: 2. Only starts the trading bot if ALL checks pass
::
:: Setup:
::   1. Set SCRIPT_DIR below to your cloned repo path
::   2. Leave PYTHON_EXE as "python" if Python is in your PATH,
::      or set the full path e.g. C:\Python312\python.exe
:: ─────────────────────────────────────────────────────────────────

set PYTHON_EXE=python
set SCRIPT_DIR=%~dp0

:: Change to the repo directory
cd /d "%SCRIPT_DIR%"

echo.
echo ============================================================
echo   Delta Trader — Startup Health Check
echo   %date% %time%
echo ============================================================
echo.

:: ── Step 1: Health Check ─────────────────────────────────────────
echo [STEP 1/2] Running connectivity and credential checks...
"%PYTHON_EXE%" test_connectivity.py
IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ABORTED] Health check FAILED. Trading bot will NOT start.
    echo           Fix the issues shown above, then re-run this script.
    echo.
    echo [%date% %time%] ABORTED: health check failed >> automation_log.txt
    pause
    exit /b 1
)

echo.
echo [STEP 2/2] All checks passed. Starting trading bot...
echo.

:: ── Step 2: Run the trading bot ──────────────────────────────────
echo [%date% %time%] Starting Delta Trader >> automation_log.txt
"%PYTHON_EXE%" analyze_0dte.py >> automation_log.txt 2>&1
echo [%date% %time%] Script finished (exit code %ERRORLEVEL%) >> automation_log.txt

echo.
echo Trading session ended. Check automation_log.txt for details.
pause

endlocal
