#!/usr/bin/env bash
# Isolated-release deploy runner for Eternal Packaging (fail-closed).
#
# Order:
#   lock → preflight → baseline/provenance → materialize → build (isolated)
#   → verify backup → switch current → restart if needed → verify runtime+health
#   → atomic frontend publish (assets then index) → health → mark ok
#
# Lock is held through rollback/cleanup that affects live.
# Never uses unrestricted sudo. Never prints secrets. Never mutates Mongo.

set -euo pipefail

RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$RUNNER_DIR/lib.sh"

# Dedicated runtime root (writable by deploy user). Never chown /opt or /var/www parents.
ETERNAL_RUNTIME_ROOT="${ETERNAL_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
ETERNAL_RELEASES_ROOT="${ETERNAL_RELEASES_ROOT:-$ETERNAL_RUNTIME_ROOT/releases}"
ETERNAL_CURRENT_LINK="${ETERNAL_CURRENT_LINK:-$ETERNAL_RUNTIME_ROOT/current}"
ETERNAL_LEGACY_ROOT="${ETERNAL_LEGACY_ROOT:-/opt/eternal-packaging}"
ETERNAL_FRONTEND_ROOT="${ETERNAL_FRONTEND_ROOT:-/var/www/eternal-packaging}"
# FE backup lives under runtime root (not a /var/www sibling — parent is often root-owned).
ETERNAL_FRONTEND_PREV="${ETERNAL_FRONTEND_PREV:-$ETERNAL_RUNTIME_ROOT/frontend-prev}"
ETERNAL_DEPLOY_LOCK="${ETERNAL_DEPLOY_LOCK:-/tmp/eternal-packaging-deploy.lock}"
ETERNAL_SERVICE_NAME="${ETERNAL_SERVICE_NAME:-eternal-packaging-backend.service}"
ETERNAL_HEALTH_URL="${ETERNAL_HEALTH_URL:-http://127.0.0.1:8000/api/system/health}"
# Public Nginx origin. Production default is the HTTPS site hostname.
# Synthetic tests override this with a fixture origin. Backend :8000 is not a substitute.
# Optional ETERNAL_FRONTEND_PUBLIC_HOST overrides the expected hostname.
ETERNAL_FRONTEND_PUBLIC_URL="${ETERNAL_FRONTEND_PUBLIC_URL:-https://produksi.eternalpackaging.id}"
ETERNAL_FRONTEND_HTTP_TIMEOUT_S="${ETERNAL_FRONTEND_HTTP_TIMEOUT_S:-5}"
# Wall-clock budget for post-restart / post-publish health (includes transient HTTP 502).
ETERNAL_HEALTH_BUDGET_S="${ETERNAL_HEALTH_BUDGET_S:-60}"
ETERNAL_HEALTH_SLEEP_S="${ETERNAL_HEALTH_SLEEP_S:-2}"
# Optional attempt cap for tests (0 = budget-only). Production default is budget-only.
ETERNAL_HEALTH_RETRIES="${ETERNAL_HEALTH_RETRIES:-0}"
ETERNAL_MAIN_REF="${ETERNAL_MAIN_REF:-main}"
ETERNAL_ALLOW_MIGRATIONS="${ETERNAL_ALLOW_MIGRATIONS:-0}"
ETERNAL_MIRROR_DIR="${ETERNAL_MIRROR_DIR:-$ETERNAL_RELEASES_ROOT/mirror.git}"
ETERNAL_GIT_REMOTE="${ETERNAL_GIT_REMOTE:-}"
ETERNAL_SYSTEMCTL_BIN="${ETERNAL_SYSTEMCTL_BIN:-systemctl}"
ETERNAL_SYSTEMCTL_SUDO="${ETERNAL_SYSTEMCTL_SUDO:-sudo -n}"
ETERNAL_USE_SYSTEMCTL_SUDO="${ETERNAL_USE_SYSTEMCTL_SUDO:-1}"
ETERNAL_CURL_BIN="${ETERNAL_CURL_BIN:-curl}"
ETERNAL_SKIP_FETCH="${ETERNAL_SKIP_FETCH:-0}"
ETERNAL_DRY_BUILD="${ETERNAL_DRY_BUILD:-0}"
# Frontend acquisition mode:
#   artifact — verified GHA/local artifact only (default; never yarn fallback)
#   legacy   — explicit VPS yarn install/build (Owner override only)
#   dry      — synthetic fixtures (tests); also implied by ETERNAL_DRY_BUILD=1
ETERNAL_FRONTEND_BUILD_MODE="${ETERNAL_FRONTEND_BUILD_MODE:-artifact}"
ETERNAL_BASELINE_DIR="${ETERNAL_BASELINE_DIR:-$ETERNAL_RELEASES_ROOT/baseline-active}"
ETERNAL_EXPECTED_WD="${ETERNAL_EXPECTED_WD:-$ETERNAL_CURRENT_LINK/backend}"
ETERNAL_EXPECTED_EXEC_PREFIX="${ETERNAL_EXPECTED_EXEC_PREFIX:-$ETERNAL_CURRENT_LINK/backend}"
ETERNAL_MIN_DISK_KB="${ETERNAL_MIN_DISK_KB:-524288}"
ETERNAL_NODE_OPTIONS="${ETERNAL_NODE_OPTIONS:---max-old-space-size=2048}"
ETERNAL_FRONTEND_ENV_FILE="${ETERNAL_FRONTEND_ENV_FILE:-}"
# Optional read-only overlay marker: if legacy tree looks dirty without verified baseline, block.
ETERNAL_REQUIRE_VERIFIED_BASELINE="${ETERNAL_REQUIRE_VERIFIED_BASELINE:-1}"
ETERNAL_RUNTIME_MODE="${ETERNAL_RUNTIME_MODE:-full}"
ETERNAL_PYTHON_BIN="${ETERNAL_PYTHON_BIN:-python3}"
# Keep newest N identity-scoped rollback points (active SHA never pruned).
ETERNAL_ROLLBACK_POINT_RETENTION="${ETERNAL_ROLLBACK_POINT_RETENTION:-5}"

PREV_RELEASE=""
PREV_IS_BASELINE=0
NEW_RELEASE=""
TARGET_SHA=""
SWITCHED=0
FRONTEND_SWITCHED=0
BACKEND_RESTART_ATTEMPTED=0
BACKEND_RESTARTED=0
LOCK_HELD=0
FRONTEND_PUBLISH_STARTED=0
ROLLING_BACK=0
ETERNAL_REUSED_RELEASE=0
# deploy = failure-path auto rollback; post_success = Owner-requested cmd_rollback
RUNNER_MODE="deploy"

ROLLBACK_STATUS="" # PASS | FAILED | SUCCESS | "" (no rollback attempted)

cleanup() {
  local ec=$?
  if [[ "$RUNNER_MODE" == "deploy" && "$ec" -ne 0 ]]; then
    eternal_deploy_log "deploy failed (exit=$ec); attempting rollback while holding lock"
    ROLLING_BACK=1
    if eternal_rollback; then
      ROLLBACK_STATUS="PASS"
    else
      ROLLBACK_STATUS="FAILED"
      eternal_deploy_log "ROLLBACK_FAILED (lock still held until release below)"
    fi
  fi
  if (( LOCK_HELD == 1 )); then
    eternal_release_lock "$ETERNAL_DEPLOY_LOCK"
    LOCK_HELD=0
  fi
  if [[ "$ROLLBACK_STATUS" == "FAILED" && "$ec" -eq 0 ]]; then
    ec=1
  fi
  exit "$ec"
}

# Mutating systemctl (restart) — sudo when enabled.
eternal_systemctl() {
  if [[ "$ETERNAL_USE_SYSTEMCTL_SUDO" == "1" ]]; then
    # shellcheck disable=SC2086
    $ETERNAL_SYSTEMCTL_SUDO "$ETERNAL_SYSTEMCTL_BIN" "$@"
  else
    "$ETERNAL_SYSTEMCTL_BIN" "$@"
  fi
}

