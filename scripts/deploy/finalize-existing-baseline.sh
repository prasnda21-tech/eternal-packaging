#!/usr/bin/env bash
# Finalize an EXISTING verified baseline tree without reinstalling runtime.
#
# - Does NOT copy/replace frontend snapshot
# - Does NOT delete or recreate .venv
# - Does NOT point CURRENT or restart systemd
# - Verifies digests + existing runtime first
# - Records requirements_file + sha256 for the file actually present (default portable)
# - Writes .baseline / .deploy-ok only after all checks pass
#
# Usage:
#   finalize-existing-baseline.sh           # dry-run
#   finalize-existing-baseline.sh --apply   # write markers + requirements provenance

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

APPLY=0
ETERNAL_RUNTIME_ROOT="${ETERNAL_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
ETERNAL_RELEASES_ROOT="${ETERNAL_RELEASES_ROOT:-$ETERNAL_RUNTIME_ROOT/releases}"
ETERNAL_BASELINE_DIR="${ETERNAL_BASELINE_DIR:-$ETERNAL_RELEASES_ROOT/baseline-active}"
ETERNAL_EXPECTED_WD="${ETERNAL_EXPECTED_WD:-$ETERNAL_RUNTIME_ROOT/current/backend}"

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --help|-h)
      cat <<EOF
Usage: finalize-existing-baseline.sh [--apply]
  Dry-run by default. Verifies existing baseline integrity + dependency evidence.
  --apply writes requirements provenance (if needed) and completion markers.
  Never reinstalls .venv, never retargets systemd, never switches CURRENT.
EOF
      exit 0
      ;;
    *) eternal_deploy_die "unknown arg: $arg" ;;
  esac
done

BACKEND="$ETERNAL_BASELINE_DIR/tree/backend"

cat <<EOF
PLAN finalize existing baseline
  baseline_dir=$ETERNAL_BASELINE_DIR
  requirements_policy=${ETERNAL_REQUIREMENTS_FILE:-requirements.portable.txt}
  apply=$APPLY
  note=no snapshot copy; no venv recreate; no CURRENT/systemd activation
EOF

[[ -d "$ETERNAL_BASELINE_DIR" ]] || eternal_deploy_die "baseline dir missing: $ETERNAL_BASELINE_DIR"
[[ -d "$BACKEND" ]] || eternal_deploy_die "baseline backend missing: $BACKEND"
[[ -d "$BACKEND/.venv" ]] || eternal_deploy_die "baseline .venv missing — refuse finalize without reinstall (run prepare only with Owner ack)"
[[ -f "$ETERNAL_BASELINE_DIR/.baseline-integrity" ]] || eternal_deploy_die "baseline integrity manifest missing"
[[ -f "$ETERNAL_BASELINE_DIR/.baseline-verified" ]] || eternal_deploy_die "baseline-verified missing"

# 1) Core integrity + runtime (existing venv) — do not recreate.
eternal_baseline_verify_integrity_core "$ETERNAL_BASELINE_DIR"

# 2) Dependency evidence via shared resolver (no silent txt fallback).
req_rel="$(eternal_resolve_requirements_relpath "$BACKEND")"
req_hash="$(eternal_requirements_sha256 "$BACKEND")"
eternal_deploy_log "dependency evidence requirements_file=$req_rel sha256=$req_hash"

recorded_file="$(sed -n 's/^requirements_file=//p' "$ETERNAL_BASELINE_DIR/.baseline-verified" | head -n1)"
recorded_hash="$(sed -n 's/^requirements_sha256=//p' "$ETERNAL_BASELINE_DIR/.baseline-verified" | head -n1)"

needs_stamp=0
if [[ -z "$recorded_file" ]]; then
  needs_stamp=1
  eternal_deploy_log "baseline-verified lacks requirements_file (legacy)"
elif [[ "$recorded_file" != "$req_rel" || "$recorded_hash" != "$req_hash" ]]; then
  needs_stamp=1
  eternal_deploy_log "recorded requirements_file=$recorded_file hash=${recorded_hash:-empty}"
  eternal_deploy_log "resolved requirements_file=$req_rel hash=$req_hash"
else
  eternal_verify_requirements_provenance "$BACKEND" "$recorded_file" "$recorded_hash"
fi

if [[ "$APPLY" != "1" ]]; then
  if [[ "$needs_stamp" == "1" ]]; then
    printf 'FINALIZE_DRY_RUN_NEEDS_APPLY path=%s requirements_file=%s\n' "$ETERNAL_BASELINE_DIR" "$req_rel"
    eternal_deploy_log "dry-run: digests/runtime OK; pass --apply to stamp requirements + completion markers"
    exit 0
  fi
  eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"
  printf 'FINALIZE_DRY_RUN_OK path=%s requirements_file=%s\n' "$ETERNAL_BASELINE_DIR" "$req_rel"
  exit 0
fi

# 3) Stamp requirements provenance into .baseline-verified (keep digest fields intact).
tmp_verified="$(mktemp)"
{
  # Drop old requirements_* lines; keep all other provenance.
  grep -vE '^(requirements_file|requirements_sha256|note)=' "$ETERNAL_BASELINE_DIR/.baseline-verified" || true
  printf 'requirements_file=%s\n' "$req_rel"
  printf 'requirements_sha256=%s\n' "$req_hash"
  printf 'note=Finalized existing baseline; requirements stamped after digest+runtime verify; no venv reinstall; not activated\n'
  printf 'finalized_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} >"$tmp_verified"
mv -Tf "$tmp_verified" "$ETERNAL_BASELINE_DIR/.baseline-verified"
printf '%s\n' "$req_rel" >"$BACKEND/.eternal-requirements-file"
printf '%s\n' "$req_hash" >"$BACKEND/.eternal-requirements-sha256"

# 4) Full verify including requirements — refuse markers on failure.
eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"

# 5) Completion markers only after success (idempotent rewrite).
{
  printf 'kind=verified-baseline\n'
  printf 'sha=baseline-active\n'
  printf 'immutable=1\n'
  printf 'requirements_file=%s\n' "$req_rel"
  printf 'requirements_sha256=%s\n' "$req_hash"
  printf 'expected_wd=%s\n' "$ETERNAL_EXPECTED_WD"
  printf 'finalized_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'note=Finalize complete; activation (CURRENT/systemd) is a separate Owner step\n'
} >"$ETERNAL_BASELINE_DIR/.deploy-ok"
: >"$ETERNAL_BASELINE_DIR/.baseline"

printf 'BASELINE_FINALIZE_OK path=%s requirements_file=%s\n' "$ETERNAL_BASELINE_DIR" "$req_rel"
eternal_deploy_log "finalize complete — CURRENT/systemd untouched"
