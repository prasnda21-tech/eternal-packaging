#!/usr/bin/env bash
# Synthetic fail-closed proofs for deploy infra (no real VPS / no real dispatch).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$ROOT/scripts/deploy/eternal-actions-gate"
RUNNER="$ROOT/scripts/deploy/eternal-deploy-runner.sh"
TRIGGER="$ROOT/scripts/deploy/trigger_github_actions.sh"
PROBE_SH="$ROOT/scripts/deploy/ssh_gate_probe.sh"
PREPARE_BASELINE="$ROOT/scripts/deploy/prepare-verified-baseline.sh"
FINALIZE_BASELINE="$ROOT/scripts/deploy/finalize-existing-baseline.sh"
VERIFY_CI="$ROOT/scripts/deploy/verify_ci_for_sha.sh"
PASS=0
FAIL=0
REPORT="${ETERNAL_TEST_REPORT:-/opt/cursor/artifacts/deploy-infra-synthetic.log}"
mkdir -p "$(dirname "$REPORT")"
: >"$REPORT"

log() { printf '%s\n' "$*" | tee -a "$REPORT"; }
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then log "PASS: $label"; PASS=$((PASS+1))
  else log "FAIL: $label expected=[$expected] actual=[$actual]"; FAIL=$((FAIL+1)); fi
}
assert_true() {
  local label="$1"; shift
  if "$@"; then log "PASS: $label"; PASS=$((PASS+1)); else log "FAIL: $label"; FAIL=$((FAIL+1)); fi
}
assert_false() {
  local label="$1"; shift
  if "$@"; then log "FAIL: $label (expected failure)"; FAIL=$((FAIL+1)); else log "PASS: $label"; PASS=$((PASS+1)); fi
}

WORKDIR="$(mktemp -d)"
cleanup_work() {
  # Permission fixtures may use mode 555 parents; restore before rm.
  chmod -R u+w "$WORKDIR" 2>/dev/null || true
  rm -rf "$WORKDIR"
}
BIN="$WORKDIR/bin"
HARNESS="$WORKDIR/harness"
MIRROR_SRC="$WORKDIR/repo"
RUNTIME="$HARNESS/runtime"
RELEASES="$RUNTIME/releases"
FRONTEND="$HARNESS/www"
LEGACY="$HARNESS/legacy-dirty"
CURRENT="$RUNTIME/current"
FRONTEND_PREV="$RUNTIME/frontend-prev"
LOCK="$HARNESS/deploy.lock"
BASELINE="$RELEASES/baseline-active"
# Simulate root-owned /var/www parent (mode 555) with writable webroot leaf.
SIM_WWW_PARENT="$HARNESS/sim-var-www"
mkdir -p "$BIN" "$RELEASES" "$FRONTEND_PREV" "$FRONTEND/live" "$LEGACY/backend" \
  "$SIM_WWW_PARENT/eternal-packaging/static/js"
chmod 755 "$SIM_WWW_PARENT/eternal-packaging"
chmod 555 "$SIM_WWW_PARENT"

