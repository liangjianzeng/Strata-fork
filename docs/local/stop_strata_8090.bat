@echo off
REM [!] stop the Strata service listening on 8090 (kills the whole process tree)

set FOUND=
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8090 .*LISTENING"') do set FOUND=%%P
if "%FOUND%"=="" (
    echo [i] nothing listening on 8090
    pause
    exit /b 0
)
echo [i] killing pid %FOUND% (tree)
taskkill /F /T /PID %FOUND%
echo [i] done - the port is free
pause
