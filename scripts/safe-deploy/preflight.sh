# shellcheck shell=bash
# Host-local preflight (read-only checks + resource guard + lock inspect).
# Does NOT mutate production. Does NOT restart services.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "${SCRIPT_DIR}/config.sh"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/lock.sh
source "${SCRIPT_DIR}/lib/lock.sh"
# shellcheck source=lib/resource_guard.sh
source "${SCRIPT_DIR}/lib/resource_guard.sh"

phase "PREFLIGHT"

require_cmd git python3 curl systemctl

APP_ROOT="${APP_ROOT}"
cd "$APP_ROOT"

LIVE_SHA="$(git rev-parse HEAD)"
info "production HEAD: $LIVE_SHA"

if [[ "${ALLOW_DIRTY_WORKTREE:-0}" != "1" ]]; then
  if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    die "dirty worktree — abort (set ALLOW_DIRTY_WORKTREE=1 only with explicit approval)"
  fi
fi
info "worktree: clean"

resource_guard_check

# Service state (read-only)
for svc in "$BACKEND_SERVICE" "$NGINX_SERVICE" mongod; do
  state="$(systemctl is-active "$svc" 2>/dev/null || echo unknown)"
  info "service ${svc}: ${state}"
  [[ "$state" == "active" ]] || die "service not active: $svc"
done

# Health (bounded)
health_body="$(run_timeout "$HEALTH_TIMEOUT" curl -fsS --max-time "$HEALTH_TIMEOUT" "$HEALTH_URL" || true)"
if [[ -z "$health_body" ]]; then
  die "backend health unreachable: $HEALTH_URL"
fi
python3 - "$health_body" <<'PY' || die "backend health payload unexpected"
import json, sys
d = json.loads(sys.argv[1])
assert d.get("mongo") is True, d
assert d.get("mode") == "operational", d
print("backend health: mongo=true mode=operational")
PY

# Nginx / frontend smoke (local)
code="$(run_timeout 15 curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$FRONTEND_HEALTH_URL" || echo 000)"
info "frontend local /login HTTP $code"
[[ "$code" == "200" || "$code" == "301" || "$code" == "302" ]] || warn "frontend local check returned $code"

inspect_deploy_lock

# Lightweight data preflight (optional if Mongo env present)
if [[ "${SKIP_DATA_PREFLIGHT:-0}" != "1" && -f "${BACKEND_DIR}/.env" ]]; then
  info "running lightweight data preflight (timeout=${DATA_PREFLIGHT_TIMEOUT}s)"
  out="${TMPDIR:-/tmp}/safe-deploy-preflight-$$.json"
  BACKEND_DIR="$BACKEND_DIR" \
    run_timeout "$DATA_PREFLIGHT_TIMEOUT" \
    "${BACKEND_DIR}/.venv/bin/python" "${SCRIPT_DIR}/lib/preflight_data.py" \
      --env "${BACKEND_DIR}/.env" \
      --sku "$CRITICAL_SKU" \
      --material-id "$CRITICAL_SKU_MATERIAL_ID" \
      --out "$out" >/dev/null
  info "data preflight written: $out"
  python3 -c "import json;d=json.load(open('$out'));print('counts',d['counts']);print('sku',d.get('critical_sku',{}).get('valuation',{}))"
else
  info "skipping data preflight"
fi

# Collision check vs origin/main (informational unless target provided)
if [[ "${REQUIRE_CLEAN_MAIN_COLLISION_CHECK:-1}" == "1" ]]; then
  run_timeout 120 git fetch --prune origin main >/dev/null 2>&1 || warn "git fetch origin main failed (non-fatal for standalone preflight)"
  MAIN_SHA="$(git rev-parse origin/main 2>/dev/null || echo unknown)"
  info "origin/main: $MAIN_SHA"
fi

phase "PREFLIGHT OK"
echo "LIVE_SHA=$LIVE_SHA"
