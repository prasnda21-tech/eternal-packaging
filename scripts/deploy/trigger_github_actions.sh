#!/usr/bin/env bash
# Cursor/Owner helper to plan or (later) execute GitHub Actions deploy/probe.
#
# Default mode is plan: NEVER POSTs. Execute requires explicit --execute and
# an Owner-authorized credential with Actions write (not assumed for cursor[bot]).
#
# Usage:
#   trigger_github_actions.sh plan  probe
#   trigger_github_actions.sh plan  deploy --sha <40> --confirm-sha <40>
#   trigger_github_actions.sh execute probe --execute
#   trigger_github_actions.sh execute deploy --sha <40> --confirm-sha <40> --execute
#
# Env:
#   ETERNAL_GH_REPO                 default prasnda21-tech/eternal-packaging
#   ETERNAL_GH_API_MOCK_DIR         synthetic mocks (no network)
#   ETERNAL_GH_BIN                  override gh binary (tests/stubs)
#   ETERNAL_TRIGGER_DISPATCH_LOG    records mock POSTs for tests
#   ETERNAL_DEPLOY_SHARED_SECRET    execute+deploy only (env → JSON body; never argv/logs)
#   GH_TOKEN / ETERNAL_GH_TOKEN     write-capable token from env secret store
#   ETERNAL_TRIGGER_POLL_TIMEOUT_SEC   wall-clock budget to correlate run URL (default 45)
#   ETERNAL_TRIGGER_POLL_INTERVAL_SEC  sleep between correlation GETs (default 2; 0 = no sleep)
#   ETERNAL_TRIGGER_POLL_MAX_ATTEMPTS  optional hard cap (required for fast zero-sleep mocks)
#   ETERNAL_TRIGGER_WAIT_TIMEOUT_SEC   wall-clock budget after DISPATCH_OK for workflow completion (default 900)
#   ETERNAL_TRIGGER_WAIT_INTERVAL_SEC  sleep between run-status GETs (default 5; 0 = no sleep)
#   ETERNAL_TRIGGER_WAIT_MAX_ATTEMPTS  optional hard cap for completion polls (tests)
#   ETERNAL_TRIGGER_HTTP_TIMEOUT_SEC   per-request gh api bound (default 30)
#   ETERNAL_TRIGGER_HTTP_KILL_AFTER_SEC  SIGKILL after TERM on hung child (default 2)
#   ETERNAL_TRIGGER_FORCE_HTTP_TIMEOUT   when set with mock dir, apply timeout to mock calls (tests)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

REPO="${ETERNAL_GH_REPO:-prasnda21-tech/eternal-packaging}"
MODE="plan"
ACTION=""
SHA=""
CONFIRM_SHA=""
REQUEST_ID=""
DO_EXECUTE=0
ALLOW_MIGRATIONS=0
ACK_REPLACE_LIVE_FRONTEND=0
DISPATCH_REF="main"
GH_BIN="${ETERNAL_GH_BIN:-gh}"

# Polling: only GET is retried. POST happens at most once per execute.
POLL_TIMEOUT_SEC="${ETERNAL_TRIGGER_POLL_TIMEOUT_SEC:-45}"
POLL_INTERVAL_SEC="${ETERNAL_TRIGGER_POLL_INTERVAL_SEC:-2}"
POLL_MAX_ATTEMPTS="${ETERNAL_TRIGGER_POLL_MAX_ATTEMPTS:-}"
WAIT_TIMEOUT_SEC="${ETERNAL_TRIGGER_WAIT_TIMEOUT_SEC:-900}"
WAIT_INTERVAL_SEC="${ETERNAL_TRIGGER_WAIT_INTERVAL_SEC:-5}"
WAIT_MAX_ATTEMPTS="${ETERNAL_TRIGGER_WAIT_MAX_ATTEMPTS:-}"
HTTP_TIMEOUT_SEC="${ETERNAL_TRIGGER_HTTP_TIMEOUT_SEC:-30}"
HTTP_KILL_AFTER_SEC="${ETERNAL_TRIGGER_HTTP_KILL_AFTER_SEC:-2}"

if [[ $# -lt 2 ]]; then
  eternal_deploy_die "usage: $0 plan|execute probe|deploy [options]"
fi
MODE="$1"
ACTION="$2"
shift 2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="${2:-}"; shift 2 ;;
    --confirm-sha) CONFIRM_SHA="${2:-}"; shift 2 ;;
    --request-id) REQUEST_ID="${2:-}"; shift 2 ;;
    --execute) DO_EXECUTE=1; shift ;;
    --allow-migrations) ALLOW_MIGRATIONS=1; shift ;;
    --ack-replace-live-frontend) ACK_REPLACE_LIVE_FRONTEND=1; shift ;;
    -h|--help)
      cat <<'EOF'
Modes: plan (default, no POST) | execute (requires --execute)
Actions: probe | deploy
Deploy requires --sha and --confirm-sha (identical 40-char hex). Never auto-picks main tip.
Optional: --ack-replace-live-frontend (first cutover when pure SHA replaces live selective-merge FE).
EOF
      exit 0
      ;;
    *) eternal_deploy_die "unknown arg: $1" ;;
  esac
done

