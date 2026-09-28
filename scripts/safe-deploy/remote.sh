#!/usr/bin/env bash
# Remote wrapper: run safe-deploy on production via SSH with hard timeouts.
# Does NOT deploy unless TARGET_SHA is set and --execute is passed.
#
# Examples:
#   ./scripts/safe-deploy/remote.sh preflight
#   ./scripts/safe-deploy/remote.sh deploy --execute TARGET_SHA=<sha>
#
# Defaults to dry information / preflight only.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=config.sh
source "${SCRIPT_DIR}/config.sh"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

HOST="${DEPLOY_SSH_HOST:-103.126.116.122}"
USER="${DEPLOY_SSH_USER:-eternaladmin}"
KEY="${DEPLOY_SSH_KEY:-${HOME}/.cursor/ssh/eternal_vps_cursor_ed25519}"
APP_ROOT="${APP_ROOT:-/opt/eternal-packaging}"

ACTION="${1:-preflight}"
shift || true

EXECUTE=0
TARGET_SHA="${TARGET_SHA:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --execute) EXECUTE=1; shift ;;
    TARGET_SHA=*) TARGET_SHA="${1#TARGET_SHA=}"; shift ;;
    *) die "unknown arg: $1" ;;
  esac
done

SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
     -o ConnectTimeout="${SSH_CONNECT_TIMEOUT}"
     -o ServerAliveInterval=15
     -o ServerAliveCountMax=3
     "${USER}@${HOST}")

remote() {
  run_timeout "${SSH_COMMAND_TIMEOUT}" "${SSH[@]}" "$@"
}

case "$ACTION" in
  preflight)
    phase "REMOTE PREFLIGHT (read-only)"
    remote "cd ${APP_ROOT} && bash scripts/safe-deploy/preflight.sh"
    ;;
  deploy)
    [[ "$EXECUTE" == "1" ]] || die "refusing deploy without --execute (safety)"
    [[ -n "$TARGET_SHA" ]] || die "TARGET_SHA required"
    phase "REMOTE DEPLOY exact SHA $TARGET_SHA"
    # Overall deploy wall clock = build + cutover budget
    run_timeout $(( FRONTEND_BUILD_TIMEOUT + 300 )) "${SSH[@]}" \
      "cd ${APP_ROOT} && TARGET_SHA=${TARGET_SHA} bash scripts/safe-deploy/deploy.sh"
    ;;
  *)
    die "usage: $0 {preflight|deploy} [--execute] [TARGET_SHA=...]"
    ;;
esac
