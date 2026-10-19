@echo off
cd /d "%~dp0"
python "%~dp0.tandem_tour8\installer.py" verify
set "status=%ERRORLEVEL%"
echo.
pause
exit /b %status%
