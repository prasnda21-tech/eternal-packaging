#!/usr/bin/env bash
# Isolated MongoDB dump → restore proof (NEVER production).
# Starts a throwaway mongod, dumps a synthetic DB, restores into a NEW DB name,
# verifies collection counts, then tears down. No S3. No secret output.
set -euo pipefail

PASS=0
FAIL=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    echo "PASS: $label"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $label expected=[$expected] actual=[$actual]"
    FAIL=$((FAIL + 1))
  fi
}

assert_true() {
  local label="$1"
  shift
  if "$@"; then
    echo "PASS: $label"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $label"
    FAIL=$((FAIL + 1))
  fi
}

command -v mongod >/dev/null
command -v mongodump >/dev/null
command -v mongorestore >/dev/null
command -v mongosh >/dev/null

WORKDIR="$(mktemp -d)"
DBPATH="$WORKDIR/db"
ARCHIVE_DIR="$WORKDIR/archives"
mkdir -p "$DBPATH" "$ARCHIVE_DIR"
PORT=$((27050 + RANDOM % 200))
URI="mongodb://127.0.0.1:${PORT}"
SRC_DB="eternal_restore_src_$$"
DST_DB="eternal_restore_dst_$$"
ARCHIVE="$ARCHIVE_DIR/eternal_packaging_isolated_test.archive.gz"
LOG="$WORKDIR/mongod.log"
PID=""

cleanup() {
  if [[ -n "${PID}" ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

mongod --dbpath "$DBPATH" --port "$PORT" --bind_ip 127.0.0.1 --noauth \
  --fork --logpath "$LOG" >/dev/null
PID="$(pgrep -f "mongod --dbpath $DBPATH" | head -1 || true)"
# Prefer pid from log / process list; wait for ready
for _ in $(seq 1 40); do
  if mongosh --quiet "$URI" --eval 'db.runCommand({ping:1}).ok' 2>/dev/null | grep -q 1; then
    break
  fi
  sleep 0.25
done
assert_true "isolated mongod ping" \
  mongosh --quiet "$URI" --eval 'quit(db.runCommand({ping:1}).ok ? 0 : 1)'

# Seed synthetic collections (no PII)
mongosh --quiet "$URI/$SRC_DB" --eval '
  db.sales_invoices.insertMany([
    {id:"inv-1", financial_status:"finalized"},
    {id:"inv-2", financial_status:"cancelled"}
  ]);
  db.sales_receipts.insertMany([
    {id:"rcpt-1", settlement_effective:true},
    {id:"rcpt-2"}
  ]);
  db.journal_entries.insertMany([
    {id:"je-1", source_type:"sales_invoice", source_id:"inv-1", status:"posted", reversal_of:null},
    {id:"je-2", source_type:"sales_receipt", source_id:"rcpt-1", status:"posted", reversal_of:null}
  ]);
  db.journal_lines.insertMany([
    {journal_id:"je-1", debit:100, credit:0},
    {journal_id:"je-1", debit:0, credit:100}
  ]);
' >/dev/null

assert_eq "src invoice count" "2" \
  "$(mongosh --quiet "$URI/$SRC_DB" --eval 'print(db.sales_invoices.countDocuments({}))')"
assert_eq "src journal count" "2" \
  "$(mongosh --quiet "$URI/$SRC_DB" --eval 'print(db.journal_entries.countDocuments({}))')"

mongodump --uri="$URI" --db="$SRC_DB" --archive="$ARCHIVE" --gzip >/dev/null
assert_true "archive exists" test -f "$ARCHIVE"
assert_true "gzip integrity" gzip -t "$ARCHIVE"
SHA="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
assert_true "sha256 non-empty" test "${#SHA}" -eq 64
echo "ISOLATED_ARCHIVE_SHA256_LEN=${#SHA}"

# Restore into a DIFFERENT database name (never overwrite source)
mongorestore --uri="$URI" --archive="$ARCHIVE" --gzip \
  --nsFrom="${SRC_DB}.*" --nsTo="${DST_DB}.*" >/dev/null

assert_eq "dst invoice count" "2" \
  "$(mongosh --quiet "$URI/$DST_DB" --eval 'print(db.sales_invoices.countDocuments({}))')"
assert_eq "dst receipt count" "2" \
  "$(mongosh --quiet "$URI/$DST_DB" --eval 'print(db.sales_receipts.countDocuments({}))')"
assert_eq "dst journal count" "2" \
  "$(mongosh --quiet "$URI/$DST_DB" --eval 'print(db.journal_entries.countDocuments({}))')"
assert_eq "dst lines count" "2" \
  "$(mongosh --quiet "$URI/$DST_DB" --eval 'print(db.journal_lines.countDocuments({}))')"

# Source still intact (no overwrite)
assert_eq "src still 2 after restore-to-other-db" "2" \
  "$(mongosh --quiet "$URI/$SRC_DB" --eval 'print(db.sales_invoices.countDocuments({}))')"

# Drop only synthetic DBs
mongosh --quiet "$URI" --eval "db.getSiblingDB('${SRC_DB}').dropDatabase(); db.getSiblingDB('${DST_DB}').dropDatabase();" >/dev/null

echo "ISOLATED_RESTORE_SUMMARY pass=$PASS fail=$FAIL port=$PORT (localhost only)"
if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi
exit 0