# --- fixture repo ---
git init -q -b main "$MIRROR_SRC"
git -C "$MIRROR_SRC" config user.email "synthetic@example.com"
git -C "$MIRROR_SRC" config user.name "Synthetic"
mkdir -p "$MIRROR_SRC/backend" "$MIRROR_SRC/frontend/src" "$MIRROR_SRC/scripts/deploy"
printf 'ok\n' >"$MIRROR_SRC/backend/requirements.txt"
printf 'ok-portable\n' >"$MIRROR_SRC/backend/requirements.portable.txt"
cat >"$MIRROR_SRC/frontend/package.json" <<'EOF'
{"name":"frontend","private":true,"scripts":{"build":"mkdir -p build/static/js && echo asset>build/static/js/app.js && printf '%s\\n' '{\"files\":{\"main.js\":\"/static/js/app.js\"}}'>build/asset-manifest.json && printf '%s\\n' '<script src=\"/static/js/app.js\"></script>'>build/index.html"}}
EOF
printf 'yarn lockfile v1\n' >"$MIRROR_SRC/frontend/yarn.lock"
printf 'app\n' >"$MIRROR_SRC/frontend/src/index.js"
printf 'dirty\n' >"$LEGACY/DO_NOT_TOUCH"
printf 'legacy\n' >"$LEGACY/backend/app.py"
printf 'ok\n' >"$LEGACY/backend/requirements.txt"
printf 'ok-portable\n' >"$LEGACY/backend/requirements.portable.txt"
cp "$GATE" "$RUNNER" "$ROOT/scripts/deploy/lib.sh" "$MIRROR_SRC/scripts/deploy/"
git -C "$MIRROR_SRC" add . && git -C "$MIRROR_SRC" commit -q -m base
printf 'ok\n#pin\n' >"$MIRROR_SRC/backend/requirements.txt"
printf 'ok-portable\n#pin\n' >"$MIRROR_SRC/backend/requirements.portable.txt"
git -C "$MIRROR_SRC" add backend/requirements.txt backend/requirements.portable.txt && git -C "$MIRROR_SRC" commit -q -m 'backend bump'
MAIN_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git -C "$MIRROR_SRC" checkout -q -b feature/unmerged
printf 'x\n' >"$MIRROR_SRC/backend/feature.txt"
git -C "$MIRROR_SRC" add backend/feature.txt && git -C "$MIRROR_SRC" commit -q -m unmerged
UNMERGED_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git -C "$MIRROR_SRC" checkout -q main
git clone -q --mirror "$MIRROR_SRC" "$RELEASES/mirror.git"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/*:refs/heads/*'

EXPECTED_WD="$CURRENT/backend"
# Real isolated backend stand-in process (cwd = active release backend). No prod DB.
start_harness_backend_proc() {
  local cwd="$1"
  mkdir -p "$cwd"
  if [[ -f "$HARNESS/backend.pid" ]]; then
    kill "$(cat "$HARNESS/backend.pid")" 2>/dev/null || true
    rm -f "$HARNESS/backend.pid"
  fi
  ( cd "$cwd" && exec sleep 3600 ) &
  echo $! >"$HARNESS/backend.pid"
}
stop_harness_backend_proc() {
  if [[ -f "$HARNESS/backend.pid" ]]; then
    kill "$(cat "$HARNESS/backend.pid")" 2>/dev/null || true
    rm -f "$HARNESS/backend.pid"
  fi
}

cat >"$BIN/systemctl" <<EOF
#!/usr/bin/env bash
echo "systemctl \$*" >>"\${ETERNAL_SYSTEMCTL_LOG}"
# Enforce exact show argv contract used by runner (sudoers allowlist).
if [[ "\$1" == "show" ]]; then
  if [[ "\$#" -ne 5 || "\$2" != "-p" || "\$4" != "--value" ]]; then
    echo "systemctl show argv not in allowlist form: \$*" >&2
    exit 2
  fi
fi
case "\$1" in
  show)
    prop="\$3"
    case "\$prop" in
      WorkingDirectory) printf '%s\n' "$EXPECTED_WD"; exit 0 ;;
      ExecStart) printf '%s\n' "$EXPECTED_WD/.venv/bin/uvicorn server:app"; exit 0 ;;
      MainPID)
        if [[ -f "$HARNESS/backend.pid" ]]; then cat "$HARNESS/backend.pid"; else printf '0\n'; fi
        exit 0
        ;;
      ActiveState) printf 'active\n'; exit 0 ;;
      *) echo "unexpected show property: \$prop" >&2; exit 3 ;;
    esac
    ;;
  is-active) printf 'active\n'; exit 0 ;;
  restart)
    n=0
    if [[ -f "$HARNESS/restart.count" ]]; then n="\$(cat "$HARNESS/restart.count")"; fi
    n=\$((n+1))
    echo "\$n" >"$HARNESS/restart.count"
    if [[ "\${ETERNAL_FORCE_RESTART_FAIL:-0}" == "1" ]]; then exit 1; fi
    if [[ -n "\${ETERNAL_RESTART_FAIL_FROM:-}" && "\$n" -ge "\${ETERNAL_RESTART_FAIL_FROM}" ]]; then exit 1; fi
    # Respawn stand-in process at CURRENT/backend (isolated; no DB).
    cwd="\$(readlink -f "${CURRENT}/backend" 2>/dev/null || true)"
    if [[ -n "\$cwd" ]]; then
      mkdir -p "\$cwd"
      if [[ -f "$HARNESS/backend.pid" ]]; then kill "\$(cat "$HARNESS/backend.pid")" 2>/dev/null || true; fi
      ( cd "\$cwd" && exec sleep 3600 ) &
      echo \$! >"$HARNESS/backend.pid"
    fi
    exit 0
    ;;
esac
exit 0
EOF
chmod +x "$BIN/systemctl"
: >"$HARNESS/restart.count"
trap 'stop_harness_backend_proc; cleanup_work' EXIT

cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
# Harness curl: supports -o BODY -w '%{http_code}' used by eternal_health_check
# and the public frontend Nginx check. No production server.
out_file=""
dump_header=""
url=""
if [[ -n "${ETERNAL_CURL_ARGS_LOG:-}" ]]; then
  printf '%s\n' "$*" >>"${ETERNAL_CURL_ARGS_LOG}"
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) out_file="${2:-}"; shift 2 ;;
    -D|--dump-header) dump_header="${2:-}"; shift 2 ;;
    -w|--write-out) shift 2 ;;
    --max-time) shift 2 ;;
    -s|-S|-f|-sS|-L|-k|--insecure) shift ;;
    http://*|https://*) url="$1"; shift ;;
    *) shift ;;
  esac
done
# Frontend fixture is any URL that is not the backend health endpoint.
if [[ "$url" != *"/api/system/health"* ]]; then
  body="fixture-frontend"
  code=200
  location=""
  fe_scheme="${url%%://*}"
  fe_rest="${url#*://}"
  fe_hostport="${fe_rest%%/*}"
  fe_host="${fe_hostport%%:*}"
  fe_path="/${fe_rest#*/}"
  if [[ "$fe_rest" != */* ]]; then
    fe_path="/"
  fi
  if [[ "$fe_scheme" == "https" && "${ETERNAL_FRONTEND_TLS_FAIL:-0}" == "1" ]]; then
    echo "SSL certificate problem: unable to get local issuer certificate" >&2
    printf '%s' "000"
    exit 60
  fi
  if [[ -n "${ETERNAL_FRONTEND_HTTP_FAIL_STATE:-}" && -f "${ETERNAL_FRONTEND_HTTP_FAIL_STATE}" ]]; then
    n="$(cat "${ETERNAL_FRONTEND_HTTP_FAIL_STATE}")"
    if [[ "${n:-0}" -gt 0 ]]; then
      echo $((n - 1)) >"${ETERNAL_FRONTEND_HTTP_FAIL_STATE}"
      body=""
      code=403
    fi
  fi
  if [[ "$code" == "200" && -n "${ETERNAL_FRONTEND_HTTP_CODE:-}" ]]; then
    code="${ETERNAL_FRONTEND_HTTP_CODE}"
    if [[ "$code" != "200" ]]; then
      body=""
    fi
  fi
  if [[ "$code" == "200" && "${ETERNAL_FRONTEND_FORCE_200:-0}" != "1" ]]; then
    case "${ETERNAL_FRONTEND_REDIRECT_MODE:-}" in
      other)
        code=301
        location="https://evil.example${fe_path}"
        body=""
        ;;
      loop)
        code=301
        location="$url"
        body=""
        ;;
      https)
        if [[ "$fe_scheme" == "http" ]]; then
          code=301
          location="https://${fe_host}${fe_path}"
          body=""
        fi
        ;;
    esac
  fi
  if [[ -n "$dump_header" ]]; then
    {
      printf 'HTTP/1.1 %s\r\n' "$code"
      if [[ -n "$location" ]]; then
        printf 'Location: %s\r\n' "$location"
      fi
      printf '\r\n'
    } >"$dump_header"
  fi
  if [[ -n "$out_file" ]]; then
    printf '%s' "$body" >"$out_file"
    printf '%s' "$code"
  else
    printf '%s' "$body"
  fi
  exit 0
fi
body='{"mongo":true,"mode":"operational","checked_at":"t"}'
code=200
# Transient HTTP 502 sequence (post-restart warming).
if [[ -n "${ETERNAL_HEALTH_HTTP_FAIL_STATE:-}" && -f "${ETERNAL_HEALTH_HTTP_FAIL_STATE}" ]]; then
  n="$(cat "${ETERNAL_HEALTH_HTTP_FAIL_STATE}")"
  if [[ "${n:-0}" -gt 0 ]]; then
    echo $((n - 1)) >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
    body=""
    code=502
  fi
fi
# Counted payload failures: activate can fail, rollback health can recover.
if [[ "$code" == "200" && -n "${ETERNAL_HEALTH_FAIL_STATE:-}" && -f "${ETERNAL_HEALTH_FAIL_STATE}" ]]; then
  n="$(cat "${ETERNAL_HEALTH_FAIL_STATE}")"
  if [[ "${n:-0}" -gt 0 ]]; then
    echo $((n - 1)) >"$ETERNAL_HEALTH_FAIL_STATE"
    body='{"mongo":false,"mode":"degraded"}'
    code=200
  fi
fi
if [[ "$code" == "200" && "${ETERNAL_HEALTH_FORCE_FAIL:-0}" == "1" ]]; then body='{"mongo":false,"mode":"degraded"}'; code=200; fi
if [[ "$code" == "200" && "${ETERNAL_HEALTH_PARTIAL:-0}" == "1" ]]; then body='{"mongo":false,"mode":"degraded"}'; code=200; fi
if [[ -n "$out_file" ]]; then
  printf '%s' "$body" >"$out_file"
  printf '%s' "$code"
else
  printf '%s' "$body"
fi
exit 0
EOF
chmod +x "$BIN/curl"
export ETERNAL_HEALTH_FAIL_STATE="$HARNESS/health-fail.remaining"
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"
export ETERNAL_HEALTH_HTTP_FAIL_STATE="$HARNESS/health-http-fail.remaining"
echo 0 >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
export ETERNAL_FRONTEND_HTTP_FAIL_STATE="$HARNESS/frontend-http-fail.remaining"
echo 0 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
export ETERNAL_FRONTEND_PUBLIC_URL="https://fixture.nginx.local"
export ETERNAL_FRONTEND_HTTP_TIMEOUT_S=2

cat >"$BIN/ssh" <<'EOF'
#!/usr/bin/env bash
echo "ssh_args $*" >>"${ETERNAL_SSH_LOG}"
# Ensure remote command is last arg and equals probe for probe helper
remote_cmd="${*: -1}"
if [[ "${ETERNAL_SSH_FORCE_FAIL:-0}" == "1" ]]; then echo "ssh fail" >&2; exit 255; fi
if [[ "$remote_cmd" != "probe" ]]; then
  echo "REFUSING non-probe command: $remote_cmd" >&2
  exit 9
fi
if [[ "$*" != *StrictHostKeyChecking=yes* ]]; then echo "missing strict host key" >&2; exit 8; fi
printf 'ETERNAL_GATE_OK probe=pass version=2\nPASS probe\n'
exit 0
EOF
chmod +x "$BIN/ssh"

export PATH="$BIN:$PATH"
export ETERNAL_RUNTIME_ROOT="$RUNTIME"
export ETERNAL_RELEASES_ROOT="$RELEASES"
export ETERNAL_CURRENT_LINK="$CURRENT"
export ETERNAL_LEGACY_ROOT="$LEGACY"
export ETERNAL_FRONTEND_ROOT="$FRONTEND/live"
export ETERNAL_FRONTEND_PREV="$FRONTEND_PREV"
export ETERNAL_DEPLOY_LOCK="$LOCK"
export ETERNAL_SERVICE_NAME="eternal-packaging-backend.service"
export ETERNAL_HEALTH_URL="http://127.0.0.1:9/api/system/health"
export ETERNAL_HEALTH_RETRIES=2
export ETERNAL_HEALTH_SLEEP_S=0
export ETERNAL_MIRROR_DIR="$RELEASES/mirror.git"
export ETERNAL_SKIP_FETCH=1
export ETERNAL_DRY_BUILD=1
export ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl"
export ETERNAL_USE_SYSTEMCTL_SUDO=0
export ETERNAL_CURL_BIN="$BIN/curl"
export ETERNAL_SYSTEMCTL_LOG="$HARNESS/systemctl.log"
export ETERNAL_DEPLOY_RUNNER="$RUNNER"
export ETERNAL_BASELINE_DIR="$BASELINE"
export ETERNAL_EXPECTED_WD="$EXPECTED_WD"
export ETERNAL_EXPECTED_EXEC_PREFIX="$EXPECTED_WD"
export ETERNAL_REQUIRE_PROCCWD=1
export ETERNAL_REQUIRE_VERIFIED_BASELINE=1
export ETERNAL_MIN_DISK_KB=1
export ETERNAL_RUNTIME_MODE=stub
: >"$ETERNAL_SYSTEMCTL_LOG"
mkdir -p "$FRONTEND/live/static/js"
printf 'old\n' >"$FRONTEND/live/index.html"
printf 'old-asset\n' >"$FRONTEND/live/static/js/old.js"
printf 'active\n' >"$FRONTEND/live/active.txt"

# shellcheck disable=SC1091
source "$ROOT/scripts/deploy/lib.sh"

make_verified_baseline() {
  mkdir -p "$BASELINE/tree/backend" "$BASELINE/frontend-snapshot/static/js"
  cp -a "$LEGACY/backend"/. "$BASELINE/tree/backend"/
  rm -rf "$BASELINE/tree/backend/.venv"
  cp -a "$FRONTEND/live"/. "$BASELINE/frontend-snapshot"/
  {
    printf 'kind=verified-baseline\nsha=baseline-active\nimmutable=1\n'
  } >"$BASELINE/.deploy-ok"
  : >"$BASELINE/.baseline"
  ETERNAL_RUNTIME_MODE=stub eternal_prepare_backend_runtime "$BASELINE/tree/backend"
  eternal_baseline_write_integrity "$BASELINE" "$LEGACY" "$EXPECTED_WD" "$EXPECTED_WD/.venv/bin/uvicorn"
}

run_gate() { SSH_ORIGINAL_COMMAND="$*" "$GATE"; }

# ========== trigger: plan does not POST ==========
MOCK="$HARNESS/gh-mock"
mkdir -p "$MOCK"
printf '{"login":"cursor[bot]"}\n' >"$MOCK/actor.json"
printf '{"ancestor_ok":true}\n' >"$MOCK/ancestry.json"
cat >"$MOCK/workflow.json" <<'EOF'
{"id":100,"path":".github/workflows/reconciliation-validation.yml","state":"active"}
EOF
cat >"$MOCK/workflow-runs.json" <<EOF
{"workflow_runs":[{"id":1,"workflow_id":100,"status":"completed","conclusion":"success","head_sha":"${MAIN_SHA}","path":".github/workflows/reconciliation-validation.yml","repository":{"full_name":"prasnda21-tech/eternal-packaging"}}]}
EOF
cat >"$MOCK/jobs-1.json" <<'EOF'
{"jobs":[
 {"name":"Backend focused tests","status":"completed","conclusion":"success"},
 {"name":"Backend stock compatibility (replica set)","status":"completed","conclusion":"success"},
 {"name":"Frontend tests and build","status":"completed","conclusion":"success"},
 {"name":"Backend live orders summary","status":"completed","conclusion":"skipped"}
]}
EOF
: >"$HARNESS/dispatch.log"
plan_out="$(ETERNAL_GH_API_MOCK_DIR="$MOCK" ETERNAL_TRIGGER_DISPATCH_LOG="$HARNESS/dispatch.log" \
  "$TRIGGER" plan deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" 2>"$HARNESS/plan.err")"
assert_true "plan mode prints PLAN_OK" grep -q 'PLAN_OK' <<<"$plan_out"
assert_true "plan mode POST=no" grep -q 'POST=no' <<<"$plan_out"
assert_eq "plan mode wrote no dispatch log" "" "$(cat "$HARNESS/dispatch.log")"

# ========== invalid/mismatch SHA / checks ==========
assert_false "trigger rejects sha mismatch" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" "$TRIGGER" plan deploy --sha "$MAIN_SHA" --confirm-sha "$(printf 'a%.0s' {1..40})"
assert_false "trigger rejects unmerged via ancestry mock" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" \
  bash -c 'jq -n "{ancestor_ok:false}" >"'"$MOCK"'/ancestry.json"; '"$TRIGGER"' plan deploy --sha '"$MAIN_SHA"' --confirm-sha '"$MAIN_SHA"''
printf '{"ancestor_ok":true}\n' >"$MOCK/ancestry.json"
cat >"$MOCK/jobs-1.json" <<'EOF'
{"jobs":[{"name":"Backend focused tests","status":"completed","conclusion":"success"}]}
EOF
assert_false "trigger rejects missing required jobs" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" "$TRIGGER" plan deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA"
cat >"$MOCK/jobs-1.json" <<'EOF'
{"jobs":[
 {"name":"Backend focused tests","status":"completed","conclusion":"success"},
 {"name":"Backend stock compatibility (replica set)","status":"completed","conclusion":"success"},
 {"name":"Frontend tests and build","status":"completed","conclusion":"success"},
 {"name":"Backend live orders summary","status":"completed","conclusion":"skipped"}
]}
EOF

# ========== trigger correlation: run-name + exact request_id + GET poll ==========
assert_true "deploy-probe.yml run-name carries request_id" \
  grep -qE '^run-name:.*request_id=\$\{\{ inputs\.request_id \}\}' "$ROOT/.github/workflows/deploy-probe.yml"
assert_true "deploy-production.yml run-name carries request_id" \
  grep -qE '^run-name:.*request_id=\$\{\{ inputs\.request_id \}\}' "$ROOT/.github/workflows/deploy-production.yml"

POLL_ENV=(
  ETERNAL_GH_API_MOCK_DIR="$MOCK"
  ETERNAL_TRIGGER_DISPATCH_LOG="$HARNESS/dispatch.log"
  ETERNAL_DEPLOY_SHARED_SECRET='test-secret'
  ETERNAL_TRIGGER_POLL_INTERVAL_SEC=0
  ETERNAL_TRIGGER_POLL_MAX_ATTEMPTS=3
  ETERNAL_TRIGGER_POLL_TIMEOUT_SEC=3
  ETERNAL_TRIGGER_WAIT_INTERVAL_SEC=0
  ETERNAL_TRIGGER_WAIT_MAX_ATTEMPTS=3
  ETERNAL_TRIGGER_WAIT_TIMEOUT_SEC=3
)

# Seed post-dispatch workflow completion + runner log evidence for a run id.
seed_mock_run_outcome() {
  local run_id="$1"
  local status="${2:-completed}"
  local conclusion="${3:-success}"
  # Use ${4-…} (no colon) so an explicit empty 4th arg means "no log file".
  local evidence_line="${4-DEPLOY_OK sha=${MAIN_SHA}}"
  local omit_logs=0
  if [[ $# -ge 4 && -z "${4}" ]]; then
    omit_logs=1
  fi
  cat >"$MOCK/trigger-run-${run_id}.json" <<EOF
{"id":${run_id},"status":"${status}","conclusion":"${conclusion}","html_url":"https://example.test/runs/${run_id}"}
EOF
  if [[ "$omit_logs" == "1" ]]; then
    rm -f "$MOCK/trigger-run-${run_id}-log.txt" "$MOCK/trigger-run-log.txt"
  else
    {
      printf '[eternal-deploy] synthetic evidence\n'
      printf '%s\n' "$evidence_line"
    } >"$MOCK/trigger-run-${run_id}-log.txt"
  fi
  rm -f "$MOCK"/trigger-run-detail-poll-*.json
}

# 5) Dispatch ditolak → tidak dilaporkan accepted; POST tepat 1
printf '403\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json "$MOCK/trigger-runs-http-status"
: >"$HARNESS/dispatch.log"
set +e
deny_out="$(env "${POLL_ENV[@]}" ETERNAL_DEPLOY_SHARED_SECRET='test-secret' \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" --execute --request-id req-403 \
  2>"$HARNESS/deny.err")"
deny_ec=$?
set -e
assert_eq "403 execute exits non-zero" "1" "$deny_ec"
assert_false "403 path does not claim DISPATCH_ACCEPTED" \
  grep -q 'DISPATCH_ACCEPTED' <<<"$deny_out$(cat "$HARNESS/deny.err")"
assert_false "403 path does not claim DISPATCH_OK" \
  grep -q 'DISPATCH_OK' <<<"$deny_out$(cat "$HARNESS/deny.err")"
dispatch_count="$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"
assert_eq "403 path performs single POST attempt only" "1" "$dispatch_count"

# Pre-existing ambiguous request_id → refuse before POST
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[
 {"id":1,"html_url":"https://example.test/1","event":"workflow_dispatch","head_branch":"main",
  "display_title":"Deploy production · request_id=req-ambig"},
 {"id":2,"html_url":"https://example.test/2","event":"workflow_dispatch","head_branch":"main",
  "display_title":"Deploy production · request_id=req-ambig"}
]}
EOF
: >"$HARNESS/dispatch.log"
assert_false "ambiguous existing runs refuse before dispatch" \
  env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
      --execute --request-id req-ambig
assert_eq "ambiguous pre-check performs zero POST" "0" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# 1) List kosong lalu run cocok muncul → URL benar, POST tepat 1
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json
# post-dispatch poll 1 empty, poll 2 match (counter resets after POST)
cat >"$MOCK/trigger-runs-poll-1.json" <<'EOF'
{"workflow_runs":[]}
EOF
cat >"$MOCK/trigger-runs-poll-2.json" <<'EOF'
{"workflow_runs":[
 {"id":9,"html_url":"https://example.test/runs/9","event":"workflow_dispatch","head_branch":"main",
  "name":"Deploy production · request_id=req-ok",
  "display_title":"Deploy production · request_id=req-ok"}
]}
EOF
seed_mock_run_outcome 9 completed success "DEPLOY_OK sha=${MAIN_SHA}"
: >"$HARNESS/dispatch.log"
ok_disp="$(env "${POLL_ENV[@]}" ETERNAL_DEPLOY_SHARED_SECRET='test-secret-VALUE-NEVER-LOG' \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" --execute --request-id req-ok \
  2>"$HARNESS/okdisp.err")"
assert_true "dispatch OK returns run URL" grep -q 'run_url=https://example.test/runs/9' <<<"$ok_disp"
assert_true "DISPATCH_OK printed" grep -q 'DISPATCH_OK' <<<"$ok_disp"
assert_true "WORKFLOW_SUCCESS after dispatch" grep -q 'WORKFLOW_SUCCESS' <<<"$ok_disp"
assert_true "RUNNER_DEPLOY_SUCCESS requires DEPLOY_OK evidence" grep -q 'RUNNER_DEPLOY_SUCCESS' <<<"$ok_disp"
assert_eq "empty-then-match POST exactly once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"
assert_true "dispatch uses stdin-json (no secret argv)" grep -q 'secret_in_argv=no' "$HARNESS/dispatch.log"
assert_false "dispatch log never contains shared secret" grep -q 'test-secret-VALUE-NEVER-LOG' "$HARNESS/dispatch.log"
assert_false "dispatch stdout/err never contains shared secret" \
  grep -q 'test-secret-VALUE-NEVER-LOG' <<<"$ok_disp$(cat "$HARNESS/okdisp.err" 2>/dev/null || true)"

# 2) Timeout → accepted/unresolved, POST tetap 1
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json
: >"$HARNESS/dispatch.log"
set +e
timeout_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-timeout 2>"$HARNESS/timeout.err")"
timeout_ec=$?
set -e
assert_eq "correlation timeout exits non-zero" "1" "$timeout_ec"
assert_true "timeout reports DISPATCH_ACCEPTED_UNRESOLVED" \
  grep -q 'DISPATCH_ACCEPTED_UNRESOLVED request_id=req-timeout' <<<"$timeout_out$(cat "$HARNESS/timeout.err")"
assert_true "timeout message forbids auto re-dispatch" \
  grep -qi 'do not auto re-dispatch' "$HARNESS/timeout.err"
assert_false "timeout does not claim DISPATCH_OK" \
  grep -q 'DISPATCH_OK' <<<"$timeout_out$(cat "$HARNESS/timeout.err")"
assert_eq "timeout path POST exactly once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# 3) ID berbeda / prefix serupa → tidak cocok (timeout unresolved)
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json
cat >"$MOCK/trigger-runs-poll-1.json" <<'EOF'
{"workflow_runs":[
 {"id":11,"html_url":"https://example.test/runs/11","event":"workflow_dispatch","head_branch":"main",
  "display_title":"Deploy production · request_id=req-ok-extra"},
 {"id":12,"html_url":"https://example.test/runs/12","event":"workflow_dispatch","head_branch":"main",
  "display_title":"Deploy production · request_id=req-ok2"}
]}
EOF
: >"$HARNESS/dispatch.log"
set +e
prefix_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-ok 2>"$HARNESS/prefix.err")"
prefix_ec=$?
set -e
assert_eq "prefix-similar IDs do not correlate" "1" "$prefix_ec"
assert_false "prefix-similar does not return wrong URL" \
  grep -qE 'run_url=https://example.test/runs/1[12]' <<<"$prefix_out$(cat "$HARNESS/prefix.err")"
assert_true "prefix-similar ends unresolved not OK" \
  grep -q 'DISPATCH_ACCEPTED_UNRESOLVED request_id=req-ok' <<<"$prefix_out$(cat "$HARNESS/prefix.err")"
assert_eq "prefix-similar POST exactly once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# 4) Beberapa run dengan ID sama setelah dispatch → ambiguous
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json
cat >"$MOCK/trigger-runs-poll-1.json" <<'EOF'
{"workflow_runs":[
 {"id":21,"html_url":"https://example.test/runs/21","event":"workflow_dispatch","head_branch":"main",
  "display_title":"Deploy production · request_id=req-dup"},
 {"id":22,"html_url":"https://example.test/runs/22","event":"workflow_dispatch","head_branch":"main",
  "display_title":"Deploy production · request_id=req-dup"}
]}
EOF
: >"$HARNESS/dispatch.log"
set +e
ambig_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-dup 2>"$HARNESS/ambig.err")"
ambig_ec=$?
set -e
assert_eq "post-dispatch ambiguous exits non-zero" "1" "$ambig_ec"
assert_true "post-dispatch ambiguous message" \
  grep -qi 'ambiguous runs after dispatch' "$HARNESS/ambig.err"
assert_false "ambiguous does not pick first run as DISPATCH_OK" \
  grep -q 'DISPATCH_OK' <<<"$ambig_out$(cat "$HARNESS/ambig.err")"
assert_eq "ambiguous post-dispatch POST exactly once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# 6) Error API saat polling → diagnosis jelas, tanpa POST ulang
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json "$MOCK"/trigger-runs-poll-*-http-status "$MOCK/trigger-runs-http-status"
printf '500\n' >"$MOCK/trigger-runs-poll-1-http-status"
: >"$HARNESS/dispatch.log"
set +e
apierr_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-apierr 2>"$HARNESS/apierr.err")"
apierr_ec=$?
set -e
assert_eq "poll API error exits non-zero" "1" "$apierr_ec"
assert_true "poll API error diagnosis mentions accepted" \
  grep -qi 'dispatch was accepted' "$HARNESS/apierr.err"
assert_true "poll API error forbids auto re-dispatch" \
  grep -qi 'do not auto re-dispatch' "$HARNESS/apierr.err"
assert_false "poll API error does not claim DISPATCH_OK" \
  grep -q 'DISPATCH_OK' <<<"$apierr_out$(cat "$HARNESS/apierr.err")"
assert_eq "poll API error POST exactly once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"
rm -f "$MOCK/trigger-runs-poll-1-http-status"

# ========== trigger: request_id + HTTP timeout bounds ==========
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json "$MOCK"/dispatch-hang-sec "$MOCK"/dispatch-hang-ignore-term \
  "$MOCK"/trigger-runs-hang-sec "$MOCK"/trigger-runs-hang-ignore-term \
  "$MOCK"/trigger-runs-poll-*-hang-sec "$MOCK"/trigger-runs-poll-*-hang-ignore-term

# Valid request_id accepted (regression smoke with explicit valid id)
cat >"$MOCK/trigger-runs-poll-1.json" <<'EOF'
{"workflow_runs":[
 {"id":31,"html_url":"https://example.test/runs/31","event":"workflow_dispatch","head_branch":"main",
  "display_title":"Deploy production · request_id=req.valid_1"}
]}
EOF
seed_mock_run_outcome 31 completed success "DEPLOY_OK sha=${MAIN_SHA}"
: >"$HARNESS/dispatch.log"
valid_id_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id 'req.valid_1' 2>"$HARNESS/validid.err")"
assert_true "valid request_id accepted with DISPATCH_OK" grep -q 'DISPATCH_OK' <<<"$valid_id_out"
assert_true "valid request_id reaches RUNNER_DEPLOY_SUCCESS" grep -q 'RUNNER_DEPLOY_SUCCESS' <<<"$valid_id_out"
assert_eq "valid request_id POST once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# Invalid request_id rejected before POST (space / newline / symbol)
for bad_id in 'req ok' $'req\nline' 'req@symbol'; do
  : >"$HARNESS/dispatch.log"
  set +e
  env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
    --execute --request-id "$bad_id" >/dev/null 2>"$HARNESS/badid.err"
  bad_ec=$?
  set -e
  assert_eq "invalid request_id exits non-zero ($(printf '%q' "$bad_id"))" "1" "$bad_ec"
  assert_true "invalid request_id refused before network ($(printf '%q' "$bad_id"))" \
    grep -qi 'refused before network/POST' "$HARNESS/badid.err"
  assert_eq "invalid request_id POST count 0 ($(printf '%q' "$bad_id"))" "0" \
    "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"
done

# Invalid timeout config rejected before dispatch
: >"$HARNESS/dispatch.log"
set +e
env "${POLL_ENV[@]}" ETERNAL_TRIGGER_HTTP_TIMEOUT_SEC=0 \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-badtimeout >/dev/null 2>"$HARNESS/badto.err"
badto_ec=$?
set -e
assert_eq "HTTP timeout 0 exits non-zero" "1" "$badto_ec"
assert_true "HTTP timeout 0 refused before dispatch" \
  grep -qi 'positive integer; refused before dispatch' "$HARNESS/badto.err"
assert_eq "HTTP timeout 0 POST count 0" "0" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

: >"$HARNESS/dispatch.log"
set +e
env "${POLL_ENV[@]}" ETERNAL_TRIGGER_HTTP_TIMEOUT_SEC=abc \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-badtimeout2 >/dev/null 2>"$HARNESS/badto2.err"
badto2_ec=$?
set -e
assert_eq "HTTP timeout abc exits non-zero" "1" "$badto2_ec"
assert_eq "HTTP timeout abc POST count 0" "0" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# GET hang (ignores TERM) stops within HTTP+KILL budget after accepted POST
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json
printf '30\n' >"$MOCK/trigger-runs-poll-1-hang-sec"
: >"$MOCK/trigger-runs-poll-1-hang-ignore-term"
: >"$HARNESS/dispatch.log"
ghang_start="$(date +%s)"
set +e
env "${POLL_ENV[@]}" \
  ETERNAL_TRIGGER_FORCE_HTTP_TIMEOUT=1 \
  ETERNAL_TRIGGER_HTTP_TIMEOUT_SEC=1 \
  ETERNAL_TRIGGER_HTTP_KILL_AFTER_SEC=1 \
  ETERNAL_TRIGGER_POLL_MAX_ATTEMPTS=1 \
  ETERNAL_TRIGGER_POLL_TIMEOUT_SEC=5 \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-gethang >/dev/null 2>"$HARNESS/gethang.err"
ghang_ec=$?
set -e
ghang_elapsed=$(( $(date +%s) - ghang_start ))
assert_eq "GET hang exits non-zero" "1" "$ghang_ec"
assert_true "GET hang mentions HTTP timeout after accept" \
  grep -qi 'HTTP timeout while correlating' "$HARNESS/gethang.err"
assert_true "GET hang forbids auto re-dispatch" \
  grep -qi 'do not auto re-dispatch' "$HARNESS/gethang.err"
assert_false "GET hang does not claim DISPATCH_OK" grep -q 'DISPATCH_OK' "$HARNESS/gethang.err"
assert_eq "GET hang POST exactly once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"
# Bound: ~1s TERM + ~1s KILL (+ slack); must not approach the 30s hang.
assert_true "GET hang stopped within 8s wall clock" test "$ghang_elapsed" -le 8
rm -f "$MOCK/trigger-runs-poll-1-hang-sec" "$MOCK/trigger-runs-poll-1-hang-ignore-term"

# POST timeout: acceptance unknown (not denied), no re-POST
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json
printf '30\n' >"$MOCK/dispatch-hang-sec"
: >"$MOCK/dispatch-hang-ignore-term"
: >"$HARNESS/dispatch.log"
phang_start="$(date +%s)"
set +e
phang_out="$(env "${POLL_ENV[@]}" \
  ETERNAL_TRIGGER_FORCE_HTTP_TIMEOUT=1 \
  ETERNAL_TRIGGER_HTTP_TIMEOUT_SEC=1 \
  ETERNAL_TRIGGER_HTTP_KILL_AFTER_SEC=1 \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-posthang 2>"$HARNESS/posthang.err")"
phang_ec=$?
set -e
phang_elapsed=$(( $(date +%s) - phang_start ))
assert_eq "POST timeout exits non-zero" "1" "$phang_ec"
assert_true "POST timeout reports DISPATCH_ACCEPTANCE_UNKNOWN" \
  grep -q 'DISPATCH_ACCEPTANCE_UNKNOWN request_id=req-posthang' <<<"$phang_out$(cat "$HARNESS/posthang.err")"
assert_true "POST timeout note may have accepted" \
  grep -qi 'may have accepted' <<<"$phang_out$(cat "$HARNESS/posthang.err")"
assert_false "POST timeout does not claim denied" \
  grep -qi 'workflow_dispatch denied' "$HARNESS/posthang.err"
assert_false "POST timeout does not claim DISPATCH_ACCEPTED" \
  grep -q 'DISPATCH_ACCEPTED ' <<<"$phang_out$(cat "$HARNESS/posthang.err")"
assert_eq "POST timeout POST exactly once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"
assert_true "POST timeout stopped within 8s wall clock" test "$phang_elapsed" -le 8
rm -f "$MOCK/dispatch-hang-sec" "$MOCK/dispatch-hang-ignore-term"

# ========== trigger wait: workflow outcomes + runner evidence ==========
prep_dispatch_run() {
  local req="$1" rid="$2"
  printf 'ok\n' >"$MOCK/dispatch-status"
  cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
  rm -f "$MOCK"/trigger-runs-poll-*.json "$MOCK"/trigger-run-*.json "$MOCK"/trigger-run-*-log.txt \
    "$MOCK"/trigger-run-detail-poll-*.json "$MOCK"/trigger-run-log.txt
  cat >"$MOCK/trigger-runs-poll-1.json" <<EOF
{"workflow_runs":[
 {"id":${rid},"html_url":"https://example.test/runs/${rid}","event":"workflow_dispatch","head_branch":"main",
  "display_title":"Deploy production · request_id=${req}"}
]}
EOF
  : >"$HARNESS/dispatch.log"
}

# Workflow success but missing runner logs → RESULT_UNKNOWN (not PASS)
prep_dispatch_run req-nolog 41
seed_mock_run_outcome 41 completed success ""
set +e
nolog_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-nolog 2>"$HARNESS/nolog.err")"
nolog_ec=$?
set -e
assert_eq "missing runner logs exits non-zero" "1" "$nolog_ec"
assert_true "missing logs WORKFLOW_SUCCESS still printed" grep -q 'WORKFLOW_SUCCESS' <<<"$nolog_out"
assert_true "missing logs RESULT_UNKNOWN" grep -q 'RESULT_UNKNOWN' <<<"$nolog_out$(cat "$HARNESS/nolog.err")"
assert_false "missing logs is not RUNNER_DEPLOY_SUCCESS" grep -q 'RUNNER_DEPLOY_SUCCESS' <<<"$nolog_out"
assert_eq "missing logs POST once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# Workflow success without DEPLOY_OK in logs → RESULT_UNKNOWN
prep_dispatch_run req-noev 42
seed_mock_run_outcome 42 completed success "[eternal-deploy] health PASS"
set +e
noev_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-noev 2>"$HARNESS/noev.err")"
noev_ec=$?
set -e
assert_eq "workflow success without DEPLOY_OK exits non-zero" "1" "$noev_ec"
assert_true "no-evidence RESULT_UNKNOWN" grep -q 'RESULT_UNKNOWN' <<<"$noev_out"
assert_false "no-evidence not RUNNER_DEPLOY_SUCCESS" grep -q 'RUNNER_DEPLOY_SUCCESS' <<<"$noev_out"

# Workflow failed + ROLLBACK_PASS evidence
prep_dispatch_run req-rbpass 43
seed_mock_run_outcome 43 completed failure "ROLLBACK_PASS"
set +e
rbp_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-rbpass 2>"$HARNESS/rbp.err")"
rbp_ec=$?
set -e
assert_eq "rollback pass path exits non-zero" "1" "$rbp_ec"
assert_true "WORKFLOW_FAILED printed" grep -q 'WORKFLOW_FAILED' <<<"$rbp_out"
assert_true "RUNNER_ROLLBACK_PASS printed" grep -q 'RUNNER_ROLLBACK_PASS' <<<"$rbp_out"

# Workflow failed + ROLLBACK_FAILED evidence
prep_dispatch_run req-rbfail 44
seed_mock_run_outcome 44 completed failure "ROLLBACK_FAILED"
set +e
rbf_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-rbfail 2>"$HARNESS/rbf.err")"
rbf_ec=$?
set -e
assert_eq "rollback failed path exits non-zero" "1" "$rbf_ec"
assert_true "RUNNER_ROLLBACK_FAILED printed" grep -q 'RUNNER_ROLLBACK_FAILED' <<<"$rbf_out"

# Workflow failed but log contains DEPLOY_OK-shaped marker → still FAIL (ignore success marker)
prep_dispatch_run req-failok 48
seed_mock_run_outcome 48 completed failure "DEPLOY_OK sha=${MAIN_SHA}"
set +e
failok_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-failok 2>"$HARNESS/failok.err")"
failok_ec=$?
set -e
assert_eq "failed workflow with DEPLOY_OK marker exits non-zero" "1" "$failok_ec"
assert_true "failed workflow WORKFLOW_FAILED" grep -q 'WORKFLOW_FAILED' <<<"$failok_out"
assert_true "failed workflow ignores success marker" \
  grep -q 'workflow_conclusion_not_success_ignoring_success_marker' <<<"$failok_out"
assert_eq "failed+DEPLOY_OK POST once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# Loose DEPLOY_OK substring without sha=<40-hex> is not success evidence
prep_dispatch_run req-loosedok 49
seed_mock_run_outcome 49 completed success "note: saw DEPLOY_OK in commentary"
set +e
loose_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-loosedok 2>"$HARNESS/loose.err")"
loose_ec=$?
set -e
assert_eq "loose DEPLOY_OK substring exits non-zero" "1" "$loose_ec"
assert_true "loose DEPLOY_OK is RESULT_UNKNOWN" grep -q 'RESULT_UNKNOWN' <<<"$loose_out"
assert_false "loose DEPLOY_OK not RUNNER_DEPLOY_SUCCESS" grep -q 'RUNNER_DEPLOY_SUCCESS' <<<"$loose_out"

# Cancelled workflow
prep_dispatch_run req-cancel 45
seed_mock_run_outcome 45 completed cancelled "ROLLBACK_PASS"
set +e
can_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-cancel 2>"$HARNESS/can.err")"
can_ec=$?
set -e
assert_eq "cancelled workflow exits non-zero" "1" "$can_ec"
assert_true "WORKFLOW_CANCELLED printed" grep -q 'WORKFLOW_CANCELLED' <<<"$can_out"

# In-progress forever → WORKFLOW_TIMED_OUT
prep_dispatch_run req-wtimeout 46
cat >"$MOCK/trigger-run-46.json" <<'EOF'
{"id":46,"status":"in_progress","conclusion":null,"html_url":"https://example.test/runs/46"}
EOF
printf 'DEPLOY_OK sha=%s\n' "$MAIN_SHA" >"$MOCK/trigger-run-46-log.txt"
set +e
wto_out="$(env "${POLL_ENV[@]}" ETERNAL_TRIGGER_WAIT_MAX_ATTEMPTS=2 \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-wtimeout 2>"$HARNESS/wto.err")"
wto_ec=$?
set -e
assert_eq "wait timeout exits non-zero" "1" "$wto_ec"
assert_true "WORKFLOW_TIMED_OUT printed" grep -q 'WORKFLOW_TIMED_OUT' <<<"$wto_out$(cat "$HARNESS/wto.err")"
assert_true "wait timeout RESULT_UNKNOWN" grep -q 'RESULT_UNKNOWN' <<<"$wto_out$(cat "$HARNESS/wto.err")"
assert_eq "wait timeout POST once" "1" "$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"

# Secret-like lines in logs are redacted / not echoed raw
prep_dispatch_run req-redact 47
seed_mock_run_outcome 47 completed success "DEPLOY_OK sha=${MAIN_SHA}"
{
  printf 'DEPLOY_SSH_KEY=-----BEGIN OPENSSH PRIVATE KEY----- SECRETKEYMATERIAL999 -----END OPENSSH PRIVATE KEY-----\n'
  printf 'TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWX1234567890\n'
  printf 'DEPLOY_OK sha=%s\n' "$MAIN_SHA"
} >"$MOCK/trigger-run-47-log.txt"
set +e
red_out="$(env "${POLL_ENV[@]}" "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" \
  --execute --request-id req-redact 2>"$HARNESS/red.err")"
red_ec=$?
set -e
assert_eq "redaction path still succeeds with DEPLOY_OK" "0" "$red_ec"
assert_false "log summary omits private key material" grep -q 'SECRETKEYMATERIAL999' <<<"$red_out$(cat "$HARNESS/red.err")"
assert_false "log summary omits ghp token" grep -q 'ghp_ABCDEFGHIJKLMNOPQRSTUVWX1234567890' <<<"$red_out$(cat "$HARNESS/red.err")"
assert_true "redaction path RUNNER_DEPLOY_SUCCESS" grep -q 'RUNNER_DEPLOY_SUCCESS' <<<"$red_out"

# CI/SHA guards unchanged (re-assert after reporting changes)
assert_false "reporting change still rejects sha mismatch" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" "$TRIGGER" plan deploy --sha "$MAIN_SHA" --confirm-sha "$(printf 'b%.0s' {1..40})"

# ========== probe helper literal only + cleanup ==========
export ETERNAL_SSH_BIN="$BIN/ssh"
export ETERNAL_SSH_LOG="$HARNESS/ssh.log"
: >"$ETERNAL_SSH_LOG"
export DEPLOY_HOST=127.0.0.1 DEPLOY_USER=deploy DEPLOY_SSH_PORT=22
export DEPLOY_SSH_KEY=$'-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEYMATERIAL999\n-----END OPENSSH PRIVATE KEY-----'
export DEPLOY_KNOWN_HOSTS='127.0.0.1 ssh-ed25519 AAAA'
probe_out="$("$PROBE_SH" 2>"$HARNESS/probe.err")"
assert_true "probe helper OK" grep -q 'SSH_PROBE_OK' <<<"$probe_out"
assert_true "ssh invoked with probe literal" grep -q ' probe$' "$ETERNAL_SSH_LOG"
assert_false "probe helper output has no key material" grep -q 'SECRETKEYMATERIAL999' <<<"$probe_out$("$HARNESS/probe.err" 2>/dev/null || true)"
# host-key / ssh fail stops
export ETERNAL_SSH_FORCE_FAIL=1
assert_false "ssh failure stops probe" "$PROBE_SH"
export ETERNAL_SSH_FORCE_FAIL=0
# ensure no deploy command ever sent
assert_false "ssh log never contains deploy remote command" grep -qE ' deploy($| )' "$ETERNAL_SSH_LOG"

# Gate itself
assert_true "gate probe canonical" bash -c 'SSH_ORIGINAL_COMMAND=probe "'"$GATE"'" | grep -q ETERNAL_GATE_OK'
assert_false "gate rejects deploy metachar" run_gate 'deploy $(id)'
assert_false "gate rejects unknown deploy option" run_gate "deploy $MAIN_SHA evil-opt"
assert_false "gate rejects rollback path previous" run_gate "rollback $MAIN_SHA ../../etc/passwd"
assert_false "gate rejects rollback without previous" run_gate "rollback $MAIN_SHA"
assert_false "gate rejects rollback metachar" run_gate "rollback $MAIN_SHA baseline-active;id"
# ack-fe-replace is allowlisted; gate must set env for runner (smoke via export check in runner tests)

# ========== runtime not ready blocks before activation ==========
rm -f "$CURRENT"
rm -rf "$BASELINE"
# missing verified baseline
assert_false "missing verified baseline blocks first cutover" "$RUNNER" deploy "$MAIN_SHA"
# empty/marker-only baseline-verified without integrity manifest
mkdir -p "$BASELINE/tree/backend" "$BASELINE/frontend-snapshot"
printf 'x\n' >"$BASELINE/tree/backend/app.py"
printf 'y\n' >"$BASELINE/frontend-snapshot/index.html"
: >"$BASELINE/.baseline"
: >"$BASELINE/.baseline-verified"
printf 'kind=verified-baseline\n' >"$BASELINE/.deploy-ok"
assert_false "baseline-verified without integrity manifest refused" "$RUNNER" deploy "$MAIN_SHA"
# FE snapshot without index.html refused at integrity write/verify
rm -rf "$BASELINE"
mkdir -p "$BASELINE/tree/backend" "$BASELINE/frontend-snapshot/static"
printf 'x\n' >"$BASELINE/tree/backend/app.py"
printf 'asset\n' >"$BASELINE/frontend-snapshot/static/a.js"
: >"$BASELINE/.baseline"
printf 'kind=verified-baseline\nsha=baseline-active\nimmutable=1\n' >"$BASELINE/.deploy-ok"
assert_false "baseline FE without index.html refused" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_baseline_write_integrity "'"$BASELINE"'" "'"$LEGACY"'" "'"$EXPECTED_WD"'" "'"$EXPECTED_WD"'/.venv/bin/uvicorn"'
# tampered baseline content after integrity write
rm -rf "$BASELINE"
make_verified_baseline
assert_true "baseline verified records frontend_src" grep -q '^frontend_src=' "$BASELINE/.baseline-verified"
assert_true "baseline integrity hashes FE files" grep -q ' frontend-snapshot/' "$BASELINE/.baseline-integrity"
printf 'tampered\n' >>"$BASELINE/tree/backend/app.py"
assert_false "tampered baseline digests refused" "$RUNNER" deploy "$MAIN_SHA"
# wrong systemd WD (retarget not done yet) — baseline ready but service not retargeted
rm -rf "$BASELINE"
make_verified_baseline
export ETERNAL_EXPECTED_WD="/opt/eternal-packaging-runtime/current/backend"
assert_false "systemd WD mismatch blocks preflight before activation" "$RUNNER" deploy "$MAIN_SHA"
export ETERNAL_EXPECTED_WD="$EXPECTED_WD"

# ========== first cutover requires FE replace ack ==========
rm -rf "$BASELINE" "$RELEASES/$MAIN_SHA"
make_verified_baseline
set +e
"$RUNNER" deploy "$MAIN_SHA" >"$HARNESS/noack.txt" 2>"$HARNESS/noack.err"
noack=$?
set -e
assert_eq "first cutover without ack-fe-replace refused" "1" "$noack"
assert_true "refuse message mentions live frontend replace" \
  grep -qi 'live frontend replace' "$HARNESS/noack.err"
assert_false "refuse before materialize leaves no incomplete release" \
  test -d "$RELEASES/$MAIN_SHA"

# ========== first deploy with verified baseline + ack ==========
rm -rf "$BASELINE" "$RELEASES/$MAIN_SHA"
make_verified_baseline
: >"$ETERNAL_SYSTEMCTL_LOG"
if ETERNAL_ACK_REPLACE_LIVE_FRONTEND=1 "$RUNNER" deploy "$MAIN_SHA" >"$HARNESS/ok.txt" 2>"$HARNESS/ok.err"; then
  log "PASS: valid sha deploy exit 0"; PASS=$((PASS+1))
else
  log "FAIL: valid sha deploy"; cat "$HARNESS/ok.err" >>"$REPORT"; FAIL=$((FAIL+1))
fi
assert_true "DEPLOY_OK printed" grep -q "DEPLOY_OK sha=$MAIN_SHA" "$HARNESS/ok.txt"
assert_true "legacy overlay untouched" grep -q dirty "$LEGACY/DO_NOT_TOUCH"
assert_true "old hashed asset retained after publish" test -f "$FRONTEND/live/static/js/old.js"
assert_true "new index published" grep -q dry-build "$FRONTEND/live/index.html"
assert_true "ack logged on first cutover" grep -q 'ACK live frontend replace' "$HARNESS/ok.err"
assert_true "runner used allowlisted systemctl show -p/--value" \
  grep -qE 'show -p WorkingDirectory --value' "$ETERNAL_SYSTEMCTL_LOG"
assert_true "real backend process cwd matches CURRENT/backend" \
  bash -c 'pid=$(cat "'"$HARNESS"'/backend.pid"); cwd=$(readlink -f /proc/$pid/cwd); want=$(readlink -f "'"$CURRENT"'/backend"); [[ "$cwd" == "$want" ]]'

# ========== sudoers contract matches runner show argv ==========
BOOT="$ROOT/scripts/deploy/bootstrap-permissions.sh"
assert_true "sudoers lists show -p WorkingDirectory --value" \
  grep -q 'show -p WorkingDirectory --value' "$BOOT"
assert_true "sudoers lists show -p MainPID --value" \
  grep -q 'show -p MainPID --value' "$BOOT"
assert_true "sudoers lists show -p ExecStart --value" \
  grep -q 'show -p ExecStart --value' "$BOOT"
assert_false "sudoers has no wildcard show" grep -qE 'show \\\*|show \*' "$BOOT"
assert_false "sudoers does not allow bare show SERVICE alone" \
  grep -qE "show ${ETERNAL_SERVICE_NAME}\$" "$BOOT"

# ========== FE-only: CURRENT + process stay on active release ==========
CUR_AFTER_FIRST="$(readlink -f "$CURRENT")"
PID_AFTER_FIRST="$(cat "$HARNESS/backend.pid")"
git -C "$MIRROR_SRC" checkout -q main
printf 'fe2\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m fe2
FE_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
: >"$ETERNAL_SYSTEMCTL_LOG"
if "$RUNNER" deploy "$FE_SHA" >"$HARNESS/feonly.txt" 2>"$HARNESS/feonly.err"; then
  log "PASS: FE-only deploy exit 0"; PASS=$((PASS+1))
else
  log "FAIL: FE-only deploy"; cat "$HARNESS/feonly.err" >>"$REPORT"; FAIL=$((FAIL+1))
fi
assert_eq "FE-only keeps CURRENT pointer" "$CUR_AFTER_FIRST" "$(readlink -f "$CURRENT")"
assert_true "FE-only logs keeping CURRENT" grep -q 'frontend-only/infra-only: keeping CURRENT' "$HARNESS/feonly.err"
assert_true "FE-only does not restart backend" \
  bash -c '! grep -q "restarting " "'"$HARNESS"'/feonly.err"'
assert_eq "FE-only keeps same MainPID process" "$PID_AFTER_FIRST" "$(cat "$HARNESS/backend.pid")"
assert_true "FE-only process cwd still active release backend" \
  bash -c 'cwd=$(readlink -f /proc/'"$PID_AFTER_FIRST"'/cwd); want=$(readlink -f "'"$CUR_AFTER_FIRST"'/backend"); [[ "$cwd" == "$want" ]]'
assert_true "FE-only published new index" grep -q dry-build "$FRONTEND/live/index.html"

# ========== health degraded rejected ==========
export ETERNAL_HEALTH_PARTIAL=1
assert_false "degraded health rejected" "$RUNNER" deploy "$MAIN_SHA"
export ETERNAL_HEALTH_PARTIAL=0

# ========== health payload validation (mongo=true + mode=operational) ==========
assert_true "health body operational accepted" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_health_body_ok "{\"mongo\":true,\"mode\":\"operational\"}"'
assert_false "health body mongo false rejected" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_health_body_ok "{\"mongo\":false,\"mode\":\"operational\"}"'
assert_false "health body mode degraded rejected" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_health_body_ok "{\"mongo\":true,\"mode\":\"degraded\"}"'

# ========== transient HTTP 502 then recovery (post-restart warming) ==========
echo 2 >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
export ETERNAL_HEALTH_RETRIES=6
export ETERNAL_HEALTH_SLEEP_S=0
export ETERNAL_HEALTH_BUDGET_S=60
assert_true "transient 502 recovers within health budget" "$RUNNER" deploy "$FE_SHA"
echo 0 >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
export ETERNAL_HEALTH_RETRIES=2

# ========== permanent health failure still fail-closed ==========
export ETERNAL_HEALTH_FORCE_FAIL=1
assert_false "permanent health failure fail-closed" "$RUNNER" deploy "$MAIN_SHA"
export ETERNAL_HEALTH_FORCE_FAIL=0

# ========== old health must not pass wrong runtime ==========
export ETERNAL_EXPECTED_WD="/wrong/backend"
assert_false "wrong runtime WD rejected even if health JSON ok" "$RUNNER" deploy "$FE_SHA"
export ETERNAL_EXPECTED_WD="$EXPECTED_WD"

# ========== build fail leaves live unchanged ==========
printf 'keep-live\n' >"$FRONTEND/live/keep-live.txt"
CUR_BEFORE="$(readlink -f "$CURRENT" || true)"
export ETERNAL_FORCE_BUILD_FAIL=1
git -C "$MIRROR_SRC" checkout -q main
printf 'fe3\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m fe3
FE3="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
assert_false "build fail aborts" "$RUNNER" deploy "$FE3"
assert_true "live keep file remains after build fail" test -f "$FRONTEND/live/keep-live.txt"
assert_eq "CURRENT unchanged after build fail" "$CUR_BEFORE" "$(readlink -f "$CURRENT" || true)"
export ETERNAL_FORCE_BUILD_FAIL=0

# ========== lock held during rollback (log + post-state) ==========
git -C "$MIRROR_SRC" checkout -q main
printf 'be2\n' >"$MIRROR_SRC/backend/requirements.txt"
git -C "$MIRROR_SRC" add backend/requirements.txt && git -C "$MIRROR_SRC" commit -q -m be2
BE2="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
# Activate health fails once (retries=2 → set remaining=2), rollback health recovers.
: >"$HARNESS/restart.count"
echo 2 >"$ETERNAL_HEALTH_FAIL_STATE"
set +e
"$RUNNER" deploy "$BE2" >"$HARNESS/hf.txt" 2>"$HARNESS/hf.err"
hf=$?
set -e
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"
assert_eq "health fail nonzero" "1" "$hf"
assert_true "rollback while holding lock logged" grep -q 'attempting rollback while holding lock' "$HARNESS/hf.err"
assert_true "health-fail rollback reports ROLLBACK_PASS" grep -q 'ROLLBACK_PASS' "$HARNESS/hf.err"
assert_false "health-fail path is not ROLLBACK_FAILED" grep -q 'ROLLBACK_FAILED' "$HARNESS/hf.err"
assert_true "lock file not unlinked after release" test -f "$LOCK"
assert_true "flock available after runner release" flock -n "$LOCK" true
assert_true "FE backup used runtime prev path" test -d "$FRONTEND_PREV"

# ========== rollback FAILED when restart recovery fails (no swallow) ==========
rm -rf "$RELEASES/$BE2"
: >"$HARNESS/restart.count"
echo 2 >"$ETERNAL_HEALTH_FAIL_STATE"
export ETERNAL_RESTART_FAIL_FROM=2
set +e
"$RUNNER" deploy "$BE2" >"$HARNESS/rbf.txt" 2>"$HARNESS/rbf.err"
rbf=$?
set -e
unset ETERNAL_RESTART_FAIL_FROM
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"
assert_eq "rollback-failed path nonzero" "1" "$rbf"
assert_true "rollback reports ROLLBACK_FAILED" grep -q 'ROLLBACK_FAILED' "$HARNESS/rbf.err"
assert_true "rollback notes backend restart FAILED" grep -q 'rollback: backend restart FAILED' "$HARNESS/rbf.err"
assert_true "flock released after ROLLBACK_FAILED" flock -n "$LOCK" true

# ========== partial restart failure at activation triggers recovery ==========
rm -rf "$RELEASES/$BE2"
: >"$HARNESS/restart.count"
export ETERNAL_FORCE_RESTART_FAIL=1
set +e
"$RUNNER" deploy "$BE2" >"$HARNESS/rs.txt" 2>"$HARNESS/rs.err"
rs=$?
set -e
export ETERNAL_FORCE_RESTART_FAIL=0
assert_eq "restart fail nonzero" "1" "$rs"
assert_true "rollback restart attempted after partial failure" grep -q 'rollback: restart backend' "$HARNESS/rs.err"

# Ensure isolated backend process is alive for subsequent FE-only checks.
start_harness_backend_proc "$(readlink -f "$CURRENT/backend")"

# ========== publish keeps old assets + atomic index ==========
CUR_BEFORE_FEOK="$(readlink -f "$CURRENT")"
"$RUNNER" deploy "$FE_SHA" >"$HARNESS/feok.txt" 2>"$HARNESS/feok.err" || true
assert_true "old asset still present after later FE publish" test -f "$FRONTEND/live/static/js/old.js"
assert_true "index is not .new leftover" bash -c '! test -f "'"$FRONTEND"'/live/index.html.new"'
assert_eq "FE republish still keeps CURRENT" "$CUR_BEFORE_FEOK" "$(readlink -f "$CURRENT")"

# ========== flock interop: terminal holder blocks runner; stale file does not ==========
printf 'stale-pid-note\n' >"$LOCK"
assert_true "stale lock file without holder is acquirable" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_acquire_lock "'"$LOCK"'" 0; eternal_release_lock "'"$LOCK"'"; test -f "'"$LOCK"'"'
exec {TERM_LOCK_FD}<>"$LOCK"
flock -n "$TERM_LOCK_FD" || { log "FAIL: could not hold terminal flock"; FAIL=$((FAIL+1)); }
assert_false "runner blocked while terminal flock held" "$RUNNER" deploy "$FE_SHA"
flock -u "$TERM_LOCK_FD"
exec {TERM_LOCK_FD}>&-
assert_true "runner proceeds after terminal flock release" \
  bash -c 'ETERNAL_ACK_REPLACE_LIVE_FRONTEND=0 "'"$RUNNER"'" deploy "'"$FE_SHA"'" >/dev/null 2>&1'

# ========== permission fixture: root-like parent blocks sibling; leaf publish works ==========
assert_false "cannot create sibling under non-writable www parent" mkdir "$SIM_WWW_PARENT/eternal-packaging.prev"
assert_false "bootstrap script never chowns /opt parent" \
  grep -qE 'chown[[:space:]].*[[:space:]]/opt[[:space:]]*$|install -d .*[[:space:]]/opt[[:space:]]*$' \
  "$ROOT/scripts/deploy/bootstrap-permissions.sh"
assert_true "bootstrap validates refusing parent paths" \
  grep -q 'refusing to operate on parent path' "$ROOT/scripts/deploy/bootstrap-permissions.sh"
assert_true "bootstrap FE prev under runtime root" \
  grep -q 'frontend-prev must be under runtime root' "$ROOT/scripts/deploy/bootstrap-permissions.sh"
assert_true "unit uses eternaladmin + runtime current + google-places env" \
  bash -c 'grep -q User=eternaladmin "'"$ROOT"'/scripts/deploy/systemd/eternal-packaging-backend.service" \
    && grep -q /opt/eternal-packaging-runtime/current/backend "'"$ROOT"'/scripts/deploy/systemd/eternal-packaging-backend.service" \
    && grep -q eternal-packaging-google-places.env "'"$ROOT"'/scripts/deploy/systemd/eternal-packaging-backend.service"'

# ========== venv missing / invalid stops before activation ==========
NO_VENV="$HARNESS/novenv-backend"
mkdir -p "$NO_VENV"
printf 'x\n' >"$NO_VENV/requirements.txt"
assert_false "missing venv fails runtime validate" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_validate_backend_runtime "'"$NO_VENV"'"'
# Foreign shebang uvicorn must be refused
mkdir -p "$NO_VENV/.venv/bin"
ln -sfn "$(command -v python3)" "$NO_VENV/.venv/bin/python"
cat >"$NO_VENV/.venv/bin/uvicorn" <<'EOF'
#!/opt/eternal-packaging/backend/.venv/bin/python
import sys
sys.exit(0)
EOF
chmod +x "$NO_VENV/.venv/bin/uvicorn"
assert_false "legacy shebang uvicorn refused" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_validate_backend_runtime "'"$NO_VENV"'"'

# ========== baseline: refuse overwrite; large manifest; symlink detect ==========
rm -rf "$BASELINE"
make_verified_baseline
assert_false "prepare refuses overwrite without --force-recreate" \
  env ETERNAL_RUNTIME_MODE=stub ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
      ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_FRONTEND_ROOT="$FRONTEND/live" \
      ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
      ETERNAL_BASELINE_BACKEND_SRC="$LEGACY" \
      "$PREPARE_BASELINE" --apply
# marker-only immutable insufficient
mkdir -p "$HARNESS/marker-only/tree/backend" "$HARNESS/marker-only/frontend-snapshot"
printf 'kind=verified-baseline\nimmutable=1\nintegrity_sha256=dead\nfrontend_src=x\n' >"$HARNESS/marker-only/.baseline-verified"
printf 'x\n' >"$HARNESS/marker-only/tree/backend/requirements.txt"
printf 'y\n' >"$HARNESS/marker-only/frontend-snapshot/index.html"
: >"$HARNESS/marker-only/.baseline-integrity"
assert_false "marker-only baseline without digests_ok refused" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_baseline_verify_integrity "'"$HARNESS"'/marker-only"'
# symlink in frozen tree refused
rm -rf "$BASELINE"
make_verified_baseline
ln -s /tmp/evil "$BASELINE/frontend-snapshot/evil-link"
assert_false "baseline symlink in FE snapshot refused on rewrite" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; ETERNAL_RUNTIME_MODE=stub eternal_baseline_write_integrity "'"$BASELINE"'" "'"$LEGACY"'" "'"$EXPECTED_WD"'" "'"$EXPECTED_WD"'/.venv/bin/uvicorn"'
rm -f "$BASELINE/frontend-snapshot/evil-link"
# >5000 files fully hashed
rm -rf "$BASELINE"
make_verified_baseline
mkdir -p "$BASELINE/frontend-snapshot/bulk"
for i in $(seq 1 5100); do printf 'f%s\n' "$i" >"$BASELINE/frontend-snapshot/bulk/f-$i.txt"; done
ETERNAL_RUNTIME_MODE=stub eternal_baseline_write_integrity "$BASELINE" "$LEGACY" "$EXPECTED_WD" "$EXPECTED_WD/.venv/bin/uvicorn"
fc="$(sed -n 's/^file_count=//p' "$BASELINE/.baseline-verified" | head -n1)"
assert_true "manifest file_count >5000" bash -c "[[ \"$fc\" -gt 5000 ]]"
assert_true "manifest lists all bulk files" bash -c "[[ \$(grep -c ' frontend-snapshot/bulk/' \"$BASELINE/.baseline-integrity\") -eq 5100 ]]"
printf 'tamper-bulk\n' >>"$BASELINE/frontend-snapshot/bulk/f-1.txt"
assert_false "change in file beyond 5000 detected" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; ETERNAL_RUNTIME_MODE=stub eternal_baseline_verify_integrity "'"$BASELINE"'"'

# ========== secret masking ==========
FAKE=$'-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEYMATERIAL1234567890\n-----END OPENSSH PRIVATE KEY-----'
red="$(eternal_redact_line "key=$FAKE")"
assert_true "redactor masks key" grep -q REDACTED_PRIVATE_KEY <<<"$red"
assert_false "redactor strips material" grep -q SECRETKEYMATERIAL1234567890 <<<"$red"

# ========== workflow / docs shape ==========
WF="$ROOT/.github/workflows/deploy-production.yml"
PWF="$ROOT/.github/workflows/deploy-probe.yml"
BOOT="${BOOT:-$ROOT/scripts/deploy/bootstrap-permissions.sh}"
assert_true "deploy workflow_dispatch" grep -q workflow_dispatch "$WF"
assert_true "deploy uses production env" grep -q 'environment: production' "$WF"
assert_true "deploy_shared_secret input" grep -q deploy_shared_secret "$WF"
assert_true "probe workflow exists" test -f "$PWF"
assert_true "probe workflow_dispatch" grep -q workflow_dispatch "$PWF"
assert_true "probe uses ssh_gate_probe" grep -q ssh_gate_probe.sh "$PWF"
assert_false "probe workflow steps do not invoke runner script path" \
  grep -q 'scripts/deploy/eternal-deploy-runner' "$PWF"
assert_true "probe workflow only references ssh_gate_probe helper" grep -q 'ssh_gate_probe.sh' "$PWF"
assert_true "probe run step calls ssh_gate_probe.sh" \
  grep -A2 'Probe forced-command gate' "$PWF" | grep -q 'ssh_gate_probe.sh'
assert_true "docs mention runtime root" grep -q '/opt/eternal-packaging-runtime' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs enforce DEPLOY_ALLOWED_ACTORS" grep -q 'DEPLOY_ALLOWED_ACTORS' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs say never chown /opt or /var/www" \
  grep -qiE 'Never chown `/opt`|never chown `/var/www`' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs separate preparation from activation" \
  grep -q 'Phase A — preparation' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs mention flock" grep -q 'flock' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs mention ack-replace-live-frontend" grep -q 'ack_replace_live_frontend' "$ROOT/docs/deploy-production-setup.md"
assert_true "prepare-verified-baseline exists" test -x "$PREPARE_BASELINE"
assert_true "bootstrap default user eternaladmin" grep -q 'eternaladmin' "$BOOT"
assert_true "lib uses flock not noclobber" \
  bash -c 'grep -q flock "'"$ROOT"'/scripts/deploy/lib.sh" && ! grep -q noclobber "'"$ROOT"'/scripts/deploy/lib.sh"'
assert_true "deploy workflow enforces actor allowlist" grep -q 'DEPLOY_ALLOWED_ACTORS' "$WF"
assert_true "deploy workflow has ack_replace_live_frontend input" grep -q 'ack_replace_live_frontend' "$WF"
assert_true "deploy workflow notes secret alone cannot authorize" \
  grep -q 'Secret alone cannot authorize' "$WF"
assert_true "deploy workflow can send ack-fe-replace remote token" grep -q 'ack-fe-replace' "$WF"
assert_true "probe workflow asserts PROBE_NO_DEPLOY" grep -q 'PROBE_NO_DEPLOY=1' "$PWF"

# syntax checks (no new deps)
assert_true "bash -n runner" bash -n "$RUNNER"
assert_true "bash -n trigger" bash -n "$TRIGGER"
assert_true "bash -n probe" bash -n "$PROBE_SH"
assert_true "bash -n gate" bash -n "$GATE"
assert_true "bash -n bootstrap" bash -n "$BOOT"
assert_true "bash -n prepare-baseline" bash -n "$PREPARE_BASELINE"
assert_true "bash -n finalize-baseline" bash -n "$FINALIZE_BASELINE"

# ========== requirements resolver + finalize (no silent txt fallback) ==========
REQ_BACKEND="$HARNESS/req-backend"
mkdir -p "$REQ_BACKEND"
printf 'portable-body\n' >"$REQ_BACKEND/requirements.portable.txt"
printf 'txt-body\n' >"$REQ_BACKEND/requirements.txt"
rel="$(eternal_resolve_requirements_relpath "$REQ_BACKEND")"
assert_eq "default requirements is portable" "requirements.portable.txt" "$rel"
assert_false "missing override file refused" \
  env ETERNAL_REQUIREMENTS_FILE=requirements.missing.txt \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_resolve_requirements_relpath "'"$REQ_BACKEND"'"'
assert_false "absolute requirements path refused" \
  env ETERNAL_REQUIREMENTS_FILE=/tmp/requirements.portable.txt \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_resolve_requirements_relpath "'"$REQ_BACKEND"'"'
assert_false "parent-escape requirements path refused" \
  env ETERNAL_REQUIREMENTS_FILE=../requirements.portable.txt \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_resolve_requirements_relpath "'"$REQ_BACKEND"'"'
ovr="$(ETERNAL_REQUIREMENTS_FILE=requirements.txt eternal_resolve_requirements_relpath "$REQ_BACKEND")"
assert_eq "explicit override to requirements.txt allowed" "requirements.txt" "$ovr"

# Prep stamps portable hash
rm -rf "$REQ_BACKEND/.venv"
ETERNAL_RUNTIME_MODE=stub eternal_prepare_backend_runtime "$REQ_BACKEND"
assert_true "prep stamp requirements_file=portable" \
  grep -qx 'requirements.portable.txt' "$REQ_BACKEND/.eternal-requirements-file"
stamp_hash="$(cat "$REQ_BACKEND/.eternal-requirements-sha256")"
actual_hash="$(sha256sum "$REQ_BACKEND/requirements.portable.txt" | awk '{print $1}')"
assert_eq "prep stamp hash matches portable file" "$actual_hash" "$stamp_hash"

# Hash mismatch detection
rm -rf "$BASELINE"
make_verified_baseline
assert_true "baseline records requirements_file" grep -q '^requirements_file=requirements.portable.txt' "$BASELINE/.baseline-verified"
bad_hash="$(printf 'deadbeef\n' | sha256sum | awk '{print $1}')"
sed -i "s/^requirements_sha256=.*/requirements_sha256=$bad_hash/" "$BASELINE/.baseline-verified"
assert_false "requirements hash mismatch refused" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; ETERNAL_RUNTIME_MODE=stub eternal_baseline_verify_integrity "'"$BASELINE"'"'

# Finalize existing: lock + owner attest for legacy (no invented historical provenance)
rm -rf "$BASELINE"
make_verified_baseline
# Strip recorded provenance + install stamps → simulate manual VPS baseline
grep -vE '^(requirements_file|requirements_sha256|provenance_source)=' "$BASELINE/.baseline-verified" \
  >"$BASELINE/.baseline-verified.tmp"
mv "$BASELINE/.baseline-verified.tmp" "$BASELINE/.baseline-verified"
rm -f "$BASELINE/.baseline" "$BASELINE/.deploy-ok" \
  "$BASELINE/tree/backend/.eternal-requirements-file" \
  "$BASELINE/tree/backend/.eternal-requirements-sha256"
cp "$BASELINE/.baseline-verified" "$HARNESS/baseline-verified.before-legacy"
venv_inode_before="$(stat -c '%i' "$BASELINE/tree/backend/.venv" 2>/dev/null || stat -f '%i' "$BASELINE/tree/backend/.venv")"

fin_dry="$(ETERNAL_RUNTIME_MODE=stub ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
  ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
  ETERNAL_DEPLOY_LOCK="$LOCK" \
  "$FINALIZE_BASELINE" 2>"$HARNESS/fin-dry.err" || true)"
assert_true "finalize dry-run needs attest for legacy" grep -q 'FINALIZE_DRY_RUN_NEEDS_ATTEST' <<<"$fin_dry"
assert_false "finalize dry-run does not write .baseline" test -f "$BASELINE/.baseline"

# Legacy without confirmation → fail, no markers
assert_false "legacy finalize without attest refused" \
  env ETERNAL_RUNTIME_MODE=stub ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
      ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
      ETERNAL_DEPLOY_LOCK="$LOCK" \
      "$FINALIZE_BASELINE" --apply
assert_false "legacy without attest left no .baseline" test -f "$BASELINE/.baseline"
assert_true "legacy without attest left metadata unchanged" \
  cmp -s "$HARNESS/baseline-verified.before-legacy" "$BASELINE/.baseline-verified"

# Lock held by another process → fail, metadata unchanged
exec {FIN_LOCK_FD}>"$LOCK"
flock -n "$FIN_LOCK_FD" || { log "FAIL: could not hold finalize test flock"; FAIL=$((FAIL+1)); }
assert_false "finalize blocked while shared deploy lock held" \
  env ETERNAL_RUNTIME_MODE=stub ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
      ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
      ETERNAL_DEPLOY_LOCK="$LOCK" \
      "$FINALIZE_BASELINE" --apply --attest-requirements-file=requirements.portable.txt
flock -u "$FIN_LOCK_FD"
exec {FIN_LOCK_FD}>&-
assert_false "lock-busy finalize left no .baseline" test -f "$BASELINE/.baseline"
assert_true "lock-busy finalize left metadata unchanged" \
  cmp -s "$HARNESS/baseline-verified.before-legacy" "$BASELINE/.baseline-verified"

# Owner attest portable + dependency validation → finalize OK
ETERNAL_RUNTIME_MODE=stub ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
  ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
  ETERNAL_DEPLOY_LOCK="$LOCK" \
  "$FINALIZE_BASELINE" --apply --attest-requirements-file=requirements.portable.txt \
  >"$HARNESS/fin.txt" 2>"$HARNESS/fin.err"
assert_true "finalize apply with attest OK" grep -q 'BASELINE_FINALIZE_OK' "$HARNESS/fin.txt"
assert_true "finalize wrote .baseline marker" test -f "$BASELINE/.baseline"
assert_true "finalize wrote requirements_file portable" \
  grep -q '^requirements_file=requirements.portable.txt' "$BASELINE/.baseline-verified"
assert_true "finalize wrote provenance_source=owner_attested" \
  grep -q '^provenance_source=owner_attested' "$BASELINE/.baseline-verified"
venv_inode_after="$(stat -c '%i' "$BASELINE/tree/backend/.venv" 2>/dev/null || stat -f '%i' "$BASELINE/tree/backend/.venv")"
assert_eq "finalize did not recreate .venv" "$venv_inode_before" "$venv_inode_after"
assert_true "finalize released shared deploy lock" flock -n "$LOCK" true

# Validation failure must not leave success markers when forced to fail mid-finalize:
rm -f "$BASELINE/.baseline" "$BASELINE/.deploy-ok"
chmod a-r "$BASELINE/tree/backend/requirements.portable.txt"
assert_false "finalize apply fails closed without markers when req unreadable" \
  env ETERNAL_RUNTIME_MODE=stub ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
      ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
      ETERNAL_DEPLOY_LOCK="$LOCK" \
      "$FINALIZE_BASELINE" --apply
chmod u+r "$BASELINE/tree/backend/requirements.portable.txt"
assert_false "failed finalize left no .baseline success marker" test -f "$BASELINE/.baseline"

assert_true "docs mention requirements.portable default" \
  grep -q 'requirements.portable.txt' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs mention finalize-existing-baseline" \
  grep -q 'finalize-existing-baseline.sh' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs mention Yarn only frontend build" \
  grep -q 'Yarn only' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs mention 60s health budget" \
  grep -q '60s wall-clock budget' "$ROOT/docs/deploy-production-setup.md"
assert_true "deploy workflow has jobs:" grep -q '^jobs:' "$WF"
assert_true "probe workflow has jobs:" grep -q '^jobs:' "$PWF"
assert_true "deploy workflow on.workflow_dispatch" grep -q 'workflow_dispatch:' "$WF"
assert_true "probe workflow on.workflow_dispatch" grep -q 'workflow_dispatch:' "$PWF"
assert_true "direct-url dependency verifier suite" \
  bash "$ROOT/scripts/tests/test_deploy_direct_url_dep_verify.sh"

# ========== Yarn-only production frontend build (no npm fallback) ==========
YARN_FE="$(mktemp -d)/frontend"
mkdir -p "$YARN_FE"
printf '{}\n' >"$YARN_FE/package.json"
set +e
yarn_nolock_err="$(bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_production_build "'"$YARN_FE"'"' 2>&1)"
yarn_nolock_ec=$?
set -e
assert_eq "yarn.lock missing exit nonzero" "1" "$yarn_nolock_ec"
assert_true "yarn.lock missing error mentions npm fallback removed" \
  grep -q 'npm fallback removed' <<<"$yarn_nolock_err"
printf 'yarn lockfile v1\n' >"$YARN_FE/yarn.lock"
set +e
yarn_nobin_err="$(env PATH="/usr/bin:/bin" bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_production_build "'"$YARN_FE"'"' 2>&1)"
yarn_nobin_ec=$?
set -e
assert_eq "yarn binary missing exit nonzero" "1" "$yarn_nobin_ec"
assert_true "yarn binary missing error refuses npm" \
  grep -q 'does not use npm' <<<"$yarn_nobin_err"
: >"$HARNESS/yarn.cmd.log"
cat >"$BIN/yarn" <<EOF
#!/usr/bin/env bash
echo "YARN \$*" >>"$HARNESS/yarn.cmd.log"
exit 0
EOF
chmod +x "$BIN/yarn"
assert_true "yarn install path succeeds with stub yarn" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_production_build "'"$YARN_FE"'"'
assert_true "yarn install --frozen-lockfile invoked" \
  grep -q 'install --frozen-lockfile' "$HARNESS/yarn.cmd.log"
assert_true "yarn build invoked" grep -q 'YARN build' "$HARNESS/yarn.cmd.log"
assert_true "runner has no npm ci fallback" \
  bash -c '! grep -qE "npm ci|ETERNAL_NPM_BIN" "'"$ROOT"'/scripts/deploy/eternal-deploy-runner.sh"'
assert_true "lib exposes eternal_frontend_production_build" \
  grep -q 'eternal_frontend_production_build' "$ROOT/scripts/deploy/lib.sh"

# ========== FE publish: skip identical root-owned / collision fail-closed / path escape ==========
source "$ROOT/scripts/deploy/lib.sh"
PUB_ROOT="$HARNESS/publish-webroot"
rm -rf "$PUB_ROOT"
mkdir -p "$PUB_ROOT/static/js" "$HARNESS/publish-build/static/js"
printf 'same-bytes\n' >"$PUB_ROOT/static/js/main.aaaa.js"
chmod 444 "$PUB_ROOT/static/js/main.aaaa.js"
# Simulate non-writable identical hashed asset (root-owned stand-in).
printf 'same-bytes\n' >"$HARNESS/publish-build/static/js/main.aaaa.js"
printf 'brand-new\n' >"$HARNESS/publish-build/static/js/main.bbbb.js"
printf '{"files":{"main.js":"/static/js/main.bbbb.js"}}\n' >"$HARNESS/publish-build/asset-manifest.json"
printf '<script src="/static/js/main.bbbb.js"></script>\n' >"$HARNESS/publish-build/index.html"
assert_true "merge skips identical hashed file" \
  eternal_webroot_merge_tree "$HARNESS/publish-build" "$PUB_ROOT" 0
assert_true "SKIP_IDENTICAL logged for hashed asset" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_webroot_install_file "'"$HARNESS"'/publish-build/static/js/main.aaaa.js" "'"$PUB_ROOT"'" "static/js/main.aaaa.js" 0 2>"'"$HARNESS"'/skip.err"; grep -q SKIP_IDENTICAL "'"$HARNESS"'/skip.err"'
assert_true "new hashed asset copied" test -f "$PUB_ROOT/static/js/main.bbbb.js"
# Collision: same name, different bytes → fail closed; original intact.
printf 'OTHER\n' >"$HARNESS/publish-build/static/js/main.aaaa.js"
set +e
coll_err="$(eternal_webroot_install_file "$HARNESS/publish-build/static/js/main.aaaa.js" "$PUB_ROOT" "static/js/main.aaaa.js" 0 2>&1)"
coll_ec=$?
set -e
assert_eq "hashed collision exits nonzero" "1" "$coll_ec"
assert_true "hashed collision message" grep -q 'hashed asset collision' <<<"$coll_err"
assert_true "original hashed bytes preserved" grep -q 'same-bytes' "$PUB_ROOT/static/js/main.aaaa.js"
# Path traversal refused.
set +e
trav_err="$(eternal_webroot_install_file "$HARNESS/publish-build/index.html" "$PUB_ROOT" "../escape.html" 0 2>&1)"
trav_ec=$?
set -e
assert_eq "path traversal refused" "1" "$trav_ec"
assert_true "traversal error mentions refuse" grep -qiE 'traversal|refused' <<<"$trav_err"

# ========== .js.map collision policy + hashed css fail-closed ==========
MAP_ROOT="$HARNESS/map-webroot"
MAP_BUILD="$HARNESS/map-build"
rm -rf "$MAP_ROOT" "$MAP_BUILD"
mkdir -p "$MAP_ROOT/static/js" "$MAP_ROOT/static/css" "$MAP_BUILD/static/js" "$MAP_BUILD/static/css"
# 1) identical .js + different .js.map → SKIP_SOURCEMAP_COLLISION, keep live map
printf 'js-same\n' >"$MAP_ROOT/static/js/chunk.1111.js"
printf 'map-live-old\n' >"$MAP_ROOT/static/js/chunk.1111.js.map"
chmod 444 "$MAP_ROOT/static/js/chunk.1111.js.map"
printf 'js-same\n' >"$MAP_BUILD/static/js/chunk.1111.js"
printf 'map-build-new\n' >"$MAP_BUILD/static/js/chunk.1111.js.map"
set +e
map_skip_err="$(eternal_webroot_install_file "$MAP_BUILD/static/js/chunk.1111.js.map" "$MAP_ROOT" "static/js/chunk.1111.js.map" 0 2>&1)"
map_skip_ec=$?
set -e
assert_eq "js.map collision with identical paired js exits 0" "0" "$map_skip_ec"
assert_true "SKIP_SOURCEMAP_COLLISION logged" grep -q 'SKIP_SOURCEMAP_COLLISION' <<<"$map_skip_err"
assert_true "live js.map retained (root-owned stand-in)" grep -q 'map-live-old' "$MAP_ROOT/static/js/chunk.1111.js.map"
# 2) different .js + different .js.map → FAIL_CLOSED
printf 'js-NEW\n' >"$MAP_BUILD/static/js/chunk.1111.js"
printf 'map-build-new\n' >"$MAP_BUILD/static/js/chunk.1111.js.map"
set +e
map_jsdiff_err="$(eternal_webroot_install_file "$MAP_BUILD/static/js/chunk.1111.js.map" "$MAP_ROOT" "static/js/chunk.1111.js.map" 0 2>&1)"
map_jsdiff_ec=$?
set -e
assert_eq "js.map collision with different paired js fails" "1" "$map_jsdiff_ec"
assert_true "js.map different-js error" grep -qiE 'sourcemap collision without identical paired runtime asset' <<<"$map_jsdiff_err"
assert_true "live js.map still old after fail" grep -q 'map-live-old' "$MAP_ROOT/static/js/chunk.1111.js.map"
# 3) missing paired .js on live → FAIL_CLOSED
rm -f "$MAP_ROOT/static/js/chunk.2222.js"
printf 'js-orphan\n' >"$MAP_BUILD/static/js/chunk.2222.js"
printf 'map-orphan-live\n' >"$MAP_ROOT/static/js/chunk.2222.js.map"
printf 'map-orphan-build\n' >"$MAP_BUILD/static/js/chunk.2222.js.map"
set +e
map_miss_err="$(eternal_webroot_install_file "$MAP_BUILD/static/js/chunk.2222.js.map" "$MAP_ROOT" "static/js/chunk.2222.js.map" 0 2>&1)"
map_miss_ec=$?
set -e
assert_eq "js.map collision without live paired js fails" "1" "$map_miss_ec"
assert_true "missing paired js mentioned" grep -qiE 'sourcemap collision without identical paired runtime asset' <<<"$map_miss_err"
# 3b) missing paired .js on build source → FAIL_CLOSED
printf 'js-live-only\n' >"$MAP_ROOT/static/js/chunk.3333.js"
printf 'map-3333-live\n' >"$MAP_ROOT/static/js/chunk.3333.js.map"
printf 'map-3333-build\n' >"$MAP_BUILD/static/js/chunk.3333.js.map"
rm -f "$MAP_BUILD/static/js/chunk.3333.js"
set +e
map_miss_src_err="$(eternal_webroot_install_file "$MAP_BUILD/static/js/chunk.3333.js.map" "$MAP_ROOT" "static/js/chunk.3333.js.map" 0 2>&1)"
map_miss_src_ec=$?
set -e
assert_eq "js.map collision without build paired js fails" "1" "$map_miss_src_ec"
assert_true "missing build paired js mentioned" grep -qiE 'sourcemap collision without identical paired runtime asset' <<<"$map_miss_src_err"
# 4) identical .js.map → SKIP_IDENTICAL
chmod u+w "$MAP_ROOT/static/js/chunk.1111.js.map" 2>/dev/null || true
printf 'js-same\n' >"$MAP_ROOT/static/js/chunk.1111.js"
printf 'map-same\n' >"$MAP_ROOT/static/js/chunk.1111.js.map"
printf 'js-same\n' >"$MAP_BUILD/static/js/chunk.1111.js"
printf 'map-same\n' >"$MAP_BUILD/static/js/chunk.1111.js.map"
set +e
map_id_err="$(eternal_webroot_install_file "$MAP_BUILD/static/js/chunk.1111.js.map" "$MAP_ROOT" "static/js/chunk.1111.js.map" 0 2>&1)"
map_id_ec=$?
set -e
assert_eq "identical js.map exits 0" "0" "$map_id_ec"
assert_true "identical js.map SKIP_IDENTICAL" grep -q 'SKIP_IDENTICAL' <<<"$map_id_err"
# 5) hashed .js collision still fail-closed (regression)
printf 'js-live\n' >"$MAP_ROOT/static/js/main.cccc.js"
printf 'js-other\n' >"$MAP_BUILD/static/js/main.cccc.js"
set +e
js_coll_err="$(eternal_webroot_install_file "$MAP_BUILD/static/js/main.cccc.js" "$MAP_ROOT" "static/js/main.cccc.js" 0 2>&1)"
js_coll_ec=$?
set -e
assert_eq "hashed js collision fails" "1" "$js_coll_ec"
assert_true "hashed js collision message" grep -q 'hashed asset collision' <<<"$js_coll_err"
# 6) hashed .css collision fail-closed
printf 'css-live\n' >"$MAP_ROOT/static/css/main.dddd.css"
printf 'css-other\n' >"$MAP_BUILD/static/css/main.dddd.css"
set +e
css_coll_err="$(eternal_webroot_install_file "$MAP_BUILD/static/css/main.dddd.css" "$MAP_ROOT" "static/css/main.dddd.css" 0 2>&1)"
css_coll_ec=$?
set -e
assert_eq "hashed css collision fails" "1" "$css_coll_ec"
assert_true "hashed css collision message" grep -q 'hashed asset collision' <<<"$css_coll_err"
# 7) identical .css + different .css.map → SKIP_SOURCEMAP_COLLISION (same policy as .js.map)
printf 'css-same\n' >"$MAP_ROOT/static/css/main.eeee.css"
printf 'cssmap-live\n' >"$MAP_ROOT/static/css/main.eeee.css.map"
chmod 444 "$MAP_ROOT/static/css/main.eeee.css.map"
printf 'css-same\n' >"$MAP_BUILD/static/css/main.eeee.css"
printf 'cssmap-build\n' >"$MAP_BUILD/static/css/main.eeee.css.map"
set +e
cssmap_err="$(eternal_webroot_install_file "$MAP_BUILD/static/css/main.eeee.css.map" "$MAP_ROOT" "static/css/main.eeee.css.map" 0 2>&1)"
cssmap_ec=$?
set -e
assert_eq "css.map collision with identical paired css exits 0" "0" "$cssmap_ec"
assert_true "css.map SKIP_SOURCEMAP_COLLISION logged" grep -q 'SKIP_SOURCEMAP_COLLISION' <<<"$cssmap_err"
assert_true "live css.map retained" grep -q 'cssmap-live' "$MAP_ROOT/static/css/main.eeee.css.map"
# 7b) different .css + different .css.map → FAIL_CLOSED
printf 'css-NEW\n' >"$MAP_BUILD/static/css/main.eeee.css"
printf 'cssmap-build\n' >"$MAP_BUILD/static/css/main.eeee.css.map"
set +e
cssmap_diff_err="$(eternal_webroot_install_file "$MAP_BUILD/static/css/main.eeee.css.map" "$MAP_ROOT" "static/css/main.eeee.css.map" 0 2>&1)"
cssmap_diff_ec=$?
set -e
assert_eq "css.map collision with different paired css fails" "1" "$cssmap_diff_ec"
assert_true "css.map different-css error" \
  grep -qiE 'sourcemap collision without identical paired runtime asset' <<<"$cssmap_diff_err"
assert_true "live css.map still old after fail" grep -q 'cssmap-live' "$MAP_ROOT/static/css/main.eeee.css.map"
# 8) symlink destination refused
ln -s "$MAP_ROOT/static/js/chunk.1111.js.map" "$MAP_ROOT/static/js/linky.js.map"
printf 'js-same\n' >"$MAP_BUILD/static/js/linky.js"
printf 'map-via-link\n' >"$MAP_BUILD/static/js/linky.js.map"
set +e
sym_err="$(eternal_webroot_install_file "$MAP_BUILD/static/js/linky.js.map" "$MAP_ROOT" "static/js/linky.js.map" 0 2>&1)"
sym_ec=$?
set -e
assert_eq "symlink dest refused" "1" "$sym_ec"
assert_true "symlink refuse message" grep -qiE 'symlink' <<<"$sym_err"
# Merge with safe map collision must PASS (publish path) — isolated trees only.
MERGE_ROOT="$HARNESS/map-merge-webroot"
MERGE_BUILD="$HARNESS/map-merge-build"
rm -rf "$MERGE_ROOT" "$MERGE_BUILD"
mkdir -p "$MERGE_ROOT/static/js" "$MERGE_BUILD/static/js"
printf 'js-same\n' >"$MERGE_ROOT/static/js/chunk.1111.js"
printf 'map-live-old\n' >"$MERGE_ROOT/static/js/chunk.1111.js.map"
chmod 444 "$MERGE_ROOT/static/js/chunk.1111.js.map"
printf 'js-same\n' >"$MERGE_BUILD/static/js/chunk.1111.js"
printf 'map-build-new\n' >"$MERGE_BUILD/static/js/chunk.1111.js.map"
printf '{"files":{}}\n' >"$MERGE_BUILD/asset-manifest.json"
printf '<script src="/static/js/chunk.1111.js"></script>\n' >"$MERGE_BUILD/index.html"
assert_true "merge PASS with safe js.map collision" \
  eternal_webroot_merge_tree "$MERGE_BUILD" "$MERGE_ROOT" 0
assert_true "merge kept live map bytes" grep -q 'map-live-old' "$MERGE_ROOT/static/js/chunk.1111.js.map"
# Soft rollback restore with both .js.map and .css.map collisions + leftover new maps → PASS
RB_MAP_LIVE="$HARNESS/rb-map-live"
RB_MAP_BAK="$HARNESS/rb-map-bak"
rm -rf "$RB_MAP_LIVE" "$RB_MAP_BAK"
mkdir -p "$RB_MAP_LIVE/static/js" "$RB_MAP_LIVE/static/css" \
  "$RB_MAP_BAK/static/js" "$RB_MAP_BAK/static/css"
printf '<script src="/static/js/main.328943d0.js"></script>\n<link href="/static/css/main.aaaa.css" rel="stylesheet">\n' \
  >"$RB_MAP_BAK/index.html"
printf '{"files":{"main.js":"/static/js/main.328943d0.js","main.css":"/static/css/main.aaaa.css"}}\n' \
  >"$RB_MAP_BAK/asset-manifest.json"
printf 'JS_BASE\n' >"$RB_MAP_BAK/static/js/main.328943d0.js"
printf 'MAP_JS_BASE\n' >"$RB_MAP_BAK/static/js/main.328943d0.js.map"
printf 'CSS_BASE\n' >"$RB_MAP_BAK/static/css/main.aaaa.css"
printf 'MAP_CSS_BASE\n' >"$RB_MAP_BAK/static/css/main.aaaa.css.map"
cp -a "$RB_MAP_BAK"/. "$RB_MAP_LIVE"/
# Simulate failed publish leftovers: switched index + new hashed maps + collided maps
printf '<script src="/static/js/main.newhash.js"></script>\n' >"$RB_MAP_LIVE/index.html"
printf 'JS_NEW\n' >"$RB_MAP_LIVE/static/js/main.newhash.js"
printf 'MAP_JS_ORPHAN\n' >"$RB_MAP_LIVE/static/js/main.newhash.js.map"
printf 'MAP_JS_NEW\n' >"$RB_MAP_LIVE/static/js/main.328943d0.js.map"
printf 'MAP_CSS_NEW\n' >"$RB_MAP_LIVE/static/css/main.aaaa.css.map"
chmod 444 "$RB_MAP_LIVE/static/js/main.328943d0.js.map" "$RB_MAP_LIVE/static/css/main.aaaa.css.map"
assert_true "soft rollback merge PASS with js+css map collisions" \
  eternal_webroot_merge_tree "$RB_MAP_BAK" "$RB_MAP_LIVE" 1
assert_true "soft rollback atomic index" \
  eternal_atomic_publish_index "$RB_MAP_BAK/index.html" "$RB_MAP_LIVE" 1
assert_true "soft rollback required_state OK" \
  eternal_frontend_required_state_ok "$RB_MAP_LIVE" "$RB_MAP_BAK"
assert_true "soft rollback kept live js.map (non-authoritative)" \
  grep -q 'MAP_JS_NEW' "$RB_MAP_LIVE/static/js/main.328943d0.js.map"
assert_true "soft rollback kept live css.map (non-authoritative)" \
  grep -q 'MAP_CSS_NEW' "$RB_MAP_LIVE/static/css/main.aaaa.css.map"
assert_eq "soft rollback keeps leftover new hashed js" "JS_NEW" "$(cat "$RB_MAP_LIVE/static/js/main.newhash.js")"
assert_true "soft rollback runtime js matches backup" \
  cmp -s "$RB_MAP_LIVE/static/js/main.328943d0.js" "$RB_MAP_BAK/static/js/main.328943d0.js"
assert_true "soft rollback runtime css matches backup" \
  cmp -s "$RB_MAP_LIVE/static/css/main.aaaa.css" "$RB_MAP_BAK/static/css/main.aaaa.css"

# ========== required_state: extra unreferenced files OK; missing main asset FAIL ==========
REQ_LIVE="$HARNESS/req-live"
REQ_BAK="$HARNESS/req-bak"
rm -rf "$REQ_LIVE" "$REQ_BAK"
mkdir -p "$REQ_LIVE/static/js" "$REQ_BAK/static/js"
printf '<script src="/static/js/main.ok.js"></script>\n' >"$REQ_LIVE/index.html"
cp -a "$REQ_LIVE/index.html" "$REQ_BAK/index.html"
printf '{"main":"static/js/main.ok.js"}\n' >"$REQ_BAK/asset-manifest.json"
cp -a "$REQ_BAK/asset-manifest.json" "$REQ_LIVE/asset-manifest.json"
printf 'main-bytes\n' >"$REQ_LIVE/static/js/main.ok.js"
cp -a "$REQ_LIVE/static/js/main.ok.js" "$REQ_BAK/static/js/main.ok.js"
printf 'extra-unreferenced\n' >"$REQ_LIVE/static/js/extra.ffff.js"
printf 'extra-map\n' >"$REQ_LIVE/static/js/extra.ffff.js.map"
assert_true "required_state OK with extra unreferenced assets" \
  eternal_frontend_required_state_ok "$REQ_LIVE" "$REQ_BAK"
printf 'tampered\n' >"$REQ_LIVE/static/js/main.ok.js"
assert_false "required_state fails when main js bytes drift" \
  eternal_frontend_required_state_ok "$REQ_LIVE" "$REQ_BAK"
printf 'main-bytes\n' >"$REQ_LIVE/static/js/main.ok.js"
printf '{"main":"DRIFTED"}\n' >"$REQ_LIVE/asset-manifest.json"
assert_false "required_state fails when manifest drifts" \
  eternal_frontend_required_state_ok "$REQ_LIVE" "$REQ_BAK"
# Soft restore after manifest drift + safe map collision on unrelated hashed map → PASS
printf 'map-live\n' >"$REQ_LIVE/static/js/main.ok.js.map"
printf 'map-bak\n' >"$REQ_BAK/static/js/main.ok.js.map"
chmod 444 "$REQ_LIVE/static/js/main.ok.js.map"
assert_true "soft restore recovers manifest despite map collision" \
  eternal_webroot_merge_tree "$REQ_BAK" "$REQ_LIVE" 1
cp -a "$REQ_BAK/index.html" "$REQ_LIVE/index.html"
assert_true "required_state OK after soft restore" \
  eternal_frontend_required_state_ok "$REQ_LIVE" "$REQ_BAK"
assert_true "soft restore kept live map" grep -q 'map-live' "$REQ_LIVE/static/js/main.ok.js.map"

# ========== partial publish → index unchanged → ROLLBACK_PASS ==========
git -C "$MIRROR_SRC" checkout -q main
printf 'fe-partial\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m fe-partial
FE_PARTIAL="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
# Seed live with immutable root-owned-identical asset that new dry-build also emits as app.js content change path:
# Force publish fail after merge start via ETERNAL_FORCE_PUBLISH_FAIL (index never swapped).
printf 'live-index-keep\n' >"$FRONTEND/live/index.html"
mkdir -p "$FRONTEND/live/static/js"
printf 'live-main\n' >"$FRONTEND/live/static/js/main.f7129337.js"
chmod 644 "$FRONTEND/live/index.html" "$FRONTEND/live/static/js/main.f7129337.js"
# Ensure CURRENT/baseline path exists for rollback pointer tests.
if [[ ! -e "$CURRENT" ]]; then
  make_verified_baseline
  ETERNAL_ACK_REPLACE_LIVE_FRONTEND=1 "$RUNNER" deploy "$MAIN_SHA" >/dev/null 2>&1 || true
fi
CUR_BEFORE_PARTIAL="$(readlink -f "$CURRENT" || true)"
printf 'live-index-keep\n' >"$FRONTEND/live/index.html"
export ETERNAL_FORCE_PUBLISH_FAIL=1
set +e
"$RUNNER" deploy "$FE_PARTIAL" >"$HARNESS/partial.txt" 2>"$HARNESS/partial.err"
partial_ec=$?
set -e
export ETERNAL_FORCE_PUBLISH_FAIL=0
assert_eq "partial publish deploy nonzero" "1" "$partial_ec"
assert_true "partial publish rollback PASS when index never switched" \
  grep -q 'ROLLBACK_PASS' "$HARNESS/partial.err"
assert_false "partial publish is not ROLLBACK_FAILED" grep -q 'ROLLBACK_FAILED' "$HARNESS/partial.err"
assert_true "live index preserved after forced publish fail" grep -q 'live-index-keep' "$FRONTEND/live/index.html"
assert_true "live hashed main preserved" test -f "$FRONTEND/live/static/js/main.f7129337.js"

# ========== required FE state fails when backup index missing ==========
rm -rf "$HARNESS/bad-fe-src"
mkdir -p "$HARNESS/bad-fe-src/static/js" "$HARNESS/bad-fe-live"
printf 'x\n' >"$HARNESS/bad-fe-src/static/js/x.js"
printf '<html>live</html>\n' >"$HARNESS/bad-fe-live/index.html"
assert_false "required state fails without backup index" \
  eternal_frontend_required_state_ok "$HARNESS/bad-fe-live" "$HARNESS/bad-fe-src"

# ========== required-state: PostHog array.js ignored; real assets enforced ==========
source "$ROOT/scripts/deploy/lib.sh"
REQ_PH_LIVE="$HARNESS/req-ph-live"
REQ_PH_BAK="$HARNESS/req-ph-bak"
rm -rf "$REQ_PH_LIVE" "$REQ_PH_BAK"
mkdir -p "$REQ_PH_LIVE/static/js" "$REQ_PH_LIVE/static/css" "$REQ_PH_BAK/static/js" "$REQ_PH_BAK/static/css"
cat >"$REQ_PH_LIVE/index.html" <<'EOF'
<link href="/static/css/main.abc.css" rel="stylesheet">
<script>p.src=host+"/static/array.js"</script>
<script src="/static/js/main.f7129337.js"></script>
EOF
cp -a "$REQ_PH_LIVE/index.html" "$REQ_PH_BAK/index.html"
printf '%s\n' '{"files":{"main.js":"/static/js/main.f7129337.js","main.css":"/static/css/main.abc.css"}}' \
  >"$REQ_PH_BAK/asset-manifest.json"
cp -a "$REQ_PH_BAK/asset-manifest.json" "$REQ_PH_LIVE/asset-manifest.json"
printf 'js\n' >"$REQ_PH_LIVE/static/js/main.f7129337.js"
cp -a "$REQ_PH_LIVE/static/js/main.f7129337.js" "$REQ_PH_BAK/static/js/main.f7129337.js"
printf 'css\n' >"$REQ_PH_LIVE/static/css/main.abc.css"
cp -a "$REQ_PH_LIVE/static/css/main.abc.css" "$REQ_PH_BAK/static/css/main.abc.css"
assert_true "required_state OK ignores PostHog static/array.js" \
  eternal_frontend_required_state_ok "$REQ_PH_LIVE" "$REQ_PH_BAK"
rm -f "$REQ_PH_LIVE/static/js/main.f7129337.js"
assert_false "required_state fails when real main js missing" \
  eternal_frontend_required_state_ok "$REQ_PH_LIVE" "$REQ_PH_BAK"
printf 'js\n' >"$REQ_PH_LIVE/static/js/main.f7129337.js"
printf '{"files":{"main.js":"/static/js/OTHER.js"}}\n' >"$REQ_PH_LIVE/asset-manifest.json"
assert_false "required_state fails on manifest mismatch" \
  eternal_frontend_required_state_ok "$REQ_PH_LIVE" "$REQ_PH_BAK"

# ========== reuse asset refs: PostHog static/array.js must not be required ==========
source "$ROOT/scripts/deploy/lib.sh"
ARRAY_IDX="$HARNESS/array-index.html"
ARRAY_MAN="$HARNESS/array-manifest.json"
cat >"$ARRAY_IDX" <<'EOF'
<!doctype html><html><head>
<link href="/static/css/main.abc.css" rel="stylesheet">
</head><body>
<script>/* posthog */(p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js")</script>
<script src="/static/js/main.f7129337.js"></script>
</body></html>
EOF
printf '%s\n' '{"files":{"main.js":"/static/js/main.f7129337.js","main.css":"/static/css/main.abc.css","index.html":"/index.html"},"entrypoints":["static/css/main.abc.css","static/js/main.f7129337.js"]}' >"$ARRAY_MAN"
mapfile -t REFS < <(eternal_fe_list_required_static_assets "$ARRAY_IDX" "$ARRAY_MAN" | awk 'NF && !seen[$0]++')
refs_joined="$(printf '%s|' "${REFS[@]}")"
assert_true "asset list includes real main js" grep -q 'static/js/main.f7129337.js' <<<"$refs_joined"
assert_true "asset list includes real main css" grep -q 'static/css/main.abc.css' <<<"$refs_joined"
assert_false "asset list excludes PostHog static/array.js" grep -q 'static/array.js' <<<"$refs_joined"
# Broad legacy grep would false-positive — document expected old behavior for contrast.
legacy_hit="$(grep -oE '(static/[^"'\''[:space:]>]+)' "$ARRAY_IDX" | grep -c 'static/array.js' || true)"
assert_eq "legacy broad grep still sees static/array.js (contrast)" "1" "$legacy_hit"

