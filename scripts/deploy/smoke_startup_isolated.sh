#!/usr/bin/env bash
# Isolated full-startup smoke (lifespan/seed ON). Not wired into production deploy.
#
# - Local mongod on ephemeral port + dbpath
# - Dummy env; evaluator OFF; demo orders OFF
# - Runs from a temp backend copy WITHOUT production .env
# - Proves startup + /api/system/health + clean shutdown
#
# Usage (from repo root or any cwd):
#   bash scripts/deploy/smoke_startup_isolated.sh
#   ETERNAL_SMOKE_BACKEND=/path/to/backend bash scripts/deploy/smoke_startup_isolated.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

BACKEND_SRC="${ETERNAL_SMOKE_BACKEND:-$REPO_ROOT/backend}"
[[ -d "$BACKEND_SRC" ]] || eternal_deploy_die "smoke: backend missing: $BACKEND_SRC"
command -v mongod >/dev/null || eternal_deploy_die "BLOCKED: mongod not installed"
command -v python3 >/dev/null || eternal_deploy_die "BLOCKED: python3 missing"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/eternal-smoke.XXXXXX")"
MONGO_DBPATH="$WORK/mongo"
MONGO_LOG="$WORK/mongod.log"
SMOKE_BACKEND="$WORK/backend"
PID_MONGO=""
PID_UV=""
cleanup() {
  if [[ -n "${PID_UV:-}" ]] && kill -0 "$PID_UV" 2>/dev/null; then
    kill "$PID_UV" 2>/dev/null || true
    wait "$PID_UV" 2>/dev/null || true
  fi
  if [[ -n "${PID_MONGO:-}" ]] && kill -0 "$PID_MONGO" 2>/dev/null; then
    kill "$PID_MONGO" 2>/dev/null || true
    wait "$PID_MONGO" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$MONGO_DBPATH"
# Copy backend sources excluding secrets and venvs — production .env must not load.
rsync -a \
  --exclude '.env' \
  --exclude '.env.*' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  "$BACKEND_SRC"/ "$SMOKE_BACKEND"/
[[ ! -e "$SMOKE_BACKEND/.env" ]] || eternal_deploy_die "smoke: .env leaked into smoke tree"

req_rel="$(eternal_resolve_requirements_relpath "$SMOKE_BACKEND")"
eternal_deploy_log "smoke using requirements_file=$req_rel"

# Ephemeral venv for smoke only (not production deploy path).
py="${ETERNAL_PYTHON_BIN:-python3}"
"$py" -m venv "$SMOKE_BACKEND/.venv" || eternal_deploy_die "BLOCKED: cannot create smoke venv"
# shellcheck disable=SC1091
set +e
"$SMOKE_BACKEND/.venv/bin/pip" install --upgrade pip wheel >/dev/null 2>"$WORK/pip.err"
pip_boot=$?
set -e
[[ "$pip_boot" -eq 0 ]] || {
  eternal_deploy_log "pip bootstrap failed"
  cat "$WORK/pip.err" >&2 || true
  eternal_deploy_die "BLOCKED: pip bootstrap failed for smoke venv"
}
set +e
"$SMOKE_BACKEND/.venv/bin/pip" install -r "$SMOKE_BACKEND/$req_rel" >"$WORK/pip-install.log" 2>&1
pip_ec=$?
set -e
if [[ "$pip_ec" -ne 0 ]]; then
  tail -n 40 "$WORK/pip-install.log" >&2 || true
  eternal_deploy_die "BLOCKED: pip install $req_rel failed (see log); smoke cannot run"
fi

# Pick free port for mongod and uvicorn.
pick_port() {
  python3 - <<'PY'
import socket
s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()
PY
}
MONGO_PORT="$(pick_port)"
APP_PORT="$(pick_port)"
DB_NAME="eternal_smoke_$(date +%s)_$$"

mongod --dbpath "$MONGO_DBPATH" --port "$MONGO_PORT" --bind_ip 127.0.0.1 \
  --logpath "$MONGO_LOG" --fork --quiet \
  || eternal_deploy_die "BLOCKED: mongod failed to start"
PID_MONGO="$(pgrep -f "mongod --dbpath $MONGO_DBPATH" | head -n1 || true)"
[[ -n "$PID_MONGO" ]] || eternal_deploy_die "BLOCKED: mongod pid not found"

# Wait for mongod accept.
for _ in $(seq 1 30); do
  if mongosh --quiet --port "$MONGO_PORT" --eval 'db.runCommand({ping:1})' >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

# Dummy env — pre-set so even if a dotenv appeared it would not override these.
# Block common external integration knobs with empty/disabled values.
export MONGO_URL="mongodb://127.0.0.1:${MONGO_PORT}"
export DB_NAME="$DB_NAME"
export JWT_SECRET="smoke-jwt-not-for-production"
export APP_ENV="smoke"
export SEED_DEMO_ORDERS="0"
export SALES_ACTIVITY_EVALUATOR_ENABLED="0"
export ACCURATE_API_TOKEN=""
export ACCURATE_DB_ID=""
export ACCURATE_API_SIGNATURE_SECRET=""
export AWS_ACCESS_KEY_ID=""
export AWS_SECRET_ACCESS_KEY=""
export GOOGLE_PLACES_API_KEY=""
export STORAGE_BACKEND="local"
unset ACCURATE_SESSION_ID || true

eternal_deploy_log "starting uvicorn with lifespan (full seed) on 127.0.0.1:$APP_PORT"
(
  cd "$SMOKE_BACKEND"
  # Ensure no parent .env discovery surprises: dotenv path is ROOT_DIR/.env (absent).
  exec "$SMOKE_BACKEND/.venv/bin/uvicorn" server:app --host 127.0.0.1 --port "$APP_PORT" --log-level warning
) >"$WORK/uvicorn.log" 2>&1 &
PID_UV=$!

health_url="http://127.0.0.1:${APP_PORT}/api/system/health"
ok=0
for i in $(seq 1 90); do
  if ! kill -0 "$PID_UV" 2>/dev/null; then
    eternal_deploy_log "uvicorn exited early"
    tail -n 80 "$WORK/uvicorn.log" >&2 || true
    eternal_deploy_die "smoke: uvicorn died during startup"
  fi
  body="$(curl -fsS --max-time 2 "$health_url" 2>/dev/null || true)"
  if [[ -n "$body" ]] && eternal_health_body_ok "$body"; then
    eternal_deploy_log "health OK attempt=$i body=$body"
    ok=1
    break
  fi
  sleep 0.5
done
[[ "$ok" == "1" ]] || {
  tail -n 100 "$WORK/uvicorn.log" >&2 || true
  eternal_deploy_die "smoke: health not operational after startup window"
}

# Graceful shutdown
kill -TERM "$PID_UV" 2>/dev/null || true
for _ in $(seq 1 30); do
  kill -0 "$PID_UV" 2>/dev/null || break
  sleep 0.2
done
if kill -0 "$PID_UV" 2>/dev/null; then
  kill -KILL "$PID_UV" 2>/dev/null || true
  eternal_deploy_die "smoke: uvicorn did not shut down cleanly"
fi
PID_UV=""
wait || true

printf 'SMOKE_STARTUP_OK mongo_port=%s app_port=%s db=%s requirements_file=%s\n' \
  "$MONGO_PORT" "$APP_PORT" "$DB_NAME" "$req_rel"
