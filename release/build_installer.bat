@echo off
setlocal
cd /d "%~dp0\.."

set "PS_ARGS=%*"
set "HAS_ISCC="
where ISCC.exe >nul 2>nul && set "HAS_ISCC=1"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "HAS_ISCC=1"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "HAS_ISCC=1"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "HAS_ISCC=1"
if not defined HAS_ISCC (
  echo Inno Setup not found on PATH.
  choice /C YN /N /M "Install Inno Setup now (admin prompt)? [Y/N]: "
  if not errorlevel 2 (
    set "PS_ARGS=-InstallInnoSetupIfMissing %*"
  )
)

powershell -NoProfile -ExecutionPolicy Bypass -File ".\release\build_installer.ps1" %PS_ARGS%
set "code=%ERRORLEVEL%"

if not "%code%"=="0" (
  echo.
  echo Build failed with exit code %code%.
)

pause
exit /b %code%
