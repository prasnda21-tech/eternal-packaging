#!/usr/bin/env bash
# Eternal Packaging — MongoDB backup (LOCAL_ONLY default; optional S3).
# Systemd timer/service invoke this script; units/schedule are not modified here.
set -euo pipefail

BACKEND="${BACKUP_BACKEND_DIR:-/opt/eternal-packaging/backend}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/eternal-packaging}"
LOCK_FILE="${BACKUP_LOCK_FILE:-${BACKUP_DIR}/.mongodb-backup.lock}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configurable retention / safety (owner-approved defaults)
#   BACKUP_MODE=LOCAL_ONLY          (LOCAL_ONLY | S3)
#   BACKUP_LOCAL_KEEP=7
#   BACKUP_LOCAL_PROTECTED_NAME=eternal_packaging_2026-09-30_08-11-33.archive.gz
#   BACKUP_S3_KEEP=3
#   BACKUP_S3_PROTECTED_NAME=eternal_packaging_2026-09-30_08-11-33.archive.gz
#   BACKUP_S3_EXACT_PREFIX=production/backups/mongodb/
#   BACKUP_S3_ABORT_INCOMPLETE_MULTIPART=0
# Deploy note: production active entrypoint is often
#   /opt/eternal-backup/current/backup-mongodb.sh
# Source tree: scripts/backup-mongodb.sh (+ scripts/lib/ when BACKUP_MODE=S3).
# Systemd timer/service paths and schedule stay unchanged.
export BACKUP_MODE="${BACKUP_MODE:-LOCAL_ONLY}"
export BACKUP_LOCAL_KEEP="${BACKUP_LOCAL_KEEP:-7}"
export BACKUP_S3_KEEP="${BACKUP_S3_KEEP:-3}"
export BACKUP_S3_PROTECTED_NAME="${BACKUP_S3_PROTECTED_NAME:-eternal_packaging_2026-09-30_08-11-33.archive.gz}"
export BACKUP_LOCAL_PROTECTED_NAME="${BACKUP_LOCAL_PROTECTED_NAME:-$BACKUP_S3_PROTECTED_NAME}"
export BACKUP_S3_EXACT_PREFIX="${BACKUP_S3_EXACT_PREFIX:-production/backups/mongodb/}"
export BACKUP_S3_ABORT_INCOMPLETE_MULTIPART="${BACKUP_S3_ABORT_INCOMPLETE_MULTIPART:-0}"

# Exact regular daily archive name (timestamp stamp from this script).
REGULAR_BACKUP_RE='^eternal_packaging_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}\.archive\.gz$'

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
    echo "mode=${BACKUP_MODE}"
  } >"${STATUS_FILE}.tmp" 2>/dev/null || true
  mv -f "${STATUS_FILE}.tmp" "$STATUS_FILE" 2>/dev/null || true
}

cleanup_partial() {
  rm -f "$PARTIAL" 2>/dev/null || true
}

is_regular_backup_name() {
  [[ "$1" =~ $REGULAR_BACKUP_RE ]]
}