[[ "$MODE" == "plan" || "$MODE" == "execute" ]] || eternal_deploy_die "mode must be plan|execute"
[[ "$ACTION" == "probe" || "$ACTION" == "deploy" ]] || eternal_deploy_die "action must be probe|deploy"

if [[ "$MODE" == "execute" && "$DO_EXECUTE" != "1" ]]; then
  eternal_deploy_die "execute mode requires explicit --execute (refusing accidental POST)"
fi

# Prefer env-store token without printing it.
if [[ -n "${ETERNAL_GH_TOKEN:-}" ]]; then
  export GH_TOKEN="$ETERNAL_GH_TOKEN"
fi

WORKFLOW_FILE="deploy-production.yml"
if [[ "$ACTION" == "probe" ]]; then
  WORKFLOW_FILE="deploy-probe.yml"
fi

eternal_is_positive_int() {
  [[ "${1:-}" =~ ^[1-9][0-9]*$ ]]
}

eternal_is_nonneg_int() {
  [[ "${1:-}" =~ ^(0|[1-9][0-9]*)$ ]]
}

eternal_require_positive_int_cfg() {
  local name="$1" val="$2"
  eternal_is_positive_int "$val" \
    || eternal_deploy_die "${name} must be a positive integer; refused before dispatch"
}

eternal_require_nonneg_int_cfg() {
  local name="$1" val="$2"
  eternal_is_nonneg_int "$val" \
    || eternal_deploy_die "${name} must be a non-negative integer; refused before dispatch"
}

eternal_validate_timeout_configs() {
  eternal_require_positive_int_cfg "ETERNAL_TRIGGER_HTTP_TIMEOUT_SEC" "$HTTP_TIMEOUT_SEC"
  eternal_require_positive_int_cfg "ETERNAL_TRIGGER_HTTP_KILL_AFTER_SEC" "$HTTP_KILL_AFTER_SEC"
  eternal_require_positive_int_cfg "ETERNAL_TRIGGER_POLL_TIMEOUT_SEC" "$POLL_TIMEOUT_SEC"
  eternal_require_nonneg_int_cfg "ETERNAL_TRIGGER_POLL_INTERVAL_SEC" "$POLL_INTERVAL_SEC"
  eternal_require_positive_int_cfg "ETERNAL_TRIGGER_WAIT_TIMEOUT_SEC" "$WAIT_TIMEOUT_SEC"
  eternal_require_nonneg_int_cfg "ETERNAL_TRIGGER_WAIT_INTERVAL_SEC" "$WAIT_INTERVAL_SEC"
  if [[ -n "${POLL_MAX_ATTEMPTS}" ]]; then
    eternal_require_positive_int_cfg "ETERNAL_TRIGGER_POLL_MAX_ATTEMPTS" "$POLL_MAX_ATTEMPTS"
  fi
  if [[ -n "${WAIT_MAX_ATTEMPTS}" ]]; then
    eternal_require_positive_int_cfg "ETERNAL_TRIGGER_WAIT_MAX_ATTEMPTS" "$WAIT_MAX_ATTEMPTS"
  fi
}

eternal_require_timeout_tool() {
  command -v timeout >/dev/null 2>&1 \
    || eternal_deploy_die "timeout(1) required for bounded GitHub API calls; not found in PATH; refused before dispatch"
}

# request_id must be a whole parser token: [A-Za-z0-9._-]+
eternal_validate_request_id() {
  local id="$1"
  if [[ -z "$id" ]]; then
    eternal_deploy_die "request_id empty; refused before network/POST"
  fi
  if [[ ! "$id" =~ ^[A-Za-z0-9._-]+$ ]]; then
    eternal_deploy_die "request_id invalid (allowed ^[A-Za-z0-9._-]+\$); refused before network/POST"
  fi
}

# Run command with TERM then SIGKILL (-k). Exit 124 = timeout/kill path.
# Errexit save/restore is local so nested helpers stay reentrant.
eternal_run_with_http_timeout() {
  local secs="$1"
  shift
  local ec restore_e=0
  eternal_is_positive_int "$secs" \
    || eternal_deploy_die "internal: http call timeout must be a positive integer"
  [[ $- == *e* ]] && restore_e=1
  set +e
  # -k: if child ignores TERM, send KILL after kill-after seconds so the process tree stops.
  timeout -k "${HTTP_KILL_AFTER_SEC}" "${secs}" "$@"
  ec=$?
  if [[ "$restore_e" == "1" ]]; then set -e; else set +e; fi
  if [[ "$ec" -eq 124 || "$ec" -eq 137 ]]; then
    return 124
  fi
  return "$ec"
}