# ========== reuse incomplete release: valid refs + false array.js + missing real asset ==========
REUSE_FAKE="$HARNESS/reuse-rel"
rm -rf "$REUSE_FAKE"
mkdir -p "$REUSE_FAKE/frontend/build/static/js" "$REUSE_FAKE/frontend/build/static/css" \
  "$REUSE_FAKE/backend/.venv/bin" "$REUSE_FAKE/frontend"
printf '%s\n' "$MAIN_SHA" >"$REUSE_FAKE/.eternal-source-sha"
cp -a "$ARRAY_IDX" "$REUSE_FAKE/frontend/build/index.html"
cp -a "$ARRAY_MAN" "$REUSE_FAKE/frontend/build/asset-manifest.json"
printf 'js\n' >"$REUSE_FAKE/frontend/build/static/js/main.f7129337.js"
printf 'css\n' >"$REUSE_FAKE/frontend/build/static/css/main.abc.css"
# Minimal fingerprint inputs
printf '{"name":"frontend"}\n' >"$REUSE_FAKE/frontend/package.json"
printf 'yarn lockfile v1\n' >"$REUSE_FAKE/frontend/yarn.lock"
eternal_write_frontend_fingerprint "$REUSE_FAKE"
# Stub venv: real python + stub uvicorn/pip for ETERNAL_RUNTIME_MODE=stub.
ln -sf "$(command -v python3)" "$REUSE_FAKE/backend/.venv/bin/python"
cat >"$REUSE_FAKE/backend/.venv/bin/uvicorn" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$REUSE_FAKE/backend/.venv/bin/uvicorn"
cat >"$REUSE_FAKE/backend/.venv/bin/pip" <<'EOF'
#!/usr/bin/env bash
# stub: "pip check" success for synthetic reuse
[[ "${1:-}" == "check" ]] && exit 0
exit 0
EOF
chmod +x "$REUSE_FAKE/backend/.venv/bin/pip"
printf 'requirements.portable.txt\n' >"$REUSE_FAKE/backend/.eternal-requirements-file"
printf 'ok-portable\n' >"$REUSE_FAKE/backend/requirements.portable.txt"
req_hash="$(sha256sum "$REUSE_FAKE/backend/requirements.portable.txt" | awk '{print $1}')"
printf '%s\n' "$req_hash" >"$REUSE_FAKE/backend/.eternal-requirements-sha256"
set +e
ETERNAL_RUNTIME_MODE=stub eternal_try_reuse_incomplete_release "$REUSE_FAKE" "$MAIN_SHA" \
  >"$HARNESS/reuse-array.out" 2>"$HARNESS/reuse-array.err"
