#!/bin/sh
set -eu

RUNTIME_LOGS_DIR="${RUNTIME_LOGS_DIR:-/app/runtime-logs}"
CREDS_PATH="$RUNTIME_LOGS_DIR/first-run-credentials.json"

mkdir -p "$RUNTIME_LOGS_DIR"

echo "[entrypoint] waiting for database..."
python - <<'PY'
import os, time
from sqlalchemy import create_engine, text

url = os.getenv("DATABASE_URL", "").strip()
if not url:
    raise SystemExit("DATABASE_URL is required in Docker mode.")

engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5} if url.startswith("postgresql") else {})
deadline = time.time() + float(os.getenv("DB_WAIT_SECONDS", "30"))
last_error = None
while time.time() < deadline:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("[entrypoint] database reachable")
        raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)
print("[entrypoint] database not reachable:", last_error)
raise SystemExit(1)
PY

if [ -z "${LOCAL_AUTH_LOGIN_SECRET:-}" ]; then
  if [ -f "$CREDS_PATH" ]; then
    EXISTING_SECRET="$(python - <<PY
import json
from pathlib import Path
path = Path(r"$CREDS_PATH")
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    payload = {}
print(((payload.get("local_session") or {}).get("login_secret")) or "")
PY
)"
    if [ -n "$EXISTING_SECRET" ]; then
      export LOCAL_AUTH_LOGIN_SECRET="$EXISTING_SECRET"
    fi
  fi
fi

if [ -z "${LOCAL_AUTH_LOGIN_SECRET:-}" ]; then
  GENERATED_SECRET="$(python - <<'PY'
import secrets
print(secrets.token_urlsafe(24))
PY
)"
  export LOCAL_AUTH_LOGIN_SECRET="$GENERATED_SECRET"
fi

echo "[entrypoint] running migrations..."
python -m alembic -c alembic.ini upgrade head

echo "[entrypoint] seeding (db-empty only)..."
python -m backend.seed_first_run

echo "[entrypoint] starting api..."
exec python -m backend.app
