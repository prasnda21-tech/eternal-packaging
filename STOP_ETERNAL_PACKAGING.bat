@echo off
setlocal
title Eternal Packaging - STOP

echo ==========================================
echo        ETERNAL PACKAGING - STOP
echo ==========================================
echo.

echo Stopping Backend on port 8000...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do (
    taskkill /PID %%P /T /F >nul 2>&1
)

echo Stopping Frontend on port 3002...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":3002 .*LISTENING"') do (
    taskkill /PID %%P /T /F >nul 2>&1
)

timeout /t 2 /nobreak >nul

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if errorlevel 1 (
    echo Backend stopped.
) else (
    echo WARNING: Backend port 8000 is still active.
)

netstat -ano | findstr /R /C:":3002 .*LISTENING" >nul
if errorlevel 1 (
    echo Frontend stopped.
) else (
    echo WARNING: Frontend port 3002 is still active.
)

echo.
echo Eternal Packaging stopped.
echo MongoDB service was NOT stopped.
echo OpenBuilds CONTROL was NOT touched.
echo.
pause