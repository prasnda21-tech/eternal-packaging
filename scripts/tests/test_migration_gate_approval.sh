#!/usr/bin/env bash
# Focused proofs for SHA-scoped migration gate approvals (fail-closed).
# Does not execute migrations / DB mutations.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=../deploy/lib.sh
source "$ROOT/scripts/deploy/lib.sh"

PASS=0
FAIL=0
REPORT="${ETERNAL_TEST_REPORT:-/opt/cursor/artifacts/migration-gate-approval.log}"
mkdir -p "$(dirname "$REPORT")"
: >"$REPORT"

log() { printf '%s\n' "$*" | tee -a "$REPORT"; }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then log "PASS: $label"; PASS=$((PASS + 1))
  else log "FAIL: $label expected=[$expected] actual=[$actual]"; FAIL=$((FAIL + 1)); fi
}
assert_true() {
  local label="$1"; shift
  if "$@"; then log "PASS: $label"; PASS=$((PASS + 1)); else log "FAIL: $label"; FAIL=$((FAIL + 1)); fi
}
assert_false() {
  local label="$1"; shift
  if "$@"; then log "FAIL: $label (expected failure)"; FAIL=$((FAIL + 1)); else log "PASS: $label"; PASS=$((PASS + 1)); fi
}

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

REPO="$WORKDIR/repo"
APPROVALS="$WORKDIR/approvals"
mkdir -p "$REPO" "$APPROVALS"
git init -q -b main "$REPO"
git -C "$REPO" config user.email "gate-test@example.com"
git -C "$REPO" config user.name "Gate Test"

mkdir -p "$REPO/backend/scripts" "$REPO/backend/services" "$REPO/backend/tests" "$REPO/scripts/deploy"
printf 'base\n' >"$REPO/backend/app.py"
printf 'ok\n' >"$REPO/scripts/deploy/lib.sh"
git -C "$REPO" add . && git -C "$REPO" commit -q -m base
FROM_SHA="$(git -C "$REPO" rev-parse HEAD)"

# Three audited migration-sensitive paths (same names as PR #711).
printf 'migrate-v1\n' >"$REPO/backend/scripts/migrate_sales_customers_historical.py"
printf 'service-v1\n' >"$REPO/backend/services/sales_customer_historical_migration.py"
printf 'test-v1\n' >"$REPO/backend/tests/test_sales_customer_historical_migration.py"
git -C "$REPO" add . && git -C "$REPO" commit -q -m 'add historical migration files'
TARGET_SHA="$(git -C "$REPO" rev-parse HEAD)"

BLOB_SCRIPT="$(git -C "$REPO" rev-parse "${TARGET_SHA}:backend/scripts/migrate_sales_customers_historical.py")"
BLOB_SERVICE="$(git -C "$REPO" rev-parse "${TARGET_SHA}:backend/services/sales_customer_historical_migration.py")"
BLOB_TEST="$(git -C "$REPO" rev-parse "${TARGET_SHA}:backend/tests/test_sales_customer_historical_migration.py")"

write_approval() {
  local out="$1" sha="$2" b_script="$3" b_service="$4" b_test="$5"
  cat >"$out" <<EOF
schema=eternal.deploy.migration_approval.v1
target_sha=${sha}
path=backend/scripts/migrate_sales_customers_historical.py
blob=${b_script}
path=backend/services/sales_customer_historical_migration.py
blob=${b_service}
path=backend/tests/test_sales_customer_historical_migration.py
blob=${b_test}
EOF
  chmod 644 "$out"
}

# --- 1) No approval → BLOCK ---
assert_false "1) without approval blocks" \
  eternal_blocked_migrations_are_approved "$FROM_SHA" "$TARGET_SHA" "" "$REPO" "$APPROVALS"

# --- 2) SHA mismatch → BLOCK ---
write_approval "$APPROVALS/bad-sha.v1" "$(printf 'a%.0s' {1..40})" "$BLOB_SCRIPT" "$BLOB_SERVICE" "$BLOB_TEST"
assert_false "2) mismatched target_sha blocks" \
  eternal_blocked_migrations_are_approved "$FROM_SHA" "$TARGET_SHA" "" "$REPO" "$APPROVALS"
rm -f "$APPROVALS/bad-sha.v1"

# --- 3) Extra migration-sensitive file in range → BLOCK ---
write_approval "$APPROVALS/ok.v1" "$TARGET_SHA" "$BLOB_SCRIPT" "$BLOB_SERVICE" "$BLOB_TEST"
printf 'extra\n' >"$REPO/backend/scripts/migrate_extra_thing.py"
git -C "$REPO" add . && git -C "$REPO" commit -q -m 'extra migration'
EXTRA_SHA="$(git -C "$REPO" rev-parse HEAD)"
assert_false "3) extra migration file blocks even with 3-file approval" \
  eternal_blocked_migrations_are_approved "$FROM_SHA" "$EXTRA_SHA" "" "$REPO" "$APPROVALS"
# reset to TARGET_SHA tip for remaining cases
git -C "$REPO" reset -q --hard "$TARGET_SHA"
rm -f "$APPROVALS/ok.v1"

# --- 4) Valid approval, three identical files → PASS ---
write_approval "$APPROVALS/ok.v1" "$TARGET_SHA" "$BLOB_SCRIPT" "$BLOB_SERVICE" "$BLOB_TEST"
assert_true "4) valid SHA+path+blob approval passes" \
  eternal_blocked_migrations_are_approved "$FROM_SHA" "$TARGET_SHA" "" "$REPO" "$APPROVALS"