# Local retention: keep newest N valid regular archives after a successful backup.
# Never deletes on empty keep set, protected/special names, symlinks, or unexpected paths.
retain_local_archives() {
  local keep="${BACKUP_LOCAL_KEEP}"
  local protected="${BACKUP_LOCAL_PROTECTED_NAME}"
  local -a candidates=()
  local -a keep_local=()
  local -a delete_local=()
  local name path

  if ! [[ "$keep" =~ ^[1-9][0-9]*$ ]]; then
    fail "BACKUP_LOCAL_KEEP must be a positive integer (got: ${keep})"
  fi

  mapfile -t candidates < <(
    find "$BACKUP_DIR" -maxdepth 1 -type f ! -type l -printf '%f\n' 2>/dev/null \
      | sort -r
  )

  for name in "${candidates[@]:-}"; do
    [[ -n "$name" ]] || continue
    if ! is_regular_backup_name "$name"; then
      if [[ "$name" == eternal_packaging_*.archive.gz ]]; then
        log "LOCAL_RETENTION: preserve special/non-regular archive $name"
      fi
      continue
    fi
    if [[ -n "$protected" && "$name" == "$protected" ]]; then
      log "LOCAL_RETENTION: preserve protected archive $name"
      continue
    fi
    path="${BACKUP_DIR}/${name}"
    # Refuse unexpected path resolution / symlink races
    if [[ -L "$path" ]]; then
      log "LOCAL_RETENTION: skip symlink $name"
      continue
    fi
    if [[ ! -f "$path" ]]; then
      log "LOCAL_RETENTION: skip missing/non-file $name"
      continue
    fi
    case "$(realpath -m "$path")" in
      "${BACKUP_DIR}"/*) ;;
      *)
        log "LOCAL_RETENTION: skip unexpected path $name"
        continue
        ;;
    esac
    if gzip -t "$path" 2>/dev/null; then
      if [[ "${#keep_local[@]}" -lt "$keep" ]]; then
        keep_local+=("$name")
      else
        delete_local+=("$name")
      fi
    else
      log "LOCAL_INVALID_IGNORED: $name"
    fi
  done

  if [[ "${#keep_local[@]}" -eq 0 ]]; then
    log "LOCAL_RETENTION: refusing deletes — no valid keep set (fail closed)"
    delete_local=()
  fi
  if [[ "${#keep_local[@]}" -lt "$keep" ]]; then
    # Fewer than N valid regular archives: delete nothing
    if [[ "${#delete_local[@]}" -gt 0 ]]; then
      log "LOCAL_RETENTION: below keep=${keep} (have=${#keep_local[@]}); clearing delete set"
      delete_local=()
    fi
  fi

  for name in "${delete_local[@]:-}"; do
    [[ -n "$name" ]] || continue
    if ! is_regular_backup_name "$name"; then
      log "LOCAL_RETENTION: skip unexpected name $name"
      continue
    fi
    if [[ -n "$protected" && "$name" == "$protected" ]]; then
      log "LOCAL_RETENTION: refuse delete protected $name"
      continue
    fi
    path="${BACKUP_DIR}/${name}"
    if [[ -L "$path" ]]; then
      log "LOCAL_RETENTION: refuse delete symlink $name"
      continue
    fi
    case "$(realpath -m "$path")" in
      "${BACKUP_DIR}"/*) ;;
      *)
        log "LOCAL_RETENTION: refuse unexpected path $name"
        continue
        ;;
    esac
    if [[ ! -f "$path" ]]; then
      log "LOCAL_RETENTION: skip already-absent $name"
      continue
    fi
    if ! rm -f -- "$path"; then
      fail "LOCAL_RETENTION: delete failed for $name"
    fi
    if [[ -e "$path" || -L "$path" ]]; then
      fail "LOCAL_RETENTION: delete did not remove $name"
    fi
    log "LOCAL_DELETED: $name"
  done
  log "LOCAL_KEEP: ${keep_local[*]:-}"
}

trap cleanup_partial EXIT

mkdir -p "$BACKUP_DIR"

# 1. Exclusive backup lock
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  fail "another backup process holds ${LOCK_FILE}"
fi

# 2. Verify environment and destinations
case "$BACKUP_MODE" in
  LOCAL_ONLY|S3) ;;
  *)
    fail "BACKUP_MODE must be LOCAL_ONLY or S3 (got: ${BACKUP_MODE})"
    ;;
esac

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

if [[ "$BACKUP_MODE" == "S3" ]]; then
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
log "BACKUP_MODE: $BACKUP_MODE"
if [[ "$BACKUP_MODE" == "S3" ]]; then
  log "S3 prefix: $BACKUP_S3_EXACT_PREFIX"
  log "Local keep: $BACKUP_LOCAL_KEEP | S3 keep: $BACKUP_S3_KEEP | protected: $BACKUP_S3_PROTECTED_NAME"
else
  log "LOCAL_ONLY: S3 upload/retention skipped"
  log "Local keep: $BACKUP_LOCAL_KEEP | protected: $BACKUP_LOCAL_PROTECTED_NAME"
fi

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
if [[ -z "$LOCAL_SHA256" || "${#LOCAL_SHA256}" -ne 64 ]]; then
  fail "SHA-256 computation failed for $LOCAL"
fi
log "LOCAL_SHA256: $LOCAL_SHA256"
export BACKUP_FILE="$LOCAL"
export BACKUP_NAME="$FILE"
export BACKUP_LOCAL_SHA256="$LOCAL_SHA256"

# 6–12. Optional S3 list / capacity / prune / upload / verify / retain
if [[ "$BACKUP_MODE" == "S3" ]]; then
  export PYTHONPATH="${SCRIPT_DIR}${PYTHONPATH:+:$PYTHONPATH}"
  set +e
  "$PYTHON_BIN" -m lib.mongodb_backup_s3 --local "$LOCAL" --name "$FILE"
  S3_RC=$?
  set -e
  if [[ "$S3_RC" -ne 0 ]]; then
    # Preserve validated local archive on upload/retention failure
    fail "S3 backup phase failed (local archive preserved: $LOCAL)"
  fi
else
  log "S3_PHASE_SKIPPED mode=LOCAL_ONLY"
fi

# 13. Retain latest N valid local backups (only after successful validation)
retain_local_archives

# 14. SUCCESS
STATUS="SUCCESS"
write_status
log "BACKUP_COMPLETE status=SUCCESS mode=$BACKUP_MODE file=$FILE sha256=$LOCAL_SHA256"
trap - EXIT
exit 0