eternal_http_timeout_applies() {
  if [[ -z "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    return 0
  fi
  if [[ -n "${ETERNAL_TRIGGER_FORCE_HTTP_TIMEOUT:-}" ]]; then
    return 0
  fi
  return 1
}

# Hang helper for mocks: optionally ignore TERM to prove -k SIGKILL ends the wait.
eternal_mock_hang() {
  local secs="$1"
  local ignore_term="${2:-0}"
  local call_timeout="${ETERNAL_GH_CALL_TIMEOUT:-$HTTP_TIMEOUT_SEC}"
  if eternal_http_timeout_applies; then
    if [[ "$ignore_term" == "1" ]]; then
      eternal_run_with_http_timeout "$call_timeout" \
        bash -c 'trap "" TERM; sleep "$1"' bash "$secs"
      return $?
    fi
    eternal_run_with_http_timeout "$call_timeout" sleep "$secs"
    return $?
  fi
  if [[ "$ignore_term" == "1" ]]; then
    bash -c 'trap "" TERM; sleep "$1"' bash "$secs"
    return $?
  fi
  sleep "$secs"
  return 0
}

eternal_gh_api_mock() {
  local method="${1:-GET}"
  local path="$2"
  shift 2 || true
  case "$path" in
    */actions/workflows/*/dispatches)
      printf 'POST %s' "$path" >>"${ETERNAL_TRIGGER_DISPATCH_LOG:-/dev/null}"
      if [[ "$*" == *secret_in_argv=no* ]]; then
        printf ' input=stdin-json secret_in_argv=no' >>"${ETERNAL_TRIGGER_DISPATCH_LOG:-/dev/null}"
      fi
      printf '\n' >>"${ETERNAL_TRIGGER_DISPATCH_LOG:-/dev/null}"
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/dispatch-hang-sec" ]]; then
        local hs ignore=0 hec restore_e=0
        hs="$(tr -d '[:space:]' <"${ETERNAL_GH_API_MOCK_DIR}/dispatch-hang-sec")"
        [[ -f "${ETERNAL_GH_API_MOCK_DIR}/dispatch-hang-ignore-term" ]] && ignore=1
        [[ $- == *e* ]] && restore_e=1
        set +e
        eternal_mock_hang "$hs" "$ignore"
        hec=$?
        if [[ "$restore_e" == "1" ]]; then set -e; else set +e; fi
        if [[ "$hec" -eq 124 ]]; then
          echo "{\"message\":\"mock dispatch HTTP timeout\",\"status\":\"timeout\"}" >&2
          return 124
        fi
        if [[ "$hec" -ne 0 ]]; then
          return "$hec"
        fi
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/dispatch-status" ]]; then
        local st
        st="$(cat "${ETERNAL_GH_API_MOCK_DIR}/dispatch-status")"
        if [[ "$st" != "204" && "$st" != "0" && "$st" != "ok" ]]; then
          echo "{\"message\":\"Resource not accessible by integration\",\"status\":\"$st\"}" >&2
          return 1
        fi
      fi
      printf '0\n' >"${ETERNAL_GH_API_MOCK_DIR}/.runs-get-count"
      printf '0\n' >"${ETERNAL_GH_API_MOCK_DIR}/.run-detail-get-count"
      return 0
      ;;
    */actions/workflows/*/runs*)
      local n rst hs ignore=0
      n="$(( $(cat "${ETERNAL_GH_API_MOCK_DIR}/.runs-get-count" 2>/dev/null || echo 0) + 1 ))"
      printf '%s\n' "$n" >"${ETERNAL_GH_API_MOCK_DIR}/.runs-get-count"
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-poll-${n}-hang-sec" ]]; then
        local hec restore_e=0
        hs="$(tr -d '[:space:]' <"${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-poll-${n}-hang-sec")"
        [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-poll-${n}-hang-ignore-term" ]] && ignore=1
        [[ $- == *e* ]] && restore_e=1
        set +e
        eternal_mock_hang "$hs" "$ignore"
        hec=$?
        if [[ "$restore_e" == "1" ]]; then set -e; else set +e; fi
        if [[ "$hec" -eq 124 ]]; then
          echo "{\"message\":\"mock runs list HTTP timeout\",\"status\":\"timeout\"}" >&2
          return 124
        fi
        if [[ "$hec" -ne 0 ]]; then
          return "$hec"
        fi
      elif [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-hang-sec" ]]; then
        local hec restore_e=0
        hs="$(tr -d '[:space:]' <"${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-hang-sec")"
        [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-hang-ignore-term" ]] && ignore=1
        [[ $- == *e* ]] && restore_e=1
        set +e
        eternal_mock_hang "$hs" "$ignore"
        hec=$?
        if [[ "$restore_e" == "1" ]]; then set -e; else set +e; fi
        if [[ "$hec" -eq 124 ]]; then
          echo "{\"message\":\"mock runs list HTTP timeout\",\"status\":\"timeout\"}" >&2
          return 124
        fi
        if [[ "$hec" -ne 0 ]]; then
          return "$hec"
        fi
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-poll-${n}-http-status" ]]; then
        rst="$(cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-poll-${n}-http-status")"
      elif [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-http-status" ]]; then
        rst="$(cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-http-status")"
      else
        rst="200"
      fi
      if [[ "$rst" != "200" && "$rst" != "ok" && "$rst" != "0" ]]; then
        echo "{\"message\":\"mock runs list API error\",\"status\":\"$rst\"}" >&2
        return 1
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-poll-${n}.json" ]]; then
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-poll-${n}.json"
        return 0
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs.json" ]]; then
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs.json"
        return 0
      fi
      eternal_deploy_die "mock API missing trigger-runs.json for: $method $path"
      ;;
    */actions/runs/[0-9]*/logs|*/actions/runs/[0-9]*/logs/*)
      # Mock log body (plain text). Live path uses `gh run view --log`.
      local log_id
      log_id="$(printf '%s' "$path" | sed -E 's|.*/actions/runs/([0-9]+)/logs.*|\1|')"
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-${log_id}-log.txt" ]]; then
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-${log_id}-log.txt"
        return 0
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-log.txt" ]]; then
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-log.txt"
        return 0
      fi
      echo "{\"message\":\"mock run logs missing\",\"status\":\"404\"}" >&2
      return 1
      ;;
    */actions/runs/[0-9]*)
      local rid n rst
      rid="$(printf '%s' "$path" | sed -E 's|.*/actions/runs/([0-9]+).*|\1|')"
      n="$(( $(cat "${ETERNAL_GH_API_MOCK_DIR}/.run-detail-get-count" 2>/dev/null || echo 0) + 1 ))"
      printf '%s\n' "$n" >"${ETERNAL_GH_API_MOCK_DIR}/.run-detail-get-count"
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-detail-poll-${n}-http-status" ]]; then
        rst="$(cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-detail-poll-${n}-http-status")"
        if [[ "$rst" != "200" && "$rst" != "ok" && "$rst" != "0" ]]; then
          echo "{\"message\":\"mock run detail API error\",\"status\":\"$rst\"}" >&2
          return 1
        fi
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-detail-poll-${n}.json" ]]; then
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-detail-poll-${n}.json"
        return 0
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-${rid}-poll-${n}.json" ]]; then
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-${rid}-poll-${n}.json"
        return 0
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-${rid}.json" ]]; then
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-${rid}.json"
        return 0
      fi
      if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-detail.json" ]]; then
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-run-detail.json"
        return 0
      fi
      eternal_deploy_die "mock API missing trigger-run detail JSON for: $method $path"
      ;;
  esac
  if [[ "$method" == "GET" && -f "${ETERNAL_GH_API_MOCK_DIR}/actor.json" && "$path" == *"graphql"* ]]; then
    cat "${ETERNAL_GH_API_MOCK_DIR}/actor.json"
    return 0
  fi
  eternal_deploy_die "mock API not configured for: $method $path"
}

