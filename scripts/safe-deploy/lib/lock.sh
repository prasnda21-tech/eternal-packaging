# shellcheck shell=bash
# Single-deployer lock with stale detection.

# Lock file format (plain text, no secrets):
#   owner=<user@host>
#   pid=<pid>
#   started_utc=<iso>
#   target_sha=<sha or empty>
#   hostname=<host>

_lock_is_process_alive() {
  local pid="$1"
  [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

_lock_read_field() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  awk -F= -v k="$key" '$1==k {print substr($0, index($0,$2)); exit}' "$file"
}

_lock_age_seconds() {
  local file="$1"
  [[ -f "$file" ]] || { echo 0; return; }
  local now mtime
  now="$(date +%s)"
  mtime="$(stat -c %Y "$file" 2>/dev/null || echo "$now")"
  echo $(( now - mtime ))
}

acquire_deploy_lock() {
  local lock="${DEPLOY_LOCK_FILE:-/var/lock/eternal-packaging-deploy.lock}"
  local stale_secs="${LOCK_STALE_SECONDS:-7200}"
  local target_sha="${1:-}"
  local dir owner pid age

  dir="$(dirname "$lock")"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    mkdir -p "${SAFE_DEPLOY_TEST_ROOT:-/tmp}/locks" 2>/dev/null || true
  else
    mkdir -p "$dir" 2>/dev/null || die "cannot create lock directory: $dir"
  fi

  if [[ -e "$lock" ]]; then
    # Empty / unreadable lock → treat as stale reclaim candidate
    if [[ ! -s "$lock" ]]; then
      warn "empty deploy lock found; reclaiming: $lock"
      rm -f "$lock" || die "cannot remove empty lock"
    else
      owner="$(_lock_read_field "$lock" owner || true)"
      pid="$(_lock_read_field "$lock" pid || true)"
      age="$(_lock_age_seconds "$lock")"
      if _lock_is_process_alive "$pid"; then
        die "deploy lock held by active process pid=$pid owner=${owner:-unknown} file=$lock"
      fi
      if [[ "$age" -lt "$stale_secs" ]]; then
        # Process dead but lock young — still reclaim with warning (crash mid-deploy)
        warn "deploy lock pid=$pid not running (age=${age}s); reclaiming stale lock"
      else
        warn "stale deploy lock age=${age}s (>${stale_secs}s); reclaiming"
      fi
      rm -f "$lock" || die "cannot remove stale lock"
    fi
  fi

  # Atomic create: fail if raced
  if ! (
    set -o noclobber
    {
      echo "owner=$(id -un)@$(hostname -s 2>/dev/null || hostname)"
      echo "pid=$$"
      echo "started_utc=$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
      echo "target_sha=${target_sha}"
      echo "hostname=$(hostname)"
    } > "$lock"
  ) 2>/dev/null; then
    die "failed to acquire deploy lock (race): $lock"
  fi
  info "acquired deploy lock: $lock"
  export SAFE_DEPLOY_LOCK_HELD=1
  export SAFE_DEPLOY_LOCK_PATH="$lock"
}

release_deploy_lock() {
  local lock="${SAFE_DEPLOY_LOCK_PATH:-${DEPLOY_LOCK_FILE:-/var/lock/eternal-packaging-deploy.lock}}"
  if [[ "${SAFE_DEPLOY_LOCK_HELD:-0}" != "1" ]]; then
    return 0
  fi
  if [[ -f "$lock" ]]; then
    local pid
    pid="$(_lock_read_field "$lock" pid || true)"
    if [[ "$pid" == "$$" || -z "$pid" ]]; then
      rm -f "$lock" || warn "failed to remove lock $lock"
      info "released deploy lock: $lock"
    else
      warn "not releasing lock owned by pid=$pid (ours=$$)"
    fi
  fi
  SAFE_DEPLOY_LOCK_HELD=0
}

inspect_deploy_lock() {
  local lock="${DEPLOY_LOCK_FILE:-/var/lock/eternal-packaging-deploy.lock}"
  if [[ ! -e "$lock" ]]; then
    echo "LOCK_STATUS=absent"
    return 0
  fi
  if [[ ! -s "$lock" ]]; then
    echo "LOCK_STATUS=empty_stale"
    return 0
  fi
  local pid age
  pid="$(_lock_read_field "$lock" pid || true)"
  age="$(_lock_age_seconds "$lock")"
  if _lock_is_process_alive "$pid"; then
    echo "LOCK_STATUS=active"
  elif [[ "$age" -ge "${LOCK_STALE_SECONDS:-7200}" ]]; then
    echo "LOCK_STATUS=stale"
  else
    echo "LOCK_STATUS=orphaned"
  fi
  echo "LOCK_PID=${pid:-}"
  echo "LOCK_AGE_SECONDS=${age}"
  echo "LOCK_OWNER=$(_lock_read_field "$lock" owner || true)"
  echo "LOCK_TARGET=$(_lock_read_field "$lock" target_sha || true)"
}
