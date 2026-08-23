@echo off
setlocal
cd /d "%~dp0"
title Eternal Packaging Launcher

set "STARTED=0"

echo ==========================================
echo       ETERNAL PACKAGING - START
echo ==========================================
echo.

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if errorlevel 1 (
    echo Starting Backend...
    start "ETERNAL Backend" /D "%~dp0backend" "%ComSpec%" /k "..\.venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 8000"
    set "STARTED=1"
) else (
    echo Backend already running on port 8000.
)

timeout /t 2 /nobreak >nul

netstat -ano | findstr /R /C:":3002 .*LISTENING" >nul
if errorlevel 1 (
    echo Starting Frontend...
    start "ETERNAL Frontend" /D "%~dp0frontend" "%ComSpec%" /k "corepack yarn start"
    set "STARTED=1"
) else (
    echo Frontend already running on port 3002.
)

if "%STARTED%"=="1" (
    echo.
    echo Opening Eternal Packaging...
    timeout /t 4 /nobreak >nul
    start "" "http://localhost:3002"
) else (
    echo.
    echo Eternal Packaging is already running.
    echo Browser tab will NOT be opened again.
    timeout /t 2 /nobreak >nul
)

exit /b