eternal_gh_api() {
  local method="${1:-GET}"
  local path="$2"
  shift 2 || true
  local call_timeout="${ETERNAL_GH_CALL_TIMEOUT:-$HTTP_TIMEOUT_SEC}"
  local ec restore_e=0
  [[ $- == *e* ]] && restore_e=1
  set +e
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    eternal_gh_api_mock "$method" "$path" "$@"
    ec=$?
  elif [[ "$method" == "GET" ]]; then
    eternal_run_with_http_timeout "$call_timeout" "$GH_BIN" api "$path"
    ec=$?
  else
    eternal_run_with_http_timeout "$call_timeout" "$GH_BIN" api --method "$method" "$path" "$@"
    ec=$?
  fi
  if [[ "$restore_e" == "1" ]]; then set -e; else set +e; fi
  return "$ec"
}

report_actor() {
  local login="unknown"
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" && -f "${ETERNAL_GH_API_MOCK_DIR}/actor.json" ]]; then
    login="$(jq -r '.login // .data.viewer.login // "unknown"' "${ETERNAL_GH_API_MOCK_DIR}/actor.json")"
  else
    set +e
    login="$(eternal_run_with_http_timeout "$HTTP_TIMEOUT_SEC" "$GH_BIN" api graphql \
      -f query='query { viewer { login } }' --jq '.data.viewer.login' 2>/dev/null)"
    local aec=$?
    set -e
    if [[ "$aec" -ne 0 || -z "$login" ]]; then
      login="unknown"
    fi
  fi
  printf 'AUTH_ACTOR=%s\n' "$login"
  printf 'AUTH_NOTE=Actions write is separate from read; cursor[bot] read success does not prove dispatch permission\n'
}

validate_deploy_target() {
  eternal_require_sha40 "$SHA"
  eternal_require_sha40 "$CONFIRM_SHA"
  [[ "$SHA" == "$CONFIRM_SHA" ]] || eternal_deploy_die "confirm-sha must exactly equal sha (no auto main tip)"

  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    local anc
    anc="$(jq -r '.ancestor_ok // false' "${ETERNAL_GH_API_MOCK_DIR}/ancestry.json")"
    [[ "$anc" == "true" ]] || eternal_deploy_die "target SHA is not on main (ancestry check)"
  else
    local status
    set +e
    status="$(eternal_run_with_http_timeout "$HTTP_TIMEOUT_SEC" "$GH_BIN" api \
      "repos/${REPO}/compare/main...${SHA}" --jq '.status' 2>/dev/null)"
    local cec=$?
    set -e
    if [[ "$cec" -ne 0 || -z "$status" ]]; then
      status="missing"
    fi
    [[ "$status" == "identical" || "$status" == "behind" ]] \
      || eternal_deploy_die "target SHA not verified on main (compare status=$status)"
  fi

  bash "$SCRIPT_DIR/verify_ci_for_sha.sh" "$REPO" "$SHA"
}

