# shellcheck shell=bash
# Production frontend API target guard.
# Fails closed when a production build would embed a loopback backend URL.

if ! declare -F info >/dev/null 2>&1; then
  info() { printf '[INFO] %s\n' "$*" >&2; }
fi
if ! declare -F die >/dev/null 2>&1; then
  die() { printf '[ABORT] %s\n' "$*" >&2; exit 1; }
fi

frontend_api_hostname() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlparse
raw = sys.argv[1].strip()
if not raw:
    print("")
    raise SystemExit(0)
if "://" not in raw:
    raw = "http://" + raw
print((urlparse(raw).hostname or "").lower())
PY
}

frontend_api_is_loopback() {
  local host
  host="$(frontend_api_hostname "$1")"
  case "$host" in
    localhost|127.0.0.1|0.0.0.0) return 0 ;;
    *) return 1 ;;
  esac
}

# Resolve the URL that the frontend build must embed.
# Production mode refuses loopback. Local/dev mode allows it.
# Prints the resolved URL. Dies on a forbidden production target.
resolve_frontend_api_target() {
  local mode="${SAFE_DEPLOY_MODE:-production}"
  local explicit="${PRODUCTION_FRONTEND_API_TARGET:-}"
  local inherited="${REACT_APP_BACKEND_URL:-}"
  local chosen=""

  if [[ "$mode" == "local" || "$mode" == "dev" ]]; then
    chosen="${explicit:-$inherited}"
    if [[ -z "$chosen" ]]; then
      chosen="http://localhost:8000"
    fi
    info "FRONTEND API TARGET: ${chosen}"
    printf '%s\n' "$chosen"
    return 0
  fi

  if [[ -n "$explicit" ]]; then
    chosen="$explicit"
  elif [[ -n "$inherited" ]] && ! frontend_api_is_loopback "$inherited"; then
    chosen="$inherited"
  elif [[ -n "$inherited" ]] && frontend_api_is_loopback "$inherited"; then
    die "production frontend build refused inherited loopback REACT_APP_BACKEND_URL=${inherited}. Set PRODUCTION_FRONTEND_API_TARGET to the production origin."
  else
    chosen="${PRODUCTION_FRONTEND_API_ORIGIN:-https://produksi.eternalpackaging.id}"
  fi

  if frontend_api_is_loopback "$chosen" || [[ -z "$(frontend_api_hostname "$chosen")" ]]; then
    die "production frontend build refused API target: ${chosen}"
  fi
  info "FRONTEND API TARGET: ${chosen}"
  printf '%s\n' "$chosen"
}

export_frontend_api_target() {
  local target
  target="$(resolve_frontend_api_target)"
  export REACT_APP_BACKEND_URL="$target"
  export RESOLVED_FRONTEND_API_TARGET="$target"
}

# Fail if a built bundle embeds a loopback API origin.
reject_loopback_frontend_artifact() {
  local dir="$1"
  [[ -d "$dir" ]] || die "frontend artifact missing: $dir"
  python3 - "$dir" <<'PY'
import os, sys
root = sys.argv[1]
needles = (
    "http://localhost:8000",
    "https://localhost:8000",
    "http://127.0.0.1:8000",
    "https://127.0.0.1:8000",
    "http://0.0.0.0:8000",
    "https://0.0.0.0:8000",
)
bad = []
for dirpath, _dirs, files in os.walk(root):
    for name in files:
        if not name.endswith((".js", ".html", ".css")):
            continue
        path = os.path.join(dirpath, name)
        try:
            data = open(path, "r", errors="ignore").read()
        except OSError:
            continue
        if any(n in data for n in needles):
            bad.append(path)
if bad:
    print("loopback API URL baked into frontend artifact:", file=sys.stderr)
    for path in bad[:10]:
        print(path, file=sys.stderr)
    raise SystemExit(1)
print("frontend artifact has no loopback API URL")
PY
}