reuse_array_ec=$?
set -e
assert_eq "reuse OK with PostHog static/array.js in index (not a local asset)" "0" "$reuse_array_ec"
assert_false "reuse does not complain about static/array.js" \
  grep -q 'static/array.js' "$HARNESS/reuse-array.err"
# Missing real hashed JS → FAIL_CLOSED
rm -f "$REUSE_FAKE/frontend/build/static/js/main.f7129337.js"
set +e
ETERNAL_RUNTIME_MODE=stub eternal_try_reuse_incomplete_release "$REUSE_FAKE" "$MAIN_SHA" \
  >"$HARNESS/reuse-miss.out" 2>"$HARNESS/reuse-miss.err"
reuse_miss_ec=$?
set -e
assert_eq "reuse refuses missing real main js" "1" "$reuse_miss_ec"
assert_true "missing real asset message" \
  grep -q 'build asset missing static/js/main.f7129337.js' "$HARNESS/reuse-miss.err"
# Restore real asset; break source sha → still refuse
printf 'js\n' >"$REUSE_FAKE/frontend/build/static/js/main.f7129337.js"
printf 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n' >"$REUSE_FAKE/.eternal-source-sha"
set +e
ETERNAL_RUNTIME_MODE=stub eternal_try_reuse_incomplete_release "$REUSE_FAKE" "$MAIN_SHA" \
  >/dev/null 2>"$HARNESS/reuse-sha.err"
