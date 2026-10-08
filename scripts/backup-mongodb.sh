#!/usr/bin/env bash
# Eternal Packaging — MongoDB backup with capacity-aware S3 retention.
# Systemd timer/service invoke this script; units are not modified here.
set -euo pipefail

BACKEND="${BACKUP_BACKEND_DIR:-/opt/eternal-packaging/backend}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/eternal-packaging}"
LOCK_FILE="${BACKUP_LOCK_FILE:-${BACKUP_DIR}/.mongodb-backup.lock}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configurable retention / safety (owner-approved defaults)
#   BACKUP_LOCAL_KEEP=3
#   BACKUP_S3_KEEP=3
#   BACKUP_S3_PROTECTED_NAME=eternal_packaging_2026-09-30_08-11-33.archive.gz
#   BACKUP_S3_EXACT_PREFIX=production/backups/mongodb/
#   BACKUP_S3_ABORT_INCOMPLETE_MULTIPART=0   (optional multipart cleanup; off)
#   BACKUP_S3_ASSUMED_CAPACITY_BYTES=        (optional; opaque-quota planning)
#   BACKUP_S3_SAFETY_RESERVE_BYTES=0
# Deploy: keep scripts/lib/ next to this entrypoint
#   (/opt/eternal-packaging/scripts/backup-mongodb.sh + scripts/lib/).
# Systemd timer/service paths are unchanged.
export BACKUP_LOCAL_KEEP="${BACKUP_LOCAL_KEEP:-3}"
export BACKUP_S3_KEEP="${BACKUP_S3_KEEP:-3}"
export BACKUP_S3_PROTECTED_NAME="${BACKUP_S3_PROTECTED_NAME:-eternal_packaging_2026-09-30_08-11-33.archive.gz}"
export BACKUP_S3_EXACT_PREFIX="${BACKUP_S3_EXACT_PREFIX:-production/backups/mongodb/}"
export BACKUP_S3_ABORT_INCOMPLETE_MULTIPART="${BACKUP_S3_ABORT_INCOMPLETE_MULTIPART:-0}"

STATUS="FAIL"
STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
FILE="eternal_packaging_${STAMP}.archive.gz"
PARTIAL="${BACKUP_DIR}/.${FILE}.partial"
LOCAL="${BACKUP_DIR}/${FILE}"
STATUS_FILE="${BACKUP_STATUS_FILE:-${BACKUP_DIR}/last-backup-status.txt}"

log() { echo "[backup] $*"; }
fail() { log "FAIL: $*"; STATUS="FAIL"; write_status; exit 1; }

write_status() {
  mkdir -p "$(dirname "$STATUS_FILE")" 2>/dev/null || true
  {
    echo "status=${STATUS}"
    echo "timestamp=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
    echo "file=${FILE}"
    echo "local=${LOCAL}"
  } >"${STATUS_FILE}.tmp" 2>/dev/null || true
  mv -f "${STATUS_FILE}.tmp" "$STATUS_FILE" 2>/dev/null || true
}

cleanup_partial() {
  rm -f "$PARTIAL" 2>/dev/null || true
}

trap cleanup_partial EXIT

mkdir -p "$BACKUP_DIR"

# 1. Exclusive backup lock
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  fail "another backup process holds ${LOCK_FILE}"
fi

# 2. Verify environment and destinations
if [[ ! -d "$BACKEND" ]]; then
  fail "backend directory missing: $BACKEND"
fi

cd "$BACKEND"

if [[ ! -f .env ]]; then
  fail "backend .env missing"
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

: "${MONGO_URL:?MONGO_URL required}"
: "${DB_NAME:?DB_NAME required}"
: "${S3_BUCKET:?S3_BUCKET required}"
: "${S3_ENDPOINT_URL:?S3_ENDPOINT_URL required}"
: "${S3_REGION:?S3_REGION required}"
: "${S3_ACCESS_KEY_ID:?S3_ACCESS_KEY_ID required}"
: "${S3_SECRET_ACCESS_KEY:?S3_SECRET_ACCESS_KEY required}"

