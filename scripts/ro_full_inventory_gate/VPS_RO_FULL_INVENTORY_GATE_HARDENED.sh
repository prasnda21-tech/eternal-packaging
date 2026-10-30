#!/usr/bin/env bash
# ETERNAL — HARDENED READ-ONLY full inventory gate
# Requires dedicated ETERNAL_RO_MONGO_URL (fail-closed). Never uses app RW URI silently.
# Paginated · resumable · never labels FULL when skipped/truncated.
# NO Mongo writes · NO migrations · NO restarts · NO deploy · NO secrets in output.
#
# Required env:
#   ETERNAL_RO_MONGO_URL   read-only Mongo URI
#   ETERNAL_RO_DB_NAME     database name
# Optional:
#   ETERNAL_RO_EXPECT_SHA=27df94962dd67c1e63ef42e33dd34640c0de109c
#   ETERNAL_RO_OUT_DIR=/tmp/eternal_ro_inv_audit_<ts>
#   ETERNAL_RO_BATCH_SIZE=100
#   ETERNAL_RO_QUERY_MAX_MS=30000
#   ETERNAL_RO_BACKEND=/opt/eternal-packaging-runtime/current/backend
#   ETERNAL_RO_ALLOW_SAME_AS_APP_URI=1  # only if RO URI string equals MONGO_URL by coincidence
#
# Exit: 0=FULL coverage complete · 2=PARTIAL/INCOMPLETE · 1=setup/RO failure
set -euo pipefail

EXPECT="${ETERNAL_RO_EXPECT_SHA:-27df94962dd67c1e63ef42e33dd34640c0de109c}"
R="${ETERNAL_RO_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_AUDIT="${SCRIPT_DIR}/ro_inventory_audit.py"

if [[ -z "${ETERNAL_RO_MONGO_URL:-}" ]]; then
  echo "STOP_MISSING_ETERNAL_RO_MONGO_URL"
  exit 1
fi
if [[ -z "${ETERNAL_RO_DB_NAME:-}" ]]; then
  echo "STOP_MISSING_ETERNAL_RO_DB_NAME"
  exit 1
fi

# Resolve backend (for classification helpers) — local checkout or VPS runtime
if [[ -z "${ETERNAL_RO_BACKEND:-}" ]]; then
  if [[ -d "$R/current/backend" ]]; then
    ETERNAL_RO_BACKEND="$(readlink -f "$R/current/backend")"
  elif [[ -d "${SCRIPT_DIR}/../../backend" ]]; then
    ETERNAL_RO_BACKEND="$(readlink -f "${SCRIPT_DIR}/../../backend")"
  else
    echo "STOP_BACKEND"
    exit 1
  fi
fi
export ETERNAL_RO_BACKEND
test -d "$ETERNAL_RO_BACKEND" || { echo "STOP_BACKEND"; exit 1; }

# Prefer backend venv python if it can import pymongo; else python3
PY="${ETERNAL_RO_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x "$ETERNAL_RO_BACKEND/.venv/bin/python" ]] && \
     "$ETERNAL_RO_BACKEND/.venv/bin/python" -c 'import pymongo' 2>/dev/null; then
    PY="$ETERNAL_RO_BACKEND/.venv/bin/python"
  else
    PY="$(command -v python3)"
  fi
fi
"$PY" -c 'import pymongo' || { echo "STOP_PYMONGO"; exit 1; }

ACTIVE_SHA=""
if [[ -f "$R/releases/active-release.txt" ]]; then
  ACTIVE_SHA="$(grep -E '^sha=' "$R/releases/active-release.txt" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '[:space:]' || true)"
fi
export ETERNAL_RO_ACTIVE_SHA="${ETERNAL_RO_ACTIVE_SHA:-$ACTIVE_SHA}"
export ETERNAL_RO_EXPECT_SHA="$EXPECT"

OUT_DIR="${ETERNAL_RO_OUT_DIR:-/tmp/eternal_ro_inv_audit_$(date -u +%Y%m%dT%H%M%SZ)}"
# Refuse production app paths
case "$OUT_DIR" in
  /opt/eternal-packaging/*|/opt/eternal-packaging-runtime/*|/data/db/*)
    echo "STOP_OUT_DIR_FORBIDDEN $OUT_DIR"; exit 1 ;;
esac
mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR" 2>/dev/null || true
export ETERNAL_RO_OUT_DIR="$OUT_DIR"
export ETERNAL_RO_BATCH_SIZE="${ETERNAL_RO_BATCH_SIZE:-100}"
export ETERNAL_RO_QUERY_MAX_MS="${ETERNAL_RO_QUERY_MAX_MS:-30000}"

echo "EXPECT_SHA=$EXPECT"
echo "ACTIVE_SHA=${ETERNAL_RO_ACTIVE_SHA:-unknown}"
echo "OUT_DIR=$OUT_DIR"
echo "BACKEND=$ETERNAL_RO_BACKEND"
echo "BATCH_SIZE=$ETERNAL_RO_BATCH_SIZE"

set +e
"$PY" "$PY_AUDIT"
EC=$?
set -e
echo "EXIT=$EC"
exit "$EC"
