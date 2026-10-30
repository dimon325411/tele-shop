@echo off
rem One-time setup on Windows: virtual environment + dependencies. Run from the project folder or by double-click.
cd /d "%~dp0\..\.."
where py >nul 2>nul || (echo Python not found. Install Python 3.12+ from python.org ^(tick "Add to PATH"^). & pause & exit /b 1)
if not exist .venv ( py -3 -m venv .venv || (echo venv failed & pause & exit /b 1) )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || (echo pip install failed & pause & exit /b 1)
if not exist .env ( copy .env.example .env >nul & echo Created .env - fill in BOT_TOKEN, ADMIN_IDS, PERSONAL_MODE=true, ENABLED_SOURCES=mock,olx, AI_* )
if not exist app\data\olx_categories.json ( echo Next: python scripts\olx_categories.py  ^(once, finds OLX category ids^) )
echo.
echo Done. Check AI: .venv\Scripts\python scripts\ai_check.py
echo Start bot:  scripts\windows\run_bot.bat
pause
