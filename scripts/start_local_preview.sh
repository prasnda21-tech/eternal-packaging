#!/usr/bin/env bash
# Local preview only. Safe to run repeatedly. Does not seed, clean, or deploy.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.tmp/dev-logs"
mkdir -p "$LOG_DIR"

fail() {
  echo "BLOCKER: local preview did not become ready" >&2
  echo "CAUSE: $1" >&2
  echo "FILE/COMMAND: $2" >&2
  if [[ -n "${3:-}" && -f "$3" ]]; then
    echo "--- log tail: $3 ---" >&2
    tail -n 40 "$3" >&2 || true
  fi
  exit 1
}

port_open() {
  python3 - "$1" <<'PY'
import socket, sys
port = int(sys.argv[1])
sock = socket.socket()
sock.settimeout(0.4)
try:
    raise SystemExit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
finally:
    sock.close()
PY
}

python3 - "$ROOT/backend/.env" <<'PY'
import sys
from pathlib import Path
from urllib.parse import urlparse

env_path = Path(sys.argv[1])
if not env_path.is_file():
    sys.exit("CAUSE: backend/.env is missing\nFILE/COMMAND: backend/.env")
values = {}
for line in env_path.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    values[key.strip()] = value.strip().strip('"').strip("'")
host = (urlparse(values.get("MONGO_URL", "")).hostname or "").lower()
db_name = values.get("DB_NAME", "")
app_env = (values.get("APP_ENV") or "").lower()
if host not in {"127.0.0.1", "localhost"}:
    sys.exit("CAUSE: Mongo host is not local\nFILE/COMMAND: backend/.env")
if db_name != "eternal_packaging_preview":
    sys.exit("CAUSE: DB_NAME is not eternal_packaging_preview\nFILE/COMMAND: backend/.env")
if app_env in {"production", "prod"}:
    sys.exit("CAUSE: APP_ENV is production\nFILE/COMMAND: backend/.env")
# Fail-closed package lock: admin UAT requires explicit local APP_ENV.
if app_env not in {"local", "development", "preview", "test"}:
    sys.exit(
        "CAUSE: APP_ENV must be local|development|preview|test for local preview\n"
        "FILE/COMMAND: backend/.env"
    )
PY

if ! port_open 27017; then
  if ! command -v mongod >/dev/null 2>&1; then
    fail "mongod is not installed and 27017 is closed" "mongod"
  fi
  mongod --dbpath /data/db --bind_ip 127.0.0.1 --port 27017 --fork \
    --logpath "$LOG_DIR/mongo.log" --replSet eternal_local_rs \
    || fail "mongod failed to start" "mongod --dbpath /data/db --replSet eternal_local_rs" "$LOG_DIR/mongo.log"
fi

python3 - <<'PY' || fail "Mongo did not become eternal_local_rs PRIMARY" "mongosh/pymongo hello"
import time
from pymongo import MongoClient
from pymongo.errors import PyMongoError

client = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=2000)
deadline = time.time() + 30
last = "no response"
while time.time() < deadline:
    try:
        hello = client.admin.command("hello")
        name = hello.get("setName")
        if not name:
            status = client.admin.command("replSetGetStatus")
            last = str(status)
        elif name != "eternal_local_rs":
            raise SystemExit(f"unexpected replica set {name}")
        elif hello.get("isWritablePrimary"):
            raise SystemExit(0)
        else:
            last = "replica set is not primary yet"
    except SystemExit:
        raise
    except PyMongoError as exc:
        last = str(exc)
        if "no replset config" in last.lower() or getattr(exc, "code", None) == 94:
            client.admin.command(
                "replSetInitiate",
                {"_id": "eternal_local_rs", "members": [{"_id": 0, "host": "127.0.0.1:27017"}]},
            )
            last = "initiated eternal_local_rs"
    time.sleep(1)
raise SystemExit(last)
PY

if ! port_open 8000; then
  (
    cd "$ROOT/backend"
    # Local preview only. Does not change production CORS.
    export CORS_ORIGINS="http://127.0.0.1:3002,http://localhost:3002"
    nohup python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 \
      >>"$LOG_DIR/backend.log" 2>&1 &
  )
fi

backend_ok=0
for _ in $(seq 1 40); do
  if python3 - <<'PY'
import json, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:8000/api/system/health", timeout=1) as resp:
        body = json.load(resp)
    raise SystemExit(0 if body.get("mongo") is True else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    backend_ok=1
    break
  fi
  sleep 0.5
done
[[ "$backend_ok" == 1 ]] || fail "backend health check failed" "python3 -m uvicorn server:app --host 127.0.0.1 --port 8000" "$LOG_DIR/backend.log"

if ! port_open 3002; then
  (
    cd "$ROOT/frontend"
    PORT=3002 BROWSER=none nohup npm start >>"$LOG_DIR/frontend.log" 2>&1 &
  )
fi

frontend_ok=0
for _ in $(seq 1 60); do
  if python3 - <<'PY'
import urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:3002", timeout=1) as resp:
        raise SystemExit(0 if resp.status == 200 else 1)
except Exception:
    raise SystemExit(1)
PY
  then
    frontend_ok=1
    break
  fi
  sleep 1
done
[[ "$frontend_ok" == 1 ]] || fail "frontend did not become reachable" "PORT=3002 npm start" "$LOG_DIR/frontend.log"

cat <<'EOF'
========================================
ETERNAL PACKAGING LOCAL PREVIEW READY
========================================

Mongo:
127.0.0.1:27017

Replica Set:
eternal_local_rs PRIMARY

Database:
eternal_packaging_preview

Backend:
http://127.0.0.1:8000

Frontend:
http://127.0.0.1:3002

Production:
NOT ACCESSED

Open:
http://127.0.0.1:3002
========================================
EOF
