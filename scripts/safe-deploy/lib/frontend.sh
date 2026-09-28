# shellcheck shell=bash
# Frontend prepare + near-atomic / atomic publish helpers.
# Fits current nginx: root /var/www/eternal-packaging; (directory today).
# If WEBROOT is already a symlink → atomic ln -sfn + mv -T swap.
# If WEBROOT is a directory → near-atomic sibling rename (no nginx change).
# Production /var/www is root-owned. Privileged file ops use sudo -n.
# Dry-run stays unprivileged. Business logic is not involved.

_priv() {
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    "$@"
  else
    sudo -n "$@"
  fi
}

verify_frontend_build_dir() {
  local dir="$1"
  [[ -d "$dir" ]] || die "frontend build dir missing: $dir"
  [[ -f "$dir/index.html" ]] || die "frontend build missing index.html: $dir"
  [[ -d "$dir/static" ]] || die "frontend build missing static/: $dir"
  local js_count
  js_count="$(find "$dir/static" -type f \( -name '*.js' -o -name '*.css' \) 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${js_count:-0}" -gt 0 ]] || die "frontend build has no static js/css assets"
  info "frontend build verified: $dir (assets=${js_count})"
}

prepare_frontend_release_dir() {
  local sha="$1"
  local dest="${RELEASES_ROOT}/${sha}"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    dest="${SAFE_DEPLOY_TEST_ROOT}/releases/${sha}"
  fi
  _priv mkdir -p "$(dirname "$dest")"
  if [[ -e "$dest" ]]; then
    _priv rm -rf "$dest"
  fi
  _priv mkdir -p "$dest"
  printf '%s\n' "$dest"
}

# Build frontend from a detached worktree at TARGET_SHA (not the live checkout).
# OLD LIVE webroot and running backend stay untouched.
build_frontend_from_sha() {
  local sha="$1"
  local worktree="${WORKTREE_ROOT}/${sha}"
  local dest

  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    worktree="${SAFE_DEPLOY_TEST_ROOT}/worktrees/${sha}"
  fi

  if [[ -n "${FRONTEND_ARTIFACT_DIR:-}" ]]; then
    info "using external frontend artifact: $FRONTEND_ARTIFACT_DIR"
    verify_frontend_build_dir "$FRONTEND_ARTIFACT_DIR"
    dest="$(prepare_frontend_release_dir "$sha")"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
      cp -a "$FRONTEND_ARTIFACT_DIR/." "$dest/"
    else
      run_timeout "${CUTOVER_STEP_TIMEOUT:-60}" sudo -n cp -a "$FRONTEND_ARTIFACT_DIR/." "$dest/"
    fi
    verify_frontend_build_dir "$dest"
    printf '%s\n' "$dest"
    return 0
  fi

  if [[ "${SKIP_FRONTEND_BUILD:-0}" == "1" ]]; then
    die "SKIP_FRONTEND_BUILD=1 but FRONTEND_ARTIFACT_DIR not set"
  fi

  if [[ "${SIMULATE_BUILD_FAILURE:-0}" == "1" ]]; then
    die "simulated frontend build failure"
  fi

  resource_guard_check

  mkdir -p "$(dirname "$worktree")"
  if [[ -d "$worktree" ]]; then
    git -C "${APP_ROOT}" worktree remove --force "$worktree" 2>/dev/null || rm -rf "$worktree"
  fi

  info "creating worktree at $worktree for $sha"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    mkdir -p "$worktree/frontend"
    # Dry-run: fabricate a minimal valid build tree instead of npm
    dest="$(prepare_frontend_release_dir "$sha")"
    mkdir -p "$dest/static/js"
    echo '<!doctype html><title>dry-run</title>' > "$dest/index.html"
    echo 'console.log("dry")' > "$dest/static/js/main.js"
    verify_frontend_build_dir "$dest"
    printf '%s\n' "$dest"
    return 0
  fi

  run_timeout 120 git -C "${APP_ROOT}" fetch --prune origin
  run_timeout 60 git -C "${APP_ROOT}" worktree add --detach "$worktree" "$sha"

  info "building frontend in worktree (timeout=${FRONTEND_BUILD_TIMEOUT}s)"
  (
    cd "$worktree/frontend"
    if [[ -f package-lock.json ]]; then
      run_timeout 300 npm ci --prefer-offline --no-audit --no-fund
    else
      run_timeout 300 npm install --no-audit --no-fund
    fi
    run_timeout "${FRONTEND_BUILD_TIMEOUT}" npm run build
  ) || {
    git -C "${APP_ROOT}" worktree remove --force "$worktree" 2>/dev/null || true
    die "frontend build failed — OLD LIVE untouched"
  }

  local build_dir="$worktree/frontend/build"
  verify_frontend_build_dir "$build_dir"
  dest="$(prepare_frontend_release_dir "$sha")"
  run_timeout "${CUTOVER_STEP_TIMEOUT:-60}" sudo -n cp -a "$build_dir/." "$dest/"
  verify_frontend_build_dir "$dest"

  # Cleanup worktree after artifact copied (keeps disk sane)
  git -C "${APP_ROOT}" worktree remove --force "$worktree" 2>/dev/null || warn "worktree cleanup deferred: $worktree"
  printf '%s\n' "$dest"
}

