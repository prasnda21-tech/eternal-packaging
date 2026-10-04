#!/usr/bin/env bash
# Eternal Packaging — 24/7-safe production deploy entrypoint.
#
# Usage (ON the VPS, after scripts are present in the repo checkout):
#   TARGET_SHA=<40-char> ./scripts/safe-deploy/deploy.sh
#
# Or dry-run locally:
#   DRY_RUN=1 SAFE_DEPLOY_TEST_ROOT=/tmp/safe-deploy-test TARGET_SHA=deadbeef... ./scripts/safe-deploy/deploy.sh
#
# NEVER:
#   - full collection hash / to_list(None) preflight
#   - build inside live webroot
#   - automatic database rollback
#   - git pull blindly
#
# Database policy: CODE rollback only. Never mongorestore as part of this script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "${SCRIPT_DIR}/config.sh"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/lock.sh
source "${SCRIPT_DIR}/lib/lock.sh"
# shellcheck source=lib/resource_guard.sh
source "${SCRIPT_DIR}/lib/resource_guard.sh"
# shellcheck source=lib/frontend.sh
source "${SCRIPT_DIR}/lib/frontend.sh"

TARGET_SHA="${TARGET_SHA:-${1:-}}"
[[ -n "$TARGET_SHA" ]] || die "TARGET_SHA required (exact commit to deploy)"
[[ "$TARGET_SHA" =~ ^[0-9a-f]{7,40}$ ]] || die "TARGET_SHA must be a git SHA"

PREV_SHA=""
FRONTEND_RELEASE=""
HEALTH_RESULT="unchecked"
RESULT="aborted"

cleanup() {
  local rc=$?
  release_deploy_lock || true
  if [[ "$RESULT" == "success" ]]; then
    record_deploy_event success "$PREV_SHA" "$TARGET_SHA" "$PREV_SHA" "$FRONTEND_RELEASE" "$HEALTH_RESULT" || true
  elif [[ "$RESULT" == "rolled_back" ]]; then
    record_deploy_event rolled_back "$PREV_SHA" "$TARGET_SHA" "$PREV_SHA" "$FRONTEND_RELEASE" "$HEALTH_RESULT" || true
  else
    record_deploy_event aborted "$PREV_SHA" "$TARGET_SHA" "" "$FRONTEND_RELEASE" "$HEALTH_RESULT" || true
  fi
  exit "$rc"
}
trap cleanup EXIT

phase "PREFLIGHT"
require_cmd git python3 curl

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  SAFE_DEPLOY_TEST_ROOT="${SAFE_DEPLOY_TEST_ROOT:-/tmp/safe-deploy-test-$$}"
  export SAFE_DEPLOY_TEST_ROOT
  mkdir -p "$SAFE_DEPLOY_TEST_ROOT"/{webroot/static/js,locks,releases,worktrees,app}
  # Preserve caller webroot content if already prepared by tests.
  if [[ ! -f "$SAFE_DEPLOY_TEST_ROOT/webroot/index.html" ]]; then
    echo '<!doctype html><title>old-live</title>' > "$SAFE_DEPLOY_TEST_ROOT/webroot/index.html"
    echo 'old' > "$SAFE_DEPLOY_TEST_ROOT/webroot/static/js/main.js"
  fi
  DEPLOY_LOCK_FILE="${DEPLOY_LOCK_FILE:-$SAFE_DEPLOY_TEST_ROOT/locks/deploy.lock}"
  DEPLOY_HISTORY_DIR="${DEPLOY_HISTORY_DIR:-$SAFE_DEPLOY_TEST_ROOT/history}"
  WEBROOT="${WEBROOT:-$SAFE_DEPLOY_TEST_ROOT/webroot}"
  PREVIOUS_WEBROOT="${PREVIOUS_WEBROOT:-$SAFE_DEPLOY_TEST_ROOT/webroot-previous}"
  RELEASES_ROOT="${RELEASES_ROOT:-$SAFE_DEPLOY_TEST_ROOT/releases}"
  APP_ROOT="${APP_ROOT_OVERRIDE:-${APP_ROOT:-$SAFE_DEPLOY_TEST_ROOT/app}}"
  if [[ ! -d "$APP_ROOT" ]]; then
    APP_ROOT="$SAFE_DEPLOY_TEST_ROOT/app"
    mkdir -p "$APP_ROOT"
  fi
  info "DRY_RUN root: $SAFE_DEPLOY_TEST_ROOT"
