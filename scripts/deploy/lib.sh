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

eternal_acquire_lock() {
  local lock_path="$1"
  local timeout_s="${2:-0}"
  local waited=0
  mkdir -p "$(dirname "$lock_path")"
  while true; do
    if (set -o noclobber; printf '%s\n' "$$" >"$lock_path") 2>/dev/null; then
      return 0
    fi
    if (( timeout_s <= 0 || waited >= timeout_s )); then
      eternal_deploy_die "deploy lock held: $lock_path"
    fi
    sleep 1
    waited=$((waited + 1))
  done
}

eternal_release_lock() {
  local lock_path="$1"
  if [[ -f "$lock_path" ]]; then
    local owner
    owner="$(cat "$lock_path" 2>/dev/null || true)"
    if [[ "$owner" == "$$" ]]; then
      rm -f "$lock_path"
    fi
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

# Write provenance + integrity manifest for a verified baseline directory.
# Stores relative sha256 lines in .baseline-integrity and metadata in .baseline-verified.
eternal_baseline_write_integrity() {
  local baseline_dir="$1"
  local backend_src="${2:-}"
  local wd="${3:-}"
  local exe="${4:-}"
  [[ -d "$baseline_dir/tree/backend" ]] || eternal_deploy_die "baseline integrity: frozen backend missing"
  [[ -d "$baseline_dir/frontend-snapshot" ]] || eternal_deploy_die "baseline integrity: frontend snapshot missing"
  if [[ -L "$baseline_dir/tree/backend" ]]; then
    eternal_deploy_die "baseline integrity: tree/backend must be a real directory, not a symlink"
  fi

  local manifest="$baseline_dir/.baseline-integrity"
  (
    cd "$baseline_dir"
    # Stable, bounded file set for provenance (not a full recursive tree walk of huge trees).
    find tree/backend frontend-snapshot -type f 2>/dev/null \
      | LC_ALL=C sort \
      | head -n 5000 \
      | xargs -r sha256sum
  ) >"$manifest"
  [[ -s "$manifest" ]] || eternal_deploy_die "baseline integrity: empty manifest"

  local manifest_hash
  manifest_hash="$(sha256sum "$manifest" | awk '{print $1}')"
  {
    printf 'version=1\n'
    printf 'kind=verified-baseline\n'
    printf 'immutable=1\n'
    printf 'backend_src=%s\n' "$backend_src"
    printf 'systemd_wd_at_capture=%s\n' "$wd"
    printf 'systemd_exec_at_capture=%s\n' "$exe"
    printf 'integrity_manifest=.baseline-integrity\n'
    printf 'integrity_sha256=%s\n' "$manifest_hash"
    printf 'file_count=%s\n' "$(wc -l <"$manifest" | tr -d ' ')"
    printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'note=Baseline must exist before systemd retarget/activation\n'
  } >"$baseline_dir/.baseline-verified"
}

# Validate .baseline-verified provenance + integrity manifest. Fail-closed.
eternal_baseline_verify_integrity() {
  local baseline_dir="$1"
  [[ -f "$baseline_dir/.baseline-verified" ]] || eternal_deploy_die "baseline missing .baseline-verified"
  [[ -f "$baseline_dir/.baseline-integrity" ]] || eternal_deploy_die "baseline missing .baseline-integrity provenance manifest"
  [[ -d "$baseline_dir/tree/backend" ]] || eternal_deploy_die "baseline missing frozen backend tree"
  [[ -d "$baseline_dir/frontend-snapshot" ]] || eternal_deploy_die "baseline missing frontend snapshot"
  if [[ -L "$baseline_dir/tree/backend" ]]; then
    eternal_deploy_die "baseline tree/backend is a symlink to mutable source; refuse"
  fi

  local expected_hash actual_hash kind immutable
  expected_hash="$(sed -n 's/^integrity_sha256=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  [[ -n "$expected_hash" ]] || eternal_deploy_die "baseline-verified missing integrity_sha256"
  actual_hash="$(sha256sum "$baseline_dir/.baseline-integrity" | awk '{print $1}')"
  [[ "$expected_hash" == "$actual_hash" ]] || eternal_deploy_die "baseline integrity manifest hash mismatch (tampered provenance)"

  kind="$(sed -n 's/^kind=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  immutable="$(sed -n 's/^immutable=//p' "$baseline_dir/.baseline-verified" | head -n1)"
  [[ "$kind" == "verified-baseline" ]] || eternal_deploy_die "baseline-verified kind invalid"
  [[ "$immutable" == "1" ]] || eternal_deploy_die "baseline-verified not marked immutable"

  # Verify file digests (fail if any listed file changed/missing).
  if ! (cd "$baseline_dir" && sha256sum -c .baseline-integrity >/dev/null 2>&1); then
    eternal_deploy_die "baseline content digest mismatch (frozen tree altered)"
  fi
  eternal_deploy_log "baseline integrity OK files=$(wc -l <"$baseline_dir/.baseline-integrity" | tr -d ' ')"
}
