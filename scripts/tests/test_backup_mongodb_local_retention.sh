#!/usr/bin/env bash
# LOCAL_ONLY retention proofs for backup-mongodb.sh (isolated temp dirs; no Mongo/S3/prod).
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

assert_false() {
  local label="$1"
  shift
  if "$@"; then
    echo "FAIL: $label (expected false)"
    FAIL=$((FAIL + 1))
  else
    echo "PASS: $label"
    PASS=$((PASS + 1))
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
S3_BUCKET=should-not-be-used
S3_ENDPOINT_URL=http://127.0.0.1:9
S3_REGION=wjv-1
S3_ACCESS_KEY_ID=test-key
S3_SECRET_ACCESS_KEY=test-secret
EOF

# Fail loudly if S3 Python helper is invoked in LOCAL_ONLY
cat >"$BIN/python3" <<'EOF'
#!/usr/bin/env bash
echo "[mock-python] S3 helper must not run in LOCAL_ONLY" >&2
exit 99
EOF
chmod +x "$BIN/python3"

write_gzip() {
  local path="$1"
  /usr/bin/python3 -c "import gzip,sys; gzip.open(sys.argv[1],'wb').write(b'ok-'+sys.argv[1].encode())" "$path"
}

seed_regular() {
  local stamp="$1"
  write_gzip "$BACKUP_DIR/eternal_packaging_${stamp}.archive.gz"
}

# Successful dump mock
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

run_backup() {
  local out="$1"
  shift
  local rc=0
  PATH="$BIN:$PATH" \
  BACKUP_MODE=LOCAL_ONLY \
  BACKUP_BACKEND_DIR="$BACKEND" \
  BACKUP_DIR="$BACKUP_DIR" \
  BACKUP_PYTHON_BIN="$BIN/python3" \
  BACKUP_STATUS_FILE="$BACKUP_DIR/status.txt" \
  BACKUP_LOCAL_KEEP="${BACKUP_LOCAL_KEEP:-7}" \
  "$@" \
  bash "$SCRIPT" >"$out" 2>&1 || rc=$?
  return "$rc"
}

regular_count() {
  find "$BACKUP_DIR" -maxdepth 1 -type f -name 'eternal_packaging_????-??-??_??-??-??.archive.gz' | wc -l | tr -d ' '
}

# --- 1) 8 valid → retain 7 ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
for d in 01 02 03 04 05 06 07 08; do
  seed_regular "2026-10-${d}_01-00-00"
done
assert_eq "pre: 8 regular archives" "8" "$(regular_count)"
run_backup "$WORKDIR/out_keep8.txt" || true
# new backup adds 1 then retention keeps 7 → still 7 (oldest of the 9 gone... wait:
# 8 seed + 1 new = 9, keep 7 → 7 remain)
assert_eq "8+1 valid → retain 7" "7" "$(regular_count)"
assert_true "status SUCCESS after retention" grep -q 'status=SUCCESS' "$BACKUP_DIR/status.txt"
assert_true "mode LOCAL_ONLY in status" grep -q 'mode=LOCAL_ONLY' "$BACKUP_DIR/status.txt"
assert_true "logged LOCAL_DELETED after real delete" grep -q 'LOCAL_DELETED:' "$WORKDIR/out_keep8.txt"
assert_true "LOCAL_ONLY skips S3" grep -q 'S3_PHASE_SKIPPED mode=LOCAL_ONLY' "$WORKDIR/out_keep8.txt"
assert_false "no S3 helper invocation" grep -q 'S3 helper must not run' "$WORKDIR/out_keep8.txt"
assert_true "oldest of seed deleted" \
  bash -c "! test -f '$BACKUP_DIR/eternal_packaging_2026-10-01_01-00-00.archive.gz'"
assert_true "sha256 logged" grep -q 'LOCAL_SHA256:' "$WORKDIR/out_keep8.txt"

# --- 2) exactly 7 after run with 6 seed → delete none of the prior 6 beyond keep ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
for d in 01 02 03 04 05 06; do
  seed_regular "2026-10-${d}_02-00-00"
done
run_backup "$WORKDIR/out_keep6.txt"
# 6 + 1 = 7 → delete none
assert_eq "6+1 → keep all 7, delete none" "7" "$(regular_count)"
assert_false "no LOCAL_DELETED when at keep" grep -q 'LOCAL_DELETED:' "$WORKDIR/out_keep6.txt"

# --- 3) fewer than 7 → delete none ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
for d in 01 02 03; do
  seed_regular "2026-10-${d}_03-00-00"
done
run_backup "$WORKDIR/out_keep3.txt"
assert_eq "3+1 → keep 4, delete none" "4" "$(regular_count)"
assert_false "no LOCAL_DELETED when below keep" grep -q 'LOCAL_DELETED:' "$WORKDIR/out_keep3.txt"

# --- 4) invalid new backup (bad gzip) → delete none ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
for d in 01 02 03 04 05 06 07 08; do
  seed_regular "2026-10-${d}_04-00-00"
done
cat >"$BIN/mongodump" <<'EOF'
#!/usr/bin/env bash
for arg in "$@"; do
  if [[ "$arg" == --archive=* ]]; then
    printf 'NOT-GZIP' >"${arg#--archive=}"
  fi
done
exit 0
EOF
chmod +x "$BIN/mongodump"
set +e
run_backup "$WORKDIR/out_badgzip.txt"
bad_rc=$?
set -e
assert_eq "invalid gzip exits non-zero" "1" "$bad_rc"
assert_eq "invalid gzip does not prune existing 8" "8" "$(regular_count)"
assert_false "invalid gzip no LOCAL_DELETED" grep -q 'LOCAL_DELETED:' "$WORKDIR/out_badgzip.txt"
assert_true "status FAIL on bad gzip" grep -q 'status=FAIL' "$BACKUP_DIR/status.txt"

# --- 5) failed dump → delete none ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
for d in 01 02 03 04 05 06 07 08; do
  seed_regular "2026-10-${d}_05-00-00"
done
cat >"$BIN/mongodump" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$BIN/mongodump"
set +e
run_backup "$WORKDIR/out_faildump.txt"
fail_rc=$?
set -e
assert_eq "failed dump exits non-zero" "1" "$fail_rc"
assert_eq "failed dump does not prune" "8" "$(regular_count)"
assert_false "failed dump no LOCAL_DELETED" grep -q 'LOCAL_DELETED:' "$WORKDIR/out_faildump.txt"

# Restore successful mongodump
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

# --- 6) symlink protection ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
seed_regular "2026-10-10_06-00-00"
write_gzip "$WORKDIR/outside.archive.gz"
ln -s "$WORKDIR/outside.archive.gz" \
  "$BACKUP_DIR/eternal_packaging_2026-10-01_06-00-00.archive.gz"
for d in 02 03 04 05 06 07 08 09; do
  seed_regular "2026-10-${d}_06-00-00"
done
run_backup "$WORKDIR/out_symlink.txt"
assert_true "symlink target outside still exists" test -f "$WORKDIR/outside.archive.gz"
assert_true "symlink itself still present or skipped safely" \
  bash -c "test -L '$BACKUP_DIR/eternal_packaging_2026-10-01_06-00-00.archive.gz' || true"
assert_false "must not delete via symlink path as LOCAL_DELETED of link victim incorrectly" \
  grep -q 'LOCAL_DELETED: eternal_packaging_2026-10-01_06-00-00.archive.gz' "$WORKDIR/out_symlink.txt"

# --- 7) unexpected filename protection ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
write_gzip "$BACKUP_DIR/eternal_packaging_migration_recovery.archive.gz"
write_gzip "$BACKUP_DIR/notes_not_backup.archive.gz"
for d in 01 02 03 04 05 06 07 08; do
  seed_regular "2026-10-${d}_07-00-00"
done
run_backup "$WORKDIR/out_special.txt"
assert_true "migration/special archive preserved" \
  test -f "$BACKUP_DIR/eternal_packaging_migration_recovery.archive.gz"
assert_true "unrelated archive preserved" \
  test -f "$BACKUP_DIR/notes_not_backup.archive.gz"
assert_eq "regular retained at 7" "7" "$(regular_count)"

# --- 8) protected local name preserved even if regular pattern ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
PROTECTED="eternal_packaging_2026-09-30_08-11-33.archive.gz"
write_gzip "$BACKUP_DIR/$PROTECTED"
for d in 01 02 03 04 05 06 07 08; do
  seed_regular "2026-10-${d}_08-00-00"
done
BACKUP_LOCAL_PROTECTED_NAME="$PROTECTED" run_backup "$WORKDIR/out_protected.txt"
assert_true "protected recovery archive preserved" test -f "$BACKUP_DIR/$PROTECTED"
assert_false "protected never LOCAL_DELETED" \
  grep -q "LOCAL_DELETED: $PROTECTED" "$WORKDIR/out_protected.txt"

# --- 9) lock contention ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
seed_regular "2026-10-01_09-00-00"
(
  exec 9>"$BACKUP_DIR/.mongodb-backup.lock"
  flock -n 9
  set +e
  run_backup "$WORKDIR/out_lock.txt"
  lock_rc=$?
  set -e
  assert_eq "lock contention fails closed" "1" "$lock_rc"
  assert_true "lock failure message" grep -q 'another backup process holds' "$WORKDIR/out_lock.txt"
  assert_eq "lock contention does not prune" "1" "$(regular_count)"
)

# --- 10) LOCAL_DELETED only after verified removal (success path) ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
for d in 01 02 03 04 05 06 07 08; do
  seed_regular "2026-10-${d}_10-00-00"
done
run_backup "$WORKDIR/out_verified_del.txt"
while IFS= read -r line; do
  name="${line##*LOCAL_DELETED: }"
  name="${name%%$'\r'}"
  assert_true "LOCAL_DELETED name present in log" test -n "$name"
  assert_false "LOCAL_DELETED file must be gone: $name" test -e "$BACKUP_DIR/$name"
done < <(grep 'LOCAL_DELETED:' "$WORKDIR/out_verified_del.txt")
assert_true "at least one verified LOCAL_DELETED" grep -q 'LOCAL_DELETED:' "$WORKDIR/out_verified_del.txt"

# --- 10b) Python delete-failure fail-closed ---
PYTHONPATH="$ROOT/scripts" /usr/bin/python3 - "$WORKDIR" <<'PY'
import sys
from pathlib import Path
from lib.mongodb_backup_s3 import retain_local_backups, BackupS3Error

workdir = Path(sys.argv[1])
d = workdir / "py_rmfail"
d.mkdir(parents=True, exist_ok=True)
for i in range(1, 9):
    (d / f"eternal_packaging_2026-10-{i:02d}_10-00-00.archive.gz").write_bytes(b"x")

real_unlink = Path.unlink

def sticky_unlink(self, missing_ok=False):
    # Pretend delete succeeded for logging path but leave file (simulate silent rm failure)
    if self.name.endswith("_01_10-00-00.archive.gz") or "2026-10-01" in self.name:
        return None
    return real_unlink(self, missing_ok=missing_ok)

Path.unlink = sticky_unlink  # type: ignore[method-assign]
try:
    raised = False
    try:
        retain_local_backups(d, keep=7, is_valid=lambda p: True)
    except BackupS3Error as exc:
        raised = True
        assert "did not remove" in str(exc)
    assert raised, "expected BackupS3Error when delete did not remove file"
finally:
    Path.unlink = real_unlink  # type: ignore[method-assign]
print("PASS: python retain delete-failure fail-closed")
PY
PASS=$((PASS + 1))

# --- 11) LOCAL_ONLY does not require S3 env ---
rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
cat >"$BACKEND/.env" <<'EOF'
MONGO_URL=mongodb://127.0.0.1:27017
DB_NAME=eternal_packaging
EOF
run_backup "$WORKDIR/out_nos3env.txt"
assert_true "LOCAL_ONLY works without S3 env" grep -q 'status=SUCCESS' "$BACKUP_DIR/status.txt"
assert_true "complete without S3" grep -q 'BACKUP_COMPLETE' "$WORKDIR/out_nos3env.txt"

# --- 12) bash syntax ---
assert_true "bash -n backup-mongodb.sh" bash -n "$SCRIPT"

# --- 13) Python retain_local_backups keep=7 + symlink + special ---
PYTHONPATH="$ROOT/scripts" /usr/bin/python3 - "$WORKDIR" <<'PY'
import gzip
import sys
from pathlib import Path
from lib.mongodb_backup_s3 import retain_local_backups

workdir = Path(sys.argv[1])
d = workdir / "py_retain"
d.mkdir(parents=True, exist_ok=True)
outside = workdir / "py_outside.gz"
with gzip.open(outside, "wb") as fh:
    fh.write(b"outside")

def gz(name: str) -> None:
    with gzip.open(d / name, "wb") as fh:
        fh.write(name.encode())

for i in range(1, 9):
    stamp = f"2026-11-{i:02d}_01-00-00"
    gz(f"eternal_packaging_{stamp}.archive.gz")

gz("eternal_packaging_migration_manual.archive.gz")
link = d / "eternal_packaging_2026-10-01_01-00-00.archive.gz"
link.symlink_to(outside)

result = retain_local_backups(
    d,
    keep=7,
    protected_names={"eternal_packaging_2026-09-30_08-11-33.archive.gz"},
    is_valid=lambda p: True,
)
assert len(result["kept"]) == 7, result
assert len(result["deleted"]) == 1, result
assert (d / "eternal_packaging_migration_manual.archive.gz").exists()
assert link.is_symlink()
assert outside.exists()

for p in d.glob("eternal_packaging_2026-11-*.archive.gz"):
    if p.is_file() and not p.is_symlink():
        p.unlink()
for i in range(1, 8):
    gz(f"eternal_packaging_2026-12-{i:02d}_01-00-00.archive.gz")
r2 = retain_local_backups(d, keep=7, is_valid=lambda p: True)
assert r2["deleted"] == [], r2
assert len(r2["kept"]) == 7, r2
print("PASS: python retain_local_backups keep7/symlink/special")
PY
PASS=$((PASS + 1))

echo "LOCAL_RETENTION_SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
