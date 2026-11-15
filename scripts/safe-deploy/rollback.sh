#!/usr/bin/env bash
# Emergency CODE + frontend rollback. NEVER restores MongoDB.
#
# Usage on VPS:
#   ROLLBACK_SHA=<prev> ./scripts/safe-deploy/rollback.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "${SCRIPT_DIR}/config.sh"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/lock.sh
source "${SCRIPT_DIR}/lib/lock.sh"
# shellcheck source=lib/frontend.sh
source "${SCRIPT_DIR}/lib/frontend.sh"

ROLLBACK_SHA="${ROLLBACK_SHA:-${1:-}}"
[[ -n "$ROLLBACK_SHA" ]] || die "ROLLBACK_SHA required"

phase "CODE ROLLBACK (database NOT restored)"
acquire_deploy_lock "rollback:$ROLLBACK_SHA"
trap release_deploy_lock EXIT

cd "$APP_ROOT"
run_timeout 120 git fetch --prune origin || true
run_timeout "$CUTOVER_STEP_TIMEOUT" git checkout --detach "$ROLLBACK_SHA"
run_timeout "$CUTOVER_STEP_TIMEOUT" sudo systemctl restart "$BACKEND_SERVICE"

if [[ -e "$PREVIOUS_WEBROOT" ]]; then
  rollback_frontend_previous
else
  warn "no previous frontend at $PREVIOUS_WEBROOT — backend-only rollback"
fi

for i in $(seq 1 "${HEALTH_RETRIES}"); do
  if body="$(run_timeout "$HEALTH_TIMEOUT" curl -fsS --max-time "$HEALTH_TIMEOUT" "$HEALTH_URL" 2>/dev/null)"; then
    if python3 -c "import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get('mongo') is True and d.get('mode')=='operational' else 1)" "$body"; then
      info "rollback health OK"
      record_deploy_event rollback_success "$(git rev-parse HEAD)" "$ROLLBACK_SHA" "$ROLLBACK_SHA" "$WEBROOT" "mongo=true mode=operational"
      phase "ROLLBACK SUCCESS"
      exit 0
    fi
  fi
  sleep "$HEALTH_RETRY_SLEEP"
done

die "rollback health check failed — manual intervention required (still no DB restore)"
