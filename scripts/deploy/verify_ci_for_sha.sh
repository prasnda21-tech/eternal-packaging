#!/usr/bin/env bash
# Explicit CI gate for production deploy preflight.
# Requires the named Reconciliation validation workflow run and its Actions jobs.
# Does not call the GitHub Checks API.
#
# Usage:
#   verify_ci_for_sha.sh <repo> <sha40>
#
# Env:
#   GH_TOKEN / gh auth — required unless ETERNAL_GH_API_MOCK_DIR is set
#   ETERNAL_GH_API_MOCK_DIR — synthetic GitHub Actions responses (no network):
#     workflow.json
#     workflow-runs.json
#     workflow-runs-page-N.json
#     jobs-<run_id>.json
#     jobs-<run_id>-page-N.json
#     workflow-http-status / runs-http-status / jobs-http-status
#     hang-kinds / hang-sec
#   ETERNAL_REQUIRED_WORKFLOW_FILE — default reconciliation-validation.yml
#   ETERNAL_REQUIRED_CI_JOBS — newline-separated exact job names
#   ETERNAL_CI_HTTP_TIMEOUT_SEC — per-request timeout (default 30)
#   ETERNAL_CI_HTTP_KILL_AFTER_SEC — SIGKILL delay after TERM (default 2)
#   ETERNAL_CI_MAX_PAGES — page cap for runs and jobs (default 5)
#   ETERNAL_CI_PER_PAGE — page size (default 100)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

REPO="${1:-}"
SHA="${2:-}"
REQUIRED_WORKFLOW_FILE="${ETERNAL_REQUIRED_WORKFLOW_FILE:-reconciliation-validation.yml}"
REQUIRED_JOB_NAMES_DEFAULT=$'Backend focused tests\nBackend stock compatibility (replica set)\nFrontend tests and build'
REQUIRED_JOB_NAMES="${ETERNAL_REQUIRED_CI_JOBS:-$REQUIRED_JOB_NAMES_DEFAULT}"
HTTP_TIMEOUT_SEC="${ETERNAL_CI_HTTP_TIMEOUT_SEC:-30}"
HTTP_KILL_AFTER_SEC="${ETERNAL_CI_HTTP_KILL_AFTER_SEC:-2}"
MAX_PAGES="${ETERNAL_CI_MAX_PAGES:-5}"
PER_PAGE="${ETERNAL_CI_PER_PAGE:-100}"
EXPECTED_WORKFLOW_PATH=".github/workflows/${REQUIRED_WORKFLOW_FILE}"

eternal_ci_positive_int() {
  [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]
}

