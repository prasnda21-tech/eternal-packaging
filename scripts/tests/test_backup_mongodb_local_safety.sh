#!/usr/bin/env bash
# Local safety proofs for backup-mongodb.sh (no real Mongo/S3).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/scripts/backup-mongodb.sh"
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

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

BACKEND="$WORKDIR/backend"
BACKUP_DIR="$WORKDIR/backups"
BIN="$WORKDIR/bin"
mkdir -p "$BACKEND" "$BACKUP_DIR" "$BIN"

cat >"$BACKEND/.env" <<'EOF'
MONGO_URL=mongodb://127.0.0.1:27017
DB_NAME=eternal_packaging
S3_BUCKET=test-bucket
S3_ENDPOINT_URL=http://127.0.0.1:9
S3_REGION=wjv-1
S3_ACCESS_KEY_ID=test-key
S3_SECRET_ACCESS_KEY=test-secret
EOF

# Mock python that always fails S3 phase (preserves local archive path)
cat >"$BIN/python3" <<'EOF'
#!/usr/bin/env bash
echo "[mock-python] refusing real S3" >&2
exit 2
EOF
chmod +x "$BIN/python3"

# --- 1) failed mongodump never promotes .partial ---
cat >"$BIN/mongodump" <<'EOF'
#!/usr/bin/env bash
# Write a partial then fail
for arg in "$@"; do
  if [[ "$arg" == --archive=* ]]; then
    echo "partial-junk" >"${arg#--archive=}"
  fi
done
exit 1
EOF
chmod +x "$BIN/mongodump"

PATH="$BIN:$PATH" \
BACKUP_BACKEND_DIR="$BACKEND" \
BACKUP_DIR="$BACKUP_DIR" \
BACKUP_PYTHON_BIN="$BIN/python3" \
BACKUP_STATUS_FILE="$BACKUP_DIR/status.txt" \
bash "$SCRIPT" >"$WORKDIR/out1.txt" 2>&1 || true

partial_count="$(find "$BACKUP_DIR" -name '.*.partial' | wc -l | tr -d ' ')"
final_count="$(find "$BACKUP_DIR" -name 'eternal_packaging_*.archive.gz' | wc -l | tr -d ' ')"
assert_eq "failed mongodump leaves no final archive" "0" "$final_count"
assert_eq "failed mongodump cleans partial" "0" "$partial_count"
assert_true "status FAIL after mongodump failure" grep -q 'status=FAIL' "$BACKUP_DIR/status.txt"

# --- 2) failed gzip does not produce valid final archive ---
rm -f "$BACKUP_DIR"/* "$BACKUP_DIR"/.* 2>/dev/null || true
mkdir -p "$BACKUP_DIR"
cat >"$BIN/mongodump" <<'EOF'
#!/usr/bin/env bash
for arg in "$@"; do
  if [[ "$arg" == --archive=* ]]; then
    # Not a valid gzip stream
    printf 'NOT-GZIP' >"${arg#--archive=}"
  fi
done
exit 0
EOF
chmod +x "$BIN/mongodump"

PATH="$BIN:$PATH" \
BACKUP_BACKEND_DIR="$BACKEND" \
BACKUP_DIR="$BACKUP_DIR" \
BACKUP_PYTHON_BIN="$BIN/python3" \
BACKUP_STATUS_FILE="$BACKUP_DIR/status.txt" \
bash "$SCRIPT" >"$WORKDIR/out2.txt" 2>&1 || true

final_count="$(find "$BACKUP_DIR" -name 'eternal_packaging_*.archive.gz' | wc -l | tr -d ' ')"
partial_count="$(find "$BACKUP_DIR" -name '.*.partial' | wc -l | tr -d ' ')"
assert_eq "failed gzip leaves no final archive" "0" "$final_count"
assert_eq "failed gzip cleans partial" "0" "$partial_count"

# --- 3) failed S3 upload preserves local completed backup ---
rm -f "$BACKUP_DIR"/* "$BACKUP_DIR"/.[!.]* 2>/dev/null || true
mkdir -p "$BACKUP_DIR"
# Use system python inside mongodump mock (BIN/python3 is the failing S3 stub)
cat >"$BIN/mongodump" <<'EOF'
#!/usr/bin/env bash
for arg in "$@"; do
  if [[ "$arg" == --archive=* ]]; then
    /usr/bin/python3 -c "import gzip,sys; gzip.open(sys.argv[1],'wb').write(b'dump-ok')" "${arg#--archive=}"
  fi
done
exit 0
EOF
chmod +x "$BIN/mongodump"

PATH="$BIN:$PATH" \
BACKUP_BACKEND_DIR="$BACKEND" \
BACKUP_DIR="$BACKUP_DIR" \
BACKUP_PYTHON_BIN="$BIN/python3" \
BACKUP_STATUS_FILE="$BACKUP_DIR/status.txt" \
bash "$SCRIPT" >"$WORKDIR/out3.txt" 2>&1 || true

final_count="$(find "$BACKUP_DIR" -name 'eternal_packaging_*.archive.gz' | wc -l | tr -d ' ')"
assert_eq "failed S3 preserves local completed backup" "1" "$final_count"
assert_true "status FAIL after S3 failure" grep -q 'status=FAIL' "$BACKUP_DIR/status.txt"
assert_true "log mentions local archive preserved" grep -q 'local archive preserved' "$WORKDIR/out3.txt"

# --- 4) retention only targets known MongoDB filenames + never sole valid ---
PYTHONPATH="$ROOT/scripts" /usr/bin/python3 - <<PY
from pathlib import Path
from lib.mongodb_backup_s3 import retain_local_backups
d = Path("$BACKUP_DIR")
(d / "notes.txt").write_text("x")
(d / "other.gz").write_bytes(b"y")
result = retain_local_backups(d, keep=3)
assert result["deleted"] == [], result
assert len(result["kept"]) == 1, result
assert (d / "notes.txt").exists()
assert (d / "other.gz").exists()
print("PASS: local retention sole+filename guards")
PY
PASS=$((PASS + 1))

# Entrypoint path compatibility: script must exist at repo scripts/ path
assert_true "entrypoint script path exists" test -x "$SCRIPT"
assert_true "no secret values in mongodump-fail log" \
  bash -c "! grep -E 'test-secret|S3_SECRET_ACCESS_KEY=' '$WORKDIR/out1.txt'"

echo "LOCAL_SAFETY_SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
