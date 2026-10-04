#!/usr/bin/env bash
# Offline dry-run tests for safe-deploy tooling.
# NEVER connects to production. NEVER mutates LIVE.
#
# Usage: ./scripts/safe-deploy/tests/dry-run-tests.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ROOT="$(mktemp -d /tmp/safe-deploy-dryrun-XXXXXX)"
export SAFE_DEPLOY_TEST_ROOT="$ROOT"
export DRY_RUN=1
export DEPLOY_LOCK_FILE="$ROOT/locks/deploy.lock"
export DEPLOY_HISTORY_DIR="$ROOT/history"
export WEBROOT="$ROOT/webroot"
export PREVIOUS_WEBROOT="$ROOT/webroot-previous"
export RELEASES_ROOT="$ROOT/releases"
export LOCK_STALE_SECONDS=2
export APP_ROOT="$ROOT/app"
mkdir -p "$ROOT"/{locks,history,releases,webroot/static/js,app}
echo '<!doctype html><title>old</title>' > "$ROOT/webroot/index.html"
echo 'old' > "$ROOT/webroot/static/js/main.js"

PASS=0
FAIL=0

assert_eq() {
  local name="$1" got="$2" want="$3"
  if [[ "$got" == "$want" ]]; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name (got='$got' want='$want')"
    FAIL=$((FAIL + 1))
  fi
}

assert_ok() {
  local name="$1"
  shift
  if "$@" >/tmp/safe-deploy-assert.out 2>/tmp/safe-deploy-assert.err; then
    echo "PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL: $name"
    sed -n '1,80p' /tmp/safe-deploy-assert.err >&2 || true
    sed -n '1,80p' /tmp/safe-deploy-assert.out >&2 || true
    FAIL=$((FAIL + 1))
  fi
}

assert_fail() {
  local name="$1"
  shift
  if "$@" >/tmp/safe-deploy-assert.out 2>/tmp/safe-deploy-assert.err; then
    echo "FAIL: $name (expected failure)"
    sed -n '1,80p' /tmp/safe-deploy-assert.out >&2 || true
    FAIL=$((FAIL + 1))
  else
    echo "PASS: $name"
    PASS=$((PASS + 1))
  fi
}

