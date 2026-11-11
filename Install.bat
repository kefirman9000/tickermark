@echo off
setlocal
cd /d "%~dp0"
title Stock Logo Downloader - Setup
echo.
echo  === Stock Logo Downloader: one-time setup ===
echo.

rem ---- find a working Python ----
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"

rem ---- none? install with winget ----
if not defined PY (
  echo Python not found. Installing Python 3.12 - this takes a minute...
  where winget >nul 2>nul
  if errorlevel 1 (
    echo.
    echo  Please install Python from https://www.python.org/downloads/
    echo  ^(tick "Add python.exe to PATH"^), then run Install.bat again.
    goto :fail
  )
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  echo.
  echo  Python was installed but this window can't see it yet.
  echo  Close this window and run Install.bat once more.
  goto :fail
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating environment...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)
echo Installing packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail
echo Installing optional backup (yfinance)...
".venv\Scripts\python.exe" -m pip install yfinance >nul 2>nul

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1" -Dir "%~dp0."
echo.
echo  Done! A "Stock Logo Downloader" shortcut is on your desktop.
echo  Opening it now...
call "%~dp0Run.bat"
timeout /t 3 >nul
exit /b 0

:fail
echo.
echo  Setup did not finish. See the message above.
pause
exit /b 1
