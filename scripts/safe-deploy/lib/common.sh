# shellcheck shell=bash
# Common helpers for safe-deploy.

safe_deploy_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  echo "$here"
}

phase() { printf '\n======== %s ========\n' "$1" >&2; }

info() { printf '[INFO] %s\n' "$*" >&2; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; return 1; }
die()  { printf '[ABORT] %s\n' "$*" >&2; exit 1; }

require_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "required command missing: $c"
  done
}

# Run a command with a hard wall-clock timeout.
# Usage: run_timeout <seconds> <command...>
run_timeout() {
  local secs="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout --foreground --signal=TERM --kill-after=10 "${secs}s" "$@"
  else
    warn "GNU timeout unavailable; running without wall-clock limit"
    "$@"
  fi
}

iso_now() {
  date -u +'%Y-%m-%dT%H:%M:%SZ'
}

nproc_safe() {
  nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1
}

json_get() {
  # json_get <file> <python_expr_on_obj_as_d>
  local file="$1"
  local expr="$2"
  python3 - "$file" "$expr" <<'PY'
import json, sys
path, expr = sys.argv[1], sys.argv[2]
with open(path) as f:
    d = json.load(f)
print(eval(expr, {"d": d}))
PY
}

record_deploy_event() {
  local history_dir="${DEPLOY_HISTORY_DIR:-/var/log/eternal-packaging-deploy}"
  local stamp result prev target rollback frontend_path health
  stamp="$(date +'%Y%m%d-%H%M%S')"
  result="${1:-unknown}"
  prev="${2:-}"
  target="${3:-}"
  rollback="${4:-}"
  frontend_path="${5:-}"
  health="${6:-}"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    info "DRY_RUN skip deploy history write ($result)"
    return 0
  fi
  mkdir -p "$history_dir" 2>/dev/null || {
    warn "cannot create deploy history dir: $history_dir"
    return 0
  }
  local out="${history_dir}/deploy-${stamp}-${result}.json"
  python3 - "$out" "$result" "$prev" "$target" "$rollback" "$frontend_path" "$health" <<'PY'
import json, sys, os
from datetime import datetime, timezone
out, result, prev, target, rollback, frontend, health = sys.argv[1:8]
payload = {
    "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "result": result,
    "previous_sha": prev,
    "target_sha": target,
    "rollback_sha": rollback,
    "frontend_release_path": frontend,
    "backend_health": health,
    "host": os.uname().nodename,
    "pid": os.getpid(),
}
with open(out, "w") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
print(out)
PY
}
