@echo off
REM Daily Baseline Short — the entry point Task Scheduler calls.
REM
REM A .cmd rather than pointing the task straight at python.exe, for three
REM reasons that all bite in practice:
REM   1. The working directory must be THIS folder. A scheduled task's default
REM      cwd is system32, and every relative path in the job (scene.html,
REM      logo.png, node_modules, secrets/) would miss.
REM   2. The FULL interpreter path is used. "python" on PATH resolves to the
REM      Windows Store execution alias, which is a reparse point and does not
REM      reliably launch from a non-interactive task.
REM   3. Output is kept. A scheduled task that fails silently is worse than one
REM      that does not run, so stdout and stderr land in logs/.

setlocal
cd /d "%~dp0"

set "PY=C:\Users\shawn\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"

REM node must be findable for the render step
set "PATH=E:\node;%PATH%"

if not exist "logs" mkdir "logs"
for /f "usebackq delims=" %%d in (`powershell -NoProfile -Command "Get-Date -Format 'yyyy-MM-dd'"`) do set "DAY=%%d"

echo ============================================================ >> "logs\%DAY%.log"
echo run started %DATE% %TIME% >> "logs\%DAY%.log"
"%PY%" daily.py %* >> "logs\%DAY%.log" 2>&1
set "RC=%ERRORLEVEL%"
echo run finished rc=%RC% %DATE% %TIME% >> "logs\%DAY%.log"

REM Keep only the last 30 days of logs.
forfiles /p "logs" /m *.log /d -30 /c "cmd /c del @path" 2>nul

exit /b %RC%
