#!/usr/bin/env bash
# Verified install/refresh of the SSH forced-command gate + runner + lib.
# Transactional activate: backup live files (hash/owner/mode), stage, probe,
# activate, post-probe; automatic restore on failure or interrupted activate.
#
# Usage (on VPS as deploy user):
#   ./install-gate.sh --source /path/to/checkout/scripts/deploy
#   ./install-gate.sh --source ... --activate
#
# Without --activate: stage + verify only (safe).
# With --activate: backup → atomic replace → probe; restore on failure.
#
# Does NOT deploy application releases. Separate authorization from app deploy.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SOURCE_DIR="$SCRIPT_DIR"
TARGET_DIR="${ETERNAL_GATE_INSTALL_DIR:-$HOME/.ssh}"
ACTIVATE=0
BACKUP_RETENTION="${ETERNAL_GATE_BACKUP_RETENTION:-5}"

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

FILES=(eternal-actions-gate eternal-deploy-runner.sh lib.sh)

eternal_gate_file_meta() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    printf 'missing\n'
    return 0
  fi
  local mode owner group digest
  mode="$(stat -c '%a' "$path" 2>/dev/null || echo '?')"
  owner="$(stat -c '%u' "$path" 2>/dev/null || echo '?')"
  group="$(stat -c '%g' "$path" 2>/dev/null || echo '?')"
  if [[ -f "$path" ]]; then
    digest="$(sha256sum "$path" | awk '{print $1}')"
  else
    digest="not-a-file"
  fi
  printf 'mode=%s owner=%s group=%s sha256=%s\n' "$mode" "$owner" "$group" "$digest"
}

eternal_gate_restore_backup() {
  local backup_dir="$1"
  local f
  [[ -d "$backup_dir" ]] || return 1
  eternal_deploy_log "restoring gate/runner/lib from $backup_dir"
  for f in "${FILES[@]}"; do
    if [[ -f "$backup_dir/$f" ]]; then
      local mode="755"
      [[ "$f" == "lib.sh" ]] && mode="644"
      if [[ -f "$backup_dir/$f.mode" ]]; then
        mode="$(tr -d '\n' <"$backup_dir/$f.mode")"
      fi
      install -m "$mode" "$backup_dir/$f" "$TARGET_DIR/${f}.restore"
      mv -f "$TARGET_DIR/${f}.restore" "$TARGET_DIR/$f"
    fi
  done
}

mkdir -p "$TARGET_DIR"
STAGE_DIR="$TARGET_DIR/eternal-gate-staging.$$"
mkdir -p "$STAGE_DIR"
cleanup_stage() { rm -rf "$STAGE_DIR"; }
trap cleanup_stage EXIT

install -m 755 "$SOURCE_DIR/eternal-actions-gate" "$STAGE_DIR/eternal-actions-gate"
install -m 755 "$SOURCE_DIR/eternal-deploy-runner.sh" "$STAGE_DIR/eternal-deploy-runner.sh"
install -m 644 "$SOURCE_DIR/lib.sh" "$STAGE_DIR/lib.sh"

# Pre-install probe of staged copy.
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
  KEEP="$TARGET_DIR/eternal-gate-staging-verified"
  rm -rf "$KEEP"
  mv "$STAGE_DIR" "$KEEP"
  trap - EXIT
  printf 'GATE_VERIFY_OK path=%s\n' "$KEEP"
  exit 0
fi

# --- transactional activate ---
BACKUP_ROOT="$TARGET_DIR/eternal-gate-backups"
mkdir -p "$BACKUP_ROOT"
BACKUP_DIR="$BACKUP_ROOT/backup-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$BACKUP_DIR"
printf 'created_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$BACKUP_DIR/META.txt"

for f in "${FILES[@]}"; do
  if [[ -e "$TARGET_DIR/$f" ]]; then
    cp -a "$TARGET_DIR/$f" "$BACKUP_DIR/$f"
    eternal_gate_file_meta "$TARGET_DIR/$f" >"$BACKUP_DIR/$f.meta"
    stat -c '%a' "$TARGET_DIR/$f" >"$BACKUP_DIR/$f.mode" 2>/dev/null || true
  else
    printf 'missing\n' >"$BACKUP_DIR/$f.meta"
  fi
done
printf 'GATE_BACKUP_OK path=%s\n' "$BACKUP_DIR"

ACTIVATE_STATE="$TARGET_DIR/eternal-gate-activate.in-progress"
printf '%s\n' "$BACKUP_DIR" >"$ACTIVATE_STATE"

restore_on_fail() {
  local ec=$?
  if [[ -f "$ACTIVATE_STATE" ]]; then
    local b
    b="$(tr -d '\n' <"$ACTIVATE_STATE" 2>/dev/null || true)"
    if [[ -n "$b" && -d "$b" ]]; then
      eternal_gate_restore_backup "$b" || true
      eternal_deploy_log "GATE_ACTIVATE_ROLLED_BACK from=$b"
    fi
    rm -f "$ACTIVATE_STATE" 2>/dev/null || true
  fi
  cleanup_stage
  exit "$ec"
}
trap restore_on_fail EXIT

# Stage to .new then move into place (gate last so mid-failure still probes old gate if present).
install -m 755 "$STAGE_DIR/eternal-deploy-runner.sh" "$TARGET_DIR/eternal-deploy-runner.sh.new"
install -m 644 "$STAGE_DIR/lib.sh" "$TARGET_DIR/lib.sh.new"
install -m 755 "$STAGE_DIR/eternal-actions-gate" "$TARGET_DIR/eternal-actions-gate.new"
mv -f "$TARGET_DIR/eternal-deploy-runner.sh.new" "$TARGET_DIR/eternal-deploy-runner.sh"
mv -f "$TARGET_DIR/lib.sh.new" "$TARGET_DIR/lib.sh"
mv -f "$TARGET_DIR/eternal-actions-gate.new" "$TARGET_DIR/eternal-actions-gate"

live_out="$(SSH_ORIGINAL_COMMAND=probe "$TARGET_DIR/eternal-actions-gate")"
if ! eternal_probe_output_accepted "$live_out"; then
  eternal_deploy_die "live gate probe failed after activate"
fi
if ! grep -qE 'ETERNAL_GATE_OK[[:space:]]+probe=pass' <<<"$live_out"; then
  eternal_deploy_die "live gate missing canonical probe line"
fi

rm -f "$ACTIVATE_STATE"
trap cleanup_stage EXIT

# Prune old backups (keep newest N).
if [[ -d "$BACKUP_ROOT" ]]; then
  mapfile -t olds < <(ls -1dt "$BACKUP_ROOT"/backup-* 2>/dev/null | tail -n +"$((BACKUP_RETENTION + 1))" || true)
  for d in "${olds[@]:-}"; do
    [[ -n "$d" && -d "$d" ]] && rm -rf "$d"
  done
fi

printf 'GATE_ACTIVATE_OK path=%s backup=%s\n' "$TARGET_DIR/eternal-actions-gate" "$BACKUP_DIR"
eternal_deploy_log "authorized_keys command= must point at $TARGET_DIR/eternal-actions-gate before enabling the workflow"