publish_frontend_atomic() {
  local new_release="$1"
  local live="${WEBROOT}"
  local prev="${PREVIOUS_WEBROOT}"

  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    live="${SAFE_DEPLOY_TEST_ROOT}/webroot"
    prev="${SAFE_DEPLOY_TEST_ROOT}/webroot-previous"
  fi

  verify_frontend_build_dir "$new_release"

  if [[ "${SIMULATE_FRONTEND_PUBLISH_FAILURE:-0}" == "1" ]]; then
    die "simulated frontend publish failure — previous release preserved"
  fi

  _priv mkdir -p "$(dirname "$live")"

  if [[ -L "$live" ]]; then
    info "WEBROOT is symlink — atomic symlink swap"
    local tmp_link="${live}.next.$$"
    _priv ln -sfn "$new_release" "$tmp_link"
    _priv mv -Tf "$tmp_link" "$live"
    # Keep previous target path recorded
    readlink -f "$new_release" > "${DEPLOY_HISTORY_DIR:-/tmp}/last-frontend-release.path" 2>/dev/null || true
    info "frontend published via symlink → $new_release"
    return 0
  fi

  info "WEBROOT is directory — near-atomic rename swap (nginx root unchanged)"
  # Rotate previous
  if [[ -e "$prev" ]]; then
    local stamp
    stamp="$(date +%Y%m%d%H%M%S)"
    if [[ "${DRY_RUN:-0}" == "1" ]]; then
      rm -rf "${prev}.old" 2>/dev/null || true
      mv "$prev" "${prev}.old" 2>/dev/null || rm -rf "$prev"
    else
      _priv rm -rf "${prev}.${stamp}" 2>/dev/null || true
      _priv mv "$prev" "${prev}.${stamp}" 2>/dev/null || _priv rm -rf "$prev"
      # Keep only one stamped previous to limit disk
      _priv bash -c 'find "$1" -maxdepth 1 -type d -name "$2.*" 2>/dev/null | sort | head -n -2 | xargs -r rm -rf' _ "$(dirname "$prev")" "$(basename "$prev")"
    fi
  fi

  if [[ -e "$live" ]]; then
    _priv mv "$live" "$prev"
  fi
  _priv mv "$new_release" "$live"
  # Ensure ownership if possible (non-fatal)
  if [[ "${DRY_RUN:-0}" != "1" ]]; then
    sudo -n chown -R www-data:www-data "$live" 2>/dev/null || true
  fi
  info "frontend published: live=$live previous=$prev"
}

rollback_frontend_previous() {
  local live="${WEBROOT}"
  local prev="${PREVIOUS_WEBROOT}"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    live="${SAFE_DEPLOY_TEST_ROOT}/webroot"
    prev="${SAFE_DEPLOY_TEST_ROOT}/webroot-previous"
  fi
  [[ -e "$prev" ]] || die "no previous frontend release at $prev"
  local failed="${live}.failed.$$"
  if [[ -e "$live" ]]; then
    _priv mv "$live" "$failed"
  fi
  _priv mv "$prev" "$live"
  _priv rm -rf "$failed" 2>/dev/null || true
  info "frontend rolled back to previous release at $live"
}
