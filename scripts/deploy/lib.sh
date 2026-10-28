#!/usr/bin/env bash
# Shared helpers for Eternal Packaging deploy gate/runner.
# Fail-closed. No secrets printed. No sudo assumed beyond least-privilege wrappers.

set -euo pipefail

# Canonical probe token for the repo-managed gate (versioned).
ETERNAL_GATE_PROBE_VERSION="${ETERNAL_GATE_PROBE_VERSION:-2}"
ETERNAL_GATE_PROBE_LINE="ETERNAL_GATE_OK probe=pass version=${ETERNAL_GATE_PROBE_VERSION}"

eternal_deploy_log() {
  printf '[eternal-deploy] %s\n' "$*" >&2
}

eternal_deploy_die() {
  eternal_deploy_log "ERROR: $*"
  exit 1
}

eternal_is_sha40() {
  local value="${1:-}"
  [[ "$value" =~ ^[0-9a-f]{40}$ ]]
}

eternal_require_sha40() {
  local value="${1:-}"
  eternal_is_sha40 "$value" || eternal_deploy_die "SHA must be exactly 40 lowercase hex characters"
}

# Redact obvious secret-shaped tokens from a line before logging.
eternal_redact_line() {
  local line="$1"
  line="$(printf '%s' "$line" | tr '\n' ' ')"
  if [[ "$line" == *"PRIVATE KEY"* ]]; then
    line="$(printf '%s' "$line" | sed -E 's/-----BEGIN[A-Z ]*PRIVATE KEY-----+ .+ -----END[A-Z ]*PRIVATE KEY-----+/[REDACTED_PRIVATE_KEY]/g')"
    if [[ "$line" == *"PRIVATE KEY"* ]]; then
      line='[REDACTED_PRIVATE_KEY]'
    fi
  fi
  line="$(printf '%s' "$line" | sed -E \
    -e 's/(ghp_[A-Za-z0-9]{20,})/[REDACTED]/g' \
    -e 's/(github_pat_[A-Za-z0-9_]{20,})/[REDACTED]/g' \
    -e 's/(AKIA[0-9A-Z]{16})/[REDACTED]/g' \
    -e 's/([Bb]earer[[:space:]]+)[A-Za-z0-9._~+\/=-]{12,}/\1[REDACTED]/g' \
    -e 's/(SECRET|TOKEN|PASSWORD|PRIVATE_KEY|APPROVAL)([^=]*[=:][[:space:]]*)[^[:space:]]+/\1\2[REDACTED]/g')"
  printf '%s\n' "$line"
}

eternal_safe_echo() {
  eternal_redact_line "$*" >&2
}

# Return 0 if probe stdout is acceptable for workflow activation.
# Accepts canonical repo gate output and known legacy PASS forms from the
# already-installed VPS gate (Owner-confirmed probe PASS).
eternal_probe_output_accepted() {
  local out="$1"
  grep -qE 'ETERNAL_GATE_OK[[:space:]]+probe=pass' <<<"$out" && return 0
  grep -qE '(^|[[:space:]])PASS probe([[:space:]]|$)' <<<"$out" && return 0
  grep -qx 'OK' <<<"$out" && return 0
  grep -qE '^probe:[[:space:]]*(PASS|ok|OK)$' <<<"$out" && return 0
  return 1
}

eternal_print_probe_ok() {
  # Dual line: canonical + legacy-compatible marker for gradual cutover.
  printf '%s\n' "$ETERNAL_GATE_PROBE_LINE"
  printf 'PASS probe\n'
}