fi

export DEPLOY_LOCK_FILE DEPLOY_HISTORY_DIR WEBROOT PREVIOUS_WEBROOT RELEASES_ROOT APP_ROOT

acquire_deploy_lock "$TARGET_SHA"

if [[ "${DRY_RUN:-0}" != "1" ]]; then
  cd "$APP_ROOT"
  PREV_SHA="$(git rev-parse HEAD)"
  info "CURRENT LIVE SHA: $PREV_SHA"
  info "TARGET SHA: $TARGET_SHA"

  if [[ "${ALLOW_DIRTY_WORKTREE:-0}" != "1" ]]; then
    [[ -z "$(git status --porcelain)" ]] || die "dirty worktree — abort; OLD LIVE untouched"
  fi

  resource_guard_check

  for svc in "$BACKEND_SERVICE" "$NGINX_SERVICE" mongod; do
    [[ "$(systemctl is-active "$svc")" == "active" ]] || die "service not active: $svc"
  done

  run_timeout 120 git fetch --prune origin
  git cat-file -e "${TARGET_SHA}^{commit}" 2>/dev/null || die "TARGET_SHA not found after fetch: $TARGET_SHA"

  # Exact-SHA ancestry / expected presence
  if ! git merge-base --is-ancestor "$PREV_SHA" "$PREV_SHA"; then
    die "internal git error resolving PREV_SHA"
  fi
  info "exact-SHA mode: will checkout $TARGET_SHA (not blind pull)"
  info "diffstat preview:"
  git diff --stat "$PREV_SHA" "$TARGET_SHA" | tail -n 20 || true

  # Live health before we do anything
  run_timeout "$HEALTH_TIMEOUT" curl -fsS --max-time "$HEALTH_TIMEOUT" "$HEALTH_URL" >/tmp/safe-deploy-health-pre.json
  python3 - <<'PY'
import json
d=json.load(open("/tmp/safe-deploy-health-pre.json"))
assert d.get("mongo") is True and d.get("mode")=="operational", d
print("pre-cutover backend health OK")
PY

  if [[ "${SKIP_DATA_PREFLIGHT:-0}" != "1" ]]; then
    phase "PRE-CUTOVER DATA CHECK"
    run_timeout "$DATA_PREFLIGHT_TIMEOUT" \
      "${BACKEND_DIR}/.venv/bin/python" "${SCRIPT_DIR}/lib/preflight_data.py" \
      --env "${BACKEND_DIR}/.env" \
      --sku "$CRITICAL_SKU" \
      --material-id "$CRITICAL_SKU_MATERIAL_ID" \
      --out /tmp/safe-deploy-pre-data.json >/dev/null
    info "pre data snapshot: /tmp/safe-deploy-pre-data.json"
  fi
else
  PREV_SHA="previousdeadbeef"
  info "DRY_RUN PREV_SHA=$PREV_SHA TARGET=$TARGET_SHA"
  resource_guard_check
fi

phase "PREPARE RELEASE / BUILD VERIFY"
# Build outside active webroot. OLD LIVE keeps serving.
FRONTEND_RELEASE="$(build_frontend_from_sha "$TARGET_SHA")"
info "frontend release ready: $FRONTEND_RELEASE"

phase "PRE-CUTOVER CHECK"
if [[ "${DRY_RUN:-0}" != "1" ]]; then
  # Confirm LIVE still same SHA (no concurrent deploy mutated checkout)
  now_sha="$(git -C "$APP_ROOT" rev-parse HEAD)"
  [[ "$now_sha" == "$PREV_SHA" ]] || die "LIVE SHA changed during prepare ($now_sha != $PREV_SHA) — abort"
fi
verify_frontend_build_dir "$FRONTEND_RELEASE"
info "OLD LIVE still serving; cutover not started"

phase "CUTOVER"
# Order: publish frontend first (static), then backend code checkout + restart.
publish_frontend_atomic "$FRONTEND_RELEASE"
FRONTEND_RELEASE="$WEBROOT"

