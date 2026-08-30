@echo off
rem Launches the NewWorldSupport Discord bot.
rem PYTHONUTF8 matters on Windows: the bot's startup logging contains emoji,
rem and on a cp1252 console the first print raises UnicodeEncodeError and
rem kills the process before it ever connects.
setlocal
set "REPO=%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
rem Unbuffered stdout is load-bearing, not cosmetic: output here is redirected
rem to a file, so Python block-buffers print(). discord.py's own logging goes
rem to stderr and still appears, which makes a buffered run look ALIVE-but-
rem broken -- the watchdog waits for the "Discord bot connected as" print() and
rem would report PARTIAL forever on a perfectly healthy bot, and could not tell
rem that state apart from a genuinely hung one.
set "PYTHONUNBUFFERED=1"

if not exist "%~dp0logs" mkdir "%~dp0logs"
cd /d "%REPO%"

echo. >> "%~dp0logs\discord-bot.log"
echo ===== starting %DATE% %TIME% ===== >> "%~dp0logs\discord-bot.log"
"%REPO%\.venv\Scripts\python.exe" -m dashboard.backend.integrations.discord_bot >> "%~dp0logs\discord-bot.log" 2>&1
