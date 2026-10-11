#!/usr/bin/env bash
# Prepare an immutable verified baseline for first isolated cutover.
# Default dry-run. Pass --apply to write under ETERNAL_BASELINE_DIR.
# Does NOT activate systemd, replace CURRENT, or touch live webroot atomically
# beyond copying a frontend snapshot into the baseline directory.
#
# Refuses to treat a mutable overlay symlink as a verified rollback snapshot.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

APPLY=0
ETERNAL_BASELINE_DIR="${ETERNAL_BASELINE_DIR:-/opt/eternal-packaging-releases/baseline-active}"
ETERNAL_FRONTEND_ROOT="${ETERNAL_FRONTEND_ROOT:-/var/www/eternal-packaging}"
ETERNAL_SERVICE_NAME="${ETERNAL_SERVICE_NAME:-eternal-packaging-backend.service}"
ETERNAL_SYSTEMCTL_BIN="${ETERNAL_SYSTEMCTL_BIN:-systemctl}"
ETERNAL_USE_SYSTEMCTL_SUDO="${ETERNAL_USE_SYSTEMCTL_SUDO:-0}"
ETERNAL_EXPECTED_WD="${ETERNAL_EXPECTED_WD:-/opt/eternal-packaging-current/backend}"
BACKEND_SRC="${ETERNAL_BASELINE_BACKEND_SRC:-}"

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --help|-h)
      cat <<EOF
Usage: prepare-verified-baseline.sh [--apply]
  Copies frontend snapshot + freezes backend tree into baseline-active.
  Records systemd WorkingDirectory/ExecStart for mismatch detection.
EOF
      exit 0
      ;;
    *) eternal_deploy_die "unknown arg: $arg" ;;
  esac
done

eternal_systemctl() {
  if [[ "${ETERNAL_USE_SYSTEMCTL_SUDO}" == "1" ]]; then
    sudo -n "$ETERNAL_SYSTEMCTL_BIN" "$@"
  else
    "$ETERNAL_SYSTEMCTL_BIN" "$@"
  fi
}

wd="$(eternal_systemctl show -p WorkingDirectory --value "$ETERNAL_SERVICE_NAME" 2>/dev/null || true)"
exe="$(eternal_systemctl show -p ExecStart --value "$ETERNAL_SERVICE_NAME" 2>/dev/null || true)"

if [[ -z "$BACKEND_SRC" ]]; then
  if [[ -n "$wd" && "$(basename "$wd")" == "backend" ]]; then
    BACKEND_SRC="$(dirname "$wd")"
  else
    BACKEND_SRC="${ETERNAL_LEGACY_ROOT:-/opt/eternal-packaging}"
  fi
fi

cat <<EOF
PLAN verified baseline
  baseline_dir=$ETERNAL_BASELINE_DIR
  frontend_root=$ETERNAL_FRONTEND_ROOT
  backend_src=$BACKEND_SRC
  systemd_wd=$wd
  expected_wd_after_cutover=$ETERNAL_EXPECTED_WD
  order=1) prepare baseline+mirror+releases  2) retarget unit config  3) activate/deploy
  note=This script does NOT retarget or restart systemd. If systemd_wd != expected_wd, deploy stays BLOCKED until config retarget (separate step).
EOF

if [[ "$APPLY" != "1" ]]; then
  eternal_deploy_log "dry-run only (pass --apply to write baseline)"
  exit 0
fi

[[ -d "$BACKEND_SRC" ]] || eternal_deploy_die "backend source missing: $BACKEND_SRC"
[[ -d "$ETERNAL_FRONTEND_ROOT" ]] || eternal_deploy_die "frontend root missing: $ETERNAL_FRONTEND_ROOT"

rm -rf "$ETERNAL_BASELINE_DIR"
mkdir -p "$ETERNAL_BASELINE_DIR/tree" "$ETERNAL_BASELINE_DIR/frontend-snapshot"

# Immutable backend freeze (copy, not symlink to mutable overlay).
if [[ -d "$BACKEND_SRC/backend" ]]; then
  cp -a "$BACKEND_SRC/backend" "$ETERNAL_BASELINE_DIR/tree/backend"
else
  mkdir -p "$ETERNAL_BASELINE_DIR/tree/backend"
  cp -a "$BACKEND_SRC"/. "$ETERNAL_BASELINE_DIR/tree/backend"/
fi

cp -a "$ETERNAL_FRONTEND_ROOT"/. "$ETERNAL_BASELINE_DIR/frontend-snapshot"/

{
  printf 'kind=verified-baseline\n'
  printf 'sha=baseline-active\n'
  printf 'backend_src=%s\n' "$BACKEND_SRC"
  printf 'systemd_wd_at_capture=%s\n' "${wd:-}"
  printf 'systemd_exec_at_capture=%s\n' "${exe:-}"
  printf 'expected_wd=%s\n' "$ETERNAL_EXPECTED_WD"
  printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'immutable=1\n'
} >"$ETERNAL_BASELINE_DIR/.deploy-ok"
: >"$ETERNAL_BASELINE_DIR/.baseline"

eternal_baseline_write_integrity "$ETERNAL_BASELINE_DIR" "$BACKEND_SRC" "${wd:-}" "${exe:-}"
eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"

printf 'BASELINE_VERIFIED_OK path=%s integrity=1\n' "$ETERNAL_BASELINE_DIR"
