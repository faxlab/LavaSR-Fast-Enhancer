@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>&1
if errorlevel 1 (
  echo Python launcher "py" was not found. Install Python 3.10+ and retry.
  goto :fail
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv .venv
  if errorlevel 1 goto :fail
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :fail

set HF_HUB_DISABLE_PROGRESS_BARS=1
python -c "import LavaSR, PySide6, soundfile" 1>nul 2>nul
if errorlevel 1 (
  echo Installing dependencies [first run only]...
  python -m pip install --upgrade pip
  if errorlevel 1 goto :fail
  python -m pip install -r requirements.txt
  if errorlevel 1 goto :fail
)

if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" lavasr_gui.py
) else (
  start "" pythonw lavasr_gui.py
)
if errorlevel 1 goto :fail
goto :eof

:fail
echo.
echo Launch failed. Review the error above, then run launch.bat again.
pause
exit /b 1