# Read-only systemctl: prefer unprivileged access; optional exact sudo fallback.
# Never uses wildcard sudo. Allowed sudo show forms are listed in bootstrap-permissions.sh.
eternal_systemctl_read() {
  local out ec
  set +e
  out="$("$ETERNAL_SYSTEMCTL_BIN" "$@" 2>/dev/null)"
  ec=$?
  set -e
  if [[ "$ec" -eq 0 ]]; then
    printf '%s' "$out"
    return 0
  fi
  if [[ "$ETERNAL_USE_SYSTEMCTL_SUDO" == "1" ]]; then
    set +e
    # shellcheck disable=SC2086
    out="$($ETERNAL_SYSTEMCTL_SUDO "$ETERNAL_SYSTEMCTL_BIN" "$@" 2>/dev/null)"
    ec=$?
    set -e
    if [[ "$ec" -eq 0 ]]; then
      printf '%s' "$out"
      return 0
    fi
  fi
  return 1
}

eternal_systemctl_show_value() {
  local prop="$1"
  eternal_systemctl_read show -p "$prop" --value "$ETERNAL_SERVICE_NAME"
}

eternal_systemctl_is_active() {
  eternal_systemctl_read is-active "$ETERNAL_SERVICE_NAME" >/dev/null
}

# Restore frontend INTO existing webroot (no rename of /var/www child — parent may be root-owned).
# Skip byte-identical hashed files; refuse hashed collisions; atomic index last.
# Returns 0/1 (does not exit) so rollback can continue other components.
eternal_restore_frontend_tree() {
  local src="$1"
  if [[ ! -d "$src" ]]; then
    eternal_deploy_log "frontend restore source missing: $src"
    return 1
  fi
  if [[ "${ETERNAL_FORCE_FRONTEND_RESTORE_FAIL:-0}" == "1" ]]; then
    eternal_deploy_log "frontend restore failed (forced)"
    return 1
  fi
  if [[ ! -d "$ETERNAL_FRONTEND_ROOT" ]]; then
    mkdir -p "$ETERNAL_FRONTEND_ROOT" || return 1
  fi
  if [[ ! -w "$ETERNAL_FRONTEND_ROOT" ]]; then
    eternal_deploy_log "frontend root not writable: $ETERNAL_FRONTEND_ROOT"
    return 1
  fi

  eternal_webroot_merge_tree "$src" "$ETERNAL_FRONTEND_ROOT" 1 || return 1
  eternal_atomic_publish_index "$src/index.html" "$ETERNAL_FRONTEND_ROOT" 1 || return 1
  if ! eternal_frontend_nginx_ready "$ETERNAL_FRONTEND_ROOT"; then
    eternal_deploy_log "frontend restore: public access check failed"
    return 1
  fi
  return 0
}

# Attempt full rollback while lock is held. Continues other components if one fails.
# Prints ROLLBACK_PASS or ROLLBACK_FAILED. Returns 0 only on PASS.
eternal_rollback() {
  local rb_fail=0
  local fe_src=""
  local had_switch="$SWITCHED"
  local need_backend_restart=0
  if [[ "$BACKEND_RESTART_ATTEMPTED" == "1" || "$BACKEND_RESTARTED" == "1" || "$PREV_IS_BASELINE" == "1" ]]; then
    need_backend_restart=1
  fi

  # Frontend first if publish started/switched — in-place restore only.
  if [[ "$FRONTEND_SWITCHED" == "1" || "$FRONTEND_PUBLISH_STARTED" == "1" ]]; then
    if [[ -d "$ETERNAL_FRONTEND_PREV" ]]; then
      fe_src="$ETERNAL_FRONTEND_PREV"
    elif [[ "$PREV_IS_BASELINE" == "1" && -d "$ETERNAL_BASELINE_DIR/frontend-snapshot" ]]; then
      fe_src="$ETERNAL_BASELINE_DIR/frontend-snapshot"
    fi
    if [[ -z "$fe_src" ]]; then
      eternal_deploy_log "rollback: no frontend backup/snapshot available"
      rb_fail=1
    elif [[ "$FRONTEND_SWITCHED" != "1" ]] \
      && eternal_frontend_required_state_ok "$ETERNAL_FRONTEND_ROOT" "$fe_src"; then
      # Publish failed before atomic index swap — required FE state already matches backup
      # (index/manifest/main assets). Extra unreferenced files do not force overwrite.
      eternal_deploy_log "rollback: frontend index unchanged; required FE state already OK (skip forced overwrite)"
      FRONTEND_SWITCHED=0
      FRONTEND_PUBLISH_STARTED=0
    else
      # Index switched or required state drifted (e.g. mutable manifest written before a
      # later publish failure). In-place restore; soft merge keeps identical hashed assets
      # and safe .js.map/.css.map collisions (SKIP_SOURCEMAP_COLLISION) without failing rollback.
      eternal_deploy_log "rollback: restore frontend from $fe_src (in-place)"
      if eternal_restore_frontend_tree "$fe_src" \
        && eternal_frontend_required_state_ok "$ETERNAL_FRONTEND_ROOT" "$fe_src"; then
        FRONTEND_SWITCHED=0
        FRONTEND_PUBLISH_STARTED=0
      else
        eternal_deploy_log "rollback: frontend restore/verify FAILED (continuing other components)"
        rb_fail=1
      fi
    fi
  fi

  if [[ "$had_switch" == "1" && -n "${PREV_RELEASE}" ]]; then
    eternal_deploy_log "rollback: restore current link -> $PREV_RELEASE"
    if ln -sfn "$PREV_RELEASE" "${ETERNAL_CURRENT_LINK}.tmp" \
      && mv -Tf "${ETERNAL_CURRENT_LINK}.tmp" "$ETERNAL_CURRENT_LINK"; then
      SWITCHED=0
    else
      eternal_deploy_log "rollback: CURRENT pointer restore FAILED"
      rb_fail=1
    fi
    if [[ "$need_backend_restart" == "1" ]]; then
      eternal_deploy_log "rollback: restart backend against restored target"
      if eternal_systemctl restart "$ETERNAL_SERVICE_NAME"; then
        BACKEND_RESTARTED=0
        BACKEND_RESTART_ATTEMPTED=0
      else
        eternal_deploy_log "rollback: backend restart FAILED"
        rb_fail=1
      fi
    fi
  fi
  rm -f "${ETERNAL_FRONTEND_ROOT}/.deploy-in-progress" 2>/dev/null || true
  rm -f "${ETERNAL_FRONTEND_ROOT}/index.html.new" 2>/dev/null || true

  # Post-rollback verification (pointer / process / health / index).
  local cur_resolved prev_resolved
  if [[ "$had_switch" == "1" && -n "${PREV_RELEASE}" ]]; then
    cur_resolved="$(readlink -f "$ETERNAL_CURRENT_LINK" 2>/dev/null || true)"
    prev_resolved="$(readlink -f "$PREV_RELEASE" 2>/dev/null || true)"
    if [[ -z "$cur_resolved" || -z "$prev_resolved" || "$cur_resolved" != "$prev_resolved" ]]; then
      eternal_deploy_log "rollback verify: CURRENT=${cur_resolved:-empty} != previous=${prev_resolved:-empty}"
      rb_fail=1
    fi
  fi

  if [[ -n "${PREV_RELEASE}" ]]; then
    ETERNAL_RUNTIME_VERIFY_SOFT=1
    if ! eternal_verify_runtime_points_at_release "$PREV_RELEASE"; then
      eternal_deploy_log "rollback verify: runtime/process identity FAILED"
      rb_fail=1
    fi
    ETERNAL_RUNTIME_VERIFY_SOFT=0
  fi

  if ! eternal_health_check; then
    eternal_deploy_log "rollback verify: health FAILED"
    rb_fail=1
  fi

  if [[ ! -f "$ETERNAL_FRONTEND_ROOT/index.html" ]]; then
    eternal_deploy_log "rollback verify: frontend index.html missing"
    rb_fail=1
  fi

  if [[ "$rb_fail" -eq 0 ]]; then
    printf 'ROLLBACK_PASS\n'
    eternal_deploy_log "ROLLBACK_PASS"
    return 0
  fi
  printf 'ROLLBACK_FAILED\n'
  eternal_deploy_log "ROLLBACK_FAILED"
  return 1
}