# --- 5) Migration content change → BLOCK ---
printf 'migrate-v2-changed\n' >"$REPO/backend/scripts/migrate_sales_customers_historical.py"
git -C "$REPO" add . && git -C "$REPO" commit -q -m 'change migration content'
CHANGED_SHA="$(git -C "$REPO" rev-parse HEAD)"
# Approval still pins old target_sha + old blobs
assert_false "5a) content change with old target_sha approval blocks" \
  eternal_blocked_migrations_are_approved "$FROM_SHA" "$CHANGED_SHA" "" "$REPO" "$APPROVALS"
# Even if target_sha updated but blob stale:
NEW_BLOB_SCRIPT="$(git -C "$REPO" rev-parse "${CHANGED_SHA}:backend/scripts/migrate_sales_customers_historical.py")"
write_approval "$APPROVALS/stale-blob.v1" "$CHANGED_SHA" "$BLOB_SCRIPT" "$BLOB_SERVICE" "$BLOB_TEST"
assert_false "5b) updated SHA but stale blob blocks" \
  eternal_blocked_migrations_are_approved "$FROM_SHA" "$CHANGED_SHA" "" "$REPO" "$APPROVALS"
assert_true "5c) content identity differs from audited blob" \
  test "$NEW_BLOB_SCRIPT" != "$BLOB_SCRIPT"
git -C "$REPO" reset -q --hard "$TARGET_SHA"
rm -f "$APPROVALS/stale-blob.v1"

# --- 6) Approval must never imply migration execution ---
# Static contract: runner only logs acceptance; no migrate_/apply_historical calls.
assert_true "6a) runner does not invoke historical migrate CLI" \
  bash -c '! grep -qE "migrate_sales_customers_historical|apply_historical_customer_migration" "'"$ROOT"'/scripts/deploy/eternal-deploy-runner.sh"'
assert_true "6b) approval helper name does not shell-out to python migrate" \
  bash -c '! grep -qE "python.*migrate_|--owner-approved" "'"$ROOT"'/scripts/deploy/lib.sh"'
assert_true "6c) accepted approval path still only gates paths" \
  grep -q 'migrations not executed' "$ROOT/scripts/deploy/eternal-deploy-runner.sh"

# Re-assert PASS still works after content reset
assert_true "6d) approval still passes after content restored" \
  eternal_blocked_migrations_are_approved "$FROM_SHA" "$TARGET_SHA" "" "$REPO" "$APPROVALS"

# World-writable approval file must be refused
chmod 666 "$APPROVALS/ok.v1"
assert_false "6e) world-writable approval file refused" \
  eternal_blocked_migrations_are_approved "$FROM_SHA" "$TARGET_SHA" "" "$REPO" "$APPROVALS"
chmod 644 "$APPROVALS/ok.v1"

# --- 7) Empty migration range still OK (regression: no false refuse) ---
assert_true "7a) no migration paths in range → approved (noop)" \
  eternal_blocked_migrations_are_approved "$TARGET_SHA" "$TARGET_SHA" "" "$REPO" "$APPROVALS"
# Real repo approval file parses and pins audited blobs
assert_true "7b) production approval file parses" \
  eternal_parse_migration_approval_file \
    "$ROOT/scripts/deploy/migration-approvals/sales-customer-historical-pr711.v1"
assert_eq "7c) production approval target_sha" \
  "99c201be32f974161a0cb552805267d68fed68b9" "$ETERNAL_APPROVAL_TARGET_SHA"
# Verify real incident range against the committed approval (when SHAs exist).
if git -C "$ROOT" rev-parse --verify 5456ded0^{commit} >/dev/null 2>&1 \
  && git -C "$ROOT" rev-parse --verify 99c201be32f974161a0cb552805267d68fed68b9^{commit} >/dev/null 2>&1; then
  PREV_REAL="$(git -C "$ROOT" rev-parse 5456ded0)"
  TARGET_REAL="99c201be32f974161a0cb552805267d68fed68b9"
  assert_true "7d) real audited range 5456ded0..99c201be approved" \
    eternal_blocked_migrations_are_approved "$PREV_REAL" "$TARGET_REAL" "" "$ROOT" \
    "$ROOT/scripts/deploy/migration-approvals"
fi

# --- 8) bash -n + existing path classifiers still sane ---
assert_true "8a) bash -n lib.sh" bash -n "$ROOT/scripts/deploy/lib.sh"
assert_true "8b) bash -n eternal-deploy-runner.sh" bash -n "$ROOT/scripts/deploy/eternal-deploy-runner.sh"
assert_true "8c) bash -n install-gate.sh" bash -n "$ROOT/scripts/deploy/install-gate.sh"
assert_true "8d) bash -n mint-migration-approval.sh" bash -n "$ROOT/scripts/deploy/mint-migration-approval.sh"
assert_true "8e) migrate_*.py still classified blocked" \
  eternal_is_blocked_migration_path "backend/scripts/migrate_sales_customers_historical.py"
assert_true "8f) *migration*.py under backend/tests still classified blocked" \
  eternal_is_blocked_migration_path "backend/tests/test_sales_customer_historical_migration.py"
assert_false "8g) unrelated backend module not blocked" \
  eternal_is_blocked_migration_path "backend/services/sales_customers.py"

# Env bypass must not be required / used by new path
assert_true "8h) new approval path does not reference ETERNAL_ALLOW_MIGRATIONS" \
  bash -c '! grep -q ETERNAL_ALLOW_MIGRATIONS "'"$ROOT"'/scripts/deploy/lib.sh"'

log "RESULT pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