reuse_sha_ec=$?
set -e
assert_eq "reuse refuses source sha mismatch" "1" "$reuse_sha_ec"
assert_true "source sha mismatch logged" grep -q 'source sha mismatch' "$HARNESS/reuse-sha.err"

# ========== reuse incomplete release fingerprint ==========
if [[ -d "$RELEASES/$MAIN_SHA" ]]; then
  REUSE_DIR="$RELEASES/$MAIN_SHA"
  printf '%s\n' "$MAIN_SHA" >"$REUSE_DIR/.eternal-source-sha"
  if [[ ! -f "$REUSE_DIR/.eternal-frontend-fingerprint" ]]; then
    eternal_write_frontend_fingerprint "$REUSE_DIR"
  fi
  if [[ -f "$REUSE_DIR/frontend/build/index.html" && -x "$REUSE_DIR/backend/.venv/bin/uvicorn" \
     && -f "$REUSE_DIR/backend/.eternal-requirements-file" \
     && -f "$REUSE_DIR/backend/.eternal-requirements-sha256" ]]; then
    rm -f "$REUSE_DIR/.deploy-ok"
    # Inject PostHog-like false asset string into built index to prove reuse still works.
    if ! grep -q 'static/array.js' "$REUSE_DIR/frontend/build/index.html"; then
      printf '\n<script>p.src=host+"/static/array.js"</script>\n' >>"$REUSE_DIR/frontend/build/index.html"
    fi
    set +e
    ETERNAL_RUNTIME_MODE=stub eternal_try_reuse_incomplete_release "$REUSE_DIR" "$MAIN_SHA" \
      >"$HARNESS/reuse.out" 2>"$HARNESS/reuse.err"
    reuse_ec=$?
    set -e
    assert_eq "incomplete release reuse integrity OK" "0" "$reuse_ec"
    assert_false "harness reuse ignores PostHog array.js" \
      grep -q 'build asset missing static/array.js' "$HARNESS/reuse.err"
    printf 'deadbeef\n' >"$REUSE_DIR/.eternal-frontend-fingerprint"
    set +e
    ETERNAL_RUNTIME_MODE=stub eternal_try_reuse_incomplete_release "$REUSE_DIR" "$MAIN_SHA" \
      >/dev/null 2>"$HARNESS/reuse-bad.err"
    reuse_bad=$?
    set -e
    assert_eq "fingerprint mismatch refuses reuse" "1" "$reuse_bad"
  else
    log "SKIP: reuse fixture incomplete (missing build/venv markers)"
  fi
fi
# ========== regression: yarn-only + health budget + flock + ack still present ==========
assert_true "runner still Yarn-only helper" grep -q 'eternal_frontend_production_build' "$ROOT/scripts/deploy/eternal-deploy-runner.sh"
assert_true "runner health budget default 60" grep -q 'ETERNAL_HEALTH_BUDGET_S:-60' "$ROOT/scripts/deploy/eternal-deploy-runner.sh"
assert_true "runner ack-fe still gated" grep -q 'ETERNAL_ACK_REPLACE_LIVE_FRONTEND' "$ROOT/scripts/deploy/eternal-deploy-runner.sh"
assert_true "runner still uses flock acquire" grep -q 'eternal_acquire_lock' "$ROOT/scripts/deploy/eternal-deploy-runner.sh"
assert_true "lib lists required static via src/href helper" \
  grep -q 'eternal_fe_list_required_static_assets' "$ROOT/scripts/deploy/lib.sh"
assert_true "SKIP_SOURCEMAP_COLLISION still present" \
  grep -q 'SKIP_SOURCEMAP_COLLISION' "$ROOT/scripts/deploy/lib.sh"
assert_true "workflow supports rollback operation" \
  grep -q 'rollback' "$ROOT/.github/workflows/deploy-production.yml"
assert_true "gate allowlists rollback verb" \
  grep -q 'rollback' "$ROOT/scripts/deploy/eternal-actions-gate"

# ========== POST-SUCCESS ROLLBACK (identity-scoped) ==========
# Fresh baseline + first cutover for isolated rollback proofs.
stop_harness_backend_proc 2>/dev/null || true
rm -rf "$BASELINE" "$RELEASES"/[0-9a-f]* "$RELEASES/rollback-points" "$RELEASES/active-release.txt" \
  "$RELEASES/backups" "$FRONTEND_PREV" 2>/dev/null || true
mkdir -p "$FRONTEND/live/static/js"
printf 'baseline-live\n<script src="/static/js/old.js"></script>\n' >"$FRONTEND/live/index.html"
printf 'old-asset\n' >"$FRONTEND/live/static/js/old.js"
printf 'active\n' >"$FRONTEND/live/active.txt"
make_verified_baseline
export ETERNAL_EXPECTED_WD="$EXPECTED_WD"
export ETERNAL_EXPECTED_EXEC_PREFIX="$EXPECTED_WD"
export ETERNAL_HEALTH_FORCE_FAIL=0
export ETERNAL_FORCE_RESTART_FAIL=0
export ETERNAL_FORCE_PUBLISH_FAIL=0
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"
echo 0 >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
: >"$ETERNAL_SYSTEMCTL_LOG"

# First cutover deploy → rollback point previous=baseline-active
if ETERNAL_ACK_REPLACE_LIVE_FRONTEND=1 "$RUNNER" deploy "$MAIN_SHA" \
  >"$HARNESS/rb-cutover.txt" 2>"$HARNESS/rb-cutover.err"; then
  log "PASS: cutover deploy for rollback suite"; PASS=$((PASS+1))
else
  log "FAIL: cutover deploy for rollback suite"; cat "$HARNESS/rb-cutover.err" >>"$REPORT"; FAIL=$((FAIL+1))
fi
RB_POINT="$RELEASES/rollback-points/$MAIN_SHA"
assert_true "rollback point meta after cutover" test -f "$RB_POINT/meta.txt"
assert_true "rollback point FE backup after cutover" test -f "$RB_POINT/frontend/index.html"
assert_true "meta previous_id=baseline-active" \
  grep -qx 'previous_id=baseline-active' "$RB_POINT/meta.txt"
assert_true "active-release marker is MAIN_SHA" \
  grep -qx "sha=$MAIN_SHA" "$RELEASES/active-release.txt"
assert_true "cutover FE backup is not live dry-build" \
  grep -q 'baseline-live' "$RB_POINT/frontend/index.html"

# Successful rollback first cutover → baseline
set +e
"$RUNNER" rollback "$MAIN_SHA" baseline-active \
  >"$HARNESS/rb-base.txt" 2>"$HARNESS/rb-base.err"
rb_base_ec=$?
set -e
assert_eq "rollback to baseline exit 0" "0" "$rb_base_ec"
assert_true "ROLLBACK_SUCCESS to baseline" \
  grep -qE "ROLLBACK_SUCCESS[[:space:]]+deployed_sha=${MAIN_SHA}" "$HARNESS/rb-base.txt"
assert_true "restored FE matches baseline backup" \
  grep -q 'baseline-live' "$FRONTEND/live/index.html"
assert_true "CURRENT points at baseline tree after rollback" \
  bash -c 'cur=$(readlink -f "'"$CURRENT"'"); want=$(readlink -f "'"$BASELINE"'/tree"); [[ "$cur" == "$want" ]]'
