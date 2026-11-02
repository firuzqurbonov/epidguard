@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failure
)
".venv\Scripts\python.exe" -c "import django, openpyxl, argon2" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
  if errorlevel 1 goto failure
)
".venv\Scripts\python.exe" manage.py migrate
if errorlevel 1 goto failure
".venv\Scripts\python.exe" manage.py seed_demo
if errorlevel 1 goto failure
echo.
echo EpiGuard: http://127.0.0.1:8017
echo Local accounts: .demo-credentials.txt
echo Stop the server: Ctrl+C
echo.
".venv\Scripts\python.exe" manage.py runserver 127.0.0.1:8017 --noreload
pause
exit /b 0
:failure
echo Setup failed. See the message above and README.md.
pause
exit /b 1
