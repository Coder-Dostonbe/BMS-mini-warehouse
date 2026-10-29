#!/usr/bin/env sh
# One command: venv + dependencies + migrations + server.
#   ./run.sh      -> http://127.0.0.1:8000 (DEBUG on, this computer only)
#   ./run.sh lan  -> http://<this-computer-IP>:8000 for phones in the same Wi-Fi (DEBUG stays off)
set -eu
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if [ ! -x venv/bin/python ]; then
  echo "[1/3] Creating virtual environment..."
  "$PYTHON" -m venv venv
fi
echo "[2/3] Installing dependencies..."
venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
echo "[3/3] Applying migrations..."
venv/bin/python manage.py migrate --noinput

if [ "${1:-}" = "lan" ]; then
  export DJANGO_ALLOWED_HOSTS='*'
  echo
  echo "Open http://<IP of this computer>:8000 on the phone (macOS: ipconfig getifaddr en0, Linux: hostname -I)"
  exec venv/bin/python manage.py runserver 0.0.0.0:8000
fi

export DJANGO_DEBUG=1
echo
echo "Open http://127.0.0.1:8000  (logins are in README.md). Stop: Ctrl+C"
exec venv/bin/python manage.py runserver 127.0.0.1:8000
