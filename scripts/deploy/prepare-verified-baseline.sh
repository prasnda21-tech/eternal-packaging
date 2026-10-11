#!/usr/bin/env bash
# Prepare an immutable verified baseline for first isolated cutover.
# Default dry-run. Pass --apply to write under ETERNAL_BASELINE_DIR.
# Does NOT activate systemd, replace CURRENT, or rename live webroot.
#
# Refuses to treat a mutable overlay symlink as a verified rollback snapshot.
# Refuses overwrite of an existing baseline unless --force-recreate (explicit).
# Never copies a live/legacy .venv (shebangs point at old paths); prepares a
# fresh isolated runtime instead.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

APPLY=0
FORCE_RECREATE=0
ETERNAL_RUNTIME_ROOT="${ETERNAL_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
ETERNAL_RELEASES_ROOT="${ETERNAL_RELEASES_ROOT:-$ETERNAL_RUNTIME_ROOT/releases}"
ETERNAL_BASELINE_DIR="${ETERNAL_BASELINE_DIR:-$ETERNAL_RELEASES_ROOT/baseline-active}"
ETERNAL_FRONTEND_ROOT="${ETERNAL_FRONTEND_ROOT:-/var/www/eternal-packaging}"
ETERNAL_SERVICE_NAME="${ETERNAL_SERVICE_NAME:-eternal-packaging-backend.service}"
ETERNAL_SYSTEMCTL_BIN="${ETERNAL_SYSTEMCTL_BIN:-systemctl}"
ETERNAL_USE_SYSTEMCTL_SUDO="${ETERNAL_USE_SYSTEMCTL_SUDO:-0}"
ETERNAL_EXPECTED_WD="${ETERNAL_EXPECTED_WD:-$ETERNAL_RUNTIME_ROOT/current/backend}"
ETERNAL_RUNTIME_MODE="${ETERNAL_RUNTIME_MODE:-full}"
BACKEND_SRC="${ETERNAL_BASELINE_BACKEND_SRC:-}"

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --force-recreate) FORCE_RECREATE=1 ;;
    --help|-h)
      cat <<EOF
Usage: prepare-verified-baseline.sh [--apply] [--force-recreate]
  Copies frontend snapshot + freezes backend tree into baseline-active.
  Prepares a fresh isolated .venv (never copies live venv).
  Records systemd WorkingDirectory/ExecStart for mismatch detection.
  Refuses overwrite of existing baseline unless --force-recreate.
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
  runtime_mode=$ETERNAL_RUNTIME_MODE
  force_recreate=$FORCE_RECREATE
  order=1) prepare baseline+mirror+releases  2) retarget unit config  3) activate/deploy
  note=This script does NOT retarget or restart systemd. If systemd_wd != expected_wd, deploy stays BLOCKED until config retarget (separate step).
EOF

if [[ "$APPLY" != "1" ]]; then
  eternal_deploy_log "dry-run only (pass --apply to write baseline)"
  exit 0
fi

[[ -d "$BACKEND_SRC" ]] || eternal_deploy_die "backend source missing: $BACKEND_SRC"
[[ -d "$ETERNAL_FRONTEND_ROOT" ]] || eternal_deploy_die "frontend root missing: $ETERNAL_FRONTEND_ROOT"
[[ -w "$(dirname "$ETERNAL_BASELINE_DIR")" ]] \
  || eternal_deploy_die "releases parent not writable: $(dirname "$ETERNAL_BASELINE_DIR") (run bootstrap first)"

if [[ -e "$ETERNAL_BASELINE_DIR" ]]; then
  if [[ "$FORCE_RECREATE" != "1" ]]; then
    eternal_deploy_die "baseline already exists at $ETERNAL_BASELINE_DIR; refuse overwrite (pass --force-recreate only after Owner ack that rollback baseline may be replaced)"
  fi
  if [[ -L "${ETERNAL_CURRENT_LINK:-/opt/eternal-packaging-runtime/current}" ]]; then
    cur="$(readlink -f "${ETERNAL_CURRENT_LINK}" 2>/dev/null || true)"
    case "$cur" in
      "$ETERNAL_BASELINE_DIR"/*)
        eternal_deploy_die "refusing --force-recreate: CURRENT still points at baseline rollback tree ($cur)"
        ;;
    esac
  fi
  eternal_deploy_log "Owner-ack force-recreate: removing previous baseline at $ETERNAL_BASELINE_DIR"
  rm -rf "$ETERNAL_BASELINE_DIR"
fi

mkdir -p "$ETERNAL_BASELINE_DIR/tree" "$ETERNAL_BASELINE_DIR/frontend-snapshot"

# Immutable backend freeze (copy sources only — exclude .venv / __pycache__).
if [[ -d "$BACKEND_SRC/backend" ]]; then
  mkdir -p "$ETERNAL_BASELINE_DIR/tree/backend"
  tar -C "$BACKEND_SRC/backend" \
    --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    -cf - . | tar -C "$ETERNAL_BASELINE_DIR/tree/backend" -xf -
else
  mkdir -p "$ETERNAL_BASELINE_DIR/tree/backend"
  tar -C "$BACKEND_SRC" \
    --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
    -cf - . | tar -C "$ETERNAL_BASELINE_DIR/tree/backend" -xf -
fi

# Live FE snapshot (selective-merge inclusive). Do not rename webroot.
cp -a "$ETERNAL_FRONTEND_ROOT"/. "$ETERNAL_BASELINE_DIR/frontend-snapshot"/
[[ -f "$ETERNAL_BASELINE_DIR/frontend-snapshot/index.html" ]] \
  || eternal_deploy_die "frontend snapshot missing index.html"

# Fresh isolated runtime for rollback (never copy live venv shebangs).
# Uses requirements resolver (default requirements.portable.txt).
eternal_deploy_log "preparing baseline rollback runtime (mode=$ETERNAL_RUNTIME_MODE)"
eternal_prepare_backend_runtime "$ETERNAL_BASELINE_DIR/tree/backend"

eternal_baseline_write_integrity "$ETERNAL_BASELINE_DIR" "$BACKEND_SRC" "${wd:-}" "${exe:-}"
eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"

# Completion markers only after integrity + requirements provenance succeed.
req_rel="$(sed -n 's/^requirements_file=//p' "$ETERNAL_BASELINE_DIR/.baseline-verified" | head -n1)"
req_hash="$(sed -n 's/^requirements_sha256=//p' "$ETERNAL_BASELINE_DIR/.baseline-verified" | head -n1)"
{
  printf 'kind=verified-baseline\n'
  printf 'sha=baseline-active\n'
  printf 'backend_src=%s\n' "$BACKEND_SRC"
  printf 'systemd_wd_at_capture=%s\n' "${wd:-}"
  printf 'systemd_exec_at_capture=%s\n' "${exe:-}"
  printf 'expected_wd=%s\n' "$ETERNAL_EXPECTED_WD"
  printf 'requirements_file=%s\n' "$req_rel"
  printf 'requirements_sha256=%s\n' "$req_hash"
  printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'immutable=1\n'
} >"$ETERNAL_BASELINE_DIR/.deploy-ok"
: >"$ETERNAL_BASELINE_DIR/.baseline"

printf 'BASELINE_VERIFIED_OK path=%s integrity=1 runtime=1 requirements_file=%s\n' \
  "$ETERNAL_BASELINE_DIR" "$req_rel"
