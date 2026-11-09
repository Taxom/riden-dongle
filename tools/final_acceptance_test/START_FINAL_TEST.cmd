@echo off
setlocal
cd /d "%~dp0"
echo RD6006P final test: firmware taxom.9+ required.
echo Run without a load. Output, voltage and current will change.
echo Close TestController and disable other writers before starting.
echo.
where py >nul 2>&1
if errorlevel 1 goto fallback
py -3 "%~dp0rd6006_final_test.py" %*
set "rd_final_exit=%errorlevel%"
goto finished
:fallback
where python >nul 2>&1
if errorlevel 1 goto missing
python "%~dp0rd6006_final_test.py" %*
set "rd_final_exit=%errorlevel%"
goto finished
:missing
echo Python 3.9+ was not found. Install Python 3 for Windows.
set "rd_final_exit=2"
:finished
echo.
echo Exit code: %rd_final_exit%
pause
exit /b %rd_final_exit%