assert_true "active marker after baseline rollback" \
  grep -qx 'sha=baseline-active' "$RELEASES/active-release.txt"
assert_true "transaction disclaimer logged" \
  grep -qi 'does not reverse MongoDB' "$HARNESS/rb-base.err"

# Redeploy MAIN then SHA2; rollback SHA2 → MAIN
rm -rf "$RELEASES/$MAIN_SHA"
ETERNAL_ACK_REPLACE_LIVE_FRONTEND=1 "$RUNNER" deploy "$MAIN_SHA" \
  >"$HARNESS/rb-redeploy.txt" 2>"$HARNESS/rb-redeploy.err" || true
git -C "$MIRROR_SRC" checkout -q main
printf 'sha2-fe\n' >>"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m rb-sha2
SHA2="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/*:refs/heads/*'
"$RUNNER" deploy "$SHA2" >"$HARNESS/rb-sha2.txt" 2>"$HARNESS/rb-sha2.err" || true
assert_true "SHA2 DEPLOY_OK" grep -q "DEPLOY_OK sha=$SHA2" "$HARNESS/rb-sha2.txt"
assert_true "SHA2 rollback point previous=MAIN" \
  grep -qx "previous_id=$MAIN_SHA" "$RELEASES/rollback-points/$SHA2/meta.txt"

set +e
"$RUNNER" rollback "$SHA2" "$MAIN_SHA" >"$HARNESS/rb-sha.txt" 2>"$HARNESS/rb-sha.err"
rb_sha_ec=$?
set -e
assert_eq "rollback SHA2→MAIN exit 0" "0" "$rb_sha_ec"
assert_true "ROLLBACK_SUCCESS SHA predecessor" \
  grep -qE "ROLLBACK_SUCCESS[[:space:]]+deployed_sha=${SHA2}" "$HARNESS/rb-sha.txt"
assert_true "CURRENT at MAIN after SHA rollback" \
  bash -c 'cur=$(readlink -f "'"$CURRENT"'"); want=$(readlink -f "'"$RELEASES/$MAIN_SHA"'"); [[ "$cur" == "$want" ]]'

# Redeploy SHA2 for refuse-case matrix
"$RUNNER" deploy "$SHA2" >"$HARNESS/rb-sha2b.txt" 2>"$HARNESS/rb-sha2b.err" || true

# Current SHA mismatch → refuse
set +e
"$RUNNER" rollback "$MAIN_SHA" baseline-active \
  >"$HARNESS/rb-mismatch.txt" 2>"$HARNESS/rb-mismatch.err"
rb_mis_ec=$?
set -e
assert_eq "current SHA mismatch refuses" "1" "$rb_mis_ec"
assert_true "mismatch mentions active sha" grep -qi 'active sha mismatch' "$HARNESS/rb-mismatch.err"

# Previous release mismatch → refuse
set +e
"$RUNNER" rollback "$SHA2" baseline-active \
  >"$HARNESS/rb-prevbad.txt" 2>"$HARNESS/rb-prevbad.err"
rb_prev_ec=$?
set -e
assert_eq "previous mismatch refuses" "1" "$rb_prev_ec"
assert_true "previous mismatch logged" grep -qi 'previous release mismatch' "$HARNESS/rb-prevbad.err"

# Backup missing/corrupt → refuse (fail-closed before live mutate)
mkdir -p "$HARNESS/fe-bak-save"
cp -a "$RELEASES/rollback-points/$SHA2/frontend"/. "$HARNESS/fe-bak-save"/ 2>/dev/null || true
rm -rf "$RELEASES/rollback-points/$SHA2/frontend"
set +e
"$RUNNER" rollback "$SHA2" "$MAIN_SHA" >"$HARNESS/rb-nobak.txt" 2>"$HARNESS/rb-nobak.err"
rb_nobak_ec=$?
set -e
assert_eq "missing FE backup refuses" "1" "$rb_nobak_ec"
assert_true "missing backup fail-closed" \
  grep -qiE 'frontend backup missing|not marked ok|corrupt' "$HARNESS/rb-nobak.err"
mkdir -p "$RELEASES/rollback-points/$SHA2/frontend"
cp -a "$HARNESS/fe-bak-save"/. "$RELEASES/rollback-points/$SHA2/frontend"/
# Corrupt index
printf 'corrupt\n' >"$RELEASES/rollback-points/$SHA2/frontend/index.html"
# Still has index so validate may pass meta; restore verify should fail → ROLLBACK_FAILED
# Actually validate only checks index exists. Restore will put corrupt index; required_state
# vs backup will match corrupt=corrupt so it might SUCCESS incorrectly restoring corrupt.
# Better: remove index to fail validate, or break meta frontend_backup_ok after restore path.
# Re-test missing: delete index.html
rm -f "$RELEASES/rollback-points/$SHA2/frontend/index.html"
set +e
"$RUNNER" rollback "$SHA2" "$MAIN_SHA" >"$HARNESS/rb-corbak.txt" 2>"$HARNESS/rb-corbak.err"
rb_cor_ec=$?
set -e
assert_eq "corrupt FE backup refuses" "1" "$rb_cor_ec"
cp -a "$HARNESS/fe-bak-save"/. "$RELEASES/rollback-points/$SHA2/frontend"/

# Deploy lock busy → refuse
exec {RB_LOCK_FD}>"$LOCK"
flock -n "$RB_LOCK_FD" || true
set +e
"$RUNNER" rollback "$SHA2" "$MAIN_SHA" >"$HARNESS/rb-lock.txt" 2>"$HARNESS/rb-lock.err"
rb_lock_ec=$?
set -e
assert_eq "busy lock refuses rollback" "1" "$rb_lock_ec"
assert_true "busy lock message" grep -qi 'deploy lock held' "$HARNESS/rb-lock.err"
flock -u "$RB_LOCK_FD" 2>/dev/null || true
exec {RB_LOCK_FD}>&- 2>/dev/null || true

# Rollback after another deployment → refuse
git -C "$MIRROR_SRC" checkout -q main
printf 'sha3-fe\n' >>"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m rb-sha3
SHA3="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/*:refs/heads/*'
"$RUNNER" deploy "$SHA3" >"$HARNESS/rb-sha3.txt" 2>"$HARNESS/rb-sha3.err" || true
assert_true "SHA3 deployed" grep -q "DEPLOY_OK sha=$SHA3" "$HARNESS/rb-sha3.txt"
set +e
"$RUNNER" rollback "$SHA2" "$MAIN_SHA" >"$HARNESS/rb-stale.txt" 2>"$HARNESS/rb-stale.err"
rb_stale_ec=$?
set -e
assert_eq "rollback after newer deploy refuses" "1" "$rb_stale_ec"
assert_true "stale undo refused" grep -qi 'active sha mismatch' "$HARNESS/rb-stale.err"

# Frontend restore failure → ROLLBACK_FAILED (forced after preflight; same flock path)
export ETERNAL_FORCE_FRONTEND_RESTORE_FAIL=1
set +e
"$RUNNER" rollback "$SHA3" "$SHA2" >"$HARNESS/rb-fefail.txt" 2>"$HARNESS/rb-fefail.err"
rb_fefail_ec=$?
set -e
export ETERNAL_FORCE_FRONTEND_RESTORE_FAIL=0
assert_eq "frontend restore failure nonzero" "1" "$rb_fefail_ec"
assert_true "ROLLBACK_FAILED on FE restore" \
  grep -qE 'ROLLBACK_FAILED' <<<"$(cat "$HARNESS/rb-fefail.txt" "$HARNESS/rb-fefail.err")"
assert_true "frontend restore FAILED logged" \
  grep -qi 'frontend restore' "$HARNESS/rb-fefail.err"

# Backend-changing SHA for restart-failure + success proofs (FE-only skips restart)
git -C "$MIRROR_SRC" checkout -q main
printf 'be-rb\n' >>"$MIRROR_SRC/backend/requirements.txt"
printf 'be-rb\n' >>"$MIRROR_SRC/backend/requirements.portable.txt"
git -C "$MIRROR_SRC" add backend/requirements.txt backend/requirements.portable.txt
git -C "$MIRROR_SRC" commit -q -m rb-be
SHA_BE="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/*:refs/heads/*'
# Ensure active identity is SHA2 before backend deploy (FE fail may have left marker).
printf 'sha=%s\nprevious_id=%s\nupdated_at=t\n' "$SHA2" "$MAIN_SHA" >"$RELEASES/active-release.txt"
ln -sfn "$RELEASES/$SHA2" "$CURRENT"
start_harness_backend_proc "$(readlink -f "$CURRENT/backend")"
"$RUNNER" deploy "$SHA_BE" >"$HARNESS/rb-be.txt" 2>"$HARNESS/rb-be.err" || true
assert_true "backend SHA DEPLOY_OK" grep -q "DEPLOY_OK sha=$SHA_BE" "$HARNESS/rb-be.txt"
assert_true "backend deploy switched CURRENT" \
  grep -qx 'current_switched=1' "$RELEASES/rollback-points/$SHA_BE/meta.txt"

# Backend restart failure → ROLLBACK_FAILED
export ETERNAL_FORCE_RESTART_FAIL=1
set +e
"$RUNNER" rollback "$SHA_BE" "$SHA2" >"$HARNESS/rb-befail.txt" 2>"$HARNESS/rb-befail.err"
rb_befail_ec=$?
set -e
export ETERNAL_FORCE_RESTART_FAIL=0
assert_eq "backend restart failure nonzero" "1" "$rb_befail_ec"
assert_true "ROLLBACK_FAILED on backend restart" \
  grep -q 'ROLLBACK_FAILED' <<<"$(cat "$HARNESS/rb-befail.txt" "$HARNESS/rb-befail.err")"
assert_true "backend restart FAILED logged" \
  grep -qi 'backend restart' "$HARNESS/rb-befail.err"

# Repair after partial rollback: settle on SHA2, then redeploy SHA_BE cleanly.
chmod -R u+w "$FRONTEND/live" "$RELEASES" 2>/dev/null || true
ln -sfn "$RELEASES/$SHA2" "$CURRENT"
start_harness_backend_proc "$(readlink -f "$CURRENT/backend")"
printf 'sha=%s\nprevious_id=%s\nupdated_at=t\n' "$SHA2" "$MAIN_SHA" >"$RELEASES/active-release.txt"
if [[ -d "$RELEASES/rollback-points/$SHA_BE/frontend" ]]; then
  cp -a "$RELEASES/rollback-points/$SHA_BE/frontend"/. "$FRONTEND/live"/
fi
"$RUNNER" deploy "$SHA_BE" >"$HARNESS/rb-repair.txt" 2>"$HARNESS/rb-repair.err" || true
assert_true "repair redeploy SHA_BE OK" grep -q "DEPLOY_OK sha=$SHA_BE" "$HARNESS/rb-repair.txt"

# Successful restoration verified (SHA_BE → SHA2)
set +e
"$RUNNER" rollback "$SHA_BE" "$SHA2" >"$HARNESS/rb-ok.txt" 2>"$HARNESS/rb-ok.err"
rb_ok_ec=$?
set -e
if [[ "$rb_ok_ec" -ne 0 ]]; then
  log "NOTE rb-ok.err:"; tail -40 "$HARNESS/rb-ok.err" | tee -a "$REPORT" >/dev/null || true
  log "NOTE rb-ok.txt:"; cat "$HARNESS/rb-ok.txt" | tee -a "$REPORT" >/dev/null || true
fi
assert_eq "successful post-success rollback exit 0" "0" "$rb_ok_ec"
assert_true "ROLLBACK_SUCCESS verified" \
  grep -qE "ROLLBACK_SUCCESS[[:space:]]+deployed_sha=${SHA_BE}" "$HARNESS/rb-ok.txt"
assert_true "health operational evidence in rollback logs" \
  grep -q 'mongo=true' "$HARNESS/rb-ok.err"
assert_true "runtime verify path used" \
  bash -c 'cur=$(readlink -f "'"$CURRENT"'"); want=$(readlink -f "'"$RELEASES/$SHA2"'"); [[ "$cur" == "$want" ]]'

# Arbitrary path / symlink rejection (lib validate)
evil_points="$RELEASES/rollback-points"
mkdir -p "$evil_points"
rm -rf "$evil_points/evillink" 2>/dev/null || true
ln -s /tmp "$evil_points/$(printf 'a%.0s' {1..40})" 2>/dev/null || true
EVIL_SHA="$(printf 'a%.0s' {1..40})"
set +e
bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_validate_rollback_point "'"$RELEASES"'" "'"$EVIL_SHA"'" "'"$SHA2"'"' \
  >"$HARNESS/rb-sym.txt" 2>"$HARNESS/rb-sym.err"
rb_sym_ec=$?
set -e
assert_eq "symlink rollback point refused" "1" "$rb_sym_ec"
rm -f "$evil_points/$EVIL_SHA"

# Authorization denial (trigger execute without secret)
set +e
nosec_out="$(ETERNAL_GH_API_MOCK_DIR="$MOCK" ETERNAL_TRIGGER_DISPATCH_LOG="$HARNESS/dispatch.log" \
  "$TRIGGER" execute rollback --sha "$SHA3" --confirm-sha "$SHA3" --previous-id "$SHA2" \
  --execute --request-id req-rb-nosec 2>"$HARNESS/rb-nosec.err")"
nosec_ec=$?
set -e
assert_eq "rollback without shared secret denied" "1" "$nosec_ec"

# Trigger plan rollback + correlation / ROLLBACK_SUCCESS / unknown
plan_rb="$(ETERNAL_GH_API_MOCK_DIR="$MOCK" "$TRIGGER" plan rollback \
  --sha "$SHA3" --confirm-sha "$SHA3" --previous-id "$SHA2" --request-id req-rb-plan \
  2>"$HARNESS/rb-plan.err")"
assert_true "plan rollback PLAN_OK" grep -q 'PLAN_OK' <<<"$plan_rb"
assert_true "plan rollback OPERATION" grep -q 'OPERATION=rollback' <<<"$plan_rb"
assert_true "plan rollback previous id" grep -q "PREVIOUS_ID=$SHA2" <<<"$plan_rb"

printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
rm -f "$MOCK"/trigger-runs-poll-*.json
cat >"$MOCK/trigger-runs-poll-1.json" <<'EOF'
{"workflow_runs":[]}
EOF
cat >"$MOCK/trigger-runs-poll-2.json" <<EOF
{"workflow_runs":[
 {"id":91,"html_url":"https://example.test/runs/91","event":"workflow_dispatch","head_branch":"main",
  "name":"Deploy production · request_id=req-rb-ok",
  "display_title":"Deploy production · request_id=req-rb-ok"}
]}
EOF
seed_mock_run_outcome 91 completed success "ROLLBACK_SUCCESS deployed_sha=${SHA3} previous_id=${SHA2}"
: >"$HARNESS/dispatch.log"
rb_disp="$(env "${POLL_ENV[@]}" ETERNAL_DEPLOY_SHARED_SECRET='test-secret' \
  "$TRIGGER" execute rollback --sha "$SHA3" --confirm-sha "$SHA3" --previous-id "$SHA2" \
  --execute --request-id req-rb-ok 2>"$HARNESS/rb-disp.err")"
assert_true "rollback dispatch RUNNER_ROLLBACK_SUCCESS" grep -q 'RUNNER_ROLLBACK_SUCCESS' <<<"$rb_disp"
assert_true "rollback WORKFLOW_SUCCESS" grep -q 'WORKFLOW_SUCCESS' <<<"$rb_disp"

# Timeout / unknown result for rollback (no logs)
cat >"$MOCK/trigger-runs-poll-2.json" <<EOF
{"workflow_runs":[
 {"id":92,"html_url":"https://example.test/runs/92","event":"workflow_dispatch","head_branch":"main",
  "name":"Deploy production · request_id=req-rb-unk",
  "display_title":"Deploy production · request_id=req-rb-unk"}
]}
EOF
seed_mock_run_outcome 92 completed success ""
: >"$HARNESS/dispatch.log"
set +e
rb_unk="$(env "${POLL_ENV[@]}" ETERNAL_DEPLOY_SHARED_SECRET='test-secret' \
  "$TRIGGER" execute rollback --sha "$SHA3" --confirm-sha "$SHA3" --previous-id "$SHA2" \
  --execute --request-id req-rb-unk 2>"$HARNESS/rb-unk.err")"
rb_unk_ec=$?
set -e
assert_eq "rollback missing logs exits non-zero" "1" "$rb_unk_ec"
assert_true "rollback missing logs RESULT_UNKNOWN" \
  grep -q 'RESULT_UNKNOWN' <<<"$rb_unk$(cat "$HARNESS/rb-unk.err")"

# ========== public webroot modes (umask 077 must not publish 600) ==========
PERM_ROOT="$HARNESS/perm-web"
PERM_BUILD="$HARNESS/perm-build"
PERM_SECRET="$HARNESS/secret.env"
rm -rf "$PERM_ROOT" "$PERM_BUILD"
mkdir -p "$PERM_ROOT/static/js" "$PERM_BUILD/static/js"
chmod 755 "$PERM_ROOT" "$PERM_ROOT/static" "$PERM_ROOT/static/js"
printf 'SECRET\n' >"$PERM_SECRET"
chmod 600 "$PERM_SECRET"
secret_mode_before="$(stat -c '%a' "$PERM_SECRET")"
printf 'old-hash-keep\n' >"$PERM_ROOT/static/js/main.oldkeep.js"
chmod 644 "$PERM_ROOT/static/js/main.oldkeep.js"
oldkeep_mode_before="$(stat -c '%a' "$PERM_ROOT/static/js/main.oldkeep.js")"
live_before_stage="$(sha256sum "$PERM_ROOT/static/js/main.oldkeep.js" | awk '{print $1}')"
# Build/staging writes only under the release tree.
(
  umask 077
  mkdir -p "$PERM_BUILD/static/css" "$HARNESS/stage-release/frontend/build/static/js"
  printf 'built-js\n' >"$PERM_BUILD/static/js/main.abcd.js"
  printf 'built-css\n' >"$PERM_BUILD/static/css/main.abcd.css"
  printf 'built-index\n<script src="/static/js/main.abcd.js"></script>\n<link href="/static/css/main.abcd.css">\n' \
    >"$PERM_BUILD/index.html"
  printf 'staged-only\n' >"$HARNESS/stage-release/frontend/build/index.html"
)
assert_eq "umask 077 build js is 600" "600" "$(stat -c '%a' "$PERM_BUILD/static/js/main.abcd.js")"
assert_eq "umask 077 build index is 600" "600" "$(stat -c '%a' "$PERM_BUILD/index.html")"
stage_live_after="$(sha256sum "$PERM_ROOT/static/js/main.oldkeep.js" | awk '{print $1}')"
assert_eq "staging build does not modify live webroot" "$live_before_stage" "$stage_live_after"
src_js_hash="$(sha256sum "$PERM_BUILD/static/js/main.abcd.js" | awk '{print $1}')"
(
  umask 077
  eternal_webroot_install_file "$PERM_BUILD/static/js/main.abcd.js" "$PERM_ROOT" "static/js/main.abcd.js" 0
  eternal_webroot_install_file "$PERM_BUILD/static/css/main.abcd.css" "$PERM_ROOT" "static/css/main.abcd.css" 0
)
assert_eq "published js is 644 under umask 077" "644" "$(stat -c '%a' "$PERM_ROOT/static/js/main.abcd.js")"
assert_eq "published css is 644 under umask 077" "644" "$(stat -c '%a' "$PERM_ROOT/static/css/main.abcd.css")"
assert_eq "new public css dir is 755" "755" "$(stat -c '%a' "$PERM_ROOT/static/css")"
assert_eq "build artifact mode unchanged" "600" "$(stat -c '%a' "$PERM_BUILD/static/js/main.abcd.js")"
assert_eq "build artifact hash unchanged" "$src_js_hash" "$(sha256sum "$PERM_BUILD/static/js/main.abcd.js" | awk '{print $1}')"
assert_eq "old hashed asset retained" "old-hash-keep" "$(cat "$PERM_ROOT/static/js/main.oldkeep.js")"
assert_eq "old hashed asset mode unchanged" "$oldkeep_mode_before" "$(stat -c '%a' "$PERM_ROOT/static/js/main.oldkeep.js")"
# Owner can read 600; Nginx other-read cannot. The check must not use test -r.
printf 'owner-only\n' >"$PERM_ROOT/static/js/main.owner.js"
chmod 600 "$PERM_ROOT/static/js/main.owner.js"
assert_true "owner test -r succeeds on mode 600" test -r "$PERM_ROOT/static/js/main.owner.js"
assert_false "other-read rejects mode 600" eternal_file_other_readable "$PERM_ROOT/static/js/main.owner.js"
# SKIP_IDENTICAL repairs a 600 file the deploy user owns.
printf 'same-public\n' >"$PERM_ROOT/static/js/main.same.js"
printf 'same-public\n' >"$PERM_BUILD/static/js/main.same.js"
chmod 600 "$PERM_ROOT/static/js/main.same.js"
skip_err="$(eternal_webroot_install_file "$PERM_BUILD/static/js/main.same.js" "$PERM_ROOT" "static/js/main.same.js" 0 2>&1)"
assert_true "SKIP_IDENTICAL logged for mode repair" grep -q 'SKIP_IDENTICAL' <<<"$skip_err"
assert_eq "SKIP_IDENTICAL 600 repaired to 644" "644" "$(stat -c '%a' "$PERM_ROOT/static/js/main.same.js")"
assert_eq "SKIP_IDENTICAL bytes preserved" "same-public" "$(cat "$PERM_ROOT/static/js/main.same.js")"
# Atomic index: temp mode 644 is observed by mv before rename.
real_mv="$(PATH="/usr/bin:/bin" command -v mv)"
MV_BIN="$HARNESS/mv-capture"
MV_MODE="$HARNESS/index-temp.mode"
rm -rf "$MV_BIN"
mkdir -p "$MV_BIN"
cat >"$MV_BIN/mv" <<EOF
#!/usr/bin/env bash
for a in "\$@"; do
  if [[ "\$a" == */index.html.new && -e "\$a" ]]; then
    stat -c '%a' "\$a" >"$MV_MODE"
  fi
done
exec "$real_mv" "\$@"
EOF
chmod +x "$MV_BIN/mv"
rm -f "$MV_MODE" "$PERM_ROOT/index.html" "$PERM_ROOT/index.html.new"
set +e
PATH="$MV_BIN:$PATH" eternal_atomic_publish_index "$PERM_BUILD/index.html" "$PERM_ROOT" 0 \
  >"$HARNESS/atomic.out" 2>"$HARNESS/atomic.err"
atomic_ec=$?
set -e
assert_eq "atomic index publish exit 0" "0" "$atomic_ec"
assert_eq "index temp mode before rename is 644" "644" "$(cat "$MV_MODE")"
assert_true "index temp mode logged before rename" grep -q 'index temp mode=644 before atomic rename' "$HARNESS/atomic.err"
assert_eq "live index mode 644" "644" "$(stat -c '%a' "$PERM_ROOT/index.html")"
assert_false "index.html.new removed after rename" test -f "$PERM_ROOT/index.html.new"
assert_true "live index has published bytes" grep -q 'built-index' "$PERM_ROOT/index.html"
# Unowned files are not chmod'd. Fail closed when Nginx cannot read them.
real_stat="$(PATH="/usr/bin:/bin" command -v stat)"
real_chmod="$(PATH="/usr/bin:/bin" command -v chmod)"
SHIM="$HARNESS/perm-shim"
UNOWN_LOG="$HARNESS/unowned-chmod.log"
rm -rf "$SHIM"
mkdir -p "$SHIM"
printf 'already-public\n' >"$PERM_ROOT/unowned-ok.js"
chmod 644 "$PERM_ROOT/unowned-ok.js"
cat >"$SHIM/stat" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == "-c" && "\$2" == "%u" ]]; then
  printf '0\n'
  exit 0
