#!/usr/bin/env bash
# Local UAT for PR #606 warehouse standalone. Does NOT touch production.
# Uses isolated DB eternal_packaging_uat_pr606_standalone (R2 OFF, standalone ON).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT/.tmp/dev-logs"
mkdir -p "$LOG_DIR"
DB_NAME_UAT="eternal_packaging_uat_pr606_standalone"

# Refuse production-looking hosts from .env
python3 - "$ROOT/backend/.env" <<'PY'
import sys
from pathlib import Path
from urllib.parse import urlparse
vals={}
for line in Path(sys.argv[1]).read_text().splitlines():
    line=line.strip()
    if not line or line.startswith('#') or '=' not in line: continue
    k,v=line.split('=',1); vals[k.strip()]=v.strip().strip('"').strip("'")
host=(urlparse(vals.get('MONGO_URL','')).hostname or '').lower()
if host not in {'127.0.0.1','localhost'}:
    sys.exit('Refusing non-local MONGO_URL')
if (vals.get('APP_ENV') or '').lower() in {'production','prod'}:
    sys.exit('Refusing APP_ENV=production')
print('ok')
PY

# Stop only local uvicorn on :8000 if ours
if python3 - <<'PY'
import socket
s=socket.socket(); raise SystemExit(0 if s.connect_ex(('127.0.0.1',8000))==0 else 1)
PY
then
  pid=$(ss -ltnp 2>/dev/null | awk '/:8000/ {print}' || true)
  # kill by pgrep uvicorn bound to 8000
  for p in $(pgrep -f 'uvicorn server:app' || true); do
    kill "$p" 2>/dev/null || true
  done
  sleep 1
fi

set -a
# shellcheck disable=SC1091
source <(grep -E '^[A-Z_]+=' "$ROOT/backend/.env" | sed 's/\r$//')
set +a
export APP_ENV=preview
export DB_NAME="$DB_NAME_UAT"
export MONGO_URL="${MONGO_URL:-mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs}"
case "$MONGO_URL" in
  *replicaSet=*) ;;
  *) export MONGO_URL="mongodb://127.0.0.1:27017/?replicaSet=eternal_local_rs" ;;
esac
export CORS_ORIGINS="http://127.0.0.1:3002,http://localhost:3002"

(
  cd "$ROOT/backend"
  nohup python3 -m uvicorn server:app --host 127.0.0.1 --port 8000 \
    >>"$LOG_DIR/backend-pr606-standalone.log" 2>&1 &
)
echo "Started backend DB_NAME=$DB_NAME_UAT on :8000"
echo "Frontend: http://127.0.0.1:3002/persediaan/stock-saat-ini"
echo "Login: admin@eternalpackaging.com (TRIAL_DEFAULT_PASSWORD from backend/.env)"
