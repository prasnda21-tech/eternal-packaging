#!/usr/bin/env bash
# Focused synthetic proofs for exact-SHA Actions CI verification. No network.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERIFY="$ROOT/scripts/deploy/verify_ci_for_sha.sh"
TRIGGER="$ROOT/scripts/deploy/trigger_github_actions.sh"
SHA="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OTHER="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
REPO_NAME="example/repo"
WF="reconciliation-validation.yml"
WF_PATH=".github/workflows/${WF}"
LIVE_REPO="prasnda21-tech/eternal-packaging"
PASS=0
FAIL=0

log() { printf '%s\n' "$*"; }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then log "PASS: $label"; PASS=$((PASS + 1))
  else log "FAIL: $label expected=[$expected] actual=[$actual]"; FAIL=$((FAIL + 1)); fi
}
assert_true() {
  local label="$1"; shift
  if "$@"; then log "PASS: $label"; PASS=$((PASS + 1)); else log "FAIL: $label"; FAIL=$((FAIL + 1)); fi
}
assert_false() {
  local label="$1"; shift
  if "$@"; then log "FAIL: $label (expected failure)"; FAIL=$((FAIL + 1)); else log "PASS: $label"; PASS=$((PASS + 1)); fi
}

WORKDIR="$(mktemp -d)"
MOCK="$WORKDIR/mock"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

reset_mock() {
  rm -rf "$MOCK"
  mkdir -p "$MOCK"
}

write_workflow() {
  local path="${1:-$WF_PATH}" state="${2:-active}"
  cat >"$MOCK/workflow.json" <<EOF
{"id":100,"path":"${path}","state":"${state}"}
EOF
}

write_runs() {
  local json="$1" page="${2:-1}"
  if [[ "$page" == "1" ]]; then
    printf '%s\n' "$json" >"$MOCK/workflow-runs.json"
  else
    printf '%s\n' "$json" >"$MOCK/workflow-runs-page-${page}.json"
  fi
}

run_obj() {
  local id="$1" sha="$2" conclusion="$3" path="${4:-$WF_PATH}" repo="${5:-$REPO_NAME}" status="${6:-completed}"
  jq -nc \
    --argjson id "$id" \
    --arg sha "$sha" \
    --arg conclusion "$conclusion" \
    --arg path "$path" \
    --arg repo "$repo" \
    --arg status "$status" \
    '{id:$id,workflow_id:100,head_sha:$sha,status:$status,conclusion:$conclusion,path:$path,repository:{full_name:$repo}}'
}

jobs_doc() {
  jq -nc --argjson jobs "$1" '{jobs:$jobs}'
}

required_jobs() {
  local fe="${1:-success}" stock="${2:-success}" be="${3:-success}" live="${4:-skipped}"
  jq -nc \
    --arg fe "$fe" --arg stock "$stock" --arg be "$be" --arg live "$live" \
    '[
      {name:"Backend focused tests",status:"completed",conclusion:$be},
      {name:"Backend stock compatibility (replica set)",status:"completed",conclusion:$stock},
      {name:"Frontend tests and build",status:"completed",conclusion:$fe},
      {name:"Backend live orders summary",status:"completed",conclusion:$live}
    ]'
}

seed_success() {
  local sha="${1:-$SHA}" repo="${2:-$REPO_NAME}" run_id="${3:-11}"
  reset_mock
  write_workflow
  write_runs "{\"workflow_runs\":[$(run_obj "$run_id" "$sha" success)]}"
  jobs_doc "$(required_jobs)" >"$MOCK/jobs-${run_id}.json"
  printf '403\n' >"$MOCK/checks-http-status"
}

run_verify() {
  local repo="$1" sha="$2"
  shift 2
  set +e
  VERIFY_OUT="$(env ETERNAL_GH_API_MOCK_DIR="$MOCK" "$@" "$VERIFY" "$repo" "$sha" 2>"$WORKDIR/err")"
  VERIFY_EC=$?
  set -e
  VERIFY_ERR="$(cat "$WORKDIR/err")"
}

assert_pass() {
  local label="$1"
  assert_eq "$label exit" "0" "$VERIFY_EC"
  assert_true "$label CI_OK" grep -qx "CI_OK sha=${SHA} workflow=${WF}" <<<"$VERIFY_OUT"
}

assert_fail_closed() {
  local label="$1" needle="$2"
  assert_eq "$label exit" "1" "$VERIFY_EC"
  assert_false "$label has no CI_OK" grep -q 'CI_OK' <<<"$VERIFY_OUT"
  assert_true "$label fail closed" grep -q "$needle" <<<"$VERIFY_ERR"
}

