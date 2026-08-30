@echo off
rem Launches the Agentic Trading Lab backend the canonical way:
rem   uvicorn dashboard.backend.app:app, by import string, from the repo root.
rem
rem PYTHONUTF8 is load-bearing on Windows: the app's startup and migration
rem logging is full of emoji, and on a cp1252 console the very first print
rem raises UnicodeEncodeError and kills the process before it ever binds.
setlocal
set "BIND=%~1"
if "%BIND%"=="" set "BIND=127.0.0.1"
set "PORT=%~2"
if "%PORT%"=="" set "PORT=8000"

set "REPO=%~dp0.."
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%~dp0logs" mkdir "%~dp0logs"
cd /d "%REPO%"

echo. >> "%~dp0logs\server.log"
echo ===== starting %DATE% %TIME% on %BIND%:%PORT% ===== >> "%~dp0logs\server.log"
"%REPO%\.venv\Scripts\python.exe" -m uvicorn dashboard.backend.app:app --host %BIND% --port %PORT% >> "%~dp0logs\server.log" 2>&1
