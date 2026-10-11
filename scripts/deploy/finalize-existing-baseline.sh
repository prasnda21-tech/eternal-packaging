#!/usr/bin/env bash
# Finalize an EXISTING verified baseline tree without reinstalling runtime.
#
# - Does NOT copy/replace frontend snapshot
# - Does NOT delete or recreate .venv
# - Does NOT point CURRENT or restart systemd
# - --apply acquires the shared deploy lock (same path as eternal-deploy-runner)
#   before verification and holds it until marker writes finish (or fail)
# - Legacy baselines without requirements provenance require explicit Owner
#   attestation (--attest-requirements-file=REL); stamps provenance_source=owner_attested
#   after hash + installed-package + pip check evidence — never invents history
#
# Usage:
#   finalize-existing-baseline.sh
#   finalize-existing-baseline.sh --apply
#   finalize-existing-baseline.sh --apply --attest-requirements-file=requirements.portable.txt

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

APPLY=0
ATTEST_REQ=""
LOCK_HELD=0
ETERNAL_RUNTIME_ROOT="${ETERNAL_RUNTIME_ROOT:-/opt/eternal-packaging-runtime}"
ETERNAL_RELEASES_ROOT="${ETERNAL_RELEASES_ROOT:-$ETERNAL_RUNTIME_ROOT/releases}"
ETERNAL_BASELINE_DIR="${ETERNAL_BASELINE_DIR:-$ETERNAL_RELEASES_ROOT/baseline-active}"
ETERNAL_EXPECTED_WD="${ETERNAL_EXPECTED_WD:-$ETERNAL_RUNTIME_ROOT/current/backend}"
ETERNAL_DEPLOY_LOCK="${ETERNAL_DEPLOY_LOCK:-/tmp/eternal-packaging-deploy.lock}"

cleanup() {
  if [[ "$LOCK_HELD" == "1" ]]; then
    eternal_release_lock "$ETERNAL_DEPLOY_LOCK"
    LOCK_HELD=0
  fi
}
trap cleanup EXIT

for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --attest-requirements-file=*)
      ATTEST_REQ="${arg#--attest-requirements-file=}"
      ;;
    --help|-h)
      cat <<EOF
Usage: finalize-existing-baseline.sh [--apply] [--attest-requirements-file=REL]
  Dry-run by default. Verifies existing baseline integrity + dependency evidence.
  --apply acquires shared deploy lock, then writes provenance/markers on success.
  Legacy baselines without requirements provenance require --attest-requirements-file=
  (Owner confirms the file actually used for the existing install).
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
  attest_requirements_file=${ATTEST_REQ:-none}
  apply=$APPLY
  deploy_lock=$ETERNAL_DEPLOY_LOCK
  note=no snapshot copy; no venv recreate; no CURRENT/systemd activation
EOF

[[ -d "$ETERNAL_BASELINE_DIR" ]] || eternal_deploy_die "baseline dir missing: $ETERNAL_BASELINE_DIR"
[[ -d "$BACKEND" ]] || eternal_deploy_die "baseline backend missing: $BACKEND"
[[ -d "$BACKEND/.venv" ]] || eternal_deploy_die "baseline .venv missing — refuse finalize without reinstall (run prepare only with Owner ack)"
[[ -f "$ETERNAL_BASELINE_DIR/.baseline-integrity" ]] || eternal_deploy_die "baseline integrity manifest missing"
[[ -f "$ETERNAL_BASELINE_DIR/.baseline-verified" ]] || eternal_deploy_die "baseline-verified missing"

if [[ "$APPLY" == "1" ]]; then
  eternal_acquire_lock "$ETERNAL_DEPLOY_LOCK" 0
  LOCK_HELD=1
  eternal_deploy_log "finalize lock acquired pid=$$ path=$ETERNAL_DEPLOY_LOCK"
fi

# 1) Core integrity + runtime (existing venv) — do not recreate. Before any metadata write.
eternal_baseline_verify_integrity_core "$ETERNAL_BASELINE_DIR"

recorded_file="$(sed -n 's/^requirements_file=//p' "$ETERNAL_BASELINE_DIR/.baseline-verified" | head -n1)"
recorded_hash="$(sed -n 's/^requirements_sha256=//p' "$ETERNAL_BASELINE_DIR/.baseline-verified" | head -n1)"
stamp_file=""
stamp_hash=""
if [[ -f "$BACKEND/.eternal-requirements-file" && -f "$BACKEND/.eternal-requirements-sha256" ]]; then
  stamp_file="$(tr -d '\n' <"$BACKEND/.eternal-requirements-file")"
  stamp_hash="$(tr -d '\n' <"$BACKEND/.eternal-requirements-sha256")"
fi

req_rel=""
req_hash=""
provenance_source=""
needs_stamp=0

if [[ -n "$recorded_file" ]]; then
  # Existing recorded provenance — verify against disk; do not invent/replace from policy.
  eternal_verify_requirements_provenance "$BACKEND" "$recorded_file" "$recorded_hash"
  req_rel="$recorded_file"
  req_hash="$recorded_hash"
  provenance_source="$(sed -n 's/^provenance_source=//p' "$ETERNAL_BASELINE_DIR/.baseline-verified" | head -n1)"
  provenance_source="${provenance_source:-recorded}"
  eternal_deploy_log "using recorded provenance requirements_file=$req_rel source=$provenance_source"
