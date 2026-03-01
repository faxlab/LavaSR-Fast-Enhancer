@echo off
setlocal
cd /d "%~dp0\.."

powershell -NoProfile -ExecutionPolicy Bypass -File ".\release\build_installer.ps1" %*
set "code=%ERRORLEVEL%"

if not "%code%"=="0" (
  echo.
  echo Build failed with exit code %code%.
)

pause
exit /b %code%