eternal_serve_mock() {
  local path="$1"
  local dir="${ETERNAL_GH_API_MOCK_DIR:-}"
  local kind="other" page="1" file="" status="200" run_id="" secs="30"
  [[ -n "$dir" ]] || return 1
  printf '%s\n' "$path" >>"${dir}/calls.log"
  if [[ "$path" == *"/check-runs"* ]]; then
    kind="checks"
  elif [[ "$path" == *"/actions/runs/"*"/jobs"* ]]; then
    kind="jobs"
  elif [[ "$path" == *"/actions/workflows/"*"/runs"* ]]; then
    kind="runs"
  elif [[ "$path" == *"/actions/workflows/"* ]]; then
    kind="workflow"
  fi
  if [[ "$path" =~ [\?\&]page=([0-9]+) ]]; then
    page="${BASH_REMATCH[1]}"
  fi
  if [[ -f "${dir}/hang-kinds" ]] && grep -qx "$kind" "${dir}/hang-kinds"; then
    if [[ -f "${dir}/hang-sec" ]]; then
      secs="$(tr -d '[:space:]' <"${dir}/hang-sec")"
    fi
    [[ "$secs" =~ ^[0-9]+$ ]] || secs=30
    sleep "$secs"
  fi
  if [[ -f "${dir}/${kind}-http-status" ]]; then
    status="$(tr -d '[:space:]' <"${dir}/${kind}-http-status")"
  fi
  if [[ "$status" != "200" && "$status" != "0" && "$status" != "ok" ]]; then
    if [[ "$status" == "403" ]]; then
      echo "gh: Resource not accessible by personal access token (HTTP 403)" >&2
    elif [[ "$status" == "429" ]]; then
      echo "gh: API rate limit exceeded (HTTP 429)" >&2
    else
      echo "gh: HTTP ${status}" >&2
    fi
    return 1
  fi
  case "$kind" in
    workflow)
      file="${dir}/workflow.json"
      [[ -f "$file" ]] || { echo "gh: HTTP 404" >&2; return 1; }
      cat "$file"
      ;;
    runs)
      if [[ "$page" == "1" ]]; then
        file="${dir}/workflow-runs.json"
      else
        file="${dir}/workflow-runs-page-${page}.json"
      fi
      if [[ ! -f "$file" ]]; then
        if [[ "$page" == "1" ]]; then
          echo "gh: HTTP 404" >&2
          return 1
        fi
        printf '%s\n' '{"workflow_runs":[]}'
        return 0
      fi
      cat "$file"
      ;;
    jobs)
      [[ "$path" =~ /actions/runs/([0-9]+)/jobs ]] || { echo "gh: HTTP 404" >&2; return 1; }
      run_id="${BASH_REMATCH[1]}"
      if [[ "$page" == "1" ]]; then
        file="${dir}/jobs-${run_id}.json"
      else
        file="${dir}/jobs-${run_id}-page-${page}.json"
      fi
      if [[ ! -f "$file" ]]; then
        if [[ "$page" == "1" ]]; then
          echo "gh: HTTP 404" >&2
          return 1
        fi
        printf '%s\n' '{"jobs":[]}'
        return 0
      fi
      cat "$file"
      ;;
    *)
      echo "gh: HTTP 404" >&2
      return 1
      ;;
  esac
}

if [[ "${1:-}" == "--mock-request" ]]; then
  [[ -n "${2:-}" ]] || exit 1
  eternal_serve_mock "$2"
  exit $?
fi

[[ -n "$REPO" ]] || eternal_deploy_die "usage: verify_ci_for_sha.sh <repo> <sha40>"
eternal_require_sha40 "$SHA"
[[ "$REQUIRED_WORKFLOW_FILE" =~ ^[A-Za-z0-9._-]+\.yml$ ]] \
  || eternal_deploy_die "explicit CI: workflow file name refused (fail closed)"
[[ -n "${REQUIRED_JOB_NAMES//[[:space:]]/}" ]] \
  || eternal_deploy_die "explicit CI: required job list empty (fail closed)"
eternal_ci_positive_int "$HTTP_TIMEOUT_SEC" \
  || eternal_deploy_die "ETERNAL_CI_HTTP_TIMEOUT_SEC must be a positive integer (fail closed)"
eternal_ci_positive_int "$HTTP_KILL_AFTER_SEC" \
  || eternal_deploy_die "ETERNAL_CI_HTTP_KILL_AFTER_SEC must be a positive integer (fail closed)"
eternal_ci_positive_int "$MAX_PAGES" \
  || eternal_deploy_die "ETERNAL_CI_MAX_PAGES must be a positive integer (fail closed)"
eternal_ci_positive_int "$PER_PAGE" \
  || eternal_deploy_die "ETERNAL_CI_PER_PAGE must be a positive integer (fail closed)"
command -v timeout >/dev/null 2>&1 \
  || eternal_deploy_die "explicit CI: timeout(1) required (fail closed)"
command -v jq >/dev/null 2>&1 \
  || eternal_deploy_die "explicit CI: jq required (fail closed)"
