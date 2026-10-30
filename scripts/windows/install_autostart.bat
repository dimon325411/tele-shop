@echo off
rem Starts run_bot.bat automatically when you log in to Windows (Task Scheduler, no admin rights needed).
cd /d "%~dp0\..\.."
schtasks /Create /F /SC ONLOGON /TN "OlxBot" /TR "\"%CD%\scripts\windows\run_bot.bat\"" /RL LIMITED
if errorlevel 1 ( echo Could not create the task. & pause & exit /b 1 )
echo Autostart enabled (task "OlxBot"). Remove: schtasks /Delete /TN OlxBot /F
pause
