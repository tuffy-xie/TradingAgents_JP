#!/usr/bin/env bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-web"

# Load .env (handles values with spaces via set -a)
if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ROOT/.env"
  set +a
fi

# A copied .env can carry an SSL_CERT_FILE from another machine or a deleted
# virtual environment.  Python/httpx then aborts before the first API request.
# Ignore only invalid paths; valid user-provided CA bundles remain untouched.
if [ -n "${SSL_CERT_FILE:-}" ] && [ ! -f "$SSL_CERT_FILE" ]; then
  echo "Ignoring missing SSL_CERT_FILE: $SSL_CERT_FILE"
  unset SSL_CERT_FILE
fi

# Add project root to PYTHONPATH so `tradingagents` package is importable
export PYTHONPATH="$ROOT:${PYTHONPATH:-}"

if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating Python virtual environment ..."
  python3 -m venv "$VENV"
fi

if ! "$VENV/bin/python" -c 'import fastapi, tradingagents' >/dev/null 2>&1; then
  echo "Installing Python dependencies ..."
  "$VENV/bin/python" -m pip install --upgrade pip
  "$VENV/bin/python" -m pip install -e "$ROOT" -r "$ROOT/web/requirements.txt"
fi

echo "Starting TradingAgents at http://localhost:8000 ..."

echo ""
echo "  Web UI → http://localhost:8000"
echo ""
echo "Press Ctrl+C to stop."

exec "$VENV/bin/uvicorn" web.server:app --host 127.0.0.1 --port 8000