eternal_health_check() {
  # Bounded retry: wall-clock budget (default 60s). Transient HTTP 502/503/504 and
  # connection failures retry; success still requires mongo=true + mode=operational.
  # ETERNAL_HEALTH_RETRIES>0 caps attempts (synthetic tests); 0 = budget-only.
  local attempt=0 body code tmp started now elapsed remaining sleep_for curl_t
  local budget_s="${ETERNAL_HEALTH_BUDGET_S:-60}"
  local sleep_s="${ETERNAL_HEALTH_SLEEP_S:-2}"
  local max_attempts="${ETERNAL_HEALTH_RETRIES:-0}"
  started="$(date +%s)"

  while true; do
    now="$(date +%s)"
    elapsed=$((now - started))
    remaining=$((budget_s - elapsed))
    if (( remaining <= 0 )); then
      eternal_deploy_log "health FAIL budget_exhausted elapsed=${elapsed}s budget=${budget_s}s attempts=$attempt"
      return 1
    fi
    attempt=$((attempt + 1))
    if (( max_attempts > 0 && attempt > max_attempts )); then
      eternal_deploy_log "health FAIL attempts_exhausted attempts=$attempt max=$max_attempts"
      return 1
    fi

    # Cap curl --max-time to remaining budget so wall clock cannot exceed budget_s.
    curl_t=5
    if (( curl_t > remaining )); then
      curl_t=$remaining
    fi
    if (( curl_t < 1 )); then
      eternal_deploy_log "health FAIL budget_exhausted elapsed=${elapsed}s budget=${budget_s}s attempts=$attempt"
      return 1
    fi

    tmp="$(mktemp)"
    code="$("$ETERNAL_CURL_BIN" -sS -o "$tmp" -w '%{http_code}' --max-time "$curl_t" "$ETERNAL_HEALTH_URL" 2>/dev/null || true)"
    body="$(cat "$tmp" 2>/dev/null || true)"
    rm -f "$tmp"
    [[ -n "$code" ]] || code="000"

    if [[ "$code" == "200" && -n "$body" ]] && eternal_health_body_ok "$body"; then
      eternal_deploy_log "health PASS attempt=$attempt http=$code mongo=true mode=operational"
      return 0
    fi

    if [[ "$code" == "502" || "$code" == "503" || "$code" == "504" || "$code" == "000" ]]; then
      eternal_deploy_log "health transient http=$code attempt=$attempt elapsed=${elapsed}s body=$(eternal_redact_line "${body:-<empty>}")"
    else
      eternal_deploy_log "health retry attempt=$attempt http=$code elapsed=${elapsed}s body=$(eternal_redact_line "${body:-<empty>}")"
    fi

    now="$(date +%s)"
    remaining=$((budget_s - (now - started)))
    if (( remaining <= 0 )); then
      eternal_deploy_log "health FAIL budget_exhausted elapsed=$((now - started))s budget=${budget_s}s attempts=$attempt"
      return 1
    fi
    sleep_for="$sleep_s"
    if (( sleep_for > remaining )); then
      sleep_for="$remaining"
    fi
    if (( sleep_for > 0 )); then
      sleep "$sleep_for"
    fi
  done
}

# Verify systemd WD string, CURRENT→release identity, and live process cwd.
# When ETERNAL_RUNTIME_VERIFY_SOFT=1 (rollback path), return 1 instead of exiting.
eternal_runtime_fail() {
  local msg="$1"
  if [[ "${ETERNAL_RUNTIME_VERIFY_SOFT:-0}" == "1" ]]; then
    eternal_deploy_log "ERROR: $msg"
    return 1
  fi
  eternal_deploy_die "$msg"
}

eternal_verify_runtime_points_at_release() {
  local release_dir="$1"
  local wd pid cwd active expected_resolved release_backend
  wd="$(eternal_systemctl_show_value WorkingDirectory 2>/dev/null || true)"
  if [[ "$wd" != "$ETERNAL_EXPECTED_WD" ]]; then
    eternal_runtime_fail "runtime WD mismatch: got=${wd:-empty} expected=$ETERNAL_EXPECTED_WD (symlink swap alone does not retarget a wrong unit)" || return 1
  fi
  expected_resolved="$(readlink -f "$ETERNAL_CURRENT_LINK/backend" 2>/dev/null || true)"
  release_backend="$(readlink -f "$release_dir/backend" 2>/dev/null || true)"
  if [[ -n "$expected_resolved" && -n "$release_backend" && "$expected_resolved" != "$release_backend" ]]; then
    eternal_runtime_fail "CURRENT/backend does not resolve to target release backend" || return 1
  fi

  # Always require a live process cwd match when a MainPID is available.
  # FE-only deploys must leave CURRENT on the release the process still runs.
  active="$(eternal_systemctl_show_value ActiveState 2>/dev/null || true)"
  if [[ "$BACKEND_RESTART_ATTEMPTED" == "1" || "$BACKEND_RESTARTED" == "1" ]]; then
    [[ "$active" == "active" ]] || eternal_runtime_fail "backend service not active after restart attempt (got=${active:-empty})" || return 1
  fi
  pid="$(eternal_systemctl_show_value MainPID 2>/dev/null || true)"
  if [[ "$BACKEND_RESTART_ATTEMPTED" == "1" || "$BACKEND_RESTARTED" == "1" ]]; then
    [[ -n "$pid" && "$pid" != "0" ]] || eternal_runtime_fail "backend MainPID missing after restart; refusing to trust stale health" || return 1
  fi
  if [[ -n "$pid" && "$pid" != "0" ]]; then
    if [[ ! -d "/proc/$pid/cwd" ]]; then
      eternal_runtime_fail "active MainPID=$pid has no /proc/cwd (process gone)" || return 1
    fi
    cwd="$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)"
    if [[ -z "$cwd" || -z "$release_backend" || "$cwd" != "$release_backend" ]]; then
      eternal_runtime_fail "active process cwd=${cwd:-empty} is not target release backend=${release_backend:-empty}" || return 1
    fi
  elif [[ "${ETERNAL_REQUIRE_PROCCWD:-1}" == "1" ]]; then
    # No skip switch: FE/backend deploys must prove process identity with a real PID.
    eternal_runtime_fail "MainPID unavailable; cannot verify process cwd against release" || return 1
  fi
  return 0
}

eternal_preflight_runtime() {
  eternal_deploy_log "preflight runtime checks"
  command -v git >/dev/null || eternal_deploy_die "preflight: git missing"
  command -v curl >/dev/null || command -v "$ETERNAL_CURL_BIN" >/dev/null || eternal_deploy_die "preflight: curl missing"
  command -v python3 >/dev/null || eternal_deploy_die "preflight: python3 missing"
  [[ -d "$ETERNAL_RELEASES_ROOT" ]] || eternal_deploy_die "preflight: releases root missing: $ETERNAL_RELEASES_ROOT"
  [[ -w "$ETERNAL_RELEASES_ROOT" ]] || eternal_deploy_die "preflight: releases root not writable"
  [[ -d "$ETERNAL_MIRROR_DIR" ]] || eternal_deploy_die "preflight: mirror missing: $ETERNAL_MIRROR_DIR (bootstrap mirror before first deploy)"
  local current_parent
  current_parent="$(dirname "$ETERNAL_CURRENT_LINK")"
  [[ -d "$current_parent" && -w "$current_parent" ]] \
    || eternal_deploy_die "preflight: CURRENT parent not writable: $current_parent (bootstrap runtime root; never chown /opt)"
  [[ -d "$ETERNAL_FRONTEND_ROOT" ]] || eternal_deploy_die "preflight: frontend root missing: $ETERNAL_FRONTEND_ROOT"
  [[ -w "$ETERNAL_FRONTEND_ROOT" ]] \
    || eternal_deploy_die "preflight: frontend root not writable by deploy user (fix leaf ownership; never chown /var/www)"
  local avail
  avail="$(df -Pk "$ETERNAL_RELEASES_ROOT" | awk 'NR==2{print $4}')"
  if [[ -n "$avail" && "$avail" -lt "$ETERNAL_MIN_DISK_KB" ]]; then
    eternal_deploy_die "preflight: insufficient disk on releases (${avail}KB < ${ETERNAL_MIN_DISK_KB}KB)"
  fi

  local wd exe
  wd="$(eternal_systemctl_show_value WorkingDirectory 2>/dev/null || true)"
  exe="$(eternal_systemctl_show_value ExecStart 2>/dev/null || true)"
  if [[ -z "$wd" ]]; then
    eternal_deploy_die "preflight: cannot read systemd WorkingDirectory for $ETERNAL_SERVICE_NAME"
  fi
  if [[ "$wd" != "$ETERNAL_EXPECTED_WD" ]]; then
    eternal_deploy_die "preflight BLOCK: systemd WorkingDirectory=$wd != $ETERNAL_EXPECTED_WD. Retarget unit (config) before activation; do not assume symlink swap moves the old stage service."
  fi
  if [[ -n "$exe" && "$exe" != *"$ETERNAL_EXPECTED_EXEC_PREFIX"* ]]; then
    eternal_deploy_die "preflight BLOCK: ExecStart does not reference $ETERNAL_EXPECTED_EXEC_PREFIX"
  fi

  # Frontend env: only use dedicated frontend env file if provided; never copy backend secrets.
  if [[ -n "$ETERNAL_FRONTEND_ENV_FILE" ]]; then
    [[ -f "$ETERNAL_FRONTEND_ENV_FILE" ]] || eternal_deploy_die "preflight: frontend env file missing: $ETERNAL_FRONTEND_ENV_FILE"
    if grep -qiE 'MONGO_URL|JWT_SECRET|AWS_SECRET|PRIVATE_KEY' "$ETERNAL_FRONTEND_ENV_FILE"; then
      eternal_deploy_die "preflight: frontend env file appears to contain backend secrets; refuse"
    fi
  fi

  if [[ -e "$ETERNAL_LEGACY_ROOT" ]]; then
    eternal_deploy_log "legacy overlay present at $ETERNAL_LEGACY_ROOT (left untouched)"
  fi
}

