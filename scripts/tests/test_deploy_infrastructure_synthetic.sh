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
{"name":"frontend","private":true,"scripts":{"build":"mkdir -p build/static/js && echo asset>build/static/js/app.js && echo built>build/index.html"}}
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
# Counted failures via state file: activate can fail, rollback health can recover.
if [[ -n "${ETERNAL_HEALTH_FAIL_STATE:-}" && -f "${ETERNAL_HEALTH_FAIL_STATE}" ]]; then
  n="$(cat "${ETERNAL_HEALTH_FAIL_STATE}")"
  if [[ "${n:-0}" -gt 0 ]]; then
    echo $((n - 1)) >"$ETERNAL_HEALTH_FAIL_STATE"
    echo '{"mongo":false,"mode":"degraded"}'
    exit 0
  fi
fi
if [[ "${ETERNAL_HEALTH_FORCE_FAIL:-0}" == "1" ]]; then echo '{"mongo":false,"mode":"degraded"}'; exit 0; fi
if [[ "${ETERNAL_HEALTH_PARTIAL:-0}" == "1" ]]; then echo '{"mongo":false,"mode":"degraded"}'; exit 0; fi
echo '{"mongo":true,"mode":"operational","checked_at":"t"}'
exit 0
EOF
chmod +x "$BIN/curl"
export ETERNAL_HEALTH_FAIL_STATE="$HARNESS/health-fail.remaining"
echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"

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

# ========== 403 no fallback / no double dispatch ==========
printf '403\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
: >"$HARNESS/dispatch.log"
assert_false "execute 403 stops without success" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" ETERNAL_TRIGGER_DISPATCH_LOG="$HARNESS/dispatch.log" \
      ETERNAL_DEPLOY_SHARED_SECRET='test-secret' \
      "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" --execute --request-id req-403
dispatch_count="$(grep -c '^POST ' "$HARNESS/dispatch.log" || true)"
assert_eq "403 path performs single POST attempt only" "1" "$dispatch_count"

# Ambiguous runs refuse re-dispatch
printf '403\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<EOF
{"workflow_runs":[
 {"id":1,"html_url":"https://example.test/1","created_at":"2030-01-01T00:00:00Z","display_title":"req-ambig"},
 {"id":2,"html_url":"https://example.test/2","created_at":"2030-01-01T00:00:01Z","display_title":"req-ambig"}
]}
EOF
assert_false "ambiguous runs refuse dispatch" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" ETERNAL_DEPLOY_SHARED_SECRET='test-secret' \
      "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" --execute --request-id req-ambig

# Successful correlate once
printf 'ok\n' >"$MOCK/dispatch-status"
cat >"$MOCK/trigger-runs.json" <<'EOF'
{"workflow_runs":[]}
EOF
cat >"$MOCK/trigger-runs-after.json" <<'EOF'
{"workflow_runs":[
 {"id":9,"html_url":"https://example.test/runs/9","created_at":"2030-01-01T00:00:00Z","display_title":"Deploy production req-ok"}
]}
EOF
ok_disp="$(ETERNAL_GH_API_MOCK_DIR="$MOCK" ETERNAL_TRIGGER_DISPATCH_LOG="$HARNESS/dispatch.log" \
  ETERNAL_DEPLOY_SHARED_SECRET='test-secret-VALUE-NEVER-LOG' \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" --execute --request-id req-ok 2>"$HARNESS/okdisp.err")"
assert_true "dispatch OK returns run URL" grep -q 'run_url=https://example.test/runs/9' <<<"$ok_disp"
assert_true "dispatch uses stdin-json (no secret argv)" grep -q 'secret_in_argv=no' "$HARNESS/dispatch.log"
assert_false "dispatch log never contains shared secret" grep -q 'test-secret-VALUE-NEVER-LOG' "$HARNESS/dispatch.log"
assert_false "dispatch stdout/err never contains shared secret" \
  grep -q 'test-secret-VALUE-NEVER-LOG' <<<"$ok_disp$(cat "$HARNESS/okdisp.err" 2>/dev/null || true)"

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

# Finalize existing: no venv recreate; markers only after success
rm -rf "$BASELINE"
make_verified_baseline
# Strip requirements provenance to simulate legacy VPS baseline
grep -vE '^(requirements_file|requirements_sha256)=' "$BASELINE/.baseline-verified" >"$BASELINE/.baseline-verified.tmp"
mv "$BASELINE/.baseline-verified.tmp" "$BASELINE/.baseline-verified"
rm -f "$BASELINE/.baseline" "$BASELINE/.deploy-ok"
venv_inode_before="$(stat -c '%i' "$BASELINE/tree/backend/.venv" 2>/dev/null || stat -f '%i' "$BASELINE/tree/backend/.venv")"
fin_dry="$(ETERNAL_RUNTIME_MODE=stub ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
  ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
  "$FINALIZE_BASELINE" 2>"$HARNESS/fin-dry.err" || true)"
assert_true "finalize dry-run needs apply for legacy" grep -q 'FINALIZE_DRY_RUN_NEEDS_APPLY' <<<"$fin_dry"
assert_false "finalize dry-run does not write .baseline" test -f "$BASELINE/.baseline"
ETERNAL_RUNTIME_MODE=stub ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
  ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
  "$FINALIZE_BASELINE" --apply >"$HARNESS/fin.txt" 2>"$HARNESS/fin.err"
assert_true "finalize apply OK" grep -q 'BASELINE_FINALIZE_OK' "$HARNESS/fin.txt"
assert_true "finalize wrote .baseline marker" test -f "$BASELINE/.baseline"
assert_true "finalize wrote requirements_file portable" \
  grep -q '^requirements_file=requirements.portable.txt' "$BASELINE/.baseline-verified"
venv_inode_after="$(stat -c '%i' "$BASELINE/tree/backend/.venv" 2>/dev/null || stat -f '%i' "$BASELINE/tree/backend/.venv")"
assert_eq "finalize did not recreate .venv" "$venv_inode_before" "$venv_inode_after"
# Validation failure must not leave success markers when forced to fail mid-finalize:
rm -f "$BASELINE/.baseline" "$BASELINE/.deploy-ok"
chmod a-r "$BASELINE/tree/backend/requirements.portable.txt"
assert_false "finalize apply fails closed without markers when req unreadable" \
  env ETERNAL_RUNTIME_MODE=stub ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" \
      ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_RUNTIME_ROOT="$RUNTIME" \
      "$FINALIZE_BASELINE" --apply
chmod u+r "$BASELINE/tree/backend/requirements.portable.txt"
assert_false "failed finalize left no .baseline success marker" test -f "$BASELINE/.baseline"

assert_true "docs mention requirements.portable default" \
  grep -q 'requirements.portable.txt' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs mention finalize-existing-baseline" \
  grep -q 'finalize-existing-baseline.sh' "$ROOT/docs/deploy-production-setup.md"
assert_true "deploy workflow has jobs:" grep -q '^jobs:' "$WF"
assert_true "probe workflow has jobs:" grep -q '^jobs:' "$PWF"
assert_true "deploy workflow on.workflow_dispatch" grep -q 'workflow_dispatch:' "$WF"
assert_true "probe workflow on.workflow_dispatch" grep -q 'workflow_dispatch:' "$PWF"

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
