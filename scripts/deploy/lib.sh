#!/usr/bin/env bash
# Shared helpers for Eternal Packaging deploy gate/runner.
# Fail-closed. No secrets printed. No sudo assumed.

set -euo pipefail

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
  # Flatten newlines so PEM blocks cannot bypass single-line sed.
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
    -e 's/(SECRET|TOKEN|PASSWORD|PRIVATE_KEY)([^=]*[=:][[:space:]]*)[^[:space:]]+/\1\2[REDACTED]/g')"
  printf '%s\n' "$line"
}

eternal_safe_echo() {
  eternal_redact_line "$*" >&2
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
  # stdin: paths one per line
  # exports: ETERNAL_NEED_BACKEND=0|1 ETERNAL_NEED_FRONTEND=0|1
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
  if [[ -d "$release_dir" && ! -f "$release_dir/.deploy-ok" ]]; then
    eternal_deploy_die "local conflict: incomplete release directory exists ($release_dir)"
  fi
}

eternal_manifest_guard() {
  local release_dir="$1"
  # Require dependency manifests when their trees exist; refuse obvious corruption.
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
