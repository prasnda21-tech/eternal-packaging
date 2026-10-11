#!/usr/bin/env bash
# Isolated-release deploy runner for Eternal Packaging (fail-closed).
#
# Production defaults:
#   releases:  /opt/eternal-packaging-releases/<sha>
#   current:   /opt/eternal-packaging-current -> active release
#   legacy:    /opt/eternal-packaging (DIRTY — never reset/pull)
#   frontend:  /var/www/eternal-packaging
#   lock:      /tmp/eternal-packaging-deploy.lock
#   service:   eternal-packaging-backend.service
#
# Order: materialize → build FE (unpublished) → switch current → restart BE
#        → health(mongo=true,mode=operational) → publish FE.
# First-cutover baseline enables rollback to previously active BE/FE.
# Never uses sudo NOPASSWD:ALL. Never prints secrets. Never mutates Mongo.

set -euo pipefail

RUNNER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$RUNNER_DIR/lib.sh"

ETERNAL_RELEASES_ROOT="${ETERNAL_RELEASES_ROOT:-/opt/eternal-packaging-releases}"
ETERNAL_CURRENT_LINK="${ETERNAL_CURRENT_LINK:-/opt/eternal-packaging-current}"
ETERNAL_LEGACY_ROOT="${ETERNAL_LEGACY_ROOT:-/opt/eternal-packaging}"
ETERNAL_FRONTEND_ROOT="${ETERNAL_FRONTEND_ROOT:-/var/www/eternal-packaging}"
ETERNAL_FRONTEND_PREV="${ETERNAL_FRONTEND_PREV:-/var/www/eternal-packaging.prev}"
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
# Prefer least-privilege sudoers wrapper when available.
ETERNAL_SYSTEMCTL_SUDO="${ETERNAL_SYSTEMCTL_SUDO:-sudo -n}"
ETERNAL_USE_SYSTEMCTL_SUDO="${ETERNAL_USE_SYSTEMCTL_SUDO:-1}"
ETERNAL_NPM_BIN="${ETERNAL_NPM_BIN:-npm}"
ETERNAL_CURL_BIN="${ETERNAL_CURL_BIN:-curl}"
ETERNAL_SKIP_FETCH="${ETERNAL_SKIP_FETCH:-0}"
ETERNAL_DRY_BUILD="${ETERNAL_DRY_BUILD:-0}"
ETERNAL_BASELINE_DIR="${ETERNAL_BASELINE_DIR:-$ETERNAL_RELEASES_ROOT/baseline-active}"

PREV_RELEASE=""
PREV_IS_BASELINE=0
NEW_RELEASE=""
SWITCHED=0
FRONTEND_SWITCHED=0
BACKEND_RESTARTED=0
LOCK_HELD=0
FRONTEND_PUBLISH_STARTED=0

cleanup() {
  local ec=$?
  if (( LOCK_HELD == 1 )); then
    eternal_release_lock "$ETERNAL_DEPLOY_LOCK"
    LOCK_HELD=0
  fi
  if (( ec != 0 )); then
    eternal_deploy_log "deploy failed (exit=$ec); attempting rollback if needed"
    eternal_rollback || true
  fi
  exit "$ec"
}

eternal_systemctl() {
  if [[ "$ETERNAL_USE_SYSTEMCTL_SUDO" == "1" ]]; then
    # shellcheck disable=SC2086
    $ETERNAL_SYSTEMCTL_SUDO "$ETERNAL_SYSTEMCTL_BIN" "$@"
  else
    "$ETERNAL_SYSTEMCTL_BIN" "$@"
  fi
}

