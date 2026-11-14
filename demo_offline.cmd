@echo off
rem Strata offline demo for Windows: simulation mode, no internet, no admin rights.
rem
rem One-time setup while online, from this folder:
rem   python -m venv .venv
rem   .venv\Scripts\python -m pip install -r requirements.txt
rem   then in the web folder: npm install, then npm run build
rem
rem Then double-click this file. It changes nothing on this PC's network:
rem simulation mode only fakes measurements and faults in memory.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [Strata] Python environment not found in .venv
  echo          Run once while online:
  echo            python -m venv .venv
  echo            .venv\Scripts\python -m pip install -r requirements.txt
  pause
  exit /b 1
)
if not exist "web\dist\index.html" (
  echo [Strata] Dashboard build not found in web\dist
  echo          Run once while online:  cd web ^&^& npm install ^&^& npm run build
  pause
  exit /b 1
)

set SIM_MODE=1
set EXPLAIN_MODE=template

echo [Strata] Starting in simulation mode at http://localhost:8000
echo [Strata] Close this window or press Ctrl+C to stop.
rem Open the browser once the server has had a few seconds to start.
start "" /b cmd /c "ping -n 5 127.0.0.1 >nul & start "" http://localhost:8000"
".venv\Scripts\python.exe" -m uvicorn server.main:app --host 127.0.0.1 --port 8000
echo [Strata] Server stopped.
pause
