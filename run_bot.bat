@echo off
setlocal

:: Define paths
set PYTHON_EXE=C:\Users\aruls\AppData\Local\Programs\Python\Python312\python.exe
set SCRIPT_DIR=C:\code\delta_trader
set SCRIPT_NAME=analyze_0dte.py

:: Change to script directory
cd /d "%SCRIPT_DIR%"

:: Run the bot and log output
echo [%date% %time%] Starting Delta Trader Automation >> automation_log.txt
"%PYTHON_EXE%" "%SCRIPT_NAME%" >> automation_log.txt 2>&1
echo [%date% %time%] Script Finished >> automation_log.txt

endlocal