# Extract whole request_id token from run-name/display_title (not prefix-contains).
# Returns: 0 + "RUN_ID<TAB>HTML_URL" | 1 none | 2 ambiguous | 3 API error | 4 HTTP timeout
find_correlated_run() {
  local want_request_id="$1"
  local runs_json
  [[ -n "$want_request_id" ]] || return 1

  set +e
  runs_json="$(eternal_gh_api GET "repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/runs?event=workflow_dispatch&branch=${DISPATCH_REF}&per_page=20")"
  local api_ec=$?
  set -e
  if [[ "$api_ec" -eq 124 ]]; then
    return 4
  fi
  if [[ "$api_ec" -ne 0 ]]; then
    return 3
  fi

  local rows
  rows="$(printf '%s' "$runs_json" | jq -r --arg id "$want_request_id" --arg ref "$DISPATCH_REF" '
    def title:
      (.display_title // .name // "");
    def rid:
      (title
        | match("(?:^|[^A-Za-z0-9._-])request_id=([A-Za-z0-9._-]+)(?:$|[^A-Za-z0-9._-])")
        | .captures[0].string) // empty;
    [.workflow_runs[]?
      | select((.event // "workflow_dispatch") == "workflow_dispatch")
      | select((.head_branch // $ref) == $ref)
      | select(rid == $id)
      | "\(.id)\t\(.html_url)"] | .[]
  ' 2>/dev/null || true)"

  local count
  count="$(printf '%s\n' "$rows" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [[ "$count" == "1" ]]; then
    printf '%s\n' "$rows"
    return 0
  fi
  if [[ "${count:-0}" -gt 1 ]]; then
    eternal_deploy_log "ambiguous correlated runs ($count) for request_id=${want_request_id}"
    printf '%s\n' "$rows" >&2
    return 2
  fi
  return 1
}

eternal_wait_max_attempts() {
  if [[ -n "${WAIT_MAX_ATTEMPTS}" ]]; then
    printf '%s\n' "${WAIT_MAX_ATTEMPTS}"
    return 0
  fi
  if [[ "${WAIT_INTERVAL_SEC}" -le 0 ]]; then
    printf '%s\n' "${WAIT_TIMEOUT_SEC}"
    return 0
  fi
  printf '%s\n' "$(( (WAIT_TIMEOUT_SEC / WAIT_INTERVAL_SEC) + 1 ))"
}

eternal_wait_sleep() {
  local secs="${1:-$WAIT_INTERVAL_SEC}"
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    return 0
  fi
  if ! eternal_is_positive_int "$secs"; then
    return 0
  fi
  sleep "$secs"
}

# Fetch run detail JSON: {id,status,conclusion,html_url}. Exit 0 ok, 3 API err, 4 timeout.
eternal_fetch_run_detail() {
  local run_id="$1"
  eternal_gh_api GET "repos/${REPO}/actions/runs/${run_id}"
}

# Fetch plain-text job logs (mock file or gh run view --log). Never prints secrets.
eternal_fetch_run_logs() {
  local run_id="$1"
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    eternal_gh_api GET "repos/${REPO}/actions/runs/${run_id}/logs"
    return $?
  fi
  local call_timeout="${ETERNAL_GH_CALL_TIMEOUT:-$HTTP_TIMEOUT_SEC}"
  eternal_run_with_http_timeout "$call_timeout" \
    "$GH_BIN" run view "$run_id" --repo "$REPO" --log
}

# Print a short redacted evidence summary (never dumps full logs / secrets).
eternal_print_log_evidence_summary() {
  local log_body="$1"
  local line redacted count=0
  printf 'LOG_EVIDENCE_BEGIN\n'
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    if [[ "$line" == *"PRIVATE KEY"* ]] \
      || [[ "$line" == *"DEPLOY_SSH"* ]] \
      || [[ "$line" == *"ETERNAL_DEPLOY_SHARED_SECRET"* ]]; then
      continue
    fi
    if [[ "$line" == *"DEPLOY_OK"* ]] \
      || [[ "$line" == *"ROLLBACK_PASS"* ]] \
      || [[ "$line" == *"ROLLBACK_FAILED"* ]] \
      || [[ "$line" == *"ETERNAL_GATE_OK"* ]] \
      || [[ "$line" == *"[eternal-deploy]"* ]]; then
      redacted="$(eternal_redact_line "$line")"
      printf '%s\n' "$redacted"
      count=$((count + 1))
      if [[ "$count" -ge 40 ]]; then
        break
      fi
    fi
  done <<<"$log_body"
  printf 'LOG_EVIDENCE_END lines=%s\n' "$count"
}

# Classify runner evidence from logs fetched for a single correlated run_id.
# DEPLOY_OK must match the runner line shape (DEPLOY_OK sha=<40-hex>), not a loose
# substring. Returns 0 only for deploy/probe success markers (caller still requires
# workflow conclusion=success before treating the overall execute as PASS).
eternal_classify_runner_evidence() {
  local log_body="$1"
  local action="$2"
  if [[ "$action" == "probe" ]]; then
    if grep -qE 'ETERNAL_GATE_OK[[:space:]]+probe=pass' <<<"$log_body"; then
      printf 'RUNNER_PROBE_SUCCESS\n'
      return 0
    fi
    printf 'RESULT_UNKNOWN\n'
    return 1
  fi
  # Runner prints: DEPLOY_OK sha=<40-hex> backend_restarted=... (gh --log may prefix columns).
  if grep -qE 'DEPLOY_OK[[:space:]]+sha=[0-9a-f]{40}' <<<"$log_body"; then
    printf 'RUNNER_DEPLOY_SUCCESS\n'
    return 0
  fi
  if grep -qE '(^|[[:space:]])ROLLBACK_FAILED([[:space:]]|$)' <<<"$log_body"; then
    printf 'RUNNER_ROLLBACK_FAILED\n'
    return 1
  fi
  if grep -qE '(^|[[:space:]])ROLLBACK_PASS([[:space:]]|$)' <<<"$log_body"; then
    printf 'RUNNER_ROLLBACK_PASS\n'
    return 1
  fi
  printf 'RESULT_UNKNOWN\n'
  return 1
}

# After DISPATCH_OK: bounded wait for workflow completion + runner evidence.
# Does not re-POST. GitHub conclusion alone is never enough for deploy success.
wait_and_report_workflow_run() {
  local run_id="$1"
  local run_url="$2"
  local max_attempts attempt now rem call_to sleep_for deadline
  local detail status conclusion api_ec logs log_ec evidence ev_ec

  [[ -n "$run_id" ]] || {
    printf 'RESULT_UNKNOWN reason=missing_run_id\n'
    return 1
  }

  max_attempts="$(eternal_wait_max_attempts)"
  deadline=$(( $(date +%s) + WAIT_TIMEOUT_SEC ))
  attempt=0
  status=""
  conclusion=""

  while [[ "$attempt" -lt "$max_attempts" ]]; do
    now="$(date +%s)"
    rem=$(( deadline - now ))
    if [[ "$rem" -le 0 ]]; then
      break
    fi
    attempt=$((attempt + 1))
    call_to="$HTTP_TIMEOUT_SEC"
    if [[ "$rem" -lt "$call_to" ]]; then
      call_to="$rem"
    fi
    if [[ "$call_to" -lt 1 ]]; then
      break
    fi
    set +e
    detail="$(ETERNAL_GH_CALL_TIMEOUT="$call_to" eternal_fetch_run_detail "$run_id")"
    api_ec=$?
    set -e
    if [[ "$api_ec" -eq 124 ]]; then
      printf 'WORKFLOW_TIMED_OUT request_id=%s run_id=%s reason=http_timeout_on_run_detail\n' \
        "$REQUEST_ID" "$run_id"
      printf 'RESULT_UNKNOWN\n'
      return 1
    fi
    if [[ "$api_ec" -ne 0 || -z "$detail" ]]; then
      printf 'RESULT_UNKNOWN reason=run_detail_api_error run_id=%s\n' "$run_id"
      return 1
    fi
    status="$(printf '%s' "$detail" | jq -r '.status // empty')"
    conclusion="$(printf '%s' "$detail" | jq -r '.conclusion // empty')"
    if [[ "$status" == "completed" ]]; then
      break
    fi
    now="$(date +%s)"
    rem=$(( deadline - now ))
    if [[ "$rem" -le 0 || "$attempt" -ge "$max_attempts" ]]; then
      break
    fi
    if [[ "$WAIT_INTERVAL_SEC" -gt 0 ]]; then
      sleep_for="$WAIT_INTERVAL_SEC"
      if [[ "$rem" -lt "$sleep_for" ]]; then
        sleep_for="$rem"
      fi
      eternal_wait_sleep "$sleep_for"
    fi
  done

  if [[ "$status" != "completed" ]]; then
    printf 'WORKFLOW_TIMED_OUT request_id=%s run_id=%s run_url=%s last_status=%s\n' \
      "$REQUEST_ID" "$run_id" "$run_url" "${status:-unknown}"
    printf 'RESULT_UNKNOWN\n'
    return 1
  fi

  case "$conclusion" in
    success)
      printf 'WORKFLOW_SUCCESS run_id=%s run_url=%s request_id=%s\n' \
        "$run_id" "$run_url" "$REQUEST_ID"
      ;;
    failure)
      printf 'WORKFLOW_FAILED run_id=%s run_url=%s request_id=%s\n' \
        "$run_id" "$run_url" "$REQUEST_ID"
      ;;
    cancelled)
      printf 'WORKFLOW_CANCELLED run_id=%s run_url=%s request_id=%s\n' \
        "$run_id" "$run_url" "$REQUEST_ID"
      ;;
    timed_out)
      printf 'WORKFLOW_TIMED_OUT run_id=%s run_url=%s request_id=%s conclusion=timed_out\n' \
        "$run_id" "$run_url" "$REQUEST_ID"
      ;;
    *)
      printf 'RESULT_UNKNOWN reason=unexpected_conclusion conclusion=%s run_id=%s\n' \
        "${conclusion:-empty}" "$run_id"
      return 1
      ;;
  esac

  # Bound gh run view --log by remaining wait budget and per-call HTTP timeout.
  now="$(date +%s)"
  rem=$(( deadline - now ))
  if [[ "$rem" -le 0 ]]; then
    printf 'WORKFLOW_TIMED_OUT request_id=%s run_id=%s reason=no_time_left_for_log_fetch\n' \
      "$REQUEST_ID" "$run_id"
    printf 'RESULT_UNKNOWN\n'
    return 1
  fi
  call_to="$HTTP_TIMEOUT_SEC"
  if [[ "$rem" -lt "$call_to" ]]; then
    call_to="$rem"
  fi
  if [[ "$call_to" -lt 1 ]]; then
    call_to=1
  fi
  set +e
  logs="$(ETERNAL_GH_CALL_TIMEOUT="$call_to" eternal_fetch_run_logs "$run_id")"
  log_ec=$?
  set -e
  if [[ "$log_ec" -eq 124 ]]; then
    printf 'RESULT_UNKNOWN reason=log_fetch_http_timeout run_id=%s\n' "$run_id"
    return 1
  fi
  if [[ "$log_ec" -ne 0 || -z "$logs" ]]; then
    printf 'RESULT_UNKNOWN reason=runner_logs_unavailable run_id=%s\n' "$run_id"
    return 1
  fi
  eternal_print_log_evidence_summary "$logs"
  set +e
  evidence="$(eternal_classify_runner_evidence "$logs" "$ACTION")"
  ev_ec=$?
  set -e
  printf '%s\n' "$evidence"
  # Never PASS when the correlated workflow conclusion is not success — even if a
  # DEPLOY_OK-shaped marker appears in logs (stale/partial job output).
  if [[ "$conclusion" != "success" ]]; then
    if [[ "$ev_ec" -eq 0 ]]; then
      printf 'RESULT_UNKNOWN reason=workflow_conclusion_not_success_ignoring_success_marker conclusion=%s run_id=%s\n' \
        "$conclusion" "$run_id"
    fi
    return 1
  fi
  if [[ "$ev_ec" -eq 0 ]]; then
    return 0
  fi
  return 1
}