if [[ "$BACKUP_S3_EXACT_PREFIX" != "production/backups/mongodb/" ]]; then
  if [[ "${BACKUP_S3_ALLOW_NONDEFAULT_PREFIX:-0}" != "1" ]]; then
    fail "BACKUP_S3_EXACT_PREFIX must be production/backups/mongodb/ (got: ${BACKUP_S3_EXACT_PREFIX})"
  fi
fi

PYTHON_BIN="${BACKUP_PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "${BACKEND}/.venv/bin/python" ]]; then
    PYTHON_BIN="${BACKEND}/.venv/bin/python"
  else
    PYTHON_BIN="$(command -v python3)"
  fi
fi

log "Starting MongoDB backup: $FILE"
log "S3 prefix: $BACKUP_S3_EXACT_PREFIX"
log "Local keep: $BACKUP_LOCAL_KEEP | S3 keep: $BACKUP_S3_KEEP | protected: $BACKUP_S3_PROTECTED_NAME"

# 3. Dump to unique temporary local file
rm -f "$PARTIAL"
if ! mongodump \
  --uri="$MONGO_URL" \
  --db="$DB_NAME" \
  --archive="$PARTIAL" \
  --gzip; then
  cleanup_partial
  fail "mongodump failed"
fi

# 4. Verify completed local gzip before marking valid
if ! gzip -t "$PARTIAL"; then
  cleanup_partial
  fail "local gzip archive failed integrity check"
fi

mv -f "$PARTIAL" "$LOCAL"

# 5. Calculate local SHA-256
LOCAL_SHA256="$(sha256sum "$LOCAL" | awk '{print $1}')"
log "LOCAL_SHA256: $LOCAL_SHA256"
export BACKUP_FILE="$LOCAL"
export BACKUP_NAME="$FILE"
export BACKUP_LOCAL_SHA256="$LOCAL_SHA256"

# 6–12. S3 list / capacity / prune / upload / verify / retain (Python helper)
export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:$PYTHONPATH}"
set +e
"$PYTHON_BIN" -m lib.mongodb_backup_s3 --local "$LOCAL" --name "$FILE"
S3_RC=$?
set -e
if [[ "$S3_RC" -ne 0 ]]; then
  # Preserve validated local archive on upload/retention failure
  fail "S3 backup phase failed (local archive preserved: $LOCAL)"
fi

# 13. Retain latest N valid local backups (count-based, valid gzip only)
mapfile -t LOCAL_CANDIDATES < <(
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'eternal_packaging_*.archive.gz' -printf '%f\n' \
    | sort -r
)
KEEP_LOCAL=()
DELETE_LOCAL=()
for name in "${LOCAL_CANDIDATES[@]:-}"; do
  path="${BACKUP_DIR}/${name}"
  if gzip -t "$path" 2>/dev/null; then
    if [[ "${#KEEP_LOCAL[@]}" -lt "$BACKUP_LOCAL_KEEP" ]]; then
      KEEP_LOCAL+=("$name")
    else
      DELETE_LOCAL+=("$name")
    fi
  else
    log "LOCAL_INVALID_IGNORED: $name"
  fi
done
if [[ "${#KEEP_LOCAL[@]}" -eq 0 ]]; then
  log "LOCAL_RETENTION: refusing deletes — no valid keep set (fail closed)"
  DELETE_LOCAL=()
fi
for name in "${DELETE_LOCAL[@]:-}"; do
  # Only known MongoDB backup filenames are deleted
  if [[ "$name" != eternal_packaging_*.archive.gz ]]; then
    log "LOCAL_RETENTION: skip unexpected name $name"
    continue
  fi
  rm -f "${BACKUP_DIR}/${name}"
  log "LOCAL_DELETED: $name"
done
log "LOCAL_KEEP: ${KEEP_LOCAL[*]:-}"

# 14. SUCCESS
STATUS="SUCCESS"
write_status
log "BACKUP_COMPLETE status=SUCCESS file=$FILE sha256=$LOCAL_SHA256"
trap - EXIT
exit 0
