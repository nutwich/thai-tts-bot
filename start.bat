@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Thai TTS Bot

rem ---- 1. Find Python, or install it automatically (one time only) ----
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)
if not defined PY (
  echo Python not found - installing Python 3.12 from Microsoft winget...
  echo If it asks "Do you agree to all the source agreements terms?", type Y and press Enter.
  winget install -e --id Python.Python.3.12 --scope user
  set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not exist .venv\Scripts\python.exe (
  if not exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    where py >nul 2>nul || (
      echo.
      echo Python could not be installed automatically.
      echo Please install it from https://www.python.org/downloads/ then run start.bat again.
      pause
      exit /b 1
    )
  )
)

rem ---- 2. Create the bot's own environment and install packages (first run only) ----
if not exist .venv\Scripts\python.exe (
  echo Setting up the bot - this takes 1-2 minutes the first time...
  if "%PY%"=="py" (py -3.12 -m venv .venv || py -3 -m venv .venv) else ("%PY%" -m venv .venv)
  .venv\Scripts\python -m pip install --upgrade pip -q
)
echo Checking bot packages...
.venv\Scripts\python -m pip install -q -r requirements.txt

rem ---- 3. Make sure the token file exists ----
if not exist .env (
  copy .env.example .env >nul
  echo.
  echo ============================================================
  echo  Paste your bot token after DISCORD_TOKEN= in the Notepad
  echo  window that just opened, press Ctrl+S to save, close it,
  echo  then double-click start.bat again.
  echo ============================================================
  notepad .env
  pause
  exit /b 0
)

rem ---- 4. Run the bot ----
echo Starting Thai TTS bot... (close this window to stop the bot)
.venv\Scripts\python bot.py
pause