eternal_poll_sleep() {
  local secs="${1:-$POLL_INTERVAL_SEC}"
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    return 0
  fi
  if ! eternal_is_positive_int "$secs"; then
    return 0
  fi
  sleep "$secs"
}

eternal_poll_max_attempts() {
  if [[ -n "${POLL_MAX_ATTEMPTS}" ]]; then
    printf '%s\n' "${POLL_MAX_ATTEMPTS}"
    return 0
  fi
  if [[ "${POLL_INTERVAL_SEC}" -le 0 ]]; then
    printf '%s\n' "${POLL_TIMEOUT_SEC}"
    return 0
  fi
  printf '%s\n' "$(( (POLL_TIMEOUT_SEC / POLL_INTERVAL_SEC) + 1 ))"
}

plan_summary() {
  printf 'MODE=plan\n'
  printf 'ACTION=%s\n' "$ACTION"
  printf 'WORKFLOW=%s\n' "$WORKFLOW_FILE"
  printf 'REPO=%s\n' "$REPO"
  if [[ "$ACTION" == "deploy" ]]; then
    printf 'TARGET_SHA=%s\n' "$SHA"
    printf 'CONFIRM_SHA=%s\n' "$CONFIRM_SHA"
    printf 'ALLOW_MIGRATIONS=%s\n' "$ALLOW_MIGRATIONS"
    printf 'ACK_REPLACE_LIVE_FRONTEND=%s\n' "$ACK_REPLACE_LIVE_FRONTEND"
  fi
  printf 'REQUEST_ID=%s\n' "${REQUEST_ID:-}"
  printf 'POST=no\n'
  printf 'PLAN_OK note=no workflow_dispatch performed\n'
}