if [[ "${DRY_RUN:-0}" != "1" ]]; then
  info "checking out exact TARGET_SHA in app root"
  run_timeout "$CUTOVER_STEP_TIMEOUT" git -C "$APP_ROOT" checkout --detach "$TARGET_SHA"

  if [[ "${SIMULATE_BACKEND_FAILURE:-0}" == "1" ]]; then
    warn "SIMULATE_BACKEND_FAILURE=1"
  fi

  info "restarting backend once"
  run_timeout "$CUTOVER_STEP_TIMEOUT" sudo systemctl restart "$BACKEND_SERVICE"
else
  info "DRY_RUN skip git checkout / systemctl restart"
fi

phase "BACKEND HEALTH"
ok=0
if [[ "${SIMULATE_BACKEND_FAILURE:-0}" == "1" ]]; then
  ok=0
  HEALTH_RESULT="simulated_failure"
else
  for i in $(seq 1 "${HEALTH_RETRIES}"); do
    if body="$(run_timeout "$HEALTH_TIMEOUT" curl -fsS --max-time "$HEALTH_TIMEOUT" "$HEALTH_URL" 2>/dev/null)"; then
      if python3 -c "import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get('mongo') is True and d.get('mode')=='operational' else 1)" "$body"; then
        ok=1
        HEALTH_RESULT="mongo=true mode=operational"
        info "backend health OK (attempt $i)"
        break
      fi
    fi
    sleep "${HEALTH_RETRY_SLEEP}"
  done
fi

if [[ "$ok" != "1" ]]; then
  phase "ROLLBACK"
  warn "backend health failed — CODE rollback only (database NOT restored)"
  if [[ "${DRY_RUN:-0}" != "1" ]]; then
    run_timeout "$CUTOVER_STEP_TIMEOUT" git -C "$APP_ROOT" checkout --detach "$PREV_SHA"
    run_timeout "$CUTOVER_STEP_TIMEOUT" sudo systemctl restart "$BACKEND_SERVICE"
  fi
  rollback_frontend_previous || true
  # re-check
  if [[ "${DRY_RUN:-0}" != "1" ]]; then
    sleep 2
    run_timeout "$HEALTH_TIMEOUT" curl -fsS --max-time "$HEALTH_TIMEOUT" "$HEALTH_URL" >/tmp/safe-deploy-health-rb.json || true
  fi
  RESULT="rolled_back"
  die "deploy rolled back to $PREV_SHA"
fi

phase "FRONTEND HEALTH"
if [[ "${DRY_RUN:-0}" != "1" ]]; then
  fcode="$(run_timeout 15 curl -sk -o /dev/null -w '%{http_code}' --max-time 10 "$FRONTEND_HTTPS_URL" || echo 000)"
  info "frontend /login HTTP $fcode"
  [[ "$fcode" == "200" ]] || warn "frontend health returned $fcode"
else
  [[ -f "$WEBROOT/index.html" ]] || die "dry-run frontend missing after publish"
  info "DRY_RUN frontend present"
fi

phase "POST-CHECK"
if [[ "${DRY_RUN:-0}" != "1" && "${SKIP_DATA_PREFLIGHT:-0}" != "1" ]]; then
  run_timeout "$DATA_PREFLIGHT_TIMEOUT" \
    "${BACKEND_DIR}/.venv/bin/python" "${SCRIPT_DIR}/lib/preflight_data.py" \
    --env "${BACKEND_DIR}/.env" \
    --sku "$CRITICAL_SKU" \
    --material-id "$CRITICAL_SKU_MATERIAL_ID" \
    --out /tmp/safe-deploy-post-data.json >/dev/null
  info "post data snapshot: /tmp/safe-deploy-post-data.json"
  info "NOTE: counts may differ due to live user activity — not a corruption signal"
fi

if [[ "${DRY_RUN:-0}" != "1" ]]; then
  live_now="$(git -C "$APP_ROOT" rev-parse HEAD)"
  info "LIVE after deploy: $live_now"
  [[ "$live_now" == "$(git -C "$APP_ROOT" rev-parse "$TARGET_SHA")" ]] || die "LIVE SHA mismatch after deploy"
fi

RESULT="success"
phase "SUCCESS"
info "deployed $TARGET_SHA (previous $PREV_SHA)"
info "database auto-rollback: NO (policy)"
report_swap_recommendation