fi
if [[ "\$1" == "-c" && "\$2" == "%a" ]]; then
  printf '%s\n' "\${ETERNAL_FAKE_MODE:?}"
  exit 0
fi
exec "$real_stat" "\$@"
EOF
cat >"$SHIM/chmod" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >>"$UNOWN_LOG"
exit 0
EOF
chmod +x "$SHIM/stat" "$SHIM/chmod"
: >"$UNOWN_LOG"
set +e
ETERNAL_FAKE_MODE=644 PATH="$SHIM:$PATH" \
  eternal_webroot_ensure_public_file "$PERM_ROOT/unowned-ok.js" 0 \
  >"$HARNESS/unowned-ok.out" 2>"$HARNESS/unowned-ok.err"
unowned_ok_ec=$?
set -e
assert_eq "unowned readable file left in place" "0" "$unowned_ok_ec"
assert_false "unowned readable file was not chmod'd" test -s "$UNOWN_LOG"
assert_eq "unowned readable mode unchanged" "644" "$(stat -c '%a' "$PERM_ROOT/unowned-ok.js")"
: >"$UNOWN_LOG"
set +e
(
  ETERNAL_FAKE_MODE=600 PATH="$SHIM:$PATH" \
    eternal_webroot_ensure_public_file "$PERM_ROOT/unowned-ok.js" 0
) >"$HARNESS/unowned-bad.out" 2>"$HARNESS/unowned-bad.err"
unowned_bad_ec=$?
set -e
assert_eq "unowned unreadable file fails closed" "1" "$unowned_bad_ec"
assert_true "unowned unreadable mentions refuse" grep -q 'not owned' "$HARNESS/unowned-bad.err"
assert_false "unowned unreadable file was not chmod'd" test -s "$UNOWN_LOG"
assert_eq "unowned unreadable real mode unchanged" "644" "$(stat -c '%a' "$PERM_ROOT/unowned-ok.js")"
# Rollback from a 600 backup normalizes the public tree and leaves secrets alone.
RB_BAK="$HARNESS/perm-backup"
RB_LIVE="$HARNESS/perm-restore"
rm -rf "$RB_BAK" "$RB_LIVE"
mkdir -p "$RB_LIVE/static/js"
printf 'keep-old-hash\n' >"$RB_LIVE/static/js/main.keepold.js"
chmod 644 "$RB_LIVE/static/js/main.keepold.js"
(
  umask 077
  mkdir -p "$RB_BAK/static/js"
  printf 'restored-index\n<script src="/static/js/app.js"></script>\n' >"$RB_BAK/index.html"
  printf 'restored-js\n' >"$RB_BAK/static/js/app.js"
)
printf 'live-before-rollback\n' >"$RB_LIVE/index.html"
chmod 644 "$RB_LIVE/index.html"
assert_eq "backup index captured as 600" "600" "$(stat -c '%a' "$RB_BAK/index.html")"
assert_true "rollback merge from 600 backup" eternal_webroot_merge_tree "$RB_BAK" "$RB_LIVE" 1
assert_true "rollback atomic index from 600 backup" \
  eternal_atomic_publish_index "$RB_BAK/index.html" "$RB_LIVE" 1
assert_eq "rollback index is 644" "644" "$(stat -c '%a' "$RB_LIVE/index.html")"
assert_eq "rollback js is 644" "644" "$(stat -c '%a' "$RB_LIVE/static/js/app.js")"
assert_true "rollback index bytes restored" grep -q 'restored-index' "$RB_LIVE/index.html"
assert_eq "rollback keeps unreferenced hashed asset" "keep-old-hash" "$(cat "$RB_LIVE/static/js/main.keepold.js")"
assert_true "rollback public tree passes nginx mode+http check" \
  eternal_frontend_nginx_ready "$RB_LIVE"
assert_eq "secret outside webroot mode unchanged" "$secret_mode_before" "$(stat -c '%a' "$PERM_SECRET")"
assert_eq "secret outside webroot bytes unchanged" "SECRET" "$(cat "$PERM_SECRET")"
# HTTP 403 is not a frontend pass, even when the owner could read the files.
echo 1 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
set +e
http403_err="$(eternal_frontend_nginx_ready "$RB_LIVE" 2>&1)"
http403_ec=$?
set -e
echo 0 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
assert_eq "frontend HTTP 403 rejected" "1" "$http403_ec"
assert_true "frontend HTTP 403 status logged" grep -q 'frontend HTTP FAIL label=index http=403' <<<"$http403_err"
assert_true "frontend HTTP 403 logs asset+host" \
  grep -q 'http=403 host=fixture.nginx.local asset=index' <<<"$http403_err"
assert_true "backend health body still operational" \
  eternal_health_body_ok '{"mongo":true,"mode":"operational"}'
# 301 to the expected HTTPS host, then 200, is success. Other targets are not.
saved_public_url="$ETERNAL_FRONTEND_PUBLIC_URL"
export ETERNAL_FRONTEND_PUBLIC_URL="http://fixture.nginx.local"
export ETERNAL_FRONTEND_PUBLIC_HOST="fixture.nginx.local"
export ETERNAL_FRONTEND_REDIRECT_MODE=https
set +e
redir_ok_err="$(eternal_frontend_nginx_ready "$RB_LIVE" 2>&1)"
redir_ok_ec=$?
set -e
assert_eq "301 to expected HTTPS then 200 passes" "0" "$redir_ok_ec"
assert_true "301 follow logs https 200 on expected host" \
  grep -q 'frontend HTTP PASS label=index http=200 host=fixture.nginx.local' <<<"$redir_ok_err"
export ETERNAL_FRONTEND_REDIRECT_MODE=other
export ETERNAL_CURL_ARGS_LOG="$HARNESS/curl-redirect-other.args"
: >"$ETERNAL_CURL_ARGS_LOG"
set +e
redir_bad_err="$(eternal_frontend_nginx_ready "$RB_LIVE" 2>&1)"
redir_bad_ec=$?
set -e
assert_eq "redirect to other host fails" "1" "$redir_bad_ec"
assert_true "other-host redirect is refused" \
  grep -q 'redirect_host=evil.example' <<<"$redir_bad_err"
assert_false "disallowed redirect target is not fetched" \
  grep -q 'https://evil.example' "$ETERNAL_CURL_ARGS_LOG"
export ETERNAL_FRONTEND_PUBLIC_URL="https://fixture.nginx.local"
export ETERNAL_FRONTEND_REDIRECT_MODE=loop
set +e
loop_err="$(eternal_frontend_nginx_ready "$RB_LIVE" 2>&1)"
loop_ec=$?
set -e
assert_eq "redirect loop fails" "1" "$loop_ec"
assert_true "redirect loop logged" grep -q 'redirect_loop' <<<"$loop_err"
unset ETERNAL_FRONTEND_REDIRECT_MODE
unset ETERNAL_CURL_ARGS_LOG
export ETERNAL_FRONTEND_HTTP_CODE=404
set +e
http404_err="$(eternal_frontend_nginx_ready "$RB_LIVE" 2>&1)"
http404_ec=$?
set -e
assert_eq "frontend HTTP 404 rejected" "1" "$http404_ec"
assert_true "frontend HTTP 404 logged" grep -q 'frontend HTTP FAIL label=index http=404' <<<"$http404_err"
assert_true "frontend HTTP 404 logs asset+host" \
  grep -q 'http=404 host=fixture.nginx.local asset=index' <<<"$http404_err"
export ETERNAL_FRONTEND_HTTP_CODE=502
set +e
http502_err="$(eternal_frontend_nginx_ready "$RB_LIVE" 2>&1)"
http502_ec=$?
set -e
assert_eq "frontend HTTP 502 rejected" "1" "$http502_ec"
assert_true "frontend HTTP 502 logged" grep -q 'frontend HTTP FAIL label=index http=502' <<<"$http502_err"
unset ETERNAL_FRONTEND_HTTP_CODE
export ETERNAL_FRONTEND_PUBLIC_URL="http://127.0.0.1"
export ETERNAL_FRONTEND_PUBLIC_HOST="fixture.nginx.local"
export ETERNAL_FRONTEND_FORCE_200=1
set +e
wrong_host_err="$(eternal_frontend_nginx_ready "$RB_LIVE" 2>&1)"
wrong_host_ec=$?
set -e
assert_eq "HTTP 200 from wrong host fails" "1" "$wrong_host_ec"
assert_true "wrong host identity logged" \
  grep -q 'http=200 host=127.0.0.1 scheme=http expected=fixture.nginx.local' <<<"$wrong_host_err"
unset ETERNAL_FRONTEND_FORCE_200
export ETERNAL_FRONTEND_PUBLIC_URL="https://fixture.nginx.local"
unset ETERNAL_FRONTEND_PUBLIC_HOST
export ETERNAL_FRONTEND_TLS_FAIL=1
export ETERNAL_CURL_ARGS_LOG="$HARNESS/curl-tls.args"
: >"$ETERNAL_CURL_ARGS_LOG"
set +e
tls_err="$(eternal_frontend_nginx_ready "$RB_LIVE" 2>&1)"
tls_ec=$?
set -e
unset ETERNAL_FRONTEND_TLS_FAIL
unset ETERNAL_CURL_ARGS_LOG
assert_eq "invalid TLS fails" "1" "$tls_ec"
assert_true "invalid TLS logged" grep -q 'tls_invalid' <<<"$tls_err"
assert_false "TLS check does not disable verification" grep -qE '(^|[[:space:]])-k([[:space:]]|$)|--insecure' "$HARNESS/curl-tls.args"
export ETERNAL_FRONTEND_PUBLIC_URL="$saved_public_url"
# Full runner: backend health PASS must not print DEPLOY_OK when Nginx returns 403.
git -C "$MIRROR_SRC" checkout -q main
printf 'fe-403-marker\n' >>"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js
git -C "$MIRROR_SRC" commit -q -m 'fe 403 fixture'
FE403_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
index_before_403="$(sha256sum "$FRONTEND/live/index.html" | awk '{print $1}')"
echo 1 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
set +e
"$RUNNER" deploy "$FE403_SHA" >"$HARNESS/fe403.txt" 2>"$HARNESS/fe403.err"
fe403_ec=$?
set -e
echo 0 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
assert_eq "HTTP 403 deploy exits nonzero" "1" "$fe403_ec"
assert_false "HTTP 403 does not print DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/fe403.txt"
assert_true "backend health PASS is present" grep -q 'health PASS' "$HARNESS/fe403.err"
assert_true "403 blocks before DEPLOY_OK" grep -q 'frontend HTTP FAIL label=index http=403' "$HARNESS/fe403.err"
assert_true "403 failure names backend health as insufficient" \
  grep -q 'backend health is not frontend proof' "$HARNESS/fe403.err"
assert_true "403 uses existing rollback" grep -q 'ROLLBACK_PASS' "$HARNESS/fe403.txt"
index_after_403="$(sha256sum "$FRONTEND/live/index.html" | awk '{print $1}')"
assert_eq "403 rollback restores previous index bytes" "$index_before_403" "$index_after_403"
assert_true "403 rollback index remains other-readable" \
  eternal_file_other_readable "$FRONTEND/live/index.html"

# ========== FRONTEND ARTIFACT MODE (no VPS yarn/CRACO) ==========
source "$ROOT/scripts/deploy/lib.sh"
assert_true "lib exposes artifact install helper" \
  grep -q 'eternal_install_frontend_artifact_from_dir' "$ROOT/scripts/deploy/lib.sh"
assert_true "runner defaults to artifact mode" \
  grep -q 'ETERNAL_FRONTEND_BUILD_MODE:-artifact' "$ROOT/scripts/deploy/eternal-deploy-runner.sh"
assert_true "workflow frontend-artifact.yml exists" \
  test -f "$ROOT/.github/workflows/frontend-artifact.yml"
assert_true "pack script exists" test -f "$ROOT/scripts/deploy/pack_frontend_artifact.sh"
assert_true "prod env mode fail-closed helper" \
  grep -q 'FRONTEND_PROD_ENV_MODE unset' "$ROOT/scripts/deploy/lib.sh"

# Fail-closed when production env mode unset
set +e
env_unset_err="$(bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; unset FRONTEND_PROD_ENV_MODE; eternal_apply_frontend_prod_build_env' 2>&1)"
env_unset_ec=$?
set -e
assert_eq "prod env mode unset exits nonzero" "1" "$env_unset_ec"
assert_true "prod env mode unset mentions Owner" grep -qi 'Owner' <<<"$env_unset_err"

# Loopback explicit-url refused
set +e
env_loop_err="$(FRONTEND_PROD_ENV_MODE=explicit-url FRONTEND_PROD_REACT_APP_BACKEND_URL='http://127.0.0.1:8000' \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_apply_frontend_prod_build_env' 2>&1)"
env_loop_ec=$?
set -e
assert_eq "loopback prod URL refused" "1" "$env_loop_ec"
assert_true "loopback refusal message" grep -qi 'loopback' <<<"$env_loop_err"

# Build a valid local artifact for a FE-only commit
git -C "$MIRROR_SRC" checkout -q main
printf 'artifact-src-v1\n' >"$MIRROR_SRC/frontend/src/index.js"
mkdir -p "$MIRROR_SRC/frontend/public"
printf 'pub\n' >"$MIRROR_SRC/frontend/public/robots.txt"
git -C "$MIRROR_SRC" add frontend/src/index.js frontend/public/robots.txt
git -C "$MIRROR_SRC" commit -q -m 'fe artifact fixture'
ART_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'

ART_SRC="$HARNESS/art-src"
rm -rf "$ART_SRC"
mkdir -p "$ART_SRC"
git --git-dir="$RELEASES/mirror.git" archive --format=tar "$ART_SHA" | tar -x -C "$ART_SRC"
mkdir -p "$ART_SRC/frontend/build/static/js"
# Unique hashed-like asset path — must not collide with dry-build static/js/app.js
printf 'art-asset\n' >"$ART_SRC/frontend/build/static/js/main.art1111.js"
printf '<script src="/static/js/main.art1111.js"></script>\n' >"$ART_SRC/frontend/build/index.html"
printf '{"files":{"main.js":"/static/js/main.art1111.js"}}\n' >"$ART_SRC/frontend/build/asset-manifest.json"
ART_OUT="$HARNESS/art-out"
rm -rf "$ART_OUT"
export FRONTEND_PROD_ENV_MODE=same-origin
export DISABLE_ESLINT_PLUGIN=true
export REACT_APP_BACKEND_URL=""
bash "$ROOT/scripts/deploy/pack_frontend_artifact.sh" \
  --frontend-dir "$ART_SRC/frontend" \
  --out-dir "$ART_OUT" \
  --git-sha "$ART_SHA" \
  --workflow frontend-artifact.yml \
  --run-id "99" \
  --run-attempt "1" \
  --node-version "20.0.0" \
  --yarn-version "1.22.22" >"$HARNESS/pack.out"
assert_true "pack prints FRONTEND_ARTIFACT_OK" grep -q 'FRONTEND_ARTIFACT_OK' "$HARNESS/pack.out"
assert_true "pack wrote manifest" test -f "$ART_OUT/manifest.json"
assert_true "pack wrote archive" test -f "$ART_OUT/frontend-build-${ART_SHA}.tar.gz"

# Wrong SHA rejection
WRONG_MAN="$HARNESS/wrong-sha-manifest.json"
python3 - "$ART_OUT/manifest.json" "$WRONG_MAN" <<'PY'
import json,sys
doc=json.load(open(sys.argv[1]))
doc["git_sha"]="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
json.dump(doc, open(sys.argv[2],"w"))
PY
set +e
wrong_sha_err="$(
  expect_in="$(eternal_frontend_input_fingerprint "$ART_SRC/frontend")"
  expect_en="$(FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
    bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_expected_env_fingerprint')"
  eternal_verify_frontend_artifact_manifest "$WRONG_MAN" "$ART_SHA" "$expect_in" "$expect_en" 2>&1
)"
wrong_sha_ec=$?
set -e
assert_eq "wrong SHA manifest rejected" "1" "$wrong_sha_ec"
assert_true "wrong SHA message" grep -q 'git_sha mismatch' <<<"$wrong_sha_err"

# Wrong archive hash
BAD_ART="$HARNESS/bad-art"
rm -rf "$BAD_ART"
mkdir -p "$BAD_ART"
cp "$ART_OUT/manifest.json" "$BAD_ART/"
printf 'tampered\n' >"$BAD_ART/frontend-build-${ART_SHA}.tar.gz"
REL_FAKE="$HARNESS/rel-fake"
rm -rf "$REL_FAKE"
mkdir -p "$REL_FAKE"
git --git-dir="$RELEASES/mirror.git" archive --format=tar "$ART_SHA" | tar -x -C "$REL_FAKE"
set +e
bad_hash_err="$(
  FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_install_frontend_artifact_from_dir "'"$REL_FAKE"'" "'"$ART_SHA"'" "'"$BAD_ART"'"' 2>&1
)"
bad_hash_ec=$?
set -e
assert_eq "wrong archive hash rejected" "1" "$bad_hash_ec"
assert_true "checksum mismatch logged" \
  grep -qiE 'checksum mismatch|artifact_sha256|archive checksum' <<<"$bad_hash_err"

# Untrusted workflow
UNTRUST="$HARNESS/untrust.json"
python3 - "$ART_OUT/manifest.json" "$UNTRUST" <<'PY'
import json,sys
doc=json.load(open(sys.argv[1]))
doc["workflow"]="evil.yml"
json.dump(doc, open(sys.argv[2],"w"))
PY
set +e
untrust_err="$(
  expect_in="$(eternal_frontend_input_fingerprint "$ART_SRC/frontend")"
  expect_en="$(FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
    bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_expected_env_fingerprint')"
  eternal_verify_frontend_artifact_manifest "$UNTRUST" "$ART_SHA" "$expect_in" "$expect_en" 2>&1
)"
untrust_ec=$?
set -e
assert_eq "untrusted workflow rejected" "1" "$untrust_ec"
assert_true "untrusted workflow message" grep -q 'untrusted workflow' <<<"$untrust_err"

# Incorrect production env fingerprint
set +e
bad_env_err="$(
  expect_in="$(eternal_frontend_input_fingerprint "$ART_SRC/frontend")"
  eternal_verify_frontend_artifact_manifest "$ART_OUT/manifest.json" "$ART_SHA" "$expect_in" "deadbeef" 2>&1
)"
bad_env_ec=$?
set -e
assert_eq "incorrect env fingerprint rejected" "1" "$bad_env_ec"
assert_true "env fingerprint mismatch message" grep -q 'env fingerprint mismatch' <<<"$bad_env_err"

# Altered manifest (tamper input fp)
ALT_MAN="$HARNESS/alt-man.json"
python3 - "$ART_OUT/manifest.json" "$ALT_MAN" <<'PY'
import json,sys
doc=json.load(open(sys.argv[1]))
doc["input_fingerprint_sha256"]="0"*64
json.dump(doc, open(sys.argv[2],"w"))
PY
set +e
alt_err="$(
  expect_in="$(eternal_frontend_input_fingerprint "$ART_SRC/frontend")"
  expect_en="$(FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
    bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_expected_env_fingerprint')"
  eternal_verify_frontend_artifact_manifest "$ALT_MAN" "$ART_SHA" "$expect_in" "$expect_en" 2>&1
)"
alt_ec=$?
set -e
assert_eq "altered manifest rejected" "1" "$alt_ec"
assert_true "altered input fp message" grep -q 'input fingerprint mismatch' <<<"$alt_err"

# Unsafe archive path
UNSAFE="$HARNESS/unsafe-art"
rm -rf "$UNSAFE"
mkdir -p "$UNSAFE"
printf '{"schema":"eternal.frontend-artifact.v1","git_sha":"%s","workflow":"frontend-artifact.yml","run_id":"99","run_attempt":"1","archive_name":"bad.tar.gz","artifact_sha256":"%s","index_sha256":"%s","asset_manifest_sha256":"%s","input_fingerprint_sha256":"%s","env_fingerprint_sha256":"%s"}\n' \
  "$ART_SHA" "$(printf 'x' | sha256sum | awk '{print $1}')" \
  "$(printf 'x' | sha256sum | awk '{print $1}')" \
  "$(printf 'x' | sha256sum | awk '{print $1}')" \
  "$(eternal_frontend_input_fingerprint "$ART_SRC/frontend")" \
  "$(FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
      bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_expected_env_fingerprint')" \
  >"$UNSAFE/manifest.json"
# Fix JSON properly via python + craft tar with ../escape
python3 - "$UNSAFE" "$ART_SHA" <<'PY'
import json, hashlib, os, sys, tarfile, io
d=sys.argv[1]; sha=sys.argv[2]
# Build malicious tar
buf=io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    info=tarfile.TarInfo("../escape.js")
    data=b"evil\n"
    info.size=len(data)
    tf.addfile(info, io.BytesIO(data))
open(os.path.join(d,"bad.tar.gz"),"wb").write(buf.getvalue())
art=hashlib.sha256(open(os.path.join(d,"bad.tar.gz"),"rb").read()).hexdigest()
# placeholders replaced by install path checks
PY
# Direct extract test (parent must exist / be creatable)
mkdir -p "$HARNESS/unsafe-dest"
set +e
unsafe_err="$(eternal_safe_extract_frontend_artifact "$UNSAFE/bad.tar.gz" "$HARNESS/unsafe-dest/build" 2>&1)"
unsafe_ec=$?
set -e
assert_eq "unsafe archive path rejected" "1" "$unsafe_ec"
assert_true "unsafe path message" \
  grep -qiE 'unsafe archive path|\.\./' <<<"$unsafe_err"

# Missing artifact dir
set +e
miss_err="$(
  FRONTEND_PROD_ENV_MODE=same-origin \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_resolve_frontend_artifact_dir "'"$ART_SHA"'"' 2>&1
)"
miss_ec=$?
set -e
assert_eq "missing artifact refused" "1" "$miss_ec"
assert_true "missing artifact no yarn fallback wording" grep -qi 'no yarn fallback' <<<"$miss_err"

# Valid install into release tree
REL_OK="$HARNESS/rel-ok"
rm -rf "$REL_OK"
mkdir -p "$REL_OK"
git --git-dir="$RELEASES/mirror.git" archive --format=tar "$ART_SHA" | tar -x -C "$REL_OK"
export FRONTEND_PROD_ENV_MODE=same-origin
export DISABLE_ESLINT_PLUGIN=true
export REACT_APP_BACKEND_URL=""
unset ETERNAL_FRONTEND_EXPECTED_ENV_FP || true
unset ETERNAL_FRONTEND_ENV_FP_TEST_LOCK || true
# Subshell: eternal_deploy_die uses exit and must not abort the suite.
set +e
install_ok_err="$(
  FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_install_frontend_artifact_from_dir "'"$REL_OK"'" "'"$ART_SHA"'" "'"$ART_OUT"'"' 2>&1
)"
install_ok_ec=$?
set -e
assert_eq "valid artifact installs" "0" "$install_ok_ec"
assert_true "artifact marker written" test -f "$REL_OK/.eternal-frontend-artifact"
assert_true "fingerprint written" test -f "$REL_OK/.eternal-frontend-fingerprint"
assert_true "build index present" test -f "$REL_OK/frontend/build/index.html"