eternal_build_dispatch_payload() {
  local shared="${ETERNAL_DEPLOY_SHARED_SECRET:-}"
  if [[ "$ACTION" == "deploy" ]]; then
    [[ -n "$shared" ]] || eternal_deploy_die "ETERNAL_DEPLOY_SHARED_SECRET unset for execute deploy (use env secret store)"
  fi
  # shellcheck disable=SC2016
  ETERNAL_DEPLOY_SHARED_SECRET="$shared" jq -n \
    --arg ref "$DISPATCH_REF" \
    --arg action "$ACTION" \
    --arg sha "$SHA" \
    --arg confirm "$CONFIRM_SHA" \
    --arg rid "$REQUEST_ID" \
    --argjson allow_mig "$ALLOW_MIGRATIONS" \
    --argjson ack_fe "$ACK_REPLACE_LIVE_FRONTEND" \
    '
    {
      ref: $ref,
      inputs: (
        {}
        + (if ($rid | length) > 0 then {request_id: $rid} else {} end)
        + (if $action == "deploy" then
            {
              commit_sha: $sha,
              confirm_sha: $confirm,
              deploy_shared_secret: env.ETERNAL_DEPLOY_SHARED_SECRET
            }
            + (if $allow_mig == 1 then {allow_migrations: "true"} else {} end)
            + (if $ack_fe == 1 then {ack_replace_live_frontend: "true"} else {} end)
          else {} end)
      )
    }'
}

