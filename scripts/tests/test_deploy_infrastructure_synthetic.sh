#!/usr/bin/env bash
# Synthetic fail-closed proofs for deploy infra (no real VPS / no real dispatch).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$ROOT/scripts/deploy/eternal-actions-gate"
RUNNER="$ROOT/scripts/deploy/eternal-deploy-runner.sh"
TRIGGER="$ROOT/scripts/deploy/trigger_github_actions.sh"
PROBE_SH="$ROOT/scripts/deploy/ssh_gate_probe.sh"
PREPARE_BASELINE="$ROOT/scripts/deploy/prepare-verified-baseline.sh"
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
trap 'rm -rf "$WORKDIR"' EXIT
BIN="$WORKDIR/bin"
HARNESS="$WORKDIR/harness"
MIRROR_SRC="$WORKDIR/repo"
RELEASES="$HARNESS/releases"
FRONTEND="$HARNESS/www"
LEGACY="$HARNESS/legacy-dirty"
CURRENT="$HARNESS/current"
LOCK="$HARNESS/deploy.lock"
BASELINE="$RELEASES/baseline-active"
mkdir -p "$BIN" "$RELEASES" "$FRONTEND/live" "$LEGACY/backend"

# --- fixture repo ---
git init -q -b main "$MIRROR_SRC"
git -C "$MIRROR_SRC" config user.email "synthetic@example.com"
git -C "$MIRROR_SRC" config user.name "Synthetic"
mkdir -p "$MIRROR_SRC/backend" "$MIRROR_SRC/frontend/src" "$MIRROR_SRC/scripts/deploy"
printf 'ok\n' >"$MIRROR_SRC/backend/requirements.txt"
cat >"$MIRROR_SRC/frontend/package.json" <<'EOF'
{"name":"frontend","private":true,"scripts":{"build":"mkdir -p build/static/js && echo asset>build/static/js/app.js && echo built>build/index.html"}}
EOF
printf 'yarn lockfile v1\n' >"$MIRROR_SRC/frontend/yarn.lock"
printf 'app\n' >"$MIRROR_SRC/frontend/src/index.js"
printf 'dirty\n' >"$LEGACY/DO_NOT_TOUCH"
printf 'legacy\n' >"$LEGACY/backend/app.py"
cp "$GATE" "$RUNNER" "$ROOT/scripts/deploy/lib.sh" "$MIRROR_SRC/scripts/deploy/"
git -C "$MIRROR_SRC" add . && git -C "$MIRROR_SRC" commit -q -m base
printf 'ok\n#pin\n' >"$MIRROR_SRC/backend/requirements.txt"
git -C "$MIRROR_SRC" add backend/requirements.txt && git -C "$MIRROR_SRC" commit -q -m 'backend bump'
MAIN_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git -C "$MIRROR_SRC" checkout -q -b feature/unmerged
printf 'x\n' >"$MIRROR_SRC/backend/feature.txt"
git -C "$MIRROR_SRC" add backend/feature.txt && git -C "$MIRROR_SRC" commit -q -m unmerged
UNMERGED_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git -C "$MIRROR_SRC" checkout -q main
git clone -q --mirror "$MIRROR_SRC" "$RELEASES/mirror.git"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/*:refs/heads/*'

EXPECTED_WD="$CURRENT/backend"
cat >"$BIN/systemctl" <<EOF
#!/usr/bin/env bash
echo "systemctl \$*" >>"\${ETERNAL_SYSTEMCTL_LOG}"
case "\$1" in
  show)
    if [[ "\$*" == *WorkingDirectory* ]]; then printf '%s\n' "$EXPECTED_WD"; exit 0; fi
    if [[ "\$*" == *ExecStart* ]]; then printf '%s\n' "$EXPECTED_WD/.venv/bin/uvicorn server:app"; exit 0; fi
    if [[ "\$*" == *MainPID* ]]; then printf '0\n'; exit 0; fi
    ;;
  is-active) exit 0 ;;
  restart)
    if [[ "\${ETERNAL_FORCE_RESTART_FAIL:-0}" == "1" ]]; then exit 1; fi
    exit 0
    ;;
esac
exit 0
EOF
chmod +x "$BIN/systemctl"

cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
if [[ "${ETERNAL_HEALTH_FORCE_FAIL:-0}" == "1" ]]; then echo '{"mongo":false,"mode":"degraded"}'; exit 0; fi
if [[ "${ETERNAL_HEALTH_PARTIAL:-0}" == "1" ]]; then echo '{"mongo":false,"mode":"degraded"}'; exit 0; fi
echo '{"mongo":true,"mode":"operational","checked_at":"t"}'
exit 0
EOF
chmod +x "$BIN/curl"

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
export ETERNAL_RELEASES_ROOT="$RELEASES"
export ETERNAL_CURRENT_LINK="$CURRENT"
export ETERNAL_LEGACY_ROOT="$LEGACY"
export ETERNAL_FRONTEND_ROOT="$FRONTEND/live"
export ETERNAL_FRONTEND_PREV="$FRONTEND/prev"
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
export ETERNAL_SKIP_PROCCWD_CHECK=1
export ETERNAL_REQUIRE_VERIFIED_BASELINE=1
export ETERNAL_MIN_DISK_KB=1
: >"$ETERNAL_SYSTEMCTL_LOG"
printf 'old\n' >"$FRONTEND/live/index.html"
printf 'old-asset\n' >"$FRONTEND/live/static/js/old.js" 2>/dev/null || mkdir -p "$FRONTEND/live/static/js" && printf 'old-asset\n' >"$FRONTEND/live/static/js/old.js"
printf 'active\n' >"$FRONTEND/live/active.txt"

# shellcheck disable=SC1091
source "$ROOT/scripts/deploy/lib.sh"

make_verified_baseline() {
  mkdir -p "$BASELINE/tree/backend" "$BASELINE/frontend-snapshot/static/js"
  cp -a "$LEGACY/backend"/. "$BASELINE/tree/backend"/
  cp -a "$FRONTEND/live"/. "$BASELINE/frontend-snapshot"/
  {
    printf 'kind=verified-baseline\nsha=baseline-active\nimmutable=1\n'
  } >"$BASELINE/.deploy-ok"
  : >"$BASELINE/.baseline"
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
ok_disp="$(ETERNAL_GH_API_MOCK_DIR="$MOCK" ETERNAL_DEPLOY_SHARED_SECRET='test-secret' \
  "$TRIGGER" execute deploy --sha "$MAIN_SHA" --confirm-sha "$MAIN_SHA" --execute --request-id req-ok 2>"$HARNESS/okdisp.err")"
assert_true "dispatch OK returns run URL" grep -q 'run_url=https://example.test/runs/9' <<<"$ok_disp"

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
# tampered baseline content after integrity write
rm -rf "$BASELINE"
make_verified_baseline
printf 'tampered\n' >>"$BASELINE/tree/backend/app.py"
assert_false "tampered baseline digests refused" "$RUNNER" deploy "$MAIN_SHA"
# wrong systemd WD (retarget not done yet) — baseline ready but service not retargeted
rm -rf "$BASELINE"
make_verified_baseline
export ETERNAL_EXPECTED_WD="/opt/eternal-packaging-current/backend"
assert_false "systemd WD mismatch blocks preflight before activation" "$RUNNER" deploy "$MAIN_SHA"
export ETERNAL_EXPECTED_WD="$EXPECTED_WD"

# ========== first deploy with verified baseline ==========
make_verified_baseline
: >"$ETERNAL_SYSTEMCTL_LOG"
if "$RUNNER" deploy "$MAIN_SHA" >"$HARNESS/ok.txt" 2>"$HARNESS/ok.err"; then
  log "PASS: valid sha deploy exit 0"; PASS=$((PASS+1))
else
  log "FAIL: valid sha deploy"; cat "$HARNESS/ok.err" >>"$REPORT"; FAIL=$((FAIL+1))
fi
assert_true "DEPLOY_OK printed" grep -q "DEPLOY_OK sha=$MAIN_SHA" "$HARNESS/ok.txt"
assert_true "legacy overlay untouched" grep -q dirty "$LEGACY/DO_NOT_TOUCH"
assert_true "old hashed asset retained after publish" test -f "$FRONTEND/live/static/js/old.js"
assert_true "new index published" grep -q dry-build "$FRONTEND/live/index.html"

# ========== health degraded rejected ==========
export ETERNAL_HEALTH_PARTIAL=1
assert_false "degraded health rejected" "$RUNNER" deploy "$MAIN_SHA"
export ETERNAL_HEALTH_PARTIAL=0

# ========== old health must not pass wrong runtime ==========
# Force runtime verify fail by pointing expected WD elsewhere after a FE-only change
git -C "$MIRROR_SRC" checkout -q main
printf 'fe2\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m fe2
FE_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
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
export ETERNAL_HEALTH_FORCE_FAIL=1
set +e
"$RUNNER" deploy "$BE2" >"$HARNESS/hf.txt" 2>"$HARNESS/hf.err"
hf=$?
set -e
export ETERNAL_HEALTH_FORCE_FAIL=0
assert_eq "health fail nonzero" "1" "$hf"
assert_true "rollback while holding lock logged" grep -q 'attempting rollback while holding lock' "$HARNESS/hf.err"
assert_false "lock released after cleanup" test -f "$LOCK"

# ========== partial restart failure triggers recovery ==========
rm -rf "$RELEASES/$BE2" # prior health-fail left incomplete release without .deploy-ok
export ETERNAL_FORCE_RESTART_FAIL=1
set +e
"$RUNNER" deploy "$BE2" >"$HARNESS/rs.txt" 2>"$HARNESS/rs.err"
rs=$?
set -e
export ETERNAL_FORCE_RESTART_FAIL=0
assert_eq "restart fail nonzero" "1" "$rs"
assert_true "rollback restart attempted after partial failure" grep -q 'rollback: restart backend' "$HARNESS/rs.err"

# ========== publish keeps old assets + atomic index ==========
"$RUNNER" deploy "$FE_SHA" >"$HARNESS/feok.txt" 2>"$HARNESS/feok.err" || true
assert_true "old asset still present after later FE publish" test -f "$FRONTEND/live/static/js/old.js"
assert_true "index is not .new leftover" bash -c '! test -f "'"$FRONTEND"'/live/index.html.new"'

# ========== lock contention ==========
printf 'x\n' >"$LOCK"
assert_false "deploy lock blocks" "$RUNNER" deploy "$FE_SHA"
rm -f "$LOCK"

# ========== secret masking ==========
FAKE=$'-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEYMATERIAL1234567890\n-----END OPENSSH PRIVATE KEY-----'
red="$(eternal_redact_line "key=$FAKE")"
assert_true "redactor masks key" grep -q REDACTED_PRIVATE_KEY <<<"$red"
assert_false "redactor strips material" grep -q SECRETKEYMATERIAL1234567890 <<<"$red"

# ========== workflow shape ==========
WF="$ROOT/.github/workflows/deploy-production.yml"
PWF="$ROOT/.github/workflows/deploy-probe.yml"
assert_true "deploy workflow_dispatch" grep -q workflow_dispatch "$WF"
assert_true "deploy uses production env" grep -q 'environment: production' "$WF"
assert_true "deploy_shared_secret input" grep -q deploy_shared_secret "$WF"
assert_true "probe workflow exists" test -f "$PWF"
assert_true "probe workflow_dispatch" grep -q workflow_dispatch "$PWF"
assert_true "probe uses ssh_gate_probe" grep -q ssh_gate_probe.sh "$PWF"
assert_false "probe workflow steps do not invoke runner script path" \
  grep -q 'scripts/deploy/eternal-deploy-runner' "$PWF"
assert_true "probe workflow only references ssh_gate_probe helper" grep -q 'ssh_gate_probe.sh' "$PWF"
# Ensure run step invokes only the probe helper (not a deploy runner binary).
assert_true "probe run step calls ssh_gate_probe.sh" \
  grep -A2 'Probe forced-command gate' "$PWF" | grep -q 'ssh_gate_probe.sh'
assert_true "docs mention required reviewers" grep -q 'Required reviewers' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs say technical secret does not replace reviewers" \
  grep -qiE 'does \*\*not\*\* replace|NOT replace' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs order baseline before retarget" \
  grep -q 'baseline + runtime dirs before service retarget' "$ROOT/docs/deploy-production-setup.md"
assert_true "docs mention Cursor token store" grep -q 'ETERNAL_GH_TOKEN' "$ROOT/docs/deploy-production-setup.md"
assert_true "prepare-verified-baseline exists" test -x "$PREPARE_BASELINE"
assert_true "deploy workflow notes technical gate != human approval" \
  grep -q 'does NOT replace Environment Required reviewers' "$WF"
assert_true "probe workflow asserts PROBE_NO_DEPLOY" grep -q 'PROBE_NO_DEPLOY=1' "$PWF"

# syntax checks (no new deps)
assert_true "bash -n runner" bash -n "$RUNNER"
assert_true "bash -n trigger" bash -n "$TRIGGER"
assert_true "bash -n probe" bash -n "$PROBE_SH"
assert_true "bash -n gate" bash -n "$GATE"
assert_true "deploy workflow has jobs:" grep -q '^jobs:' "$WF"
assert_true "probe workflow has jobs:" grep -q '^jobs:' "$PWF"
assert_true "deploy workflow on.workflow_dispatch" grep -q 'workflow_dispatch:' "$WF"
assert_true "probe workflow on.workflow_dispatch" grep -q 'workflow_dispatch:' "$PWF"

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