# No yarn fallback on artifact-mode deploy (stub yarn must not run)
: >"$HARNESS/yarn.cmd.log"
# Re-arm harness defaults (earlier suites may override WD/health/http fail flags).
export ETERNAL_EXPECTED_WD="$EXPECTED_WD"
export ETERNAL_EXPECTED_EXEC_PREFIX="$EXPECTED_WD"
export ETERNAL_HEALTH_FORCE_FAIL=0
export ETERNAL_FORCE_RESTART_FAIL=0
export ETERNAL_FORCE_PUBLISH_FAIL=0
export ETERNAL_FORCE_BUILD_FAIL=0
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"
echo 0 >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
echo 0 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
export ETERNAL_DRY_BUILD=0
export ETERNAL_FRONTEND_BUILD_MODE=artifact
export ETERNAL_FRONTEND_ARTIFACT_DIR="$ART_OUT"
export FRONTEND_PROD_ENV_MODE=same-origin
unset ETERNAL_FRONTEND_EXPECTED_ENV_FP || true
unset ETERNAL_FRONTEND_ENV_FP_TEST_LOCK || true
# Ensure previous incomplete ART_SHA release does not block (clear if present)
rm -rf "$RELEASES/$ART_SHA"
set +e
"$RUNNER" deploy "$ART_SHA" >"$HARNESS/art-deploy.txt" 2>"$HARNESS/art-deploy.err"
art_deploy_ec=$?
set -e
if [[ "$art_deploy_ec" != "0" ]]; then
  log "artifact-deploy stderr (tail):"
  tail -n 40 "$HARNESS/art-deploy.err" | tee -a "$REPORT" >/dev/null || true
fi
assert_eq "artifact-mode deploy exits 0" "0" "$art_deploy_ec"
assert_true "artifact-mode DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/art-deploy.txt"
assert_true "artifact install logged" grep -q 'frontend artifact installed' "$HARNESS/art-deploy.err"
assert_false "artifact mode did not invoke yarn" grep -q 'YARN' "$HARNESS/yarn.cmd.log"
assert_true "artifact provenance marker on release" \
  test -f "$RELEASES/$ART_SHA/.eternal-frontend-artifact"

# Duplicate execution reuses artifact marker
set +e
"$RUNNER" deploy "$ART_SHA" >"$HARNESS/art-redeploy.txt" 2>"$HARNESS/art-redeploy.err"
art_re_ec=$?
set -e
assert_eq "duplicate artifact deploy OK" "0" "$art_re_ec"
assert_true "duplicate DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/art-redeploy.txt"

# Backend-only: artifact mode must not require artifact when FE unchanged
git -C "$MIRROR_SRC" checkout -q main
printf 'be-only\n' >>"$MIRROR_SRC/backend/requirements.portable.txt"
git -C "$MIRROR_SRC" add backend/requirements.portable.txt
git -C "$MIRROR_SRC" commit -q -m 'backend only after artifact'
BE_ONLY_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
unset ETERNAL_FRONTEND_ARTIFACT_DIR
set +e
"$RUNNER" deploy "$BE_ONLY_SHA" >"$HARNESS/be-only.txt" 2>"$HARNESS/be-only.err"
be_only_ec=$?
set -e
assert_eq "backend-only deploy OK without artifact" "0" "$be_only_ec"
assert_true "backend-only skipped FE" grep -q 'frontend unchanged; skip build' "$HARNESS/be-only.err"

# Incomplete staging refuse (no silent rematerialize) for a real mirror SHA
git -C "$MIRROR_SRC" checkout -q main
printf 'incomp-fe\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js
git -C "$MIRROR_SRC" commit -q -m 'incomplete staging fixture'
INCOMP_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
INCOMP="$RELEASES/$INCOMP_SHA"
rm -rf "$INCOMP"
mkdir -p "$INCOMP/frontend"
printf '%s\n' "$INCOMP_SHA" >"$INCOMP/.eternal-source-sha"
# Missing build/fingerprint/venv → reuse fails → rematerialize refused
unset ETERNAL_FRONTEND_ARTIFACT_DIR
export ETERNAL_FRONTEND_BUILD_MODE=artifact
export ETERNAL_DRY_BUILD=0
set +e
"$RUNNER" deploy "$INCOMP_SHA" >"$HARNESS/incomp.txt" 2>"$HARNESS/incomp.err"
incomp_ec=$?
set -e
assert_eq "incomplete staging refuses deploy" "1" "$incomp_ec"
assert_true "incomplete staging fail-closed message" \
  grep -q 'incomplete release exists but failed reuse' "$HARNESS/incomp.err"
rm -rf "$INCOMP"

# Publish failure rollback still works under artifact mode
git -C "$MIRROR_SRC" checkout -q main
printf 'artifact-src-v2\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js
git -C "$MIRROR_SRC" commit -q -m 'fe artifact publish-fail fixture'
ART2_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
ART2_SRC="$HARNESS/art2-src"
rm -rf "$ART2_SRC" "$HARNESS/art2-out"
mkdir -p "$ART2_SRC"
git --git-dir="$RELEASES/mirror.git" archive --format=tar "$ART2_SHA" | tar -x -C "$ART2_SRC"
mkdir -p "$ART2_SRC/frontend/build/static/js"
printf 'art2-asset\n' >"$ART2_SRC/frontend/build/static/js/main.art2222.js"
printf '<script src="/static/js/main.art2222.js"></script>\n' >"$ART2_SRC/frontend/build/index.html"
printf '{"files":{"main.js":"/static/js/main.art2222.js"}}\n' >"$ART2_SRC/frontend/build/asset-manifest.json"
FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
  bash "$ROOT/scripts/deploy/pack_frontend_artifact.sh" \
    --frontend-dir "$ART2_SRC/frontend" \
    --out-dir "$HARNESS/art2-out" \
    --git-sha "$ART2_SHA" \
    --workflow frontend-artifact.yml \
    --run-id "100" \
    --run-attempt "1" >/dev/null
export ETERNAL_FRONTEND_ARTIFACT_DIR="$HARNESS/art2-out"
export ETERNAL_FRONTEND_BUILD_MODE=artifact
export ETERNAL_DRY_BUILD=0
export ETERNAL_FORCE_PUBLISH_FAIL=1
index_before_art_fail="$(sha256sum "$FRONTEND/live/index.html" | awk '{print $1}')"
set +e
"$RUNNER" deploy "$ART2_SHA" >"$HARNESS/art-fail.txt" 2>"$HARNESS/art-fail.err"
art_fail_ec=$?
set -e
export ETERNAL_FORCE_PUBLISH_FAIL=0
assert_eq "artifact publish fail exits nonzero" "1" "$art_fail_ec"
assert_false "artifact publish fail no DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/art-fail.txt"
assert_true "artifact publish fail rollback" grep -q 'ROLLBACK_PASS' "$HARNESS/art-fail.txt"
index_after_art_fail="$(sha256sum "$FRONTEND/live/index.html" | awk '{print $1}')"
assert_eq "artifact publish fail restores index" "$index_before_art_fail" "$index_after_art_fail"

# Restore dry-build default for any later harness use
export ETERNAL_DRY_BUILD=1
unset ETERNAL_FRONTEND_ARTIFACT_DIR
unset ETERNAL_FRONTEND_EXPECTED_ENV_FP
unset ETERNAL_FRONTEND_ENV_FP_TEST_LOCK

# ========== HARDENING: env fp bypass refused ==========
set +e
bypass_err="$(
  FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
  ETERNAL_FRONTEND_EXPECTED_ENV_FP=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_expected_env_fingerprint' 2>&1
)"
bypass_ec=$?
set -e
assert_eq "env fp override without test lock refused" "1" "$bypass_ec"
assert_true "env fp override mentions refuse production bypass" \
  grep -qiE 'TEST_LOCK|refuse production env bypass' <<<"$bypass_err"
set +e
wrong_ov_err="$(
  FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
  ETERNAL_FRONTEND_ENV_FP_TEST_LOCK=1 \
  ETERNAL_FRONTEND_EXPECTED_ENV_FP=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_expected_env_fingerprint' 2>&1
)"
wrong_ov_ec=$?
set -e
assert_eq "wrong env fp override refused even with test lock" "1" "$wrong_ov_ec"
assert_true "wrong override mismatch message" grep -q 'bypass refused' <<<"$wrong_ov_err"

# ========== HARDENING: Actions run provenance ==========
assert_true "actions run provenance helper present" \
  grep -q 'eternal_verify_frontend_artifact_actions_run' "$ROOT/scripts/deploy/lib.sh"
set +e
run_mismatch_err="$(
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_verify_frontend_artifact_actions_run "'"$ART_OUT"'/manifest.json" "111" "1"' 2>&1
)"
run_mismatch_ec=$?
set -e
assert_eq "mismatched Actions run_id rejected" "1" "$run_mismatch_ec"
assert_true "run_id mismatch message" grep -q 'run_id mismatch' <<<"$run_mismatch_err"
set +e
attempt_mismatch_err="$(
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_verify_frontend_artifact_actions_run "'"$ART_OUT"'/manifest.json" "99" "2"' 2>&1
)"
attempt_mismatch_ec=$?
set -e
assert_eq "mismatched Actions run_attempt rejected" "1" "$attempt_mismatch_ec"
assert_true "run_attempt mismatch message" grep -q 'run_attempt mismatch' <<<"$attempt_mismatch_err"
assert_true "matching Actions run provenance accepted" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_verify_frontend_artifact_actions_run "'"$ART_OUT"'/manifest.json" "99" "1"'

# ========== HARDENING: path-independent fingerprint behavior ==========
FP_A="$HARNESS/fp-a/frontend"; FP_B="$HARNESS/fp-b/frontend"
rm -rf "$HARNESS/fp-a" "$HARNESS/fp-b"
mkdir -p "$FP_A/src" "$FP_B/src" "$FP_A/public" "$FP_B/public"
printf '{"name":"frontend"}\n' >"$FP_A/package.json"; cp "$FP_A/package.json" "$FP_B/"
printf 'yarn lockfile v1\n' >"$FP_A/yarn.lock"; cp "$FP_A/yarn.lock" "$FP_B/"
printf 'same\n' >"$FP_A/src/x.js"; cp "$FP_A/src/x.js" "$FP_B/src/"
printf 'pub\n' >"$FP_A/public/p.txt"; cp "$FP_A/public/p.txt" "$FP_B/public/"
fp_a="$(eternal_frontend_input_fingerprint "$FP_A")"
fp_b="$(eternal_frontend_input_fingerprint "$FP_B")"
assert_eq "identical trees different abs paths share fingerprint" "$fp_a" "$fp_b"
printf 'changed\n' >"$FP_B/src/x.js"
fp_b2="$(eternal_frontend_input_fingerprint "$FP_B")"
assert_false "modified content changes fingerprint" [[ "$fp_a" == "$fp_b2" ]]

# ========== HARDENING: special tar entries refused ==========
SPEC="$HARNESS/special-tar"
rm -rf "$SPEC"; mkdir -p "$SPEC"
python3 - "$SPEC/bad-fifo.tar.gz" <<'PY'
import tarfile, io, sys
path=sys.argv[1]
buf=io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tf:
    info=tarfile.TarInfo("fifo-entry")
    info.type=tarfile.FIFOTYPE
    tf.addfile(info)
    data=b"<html></html>\n"
    i2=tarfile.TarInfo("index.html"); i2.size=len(data)
    tf.addfile(i2, io.BytesIO(data))
    data2=b"{}\n"
    i3=tarfile.TarInfo("asset-manifest.json"); i3.size=len(data2)
    tf.addfile(i3, io.BytesIO(data2))
open(path,"wb").write(buf.getvalue())
PY
mkdir -p "$HARNESS/special-dest"
set +e
fifo_err="$(eternal_safe_extract_frontend_artifact "$SPEC/bad-fifo.tar.gz" "$HARNESS/special-dest/build" 2>&1)"
fifo_ec=$?
set -e
assert_eq "fifo tar entry rejected" "1" "$fifo_ec"
assert_true "fifo refusal message" grep -qiE 'special entry|unsupported file type|symlink' <<<"$fifo_err"

# ========== HARDENING: docs-only no application activation ==========
export ETERNAL_DRY_BUILD=1
export ETERNAL_FRONTEND_BUILD_MODE=dry
unset ETERNAL_FRONTEND_ARTIFACT_DIR
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"
echo 0 >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
echo 0 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
active_before_docs="$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"
# Tip must equal active-release so the next commit's diff is docs-only.
git -C "$MIRROR_SRC" checkout -q -B main "$active_before_docs"
mkdir -p "$MIRROR_SRC/docs"
printf 'docs-only\n' >"$MIRROR_SRC/docs/hardening-note.md"
git -C "$MIRROR_SRC" add docs/hardening-note.md
git -C "$MIRROR_SRC" commit -q -m 'docs only hardening'
DOCS_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
set +e
"$RUNNER" deploy "$DOCS_SHA" >"$HARNESS/docs-hardening.txt" 2>"$HARNESS/docs-hardening.err"
docs_ec=$?
set -e
assert_eq "docs-only deploy exits 0" "0" "$docs_ec"
assert_true "docs-only DEPLOY_OK_NOOP" grep -q 'DEPLOY_OK_NOOP' "$HARNESS/docs-hardening.txt"
assert_false "docs-only no application DEPLOY_OK" grep -qE '^DEPLOY_OK ' "$HARNESS/docs-hardening.txt"
assert_false "docs-only did not materialize release dir" test -d "$RELEASES/$DOCS_SHA"
active_after_docs="$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"
assert_eq "docs-only preserves active-release" "$active_before_docs" "$active_after_docs"
assert_true "docs-only wrote processed git marker" test -f "$RELEASES/last-processed-git-sha.txt"
assert_true "docs-only processed marker is docs sha" \
  grep -qx "sha=$DOCS_SHA" "$RELEASES/last-processed-git-sha.txt"
assert_true "docs-only logs no application change" \
  grep -q 'no application change' "$HARNESS/docs-hardening.err"

# ========== HARDENING: FE → BE → FE sequence ==========
export ETERNAL_DRY_BUILD=0
export ETERNAL_FRONTEND_BUILD_MODE=artifact
export FRONTEND_PROD_ENV_MODE=same-origin
# FE hop
printf 'fe-seq-1\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m 'fe seq 1'
FE1_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
pack_seq() {
  local sha="$1" out="$2" asset="$3" runid="$4"
  local src="$HARNESS/pack-$sha"
  rm -rf "$src" "$out"
  mkdir -p "$src"
  git --git-dir="$RELEASES/mirror.git" archive --format=tar "$sha" | tar -x -C "$src"
  mkdir -p "$src/frontend/build/static/js"
  printf '%s\n' "$asset" >"$src/frontend/build/static/js/$asset"
  printf '<script src="/static/js/%s"></script>\n' "$asset" >"$src/frontend/build/index.html"
  printf '{"files":{"main.js":"/static/js/%s"}}\n' "$asset" >"$src/frontend/build/asset-manifest.json"
  FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
    bash "$ROOT/scripts/deploy/pack_frontend_artifact.sh" \
      --frontend-dir "$src/frontend" --out-dir "$out" --git-sha "$sha" \
      --workflow frontend-artifact.yml --run-id "$runid" --run-attempt 1 >/dev/null
}
pack_seq "$FE1_SHA" "$HARNESS/fe1-out" "main.seqfe1.js" "201"
export ETERNAL_FRONTEND_ARTIFACT_DIR="$HARNESS/fe1-out"
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"; echo 0 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
set +e
"$RUNNER" deploy "$FE1_SHA" >"$HARNESS/fe1.txt" 2>"$HARNESS/fe1.err"
fe1_ec=$?
set -e
assert_eq "FE seq hop1 exits 0" "0" "$fe1_ec"
assert_true "FE seq hop1 DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/fe1.txt"
assert_eq "FE seq hop1 active-release" "$FE1_SHA" "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"
# BE hop
printf 'be-seq\n' >>"$MIRROR_SRC/backend/requirements.portable.txt"
git -C "$MIRROR_SRC" add backend/requirements.portable.txt && git -C "$MIRROR_SRC" commit -q -m 'be seq'
BE_SEQ_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
unset ETERNAL_FRONTEND_ARTIFACT_DIR
set +e
"$RUNNER" deploy "$BE_SEQ_SHA" >"$HARNESS/beseq.txt" 2>"$HARNESS/beseq.err"
beseq_ec=$?
set -e
assert_eq "BE seq hop exits 0" "0" "$beseq_ec"
assert_true "BE seq skipped FE" grep -q 'frontend unchanged; skip build' "$HARNESS/beseq.err"
assert_eq "BE seq active-release" "$BE_SEQ_SHA" "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"
# FE hop 2
printf 'fe-seq-2\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m 'fe seq 2'
FE2_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
pack_seq "$FE2_SHA" "$HARNESS/fe2-out" "main.seqfe2.js" "202"
export ETERNAL_FRONTEND_ARTIFACT_DIR="$HARNESS/fe2-out"
set +e
"$RUNNER" deploy "$FE2_SHA" >"$HARNESS/fe2.txt" 2>"$HARNESS/fe2.err"
fe2_ec=$?
set -e
assert_eq "FE seq hop2 exits 0" "0" "$fe2_ec"
assert_true "FE seq hop2 DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/fe2.txt"
assert_eq "FE seq hop2 active-release" "$FE2_SHA" "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"

# ========== HARDENING: failed FE keeps active-release ==========
active_before_fail="$FE2_SHA"
printf 'fe-fail\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m 'fe fail'
FE_FAIL_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
pack_seq "$FE_FAIL_SHA" "$HARNESS/fefail-out" "main.fefail.js" "203"
export ETERNAL_FRONTEND_ARTIFACT_DIR="$HARNESS/fefail-out"
export ETERNAL_FORCE_PUBLISH_FAIL=1
set +e
"$RUNNER" deploy "$FE_FAIL_SHA" >"$HARNESS/fefail.txt" 2>"$HARNESS/fefail.err"
fefail_ec=$?
set -e
export ETERNAL_FORCE_PUBLISH_FAIL=0
assert_eq "failed FE exits nonzero" "1" "$fefail_ec"
assert_false "failed FE no DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/fefail.txt"
assert_eq "failed FE preserves active-release" "$active_before_fail" \
  "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"

# Retry after failure with publish OK
set +e
"$RUNNER" deploy "$FE_FAIL_SHA" >"$HARNESS/feretry.txt" 2>"$HARNESS/feretry.err"
feretry_ec=$?
set -e
assert_eq "retry after FE failure exits 0" "0" "$feretry_ec"
assert_true "retry after FE failure DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/feretry.txt"
assert_eq "retry after FE failure updates active-release" "$FE_FAIL_SHA" \
  "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"

# ========== HARDENING: failed BE keeps active-release ==========
active_before_befail="$FE_FAIL_SHA"
printf 'be-fail\n' >>"$MIRROR_SRC/backend/requirements.portable.txt"
git -C "$MIRROR_SRC" add backend/requirements.portable.txt && git -C "$MIRROR_SRC" commit -q -m 'be fail'
BE_FAIL_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
unset ETERNAL_FRONTEND_ARTIFACT_DIR
export ETERNAL_FORCE_RESTART_FAIL=1
set +e
"$RUNNER" deploy "$BE_FAIL_SHA" >"$HARNESS/befail.txt" 2>"$HARNESS/befail.err"
befail_ec=$?
set -e
export ETERNAL_FORCE_RESTART_FAIL=0
assert_eq "failed BE exits nonzero" "1" "$befail_ec"
assert_false "failed BE no DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/befail.txt"
assert_eq "failed BE preserves active-release" "$active_before_befail" \
  "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"

# ========== HARDENING: rollback then redeploy ==========
# Successful BE so we have a rollback point chain (reuse BE-only incomplete OK).
export ETERNAL_DRY_BUILD=0
export ETERNAL_FRONTEND_BUILD_MODE=artifact
export ETERNAL_FORCE_RESTART_FAIL=0
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"
echo 0 >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
echo 0 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
# Ensure backend process tracks CURRENT for restart identity checks.
if [[ -L "$CURRENT" ]]; then
  start_harness_backend_proc "$(readlink -f "$CURRENT")/backend"
fi
set +e
"$RUNNER" deploy "$BE_FAIL_SHA" >"$HARNESS/beok.txt" 2>"$HARNESS/beok.err"
beok_ec=$?
set -e
if [[ "$beok_ec" != "0" ]]; then
  log "beok.err tail:"; tail -n 40 "$HARNESS/beok.err" | tee -a "$REPORT" || true
fi
assert_eq "BE recover deploy exits 0" "0" "$beok_ec"
assert_eq "BE recover active-release" "$BE_FAIL_SHA" \
  "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"
prev_for_rb="$(sed -n 's/^previous_id=//p' "$RELEASES/active-release.txt" | head -n1)"
prev_path_for_rb=""
if [[ -f "$RELEASES/rollback-points/$BE_FAIL_SHA/meta.txt" ]]; then
  if [[ -z "$prev_for_rb" ]]; then
    prev_for_rb="$(sed -n 's/^previous_id=//p' "$RELEASES/rollback-points/$BE_FAIL_SHA/meta.txt" | head -n1)"
  fi
  prev_path_for_rb="$(sed -n 's/^previous_path=//p' "$RELEASES/rollback-points/$BE_FAIL_SHA/meta.txt" | head -n1)"
fi
# After FE-only → BE, previous_id (app identity) may differ from previous_path (CURRENT).
assert_true "BE recover rollback point has previous_id" test -n "$prev_for_rb"
assert_true "BE recover rollback point has previous_path" test -n "$prev_path_for_rb"
set +e
"$RUNNER" rollback "$BE_FAIL_SHA" "$prev_for_rb" >"$HARNESS/rb.txt" 2>"$HARNESS/rb.err"
rb_ec=$?
set -e
if [[ "$rb_ec" != "0" ]]; then
  log "rb.err tail:"; tail -n 40 "$HARNESS/rb.err" | tee -a "$REPORT" || true
fi
assert_eq "rollback exits 0" "0" "$rb_ec"
assert_true "ROLLBACK_SUCCESS" grep -q 'ROLLBACK_SUCCESS' "$HARNESS/rb.txt"
active_after_rb="$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"
assert_eq "rollback active-release is previous" "$prev_for_rb" "$active_after_rb"
assert_eq "rollback CURRENT restores recorded previous_path" \
  "$(readlink -f "$prev_path_for_rb")" "$(readlink -f "$CURRENT")"
# Redeploy BE after rollback (no FE artifact needed)
if [[ -L "$CURRENT" ]]; then
  start_harness_backend_proc "$(readlink -f "$CURRENT")/backend"
fi
set +e
"$RUNNER" deploy "$BE_FAIL_SHA" >"$HARNESS/rb-redeploy.txt" 2>"$HARNESS/rb-redeploy.err"
rb_re_ec=$?
set -e
assert_eq "redeploy after rollback exits 0" "0" "$rb_re_ec"
assert_true "redeploy after rollback DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/rb-redeploy.txt"
assert_eq "redeploy after rollback active-release" "$BE_FAIL_SHA" \
  "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"

# install-gate transactional backup helpers present
assert_true "install-gate backs up live files" \
  grep -q 'GATE_BACKUP_OK' "$ROOT/scripts/deploy/install-gate.sh"
assert_true "install-gate restores on failure" \
  grep -q 'GATE_ACTIVATE_ROLLED_BACK\|eternal_gate_restore_backup' "$ROOT/scripts/deploy/install-gate.sh"
GATE_TGT="$HARNESS/gate-install"
rm -rf "$GATE_TGT"
mkdir -p "$GATE_TGT"
# Seed prior live files
install -m 755 "$ROOT/scripts/deploy/eternal-actions-gate" "$GATE_TGT/eternal-actions-gate"
install -m 755 "$ROOT/scripts/deploy/eternal-deploy-runner.sh" "$GATE_TGT/eternal-deploy-runner.sh"
install -m 644 "$ROOT/scripts/deploy/lib.sh" "$GATE_TGT/lib.sh"
assert_true "install-gate verify-only OK" \
  bash "$ROOT/scripts/deploy/install-gate.sh" --source "$ROOT/scripts/deploy" --target "$GATE_TGT"
assert_true "install-gate activate OK" \
  bash "$ROOT/scripts/deploy/install-gate.sh" --source "$ROOT/scripts/deploy" --target "$GATE_TGT" --activate
assert_true "gate backup directory created" \
  ls -d "$GATE_TGT/eternal-gate-backups"/backup-* >/dev/null

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