eternal_rollback() {
  # Frontend first if publish started/switched — restore previously active docs.
  if [[ "$FRONTEND_SWITCHED" == "1" || "$FRONTEND_PUBLISH_STARTED" == "1" ]]; then
    if [[ -d "$ETERNAL_FRONTEND_PREV" ]]; then
      eternal_deploy_log "rollback: restore frontend from previous/active backup"
      rm -rf "${ETERNAL_FRONTEND_ROOT}.bad" 2>/dev/null || true
      if [[ -d "$ETERNAL_FRONTEND_ROOT" ]]; then
        mv "$ETERNAL_FRONTEND_ROOT" "${ETERNAL_FRONTEND_ROOT}.bad"
      fi
      mv "$ETERNAL_FRONTEND_PREV" "$ETERNAL_FRONTEND_ROOT"
      FRONTEND_SWITCHED=0
      FRONTEND_PUBLISH_STARTED=0
    elif [[ "$PREV_IS_BASELINE" == "1" && -d "$ETERNAL_BASELINE_DIR/frontend-snapshot" ]]; then
      eternal_deploy_log "rollback: restore frontend from first-cutover baseline snapshot"
      rm -rf "${ETERNAL_FRONTEND_ROOT}.bad" 2>/dev/null || true
      if [[ -d "$ETERNAL_FRONTEND_ROOT" ]]; then
        mv "$ETERNAL_FRONTEND_ROOT" "${ETERNAL_FRONTEND_ROOT}.bad"
      fi
      mkdir -p "$ETERNAL_FRONTEND_ROOT"
      cp -a "$ETERNAL_BASELINE_DIR/frontend-snapshot"/. "$ETERNAL_FRONTEND_ROOT"/
      FRONTEND_SWITCHED=0
      FRONTEND_PUBLISH_STARTED=0
    fi
  fi

  if [[ "$SWITCHED" == "1" && -n "${PREV_RELEASE}" ]]; then
    eternal_deploy_log "rollback: restore current link -> $PREV_RELEASE"
    ln -sfn "$PREV_RELEASE" "${ETERNAL_CURRENT_LINK}.tmp"
    mv -Tf "${ETERNAL_CURRENT_LINK}.tmp" "$ETERNAL_CURRENT_LINK"
    SWITCHED=0
    if [[ "$BACKEND_RESTARTED" == "1" || "$PREV_IS_BASELINE" == "1" ]]; then
      eternal_deploy_log "rollback: restart backend service against restored target"
      eternal_systemctl restart "$ETERNAL_SERVICE_NAME" || true
      BACKEND_RESTARTED=0
    fi
  fi
  rm -f "${ETERNAL_FRONTEND_ROOT}/.deploy-in-progress" 2>/dev/null || true
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

eternal_ensure_mirror() {
  mkdir -p "$ETERNAL_RELEASES_ROOT"
  if [[ ! -d "$ETERNAL_MIRROR_DIR" ]]; then
    [[ -n "$ETERNAL_GIT_REMOTE" ]] || eternal_deploy_die "mirror missing and ETERNAL_GIT_REMOTE unset"
    eternal_deploy_log "creating bare mirror at $ETERNAL_MIRROR_DIR"
    git clone --mirror "$ETERNAL_GIT_REMOTE" "$ETERNAL_MIRROR_DIR"
  fi
  if [[ "$ETERNAL_SKIP_FETCH" != "1" ]]; then
    eternal_deploy_log "fetching mirror"
    git --git-dir="$ETERNAL_MIRROR_DIR" fetch --prune origin "+refs/heads/${ETERNAL_MAIN_REF}:refs/heads/${ETERNAL_MAIN_REF}"
  fi
}

eternal_capture_baseline_if_needed() {
  # If no prior isolated release is active, capture the currently live BE/FE so
  # first-deploy rollback can restore real production state (legacy tree + FE).
  if [[ -L "$ETERNAL_CURRENT_LINK" ]]; then
    local target
    target="$(readlink -f "$ETERNAL_CURRENT_LINK" 2>/dev/null || true)"
    if [[ -n "$target" && -f "$target/.deploy-ok" && ! -f "$target/.baseline" ]]; then
      PREV_RELEASE="$target"
      PREV_IS_BASELINE=0
      return 0
    fi
  fi

  eternal_deploy_log "capturing first-cutover baseline of currently active backend/frontend"
  mkdir -p "$ETERNAL_BASELINE_DIR"
  local backend_root="$ETERNAL_LEGACY_ROOT"
  local wd=""
  wd="$(eternal_systemctl show -p WorkingDirectory --value "$ETERNAL_SERVICE_NAME" 2>/dev/null || true)"
  if [[ -n "$wd" && "$wd" != "/" ]]; then
    # WorkingDirectory is typically .../backend — store app root when present.
    if [[ "$(basename "$wd")" == "backend" ]]; then
      backend_root="$(dirname "$wd")"
    else
      backend_root="$wd"
    fi
  fi
  {
    printf 'kind=legacy-baseline\n'
    printf 'sha=baseline-active\n'
    printf 'backend_root=%s\n' "$backend_root"
    printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$ETERNAL_BASELINE_DIR/.deploy-ok"
  : >"$ETERNAL_BASELINE_DIR/.baseline"

  # Symlink baseline "release" to the live backend root so CURRENT rollback works
  # with the systemd unit WorkingDirectory=/opt/eternal-packaging-current/backend.
  rm -rf "$ETERNAL_BASELINE_DIR/tree"
  mkdir -p "$ETERNAL_BASELINE_DIR/tree"
  if [[ -d "$backend_root/backend" ]]; then
    ln -sfn "$backend_root/backend" "$ETERNAL_BASELINE_DIR/tree/backend"
  else
    ln -sfn "$backend_root" "$ETERNAL_BASELINE_DIR/tree/backend"
  fi
  # Point PREV_RELEASE at baseline tree wrapper.
  # CURRENT will be switched to NEW release; rollback points CURRENT at baseline tree.
  # Use baseline dir itself as link target with backend/ child symlink.
  PREV_RELEASE="$ETERNAL_BASELINE_DIR/tree"
  PREV_IS_BASELINE=1

  rm -rf "$ETERNAL_BASELINE_DIR/frontend-snapshot"
  if [[ -d "$ETERNAL_FRONTEND_ROOT" ]]; then
    mkdir -p "$ETERNAL_BASELINE_DIR/frontend-snapshot"
    cp -a "$ETERNAL_FRONTEND_ROOT"/. "$ETERNAL_BASELINE_DIR/frontend-snapshot"/
  fi
  eternal_deploy_log "baseline captured backend_root=$backend_root"
}

eternal_materialize_release() {
  local sha="$1"
  NEW_RELEASE="$ETERNAL_RELEASES_ROOT/$sha"
  if [[ -f "$NEW_RELEASE/.deploy-ok" ]]; then
    eternal_deploy_log "reusing existing release $NEW_RELEASE"
    return 0
  fi
  eternal_assert_no_local_conflict "$ETERNAL_FRONTEND_ROOT" "$NEW_RELEASE"
  rm -rf "$NEW_RELEASE"
  mkdir -p "$NEW_RELEASE"
  eternal_deploy_log "materializing isolated release $sha"
  git --git-dir="$ETERNAL_MIRROR_DIR" archive --format=tar "$sha" | tar -x -C "$NEW_RELEASE"
  eternal_manifest_guard "$NEW_RELEASE"
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
    mkdir -p "$release_dir/frontend/build"
    printf 'dry-build\n' >"$release_dir/frontend/build/index.html"
    eternal_deploy_log "frontend dry-build written (unpublished)"
    return 0
  fi
  (
    cd "$release_dir/frontend"
    if [[ -f yarn.lock ]]; then
      command -v yarn >/dev/null 2>&1 || eternal_deploy_die "yarn required but not found"
      yarn install --frozen-lockfile
      yarn build
    else
      "$ETERNAL_NPM_BIN" ci
      "$ETERNAL_NPM_BIN" run build
    fi
  )
}

eternal_switch_current() {
  local release_dir="$1"
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
  eternal_systemctl restart "$ETERNAL_SERVICE_NAME"
  BACKEND_RESTARTED=1
}

eternal_publish_frontend() {
  local release_dir="$1"
  if [[ "$ETERNAL_NEED_FRONTEND" != "1" ]]; then
    eternal_deploy_log "frontend unchanged; skip publish"
    return 0
  fi
  local built="$release_dir/frontend/build"
  [[ -d "$built" ]] || eternal_deploy_die "frontend build missing: $built"

  FRONTEND_PUBLISH_STARTED=1
  mkdir -p "$(dirname "$ETERNAL_FRONTEND_ROOT")"
  # Preserve currently active frontend before replace (for rollback).
  rm -rf "$ETERNAL_FRONTEND_PREV"
  if [[ -d "$ETERNAL_FRONTEND_ROOT" ]]; then
    mv "$ETERNAL_FRONTEND_ROOT" "$ETERNAL_FRONTEND_PREV"
  fi
  mkdir -p "$ETERNAL_FRONTEND_ROOT"
  touch "$ETERNAL_FRONTEND_ROOT/.deploy-in-progress"
  if [[ "${ETERNAL_FORCE_PUBLISH_FAIL:-0}" == "1" ]]; then
    eternal_deploy_die "frontend publish copy failed"
  fi
  if ! cp -a "$built"/. "$ETERNAL_FRONTEND_ROOT"/; then
    eternal_deploy_die "frontend publish copy failed"
  fi
  rm -f "$ETERNAL_FRONTEND_ROOT/.deploy-in-progress"
  FRONTEND_SWITCHED=1
  eternal_deploy_log "frontend published to $ETERNAL_FRONTEND_ROOT"
}

eternal_write_ok_marker() {
  local release_dir="$1"
  local sha="$2"
  {
    printf 'sha=%s\n' "$sha"
    printf 'deployed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'need_backend=%s\n' "$ETERNAL_NEED_BACKEND"
    printf 'need_frontend=%s\n' "$ETERNAL_NEED_FRONTEND"
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

  trap cleanup EXIT

  eternal_acquire_lock "$ETERNAL_DEPLOY_LOCK" 0
  LOCK_HELD=1
  eternal_deploy_log "lock acquired pid=$$"

  eternal_ensure_mirror
  eternal_sha_is_ancestor_of_main "$ETERNAL_MIRROR_DIR" "$sha" "$ETERNAL_MAIN_REF"

  eternal_capture_baseline_if_needed

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

  # Build FE into the release only — do not publish until backend health passes.
  eternal_maybe_build_frontend "$NEW_RELEASE"
  eternal_switch_current "$NEW_RELEASE"
  eternal_maybe_restart_backend

  if ! eternal_health_check; then
    eternal_deploy_die "health check failed after backend switch (frontend not published)"
  fi

  # Publish frontend only after backend is operational.
  eternal_publish_frontend "$NEW_RELEASE"

  # Re-check health after FE publish (service should still be operational).
  if ! eternal_health_check; then
    eternal_deploy_die "health check failed after frontend publish"
  fi

  eternal_write_ok_marker "$NEW_RELEASE" "$sha"
  SWITCHED=0
  FRONTEND_SWITCHED=0
  FRONTEND_PUBLISH_STARTED=0
  eternal_deploy_log "DEPLOY PASS sha=$sha"
  printf 'DEPLOY_OK sha=%s backend_restarted=%s frontend_published=%s\n' \
    "$sha" "$BACKEND_RESTARTED" "$ETERNAL_NEED_FRONTEND"
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