elif [[ -n "$stamp_file" ]]; then
  # Install stamps from prepare path — real install evidence, not current-policy invention.
  eternal_verify_requirements_provenance "$BACKEND" "$stamp_file" "$stamp_hash"
  req_rel="$stamp_file"
  req_hash="$stamp_hash"
  provenance_source="install_stamp"
  needs_stamp=1
  eternal_deploy_log "using install stamps requirements_file=$req_rel (will stamp baseline-verified)"
else
  # Legacy: no recorded provenance and no install stamps — Owner must attest.
  needs_stamp=1
  if [[ -z "$ATTEST_REQ" ]]; then
    if [[ "$APPLY" != "1" ]]; then
      printf 'FINALIZE_DRY_RUN_NEEDS_ATTEST path=%s\n' "$ETERNAL_BASELINE_DIR"
      eternal_deploy_log "dry-run: legacy baseline lacks requirements provenance; pass --attest-requirements-file=REL --apply"
      exit 0
    fi
    eternal_deploy_die "legacy baseline lacks requirements provenance; refuse to invent install history (pass --attest-requirements-file=REL confirming the file used for the existing install)"
  fi
  ETERNAL_REQUIREMENTS_FILE="$ATTEST_REQ"
  req_rel="$(eternal_resolve_requirements_relpath "$BACKEND")"
  req_hash="$(eternal_requirements_sha256 "$BACKEND")"
  [[ "$req_rel" == "$ATTEST_REQ" ]] \
    || eternal_deploy_die "attest path resolved to $req_rel != $ATTEST_REQ"
  eternal_deploy_log "owner-attested requirements_file=$req_rel sha256=$req_hash"
  eternal_verify_requirements_installed "$BACKEND" "$req_rel"
  provenance_source="owner_attested"
fi

if [[ "$APPLY" != "1" ]]; then
  if [[ "$needs_stamp" == "1" ]]; then
    printf 'FINALIZE_DRY_RUN_NEEDS_APPLY path=%s requirements_file=%s provenance_source=%s\n' \
      "$ETERNAL_BASELINE_DIR" "$req_rel" "$provenance_source"
    eternal_deploy_log "dry-run: digests/runtime OK; pass --apply to stamp requirements + completion markers"
    exit 0
  fi
  eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"
  printf 'FINALIZE_DRY_RUN_OK path=%s requirements_file=%s\n' "$ETERNAL_BASELINE_DIR" "$req_rel"
  exit 0
fi

# 3) Stamp requirements provenance into .baseline-verified when needed (keep digest fields).
if [[ "$needs_stamp" == "1" ]]; then
  tmp_verified="$(mktemp)"
  {
    grep -vE '^(requirements_file|requirements_sha256|provenance_source|note|finalized_at)=' \
      "$ETERNAL_BASELINE_DIR/.baseline-verified" || true
    printf 'requirements_file=%s\n' "$req_rel"
    printf 'requirements_sha256=%s\n' "$req_hash"
    printf 'provenance_source=%s\n' "$provenance_source"
    if [[ "$provenance_source" == "owner_attested" ]]; then
      printf 'note=Owner-attested requirements for existing install; hash+pip-check verified; no venv reinstall; not activated\n'
    else
      printf 'note=Finalized existing baseline from install stamps; no venv reinstall; not activated\n'
    fi
    printf 'finalized_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$tmp_verified"
  mv -Tf "$tmp_verified" "$ETERNAL_BASELINE_DIR/.baseline-verified"
  printf '%s\n' "$req_rel" >"$BACKEND/.eternal-requirements-file"
  printf '%s\n' "$req_hash" >"$BACKEND/.eternal-requirements-sha256"
fi

# 4) Full verify including requirements — refuse markers on failure.
eternal_baseline_verify_integrity "$ETERNAL_BASELINE_DIR"

# 5) Completion markers only after success (idempotent rewrite).
{
  printf 'kind=verified-baseline\n'
  printf 'sha=baseline-active\n'
  printf 'immutable=1\n'
  printf 'requirements_file=%s\n' "$req_rel"
  printf 'requirements_sha256=%s\n' "$req_hash"
  printf 'provenance_source=%s\n' "$provenance_source"
  printf 'expected_wd=%s\n' "$ETERNAL_EXPECTED_WD"
  printf 'finalized_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'note=Finalize complete; activation (CURRENT/systemd) is a separate Owner step\n'
} >"$ETERNAL_BASELINE_DIR/.deploy-ok"
: >"$ETERNAL_BASELINE_DIR/.baseline"

printf 'BASELINE_FINALIZE_OK path=%s requirements_file=%s provenance_source=%s\n' \
  "$ETERNAL_BASELINE_DIR" "$req_rel" "$provenance_source"
eternal_deploy_log "finalize complete — CURRENT/systemd untouched"
