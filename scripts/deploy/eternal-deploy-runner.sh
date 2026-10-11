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
ETERNAL_HEALTH_RETRIES="${ETERNAL_HEALTH_RETRIES:-5}"
ETERNAL_HEALTH_SLEEP_S="${ETERNAL_HEALTH_SLEEP_S:-2}"
ETERNAL_MAIN_REF="${ETERNAL_MAIN_REF:-main}"
ETERNAL_ALLOW_MIGRATIONS="${ETERNAL_ALLOW_MIGRATIONS:-0}"
ETERNAL_MIRROR_DIR="${ETERNAL_MIRROR_DIR:-$ETERNAL_RELEASES_ROOT/mirror.git}"
ETERNAL_GIT_REMOTE="${ETERNAL_GIT_REMOTE:-}"
ETERNAL_SYSTEMCTL_BIN="${ETERNAL_SYSTEMCTL_BIN:-systemctl}"
ETERNAL_SYSTEMCTL_SUDO="${ETERNAL_SYSTEMCTL_SUDO:-sudo -n}"
ETERNAL_USE_SYSTEMCTL_SUDO="${ETERNAL_USE_SYSTEMCTL_SUDO:-1}"
ETERNAL_NPM_BIN="${ETERNAL_NPM_BIN:-npm}"
ETERNAL_CURL_BIN="${ETERNAL_CURL_BIN:-curl}"
ETERNAL_SKIP_FETCH="${ETERNAL_SKIP_FETCH:-0}"
ETERNAL_DRY_BUILD="${ETERNAL_DRY_BUILD:-0}"
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

ROLLBACK_STATUS="" # PASS | FAILED | "" (no rollback attempted)

