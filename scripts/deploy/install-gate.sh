#!/usr/bin/env bash
# Verified install/refresh of the SSH forced-command gate.
# Writes to a staging path, runs probe, and only activates after acceptance.
#
# Usage (on VPS as deploy user):
#   ./install-gate.sh --source /path/to/checkout/scripts/deploy
#   ./install-gate.sh --source ... --activate
#
# Without --activate: stage + verify only (safe).
# With --activate: atomic replace ~/.ssh/eternal-actions-gate after PASS.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SOURCE_DIR="$SCRIPT_DIR"
TARGET_DIR="${ETERNAL_GATE_INSTALL_DIR:-$HOME/.ssh}"
ACTIVATE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE_DIR="${2:-}"
      shift 2
      ;;
    --target)
      TARGET_DIR="${2:-}"
      shift 2
      ;;
    --activate)
      ACTIVATE=1
      shift
      ;;
    *)
      eternal_deploy_die "unknown arg: $1"
      ;;
  esac
done

[[ -d "$SOURCE_DIR" ]] || eternal_deploy_die "source missing: $SOURCE_DIR"
[[ -f "$SOURCE_DIR/eternal-actions-gate" ]] || eternal_deploy_die "gate script missing in source"
[[ -f "$SOURCE_DIR/eternal-deploy-runner.sh" ]] || eternal_deploy_die "runner missing in source"
[[ -f "$SOURCE_DIR/lib.sh" ]] || eternal_deploy_die "lib.sh missing in source"

mkdir -p "$TARGET_DIR"
STAGE_DIR="$TARGET_DIR/eternal-gate-staging.$$"
mkdir -p "$STAGE_DIR"
cleanup_stage() { rm -rf "$STAGE_DIR"; }
trap cleanup_stage EXIT

install -m 755 "$SOURCE_DIR/eternal-actions-gate" "$STAGE_DIR/eternal-actions-gate"
install -m 755 "$SOURCE_DIR/eternal-deploy-runner.sh" "$STAGE_DIR/eternal-deploy-runner.sh"
install -m 644 "$SOURCE_DIR/lib.sh" "$STAGE_DIR/lib.sh"

probe_out="$(
  SSH_ORIGINAL_COMMAND=probe \
  ETERNAL_DEPLOY_RUNNER="$STAGE_DIR/eternal-deploy-runner.sh" \
  "$STAGE_DIR/eternal-actions-gate"
)"
eternal_deploy_log "staged probe output:"$'\n'"$probe_out"

if ! eternal_probe_output_accepted "$probe_out"; then
  eternal_deploy_die "staged gate probe not accepted; refuse activation"
fi
if ! grep -qE 'ETERNAL_GATE_OK[[:space:]]+probe=pass' <<<"$probe_out"; then
  eternal_deploy_die "staged gate missing canonical ETERNAL_GATE_OK line"
fi

printf 'GATE_STAGE_OK dir=%s\n' "$STAGE_DIR"

if [[ "$ACTIVATE" != "1" ]]; then
  eternal_deploy_log "staging verified; re-run with --activate to replace live gate"
  # Keep stage for inspection by moving aside before trap
  KEEP="$TARGET_DIR/eternal-gate-staging-verified"
  rm -rf "$KEEP"
  mv "$STAGE_DIR" "$KEEP"
  trap - EXIT
  printf 'GATE_VERIFY_OK path=%s\n' "$KEEP"
  exit 0
fi

install -m 755 "$STAGE_DIR/eternal-actions-gate" "$TARGET_DIR/eternal-actions-gate.new"
install -m 755 "$STAGE_DIR/eternal-deploy-runner.sh" "$TARGET_DIR/eternal-deploy-runner.sh"
install -m 644 "$STAGE_DIR/lib.sh" "$TARGET_DIR/lib.sh"
mv -f "$TARGET_DIR/eternal-actions-gate.new" "$TARGET_DIR/eternal-actions-gate"

live_out="$(SSH_ORIGINAL_COMMAND=probe "$TARGET_DIR/eternal-actions-gate")"
eternal_probe_output_accepted "$live_out" || eternal_deploy_die "live gate probe failed after activate"
grep -qE 'ETERNAL_GATE_OK[[:space:]]+probe=pass' <<<"$live_out" \
  || eternal_deploy_die "live gate missing canonical probe line"

printf 'GATE_ACTIVATE_OK path=%s\n' "$TARGET_DIR/eternal-actions-gate"
eternal_deploy_log "authorized_keys command= must point at $TARGET_DIR/eternal-actions-gate before enabling the workflow"
