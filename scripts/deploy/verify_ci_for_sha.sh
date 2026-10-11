#!/usr/bin/env bash
# Explicit CI gate for production deploy preflight.
# Requires the named Reconciliation validation workflow + required job check-runs.
#
# Usage:
#   verify_ci_for_sha.sh <repo> <sha40>
#
# Env:
#   GH_TOKEN / gh auth — required unless ETERNAL_GH_API_MOCK_DIR is set
#   ETERNAL_GH_API_MOCK_DIR — directory with mocked API JSON files for synthetic tests:
#     check-runs.json
#     workflow-runs.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

REPO="${1:-}"
SHA="${2:-}"
REQUIRED_WORKFLOW_FILE="${ETERNAL_REQUIRED_WORKFLOW_FILE:-reconciliation-validation.yml}"
# Job display names that must be success on the commit (live-orders is optional).
REQUIRED_JOB_NAMES_DEFAULT=$'Backend focused tests\nBackend stock compatibility (replica set)\nFrontend tests and build'
REQUIRED_JOB_NAMES="${ETERNAL_REQUIRED_CI_JOBS:-$REQUIRED_JOB_NAMES_DEFAULT}"

[[ -n "$REPO" ]] || eternal_deploy_die "usage: verify_ci_for_sha.sh <repo> <sha40>"
eternal_require_sha40 "$SHA"

eternal_gh_api() {
  local path="$1"
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    case "$path" in
      */check-runs*)
        cat "$ETERNAL_GH_API_MOCK_DIR/check-runs.json"
        return 0
        ;;
      */actions/workflows/*/runs*)
        cat "$ETERNAL_GH_API_MOCK_DIR/workflow-runs.json"
        return 0
        ;;
      *)
        eternal_deploy_die "mock API path not supported: $path"
        ;;
    esac
  fi
  gh api "$path"
}

eternal_deploy_log "verifying explicit CI for $SHA via $REQUIRED_WORKFLOW_FILE"

runs_json="$(eternal_gh_api "repos/${REPO}/actions/workflows/${REQUIRED_WORKFLOW_FILE}/runs?head_sha=${SHA}&per_page=20")"
ok_runs="$(printf '%s' "$runs_json" | jq '[.workflow_runs[]? | select(.conclusion=="success" and .status=="completed")] | length')"
if [[ "${ok_runs:-0}" -lt 1 ]]; then
  eternal_deploy_die "explicit CI: no successful ${REQUIRED_WORKFLOW_FILE} workflow run for $SHA"
fi

checks_json="$(eternal_gh_api "repos/${REPO}/commits/${SHA}/check-runs?per_page=100")"
missing=0
while IFS= read -r job_name; do
  [[ -n "$job_name" ]] || continue
  hit="$(printf '%s' "$checks_json" | jq --arg n "$job_name" '
    [.check_runs[]?
      | select(.name==$n)
      | select(.status=="completed")
      | select(.conclusion=="success")]
    | length')"
  if [[ "${hit:-0}" -lt 1 ]]; then
    eternal_deploy_log "explicit CI missing required success: $job_name"
    missing=1
  else
    eternal_deploy_log "explicit CI OK: $job_name"
  fi
done <<<"$REQUIRED_JOB_NAMES"

if [[ "$missing" -ne 0 ]]; then
  eternal_deploy_die "explicit CI: one or more required check-runs not successful for $SHA"
fi

eternal_deploy_log "explicit CI PASS for $SHA"
printf 'CI_OK sha=%s workflow=%s\n' "$SHA" "$REQUIRED_WORKFLOW_FILE"