cleanup() {
  local ec=$?
  if (( ec != 0 )); then
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
# Assets first, atomic index last. Keeps hashed assets that remain useful for old sessions when present in backup.
# Returns 0/1 (does not exit) so rollback can continue other components.
eternal_restore_frontend_tree() {
  local src="$1"
  if [[ ! -d "$src" ]]; then
    eternal_deploy_log "frontend restore source missing: $src"
    return 1
  fi
  if [[ ! -d "$ETERNAL_FRONTEND_ROOT" ]]; then
    mkdir -p "$ETERNAL_FRONTEND_ROOT" || return 1
  fi
  if [[ ! -w "$ETERNAL_FRONTEND_ROOT" ]]; then
    eternal_deploy_log "frontend root not writable: $ETERNAL_FRONTEND_ROOT"
    return 1
  fi

  if [[ -d "$src/static" ]]; then
    mkdir -p "$ETERNAL_FRONTEND_ROOT/static" || return 1
    cp -a "$src/static"/. "$ETERNAL_FRONTEND_ROOT/static"/ || return 1
  fi
  local item base
  shopt -s dotglob nullglob
  for item in "$src"/*; do
    base="$(basename "$item")"
    if [[ "$base" == "index.html" || "$base" == "static" || "$base" == ".deploy-in-progress" ]]; then
      continue
    fi
    if [[ -d "$item" ]]; then
      mkdir -p "$ETERNAL_FRONTEND_ROOT/$base" || return 1
      cp -a "$item"/. "$ETERNAL_FRONTEND_ROOT/$base"/ || return 1
    else
      cp -a "$item" "$ETERNAL_FRONTEND_ROOT/$base" || return 1
    fi
  done
  shopt -u dotglob nullglob
  if [[ -f "$src/index.html" ]]; then
    cp -a "$src/index.html" "$ETERNAL_FRONTEND_ROOT/index.html.new" || return 1
    mv -Tf "$ETERNAL_FRONTEND_ROOT/index.html.new" "$ETERNAL_FRONTEND_ROOT/index.html" || return 1
  else
    eternal_deploy_log "frontend restore missing index.html in source"
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
    if [[ -n "$fe_src" ]]; then
      eternal_deploy_log "rollback: restore frontend from $fe_src (in-place)"
      if eternal_restore_frontend_tree "$fe_src"; then
        FRONTEND_SWITCHED=0
        FRONTEND_PUBLISH_STARTED=0
      else
        eternal_deploy_log "rollback: frontend restore FAILED (continuing other components)"
        rb_fail=1
      fi
    else
      eternal_deploy_log "rollback: no frontend backup/snapshot available"
      rb_fail=1
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
  local i body
  for ((i = 1; i <= ETERNAL_HEALTH_RETRIES; i++)); do
    body="$("$ETERNAL_CURL_BIN" -fsS --max-time 5 "$ETERNAL_HEALTH_URL" || true)"
    if [[ -n "$body" ]] && eternal_health_body_ok "$body"; then
      eternal_deploy_log "health PASS attempt=$i mongo=true mode=operational"
      return 0
    fi
    eternal_deploy_log "health retry attempt=$i/$ETERNAL_HEALTH_RETRIES body=$(eternal_redact_line "${body:-<empty>}")"
    sleep "$ETERNAL_HEALTH_SLEEP_S"
  done
  return 1
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
    return 0
  fi
  eternal_assert_no_local_conflict "$ETERNAL_FRONTEND_ROOT" "$NEW_RELEASE"
  rm -rf "$NEW_RELEASE"
  mkdir -p "$NEW_RELEASE"
  eternal_deploy_log "materializing isolated release $sha"
  git --git-dir="$ETERNAL_MIRROR_DIR" archive --format=tar "$sha" | tar -x -C "$NEW_RELEASE"
  # Never carry a foreign .venv from archive (should not be in git; belt-and-suspenders).
  rm -rf "$NEW_RELEASE/backend/.venv" 2>/dev/null || true
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
  if [[ "$ETERNAL_NEED_FRONTEND" != "1" ]]; then
    eternal_deploy_log "frontend unchanged; skip build"
    return 0
  fi
  [[ -d "$release_dir/frontend" ]] || eternal_deploy_die "frontend tree missing in release"
  if [[ "$ETERNAL_DRY_BUILD" == "1" ]]; then
    if [[ "${ETERNAL_FORCE_BUILD_FAIL:-0}" == "1" ]]; then
      eternal_deploy_die "frontend build failed (forced)"
    fi
    mkdir -p "$release_dir/frontend/build/static/js"
    printf 'dry-asset\n' >"$release_dir/frontend/build/static/js/app.js"
    printf 'dry-build\n' >"$release_dir/frontend/build/index.html"
    eternal_deploy_log "frontend dry-build written (unpublished)"
    return 0
  fi
  (
    cd "$release_dir/frontend"
    export NODE_OPTIONS="${ETERNAL_NODE_OPTIONS}"
    # NODE_OPTIONS heap limit is NOT a cgroup/RAM ceiling for the whole process tree.
    if [[ -n "$ETERNAL_FRONTEND_ENV_FILE" ]]; then
      set -a
      # shellcheck disable=SC1090
      source "$ETERNAL_FRONTEND_ENV_FILE"
      set +a
    fi
    if [[ -f yarn.lock ]]; then
      command -v yarn >/dev/null 2>&1 || eternal_deploy_die "yarn required but not found"
      yarn install --frozen-lockfile
      yarn build
    else
      "$ETERNAL_NPM_BIN" ci
      "$ETERNAL_NPM_BIN" run build
    fi
  ) || eternal_deploy_die "frontend install/build failed; live not activated"
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

  # Copy/merge hashed assets first; keep previously published hashed files.
  if [[ -d "$built/static" ]]; then
    mkdir -p "$ETERNAL_FRONTEND_ROOT/static"
    cp -a "$built/static"/. "$ETERNAL_FRONTEND_ROOT/static"/
  fi
  # Copy other non-index artifacts (favicon, manifest, etc.) without removing old hashed assets.
  local item
  shopt -s dotglob nullglob
  for item in "$built"/*; do
    local base
    base="$(basename "$item")"
    if [[ "$base" == "index.html" || "$base" == "static" ]]; then
      continue
    fi
    if [[ -d "$item" ]]; then
      mkdir -p "$ETERNAL_FRONTEND_ROOT/$base"
      cp -a "$item"/. "$ETERNAL_FRONTEND_ROOT/$base"/
    else
      cp -a "$item" "$ETERNAL_FRONTEND_ROOT/$base"
    fi
  done
  shopt -u dotglob nullglob

  # Atomic index swap last — never leave an empty webroot interval.
  cp -a "$built/index.html" "$ETERNAL_FRONTEND_ROOT/index.html.new"
  mv -Tf "$ETERNAL_FRONTEND_ROOT/index.html.new" "$ETERNAL_FRONTEND_ROOT/index.html"
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

  local prev_sha="none"
  if [[ "$PREV_IS_BASELINE" == "1" ]]; then
    prev_sha="baseline-active"
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

  # Pure SHA publish on first cutover replaces live webroot (e.g. selective-merge FE).
  # Gate BEFORE materialize so refuse leaves no incomplete release dir.
  # First cutover always classifies FE=1 (see below); do not assume overlay FE is preserved.
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

  # Build + fresh venv BEFORE any live activation. Never mutate active service venv.
  eternal_maybe_build_frontend "$NEW_RELEASE"
  if [[ "$ETERNAL_NEED_BACKEND" == "1" || ! -x "$NEW_RELEASE/backend/.venv/bin/uvicorn" ]]; then
    eternal_deploy_log "preparing isolated backend runtime for $sha"
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

  eternal_write_ok_marker "$NEW_RELEASE" "$sha"
  SWITCHED=0
  FRONTEND_SWITCHED=0
  FRONTEND_PUBLISH_STARTED=0
  eternal_deploy_log "DEPLOY PASS sha=$sha"
  printf 'DEPLOY_OK sha=%s backend_restarted=%s frontend_published=%s current_switched=%s\n' \
    "$sha" "$BACKEND_RESTARTED" "$ETERNAL_NEED_FRONTEND" "$([[ "$ETERNAL_NEED_BACKEND" == "1" ]] && echo 1 || echo 0)"
}

cmd_usage() {
  cat <<'EOF'
Usage:
  eternal-deploy-runner.sh deploy <sha40>
EOF
}

main() {
  local verb="${1:-}"
  local sha="${2:-}"
  case "$verb" in
    deploy)
      [[ -n "$sha" ]] || eternal_deploy_die "missing sha"
      cmd_deploy "$sha"
      ;;
    *)
      cmd_usage >&2
      eternal_deploy_die "unsupported runner verb"
      ;;
  esac
}

main "$@"
