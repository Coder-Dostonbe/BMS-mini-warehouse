@echo off
rem One command: venv + dependencies + migrations + server.
rem   run.bat        -> http://127.0.0.1:8000 (DEBUG on, this computer only)
rem   run.bat lan    -> http://<this-PC-IP>:8000 for phones in the same Wi-Fi (DEBUG stays off)
setlocal
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py -3

if not exist venv\Scripts\python.exe (
  echo [1/3] Creating virtual environment...
  %PY% -m venv venv || goto :error
)
echo [2/3] Installing dependencies...
venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt || goto :error
echo [3/3] Applying migrations...
venv\Scripts\python.exe manage.py migrate --noinput || goto :error

if /i "%~1"=="lan" goto :lan
set DJANGO_DEBUG=1
echo.
echo Open http://127.0.0.1:8000  (logins are in README.md). Stop: Ctrl+C
venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
goto :eof

:lan
set DJANGO_ALLOWED_HOSTS=*
echo.
echo Open http://^<IPv4 of this PC^>:8000 on the phone. Find the IPv4 with: ipconfig
venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
goto :eof

:error
echo.
echo Setup failed, see the message above. Python 3.11+ must be installed.
exit /b 1