eternal_ensure_mirror() {
  if [[ ! -d "$ETERNAL_MIRROR_DIR" ]]; then
    [[ -n "$ETERNAL_GIT_REMOTE" ]] || eternal_deploy_die "mirror missing and ETERNAL_GIT_REMOTE unset"
    eternal_deploy_log "creating bare mirror at $ETERNAL_MIRROR_DIR"
    mkdir -p "$ETERNAL_RELEASES_ROOT"
    git clone --mirror "$ETERNAL_GIT_REMOTE" "$ETERNAL_MIRROR_DIR"
  fi
  if [[ "$ETERNAL_SKIP_FETCH" != "1" ]]; then
    eternal_deploy_log "fetching mirror"
    git --git-dir="$ETERNAL_MIRROR_DIR" fetch --prune origin "+refs/heads/${ETERNAL_MAIN_REF}:refs/heads/${ETERNAL_MAIN_REF}"
  fi
}

eternal_resolve_previous_release() {
  if [[ -L "$ETERNAL_CURRENT_LINK" ]]; then
    local target
    target="$(readlink -f "$ETERNAL_CURRENT_LINK" 2>/dev/null || true)"
    if [[ -n "$target" && -f "$target/.deploy-ok" ]]; then
      case "$target" in
        "$ETERNAL_BASELINE_DIR"/*)
          eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"
          PREV_RELEASE="$ETERNAL_BASELINE_DIR/tree"
          PREV_IS_BASELINE=1
          eternal_deploy_log "previous points at verified baseline tree"
          return 0
          ;;
        *)
          PREV_RELEASE="$target"
          PREV_IS_BASELINE=0
          eternal_deploy_log "previous isolated release: $PREV_RELEASE"
          return 0
          ;;
      esac
    fi
  fi

  # First cutover: require verified immutable baseline with integrity provenance.
  if [[ "$ETERNAL_REQUIRE_VERIFIED_BASELINE" == "1" ]]; then
    if [[ ! -f "$ETERNAL_BASELINE_DIR/.baseline-verified" ]]; then
      cat >&2 <<EOF
[eternal-deploy] ERROR: first cutover blocked — no verified baseline.
Reconciliation plan (order matters):
  1) Keep dirty overlay at $ETERNAL_LEGACY_ROOT untouched.
  2) Bootstrap mirror + releases permissions.
  3) Run prepare-verified-baseline.sh --apply (immutable freeze + integrity manifest).
  4) Retarget systemd unit to WorkingDirectory=$ETERNAL_EXPECTED_WD (config only; after baseline exists).
  5) Re-run deploy for an explicit main SHA.
Do not archive main over the overlay without an explicit Owner scope decision.
Do not retarget/activate the service before baseline+runtime dirs are ready.
EOF
      eternal_deploy_die "unproven overlay/baseline provenance"
    fi
    eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"
    PREV_RELEASE="$ETERNAL_BASELINE_DIR/tree"
    PREV_IS_BASELINE=1
    eternal_deploy_log "using verified immutable baseline for first cutover rollback"
    return 0
  fi

  eternal_deploy_die "no previous release and verified baseline requirement disabled unexpectedly"
}

eternal_materialize_release() {
  local sha="$1"
  NEW_RELEASE="$ETERNAL_RELEASES_ROOT/$sha"
  if [[ -f "$NEW_RELEASE/.deploy-ok" ]]; then
    eternal_deploy_log "reusing existing release $NEW_RELEASE"
    eternal_validate_backend_runtime "$NEW_RELEASE/backend"
    ETERNAL_REUSED_RELEASE=1
    return 0
  fi
  eternal_assert_no_local_conflict "$ETERNAL_FRONTEND_ROOT" "$NEW_RELEASE"
  if [[ -d "$NEW_RELEASE" ]]; then
    if eternal_try_reuse_incomplete_release "$NEW_RELEASE" "$sha"; then
      eternal_deploy_log "reusing incomplete/failed release $NEW_RELEASE (integrity OK; skip rematerialize)"
      ETERNAL_REUSED_RELEASE=1
      return 0
    fi
    eternal_deploy_die "incomplete release exists but failed reuse integrity checks: $NEW_RELEASE (not deleting; Owner must inspect or clear)"
  fi
  mkdir -p "$NEW_RELEASE"
  eternal_deploy_log "materializing isolated release $sha"
  git --git-dir="$ETERNAL_MIRROR_DIR" archive --format=tar "$sha" | tar -x -C "$NEW_RELEASE"
  # Never carry a foreign .venv from archive (should not be in git; belt-and-suspenders).
  rm -rf "$NEW_RELEASE/backend/.venv" 2>/dev/null || true
  printf '%s\n' "$sha" >"$NEW_RELEASE/.eternal-source-sha"
  eternal_manifest_guard "$NEW_RELEASE"
  # Refuse selective-merge manual artifacts without provenance marker.
  if [[ -f "$NEW_RELEASE/.manual-selective-merge" ]]; then
    eternal_deploy_die "release contains .manual-selective-merge marker; not a pure SHA artifact"
  fi
  if [[ -e "$ETERNAL_LEGACY_ROOT" ]]; then
    eternal_deploy_log "legacy root left untouched: $ETERNAL_LEGACY_ROOT"
  fi
}

eternal_diff_paths() {
  local from_sha="$1"
  local to_sha="$2"
  if [[ -z "$from_sha" || "$from_sha" == "none" || "$from_sha" == "baseline-active" ]]; then
    printf 'backend/\nfrontend/\n'
    return 0
  fi
  git --git-dir="$ETERNAL_MIRROR_DIR" diff --name-only "$from_sha" "$to_sha"
}

eternal_maybe_build_frontend() {
  local release_dir="$1"
  local fp recorded mode artifact_dir
  if [[ "$ETERNAL_NEED_FRONTEND" != "1" ]]; then
    eternal_deploy_log "frontend unchanged; skip build"
    return 0
  fi
  [[ -d "$release_dir/frontend" ]] || eternal_deploy_die "frontend tree missing in release"

  mode="${ETERNAL_FRONTEND_BUILD_MODE:-artifact}"
  if [[ "$ETERNAL_DRY_BUILD" == "1" ]]; then
    mode="dry"
  fi

  # Reuse existing build when fingerprint matches recorded post-build marker.
  # Artifact mode also requires a matching provenance marker (no silent yarn).
  if [[ -f "$release_dir/frontend/build/index.html" \
     && -f "$release_dir/.eternal-frontend-fingerprint" ]]; then
    recorded="$(tr -d '\n' <"$release_dir/.eternal-frontend-fingerprint")"
    fp="$(eternal_frontend_input_fingerprint "$release_dir/frontend")"
    if [[ -n "$recorded" && "$recorded" == "$fp" ]]; then
      if [[ "$mode" == "artifact" ]]; then
        if eternal_frontend_artifact_already_installed "$release_dir" "${TARGET_SHA:-}"; then
          eternal_deploy_log "frontend artifact reuse OK; skip fetch/yarn"
          return 0
        fi
        eternal_deploy_log "frontend build present without valid artifact provenance; re-install from artifact"
      else
        eternal_deploy_log "frontend build reuse fingerprint OK; skip yarn install/build"
        return 0
      fi
    else
      eternal_deploy_log "frontend build present but fingerprint mismatch; continuing mode=$mode"
    fi
  fi

  case "$mode" in
    dry)
      if [[ "${ETERNAL_FORCE_BUILD_FAIL:-0}" == "1" ]]; then
        eternal_deploy_die "frontend build failed (forced)"
      fi
      mkdir -p "$release_dir/frontend/build/static/js"
      printf 'dry-asset\n' >"$release_dir/frontend/build/static/js/app.js"
      printf 'dry-build\n<script src="/static/js/app.js"></script>\n' \
        >"$release_dir/frontend/build/index.html"
      printf '{"files":{"main.js":"/static/js/app.js"}}\n' \
        >"$release_dir/frontend/build/asset-manifest.json"
      eternal_write_frontend_fingerprint "$release_dir"
      eternal_deploy_log "frontend dry-build written (unpublished)"
      return 0
      ;;
    artifact)
      if [[ "${ETERNAL_FORCE_BUILD_FAIL:-0}" == "1" ]]; then
        eternal_deploy_die "frontend artifact install failed (forced)"
      fi
      # Never fall back to yarn/CRACO on the VPS.
      artifact_dir="$(eternal_resolve_frontend_artifact_dir "${TARGET_SHA:?TARGET_SHA required}")"
      eternal_install_frontend_artifact_from_dir "$release_dir" "$TARGET_SHA" "$artifact_dir"
      return 0
      ;;
    legacy)
      eternal_deploy_log "WARNING: ETERNAL_FRONTEND_BUILD_MODE=legacy (explicit VPS yarn/CRACO)"
      export NODE_OPTIONS="${ETERNAL_NODE_OPTIONS}"
      # NODE_OPTIONS heap limit is NOT a cgroup/RAM ceiling for the whole process tree.
      if [[ -n "$ETERNAL_FRONTEND_ENV_FILE" ]]; then
        set -a
        # shellcheck disable=SC1090
        source "$ETERNAL_FRONTEND_ENV_FILE"
        set +a
      fi
      eternal_frontend_production_build "$release_dir/frontend"
      eternal_write_frontend_fingerprint "$release_dir"
      return 0
      ;;
    *)
      eternal_deploy_die "invalid ETERNAL_FRONTEND_BUILD_MODE=$mode (allowed: artifact|legacy|dry)"
      ;;
  esac
}

eternal_verify_backup_ready() {
  local sha="$1"
  local backup_dir="$ETERNAL_RELEASES_ROOT/backups"
  [[ -f "$backup_dir/pre-deploy-${sha}.txt" ]] || eternal_deploy_die "backup pointer missing before activation"
  if [[ "$PREV_IS_BASELINE" == "1" ]]; then
    [[ -d "$ETERNAL_BASELINE_DIR/frontend-snapshot" ]] || eternal_deploy_die "baseline frontend snapshot missing before activation"
    [[ -d "$ETERNAL_BASELINE_DIR/tree/backend" ]] || eternal_deploy_die "baseline frozen backend missing before activation"
  elif [[ -n "$PREV_RELEASE" ]]; then
    [[ -d "$PREV_RELEASE" ]] || eternal_deploy_die "previous release path missing before activation"
  fi
  # Preserve live frontend contents under runtime root (not /var/www sibling).
  if [[ "$ETERNAL_NEED_FRONTEND" == "1" && -d "$ETERNAL_FRONTEND_ROOT" ]]; then
    [[ -w "$(dirname "$ETERNAL_FRONTEND_PREV")" ]] \
      || eternal_deploy_die "frontend backup parent not writable: $(dirname "$ETERNAL_FRONTEND_PREV")"
    rm -rf "$ETERNAL_FRONTEND_PREV"
    mkdir -p "$ETERNAL_FRONTEND_PREV"
    cp -a "$ETERNAL_FRONTEND_ROOT"/. "$ETERNAL_FRONTEND_PREV"/
    eternal_deploy_log "frontend pre-activation backup at $ETERNAL_FRONTEND_PREV"
  fi
  eternal_deploy_log "backup verification OK"
}

eternal_switch_current() {
  local release_dir="$1"
  # Validate release runtime BEFORE flipping CURRENT (fail closed).
  eternal_validate_backend_runtime "$release_dir/backend"
  local parent
  parent="$(dirname "$ETERNAL_CURRENT_LINK")"
  [[ -d "$parent" && -w "$parent" ]] \
    || eternal_deploy_die "CURRENT link parent not writable (bootstrap runtime root first): $parent"
  ln -sfn "$release_dir" "${ETERNAL_CURRENT_LINK}.tmp"
  mv -Tf "${ETERNAL_CURRENT_LINK}.tmp" "$ETERNAL_CURRENT_LINK"
  SWITCHED=1
  eternal_deploy_log "current -> $release_dir"
}

eternal_maybe_restart_backend() {
  if [[ "$ETERNAL_NEED_BACKEND" != "1" ]]; then
    eternal_deploy_log "backend unchanged; skip service restart"
    return 0
  fi
  eternal_deploy_log "restarting $ETERNAL_SERVICE_NAME"
  BACKEND_RESTART_ATTEMPTED=1
  if [[ "${ETERNAL_FORCE_RESTART_FAIL:-0}" == "1" ]]; then
    eternal_deploy_die "backend restart failed (forced partial failure)"
  fi
  eternal_systemctl restart "$ETERNAL_SERVICE_NAME" || eternal_deploy_die "backend restart failed"
  BACKEND_RESTARTED=1
  if ! eternal_systemctl_is_active; then
    eternal_deploy_die "backend service not active after restart"
  fi
}

# Activate release consistently: pointer + process must agree.
# FE-only / infra-without-backend: keep CURRENT on the running release (no switch, no restart).
eternal_activate_release() {
  local release_dir="$1"
  if [[ "$ETERNAL_NEED_BACKEND" == "1" ]]; then
    eternal_switch_current "$release_dir"
    eternal_maybe_restart_backend
    eternal_verify_runtime_points_at_release "$release_dir"
    return 0
  fi

  local active_release=""
  if [[ -n "${PREV_RELEASE}" ]]; then
    active_release="$PREV_RELEASE"
  elif [[ -L "$ETERNAL_CURRENT_LINK" ]]; then
    active_release="$(readlink -f "$ETERNAL_CURRENT_LINK" 2>/dev/null || true)"
  fi
  [[ -n "$active_release" ]] \
    || eternal_deploy_die "frontend-only deploy requires an active CURRENT/previous release for process identity"
  eternal_deploy_log "frontend-only/infra-only: keeping CURRENT at $active_release (no switch, no restart)"
  # Ensure CURRENT already points at the active release identity.
  local cur
  cur="$(readlink -f "$ETERNAL_CURRENT_LINK" 2>/dev/null || true)"
  local want
  want="$(readlink -f "$active_release" 2>/dev/null || true)"
  [[ -n "$cur" && -n "$want" && "$cur" == "$want" ]] \
    || eternal_deploy_die "frontend-only deploy: CURRENT ($cur) is not active release ($want); refuse inconsistent pointer"
  eternal_verify_runtime_points_at_release "$active_release"
}

eternal_publish_frontend() {
  local release_dir="$1"
  if [[ "$ETERNAL_NEED_FRONTEND" != "1" ]]; then
    eternal_deploy_log "frontend unchanged; skip publish"
    return 0
  fi
  local built="$release_dir/frontend/build"
  [[ -d "$built" ]] || eternal_deploy_die "frontend build missing: $built"
  [[ -f "$built/index.html" ]] || eternal_deploy_die "frontend build missing index.html"

  FRONTEND_PUBLISH_STARTED=1
  mkdir -p "$ETERNAL_FRONTEND_ROOT"
  touch "$ETERNAL_FRONTEND_ROOT/.deploy-in-progress"

  if [[ "${ETERNAL_FORCE_PUBLISH_FAIL:-0}" == "1" ]]; then
    eternal_deploy_die "frontend publish failed (forced)"
  fi

  # Merge hashed/static + mutable metadata: skip identical, fail-closed on hashed collision,
  # never blind cp -a overwrite of root-owned identical files. Old unreferenced hashes kept.
  # cp -a keeps source mode; public files are normalized to 644 and dirs to 755.
  eternal_webroot_merge_tree "$built" "$ETERNAL_FRONTEND_ROOT" 0

  # Atomic index swap last — temp file is 644 before rename.
  eternal_atomic_publish_index "$built/index.html" "$ETERNAL_FRONTEND_ROOT" 0
  rm -f "$ETERNAL_FRONTEND_ROOT/.deploy-in-progress"
  FRONTEND_SWITCHED=1
  eternal_deploy_log "frontend published atomically to $ETERNAL_FRONTEND_ROOT"
}

eternal_write_ok_marker() {
  local release_dir="$1"
  local sha="$2"
  local req_rel="" req_hash=""
  if [[ -f "$release_dir/backend/.eternal-requirements-file" ]]; then
    req_rel="$(tr -d '\n' <"$release_dir/backend/.eternal-requirements-file")"
  fi
  if [[ -f "$release_dir/backend/.eternal-requirements-sha256" ]]; then
    req_hash="$(tr -d '\n' <"$release_dir/backend/.eternal-requirements-sha256")"
  fi
  {
    printf 'sha=%s\n' "$sha"
    printf 'deployed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'need_backend=%s\n' "$ETERNAL_NEED_BACKEND"
    printf 'need_frontend=%s\n' "$ETERNAL_NEED_FRONTEND"
    if [[ -n "$req_rel" ]]; then
      printf 'requirements_file=%s\n' "$req_rel"
    fi
    if [[ -n "$req_hash" ]]; then
      printf 'requirements_sha256=%s\n' "$req_hash"
    fi
  } >"$release_dir/.deploy-ok"
}

eternal_backup_pointer() {
  local sha="$1"
  local backup_dir="$ETERNAL_RELEASES_ROOT/backups"
  mkdir -p "$backup_dir"
  {
    printf 'target_sha=%s\n' "$sha"
    printf 'previous=%s\n' "${PREV_RELEASE:-none}"
    printf 'previous_baseline=%s\n' "$PREV_IS_BASELINE"
    printf 'at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$backup_dir/pre-deploy-${sha}.txt"
  eternal_deploy_log "backup pointer written"
}

cmd_deploy() {
  local sha="$1"
  eternal_require_sha40 "$sha"
  TARGET_SHA="$sha"

  trap cleanup EXIT

  eternal_acquire_lock "$ETERNAL_DEPLOY_LOCK" 0
  LOCK_HELD=1
  eternal_deploy_log "lock acquired pid=$$"

  eternal_ensure_mirror
  eternal_preflight_runtime
  eternal_sha_is_ancestor_of_main "$ETERNAL_MIRROR_DIR" "$sha" "$ETERNAL_MAIN_REF"
  eternal_resolve_previous_release

  # Change classification previous identity:
  # Prefer active-release.txt (last DEPLOY_OK), which advances on FE-only publishes
  # even when CURRENT stays on the prior backend release. Falling back to CURRENT
  # .deploy-ok would incorrectly re-require FE work after every FE-only deploy.
  local prev_sha="none"
  local active_deploy_sha=""
  active_deploy_sha="$(eternal_read_active_release_sha "$ETERNAL_RELEASES_ROOT" 2>/dev/null || true)"
  if [[ "$PREV_IS_BASELINE" == "1" ]]; then
    prev_sha="baseline-active"
  elif eternal_is_sha40 "${active_deploy_sha:-}" \
    && [[ -f "$ETERNAL_RELEASES_ROOT/$active_deploy_sha/.deploy-ok" ]]; then
    prev_sha="$active_deploy_sha"
    eternal_deploy_log "change-classify previous identity from active-release: $prev_sha"
  elif [[ -n "$PREV_RELEASE" && -f "$PREV_RELEASE/.deploy-ok" ]]; then
    prev_sha="$(sed -n 's/^sha=//p' "$PREV_RELEASE/.deploy-ok" | head -n1)"
  fi

  if [[ "$prev_sha" != "none" && "$prev_sha" != "baseline-active" && "$ETERNAL_ALLOW_MIGRATIONS" != "1" ]]; then
    local blocked
    if blocked="$(eternal_collect_blocked_migrations "$prev_sha" "$sha" "$ETERNAL_MIRROR_DIR" "")"; then
      eternal_safe_echo "blocked migration paths:"$'\n'"$blocked"
      eternal_deploy_die "unapproved business/database migration files in deploy range; refuse"
    fi
  elif [[ "$ETERNAL_ALLOW_MIGRATIONS" == "1" ]]; then
    eternal_deploy_log "ALLOW_MIGRATIONS=1 (explicit operator override)"
  fi

  # Classify BEFORE materialize so docs/tests-only SHAs never create release trees
  # or mutate application identity. First cutover always forces BE+FE below.
  local paths
  paths="$(eternal_diff_paths "$prev_sha" "$sha")"
  ETERNAL_NEED_BACKEND=0
  ETERNAL_NEED_FRONTEND=0
  eternal_classify_change_set <<<"$paths"
  if [[ "$prev_sha" == "none" || "$prev_sha" == "baseline-active" ]]; then
    ETERNAL_NEED_BACKEND=1
    ETERNAL_NEED_FRONTEND=1
  fi
  export ETERNAL_NEED_BACKEND ETERNAL_NEED_FRONTEND
  eternal_deploy_log "change classification backend=$ETERNAL_NEED_BACKEND frontend=$ETERNAL_NEED_FRONTEND"

  if [[ "$ETERNAL_NEED_BACKEND" != "1" && "$ETERNAL_NEED_FRONTEND" != "1" ]]; then
    # Docs/tests/non-app paths only: no venv, no FE publish, no CURRENT switch,
    # no .deploy-ok / active-release advance. Bookkeeping is separate.
    local active_before=""
    active_before="$(eternal_read_active_release_sha "$ETERNAL_RELEASES_ROOT" 2>/dev/null || true)"
    eternal_write_processed_git_sha_marker "$ETERNAL_RELEASES_ROOT" "$sha" "$prev_sha"
    eternal_deploy_log "no application change for sha=$sha (docs/tests/non-app); active-release preserved"
    printf 'DEPLOY_OK_NOOP sha=%s kind=no_application_change active_release=%s\n' \
      "$sha" "${active_before:-none}"
    return 0
  fi

  # Pure SHA publish on first cutover replaces live webroot (e.g. selective-merge FE).
  # Gate BEFORE materialize so refuse leaves no incomplete release dir.
  # First cutover always classifies FE=1 (see above); do not assume overlay FE is preserved.
  if [[ "$prev_sha" == "baseline-active" ]]; then
    if [[ "${ETERNAL_ACK_REPLACE_LIVE_FRONTEND:-0}" != "1" ]]; then
      cat >&2 <<EOF
[eternal-deploy] ERROR: first-cutover frontend publish blocked.
Pure SHA deploy will replace live webroot ($ETERNAL_FRONTEND_ROOT), including any
selective-merge frontend that is not present in the SHA artifact.
Baseline rollback can restore the snapshot only if prepare-verified-baseline
captured that live FE. Refusing automatic overlay preservation assumptions.
Set ETERNAL_ACK_REPLACE_LIVE_FRONTEND=1 (or SSH: deploy <sha> ack-fe-replace) after Owner ack.
EOF
      eternal_deploy_die "live frontend replace requires explicit ack"
    fi
    eternal_deploy_log "ACK live frontend replace on first cutover acknowledged"
  fi

  eternal_backup_pointer "$sha"
  eternal_materialize_release "$sha"

  # Build + fresh venv BEFORE any live activation. Never mutate active service venv.
  # Reused incomplete/successful releases keep validated build + venv (no wipe).
  # Docs-only path returned earlier — venv prepare only when BE or FE application change.
  eternal_maybe_build_frontend "$NEW_RELEASE"
  if [[ "$ETERNAL_REUSED_RELEASE" == "1" && -x "$NEW_RELEASE/backend/.venv/bin/uvicorn" ]]; then
    eternal_deploy_log "backend runtime reuse OK; skip fresh venv prepare"
    eternal_validate_backend_runtime "$NEW_RELEASE/backend"
  elif [[ "$ETERNAL_NEED_BACKEND" == "1" ]]; then
    eternal_deploy_log "preparing isolated backend runtime for $sha"
    eternal_prepare_backend_runtime "$NEW_RELEASE/backend"
  elif [[ ! -x "$NEW_RELEASE/backend/.venv/bin/uvicorn" ]]; then
    # FE-only still needs a runnable backend tree under the new release for identity checks,
    # but prefer copying/linking validation from active CURRENT rather than full prep when possible.
    eternal_deploy_log "preparing isolated backend runtime for fe-only release $sha"
    eternal_prepare_backend_runtime "$NEW_RELEASE/backend"
  else
    eternal_validate_backend_runtime "$NEW_RELEASE/backend"
  fi
  eternal_verify_backup_ready "$sha"

  eternal_activate_release "$NEW_RELEASE"

  if ! eternal_health_check; then
    eternal_deploy_die "health check failed after backend switch (frontend not published)"
  fi

  eternal_publish_frontend "$NEW_RELEASE"

  if ! eternal_health_check; then
    eternal_deploy_die "health check failed after frontend publish"
  fi

  # Backend mongo/mode success does not prove Nginx can read the static site.
  if [[ "$ETERNAL_NEED_FRONTEND" == "1" ]]; then
    if ! eternal_frontend_nginx_ready "$ETERNAL_FRONTEND_ROOT"; then
      eternal_deploy_die "frontend public access failed after publish; backend health is not frontend proof"
    fi
  fi

  local current_switched=0
  if [[ "$ETERNAL_NEED_BACKEND" == "1" ]]; then
    current_switched=1
  fi
  local fe_src=""
  if [[ "$ETERNAL_NEED_FRONTEND" == "1" ]]; then
    if [[ -d "$ETERNAL_FRONTEND_PREV" && -f "$ETERNAL_FRONTEND_PREV/index.html" ]]; then
      fe_src="$ETERNAL_FRONTEND_PREV"
    elif [[ "$PREV_IS_BASELINE" == "1" && -d "$ETERNAL_BASELINE_DIR/frontend-snapshot" ]]; then
      fe_src="$ETERNAL_BASELINE_DIR/frontend-snapshot"
    else
      eternal_deploy_die "DEPLOY_OK blocked: no identity-scoped frontend backup for rollback point"
    fi
  fi
  eternal_write_ok_marker "$NEW_RELEASE" "$sha"
  # Durable predecessor identity + FE backup (not mutable frontend-prev alone).
  eternal_write_rollback_point \
    "$ETERNAL_RELEASES_ROOT" \
    "$sha" \
    "$prev_sha" \
    "$PREV_RELEASE" \
    "$PREV_IS_BASELINE" \
    "$current_switched" \
    "$ETERNAL_NEED_FRONTEND" \
    "$fe_src"
  SWITCHED=0
  FRONTEND_SWITCHED=0
  FRONTEND_PUBLISH_STARTED=0
  eternal_deploy_log "DEPLOY PASS sha=$sha"
  printf 'DEPLOY_OK sha=%s backend_restarted=%s frontend_published=%s current_switched=%s\n' \
    "$sha" "$BACKEND_RESTARTED" "$ETERNAL_NEED_FRONTEND" "$current_switched"
}

# Resolve the filesystem path used to restore/verify CURRENT for a rollback.
#
# previous_id = application identity (active-release predecessor). May be an
# FE-only SHA whose release dir was never CURRENT.
# recorded_path = backend CURRENT predecessor captured at DEPLOY_OK (PREV_RELEASE).
# After FE-only publishes, these may differ; prefer recorded_path for CURRENT
# restore while still requiring previous_id to resolve to a valid .deploy-ok
# identity for active-release bookkeeping.
eternal_resolve_previous_path_for_id() {
  local previous_id="$1"
  local recorded_path="${2:-}"
  local id_path="" rec_resolved="" id_resolved="" ok_sha=""

  if [[ "$previous_id" == "baseline-active" ]]; then
    eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"
    [[ -d "$ETERNAL_BASELINE_DIR/tree" ]] || eternal_deploy_die "baseline tree missing for rollback"
    id_path="$ETERNAL_BASELINE_DIR/tree"
  else
    eternal_require_sha40 "$previous_id"
    id_path="$ETERNAL_RELEASES_ROOT/$previous_id"
    [[ -d "$id_path" ]] || eternal_deploy_die "previous identity release missing: $previous_id"
    [[ -f "$id_path/.deploy-ok" ]] || eternal_deploy_die "previous identity missing .deploy-ok"
    ok_sha="$(sed -n 's/^sha=//p' "$id_path/.deploy-ok" | head -n1)"
    [[ "$ok_sha" == "$previous_id" ]] \
      || eternal_deploy_die "previous identity .deploy-ok sha mismatch"
    eternal_path_is_under_root "$(readlink -f "$ETERNAL_RELEASES_ROOT")" "$(readlink -f "$id_path")" \
      || eternal_deploy_die "previous identity escapes releases root"
    [[ ! -L "$id_path" ]] || eternal_deploy_die "previous identity must not be a symlink directory"
  fi

  if [[ -n "$recorded_path" ]]; then
    rec_resolved="$(readlink -f "$recorded_path" 2>/dev/null || true)"
    [[ -n "$rec_resolved" && -d "$rec_resolved" ]] \
      || eternal_deploy_die "recorded previous_path missing or unreadable"
    if [[ "$previous_id" == "baseline-active" ]]; then
      id_resolved="$(readlink -f "$id_path" 2>/dev/null || true)"
      [[ -n "$id_resolved" && "$rec_resolved" == "$id_resolved" ]] \
        || eternal_deploy_die "baseline previous_path mismatch vs recorded meta"
    else
      # Backend CURRENT predecessor may differ from application previous_id after
      # FE-only deploys. recorded_path must still be a deploy-ok release under root.
      [[ -f "$rec_resolved/.deploy-ok" ]] \
        || eternal_deploy_die "recorded previous_path missing .deploy-ok"
      eternal_path_is_under_root "$(readlink -f "$ETERNAL_RELEASES_ROOT")" "$rec_resolved" \
        || eternal_deploy_die "recorded previous_path escapes releases root"
      [[ ! -L "$recorded_path" ]] || eternal_deploy_die "recorded previous_path must not be a symlink directory"
    fi
    printf '%s\n' "$rec_resolved"
    return 0
  fi

  printf '%s\n' "$id_path"
}

# Owner-requested post-success rollback. Same flock. No DB mutation.
# Prints ROLLBACK_SUCCESS or ROLLBACK_FAILED. Does not use failure-path eternal_rollback.
cmd_rollback() {
  local deployed_sha="$1"
  local previous_id="$2"
  local rb_fail=0
  local point_dir meta active_sha prev_path fe_dir
  local current_switched frontend_published previous_is_baseline recorded_path
  local cur_resolved want_resolved ok_sha

  RUNNER_MODE="post_success"
  eternal_require_sha40 "$deployed_sha"
  eternal_require_previous_id "$previous_id"
  TARGET_SHA="$deployed_sha"

  trap cleanup EXIT

  eternal_acquire_lock "$ETERNAL_DEPLOY_LOCK" 0
  LOCK_HELD=1
  eternal_deploy_log "rollback lock acquired pid=$$"

  # Preflight service/health before mutating live (fail closed if already unhealthy
  # only when we cannot establish a restore target — still require active marker).
  eternal_preflight_runtime

  active_sha="$(eternal_read_active_release_sha "$ETERNAL_RELEASES_ROOT" || true)"
  [[ -n "$active_sha" ]] || eternal_deploy_die "active-release marker missing; refuse rollback"
  [[ "$active_sha" == "$deployed_sha" ]] \
    || eternal_deploy_die "current active sha mismatch: active=$active_sha requested_undo=$deployed_sha (another deployment or state drift)"

  eternal_validate_rollback_point "$ETERNAL_RELEASES_ROOT" "$deployed_sha" "$previous_id"
  point_dir="$(eternal_rollback_point_dir "$ETERNAL_RELEASES_ROOT" "$deployed_sha")"
  meta="$point_dir/meta.txt"
  current_switched="$(eternal_meta_get "$meta" "current_switched")"
  frontend_published="$(eternal_meta_get "$meta" "frontend_published")"
  previous_is_baseline="$(eternal_meta_get "$meta" "previous_is_baseline")"
  recorded_path="$(eternal_meta_get "$meta" "previous_path")"

  # CURRENT must still reflect the deploy being undone.
  if [[ ! -L "$ETERNAL_CURRENT_LINK" ]]; then
    eternal_deploy_die "CURRENT link missing; refuse rollback"
  fi
  cur_resolved="$(readlink -f "$ETERNAL_CURRENT_LINK" 2>/dev/null || true)"
  [[ -n "$cur_resolved" ]] || eternal_deploy_die "CURRENT unreadable"
  if [[ "$current_switched" == "1" ]]; then
    want_resolved="$(readlink -f "$ETERNAL_RELEASES_ROOT/$deployed_sha" 2>/dev/null || true)"
    [[ -n "$want_resolved" && "$cur_resolved" == "$want_resolved" ]] \
      || eternal_deploy_die "CURRENT does not point at deployed sha being rolled back"
    [[ -f "$cur_resolved/.deploy-ok" ]] || eternal_deploy_die "deployed release missing .deploy-ok"
    ok_sha="$(sed -n 's/^sha=//p' "$cur_resolved/.deploy-ok" | head -n1)"
    [[ "$ok_sha" == "$deployed_sha" ]] || eternal_deploy_die "CURRENT .deploy-ok sha mismatch"
  else
    # FE-only deploy kept CURRENT on predecessor.
    prev_path="$(eternal_resolve_previous_path_for_id "$previous_id" "$recorded_path")"
    want_resolved="$(readlink -f "$prev_path" 2>/dev/null || true)"
    [[ -n "$want_resolved" && "$cur_resolved" == "$want_resolved" ]] \
      || eternal_deploy_die "FE-only rollback: CURRENT is not the recorded predecessor (state changed)"
  fi

  prev_path="$(eternal_resolve_previous_path_for_id "$previous_id" "$recorded_path")"
  PREV_RELEASE="$prev_path"
  PREV_IS_BASELINE="$previous_is_baseline"

  if ! eternal_health_check; then
    eternal_deploy_log "rollback precheck: health not operational before restore (continuing with restore)"
  fi

  # Restore frontend first when this deploy published FE.
  if [[ "$frontend_published" == "1" ]]; then
    fe_dir="$point_dir/frontend"
    eternal_deploy_log "rollback: restore frontend from identity backup $fe_dir"
    if eternal_restore_frontend_tree "$fe_dir" \
      && eternal_frontend_required_state_ok "$ETERNAL_FRONTEND_ROOT" "$fe_dir"; then
      eternal_deploy_log "rollback: frontend restore verified"
    else
      eternal_deploy_log "rollback: frontend restore/verify FAILED"
      rb_fail=1
    fi
  fi

  # Restore CURRENT + backend when this deploy switched pointer.
  if [[ "$current_switched" == "1" && "$rb_fail" -eq 0 ]]; then
    eternal_deploy_log "rollback: restore current link -> $prev_path"
    if ln -sfn "$prev_path" "${ETERNAL_CURRENT_LINK}.tmp" \
      && mv -Tf "${ETERNAL_CURRENT_LINK}.tmp" "$ETERNAL_CURRENT_LINK"; then
      :
    else
      eternal_deploy_log "rollback: CURRENT pointer restore FAILED"
      rb_fail=1
    fi
    if [[ "$rb_fail" -eq 0 ]]; then
      eternal_deploy_log "rollback: restart backend against restored target"
      if eternal_systemctl restart "$ETERNAL_SERVICE_NAME" && eternal_systemctl_is_active; then
        :
      else
        eternal_deploy_log "rollback: backend restart/health-active FAILED"
        rb_fail=1
      fi
    fi
  elif [[ "$current_switched" == "1" && "$rb_fail" -ne 0 ]]; then
    eternal_deploy_log "rollback: skip CURRENT/backend restore after frontend failure (partial state)"
  fi

  rm -f "${ETERNAL_FRONTEND_ROOT}/.deploy-in-progress" 2>/dev/null || true
  rm -f "${ETERNAL_FRONTEND_ROOT}/index.html.new" 2>/dev/null || true

  # Post-restore verification — never claim success from HTTP 200 alone.
  if [[ "$current_switched" == "1" ]]; then
    cur_resolved="$(readlink -f "$ETERNAL_CURRENT_LINK" 2>/dev/null || true)"
    want_resolved="$(readlink -f "$prev_path" 2>/dev/null || true)"
    if [[ -z "$cur_resolved" || -z "$want_resolved" || "$cur_resolved" != "$want_resolved" ]]; then
      eternal_deploy_log "rollback verify: CURRENT=${cur_resolved:-empty} != previous=${want_resolved:-empty}"
      rb_fail=1
    fi
    ETERNAL_RUNTIME_VERIFY_SOFT=1
    if ! eternal_verify_runtime_points_at_release "$prev_path"; then
      eternal_deploy_log "rollback verify: runtime/process identity FAILED"
      rb_fail=1
    fi
    ETERNAL_RUNTIME_VERIFY_SOFT=0
  else
    ETERNAL_RUNTIME_VERIFY_SOFT=1
    if ! eternal_verify_runtime_points_at_release "$prev_path"; then
      eternal_deploy_log "rollback verify: runtime/process identity FAILED"
      rb_fail=1
    fi
    ETERNAL_RUNTIME_VERIFY_SOFT=0
  fi

  if ! eternal_health_check; then
    eternal_deploy_log "rollback verify: health FAILED (mongo=true mode=operational required)"
    rb_fail=1
  fi

  if [[ ! -f "$ETERNAL_FRONTEND_ROOT/index.html" ]]; then
    eternal_deploy_log "rollback verify: frontend index.html missing"
    rb_fail=1
  fi
  if [[ "$frontend_published" == "1" ]]; then
    if ! eternal_frontend_required_state_ok "$ETERNAL_FRONTEND_ROOT" "$point_dir/frontend"; then
      eternal_deploy_log "rollback verify: frontend required state != identity backup"
      rb_fail=1
    fi
  fi

  if [[ "$rb_fail" -eq 0 ]]; then
    # Active marker now points at predecessor (baseline-active has no sha40).
    eternal_write_active_release_marker "$ETERNAL_RELEASES_ROOT" "$previous_id" "rollback-of-${deployed_sha}"
    printf 'ROLLBACK_SUCCESS deployed_sha=%s previous_id=%s\n' "$deployed_sha" "$previous_id"
    eternal_deploy_log "ROLLBACK_SUCCESS deployed_sha=$deployed_sha previous_id=$previous_id"
    eternal_deploy_log "NOTE: code rollback does not reverse MongoDB/business transactions already committed"
    ROLLBACK_STATUS="SUCCESS"
    return 0
  fi

  printf 'ROLLBACK_FAILED deployed_sha=%s previous_id=%s current=%s\n' \
    "$deployed_sha" "$previous_id" "$(readlink -f "$ETERNAL_CURRENT_LINK" 2>/dev/null || echo unknown)"
  eternal_deploy_log "ROLLBACK_FAILED (see logs for partial state)"
  ROLLBACK_STATUS="FAILED"
  return 1
}

cmd_usage() {
  cat <<'EOF'
Usage:
  eternal-deploy-runner.sh deploy <sha40>
  eternal-deploy-runner.sh rollback <deployed_sha40> <previous_id>
    previous_id = 40-hex SHA or baseline-active
EOF
}

main() {
  local verb="${1:-}"
  local sha="${2:-}"
  local previous_id="${3:-}"
  case "$verb" in
    deploy)
      [[ -n "$sha" ]] || eternal_deploy_die "missing sha"
      cmd_deploy "$sha"
      ;;
    rollback)
      [[ -n "$sha" ]] || eternal_deploy_die "missing deployed sha"
      [[ -n "$previous_id" ]] || eternal_deploy_die "missing previous release id"
      cmd_rollback "$sha" "$previous_id"
      ;;
    *)
      cmd_usage >&2
      eternal_deploy_die "unsupported runner verb"
      ;;
  esac
}

main "$@"
