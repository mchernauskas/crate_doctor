@echo off
REM Double-click me. Sets up crate_doctor on Windows.
cd /d "%~dp0"
echo.
echo crate_doctor setup for Windows
echo =========================
echo.
where py >nul 2>&1
if %errorlevel%==0 (
  set PY=py -3
  goto run
)
where python >nul 2>&1
if %errorlevel%==0 (
  REM the Microsoft Store stub is a fake 'python' that opens the Store instead of running
  python -c "import sys" >nul 2>&1
  if %errorlevel%==0 (
    set PY=python
    goto run
  )
)
echo Python 3 is not installed.
echo.
echo Get it from  https://www.python.org/downloads/windows/
echo.
echo IMPORTANT: on the first installer screen tick the box
echo    [x] Add python.exe to PATH
echo then run the installer, and double-click this file again.
echo.
pause
exit /b 1

:run
%PY% crate_doctor.py setup
echo.
echo Done.
pause
