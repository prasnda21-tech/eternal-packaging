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

# True for static/**/*.js.map only. Does NOT match .css.map or other artifacts.
eternal_fe_is_js_sourcemap_relpath() {
  local rel="${1#./}"
  eternal_fe_is_hashed_relpath "$rel" || return 1
  [[ "$rel" == *.js.map ]]
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
# Includes in-tree config and, when set, the external ETERNAL_FRONTEND_ENV_FILE content hash
# so reuse cannot skip rebuild after effective CRA/env inputs change.
eternal_frontend_input_fingerprint() {
  local frontend_dir="$1"
  local pkg="$frontend_dir/package.json"
  local lock=""
  if [[ -f "$frontend_dir/yarn.lock" ]]; then
    lock="$frontend_dir/yarn.lock"
  elif [[ -f "$frontend_dir/../yarn.lock" ]]; then
    lock="$frontend_dir/../yarn.lock"
  fi
  [[ -f "$pkg" ]] || eternal_deploy_die "frontend fingerprint: package.json missing"
  [[ -n "$lock" && -f "$lock" ]] || eternal_deploy_die "frontend fingerprint: yarn.lock missing"
  if [[ -n "${ETERNAL_FRONTEND_ENV_FILE:-}" ]]; then
    [[ -f "$ETERNAL_FRONTEND_ENV_FILE" ]] \
      || eternal_deploy_die "frontend fingerprint: ETERNAL_FRONTEND_ENV_FILE missing ($ETERNAL_FRONTEND_ENV_FILE)"
  fi
  {
    sha256sum "$pkg" "$lock"
    if [[ -f "$frontend_dir/craco.config.js" ]]; then
      sha256sum "$frontend_dir/craco.config.js"
    fi
    if [[ -f "$frontend_dir/config-overrides.js" ]]; then
      sha256sum "$frontend_dir/config-overrides.js"
    fi
    if [[ -f "$frontend_dir/.env.production" ]]; then
      sha256sum "$frontend_dir/.env.production"
    fi
    if [[ -f "$frontend_dir/.env" ]]; then
      sha256sum "$frontend_dir/.env"
    fi
    if [[ -n "${ETERNAL_FRONTEND_ENV_FILE:-}" && -f "$ETERNAL_FRONTEND_ENV_FILE" ]]; then
      # Hash only — never print file contents (may contain non-secret CRA public vars).
      sha256sum "$ETERNAL_FRONTEND_ENV_FILE"
    fi
  } | sha256sum | awk '{print $1}'
}

eternal_write_frontend_fingerprint() {
  local release_dir="$1"
  local fp
  fp="$(eternal_frontend_input_fingerprint "$release_dir/frontend")"
  printf '%s\n' "$fp" >"$release_dir/.eternal-frontend-fingerprint"
  eternal_deploy_log "frontend build fingerprint recorded"
}

# Merge one file into webroot with skip-identical / hashed fail-closed / mutable overwrite.
# Returns 0 on success; dies on policy violation (publish) or returns 1 when soft=1.
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

  if [[ -e "$dest" || -L "$dest" ]]; then
    if [[ -L "$dest" ]]; then
      eternal_deploy_log "ERROR: webroot install refuses symlink destination $rel"
      [[ "$soft" == "1" ]] && return 1
      eternal_deploy_die "webroot install: symlink destination refused ($rel)"
    fi
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
      eternal_deploy_log "SKIP_IDENTICAL $rel"
      return 0
    fi
    if eternal_fe_is_hashed_relpath "$rel"; then
      # .js.map only: when the paired .js is present on both sides and byte-identical,
      # keep the live map (no overwrite). Does not claim debug equivalence to the new
      # build sources — only that the executable .js payload matches. .css.map and
      # other hashed assets remain fail-closed.
      if eternal_fe_is_js_sourcemap_relpath "$rel"; then
        local js_src="${src%.map}"
        local js_dest="${dest%.map}"
        if [[ -f "$js_src" && -f "$js_dest" && ! -L "$js_src" && ! -L "$js_dest" ]] \
          && cmp -s "$js_src" "$js_dest"; then
          eternal_deploy_log "SKIP_SOURCEMAP_COLLISION $rel (paired .js byte-identical; keep live map)"
          return 0
        fi
        eternal_deploy_log "ERROR: js sourcemap collision without identical paired .js path=$rel — refuse overwrite"
        [[ "$soft" == "1" ]] && return 1
        eternal_deploy_die "js sourcemap collision without identical paired .js: $rel"
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
  [[ -f "$release_dir/frontend/build/index.html" ]] || {
    eternal_deploy_log "reuse refused: frontend/build/index.html missing"
    return 1
  }
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
  # Deduplicate refs (index attrs + manifest may overlap).
  while IFS= read -r ref; do
    [[ -z "$ref" ]] && continue
    [[ -f "$release_dir/frontend/build/$ref" ]] || {
      eternal_deploy_log "reuse refused: build asset missing $ref"
      return 1
    }
  done < <(eternal_fe_list_required_static_assets \
    "$release_dir/frontend/build/index.html" \
    "$([[ -f "$manifest" ]] && printf '%s' "$manifest")" | awk 'NF && !seen[$0]++')

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
