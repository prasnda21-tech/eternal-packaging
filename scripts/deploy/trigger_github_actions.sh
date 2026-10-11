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
#   ETERNAL_TRIGGER_DISPATCH_LOG    records mock POSTs for tests
#   ETERNAL_DEPLOY_SHARED_SECRET    execute+deploy only (never printed)
#   GH_TOKEN / ETERNAL_GH_TOKEN     write-capable token from env secret store

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
    -h|--help)
      cat <<'EOF'
Modes: plan (default, no POST) | execute (requires --execute)
Actions: probe | deploy
Deploy requires --sha and --confirm-sha (identical 40-char hex). Never auto-picks main tip.
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

eternal_gh_api() {
  local method="${1:-GET}"
  local path="$2"
  shift 2 || true
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" ]]; then
    case "$path" in
      */actions/workflows/*/dispatches)
        printf '%s\n' "POST $path" >>"${ETERNAL_TRIGGER_DISPATCH_LOG:-/dev/null}"
        if [[ -f "${ETERNAL_GH_API_MOCK_DIR}/dispatch-status" ]]; then
          local st
          st="$(cat "${ETERNAL_GH_API_MOCK_DIR}/dispatch-status")"
          if [[ "$st" != "204" && "$st" != "0" && "$st" != "ok" ]]; then
            echo "{\"message\":\"Resource not accessible by integration\",\"status\":\"$st\"}" >&2
            return 1
          fi
        fi
        return 0
        ;;
      */actions/workflows/*/runs*)
        cat "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs.json"
        return 0
        ;;
    esac
    if [[ "$method" == "GET" && -f "${ETERNAL_GH_API_MOCK_DIR}/actor.json" && "$path" == *"graphql"* ]]; then
      cat "${ETERNAL_GH_API_MOCK_DIR}/actor.json"
      return 0
    fi
    eternal_deploy_die "mock API not configured for: $method $path"
  fi
  if [[ "$method" == "GET" ]]; then
    gh api "$path"
  else
    gh api --method "$method" "$path" "$@"
  fi
}

report_actor() {
  local login="unknown"
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" && -f "${ETERNAL_GH_API_MOCK_DIR}/actor.json" ]]; then
    login="$(jq -r '.login // .data.viewer.login // "unknown"' "${ETERNAL_GH_API_MOCK_DIR}/actor.json")"
  else
    login="$(gh api graphql -f query='query { viewer { login } }' --jq '.data.viewer.login' 2>/dev/null || echo unknown)"
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
    status="$(gh api "repos/${REPO}/compare/main...${SHA}" --jq '.status' 2>/dev/null || echo missing)"
    [[ "$status" == "identical" || "$status" == "behind" ]] \
      || eternal_deploy_die "target SHA not verified on main (compare status=$status)"
  fi

  bash "$SCRIPT_DIR/verify_ci_for_sha.sh" "$REPO" "$SHA"
}

find_correlated_run() {
  local since_epoch="$1"
  local want_request_id="$2"
  local runs_json
  runs_json="$(eternal_gh_api GET "repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/runs?event=workflow_dispatch&per_page=20")"
  if [[ -n "$want_request_id" ]]; then
    local hit
    hit="$(printf '%s' "$runs_json" | jq -r --arg id "$want_request_id" '
      [.workflow_runs[]?
        | select((.display_title // .name // "") | contains($id))
      ] | first | .html_url // empty')"
    if [[ -n "$hit" ]]; then
      printf '%s\n' "$hit"
      return 0
    fi
  fi
  local urls
  urls="$(printf '%s' "$runs_json" | jq -r --argjson since "$since_epoch" '
    [.workflow_runs[]?
      | select(((.created_at // "1970-01-01T00:00:00Z") | fromdateiso8601) >= $since)
      | .html_url] | .[]' 2>/dev/null || true)"
  local count
  count="$(printf '%s\n' "$urls" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [[ "$count" == "1" ]]; then
    printf '%s\n' "$urls"
    return 0
  fi
  if [[ "${count:-0}" -gt 1 ]]; then
    eternal_deploy_log "ambiguous correlated runs ($count); refusing automatic re-dispatch"
    printf '%s\n' "$urls" >&2
    return 2
  fi
  return 1
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
  fi
  printf 'REQUEST_ID=%s\n' "${REQUEST_ID:-}"
  printf 'POST=no\n'
  printf 'PLAN_OK note=no workflow_dispatch performed\n'
}

do_dispatch() {
  local before url
  before="$(date -u +%s)"
  set +e
  find_correlated_run "$((before - 5))" "$REQUEST_ID"
  local pre=$?
  set -e
  if [[ "$pre" == "2" ]]; then
    eternal_deploy_die "existing ambiguous runs; resolve before dispatch"
  fi

  local args=(-f "ref=main")
  if [[ -n "$REQUEST_ID" ]]; then
    args+=(-f "inputs[request_id]=${REQUEST_ID}")
  fi
  if [[ "$ACTION" == "deploy" ]]; then
    args+=(-f "inputs[commit_sha]=${SHA}" -f "inputs[confirm_sha]=${CONFIRM_SHA}")
    local shared="${ETERNAL_DEPLOY_SHARED_SECRET:-}"
    [[ -n "$shared" ]] || eternal_deploy_die "ETERNAL_DEPLOY_SHARED_SECRET unset for execute deploy (use env secret store)"
    args+=(-f "inputs[deploy_shared_secret]=${shared}")
    if [[ "$ALLOW_MIGRATIONS" == "1" ]]; then
      args+=(-f "inputs[allow_migrations]=true")
    fi
  fi

  set +e
  eternal_gh_api POST "repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches" "${args[@]}"
  local ec=$?
  set -e
  if [[ "$ec" -ne 0 ]]; then
    eternal_deploy_log "dispatch denied or failed (exit=$ec); no deploy/probe fallback"
    set +e
    url="$(find_correlated_run "$before" "$REQUEST_ID")"
    local fc=$?
    set -e
    if [[ "$fc" == "0" && -n "$url" ]]; then
      printf 'DISPATCH_AMBIGUOUS_EXISTING_RUN=%s\n' "$url"
      eternal_deploy_die "dispatch HTTP failed but a correlated run exists; not re-dispatching"
    fi
    eternal_deploy_die "workflow_dispatch denied (e.g. 403). Cursor read access != Actions write"
  fi

  # After successful POST, require exactly one correlated run; never re-POST.
  if [[ -n "${ETERNAL_GH_API_MOCK_DIR:-}" && -f "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-after.json" ]]; then
    cp "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs-after.json" "${ETERNAL_GH_API_MOCK_DIR}/trigger-runs.json"
  fi
  set +e
  url="$(find_correlated_run "$before" "$REQUEST_ID")"
  local fc=$?
  set -e
  if [[ "$fc" == "0" && -n "$url" ]]; then
    printf 'DISPATCH_OK workflow=%s run_url=%s\n' "$WORKFLOW_FILE" "$url"
    return 0
  fi
  if [[ "$fc" == "2" ]]; then
    eternal_deploy_die "ambiguous runs after dispatch; not re-dispatching"
  fi
  eternal_deploy_die "dispatch accepted but correlated run URL not found; not re-dispatching"
}

report_actor

if [[ "$ACTION" == "deploy" ]]; then
  [[ -n "$SHA" && -n "$CONFIRM_SHA" ]] || eternal_deploy_die "deploy requires --sha and --confirm-sha"
  validate_deploy_target
fi

if [[ -z "$REQUEST_ID" ]]; then
  REQUEST_ID="req-$(date -u +%Y%m%d%H%M%S)-$$"
fi

if [[ "$MODE" == "plan" ]]; then
  # Ensure plan never touches dispatch log as success POST
  plan_summary
  exit 0
fi

do_dispatch