# Migration / irreversible DB mutation paths that require explicit approval.
eternal_is_blocked_migration_path() {
  local path="$1"
  [[ "$path" == backend/scripts/migrate_*.py ]] && return 0
  [[ "$path" == backend/scripts/*migration*.py ]] && return 0
  [[ "$path" == */migrations/* || "$path" == migrations/* ]] && return 0
  [[ "$path" == backend/alembic/* || "$path" == alembic/* ]] && return 0
  [[ "$path" == *cash_bank_claim_migration.py ]] && return 0
  [[ "$path" == *migrate_piece_payroll* || "$path" == *migrate_storage* ]] && return 0
  if [[ "$path" == backend/* && "$path" == *migration*.py ]]; then
    return 0
  fi
  return 1
}

eternal_collect_blocked_migrations() {
  local from_sha="$1"
  local to_sha="$2"
  local git_dir="${3:-}"
  local work_tree="${4:-}"
  local args=(diff --name-only "$from_sha" "$to_sha")
  local line
  local blocked=()

  if [[ -n "$git_dir" ]]; then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      if eternal_is_blocked_migration_path "$line"; then
        blocked+=("$line")
      fi
    done < <(git --git-dir="$git_dir" "${args[@]}")
  elif [[ -n "$work_tree" ]]; then
    while IFS= read -r line; do
      [[ -z "$line" ]] && continue
      if eternal_is_blocked_migration_path "$line"; then
        blocked+=("$line")
      fi
    done < <(git -C "$work_tree" "${args[@]}")
  else
    eternal_deploy_die "internal: git context required for migration guard"
  fi

  if ((${#blocked[@]} > 0)); then
    printf '%s\n' "${blocked[@]}"
    return 0
  fi
  return 1
}

eternal_paths_touch_backend() {
  local path="$1"
  case "$path" in
    backend/*|scripts/deploy/*) return 0 ;;
  esac
  return 1
}

eternal_paths_touch_frontend() {
  local path="$1"
  case "$path" in
    frontend/*) return 0 ;;
  esac
  return 1
}

eternal_classify_change_set() {
  ETERNAL_NEED_BACKEND=0
  ETERNAL_NEED_FRONTEND=0
  local path
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if eternal_paths_touch_backend "$path"; then
      ETERNAL_NEED_BACKEND=1
    fi
    if eternal_paths_touch_frontend "$path"; then
      ETERNAL_NEED_FRONTEND=1
    fi
  done
  export ETERNAL_NEED_BACKEND ETERNAL_NEED_FRONTEND
}

# Interoperable flock lock (same path as terminal `flock /tmp/eternal-packaging-deploy.lock`).
# Existing lock file without a holder must NOT block. Do not unlink the lock file on release.
ETERNAL_LOCK_FD="${ETERNAL_LOCK_FD:-}"

eternal_acquire_lock() {
  local lock_path="$1"
  local timeout_s="${2:-0}"
  mkdir -p "$(dirname "$lock_path")"
  # Create/open without truncating; bare file presence is not ownership.
  if [[ ! -e "$lock_path" ]]; then
    : >"$lock_path" || eternal_deploy_die "cannot create lock file: $lock_path"
  fi
  [[ -f "$lock_path" ]] || eternal_deploy_die "lock path is not a regular file: $lock_path"
  if [[ -n "${ETERNAL_LOCK_FD:-}" ]]; then
    eternal_deploy_die "internal: deploy lock FD already held"
  fi
  exec {ETERNAL_LOCK_FD}<>"$lock_path"
  if [[ "${timeout_s:-0}" -le 0 ]]; then
    if ! flock -n "$ETERNAL_LOCK_FD"; then
      exec {ETERNAL_LOCK_FD}>&- || true
      ETERNAL_LOCK_FD=""
      eternal_deploy_die "deploy lock held: $lock_path"
    fi
  else
    if ! flock -w "$timeout_s" "$ETERNAL_LOCK_FD"; then
      exec {ETERNAL_LOCK_FD}>&- || true
      ETERNAL_LOCK_FD=""
      eternal_deploy_die "deploy lock held: $lock_path"
    fi
  fi
  # Best-effort holder note for operators; not used for acquire/release logic.
  printf 'pid=%s\nacquired_at=%s\n' "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$lock_path" || true
}

eternal_release_lock() {
  # Unlock FD only — never unlink the lock file (avoids races with flock waiters).
  if [[ -n "${ETERNAL_LOCK_FD:-}" ]]; then
    flock -u "$ETERNAL_LOCK_FD" 2>/dev/null || true
    exec {ETERNAL_LOCK_FD}>&- 2>/dev/null || true
    ETERNAL_LOCK_FD=""
  fi
}

eternal_assert_no_local_conflict() {
  local frontend_root="$1"
  # release_dir arg retained for call-site compatibility; incomplete releases are
  # handled by eternal_materialize_release reuse/fail-closed (never silent rm -rf).
  local _release_dir="${2:-}"
  if [[ -e "$frontend_root/.deploy-in-progress" ]]; then
    eternal_deploy_die "local conflict: frontend deploy-in-progress marker present ($frontend_root/.deploy-in-progress)"
  fi
}

# True when relpath is under static/ (immutable hashed asset policy).
eternal_fe_is_hashed_relpath() {
  local rel="${1#./}"
  [[ "$rel" == static/* ]]
}

# Emit required static/ asset relpaths (one per line) for reuse / integrity checks.
# Sources (real refs only):
#   1) index.html src= / href= attributes pointing at /static/... or static/...
#   2) optional asset-manifest.json values/entrypoints under static/
# Does NOT treat arbitrary "static/..." substrings inside inline scripts (e.g. PostHog
# concatenating remote ".../static/array.js") as local build assets.
eternal_fe_list_required_static_assets() {
  local index_html="$1"
  local manifest="${2:-}"
  local ref raw
  [[ -f "$index_html" ]] || return 1

  while IFS= read -r raw; do
    [[ -z "$raw" ]] && continue
    # strip src=" / href=' wrappers
    ref="${raw#*=}"
    ref="${ref#\"}"
    ref="${ref#\'}"
    ref="${ref%\"}"
    ref="${ref%\'}"
    ref="${ref#./}"
    ref="${ref#/}"
    [[ "$ref" == static/* ]] || continue
    [[ "$ref" == *..* ]] && continue
    printf '%s\n' "$ref"
  done < <(grep -oE '(src|href)=["'\'']/?static/[^"'\'']+["'\'']' "$index_html" 2>/dev/null || true)

  if [[ -n "$manifest" && -f "$manifest" ]]; then
    while IFS= read -r raw; do
      [[ -z "$raw" ]] && continue
      ref="${raw#\"}"
      ref="${ref%\"}"
      ref="${ref#./}"
      ref="${ref#/}"
      [[ "$ref" == static/* ]] || continue
      [[ "$ref" == *..* ]] && continue
      # Manifest may list index.html under files; only static/ is required here.
      printf '%s\n' "$ref"
    done < <(grep -oE '"/?static/[^"]+"' "$manifest" 2>/dev/null || true)
  fi
  return 0
}

# True for hashed runtime sourcemaps under static/: *.js.map and *.css.map.
# Non-authoritative for runtime correctness when the paired .js/.css is identical.
eternal_fe_is_js_sourcemap_relpath() {
  # Backward-compatible name; matches JS and CSS sourcemaps.
  eternal_fe_is_runtime_sourcemap_relpath "$@"
}

eternal_fe_is_runtime_sourcemap_relpath() {
  local rel="${1#./}"
  eternal_fe_is_hashed_relpath "$rel" || return 1
  [[ "$rel" == *.js.map || "$rel" == *.css.map ]]
}

# Ensure candidate path resolves inside webroot (blocks symlink/path escape).
eternal_path_is_under_root() {
  local root="$1"
  local candidate="$2"
  local root_real cand_real
  root_real="$(readlink -f "$root" 2>/dev/null || true)"
  [[ -n "$root_real" ]] || return 1
  if [[ -e "$candidate" || -L "$candidate" ]]; then
    cand_real="$(readlink -f "$candidate" 2>/dev/null || true)"
  else
    local parent base
    parent="$(dirname "$candidate")"
    base="$(basename "$candidate")"
    mkdir -p "$parent" 2>/dev/null || true
    parent="$(readlink -f "$parent" 2>/dev/null || true)"
    [[ -n "$parent" ]] || return 1
    cand_real="$parent/$base"
  fi
  [[ -n "$cand_real" ]] || return 1
  [[ "$cand_real" == "$root_real" || "$cand_real" == "$root_real"/* ]]
}

# Fingerprint of frontend build inputs (no secret values logged — hashes only).
# Includes package/lock/config, src/, public/, and when set the external
# ETERNAL_FRONTEND_ENV_FILE content hash so reuse cannot skip after inputs change.
# Emit "digest  stable-label" lines (never absolute paths — fingerprints must be
# location-independent so packed artifacts verify against materialize trees).
eternal_frontend_hash_labeled() {
  local file="$1"
  local label="$2"
  local digest
  [[ -f "$file" ]] || return 0
  digest="$(sha256sum "$file" | awk '{print $1}')"
  printf '%s  %s\n' "$digest" "$label"
}

eternal_frontend_tree_file_hashes() {
  local root="$1"
  local rel="$2"
  local dir="$root/$rel"
  [[ -d "$dir" ]] || return 0
  (
    cd "$root" || exit 1
    # Sorted deterministic relative paths; regular files only (no symlinks).
    find "$rel" -type f ! -type l -print0 2>/dev/null \
      | sort -z \
      | while IFS= read -r -d '' p; do
          digest="$(sha256sum "$p" | awk '{print $1}')"
          printf '%s  %s\n' "$digest" "$p"
        done
  )
}

eternal_frontend_input_fingerprint() {
  local frontend_dir="$1"
  local pkg="$frontend_dir/package.json"
  local lock=""
  local lock_label="yarn.lock"
  if [[ -f "$frontend_dir/yarn.lock" ]]; then
    lock="$frontend_dir/yarn.lock"
    lock_label="yarn.lock"
  elif [[ -f "$frontend_dir/../yarn.lock" ]]; then
    lock="$frontend_dir/../yarn.lock"
    lock_label="../yarn.lock"
  fi
  [[ -f "$pkg" ]] || eternal_deploy_die "frontend fingerprint: package.json missing"
  [[ -n "$lock" && -f "$lock" ]] || eternal_deploy_die "frontend fingerprint: yarn.lock missing"
  if [[ -n "${ETERNAL_FRONTEND_ENV_FILE:-}" ]]; then
    [[ -f "$ETERNAL_FRONTEND_ENV_FILE" ]] \
      || eternal_deploy_die "frontend fingerprint: ETERNAL_FRONTEND_ENV_FILE missing ($ETERNAL_FRONTEND_ENV_FILE)"
  fi
  {
    eternal_frontend_hash_labeled "$pkg" "package.json"
    eternal_frontend_hash_labeled "$lock" "$lock_label"
    eternal_frontend_hash_labeled "$frontend_dir/craco.config.js" "craco.config.js"
    eternal_frontend_hash_labeled "$frontend_dir/config-overrides.js" "config-overrides.js"
    eternal_frontend_hash_labeled "$frontend_dir/.env.production" ".env.production"
    eternal_frontend_hash_labeled "$frontend_dir/.env" ".env"
    if [[ -n "${ETERNAL_FRONTEND_ENV_FILE:-}" && -f "$ETERNAL_FRONTEND_ENV_FILE" ]]; then
      # Hash only — never print file contents (may contain non-secret CRA public vars).
      eternal_frontend_hash_labeled "$ETERNAL_FRONTEND_ENV_FILE" "ETERNAL_FRONTEND_ENV_FILE"
    fi
    eternal_frontend_tree_file_hashes "$frontend_dir" "src"
    eternal_frontend_tree_file_hashes "$frontend_dir" "public"
  } | sha256sum | awk '{print $1}'
}

# Public CRA build-env fingerprint (allowlisted keys only; values hashed, never logged).
# Keys: REACT_APP_* plus DISABLE_ESLINT_PLUGIN. Empty/missing keys still contribute.
# Optional arg: path to env file (sourced in a subshell only).
eternal_frontend_public_env_fingerprint() {
  local env_file="${1:-}"
  (
    if [[ -n "$env_file" ]]; then
      [[ -f "$env_file" ]] || eternal_deploy_die "frontend env fingerprint: file missing ($env_file)"
      set -a
      # shellcheck disable=SC1090
      source "$env_file"
      set +a
    fi
    printf 'DISABLE_ESLINT_PLUGIN=%s\n' "${DISABLE_ESLINT_PLUGIN-}"
    # Always include backend URL key (empty string is a deliberate same-origin value).
    printf 'REACT_APP_BACKEND_URL=%s\n' "${REACT_APP_BACKEND_URL-}"
    while IFS= read -r k; do
      [[ "$k" == "REACT_APP_BACKEND_URL" ]] && continue
      printf '%s=%s\n' "$k" "${!k-}"
    done < <(compgen -v REACT_APP_ 2>/dev/null | sort)
  ) | sha256sum | awk '{print $1}'
}

# Resolve canonical production public build env into the current shell (fail-closed).
# FRONTEND_PROD_ENV_MODE=same-origin|explicit-url (required).
# same-origin → unset REACT_APP_BACKEND_URL (nginx same host).
# explicit-url → FRONTEND_PROD_REACT_APP_BACKEND_URL must be non-loopback http(s) URL.
eternal_apply_frontend_prod_build_env() {
  local mode="${FRONTEND_PROD_ENV_MODE:-}"
  local url="${FRONTEND_PROD_REACT_APP_BACKEND_URL-}"
  case "$mode" in
    same-origin)
      unset REACT_APP_BACKEND_URL || true
      export REACT_APP_BACKEND_URL=""
      export DISABLE_ESLINT_PLUGIN="${DISABLE_ESLINT_PLUGIN:-true}"
      ;;
    explicit-url)
      [[ -n "$url" ]] || eternal_deploy_die "FRONTEND_PROD_ENV_MODE=explicit-url requires FRONTEND_PROD_REACT_APP_BACKEND_URL"
      if [[ "$url" =~ ^https?://(127\.0\.0\.1|localhost)(:|/|$) ]]; then
        eternal_deploy_die "production frontend URL must not be loopback/CI test host"
      fi
      if [[ ! "$url" =~ ^https?:// ]]; then
        eternal_deploy_die "FRONTEND_PROD_REACT_APP_BACKEND_URL must be http(s) URL"
      fi
      export REACT_APP_BACKEND_URL="$url"
      export DISABLE_ESLINT_PLUGIN="${DISABLE_ESLINT_PLUGIN:-true}"
      ;;
    "")
      eternal_deploy_die "FRONTEND_PROD_ENV_MODE unset; Owner must set same-origin or explicit-url (fail-closed)"
      ;;
    *)
      eternal_deploy_die "FRONTEND_PROD_ENV_MODE invalid ($mode); allowed: same-origin|explicit-url"
      ;;
  esac
}

eternal_write_frontend_fingerprint() {
  local release_dir="$1"
  local fp
  fp="$(eternal_frontend_input_fingerprint "$release_dir/frontend")"
  printf '%s\n' "$fp" >"$release_dir/.eternal-frontend-fingerprint"
  eternal_deploy_log "frontend build fingerprint recorded"
}

# Other-class permission bits. Owner `test -r` is not a Nginx check: mode 600 is
# readable by the deploy user and still returns EACCES for www-data.
eternal_mode_other_digit() {
  local path="$1"
  local mode
  mode="$(stat -c '%a' "$path" 2>/dev/null || stat -f '%OLp' "$path" 2>/dev/null || true)"
  [[ -n "$mode" ]] || return 1
  printf '%s\n' "${mode: -1}"
}

eternal_file_other_readable() {
  local path="$1"
  local other
  [[ -f "$path" && ! -L "$path" ]] || return 1
  other="$(eternal_mode_other_digit "$path")" || return 1
  (( (10#$other & 4) == 4 ))
}

eternal_dir_other_traversable() {
  local path="$1"
  local other
  [[ -d "$path" && ! -L "$path" ]] || return 1
  other="$(eternal_mode_other_digit "$path")" || return 1
  (( (10#$other & 1) == 1 ))
}

eternal_path_owned_by_invoker() {
  local path="$1"
  local owner uid
  uid="$(id -u)"
  owner="$(stat -c '%u' "$path" 2>/dev/null || stat -f '%u' "$path" 2>/dev/null || true)"
  [[ -n "$owner" && "$owner" == "$uid" ]]
}

# soft=1 returns 1; otherwise exits. Never chmod 777 and never chown.
eternal_webroot_perm_fail() {
  local soft="$1"
  shift
  eternal_deploy_log "ERROR: $*"
  if [[ "$soft" == "1" ]]; then
    return 1
  fi
  eternal_deploy_die "$*"
}

# Directories under the webroot that the invoker owns become 755 when Nginx
# cannot traverse them. Unowned directories are left untouched when already
# traversable, and fail closed otherwise. Parents outside dest_root are not touched.
eternal_webroot_ensure_public_dir() {
  local dir="$1"
  local soft="${2:-0}"
  if eternal_dir_other_traversable "$dir"; then
    return 0
  fi
  if eternal_path_owned_by_invoker "$dir"; then
    chmod 755 "$dir" || {
      eternal_webroot_perm_fail "$soft" "webroot dir chmod 755 failed: $dir" || return 1
      return 1
    }
    eternal_dir_other_traversable "$dir" || {
      eternal_webroot_perm_fail "$soft" "webroot dir still not traversable: $dir" || return 1
      return 1
    }
    return 0
  fi
  eternal_webroot_perm_fail "$soft" "webroot dir not traversable and not owned; refuse chmod: $dir" || return 1
  return 1
}

eternal_webroot_ensure_traverse() {
  local dest_root="$1"
  local dir="$2"
  local soft="${3:-0}"
  local root_real dir_real rel acc part
  [[ -d "$dir" ]] || {
    eternal_webroot_perm_fail "$soft" "webroot traverse: missing dir $dir" || return 1
    return 1
  }
  root_real="$(readlink -f "$dest_root" 2>/dev/null || true)"
  dir_real="$(readlink -f "$dir" 2>/dev/null || true)"
  [[ -n "$root_real" && -n "$dir_real" ]] || {
    eternal_webroot_perm_fail "$soft" "webroot traverse: unreadable path" || return 1
    return 1
  }
  case "$dir_real" in
    "$root_real"|"$root_real"/*) ;;
    *)
      eternal_webroot_perm_fail "$soft" "webroot traverse: escapes webroot ($dir)" || return 1
      return 1
      ;;
  esac
  eternal_webroot_ensure_public_dir "$root_real" "$soft" || return 1
  if [[ "$dir_real" == "$root_real" ]]; then
    return 0
  fi
  rel="${dir_real#"$root_real"/}"
  acc="$root_real"
  local IFS='/'
  for part in $rel; do
    [[ -z "$part" ]] && continue
    acc="$acc/$part"
    eternal_webroot_ensure_public_dir "$acc" "$soft" || return 1
  done
  return 0
}

# Public webroot file. Owned files are normalized to 644 (content unchanged).
# Unowned files that are already other-readable are left alone. Unowned files
# that Nginx cannot read fail closed — no chown, no chmod of someone else's file.
eternal_webroot_ensure_public_file() {
  local dest="$1"
  local soft="${2:-0}"
  [[ -f "$dest" && ! -L "$dest" ]] || {
    eternal_webroot_perm_fail "$soft" "webroot public file missing: $dest" || return 1
    return 1
  }
  if eternal_path_owned_by_invoker "$dest"; then
    chmod 644 "$dest" || {
      eternal_webroot_perm_fail "$soft" "webroot file chmod 644 failed: $dest" || return 1
      return 1
    }
    eternal_file_other_readable "$dest" || {
      eternal_webroot_perm_fail "$soft" "webroot file still not other-readable: $dest" || return 1
      return 1
    }
    return 0
  fi
  if eternal_file_other_readable "$dest"; then
    eternal_deploy_log "webroot public file left unchanged (not owner, already other-readable): $dest"
    return 0
  fi
  eternal_webroot_perm_fail "$soft" "webroot public file not other-readable and not owned; refuse chmod: $dest" || return 1
  return 1
}

# Read-only: every directory from dest_root through the file's parent has other-execute.
eternal_webroot_path_traversable() {
  local dest_root="$1"
  local target="$2"
  local root_real dir_real rel acc part
  root_real="$(readlink -f "$dest_root" 2>/dev/null || true)"
  if [[ -d "$target" ]]; then
    dir_real="$(readlink -f "$target" 2>/dev/null || true)"
  else
    dir_real="$(readlink -f "$(dirname "$target")" 2>/dev/null || true)"
  fi
  [[ -n "$root_real" && -n "$dir_real" ]] || return 1
  case "$dir_real" in
    "$root_real"|"$root_real"/*) ;;
    *) return 1 ;;
  esac
  eternal_dir_other_traversable "$root_real" || return 1
  if [[ "$dir_real" == "$root_real" ]]; then
    return 0
  fi
  rel="${dir_real#"$root_real"/}"
  acc="$root_real"
  local IFS='/'
  for part in $rel; do
    [[ -z "$part" ]] && continue
    acc="$acc/$part"
    eternal_dir_other_traversable "$acc" || return 1
  done
  return 0
}

# Stage index.html.new as 644, then atomically rename it over index.html.
# soft=1 returns 1 instead of exiting (rollback restore).
eternal_atomic_publish_index() {
  local src="$1"
  local dest_root="$2"
  local soft="${3:-0}"
  local dest="$dest_root/index.html"
  local tmp="$dest_root/index.html.new"
  local tmp_mode

  [[ -f "$src" && ! -L "$src" ]] || {
    eternal_webroot_perm_fail "$soft" "frontend index source missing: $src" || return 1
    return 1
  }
  eternal_webroot_ensure_traverse "$dest_root" "$dest_root" "$soft" || return 1
  if [[ -L "$dest" ]]; then
    eternal_webroot_perm_fail "$soft" "frontend index destination is a symlink" || return 1
    return 1
  fi
  if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
    eternal_deploy_log "SKIP_IDENTICAL index.html"
    eternal_webroot_ensure_public_file "$dest" "$soft" || return 1
    return 0
  fi
  cp -a "$src" "$tmp" || {
    eternal_webroot_perm_fail "$soft" "frontend index stage copy failed" || return 1
    return 1
  }
  # 644 before rename so the inode Nginx observes is already other-readable.
  chmod 644 "$tmp" || {
    rm -f "$tmp" 2>/dev/null || true
    eternal_webroot_perm_fail "$soft" "frontend index temp chmod 644 failed" || return 1
    return 1
  }
  eternal_file_other_readable "$tmp" || {
    rm -f "$tmp" 2>/dev/null || true
    eternal_webroot_perm_fail "$soft" "frontend index temp not other-readable before rename" || return 1
    return 1
  }
  tmp_mode="$(stat -c '%a' "$tmp" 2>/dev/null || stat -f '%OLp' "$tmp" 2>/dev/null || echo unknown)"
  eternal_deploy_log "index temp mode=$tmp_mode before atomic rename"
  eternal_path_is_under_root "$dest_root" "$tmp" || {
    rm -f "$tmp" 2>/dev/null || true
    eternal_webroot_perm_fail "$soft" "frontend publish: index.html.new escapes webroot" || return 1
    return 1
  }
  mv -Tf "$tmp" "$dest" || {
    rm -f "$tmp" 2>/dev/null || true
    eternal_webroot_perm_fail "$soft" "frontend index atomic mv failed" || return 1
    return 1
  }
  eternal_file_other_readable "$dest" || {
    eternal_webroot_perm_fail "$soft" "frontend index not other-readable after rename" || return 1
    return 1
  }
  return 0
}

# Scheme and hostname only. Port, userinfo, path, and query are not part of identity.
eternal_url_scheme() {
  local url="$1"
  printf '%s\n' "${url%%://*}"
}

eternal_url_host() {
  local url="$1"
  local rest host
  rest="${url#*://}"
  rest="${rest%%/*}"
  rest="${rest%%\?*}"
  rest="${rest##*@}"
  if [[ "$rest" == \[* ]]; then
    host="${rest%%]*}"
    host="${host#[}"
  else
    host="${rest%%:*}"
  fi
  printf '%s\n' "$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')"
}

# Expected site hostname. Explicit env wins; otherwise the public URL host.
# The production hostname is not hardcoded in the comparison.
eternal_frontend_expect_host() {
  local configured="${ETERNAL_FRONTEND_PUBLIC_HOST:-}"
  local base="${ETERNAL_FRONTEND_PUBLIC_URL:-https://produksi.eternalpackaging.id}"
  if [[ -n "$configured" ]]; then
    printf '%s\n' "$(printf '%s' "$configured" | tr '[:upper:]' '[:lower:]')"
    return 0
  fi
  eternal_url_host "$base"
}

# Absolute or relative Location. Protocol-relative and path redirects are pinned
# to HTTPS on the current host; the caller still rejects a different host.
eternal_frontend_resolve_redirect() {
  local base="$1"
  local loc="$2"
  local host
  loc="${loc//$'\r'/}"
  loc="${loc#"${loc%%[![:space:]]*}"}"
  loc="${loc%"${loc##*[![:space:]]}"}"
  if [[ "$loc" == http://* || "$loc" == https://* ]]; then
    printf '%s\n' "$loc"
    return 0
  fi
  host="$(eternal_url_host "$base")"
  if [[ "$loc" == //* ]]; then
    printf '%s\n' "https:${loc}"
    return 0
  fi
  if [[ "$loc" == /* ]]; then
    printf '%s\n' "https://${host}${loc}"
    return 0
  fi
  printf '%s\n' "https://${host}/${loc}"
}

# Final response must be HTTPS on the expected host. Other hosts are refused.
eternal_frontend_https_host_ok() {
  local url="$1"
  local expect="$2"
  local scheme host
  scheme="$(eternal_url_scheme "$url")"
  host="$(eternal_url_host "$url")"
  [[ "$scheme" == "https" && -n "$host" && "$host" == "$expect" ]]
}

# Limited-timeout GET. Does not use curl -L or -k.
# 301/302/303/307/308 are followed only to HTTPS on the expected host.
# 403, 404, 5xx, TLS failure, redirect loops, and other hosts fail closed.
# A 200 from a different host is not success.
eternal_frontend_http_status() {
  local start_url="$1"
  local label="$2"
  local expect="${3:-}"
  local timeout="${ETERNAL_FRONTEND_HTTP_TIMEOUT_S:-5}"
  local curl_bin="${ETERNAL_CURL_BIN:-curl}"
  local url="$start_url"
  local hop=0 max_hops=4
  local seen=$'\n'
  local tmp hdr err code ec loc next scheme host
  if [[ -z "$expect" ]]; then
    expect="$(eternal_frontend_expect_host)"
  fi
  expect="$(printf '%s' "$expect" | tr '[:upper:]' '[:lower:]')"
  [[ -n "$expect" ]] || {
    eternal_deploy_log "frontend HTTP FAIL label=$label expected_host_unset"
    return 1
  }
  while (( hop < max_hops )); do
    case "$seen" in
      *$'\n'"$url"$'\n'*)
        eternal_deploy_log "frontend HTTP FAIL label=$label redirect_loop"
        return 1
        ;;
    esac
    seen+="$url"$'\n'
    tmp="$(mktemp)"
    hdr="$(mktemp)"
    err="$(mktemp)"
    ec=0
    # TLS verification stays at curl's default. Never pass -k/--insecure or -L.
    code="$("$curl_bin" -sS --dump-header "$hdr" -o "$tmp" -w '%{http_code}' --max-time "$timeout" "$url" 2>"$err")" || ec=$?
    rm -f "$tmp"
    [[ -n "$code" ]] || code="000"
    if [[ "$ec" -ne 0 ]]; then
      if [[ "$ec" -eq 60 ]] || grep -qiE 'certificate|ssl|tls' "$err" 2>/dev/null; then
        eternal_deploy_log "frontend HTTP FAIL label=$label tls_invalid curl_exit=$ec http=$code"
      else
        eternal_deploy_log "frontend HTTP FAIL label=$label transport curl_exit=$ec http=$code"
      fi
      rm -f "$hdr" "$err"
      return 1
    fi
    rm -f "$err"
    case "$code" in
      200)
        scheme="$(eternal_url_scheme "$url")"
        host="$(eternal_url_host "$url")"
        rm -f "$hdr"
        if eternal_frontend_https_host_ok "$url" "$expect"; then
          eternal_deploy_log "frontend HTTP PASS label=$label http=200 host=$host"
          return 0
        fi
        eternal_deploy_log "frontend HTTP FAIL label=$label http=200 host=${host:-unknown} scheme=${scheme:-unknown} expected=$expect"
        return 1
        ;;
      301|302|303|307|308)
        loc="$(grep -i '^Location:' "$hdr" 2>/dev/null | tail -n 1 | sed -e 's/^[Ll]ocation:[[:space:]]*//' -e 's/[[:space:]\r]*$//' || true)"
        rm -f "$hdr"
        if [[ -z "$loc" ]]; then
          eternal_deploy_log "frontend HTTP FAIL label=$label http=$code redirect_missing"
          return 1
        fi
        next="$(eternal_frontend_resolve_redirect "$url" "$loc")"
        if ! eternal_frontend_https_host_ok "$next" "$expect"; then
          eternal_deploy_log "frontend HTTP FAIL label=$label http=$code redirect_host=$(eternal_url_host "$next") redirect_scheme=$(eternal_url_scheme "$next") expected=$expect"
          return 1
        fi
        url="$next"
        hop=$((hop + 1))
        ;;
      *)
        host="$(eternal_url_host "$url")"
        rm -f "$hdr"
        # label=asset identity; keep "label=… http=…" contiguous for operators/tests.
        eternal_deploy_log "frontend HTTP FAIL label=$label http=$code host=${host:-unknown} asset=$label url=$url"
        return 1
        ;;
    esac
  done
  eternal_deploy_log "frontend HTTP FAIL label=$label redirect_loop host=$expect asset=$label url=$start_url"
  return 1
}

# Mode bits plus public HTTP. Owner readability is not accepted as proof.
# HTTP target is ETERNAL_FRONTEND_PUBLIC_URL (fixture in synthetic tests).
eternal_frontend_nginx_ready() {
  local root="$1"
  local index="$root/index.html"
  local manifest="$root/asset-manifest.json"
  local base ref path url
  local -a refs=()
  [[ -f "$index" && ! -L "$index" ]] || {
    eternal_deploy_log "frontend mode FAIL index.html missing"
    return 1
  }
  if ! eternal_file_other_readable "$index"; then
    eternal_deploy_log "frontend mode FAIL index.html not other-readable mode=$(eternal_mode_other_digit "$index" 2>/dev/null || echo unknown) (owner test -r is not sufficient)"
    return 1
  fi
  if ! eternal_webroot_path_traversable "$root" "$index"; then
    eternal_deploy_log "frontend mode FAIL index directory not traversable"
    return 1
  fi
  while IFS= read -r ref; do
    [[ -z "$ref" ]] && continue
    case "$ref" in
      *.js|*.css) ;;
      *) continue ;;
    esac
    path="$root/$ref"
    [[ -f "$path" && ! -L "$path" ]] || {
      eternal_deploy_log "frontend mode FAIL missing published asset $ref"
      return 1
    }
    if ! eternal_file_other_readable "$path"; then
      eternal_deploy_log "frontend mode FAIL asset not other-readable path=$ref mode=$(eternal_mode_other_digit "$path" 2>/dev/null || echo unknown)"
      return 1
    fi
    if ! eternal_webroot_path_traversable "$root" "$path"; then
      eternal_deploy_log "frontend mode FAIL asset directory not traversable path=$ref"
      return 1
    fi
    refs+=("$ref")
  done < <(eternal_fe_list_required_static_assets \
    "$index" \
    "$([[ -f "$manifest" ]] && printf '%s' "$manifest")" | awk 'NF && !seen[$0]++')

  base="${ETERNAL_FRONTEND_PUBLIC_URL:-https://produksi.eternalpackaging.id}"
  base="${base%/}"
  [[ -n "$base" ]] || {
    eternal_deploy_log "frontend HTTP FAIL public URL unset"
    return 1
  }
  local expect_host
  expect_host="$(eternal_frontend_expect_host)"
  [[ -n "$expect_host" ]] || {
    eternal_deploy_log "frontend HTTP FAIL expected host unset"
    return 1
  }
  eternal_frontend_http_status "$base/" "index" "$expect_host" || return 1
  eternal_frontend_http_status "$base/index.html" "index.html" "$expect_host" || return 1
  for ref in "${refs[@]}"; do
    url="$base/$ref"
    eternal_frontend_http_status "$url" "$ref" "$expect_host" || return 1
  done
  eternal_deploy_log "frontend public access PASS"
  return 0
}