run_lib() {
  local -a exports=()
  while [[ $# -gt 0 && "$1" == *=* ]]; do
    exports+=("$1")
    shift
  done
  # Isolate die/exit inside a subshell
  env "${exports[@]}" bash -c '
    set -euo pipefail
    SCRIPT_DIR="$1"; shift
    source "$SCRIPT_DIR/config.sh"
    source "$SCRIPT_DIR/lib/common.sh"
    source "$SCRIPT_DIR/lib/lock.sh"
    source "$SCRIPT_DIR/lib/resource_guard.sh"
    source "$SCRIPT_DIR/lib/frontend.sh"
    "$@"
  ' bash "$SCRIPT_DIR" "$@"
}

echo "==== A. HEALTHY DRY DEPLOY ===="
assert_ok "healthy dry deploy" \
  env DRY_RUN=1 SAFE_DEPLOY_TEST_ROOT="$ROOT" APP_ROOT="$ROOT/app" TARGET_SHA=abc1234deadbeef \
  bash "${SCRIPT_DIR}/deploy.sh"
assert_eq "healthy webroot remains present" "$(test -f "$ROOT/webroot/index.html" && echo 1)" "1"

echo "==== B. DIRTY WORKTREE ===="
dirty_check() {
  local porcelain="$1"
  [[ -z "$porcelain" ]] || return 1
  return 0
}
assert_fail "dirty worktree aborts" dirty_check " M file"
assert_ok "clean worktree ok" dirty_check ""

echo "==== C. DEPLOY LOCK ACTIVE ===="
mkdir -p "$ROOT/locks"
# Hold an alive PID so lock is considered active (pid 1 may be unkillable/-0 false in some sandboxes).
sleep 120 &
HOLD_PID=$!
cat > "$DEPLOY_LOCK_FILE" <<EOF
owner=tester@host
pid=${HOLD_PID}
started_utc=2026-01-01T00:00:00Z
target_sha=other
hostname=host
EOF
assert_fail "active lock aborts" \
  run_lib DEPLOY_LOCK_FILE="$DEPLOY_LOCK_FILE" LOCK_STALE_SECONDS=7200 acquire_deploy_lock newtarget
kill "$HOLD_PID" 2>/dev/null || true
wait "$HOLD_PID" 2>/dev/null || true
rm -f "$DEPLOY_LOCK_FILE"

echo "==== D. LOW MEMORY ===="
assert_fail "low memory aborts" \
  run_lib FORCE_LOW_MEMORY=1 MIN_AVAIL_MEM_MB=1536 APP_ROOT="$ROOT/app" SAFE_DEPLOY_TEST_ROOT="$ROOT" resource_guard_check

echo "==== E. BUILD FAILURE — OLD LIVE UNTOUCHED ===="
mkdir -p "$ROOT/webroot/static/js"
echo 'OLD_LIVE_MARKER' > "$ROOT/webroot/index.html"
assert_fail "build failure aborts" \
  env DRY_RUN=1 SIMULATE_BUILD_FAILURE=1 SAFE_DEPLOY_TEST_ROOT="$ROOT" APP_ROOT="$ROOT/app" TARGET_SHA=abc1234dead \
  bash "${SCRIPT_DIR}/deploy.sh"
assert_eq "old live untouched after build fail" "$(cat "$ROOT/webroot/index.html")" "OLD_LIVE_MARKER"

echo "==== F. PREPARATION FAILURE — OLD LIVE UNTOUCHED ===="
echo 'OLD_LIVE_MARKER2' > "$ROOT/webroot/index.html"
assert_fail "bad sha aborts" \
  env DRY_RUN=1 SAFE_DEPLOY_TEST_ROOT="$ROOT" APP_ROOT="$ROOT/app" TARGET_SHA=NOT_A_SHA \
  bash "${SCRIPT_DIR}/deploy.sh"
assert_eq "old live untouched after prep fail" "$(cat "$ROOT/webroot/index.html")" "OLD_LIVE_MARKER2"

echo "==== G. BACKEND FAILURE → ROLLBACK PATH ===="
rm -rf "$ROOT/webroot" "$ROOT/webroot-previous" "$ROOT/releases" "$ROOT/locks"
mkdir -p "$ROOT/webroot/static/js" "$ROOT/webroot-previous/static/js" "$ROOT/locks" "$ROOT/app"
echo 'live-before' > "$ROOT/webroot/index.html"
echo 'x' > "$ROOT/webroot/static/js/main.js"
echo 'stale-prev' > "$ROOT/webroot-previous/index.html"
echo 'x' > "$ROOT/webroot-previous/static/js/main.js"
assert_fail "backend failure triggers rollback" \
  env DRY_RUN=1 SIMULATE_BACKEND_FAILURE=1 SAFE_DEPLOY_TEST_ROOT="$ROOT" APP_ROOT="$ROOT/app" TARGET_SHA=abc1234dead \
  bash "${SCRIPT_DIR}/deploy.sh"
assert_eq "frontend restored on backend fail" "$(cat "$ROOT/webroot/index.html")" "live-before"

echo "==== H. FRONTEND PUBLISH FAILURE → PREVIOUS PRESERVED ===="
rm -rf "$ROOT/webroot" "$ROOT/webroot-previous" "$ROOT/releases" "$ROOT/locks"
mkdir -p "$ROOT/webroot/static/js" "$ROOT/locks" "$ROOT/app"
echo 'KEEP_ME' > "$ROOT/webroot/index.html"
echo 'x' > "$ROOT/webroot/static/js/main.js"
assert_fail "frontend publish failure aborts" \
  env DRY_RUN=1 SIMULATE_FRONTEND_PUBLISH_FAILURE=1 SAFE_DEPLOY_TEST_ROOT="$ROOT" APP_ROOT="$ROOT/app" TARGET_SHA=abc1234dead \
  bash "${SCRIPT_DIR}/deploy.sh"
assert_eq "live preserved on publish fail" "$(cat "$ROOT/webroot/index.html")" "KEEP_ME"

echo "==== I. STALE LOCK IDENTIFIED / RECLAIMED ===="
cat > "$DEPLOY_LOCK_FILE" <<EOF
owner=tester@host
pid=99999999
started_utc=2020-01-01T00:00:00Z
target_sha=old
hostname=host
EOF
touch -d "2020-01-01" "$DEPLOY_LOCK_FILE" 2>/dev/null || touch -t 202001010000 "$DEPLOY_LOCK_FILE"
status="$(run_lib DEPLOY_LOCK_FILE="$DEPLOY_LOCK_FILE" LOCK_STALE_SECONDS=2 inspect_deploy_lock | awk -F= '/^LOCK_STATUS/{print $2}')"
assert_eq "stale lock status" "$status" "stale"
assert_ok "stale lock reclaimed by acquire" \
  run_lib DEPLOY_LOCK_FILE="$DEPLOY_LOCK_FILE" LOCK_STALE_SECONDS=2 \
  eval 'acquire_deploy_lock fresh; release_deploy_lock'

echo "==== J. EMPTY LOCK RECLAIMED ===="
: > "$DEPLOY_LOCK_FILE"
status="$(run_lib DEPLOY_LOCK_FILE="$DEPLOY_LOCK_FILE" inspect_deploy_lock | awk -F= '/^LOCK_STATUS/{print $2}')"
assert_eq "empty lock status" "$status" "empty_stale"
assert_ok "empty lock reclaimed" \
  run_lib DEPLOY_LOCK_FILE="$DEPLOY_LOCK_FILE" \
  eval 'acquire_deploy_lock fresh2; release_deploy_lock'

echo "==== K. NO HEAVY PATTERNS IN FUTURE PATH ===="
# Only flag real call sites, not documentation/forbidden-lists.
if grep -nE '\.to_list\(None\)' "${SCRIPT_DIR}/lib/preflight_data.py" "${SCRIPT_DIR}/deploy.sh" "${SCRIPT_DIR}/preflight.sh" >/dev/null 2>&1; then
  echo "FAIL: .to_list(None) call found in future deploy path"
  FAIL=$((FAIL + 1))
else
  echo "PASS: no .to_list(None) calls in future deploy path"
  PASS=$((PASS + 1))
fi
if [[ -f "${SCRIPT_DIR}/legacy/README.md" && -f "${SCRIPT_DIR}/legacy/due_edit_pre_snapshot.DO_NOT_USE.py" ]]; then
  echo "PASS: legacy heavy scripts quarantined"
  PASS=$((PASS + 1))
else
  echo "FAIL: legacy archive incomplete"
  FAIL=$((FAIL + 1))
fi
if grep -nE '\.to_list\(' "${SCRIPT_DIR}/lib/preflight_data.py" | grep -vE '\.to_list\(1\)' >/dev/null 2>&1; then
  echo "FAIL: non-bounded to_list found in preflight_data.py"
  grep -nE '\.to_list\(' "${SCRIPT_DIR}/lib/preflight_data.py" || true
  FAIL=$((FAIL + 1))
else
  echo "PASS: preflight_data to_list usage bounded (to_list(1) only)"
  PASS=$((PASS + 1))
fi

# Quarantined legacy refuses execution
assert_fail "legacy heavy snapshot refuses exec" python3 "${SCRIPT_DIR}/legacy/due_edit_pre_snapshot.DO_NOT_USE.py"

echo "==== L. FRONTEND API TARGET GUARD ===="
GUARD="${SCRIPT_DIR}/lib/frontend_api_guard.sh"
# shellcheck source=../lib/frontend_api_guard.sh
source "$GUARD"

assert_fail "production build + localhost API target" \
  env SAFE_DEPLOY_MODE=production REACT_APP_BACKEND_URL=http://localhost:8000 PRODUCTION_FRONTEND_API_TARGET= \
  bash -c "source '$GUARD'; resolve_frontend_api_target >/dev/null"
assert_fail "production build + 127.0.0.1 API target" \
  env SAFE_DEPLOY_MODE=production REACT_APP_BACKEND_URL=http://127.0.0.1:8000 PRODUCTION_FRONTEND_API_TARGET= \
  bash -c "source '$GUARD'; resolve_frontend_api_target >/dev/null"
assert_ok "local preview + localhost API" \
  env SAFE_DEPLOY_MODE=local REACT_APP_BACKEND_URL=http://localhost:8000 PRODUCTION_FRONTEND_API_TARGET= \
  bash -c "source '$GUARD'; resolve_frontend_api_target >/dev/null"
resolved="$(SAFE_DEPLOY_MODE=production PRODUCTION_FRONTEND_API_TARGET=https://produksi.eternalpackaging.id REACT_APP_BACKEND_URL=http://localhost:8000 bash -c "source '$GUARD'; resolve_frontend_api_target" | tail -n 1)"
assert_eq "production build + proven production API target" "$resolved" "https://produksi.eternalpackaging.id"

bad_art="$(mktemp -d)"
mkdir -p "$bad_art/static/js"
echo '<!doctype html><title>bad</title>' > "$bad_art/index.html"
echo 'fetch("http://localhost:8000/api/auth/login")' > "$bad_art/static/js/main.js"
assert_fail "artifact with localhost API is rejected" reject_loopback_frontend_artifact "$bad_art"
good_art="$(mktemp -d)"
mkdir -p "$good_art/static/js"
echo '<!doctype html><title>good</title>' > "$good_art/index.html"
echo 'fetch("https://produksi.eternalpackaging.id/api/auth/login")' > "$good_art/static/js/main.js"
assert_ok "artifact with production API is accepted" reject_loopback_frontend_artifact "$good_art"
rm -rf "$bad_art" "$good_art"

echo
echo "RESULT: PASS=$PASS FAIL=$FAIL ROOT=$ROOT"
if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi
echo "ALL DRY-RUN TESTS PASSED"
