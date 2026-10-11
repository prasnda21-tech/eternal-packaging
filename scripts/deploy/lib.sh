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
  local release_dir="$2"
  if [[ -e "$frontend_root/.deploy-in-progress" ]]; then
    eternal_deploy_die "local conflict: frontend deploy-in-progress marker present ($frontend_root/.deploy-in-progress)"
  fi
  if [[ -d "$release_dir" && ! -f "$release_dir/.deploy-ok" && ! -f "$release_dir/.baseline" ]]; then
    eternal_deploy_die "local conflict: incomplete release directory exists ($release_dir)"
  fi
}

eternal_manifest_guard() {
  local release_dir="$1"
  if [[ -d "$release_dir/backend" ]]; then
    [[ -f "$release_dir/backend/requirements.txt" ]] \
      || eternal_deploy_die "manifest guard: backend/requirements.txt missing"
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
         -o -path '*/__pycache__' -o -path '*/__pycache__/*' \) -prune -o \
      -type f -print0 2>/dev/null \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum
  ) >"$manifest"
}

# Fresh venv in target backend (never copy live venv; never mutate active service venv).
# ETERNAL_RUNTIME_MODE=stub|full (default full). Stub is for synthetic tests only.
eternal_prepare_backend_runtime() {
  local backend_dir="$1"
  local mode="${ETERNAL_RUNTIME_MODE:-full}"
  local py="${ETERNAL_PYTHON_BIN:-python3}"
  [[ -d "$backend_dir" ]] || eternal_deploy_die "runtime prep: backend dir missing: $backend_dir"
  [[ -f "$backend_dir/requirements.txt" ]] || eternal_deploy_die "runtime prep: requirements.txt missing"
  command -v "$py" >/dev/null 2>&1 || eternal_deploy_die "runtime prep: python missing: $py"

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
  else
    "$py" -m venv "$backend_dir/.venv" || eternal_deploy_die "runtime prep: venv create failed"
    # shellcheck disable=SC1091
    "$backend_dir/.venv/bin/pip" install --upgrade pip wheel >/dev/null \
      || eternal_deploy_die "runtime prep: pip bootstrap failed"
    "$backend_dir/.venv/bin/pip" install -r "$backend_dir/requirements.txt" \
      || eternal_deploy_die "runtime prep: pip install requirements failed"
  fi
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
  local fe_count file_count req_hash
  fe_count="$(grep -c ' frontend-snapshot/' "$manifest" || true)"
  file_count="$(wc -l <"$manifest" | tr -d ' ')"
  [[ "${fe_count:-0}" -ge 1 ]] || eternal_deploy_die "baseline integrity: no frontend-snapshot files hashed"
  req_hash="$(sha256sum "$baseline_dir/tree/backend/requirements.txt" | awk '{print $1}')"

  local manifest_hash
  manifest_hash="$(sha256sum "$manifest" | awk '{print $1}')"
  {
    printf 'version=2\n'
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
    printf 'requirements_sha256=%s\n' "$req_hash"
    printf 'runtime_python=%s\n' "$baseline_dir/tree/backend/.venv/bin/python"
    printf 'runtime_uvicorn=%s\n' "$baseline_dir/tree/backend/.venv/bin/uvicorn"
    printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'note=Capture live FE before retarget; digests+runtime validated; baseline before activation\n'
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

  local expected_hash actual_hash kind immutable frontend_src digests_ok
  expected_hash="$(sed -n 's/^integrity_sha256=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  [[ -n "$expected_hash" ]] || eternal_deploy_die "baseline-verified missing integrity_sha256"
  actual_hash="$(sha256sum "$baseline_dir/.baseline-integrity" | awk '{print $1}')"
  [[ "$expected_hash" == "$actual_hash" ]] || eternal_deploy_die "baseline integrity manifest hash mismatch (tampered provenance)"

  kind="$(sed -n 's/^kind=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  immutable="$(sed -n 's/^immutable=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  frontend_src="$(sed -n 's/^frontend_src=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  digests_ok="$(sed -n 's/^content_digests_verified_at_write=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  [[ "$kind" == "verified-baseline" ]] || eternal_deploy_die "baseline-verified kind invalid"
  [[ "$immutable" == "1" ]] || eternal_deploy_die "baseline-verified not marked immutable"
  # Marker immutable=1 is insufficient without digest proof fields + live check.
  [[ "$digests_ok" == "1" ]] || eternal_deploy_die "baseline-verified missing content_digests_verified_at_write (marker-only refuse)"
  [[ -n "$frontend_src" ]] || eternal_deploy_die "baseline-verified missing frontend_src provenance"

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
         -o -path '*/__pycache__' -o -path '*/__pycache__/*' \) -prune -o \
      -type f -print 2>/dev/null | wc -l | tr -d ' '
  )"
  [[ "$listed" == "$disk_count" ]] \
    || eternal_deploy_die "baseline integrity incomplete: manifest_files=$listed disk_files=$disk_count"

  eternal_validate_backend_runtime "$baseline_dir/tree/backend"
  eternal_deploy_log "baseline integrity OK files=$listed frontend_src=$frontend_src runtime=ok"
}