# Merge one file into webroot with skip-identical / hashed fail-closed / mutable overwrite.
# Returns 0 on success; dies on policy violation (publish) or returns 1 when soft=1.
# Copies preserve source bytes. Public mode is normalized afterwards (644 / 755).
eternal_webroot_install_file() {
  local src="$1"
  local dest_root="$2"
  local rel="${3#./}"
  local soft="${4:-0}"
  local dest="$dest_root/$rel"
  local dest_dir

  [[ -f "$src" ]] || {
    eternal_deploy_log "webroot install: source missing $src"
    [[ "$soft" == "1" ]] && return 1
    eternal_deploy_die "webroot install: source missing $src"
  }
  [[ "$rel" != *..* ]] || eternal_deploy_die "webroot install: path traversal refused ($rel)"
  [[ "$rel" != /* ]] || eternal_deploy_die "webroot install: absolute relpath refused ($rel)"

  dest_dir="$(dirname "$dest")"
  mkdir -p "$dest_dir" || {
    [[ "$soft" == "1" ]] && return 1
    eternal_deploy_die "webroot install: cannot mkdir $dest_dir"
  }
  eternal_path_is_under_root "$dest_root" "$dest" \
    || eternal_deploy_die "webroot install: destination escapes webroot ($rel)"
  eternal_webroot_ensure_traverse "$dest_root" "$dest_dir" "$soft" || return 1

  if [[ -e "$dest" || -L "$dest" ]]; then
    if [[ -L "$dest" ]]; then
      eternal_deploy_log "ERROR: webroot install refuses symlink destination $rel"
      [[ "$soft" == "1" ]] && return 1
      eternal_deploy_die "webroot install: symlink destination refused ($rel)"
    fi
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
      eternal_deploy_log "SKIP_IDENTICAL $rel"
      # Identical bytes do not prove Nginx can read the live file.
      eternal_webroot_ensure_public_file "$dest" "$soft" || return 1
      return 0
    fi
    if eternal_fe_is_hashed_relpath "$rel"; then
      # *.js.map / *.css.map: when the paired runtime asset (.js / .css) is present on
      # both sides and byte-identical, keep the live map (no overwrite, no delete).
      # Does not claim debug equivalence — only that the executable payload matches.
      # Hashed .js / .css and other artifacts remain fail-closed.
      if eternal_fe_is_runtime_sourcemap_relpath "$rel"; then
        local pair_src="${src%.map}"
        local pair_dest="${dest%.map}"
        if [[ -f "$pair_src" && -f "$pair_dest" && ! -L "$pair_src" && ! -L "$pair_dest" ]] \
          && cmp -s "$pair_src" "$pair_dest"; then
          eternal_deploy_log "SKIP_SOURCEMAP_COLLISION $rel (paired runtime asset byte-identical; keep live map)"
          eternal_webroot_ensure_public_file "$dest" "$soft" || return 1
          return 0
        fi
        eternal_deploy_log "ERROR: sourcemap collision without identical paired runtime asset path=$rel — refuse overwrite"
        [[ "$soft" == "1" ]] && return 1
        eternal_deploy_die "sourcemap collision without identical paired runtime asset: $rel"
      fi
      eternal_deploy_log "ERROR: hashed asset collision (byte-different) path=$rel — refuse overwrite"
      [[ "$soft" == "1" ]] && return 1
      eternal_deploy_die "hashed asset collision (byte-different): $rel"
    fi
    # Mutable metadata (manifest, favicon, …): overwrite only when writable.
    if [[ -w "$dest" ]]; then
      cp -a "$src" "$dest" || {
        [[ "$soft" == "1" ]] && return 1
        eternal_deploy_die "webroot install: failed writing mutable $rel"
      }
      eternal_webroot_ensure_public_file "$dest" "$soft" || return 1
      return 0
    fi
    eternal_deploy_log "ERROR: mutable webroot file not writable path=$rel (no recursive chown)"
    [[ "$soft" == "1" ]] && return 1
    eternal_deploy_die "webroot install: not writable $rel"
  fi

  # New file — copy into place (parent dir must be writable; preflight checks leaf).
  cp -a "$src" "$dest" || {
    [[ "$soft" == "1" ]] && return 1
    eternal_deploy_die "webroot install: copy failed $rel"
  }
  eternal_path_is_under_root "$dest_root" "$dest" \
    || eternal_deploy_die "webroot install: post-copy escape ($rel)"
  eternal_webroot_ensure_public_file "$dest" "$soft" || return 1
  return 0
}

# Merge build/backup tree into webroot except index.html (atomic separately).
# soft=1 → return 1 on failure instead of die (rollback restore).
eternal_webroot_merge_tree() {
  local src_root="$1"
  local dest_root="$2"
  local soft="${3:-0}"
  local src_file rel
  [[ -d "$src_root" ]] || {
    eternal_deploy_log "webroot merge: source tree missing $src_root"
    return 1
  }
  [[ -d "$dest_root" ]] || mkdir -p "$dest_root" || return 1
  [[ -w "$dest_root" ]] || {
    eternal_deploy_log "webroot merge: dest not writable $dest_root"
    return 1
  }

  while IFS= read -r -d '' src_file; do
    rel="${src_file#"$src_root"/}"
    [[ "$rel" == "index.html" ]] && continue
    [[ "$rel" == ".deploy-in-progress" ]] && continue
    if [[ "$soft" == "1" ]]; then
      eternal_webroot_install_file "$src_file" "$dest_root" "$rel" 1 || return 1
    else
      eternal_webroot_install_file "$src_file" "$dest_root" "$rel" 0
    fi
  done < <(find "$src_root" -type f -print0 2>/dev/null)

  return 0
}

# Validate a failed/incomplete release for reuse (no .deploy-ok). Never deletes the tree.
# Uses die-based validators; on failure the process exits (fail closed).
eternal_try_reuse_incomplete_release() {
  local release_dir="$1"
  local sha="$2"
  local recorded_sha recorded_fp actual_fp req_rel req_hash
  [[ -d "$release_dir" ]] || return 1
  [[ ! -f "$release_dir/.deploy-corrupt" ]] || {
    eternal_deploy_log "reuse refused: .deploy-corrupt present"
    return 1
  }
  [[ -f "$release_dir/.eternal-source-sha" ]] || {
    eternal_deploy_log "reuse refused: .eternal-source-sha missing"
    return 1
  }
  recorded_sha="$(tr -d '\n' <"$release_dir/.eternal-source-sha")"
  [[ "$recorded_sha" == "$sha" ]] || {
    eternal_deploy_log "reuse refused: source sha mismatch recorded=$recorded_sha want=$sha"
    return 1
  }
  # Frontend build is required only when a prior FE build was recorded for this
  # incomplete release. BE-only attempts leave no fingerprint/build — allow reuse.
  if [[ -f "$release_dir/frontend/build/index.html" ]]; then
    [[ -f "$release_dir/.eternal-frontend-fingerprint" ]] || {
      eternal_deploy_log "reuse refused: .eternal-frontend-fingerprint missing"
      return 1
    }
    recorded_fp="$(tr -d '\n' <"$release_dir/.eternal-frontend-fingerprint")"
    actual_fp="$(eternal_frontend_input_fingerprint "$release_dir/frontend")"
    [[ "$recorded_fp" == "$actual_fp" ]] || {
      eternal_deploy_log "reuse refused: frontend input fingerprint mismatch"
      return 1
    }
    local ref
    local manifest="$release_dir/frontend/build/asset-manifest.json"
    while IFS= read -r ref; do
      [[ -z "$ref" ]] && continue
      [[ -f "$release_dir/frontend/build/$ref" ]] || {
        eternal_deploy_log "reuse refused: build asset missing $ref"
        return 1
      }
    done < <(eternal_fe_list_required_static_assets \
      "$release_dir/frontend/build/index.html" \
      "$([[ -f "$manifest" ]] && printf '%s' "$manifest")" | awk 'NF && !seen[$0]++')
  elif [[ -f "$release_dir/.eternal-frontend-fingerprint" ]]; then
    eternal_deploy_log "reuse refused: fingerprint present but frontend/build/index.html missing"
    return 1
  else
    eternal_deploy_log "reuse: no frontend build (BE-only or pre-FE incomplete); continuing backend checks"
  fi

  [[ -x "$release_dir/backend/.venv/bin/uvicorn" ]] || {
    eternal_deploy_log "reuse refused: backend .venv/uvicorn missing"
    return 1
  }
  eternal_validate_backend_runtime "$release_dir/backend" || return 1
  if [[ -f "$release_dir/backend/.eternal-requirements-file" \
     && -f "$release_dir/backend/.eternal-requirements-sha256" ]]; then
    req_rel="$(tr -d '\n' <"$release_dir/backend/.eternal-requirements-file")"
    req_hash="$(tr -d '\n' <"$release_dir/backend/.eternal-requirements-sha256")"
    eternal_verify_requirements_provenance "$release_dir/backend" "$req_rel" "$req_hash" || return 1
    eternal_verify_requirements_installed "$release_dir/backend" "$req_rel" || return 1
  else
    eternal_deploy_log "reuse refused: requirements provenance markers missing"
    return 1
  fi
  return 0
}

# True when live required FE state matches backup: index (+ manifest when present in
# backup) byte-identical, and every *real* static asset ref exists on live.
# Asset refs use eternal_fe_list_required_static_assets (src/href + manifest only) —
# inline/remote strings such as PostHog ".../static/array.js" are ignored.
# Executable .js/.css refs present in the backup must also be byte-identical on live.
# Extra unreferenced files on live (including leftover hashed assets / .js.map) do not
# fail this check. Does not treat HTTP health as sufficient.
eternal_frontend_required_state_ok() {
  local live_root="$1"
  local backup_root="$2"
  local live_index="$live_root/index.html"
  local bak_index="$backup_root/index.html"
  local bak_manifest="$backup_root/asset-manifest.json"
  local live_manifest="$live_root/asset-manifest.json"
  [[ -f "$live_index" && -f "$bak_index" ]] || return 1
  cmp -s "$live_index" "$bak_index" || return 1
  if [[ -f "$bak_manifest" ]]; then
    [[ -f "$live_manifest" ]] || return 1
    cmp -s "$bak_manifest" "$live_manifest" || return 1
  fi
  local ref
  while IFS= read -r ref; do
    [[ -z "$ref" ]] && continue
    [[ -f "$live_root/$ref" ]] || return 1
    # Main executable assets from the previous frontend must match backup bytes when
    # the backup still holds them. Source maps are not required to match.
    case "$ref" in
      *.js.map|*.css.map) ;;
      *.js|*.css)
        if [[ -f "$backup_root/$ref" ]]; then
          cmp -s "$live_root/$ref" "$backup_root/$ref" || return 1
        fi
        ;;
    esac
  done < <(eternal_fe_list_required_static_assets \
    "$live_index" \
    "$([[ -f "$bak_manifest" ]] && printf '%s' "$bak_manifest")" | awk 'NF && !seen[$0]++')
  return 0
}

# Resolve requirements file relative to backend/. Default: requirements.portable.txt.
# Override: ETERNAL_REQUIREMENTS_FILE (relative path under backend only).
# No silent fallback to requirements.txt. Rejects absolute paths, .., and escapes.
eternal_resolve_requirements_relpath() {
  local backend_dir="$1"
  local rel="${ETERNAL_REQUIREMENTS_FILE:-requirements.portable.txt}"

  [[ -n "$rel" ]] || eternal_deploy_die "requirements: empty ETERNAL_REQUIREMENTS_FILE"
  [[ "$rel" != /* ]] || eternal_deploy_die "requirements: absolute path forbidden ($rel)"
  [[ "$rel" != *'..'* ]] || eternal_deploy_die "requirements: path must not contain '..' ($rel)"
  case "$rel" in
    *[[:space:]]*|*$'\n'*|*$'\r'*) eternal_deploy_die "requirements: whitespace/newline forbidden in path" ;;
  esac

  [[ -d "$backend_dir" ]] || eternal_deploy_die "requirements: backend dir missing: $backend_dir"
  local candidate="$backend_dir/$rel"
  [[ -e "$candidate" ]] || eternal_deploy_die "requirements: file missing: $rel (under $backend_dir); no fallback"
  [[ -f "$candidate" ]] || eternal_deploy_die "requirements: not a regular file: $rel"
  [[ ! -L "$candidate" ]] || eternal_deploy_die "requirements: symlink refused: $rel"

  local real_backend real_file
  real_backend="$(readlink -f "$backend_dir")"
  real_file="$(readlink -f "$candidate")"
  [[ -n "$real_backend" && -n "$real_file" ]] \
    || eternal_deploy_die "requirements: cannot resolve real paths"
  case "$real_file" in
    "$real_backend"/*) ;;
    *) eternal_deploy_die "requirements: path escapes backend dir ($rel)" ;;
  esac

  printf '%s\n' "$rel"
}

eternal_requirements_abs_path() {
  local backend_dir="$1"
  local rel
  rel="$(eternal_resolve_requirements_relpath "$backend_dir")"
  printf '%s\n' "$backend_dir/$rel"
}

eternal_requirements_sha256() {
  local backend_dir="$1"
  local abs
  abs="$(eternal_requirements_abs_path "$backend_dir")"
  sha256sum "$abs" | awk '{print $1}'
}

# Verify venv packages satisfy requirements file + pip check (no network).
# Full mode: packaging.requirements.Requirement + importlib.metadata + PEP 610
# direct_url.json for name @ url lines (see verify_requirements_installed.py).
# Stub mode: stub pip check only (fixture requirements may be opaque markers).
eternal_verify_requirements_installed() {
  local backend_dir="$1"
  local req_rel="$2"
  local req_abs="$backend_dir/$req_rel"
  local pip="$backend_dir/.venv/bin/pip"
  local py="$backend_dir/.venv/bin/python"
  local verifier="${ETERNAL_DEPLOY_LIB_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}/verify_requirements_installed.py"
  [[ -f "$req_abs" ]] || eternal_deploy_die "dependency evidence: requirements missing: $req_rel"
  [[ -d "$backend_dir/.venv" ]] || eternal_deploy_die "dependency evidence: .venv missing"
  [[ -x "$pip" ]] || eternal_deploy_die "dependency evidence: pip missing/not executable: $pip"
  [[ -x "$py" ]] || eternal_deploy_die "dependency evidence: python missing/not executable: $py"
  [[ -f "$verifier" ]] || eternal_deploy_die "dependency evidence: verifier missing: $verifier"

  if ! "$pip" check; then
    eternal_deploy_die "dependency evidence: pip check FAILED for $req_rel"
  fi
  eternal_deploy_log "pip check PASS requirements_file=$req_rel"

  if [[ "${ETERNAL_RUNTIME_MODE:-full}" == "stub" ]]; then
    eternal_deploy_log "stub dependency evidence OK (pip check only) requirements_file=$req_rel"
    return 0
  fi

  # Use venv python so packaging/importlib.metadata see that environment.
  if ! "$py" "$verifier" "$req_abs"; then
    eternal_deploy_die "dependency evidence: installed packages do not satisfy $req_rel"
  fi
  eternal_deploy_log "installed packages match requirements_file=$req_rel"
}

# Verify provenance requirements_file + hash against disk (fail-closed).
eternal_verify_requirements_provenance() {
  local backend_dir="$1"
  local recorded_file="${2:-}"
  local recorded_hash="${3:-}"
  [[ -n "$recorded_file" ]] || eternal_deploy_die "requirements provenance: requirements_file missing (refuse invented/legacy marker)"
  [[ -n "$recorded_hash" ]] || eternal_deploy_die "requirements provenance: requirements_sha256 missing"

  local prev="${ETERNAL_REQUIREMENTS_FILE:-}"
  ETERNAL_REQUIREMENTS_FILE="$recorded_file"
  local rel abs actual
  rel="$(eternal_resolve_requirements_relpath "$backend_dir")"
  if [[ -n "$prev" ]]; then
    ETERNAL_REQUIREMENTS_FILE="$prev"
  else
    unset ETERNAL_REQUIREMENTS_FILE
  fi
  [[ "$rel" == "$recorded_file" ]] \
    || eternal_deploy_die "requirements provenance: resolved=$rel != recorded=$recorded_file"
  abs="$backend_dir/$rel"
  actual="$(sha256sum "$abs" | awk '{print $1}')"
  [[ "$actual" == "$recorded_hash" ]] \
    || eternal_deploy_die "requirements provenance: hash mismatch for $recorded_file"
}

eternal_manifest_guard() {
  local release_dir="$1"
  if [[ -d "$release_dir/backend" ]]; then
    # Same resolver as runtime prep — default portable; no silent txt fallback.
    eternal_resolve_requirements_relpath "$release_dir/backend" >/dev/null
  fi
  if [[ -d "$release_dir/frontend" ]]; then
    [[ -f "$release_dir/frontend/package.json" ]] \
      || eternal_deploy_die "manifest guard: frontend/package.json missing"
    if [[ -f "$release_dir/yarn.lock" || -f "$release_dir/frontend/yarn.lock" || -f "$release_dir/frontend/package-lock.json" ]]; then
      :
    else
      eternal_deploy_die "manifest guard: frontend lockfile missing"
    fi
  fi
}

eternal_sha_is_ancestor_of_main() {
  local git_dir="$1"
  local sha="$2"
  local main_ref="${3:-main}"
  git --git-dir="$git_dir" rev-parse --verify "$sha^{commit}" >/dev/null 2>&1 \
    || eternal_deploy_die "SHA not found in mirror: $sha"
  git --git-dir="$git_dir" merge-base --is-ancestor "$sha" "refs/heads/$main_ref" \
    || eternal_deploy_die "SHA is not merged into $main_ref (ancestry check failed)"
}

# Health body must report mongo=true and mode=operational.
eternal_health_body_ok() {
  local body="$1"
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$body" | python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(1)
mongo=d.get("mongo")
mode=d.get("mode")
sys.exit(0 if mongo is True and mode=="operational" else 1)
'
    return $?
  fi
  # Minimal fallback without python: strict substring checks (synthetic/tests).
  [[ "$body" == *'"mongo": true'* || "$body" == *'"mongo":true'* ]] || return 1
  [[ "$body" == *'"mode": "operational"'* || "$body" == *'"mode":"operational"'* ]] || return 1
  return 0
}

# Production frontend install/build: Yarn only (matches CI). No npm fallback.
# Invoked only when ETERNAL_FRONTEND_BUILD_MODE=legacy (explicit). Never an artifact fallback.
eternal_frontend_production_build() {
  local frontend_dir="$1"
  [[ -d "$frontend_dir" ]] || eternal_deploy_die "frontend tree missing: $frontend_dir"
  [[ -f "$frontend_dir/yarn.lock" ]] \
    || eternal_deploy_die "frontend yarn.lock missing; production deploy requires Yarn (CI standard); npm fallback removed"
  command -v yarn >/dev/null 2>&1 \
    || eternal_deploy_die "yarn required but not found; production deploy does not use npm"

  (
    cd "$frontend_dir"
    yarn install --frozen-lockfile
    yarn build
  ) || eternal_deploy_die "frontend yarn install/build failed; live not activated"
}

# --- Frontend artifact provenance (schema eternal.frontend-artifact.v1) ---

ETERNAL_FRONTEND_ARTIFACT_SCHEMA="${ETERNAL_FRONTEND_ARTIFACT_SCHEMA:-eternal.frontend-artifact.v1}"
ETERNAL_FRONTEND_ARTIFACT_WORKFLOW="${ETERNAL_FRONTEND_ARTIFACT_WORKFLOW:-frontend-artifact.yml}"

eternal_frontend_artifact_json_get() {
  local manifest="$1"
  local key="$2"
  python3 - "$manifest" "$key" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
key = sys.argv[2]
val = doc.get(key, "")
if val is None:
    val = ""
print(val if isinstance(val, str) else str(val))
PY
}

# Verify manifest identity fields against expected deploy SHA / trusted workflow / env+input fps.
# Optional args 5–6: expect_run_id expect_run_attempt (required when verifying a GH download).
eternal_verify_frontend_artifact_manifest() {
  local manifest="$1"
  local expect_sha="$2"
  local expect_input_fp="$3"
  local expect_env_fp="$4"
  local expect_run_id="${5:-}"
  local expect_run_attempt="${6:-}"
  local schema workflow git_sha artifact_sha archive_name input_fp env_fp
  local run_id run_attempt
  [[ -f "$manifest" ]] || eternal_deploy_die "frontend artifact: manifest missing"
  schema="$(eternal_frontend_artifact_json_get "$manifest" schema)"
  [[ "$schema" == "$ETERNAL_FRONTEND_ARTIFACT_SCHEMA" ]] \
    || eternal_deploy_die "frontend artifact: unsupported schema ($schema)"
  workflow="$(eternal_frontend_artifact_json_get "$manifest" workflow)"
  [[ "$workflow" == "$ETERNAL_FRONTEND_ARTIFACT_WORKFLOW" ]] \
    || eternal_deploy_die "frontend artifact: untrusted workflow ($workflow)"
  git_sha="$(eternal_frontend_artifact_json_get "$manifest" git_sha)"
  eternal_require_sha40 "$git_sha"
  [[ "$git_sha" == "$expect_sha" ]] \
    || eternal_deploy_die "frontend artifact: git_sha mismatch manifest=$git_sha want=$expect_sha"
  artifact_sha="$(eternal_frontend_artifact_json_get "$manifest" artifact_sha256)"
  [[ "$artifact_sha" =~ ^[0-9a-f]{64}$ ]] \
    || eternal_deploy_die "frontend artifact: artifact_sha256 malformed"
  archive_name="$(eternal_frontend_artifact_json_get "$manifest" archive_name)"
  [[ -n "$archive_name" && "$archive_name" != *"/"* && "$archive_name" != *".."* ]] \
    || eternal_deploy_die "frontend artifact: archive_name unsafe"
  input_fp="$(eternal_frontend_artifact_json_get "$manifest" input_fingerprint_sha256)"
  env_fp="$(eternal_frontend_artifact_json_get "$manifest" env_fingerprint_sha256)"
  [[ "$input_fp" == "$expect_input_fp" ]] \
    || eternal_deploy_die "frontend artifact: input fingerprint mismatch"
  [[ "$env_fp" == "$expect_env_fp" ]] \
    || eternal_deploy_die "frontend artifact: env fingerprint mismatch (production build env)"
  run_id="$(eternal_frontend_artifact_json_get "$manifest" run_id)"
  run_attempt="$(eternal_frontend_artifact_json_get "$manifest" run_attempt)"
  [[ -n "$run_id" && "$run_id" != "0" && "$run_id" =~ ^[0-9]+$ ]] \
    || eternal_deploy_die "frontend artifact: run_id missing or malformed"
  [[ -n "$run_attempt" && "$run_attempt" =~ ^[1-9][0-9]*$ ]] \
    || eternal_deploy_die "frontend artifact: run_attempt missing or malformed"
  if [[ -n "$expect_run_id" || -n "$expect_run_attempt" ]]; then
    [[ -n "$expect_run_id" && -n "$expect_run_attempt" ]] \
      || eternal_deploy_die "frontend artifact: run provenance expectation incomplete"
    [[ "$run_id" == "$expect_run_id" ]] \
      || eternal_deploy_die "frontend artifact: run_id mismatch manifest=$run_id actions=$expect_run_id"
    [[ "$run_attempt" == "$expect_run_attempt" ]] \
      || eternal_deploy_die "frontend artifact: run_attempt mismatch manifest=$run_attempt actions=$expect_run_attempt"
  fi
}

# Extract tar.gz into dest_build with hard path/symlink policy. Atomic via temp dir.
eternal_safe_extract_frontend_artifact() {
  local archive="$1"
  local dest_build="$2"
  local parent tmp listing
  [[ -f "$archive" ]] || eternal_deploy_die "frontend artifact: archive missing"
  parent="$(dirname "$dest_build")"
  mkdir -p "$parent" || eternal_deploy_die "frontend artifact: cannot create build parent"
  [[ -w "$parent" ]] || eternal_deploy_die "frontend artifact: build parent not writable"
  tmp="${dest_build}.artifact-extract.$$"
  rm -rf "$tmp"
  mkdir -p "$tmp"

  # List entries first; refuse unsafe paths / symlinks / absolute names.
  # Include stderr (GNU tar may warn while still listing traversal names).
  listing="$(tar -tzf "$archive" 2>&1 || true)"
  [[ -n "$listing" ]] || {
    rm -rf "$tmp"
    eternal_deploy_die "frontend artifact: archive empty or unreadable"
  }
  while IFS= read -r ent; do
    [[ -z "$ent" || "$ent" == "./" ]] && continue
    [[ "$ent" == tar:* ]] && continue
    ent="${ent#./}"
    if [[ "$ent" == /* || "$ent" == *".."* ]]; then
      rm -rf "$tmp"
      eternal_deploy_die "frontend artifact: unsafe archive path ($ent)"
    fi
  done <<<"$listing"

  if tar -tzf "$archive" | grep -q '/$'; then
    :
  fi
  # Extract without preserving symlinks as links that escape (GNU tar --no-same-owner).
  if ! tar -xzf "$archive" -C "$tmp" --no-same-owner --no-same-permissions 2>/dev/null; then
    rm -rf "$tmp"
    eternal_deploy_die "frontend artifact: extract failed"
  fi
  # Permit only regular files and directories (refuse symlink/fifo/device/socket).
  if find "$tmp" \( -type l -o -type p -o -type b -o -type c -o -type s \) -print -quit | grep -q .; then
    rm -rf "$tmp"
    eternal_deploy_die "frontend artifact: archive contains non-regular special entry (refused)"
  fi
  if find "$tmp" ! -type f ! -type d -print -quit | grep -q .; then
    rm -rf "$tmp"
    eternal_deploy_die "frontend artifact: archive contains unsupported file type (refused)"
  fi
  [[ -f "$tmp/index.html" ]] || {
    rm -rf "$tmp"
    eternal_deploy_die "frontend artifact: extracted index.html missing"
  }
  [[ -f "$tmp/asset-manifest.json" ]] || {
    rm -rf "$tmp"
    eternal_deploy_die "frontend artifact: extracted asset-manifest.json missing"
  }

  # Atomic replace of build dir.
  rm -rf "${dest_build}.old" 2>/dev/null || true
  if [[ -d "$dest_build" ]]; then
    mv "$dest_build" "${dest_build}.old"
  fi
  if ! mv "$tmp" "$dest_build"; then
    [[ -d "${dest_build}.old" ]] && mv "${dest_build}.old" "$dest_build"
    rm -rf "$tmp"
    eternal_deploy_die "frontend artifact: atomic place failed"
  fi
  rm -rf "${dest_build}.old" 2>/dev/null || true
}

eternal_write_frontend_artifact_marker() {
  local release_dir="$1"
  local manifest="$2"
  local marker="$release_dir/.eternal-frontend-artifact"
  {
    printf 'schema=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" schema)"
    printf 'git_sha=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" git_sha)"
    printf 'workflow=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" workflow)"
    printf 'run_id=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" run_id)"
    printf 'run_attempt=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" run_attempt)"
    printf 'artifact_sha256=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" artifact_sha256)"
    printf 'input_fingerprint_sha256=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" input_fingerprint_sha256)"
    printf 'env_fingerprint_sha256=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" env_fingerprint_sha256)"
    printf 'index_sha256=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" index_sha256)"
    printf 'asset_manifest_sha256=%s\n' "$(eternal_frontend_artifact_json_get "$manifest" asset_manifest_sha256)"
  } >"$marker"
}

# Install artifact from a local directory containing manifest.json + archive.
eternal_install_frontend_artifact_from_dir() {
  local release_dir="$1"
  local sha="$2"
  local artifact_dir="$3"
  local manifest archive expect_input expect_env actual_sha index_got asset_got
  local frontend_dir="$release_dir/frontend"

  [[ -d "$artifact_dir" ]] || eternal_deploy_die "frontend artifact dir missing: $artifact_dir"
  manifest="$artifact_dir/manifest.json"
  expect_input="$(eternal_frontend_input_fingerprint "$frontend_dir")"
  expect_env="$(eternal_frontend_expected_env_fingerprint)"
  eternal_verify_frontend_artifact_manifest "$manifest" "$sha" "$expect_input" "$expect_env"

  archive="$artifact_dir/$(eternal_frontend_artifact_json_get "$manifest" archive_name)"
  [[ -f "$archive" ]] || eternal_deploy_die "frontend artifact: archive file missing"
  actual_sha="$(sha256sum "$archive" | awk '{print $1}')"
  [[ "$actual_sha" == "$(eternal_frontend_artifact_json_get "$manifest" artifact_sha256)" ]] \
    || eternal_deploy_die "frontend artifact: archive checksum mismatch"

  eternal_safe_extract_frontend_artifact "$archive" "$frontend_dir/build"

  index_got="$(sha256sum "$frontend_dir/build/index.html" | awk '{print $1}')"
  asset_got="$(sha256sum "$frontend_dir/build/asset-manifest.json" | awk '{print $1}')"
  [[ "$index_got" == "$(eternal_frontend_artifact_json_get "$manifest" index_sha256)" ]] \
    || eternal_deploy_die "frontend artifact: index.html digest mismatch after extract"
  [[ "$asset_got" == "$(eternal_frontend_artifact_json_get "$manifest" asset_manifest_sha256)" ]] \
    || eternal_deploy_die "frontend artifact: asset-manifest digest mismatch after extract"

  # Required static assets from index/manifest must exist.
  local ref
  while IFS= read -r ref; do
    [[ -z "$ref" ]] && continue
    [[ -f "$frontend_dir/build/$ref" ]] \
      || eternal_deploy_die "frontend artifact: required asset missing after extract ($ref)"
  done < <(eternal_fe_list_required_static_assets \
    "$frontend_dir/build/index.html" \
    "$frontend_dir/build/asset-manifest.json")

  eternal_write_frontend_fingerprint "$release_dir"
  eternal_write_frontend_artifact_marker "$release_dir" "$manifest"
  eternal_deploy_log "frontend artifact installed (yarn/craco skipped) sha=$sha"
}

# Expected public env fingerprint for artifact verification (fail-closed).
# ALWAYS derived from validated FRONTEND_PROD_ENV_MODE (+ URL). Production must
# never accept ETERNAL_FRONTEND_EXPECTED_ENV_FP as an independent bypass.
# Test harness may set ETERNAL_FRONTEND_EXPECTED_ENV_FP only when
# ETERNAL_FRONTEND_ENV_FP_TEST_LOCK=1; the value must still equal the derivation
# (wrong override is rejected).
eternal_frontend_expected_env_fingerprint() {
  local derived
  derived="$(
    eternal_apply_frontend_prod_build_env
    eternal_frontend_public_env_fingerprint
  )"
  if [[ -n "${ETERNAL_FRONTEND_EXPECTED_ENV_FP:-}" ]]; then
    if [[ "${ETERNAL_FRONTEND_ENV_FP_TEST_LOCK:-0}" != "1" ]]; then
      eternal_deploy_die "ETERNAL_FRONTEND_EXPECTED_ENV_FP is set but ETERNAL_FRONTEND_ENV_FP_TEST_LOCK!=1; refuse production env bypass"
    fi
    if [[ "$ETERNAL_FRONTEND_EXPECTED_ENV_FP" != "$derived" ]]; then
      eternal_deploy_die "frontend artifact: ETERNAL_FRONTEND_EXPECTED_ENV_FP does not match FRONTEND_PROD_ENV_MODE derivation (bypass refused)"
    fi
  fi
  printf '%s\n' "$derived"
}

# True when release already has a verified artifact marker matching sha+fingerprints.
eternal_frontend_artifact_already_installed() {
  local release_dir="$1"
  local sha="$2"
  local marker="$release_dir/.eternal-frontend-artifact"
  local recorded_sha recorded_input expect_input
  [[ -f "$marker" ]] || return 1
  [[ -f "$release_dir/frontend/build/index.html" ]] || return 1
  recorded_sha="$(sed -n 's/^git_sha=//p' "$marker" | head -n1)"
  [[ "$recorded_sha" == "$sha" ]] || return 1
  recorded_input="$(sed -n 's/^input_fingerprint_sha256=//p' "$marker" | head -n1)"
  expect_input="$(eternal_frontend_input_fingerprint "$release_dir/frontend")"
  [[ "$recorded_input" == "$expect_input" ]] || return 1
  local recorded_env expect_env
  recorded_env="$(sed -n 's/^env_fingerprint_sha256=//p' "$marker" | head -n1)"
  expect_env="$(eternal_frontend_expected_env_fingerprint)"
  [[ "$recorded_env" == "$expect_env" ]] || return 1
  return 0
}

# Resolve artifact directory for SHA: local dir, or mock GH dir, or die (no yarn fallback).
eternal_resolve_frontend_artifact_dir() {
  local sha="$1"
  local local_dir="${ETERNAL_FRONTEND_ARTIFACT_DIR:-}"
  local mock="${ETERNAL_GH_ARTIFACT_MOCK_DIR:-}"

  if [[ -n "$local_dir" ]]; then
    [[ -d "$local_dir" ]] || eternal_deploy_die "ETERNAL_FRONTEND_ARTIFACT_DIR missing: $local_dir"
    printf '%s\n' "$local_dir"
    return 0
  fi
  if [[ -n "$mock" ]]; then
    local cand="$mock/$sha"
    [[ -d "$cand" ]] || eternal_deploy_die "frontend artifact mock missing for sha=$sha"
    printf '%s\n' "$cand"
    return 0
  fi
  # Live GitHub download path (least privilege token). Never log token.
  if [[ -n "${ETERNAL_FRONTEND_ARTIFACT_TOKEN:-${GH_TOKEN:-}}" && -n "${ETERNAL_GITHUB_REPO:-}" ]]; then
    eternal_download_frontend_artifact_from_github "$sha"
    return 0
  fi
  eternal_deploy_die "frontend artifact unavailable for sha=$sha (set ETERNAL_FRONTEND_ARTIFACT_DIR or GH credentials; no yarn fallback)"
}

# Bind downloaded Actions run identity to manifest (fail-closed; never swap artifacts).
eternal_verify_frontend_artifact_actions_run() {
  local manifest="$1"
  local expect_run_id="$2"
  local expect_run_attempt="$3"
  local run_id run_attempt
  [[ -f "$manifest" ]] || eternal_deploy_die "frontend artifact: manifest missing for run provenance"
  [[ -n "$expect_run_id" && "$expect_run_id" =~ ^[0-9]+$ ]] \
    || eternal_deploy_die "frontend artifact: Actions run_id expectation malformed"
  [[ -n "$expect_run_attempt" && "$expect_run_attempt" =~ ^[1-9][0-9]*$ ]] \
    || eternal_deploy_die "frontend artifact: Actions run_attempt expectation malformed"
  run_id="$(eternal_frontend_artifact_json_get "$manifest" run_id)"
  run_attempt="$(eternal_frontend_artifact_json_get "$manifest" run_attempt)"
  [[ "$run_id" == "$expect_run_id" ]] \
    || eternal_deploy_die "frontend artifact: run_id mismatch manifest=$run_id actions=$expect_run_id"
  [[ "$run_attempt" == "$expect_run_attempt" ]] \
    || eternal_deploy_die "frontend artifact: run_attempt mismatch manifest=$run_attempt actions=$expect_run_attempt"
}

eternal_download_frontend_artifact_from_github() {
  local sha="$1"
  local repo="${ETERNAL_GITHUB_REPO:?}"
  local token="${ETERNAL_FRONTEND_ARTIFACT_TOKEN:-${GH_TOKEN:-}}"
  local workflow="$ETERNAL_FRONTEND_ARTIFACT_WORKFLOW"
  local dest="${ETERNAL_FRONTEND_ARTIFACT_CACHE:-/tmp}/eternal-fe-artifact-${sha}"
  local api="${ETERNAL_GITHUB_API:-https://api.github.com}"
  local runs_json run_id run_attempt arts_json art_id zip_path
  [[ -n "$token" ]] || eternal_deploy_die "frontend artifact: token missing"
  rm -rf "$dest"
  mkdir -p "$dest"

  # Find newest successful workflow run for head SHA (Actions API).
  runs_json="$(
    curl -fsS \
      -H "Authorization: Bearer ${token}" \
      -H "Accept: application/vnd.github+json" \
      "${api}/repos/${repo}/actions/workflows/${workflow}/runs?head_sha=${sha}&per_page=10" \
      2>/dev/null
  )" || eternal_deploy_die "frontend artifact: failed to list workflow runs (network/auth)"
  # Emit "run_id run_attempt" for the selected successful run (no silent alternate).
  read -r run_id run_attempt < <(
    printf '%s' "$runs_json" | python3 -c '
import json,sys
d=json.load(sys.stdin)
runs=d.get("workflow_runs") or []
for r in runs:
    if r.get("head_sha")==sys.argv[1] and r.get("status")=="completed" and r.get("conclusion")=="success":
        attempt=r.get("run_attempt") or 1
        print("%s %s" % (r.get("id"), attempt))
        sys.exit(0)
sys.exit(1)
' "$sha"
  ) || eternal_deploy_die "frontend artifact: no successful $workflow run for sha=$sha"
  [[ "$run_id" =~ ^[0-9]+$ ]] || eternal_deploy_die "frontend artifact: Actions run_id malformed"
  [[ "$run_attempt" =~ ^[1-9][0-9]*$ ]] || eternal_deploy_die "frontend artifact: Actions run_attempt malformed"

  arts_json="$(
    curl -fsS \
      -H "Authorization: Bearer ${token}" \
      -H "Accept: application/vnd.github+json" \
      "${api}/repos/${repo}/actions/runs/${run_id}/artifacts?per_page=50"
  )" || eternal_deploy_die "frontend artifact: list artifacts failed"
  art_id="$(
    printf '%s' "$arts_json" | python3 -c '
import json,sys
want="eternal-frontend-build-"+sys.argv[1]
d=json.load(sys.stdin)
for a in d.get("artifacts") or []:
    if a.get("name")==want and not a.get("expired"):
        print(a.get("id")); sys.exit(0)
sys.exit(1)
' "$sha"
  )" || eternal_deploy_die "frontend artifact: named artifact missing/expired for sha=$sha"

  zip_path="$dest/artifact.zip"
  curl -fsSL \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: application/vnd.github+json" \
    -o "$zip_path" \
    "${api}/repos/${repo}/actions/artifacts/${art_id}/zip" \
    || eternal_deploy_die "frontend artifact: download failed"
  # Token must not appear in logs; avoid dumping curl headers.
  (
    cd "$dest" || exit 1
    python3 - "$zip_path" <<'PY'
import sys, zipfile
zpath = sys.argv[1]
with zipfile.ZipFile(zpath) as zf:
    for info in zf.infolist():
        name = info.filename
        parts = name.split("/")
        if ".." in parts or name.startswith("/"):
            raise SystemExit("unsafe zip entry")
    zf.extractall(".")
PY
  ) || eternal_deploy_die "frontend artifact: zip extract refused"
  rm -f "$zip_path"
  [[ -f "$dest/manifest.json" ]] || eternal_deploy_die "frontend artifact: manifest.json missing after download"
  # Provenance bind: refuse mismatched run/attempt (do not try another artifact).
  eternal_verify_frontend_artifact_actions_run "$dest/manifest.json" "$run_id" "$run_attempt"
  printf '%s\n' "$run_id" >"$dest/.eternal-actions-run-id"
  printf '%s\n' "$run_attempt" >"$dest/.eternal-actions-run-attempt"
  printf '%s\n' "$dest"
}

eternal_constant_time_eq() {
  local a="$1"
  local b="$2"
  local ha hb
  ha="$(printf '%s' "$a" | sha256sum | awk '{print $1}')"
  hb="$(printf '%s' "$b" | sha256sum | awk '{print $1}')"
  [[ "$ha" == "$hb" ]]
}

# Hash all regular files under frozen source trees. No truncation.
# Excludes .venv / __pycache__ (runtime validated separately). Refuses unexpected symlinks.
eternal_baseline_collect_manifest() {
  local baseline_dir="$1"
  local manifest="$2"
  (
    cd "$baseline_dir" || exit 1
    local symlinks
    symlinks="$(find tree/backend frontend-snapshot \( -path 'tree/backend/.venv' -o -path 'tree/backend/.venv/*' \) -prune -o -type l -print 2>/dev/null | head -n 20 || true)"
    if [[ -n "${symlinks//[[:space:]]/}" ]]; then
      printf 'baseline integrity: unexpected symlinks in frozen trees:\n%s\n' "$symlinks" >&2
      exit 1
    fi
    # -print0 / sort -z / xargs -0: safe for spaces/newlines in names; no head truncate.
    find tree/backend frontend-snapshot \
      \( -path 'tree/backend/.venv' -o -path 'tree/backend/.venv/*' \
         -o -path 'tree/backend/.eternal-requirements-file' \
         -o -path 'tree/backend/.eternal-requirements-sha256' \
         -o -path '*/__pycache__' -o -path '*/__pycache__/*' \) -prune -o \
      -type f -print0 2>/dev/null \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum
  ) >"$manifest"
}

# Fresh venv in target backend (never copy live venv; never mutate active service venv).
# ETERNAL_RUNTIME_MODE=stub|full (default full). Stub is for synthetic tests only.
# Installs ETERNAL_REQUIREMENTS_FILE (default requirements.portable.txt) — no silent txt fallback.
eternal_prepare_backend_runtime() {
  local backend_dir="$1"
  local mode="${ETERNAL_RUNTIME_MODE:-full}"
  local py="${ETERNAL_PYTHON_BIN:-python3}"
  local req_rel req_abs req_hash
  [[ -d "$backend_dir" ]] || eternal_deploy_die "runtime prep: backend dir missing: $backend_dir"
  command -v "$py" >/dev/null 2>&1 || eternal_deploy_die "runtime prep: python missing: $py"

  req_rel="$(eternal_resolve_requirements_relpath "$backend_dir")"
  req_abs="$backend_dir/$req_rel"
  req_hash="$(sha256sum "$req_abs" | awk '{print $1}')"
  eternal_deploy_log "runtime prep requirements_file=$req_rel sha256=$req_hash"

  # Never reuse/copy a foreign .venv (shebangs point at old absolute paths).
  if [[ -e "$backend_dir/.venv" ]]; then
    rm -rf "$backend_dir/.venv"
  fi

  if [[ "$mode" == "stub" ]]; then
    mkdir -p "$backend_dir/.venv/bin"
    local sys_py
    sys_py="$(command -v "$py")"
    ln -sfn "$sys_py" "$backend_dir/.venv/bin/python"
    ln -sfn "$sys_py" "$backend_dir/.venv/bin/python3"
    cat >"$backend_dir/.venv/bin/uvicorn" <<EOF
#!/usr/bin/env bash
# stub uvicorn for synthetic harness — not production
exec "$(readlink -f "$backend_dir/.venv/bin/python")" -c 'import sys; sys.exit(0)' "\$@"
EOF
    chmod +x "$backend_dir/.venv/bin/uvicorn"
    # Stub pip: check/freeze succeed so finalize dependency evidence can run in harness.
    cat >"$backend_dir/.venv/bin/pip" <<'EOF'
#!/usr/bin/env bash
# stub pip for synthetic harness — not production
case "${1:-}" in
  check) exit 0 ;;
  freeze) exit 0 ;;
  *) exit 0 ;;
esac
EOF
    chmod +x "$backend_dir/.venv/bin/pip"
  else
    "$py" -m venv "$backend_dir/.venv" || eternal_deploy_die "runtime prep: venv create failed"
    # shellcheck disable=SC1091
    "$backend_dir/.venv/bin/pip" install --upgrade pip wheel >/dev/null \
      || eternal_deploy_die "runtime prep: pip bootstrap failed"
    "$backend_dir/.venv/bin/pip" install -r "$req_abs" \
      || eternal_deploy_die "runtime prep: pip install failed for $req_rel"
  fi
  # Record what was installed for callers (baseline write / release marker).
  printf '%s\n' "$req_rel" >"$backend_dir/.eternal-requirements-file"
  printf '%s\n' "$req_hash" >"$backend_dir/.eternal-requirements-sha256"
  eternal_validate_backend_runtime "$backend_dir"
}

eternal_validate_backend_runtime() {
  local backend_dir="$1"
  local py="$backend_dir/.venv/bin/python"
  local uv="$backend_dir/.venv/bin/uvicorn"
  [[ -x "$py" ]] || eternal_deploy_die "runtime validate: python missing/not executable: $py"
  [[ -x "$uv" ]] || eternal_deploy_die "runtime validate: uvicorn missing/not executable: $uv"

  local py_real uv_shebang
  py_real="$(readlink -f "$py" 2>/dev/null || true)"
  [[ -n "$py_real" && -x "$py_real" ]] || eternal_deploy_die "runtime validate: cannot resolve venv python"

  # Refuse shebang still pointing at a foreign/legacy venv path.
  if [[ -f "$uv" && ! -L "$uv" ]]; then
    uv_shebang="$(head -n1 "$uv" 2>/dev/null || true)"
    if [[ "$uv_shebang" == "#!"* ]]; then
      if [[ "$uv_shebang" == *"/opt/eternal-packaging/backend/.venv/"* ]] \
        || [[ "$uv_shebang" == *"phase3-stage"* ]]; then
        eternal_deploy_die "runtime validate: uvicorn shebang points at legacy/stage venv; refuse"
      fi
      # Accept shebang under this backend's .venv or a stub/env bash wrapper.
      if [[ "$uv_shebang" == "#!"*python* && "$uv_shebang" != *"$backend_dir/.venv/"* ]]; then
        eternal_deploy_die "runtime validate: uvicorn shebang not under release venv ($uv_shebang)"
      fi
    fi
  fi

  # Isolated smoke only — no production DB connection.
  if [[ "${ETERNAL_RUNTIME_MODE:-full}" == "stub" ]]; then
    "$py" -c 'import sys; assert sys.version_info >= (3, 10)' \
      || eternal_deploy_die "runtime validate: stub python smoke failed"
  else
    "$py" -c 'import uvicorn, fastapi' \
      || eternal_deploy_die "runtime validate: cannot import uvicorn/fastapi in release venv"
    "$uv" --help >/dev/null 2>&1 \
      || eternal_deploy_die "runtime validate: uvicorn --help failed"
  fi
  eternal_deploy_log "runtime validate OK backend=$backend_dir"
}

# Write provenance + integrity manifest for a verified baseline directory.
# Digests alone prove content; immutable=1 is recorded only after digest write succeeds.
eternal_baseline_write_integrity() {
  local baseline_dir="$1"
  local backend_src="${2:-}"
  local wd="${3:-}"
  local exe="${4:-}"
  [[ -d "$baseline_dir/tree/backend" ]] || eternal_deploy_die "baseline integrity: frozen backend missing"
  [[ -d "$baseline_dir/frontend-snapshot" ]] || eternal_deploy_die "baseline integrity: frontend snapshot missing"
  [[ -f "$baseline_dir/frontend-snapshot/index.html" ]] \
    || eternal_deploy_die "baseline integrity: frontend-snapshot/index.html missing (capture live webroot after selective-merge publish)"
  if [[ -L "$baseline_dir/tree/backend" ]]; then
    eternal_deploy_die "baseline integrity: tree/backend must be a real directory, not a symlink"
  fi

  # Runtime must be valid before claiming a rollback-capable baseline.
  eternal_validate_backend_runtime "$baseline_dir/tree/backend"

  local manifest="$baseline_dir/.baseline-integrity"
  local frontend_src="${ETERNAL_FRONTEND_ROOT:-/var/www/eternal-packaging}"
  if ! eternal_baseline_collect_manifest "$baseline_dir" "$manifest"; then
    eternal_deploy_die "baseline integrity: manifest collection failed (symlinks or find error)"
  fi
  [[ -s "$manifest" ]] || eternal_deploy_die "baseline integrity: empty manifest"
  local fe_count file_count req_rel req_hash
  fe_count="$(grep -c ' frontend-snapshot/' "$manifest" || true)"
  file_count="$(wc -l <"$manifest" | tr -d ' ')"
  [[ "${fe_count:-0}" -ge 1 ]] || eternal_deploy_die "baseline integrity: no frontend-snapshot files hashed"

  # Prefer stamp from prepare_backend_runtime; else resolve current policy (no invented history).
  if [[ -f "$baseline_dir/tree/backend/.eternal-requirements-file" \
     && -f "$baseline_dir/tree/backend/.eternal-requirements-sha256" ]]; then
    req_rel="$(tr -d '\n' <"$baseline_dir/tree/backend/.eternal-requirements-file")"
    req_hash="$(tr -d '\n' <"$baseline_dir/tree/backend/.eternal-requirements-sha256")"
    eternal_verify_requirements_provenance "$baseline_dir/tree/backend" "$req_rel" "$req_hash"
  else
    req_rel="$(eternal_resolve_requirements_relpath "$baseline_dir/tree/backend")"
    req_hash="$(eternal_requirements_sha256 "$baseline_dir/tree/backend")"
  fi

  local manifest_hash
  manifest_hash="$(sha256sum "$manifest" | awk '{print $1}')"
  {
    printf 'version=3\n'
    printf 'kind=verified-baseline\n'
    # Digests verified at write time; marker alone is never sufficient on read.
    printf 'immutable=1\n'
    printf 'content_digests_verified_at_write=1\n'
    printf 'backend_src=%s\n' "$backend_src"
    printf 'frontend_src=%s\n' "$frontend_src"
    printf 'systemd_wd_at_capture=%s\n' "$wd"
    printf 'systemd_exec_at_capture=%s\n' "$exe"
    printf 'integrity_manifest=.baseline-integrity\n'
    printf 'integrity_sha256=%s\n' "$manifest_hash"
    printf 'file_count=%s\n' "$file_count"
    printf 'frontend_file_count=%s\n' "$fe_count"
    printf 'requirements_file=%s\n' "$req_rel"
    printf 'requirements_sha256=%s\n' "$req_hash"
    printf 'runtime_python=%s\n' "$baseline_dir/tree/backend/.venv/bin/python"
    printf 'runtime_uvicorn=%s\n' "$baseline_dir/tree/backend/.venv/bin/uvicorn"
    printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'note=Capture live FE before retarget; digests+runtime+requirements provenance; baseline before activation\n'
  } >"$baseline_dir/.baseline-verified"
}

# Validate .baseline-verified provenance + full integrity manifest + runtime. Fail-closed.
eternal_baseline_verify_integrity() {
  local baseline_dir="$1"
  [[ -f "$baseline_dir/.baseline-verified" ]] || eternal_deploy_die "baseline missing .baseline-verified"
  [[ -f "$baseline_dir/.baseline-integrity" ]] || eternal_deploy_die "baseline missing .baseline-integrity provenance manifest"
  [[ -d "$baseline_dir/tree/backend" ]] || eternal_deploy_die "baseline missing frozen backend tree"
  [[ -d "$baseline_dir/frontend-snapshot" ]] || eternal_deploy_die "baseline missing frontend snapshot"
  [[ -f "$baseline_dir/frontend-snapshot/index.html" ]] \
    || eternal_deploy_die "baseline frontend-snapshot/index.html missing"
  if [[ -L "$baseline_dir/tree/backend" ]]; then
    eternal_deploy_die "baseline tree/backend is a symlink to mutable source; refuse"
  fi

  local expected_hash actual_hash kind immutable frontend_src digests_ok req_file req_hash
  expected_hash="$(sed -n 's/^integrity_sha256=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  [[ -n "$expected_hash" ]] || eternal_deploy_die "baseline-verified missing integrity_sha256"
  actual_hash="$(sha256sum "$baseline_dir/.baseline-integrity" | awk '{print $1}')"
  [[ "$expected_hash" == "$actual_hash" ]] || eternal_deploy_die "baseline integrity manifest hash mismatch (tampered provenance)"

  kind="$(sed -n 's/^kind=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  immutable="$(sed -n 's/^immutable=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  frontend_src="$(sed -n 's/^frontend_src=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  digests_ok="$(sed -n 's/^content_digests_verified_at_write=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  req_file="$(sed -n 's/^requirements_file=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  req_hash="$(sed -n 's/^requirements_sha256=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  [[ "$kind" == "verified-baseline" ]] || eternal_deploy_die "baseline-verified kind invalid"
  [[ "$immutable" == "1" ]] || eternal_deploy_die "baseline-verified not marked immutable"
  # Marker immutable=1 is insufficient without digest proof fields + live check.
  [[ "$digests_ok" == "1" ]] || eternal_deploy_die "baseline-verified missing content_digests_verified_at_write (marker-only refuse)"
  [[ -n "$frontend_src" ]] || eternal_deploy_die "baseline-verified missing frontend_src provenance"
  # Do not invent requirements provenance for legacy markers that omit it.
  eternal_verify_requirements_provenance "$baseline_dir/tree/backend" "$req_file" "$req_hash"

  if ! (cd "$baseline_dir" && sha256sum -c .baseline-integrity >/dev/null 2>&1); then
    eternal_deploy_die "baseline content digest mismatch (frozen tree altered)"
  fi
  if ! grep -q ' frontend-snapshot/' "$baseline_dir/.baseline-integrity"; then
    eternal_deploy_die "baseline integrity manifest lacks frontend-snapshot entries"
  fi
  # Re-count files on disk vs manifest (detect truncation / missing paths).
  local listed disk_count
  listed="$(wc -l <"$baseline_dir/.baseline-integrity" | tr -d ' ')"
  disk_count="$(
    cd "$baseline_dir" && find tree/backend frontend-snapshot \
      \( -path 'tree/backend/.venv' -o -path 'tree/backend/.venv/*' \
         -o -path 'tree/backend/.eternal-requirements-file' \
         -o -path 'tree/backend/.eternal-requirements-sha256' \
         -o -path '*/__pycache__' -o -path '*/__pycache__/*' \) -prune -o \
      -type f -print 2>/dev/null | wc -l | tr -d ' '
  )"
  [[ "$listed" == "$disk_count" ]] \
    || eternal_deploy_die "baseline integrity incomplete: manifest_files=$listed disk_files=$disk_count"

  eternal_validate_backend_runtime "$baseline_dir/tree/backend"
  eternal_deploy_log "baseline integrity OK files=$listed frontend_src=$frontend_src requirements_file=$req_file runtime=ok"
}

# Digest + runtime checks without requiring requirements_file (used by finalize before stamping).
eternal_baseline_verify_integrity_core() {
  local baseline_dir="$1"
  [[ -f "$baseline_dir/.baseline-verified" ]] || eternal_deploy_die "baseline missing .baseline-verified"
  [[ -f "$baseline_dir/.baseline-integrity" ]] || eternal_deploy_die "baseline missing .baseline-integrity provenance manifest"
  [[ -d "$baseline_dir/tree/backend" ]] || eternal_deploy_die "baseline missing frozen backend tree"
  [[ -d "$baseline_dir/frontend-snapshot" ]] || eternal_deploy_die "baseline missing frontend snapshot"
  [[ -f "$baseline_dir/frontend-snapshot/index.html" ]] \
    || eternal_deploy_die "baseline frontend-snapshot/index.html missing"
  if [[ -L "$baseline_dir/tree/backend" ]]; then
    eternal_deploy_die "baseline tree/backend is a symlink to mutable source; refuse"
  fi

  local expected_hash actual_hash kind immutable digests_ok
  expected_hash="$(sed -n 's/^integrity_sha256=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  [[ -n "$expected_hash" ]] || eternal_deploy_die "baseline-verified missing integrity_sha256"
  actual_hash="$(sha256sum "$baseline_dir/.baseline-integrity" | awk '{print $1}')"
  [[ "$expected_hash" == "$actual_hash" ]] || eternal_deploy_die "baseline integrity manifest hash mismatch (tampered provenance)"
  kind="$(sed -n 's/^kind=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  immutable="$(sed -n 's/^immutable=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  digests_ok="$(sed -n 's/^content_digests_verified_at_write=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  [[ "$kind" == "verified-baseline" ]] || eternal_deploy_die "baseline-verified kind invalid"
  [[ "$immutable" == "1" ]] || eternal_deploy_die "baseline-verified not marked immutable"
  [[ "$digests_ok" == "1" ]] || eternal_deploy_die "baseline-verified missing content_digests_verified_at_write"
  if ! (cd "$baseline_dir" && sha256sum -c .baseline-integrity >/dev/null 2>&1); then
    eternal_deploy_die "baseline content digest mismatch (frozen tree altered)"
  fi
  eternal_validate_backend_runtime "$baseline_dir/tree/backend"
}

# --- Post-success rollback identity (rollback-points/<sha40>/) ---
# previous_id is either a 40-hex SHA or the literal baseline-active (first cutover).

eternal_is_previous_id() {
  local value="${1:-}"
  [[ "$value" == "baseline-active" ]] && return 0
  eternal_is_sha40 "$value"
}

eternal_require_previous_id() {
  local value="${1:-}"
  eternal_is_previous_id "$value" \
    || eternal_deploy_die "previous release id must be 40 lowercase hex or baseline-active"
}

eternal_rollback_points_root() {
  local releases_root="${1:-}"
  [[ -n "$releases_root" ]] || eternal_deploy_die "rollback points root requires releases root"
  printf '%s/rollback-points\n' "$releases_root"
}

eternal_rollback_point_dir() {
  local releases_root="$1"
  local deployed_sha="$2"
  eternal_require_sha40 "$deployed_sha"
  printf '%s/%s\n' "$(eternal_rollback_points_root "$releases_root")" "$deployed_sha"
}

# Refuse symlink escape / arbitrary path for a rollback-point directory.
eternal_assert_safe_rollback_point_dir() {
  local point_dir="$1"
  local releases_root="$2"
  local root points resolved
  points="$(eternal_rollback_points_root "$releases_root")"
  [[ -d "$points" ]] || eternal_deploy_die "rollback-points root missing: $points"
  root="$(readlink -f "$points" 2>/dev/null || true)"
  [[ -n "$root" ]] || eternal_deploy_die "rollback-points root unreadable"
  resolved="$(readlink -f "$point_dir" 2>/dev/null || true)"
  [[ -n "$resolved" ]] || eternal_deploy_die "rollback point path unreadable: $point_dir"
  eternal_path_is_under_root "$root" "$resolved" \
    || eternal_deploy_die "rollback point escapes rollback-points root"
  # Point dir itself must not be a symlink.
  [[ ! -L "$point_dir" ]] || eternal_deploy_die "rollback point directory must not be a symlink"
}

eternal_meta_get() {
  local meta_file="$1"
  local key="$2"
  [[ -f "$meta_file" ]] || return 1
  sed -n "s/^${key}=//p" "$meta_file" | head -n1
}

# Write identity-scoped FE backup + meta after DEPLOY_OK.
# fe_src may be empty when frontend was not published in this deploy.
eternal_write_rollback_point() {
  local releases_root="$1"
  local deployed_sha="$2"
  local previous_id="$3"
  local previous_path="$4"
  local previous_is_baseline="$5"
  local current_switched="$6"
  local frontend_published="$7"
  local fe_src="${8:-}"
  local retention="${ETERNAL_ROLLBACK_POINT_RETENTION:-5}"
  local point_dir meta fe_dst parent

  eternal_require_sha40 "$deployed_sha"
  eternal_require_previous_id "$previous_id"
  [[ "$previous_is_baseline" == "0" || "$previous_is_baseline" == "1" ]] \
    || eternal_deploy_die "previous_is_baseline must be 0|1"
  [[ "$current_switched" == "0" || "$current_switched" == "1" ]] \
    || eternal_deploy_die "current_switched must be 0|1"
  [[ "$frontend_published" == "0" || "$frontend_published" == "1" ]] \
    || eternal_deploy_die "frontend_published must be 0|1"
  [[ -n "$previous_path" ]] || eternal_deploy_die "previous_path required for rollback point"

  parent="$(eternal_rollback_points_root "$releases_root")"
  mkdir -p "$parent"
  point_dir="$(eternal_rollback_point_dir "$releases_root" "$deployed_sha")"
  # Replace any prior point for this SHA (redeploy of same SHA).
  rm -rf "$point_dir"
  mkdir -p "$point_dir"
  eternal_assert_safe_rollback_point_dir "$point_dir" "$releases_root"

  meta="$point_dir/meta.txt"
  {
    printf 'deployed_sha=%s\n' "$deployed_sha"
    printf 'previous_id=%s\n' "$previous_id"
    printf 'previous_path=%s\n' "$previous_path"
    printf 'previous_is_baseline=%s\n' "$previous_is_baseline"
    printf 'current_switched=%s\n' "$current_switched"
    printf 'frontend_published=%s\n' "$frontend_published"
    printf 'created_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$meta"

  if [[ "$frontend_published" == "1" ]]; then
    [[ -n "$fe_src" && -d "$fe_src" ]] \
      || eternal_deploy_die "frontend backup source missing for rollback point"
    [[ -f "$fe_src/index.html" ]] \
      || eternal_deploy_die "frontend backup source missing index.html"
    [[ ! -L "$fe_src" ]] || eternal_deploy_die "frontend backup source must not be a symlink"
    fe_dst="$point_dir/frontend"
    mkdir -p "$fe_dst"
    cp -a "$fe_src"/. "$fe_dst"/
    [[ -f "$fe_dst/index.html" ]] || eternal_deploy_die "rollback point frontend missing index.html after copy"
    printf 'frontend_backup_ok=1\n' >>"$meta"
  else
    printf 'frontend_backup_ok=0\n' >>"$meta"
  fi

  eternal_write_active_release_marker "$releases_root" "$deployed_sha" "$previous_id"
  eternal_prune_rollback_points "$releases_root" "$retention" "$deployed_sha"
  eternal_deploy_log "rollback point recorded sha=$deployed_sha previous=$previous_id"
}

eternal_write_active_release_marker() {
  local releases_root="$1"
  local sha="$2"
  local previous_id="$3"
  local marker="$releases_root/active-release.txt"
  mkdir -p "$releases_root"
  {
    printf 'sha=%s\n' "$sha"
    printf 'previous_id=%s\n' "$previous_id"
    printf 'updated_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$marker"
}

# Bookkeeping for docs/tests-only SHAs: processed Git identity ≠ application runtime identity.
# Does not update active-release.txt and does not create .deploy-ok application markers.
eternal_write_processed_git_sha_marker() {
  local releases_root="$1"
  local sha="$2"
  local prev_sha="$3"
  local marker="$releases_root/last-processed-git-sha.txt"
  eternal_require_sha40 "$sha"
  mkdir -p "$releases_root"
  {
    printf 'sha=%s\n' "$sha"
    printf 'kind=no_application_change\n'
    printf 'previous_classify_sha=%s\n' "$prev_sha"
    printf 'active_release_unchanged=1\n'
    printf 'updated_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$marker"
}

eternal_read_active_release_sha() {
  local releases_root="$1"
  local marker="$releases_root/active-release.txt"
  [[ -f "$marker" ]] || return 1
  eternal_meta_get "$marker" "sha"
}

# Keep newest N points; never delete the point for keep_sha (active deploy identity).
eternal_prune_rollback_points() {
  local releases_root="$1"
  local retention="$2"
  local keep_sha="$3"
  local parent entry meta created
  local -a entries=()
  parent="$(eternal_rollback_points_root "$releases_root")"
  [[ -d "$parent" ]] || return 0
  [[ "$retention" =~ ^[1-9][0-9]*$ ]] || retention=5

  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    meta="$entry/meta.txt"
    [[ -f "$meta" ]] || continue
    created="$(eternal_meta_get "$meta" "created_at")"
    [[ -n "$created" ]] || created="1970-01-01T00:00:00Z"
    entries+=("$created|$entry")
  done < <(find "$parent" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort)

  if ((${#entries[@]} <= retention)); then
    return 0
  fi
  # Newest first.
  local sorted
  mapfile -t sorted < <(printf '%s\n' "${entries[@]}" | sort -r)
  local i=0
  local path base
  for item in "${sorted[@]}"; do
    i=$((i + 1))
    path="${item#*|}"
    base="$(basename "$path")"
    if [[ "$base" == "$keep_sha" ]]; then
      continue
    fi
    if (( i > retention )); then
      eternal_deploy_log "prune rollback point (retention=$retention): $base"
      rm -rf "$path"
    fi
  done
}

# Validate rollback-point meta + optional FE backup. Prints nothing; returns 0/1 or dies on hard errors.
eternal_validate_rollback_point() {
  local releases_root="$1"
  local deployed_sha="$2"
  local expected_previous_id="$3"
  local point_dir meta prev_id fe_ok published fe_dir

  eternal_require_sha40 "$deployed_sha"
  eternal_require_previous_id "$expected_previous_id"
  point_dir="$(eternal_rollback_point_dir "$releases_root" "$deployed_sha")"
  [[ -d "$point_dir" ]] || eternal_deploy_die "rollback point missing for sha=$deployed_sha"
  eternal_assert_safe_rollback_point_dir "$point_dir" "$releases_root"
  meta="$point_dir/meta.txt"
  [[ -f "$meta" && ! -L "$meta" ]] || eternal_deploy_die "rollback point meta missing or symlink"
  prev_id="$(eternal_meta_get "$meta" "previous_id")"
  [[ "$prev_id" == "$expected_previous_id" ]] \
    || eternal_deploy_die "previous release mismatch: requested=$expected_previous_id recorded=$prev_id"
  [[ "$(eternal_meta_get "$meta" "deployed_sha")" == "$deployed_sha" ]] \
    || eternal_deploy_die "rollback point deployed_sha mismatch"
  published="$(eternal_meta_get "$meta" "frontend_published")"
  fe_ok="$(eternal_meta_get "$meta" "frontend_backup_ok")"
  if [[ "$published" == "1" ]]; then
    [[ "$fe_ok" == "1" ]] || eternal_deploy_die "rollback point frontend backup not marked ok"
    fe_dir="$point_dir/frontend"
    [[ -d "$fe_dir" && ! -L "$fe_dir" ]] || eternal_deploy_die "rollback point frontend backup missing or symlink"
    [[ -f "$fe_dir/index.html" ]] || eternal_deploy_die "rollback point frontend backup corrupt (no index.html)"
    eternal_path_is_under_root "$point_dir" "$(readlink -f "$fe_dir")" \
      || eternal_deploy_die "rollback point frontend escapes point dir"
  fi
  return 0
}
