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
# Harness curl: supports -o BODY -w '%{http_code}' used by eternal_health_check.
out_file=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) out_file="${2:-}"; shift 2 ;;
    -w|--write-out) shift 2 ;;
    --max-time) shift 2 ;;
    -s|-S|-f|-sS) shift ;;
    *) shift ;;
  esac
done
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
cat >"$MOCK/workflow-runs.json" <<'EOF'
{"workflow_runs":[{"id":1,"status":"completed","conclusion":"success"}]}
EOF
cat >"$MOCK/check-runs.json" <<'EOF'
{"check_runs":[
 {"name":"Backend focused tests","status":"completed","conclusion":"success"},
 {"name":"Backend stock compatibility (replica set)","status":"completed","conclusion":"success"},
 {"name":"Frontend tests and build","status":"completed","conclusion":"success"}
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
cat >"$MOCK/check-runs.json" <<'EOF'
{"check_runs":[{"name":"Backend focused tests","status":"completed","conclusion":"success"}]}
EOF
assert_false "trigger rejects missing required checks" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" "$TRIGGER" plan deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA"
# restore checks
cat >"$MOCK/check-runs.json" <<'EOF'
{"check_runs":[
 {"name":"Backend focused tests","status":"completed","conclusion":"success"},
 {"name":"Backend stock compatibility (replica set)","status":"completed","conclusion":"success"},
 {"name":"Frontend tests and build","status":"completed","conclusion":"success"}
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
assert_true "js.map different-js error" grep -qiE 'sourcemap collision|without identical paired' <<<"$map_jsdiff_err"
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
assert_true "missing paired js mentioned" grep -qiE 'sourcemap collision|without identical paired' <<<"$map_miss_err"
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
assert_true "missing build paired js mentioned" grep -qiE 'sourcemap collision|without identical paired' <<<"$map_miss_src_err"
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
# 7) .css.map different is NOT granted the js.map exception
printf 'css-same\n' >"$MAP_ROOT/static/css/main.eeee.css"
printf 'cssmap-live\n' >"$MAP_ROOT/static/css/main.eeee.css.map"
printf 'css-same\n' >"$MAP_BUILD/static/css/main.eeee.css"
printf 'cssmap-build\n' >"$MAP_BUILD/static/css/main.eeee.css.map"
set +e
cssmap_err="$(eternal_webroot_install_file "$MAP_BUILD/static/css/main.eeee.css.map" "$MAP_ROOT" "static/css/main.eeee.css.map" 0 2>&1)"
cssmap_ec=$?
set -e
assert_eq "css.map collision still fail-closed" "1" "$cssmap_ec"
assert_true "css.map uses hashed collision path" grep -q 'hashed asset collision' <<<"$cssmap_err"
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

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
