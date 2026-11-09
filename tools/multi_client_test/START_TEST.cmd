@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 "%~dp0rd6006_multi_client_test.py" %*
    goto finished
)
where python >nul 2>&1
if not errorlevel 1 (
    python "%~dp0rd6006_multi_client_test.py" %*
    goto finished
)
echo Python 3.9+ was not found. Install Python 3 for Windows and run again.
:finished
echo.
pause