do_dispatch() {
  local row run_id url

  # Pre-dispatch: refuse if this request_id already correlates (no POST).
  set +e
  row="$(find_correlated_run "$REQUEST_ID")"
  local pre=$?
  set -e
  if [[ "$pre" == "4" ]]; then
    eternal_deploy_die "HTTP timeout listing runs before dispatch (request_id=${REQUEST_ID}); not dispatching"
  fi
  if [[ "$pre" == "3" ]]; then
    eternal_deploy_die "API error listing runs before dispatch (request_id=${REQUEST_ID}); not dispatching"
  fi
  if [[ "$pre" == "2" ]]; then
    eternal_deploy_die "ambiguous existing runs for request_id=${REQUEST_ID}; resolve before dispatch; not dispatching"
  fi
  if [[ "$pre" == "0" ]]; then
    url="${row#*$'\t'}"
    printf 'EXISTING_CORRELATED_RUN=%s\n' "$url"
    eternal_deploy_die "request_id=${REQUEST_ID} already correlates to a run; not re-dispatching"
  fi

  local payload ec
  payload="$(eternal_build_dispatch_payload)"
  # Never log payload (contains shared secret for deploy).

  set +e
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    eternal_gh_api POST "repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches" \
      input=stdin-json secret_in_argv=no
    ec=$?
  else
    # Live: pipe JSON via --input -. Timeout may fire after GitHub accepted — never re-POST.
    printf '%s' "$payload" | eternal_run_with_http_timeout "$HTTP_TIMEOUT_SEC" \
      "$GH_BIN" api --method POST \
      "repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches" \
      --input -
    ec=$?
  fi
  set -e
  if [[ "$ec" -eq 124 ]]; then
    printf 'DISPATCH_ACCEPTANCE_UNKNOWN request_id=%s workflow=%s\n' \
      "$REQUEST_ID" "$WORKFLOW_FILE"
    printf 'DISPATCH_NOTE=POST timed out; GitHub may have accepted the dispatch — do not auto re-dispatch\n'
    eternal_deploy_die "workflow_dispatch POST timed out (request_id=${REQUEST_ID}); acceptance unknown — do not auto re-dispatch"
  fi
  if [[ "$ec" -ne 0 ]]; then
    eternal_deploy_log "dispatch denied or failed (exit=$ec); no deploy/probe fallback"
    eternal_deploy_die "workflow_dispatch denied (e.g. 403). Cursor read access != Actions write"
  fi

  printf 'DISPATCH_ACCEPTED workflow=%s request_id=%s note=POST once; correlating via GET only\n' \
    "$WORKFLOW_FILE" "$REQUEST_ID"

  # After successful POST: poll GET only within wall-clock budget; never re-POST.
  local max_attempts attempt fc now rem call_to sleep_for deadline
  max_attempts="$(eternal_poll_max_attempts)"
  deadline=$(( $(date +%s) + POLL_TIMEOUT_SEC ))
  attempt=0
  while [[ "$attempt" -lt "$max_attempts" ]]; do
    now="$(date +%s)"
    rem=$(( deadline - now ))
    if [[ "$rem" -le 0 ]]; then
      break
    fi
    attempt=$((attempt + 1))
    call_to="$HTTP_TIMEOUT_SEC"
    if [[ "$rem" -lt "$call_to" ]]; then
      call_to="$rem"
    fi
    if [[ "$call_to" -lt 1 ]]; then
      break
    fi
    set +e
    row="$(ETERNAL_GH_CALL_TIMEOUT="$call_to" find_correlated_run "$REQUEST_ID")"
    fc=$?
    set -e
    if [[ "$fc" == "0" && -n "$row" ]]; then
      run_id="${row%%$'\t'*}"
      url="${row#*$'\t'}"
      printf 'DISPATCH_OK workflow=%s run_id=%s run_url=%s request_id=%s\n' \
        "$WORKFLOW_FILE" "$run_id" "$url" "$REQUEST_ID"
      # Bounded wait for workflow conclusion + runner evidence (still no re-POST).
      wait_and_report_workflow_run "$run_id" "$url"
      return $?
    fi
    if [[ "$fc" == "2" ]]; then
      eternal_deploy_die "ambiguous runs after dispatch for request_id=${REQUEST_ID}; not re-dispatching"
    fi
    if [[ "$fc" == "3" ]]; then
      eternal_deploy_die "API error while correlating run after dispatch (request_id=${REQUEST_ID}); dispatch was accepted — do not auto re-dispatch"
    fi
    if [[ "$fc" == "4" ]]; then
      eternal_deploy_die "HTTP timeout while correlating run after dispatch (request_id=${REQUEST_ID}); dispatch was accepted — do not auto re-dispatch"
    fi
    now="$(date +%s)"
    rem=$(( deadline - now ))
    if [[ "$rem" -le 0 || "$attempt" -ge "$max_attempts" ]]; then
      break
    fi
    if [[ "$POLL_INTERVAL_SEC" -gt 0 ]]; then
      sleep_for="$POLL_INTERVAL_SEC"
      if [[ "$rem" -lt "$sleep_for" ]]; then
        sleep_for="$rem"
      fi
      eternal_poll_sleep "$sleep_for"
    fi
  done

  printf 'DISPATCH_ACCEPTED_UNRESOLVED request_id=%s workflow=%s\n' \
    "$REQUEST_ID" "$WORKFLOW_FILE"
  eternal_deploy_die "dispatch accepted but run URL correlation timed out (request_id=${REQUEST_ID}); do not auto re-dispatch"
}

# Finalize request_id before execute network/POST.
if [[ -z "$REQUEST_ID" ]]; then
  REQUEST_ID="req-$(date -u +%Y%m%d%H%M%S)-$$"
fi

if [[ "$MODE" == "execute" ]]; then
  eternal_require_timeout_tool
  eternal_validate_timeout_configs
  eternal_validate_request_id "$REQUEST_ID"
fi

report_actor

if [[ "$ACTION" == "deploy" ]]; then
  [[ -n "$SHA" && -n "$CONFIRM_SHA" ]] || eternal_deploy_die "deploy requires --sha and --confirm-sha"
  validate_deploy_target
fi

if [[ "$MODE" == "plan" ]]; then
  plan_summary
  exit 0
fi

do_dispatch