# Exact SHA + workflow success + three required jobs + optional skipped.
seed_success
run_verify "$REPO_NAME" "$SHA"
assert_pass "exact SHA success"
assert_false "success path does not call Checks API" \
  grep -q 'check-runs' "$MOCK/calls.log"
assert_true "success path reads Actions jobs for the matched run" \
  grep -q "/actions/runs/11/jobs" "$MOCK/calls.log"

# SHA mismatch.
seed_success "$OTHER"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "SHA mismatch" "no successful ${WF} workflow run"

# Workflow failure and cancellation.
seed_success
write_runs "{\"workflow_runs\":[$(run_obj 11 "$SHA" failure)]}"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "workflow failure" "no successful ${WF} workflow run"

seed_success
write_runs "{\"workflow_runs\":[$(run_obj 11 "$SHA" cancelled)]}"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "workflow cancelled" "no successful ${WF} workflow run"

# Required job failed, skipped, or missing. Optional skipped stays on the pass path above.
seed_success
jobs_doc "$(required_jobs failure)" >"$MOCK/jobs-11.json"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "required job failed" "required jobs not successful"

seed_success
jobs_doc "$(required_jobs skipped)" >"$MOCK/jobs-11.json"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "required job skipped" "required jobs not successful"

seed_success
jq -nc '{jobs:[{name:"Backend focused tests",status:"completed",conclusion:"success"}]}' >"$MOCK/jobs-11.json"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "required job missing" "required jobs not successful"

# Incomplete required job.
seed_success
jq -nc '{jobs:[
  {name:"Backend focused tests",status:"in_progress",conclusion:null},
  {name:"Backend stock compatibility (replica set)",status:"completed",conclusion:"success"},
  {name:"Frontend tests and build",status:"completed",conclusion:"success"}
]}' >"$MOCK/jobs-11.json"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "required job incomplete" "required jobs not successful"

# Wrong workflow metadata and a success run that belongs to another workflow file.
seed_success
write_workflow ".github/workflows/deploy-production.yml"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "wrong workflow file" "workflow path mismatch"

seed_success
write_runs "{\"workflow_runs\":[$(run_obj 11 "$SHA" success ".github/workflows/ci.yml")]}"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "run from another workflow" "no successful ${WF} workflow run"

# API 403, 429, generic error, and timeout. Fail closed, no CI_OK.
seed_success
printf '403\n' >"$MOCK/runs-http-status"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "runs API 403" "HTTP 403 (fail closed)"

seed_success
printf '403\n' >"$MOCK/jobs-http-status"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "jobs API 403" "HTTP 403 (fail closed)"

seed_success
printf '429\n' >"$MOCK/runs-http-status"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "runs API rate limit" "rate limit (fail closed)"

seed_success
printf '500\n' >"$MOCK/workflow-http-status"
run_verify "$REPO_NAME" "$SHA"
assert_fail_closed "workflow API error" "GitHub API error (fail closed)"

seed_success
printf 'runs\n' >"$MOCK/hang-kinds"
printf '30\n' >"$MOCK/hang-sec"
run_verify "$REPO_NAME" "$SHA" ETERNAL_CI_HTTP_TIMEOUT_SEC=1 ETERNAL_CI_HTTP_KILL_AFTER_SEC=1
assert_fail_closed "API timeout" "timeout (fail closed)"

# Job list pagination: required jobs are only on page 2.
seed_success
jq -nc '{jobs:[range(0;100) | {name:("pad-" + (tostring)),status:"completed",conclusion:"success"}]}' >"$MOCK/jobs-11.json"
jobs_doc "$(required_jobs)" >"$MOCK/jobs-11-page-2.json"
run_verify "$REPO_NAME" "$SHA"
assert_pass "paginated job list"
assert_true "job page 2 was requested" grep -q '/jobs?per_page=100&page=2' "$MOCK/calls.log"

# Runs pagination: the exact success run is on page 2, not the latest page of other SHAs.
reset_mock
write_workflow
jq -nc --arg other "$OTHER" --arg path "$WF_PATH" --arg repo "$REPO_NAME" \
  '{workflow_runs:[range(0;100) | {id:(1000+.),workflow_id:100,head_sha:$other,status:"completed",conclusion:"success",path:$path,repository:{full_name:$repo}}]}' \
  >"$MOCK/workflow-runs.json"
