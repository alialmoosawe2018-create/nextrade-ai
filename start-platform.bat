@echo off
REM NexTrade AI - start the local platform (two windows: API + web).
REM Everything binds to this computer only (127.0.0.1). Close both windows to stop.
setlocal
set ROOT=%~dp0

if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo [setup] Creating Python environment...
  py -3.12 -m venv "%ROOT%.venv" || goto :fail
  "%ROOT%.venv\Scripts\python.exe" -m pip install -r "%ROOT%backend\requirements.txt" -r "%ROOT%backend\requirements-mt5.txt" || goto :fail
)

if not exist "%ROOT%frontend\node_modules" (
  echo [setup] Installing web dependencies...
  pushd "%ROOT%frontend" && call npm install && popd || goto :fail
)

if not exist "%ROOT%frontend\.next\BUILD_ID" (
  echo [setup] Building the web interface...
  pushd "%ROOT%frontend" && call npm run build && popd || goto :fail
)

start "NexTrade API" cmd /k ""%ROOT%.venv\Scripts\python.exe" -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --app-dir "%ROOT%backend""
start "NexTrade Web" cmd /k "cd /d "%ROOT%frontend" && npm run start -- -p 3100"
timeout /t 6 >nul
start "" http://localhost:3100
echo NexTrade AI is starting at http://localhost:3100
exit /b 0

:fail
echo Setup failed. See the messages above.
exit /b 1
