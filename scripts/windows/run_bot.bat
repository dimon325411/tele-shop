@echo off
rem Runs the bot and restarts it after a crash or network loss. Close this window (or Ctrl+C, then "N") to stop.
cd /d "%~dp0\..\.."
if not exist logs mkdir logs
:loop
echo [%date% %time%] starting bot >> logs\bot.log
.venv\Scripts\python.exe -m app.dev_run
echo [%date% %time%] bot exited with code %errorlevel%, restart in 30 s >> logs\bot.log
timeout /t 30 /nobreak >nul
goto loop