write_runs "{\"workflow_runs\":[$(run_obj 21 "$SHA" success)]}" 2
jobs_doc "$(required_jobs)" >"$MOCK/jobs-21.json"
printf '403\n' >"$MOCK/checks-http-status"
run_verify "$REPO_NAME" "$SHA"
assert_pass "paginated workflow runs"
assert_eq "jobs fetched only for the exact page-2 run" "1" \
  "$(grep -c '/actions/runs/21/jobs' "$MOCK/calls.log")"
other_job_calls="$(grep -E '/actions/runs/[0-9]+/jobs' "$MOCK/calls.log" | grep -v '/actions/runs/21/jobs' || true)"
assert_eq "jobs not fetched for a different SHA run" "" "$other_job_calls"

# Pagination cap refuses to guess past the last fetched page.
run_verify "$REPO_NAME" "$SHA" ETERNAL_CI_MAX_PAGES=1
assert_fail_closed "runs pagination cap" "pagination cap reached (fail closed)"

# Multiple runs: ignore other SHA, failed run, and a success run with a bad job.
# Accept only the exact success run whose own jobs pass.
reset_mock
write_workflow
jq -nc \
  --arg sha "$SHA" --arg other "$OTHER" --arg path "$WF_PATH" --arg repo "$REPO_NAME" \
  --argjson wrong "$(run_obj 12 "$OTHER" success)" \
  --argjson bad "$(run_obj 10 "$SHA" success)" \
  --argjson failed "$(run_obj 9 "$SHA" failure)" \
  --argjson good "$(run_obj 11 "$SHA" success)" \
  '{workflow_runs:[$wrong,$bad,$failed,$good]}' >"$MOCK/workflow-runs.json"
jobs_doc "$(required_jobs)" >"$MOCK/jobs-12.json"
jobs_doc "$(required_jobs failure)" >"$MOCK/jobs-10.json"
jobs_doc "$(required_jobs)" >"$MOCK/jobs-9.json"
jobs_doc "$(required_jobs success success success skipped)" >"$MOCK/jobs-11.json"
printf '403\n' >"$MOCK/checks-http-status"
run_verify "$REPO_NAME" "$SHA"
assert_pass "multiple runs use the exact success run"
assert_true "checked the exact SHA run with a failed job" grep -q '/actions/runs/10/jobs' "$MOCK/calls.log"
assert_true "checked the exact SHA success run" grep -q '/actions/runs/11/jobs' "$MOCK/calls.log"
assert_false "did not read jobs for a different SHA" grep -q '/actions/runs/12/jobs' "$MOCK/calls.log"
assert_false "did not read jobs for a failed workflow run" grep -q '/actions/runs/9/jobs' "$MOCK/calls.log"
assert_false "multiple-run pass does not call Checks API" grep -q 'check-runs' "$MOCK/calls.log"

# Regression: plan deploy still emits CI_OK and PLAN_OK, and does not POST.
reset_mock
write_workflow
write_runs "{\"workflow_runs\":[$(run_obj 11 "$SHA" success "$WF_PATH" "$LIVE_REPO")]}"
jobs_doc "$(required_jobs)" >"$MOCK/jobs-11.json"
printf '{"login":"prasnda21-tech"}\n' >"$MOCK/actor.json"
printf '{"ancestor_ok":true}\n' >"$MOCK/ancestry.json"
printf '403\n' >"$MOCK/checks-http-status"
: >"$WORKDIR/dispatch.log"
set +e
PLAN_OUT="$(ETERNAL_GH_API_MOCK_DIR="$MOCK" ETERNAL_TRIGGER_DISPATCH_LOG="$WORKDIR/dispatch.log" \
  "$TRIGGER" plan deploy --sha "$SHA" --confirm-sha "$SHA" 2>"$WORKDIR/plan.err")"
PLAN_EC=$?
set -e
assert_eq "plan deploy contract exit" "0" "$PLAN_EC"
assert_true "plan deploy prints CI_OK" grep -q "CI_OK sha=${SHA} workflow=${WF}" <<<"$PLAN_OUT"
assert_true "plan deploy prints PLAN_OK" grep -q 'PLAN_OK' <<<"$PLAN_OUT"
assert_true "plan deploy POST=no" grep -q 'POST=no' <<<"$PLAN_OUT"
assert_eq "plan deploy wrote no dispatch log" "" "$(cat "$WORKDIR/dispatch.log")"
assert_false "plan deploy does not call Checks API" grep -q 'check-runs' "$MOCK/calls.log"

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