if [[ -z "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
  command -v gh >/dev/null 2>&1 \
    || eternal_deploy_die "explicit CI: gh required (fail closed)"
fi

eternal_ci_api_json() {
  local path="$1"
  local out err ec kind
  out="$(mktemp)"
  err="$(mktemp)"
  set +e
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    timeout -k "$HTTP_KILL_AFTER_SEC" "$HTTP_TIMEOUT_SEC" \
      bash "${BASH_SOURCE[0]}" --mock-request "$path" >"$out" 2>"$err"
    ec=$?
  else
    timeout -k "$HTTP_KILL_AFTER_SEC" "$HTTP_TIMEOUT_SEC" \
      gh api "$path" >"$out" 2>"$err"
    ec=$?
  fi
  set -e
  if [[ "$ec" -eq 124 || "$ec" -eq 137 ]]; then
    rm -f "$out" "$err"
    eternal_deploy_die "explicit CI: GitHub API timeout (fail closed)"
  fi
  if [[ "$ec" -ne 0 ]]; then
    kind="error"
    if grep -qi 'rate limit' "$err" || grep -q 'HTTP 429' "$err"; then
      kind="rate"
    elif grep -q 'HTTP 403' "$err"; then
      kind="403"
    fi
    rm -f "$out" "$err"
    if [[ "$kind" == "rate" ]]; then
      eternal_deploy_die "explicit CI: GitHub API rate limit (fail closed)"
    fi
    if [[ "$kind" == "403" ]]; then
      eternal_deploy_die "explicit CI: GitHub API HTTP 403 (fail closed)"
    fi
    eternal_deploy_die "explicit CI: GitHub API error (fail closed)"
  fi
  if ! jq -e . >/dev/null <"$out"; then
    rm -f "$out" "$err"
    eternal_deploy_die "explicit CI: invalid JSON from GitHub API (fail closed)"
  fi
  cat "$out"
  rm -f "$out" "$err"
}

eternal_ci_jobs_satisfy() {
  local json="$1"
  local name hits count bad
  local -a ok_names=()
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    hits="$(printf '%s' "$json" | jq -c --arg n "$name" '[.[] | select(.name == $n)]')"
    count="$(printf '%s' "$hits" | jq 'length')"
    bad="$(printf '%s' "$hits" | jq '[.[] | select(.status != "completed" or .conclusion != "success")] | length')"
    if [[ "${count:-0}" -lt 1 || "${bad:-1}" != "0" ]]; then
      eternal_deploy_log "explicit CI missing required success: $name"
      return 1
    fi
    ok_names+=("$name")
  done <<<"$REQUIRED_JOB_NAMES"
  for name in "${ok_names[@]}"; do
    eternal_deploy_log "explicit CI OK: $name"
  done
  return 0
}

eternal_ci_collect_runs() {
  local page=1 json count page_ids truncated=0
  RUN_IDS=()
  RUNS_TRUNCATED=0
  while [[ "$page" -le "$MAX_PAGES" ]]; do
    json="$(eternal_ci_api_json "repos/${REPO}/actions/workflows/${REQUIRED_WORKFLOW_FILE}/runs?head_sha=${SHA}&per_page=${PER_PAGE}&page=${page}")"
    count="$(printf '%s' "$json" | jq '.workflow_runs // [] | length')"
    page_ids="$(printf '%s' "$json" | jq -r \
      --arg sha "$SHA" \
      --arg path "$EXPECTED_WORKFLOW_PATH" \
      --arg repo "$REPO" \
      --arg wf_id "$WORKFLOW_ID" \
      '.workflow_runs // []
        | .[]
        | select(.head_sha == $sha)
        | select(.status == "completed")
        | select(.conclusion == "success")
        | select(.path == $path)
        | select(.repository.full_name == $repo)
        | select((.workflow_id | tostring) == $wf_id)
        | (.id | tostring)
        | select(test("^[0-9]+$"))')"
    if [[ -n "$page_ids" ]]; then
      while IFS= read -r run_id; do
        [[ -n "$run_id" ]] || continue
        RUN_IDS+=("$run_id")
      done <<<"$page_ids"
    fi
    if [[ "${count:-0}" -lt "$PER_PAGE" ]]; then
      break
    fi
    if [[ "$page" -eq "$MAX_PAGES" ]]; then
      truncated=1
      break
    fi
    page=$((page + 1))
  done
  RUNS_TRUNCATED="$truncated"
}

eternal_ci_load_jobs() {
  local run_id="$1"
  local page=1 json count page_jobs all='[]' truncated=0
  while [[ "$page" -le "$MAX_PAGES" ]]; do
    json="$(eternal_ci_api_json "repos/${REPO}/actions/runs/${run_id}/jobs?per_page=${PER_PAGE}&page=${page}")"
    count="$(printf '%s' "$json" | jq '.jobs // [] | length')"
    page_jobs="$(printf '%s' "$json" | jq -c '.jobs // []')"
    all="$(jq -cn --argjson a "$all" --argjson b "$page_jobs" '$a + $b')"
    if [[ "${count:-0}" -lt "$PER_PAGE" ]]; then
      printf '%s\n' "$all"
      return 0
    fi
    if [[ "$page" -eq "$MAX_PAGES" ]]; then
      truncated=1
      break
    fi
    page=$((page + 1))
  done
  if [[ "$truncated" -eq 1 ]]; then
    eternal_deploy_die "explicit CI: pagination cap reached (fail closed)"
  fi
  eternal_deploy_die "explicit CI: job page loop ended without a complete list (fail closed)"
}

eternal_deploy_log "verifying explicit CI for $SHA via $REQUIRED_WORKFLOW_FILE"

workflow_json="$(eternal_ci_api_json "repos/${REPO}/actions/workflows/${REQUIRED_WORKFLOW_FILE}")"
workflow_path="$(printf '%s' "$workflow_json" | jq -r '.path // empty')"
workflow_state="$(printf '%s' "$workflow_json" | jq -r '.state // empty')"
WORKFLOW_ID="$(printf '%s' "$workflow_json" | jq -r '.id // empty')"
[[ "$workflow_path" == "$EXPECTED_WORKFLOW_PATH" ]] \
  || eternal_deploy_die "explicit CI: workflow path mismatch (fail closed)"
[[ "$workflow_state" == "active" ]] \
  || eternal_deploy_die "explicit CI: workflow is not active (fail closed)"
[[ "$WORKFLOW_ID" =~ ^[0-9]+$ ]] \
  || eternal_deploy_die "explicit CI: workflow id missing (fail closed)"

eternal_ci_collect_runs
if [[ "${#RUN_IDS[@]}" -eq 0 ]]; then
  if [[ "$RUNS_TRUNCATED" -eq 1 ]]; then
    eternal_deploy_die "explicit CI: pagination cap reached (fail closed)"
  fi
  eternal_deploy_die "explicit CI: no successful ${REQUIRED_WORKFLOW_FILE} workflow run for $SHA"
fi

matched=0
for run_id in "${RUN_IDS[@]}"; do
  [[ "$run_id" =~ ^[0-9]+$ ]] || eternal_deploy_die "explicit CI: run id refused (fail closed)"
  jobs_json="$(eternal_ci_load_jobs "$run_id")"
  if eternal_ci_jobs_satisfy "$jobs_json"; then
    matched=1
    break
  fi
done

if [[ "$matched" -ne 1 ]]; then
  if [[ "$RUNS_TRUNCATED" -eq 1 ]]; then
    eternal_deploy_die "explicit CI: pagination cap reached (fail closed)"
  fi
  eternal_deploy_die "explicit CI: one or more required jobs not successful for $SHA"
fi

eternal_deploy_log "explicit CI PASS for $SHA"
printf 'CI_OK sha=%s workflow=%s\n' "$SHA" "$REQUIRED_WORKFLOW_FILE"
