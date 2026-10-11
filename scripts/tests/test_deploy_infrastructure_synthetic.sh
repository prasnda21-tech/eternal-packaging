#!/usr/bin/env bash
# Synthetic fail-closed proofs for deploy gate + runner (no real VPS / secrets).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$ROOT/scripts/deploy/eternal-actions-gate"
RUNNER="$ROOT/scripts/deploy/eternal-deploy-runner.sh"
INSTALL_GATE="$ROOT/scripts/deploy/install-gate.sh"
BOOTSTRAP="$ROOT/scripts/deploy/bootstrap-permissions.sh"
VERIFY_CI="$ROOT/scripts/deploy/verify_ci_for_sha.sh"
PASS=0
FAIL=0
REPORT="${ETERNAL_TEST_REPORT:-/opt/cursor/artifacts/deploy-infra-synthetic.log}"
mkdir -p "$(dirname "$REPORT")"
: >"$REPORT"

log() { printf '%s\n' "$*" | tee -a "$REPORT"; }

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    log "PASS: $label"
    PASS=$((PASS + 1))
  else
    log "FAIL: $label expected=[$expected] actual=[$actual]"
    FAIL=$((FAIL + 1))
  fi
}

assert_true() {
  local label="$1"
  shift
  if "$@"; then
    log "PASS: $label"
    PASS=$((PASS + 1))
  else
    log "FAIL: $label"
    FAIL=$((FAIL + 1))
  fi
}

assert_false() {
  local label="$1"
  shift
  if "$@"; then
    log "FAIL: $label (expected failure)"
    FAIL=$((FAIL + 1))
  else
    log "PASS: $label"
    PASS=$((PASS + 1))
  fi
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
mkdir -p "$BIN" "$RELEASES" "$FRONTEND" "$LEGACY/backend" "$HARNESS/prev"

# --- fixture git repo with main + unmerged branch ---
git init -q -b main "$MIRROR_SRC"
git -C "$MIRROR_SRC" config user.email "synthetic@example.com"
git -C "$MIRROR_SRC" config user.name "Synthetic"
mkdir -p "$MIRROR_SRC/backend" "$MIRROR_SRC/frontend/src" "$MIRROR_SRC/scripts/deploy"
printf 'ok\n' >"$MIRROR_SRC/backend/requirements.txt"
cat >"$MIRROR_SRC/frontend/package.json" <<'EOF'
{
  "name": "frontend",
  "private": true,
  "scripts": {
    "build": "mkdir -p build && echo built > build/index.html"
  }
}
EOF
printf 'yarn lockfile v1\n' >"$MIRROR_SRC/frontend/yarn.lock"
printf 'console.log("app")\n' >"$MIRROR_SRC/frontend/src/index.js"
printf 'dirty-local\n' >"$LEGACY/DO_NOT_TOUCH"
printf 'legacy-backend\n' >"$LEGACY/backend/app.py"
cp "$GATE" "$RUNNER" "$ROOT/scripts/deploy/lib.sh" "$MIRROR_SRC/scripts/deploy/"
git -C "$MIRROR_SRC" add .
git -C "$MIRROR_SRC" commit -q -m "base"
BASE_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"

printf 'ok\n# pin\n' >"$MIRROR_SRC/backend/requirements.txt"
git -C "$MIRROR_SRC" add backend/requirements.txt
git -C "$MIRROR_SRC" commit -q -m "backend bump"
MAIN_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"

git -C "$MIRROR_SRC" checkout -q -b feature/unmerged
printf 'secret-feature\n' >"$MIRROR_SRC/backend/feature.txt"
git -C "$MIRROR_SRC" add backend/feature.txt
git -C "$MIRROR_SRC" commit -q -m "unmerged"
UNMERGED_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git -C "$MIRROR_SRC" checkout -q main

git -C "$MIRROR_SRC" checkout -q -b tmp/migration
mkdir -p "$MIRROR_SRC/backend/scripts"
printf 'print("migrate")\n' >"$MIRROR_SRC/backend/scripts/migrate_storage_dryrun.py"
git -C "$MIRROR_SRC" add backend/scripts/migrate_storage_dryrun.py
git -C "$MIRROR_SRC" commit -q -m "migration"
MIGRATION_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git -C "$MIRROR_SRC" checkout -q main

git clone -q --mirror "$MIRROR_SRC" "$RELEASES/mirror.git"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/*:refs/heads/*'

cat >"$BIN/systemctl" <<EOF
#!/usr/bin/env bash
echo "systemctl \$*" >>"\${ETERNAL_SYSTEMCTL_LOG}"
if [[ "\$1" == "show" ]]; then
  printf '%s\n' "${LEGACY}/backend"
  exit 0
fi
exit 0
EOF
chmod +x "$BIN/systemctl"

cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
if [[ "${ETERNAL_HEALTH_FORCE_FAIL:-0}" == "1" ]]; then
  echo '{"mongo":false,"mode":"degraded"}'
  exit 0
fi
if [[ "${ETERNAL_HEALTH_FORCE_HTTP_FAIL:-0}" == "1" ]]; then
  exit 22
fi
if [[ "${ETERNAL_HEALTH_PARTIAL:-0}" == "1" ]]; then
  # HTTP 200 but not operational — must FAIL health gate
  echo '{"mongo":false,"mode":"degraded"}'
  exit 0
fi
echo '{"mongo":true,"mode":"operational","checked_at":"2026-01-01T00:00:00Z"}'
exit 0
EOF
chmod +x "$BIN/curl"

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
export ETERNAL_BASELINE_DIR="$RELEASES/baseline-active"
: >"$ETERNAL_SYSTEMCTL_LOG"
mkdir -p "$FRONTEND/live"
printf 'old-frontend\n' >"$FRONTEND/live/index.html"
printf 'active-marker\n' >"$FRONTEND/live/active.txt"

# shellcheck disable=SC1091
source "$ROOT/scripts/deploy/lib.sh"

run_gate() {
  SSH_ORIGINAL_COMMAND="$*" "$GATE"
}

# ========== 1) probe canonical + accepted patterns ==========
probe_out="$(run_gate probe)"
assert_true "gate probe canonical ETERNAL_GATE_OK" grep -qE 'ETERNAL_GATE_OK[[:space:]]+probe=pass' <<<"$probe_out"
assert_true "gate probe legacy PASS probe line" grep -q 'PASS probe' <<<"$probe_out"
assert_true "lib accepts canonical probe" eternal_probe_output_accepted "$probe_out"
assert_true "lib accepts legacy PASS probe" eternal_probe_output_accepted $'PASS probe\n'
assert_true "lib accepts OK" eternal_probe_output_accepted $'OK\n'
assert_false "lib rejects garbage probe" eternal_probe_output_accepted $'nope\n'

# ========== 2) install-gate verified staging ==========
GATE_HOME="$HARNESS/gate-home"
mkdir -p "$GATE_HOME"
stage_out="$("$INSTALL_GATE" --source "$ROOT/scripts/deploy" --target "$GATE_HOME" 2>"$HARNESS/install.err")"
assert_true "install-gate verify OK" grep -q 'GATE_VERIFY_OK' <<<"$stage_out"
assert_false "install-gate without activate leaves live gate absent" test -f "$GATE_HOME/eternal-actions-gate"
act_out="$("$INSTALL_GATE" --source "$ROOT/scripts/deploy" --target "$GATE_HOME" --activate 2>"$HARNESS/install-act.err")"
assert_true "install-gate activate OK" grep -q 'GATE_ACTIVATE_OK' <<<"$act_out"
assert_true "activated gate probe accepted" \
  bash -c "SSH_ORIGINAL_COMMAND=probe '$GATE_HOME/eternal-actions-gate' | grep -q ETERNAL_GATE_OK"

# ========== 3) invalid commands rejected ==========
assert_false "gate rejects arbitrary shell" run_gate 'bash -lc id'
assert_false "gate rejects path" run_gate 'cat /etc/passwd'
assert_false "gate rejects metacharacters" run_gate 'deploy $(id)'
assert_false "gate rejects short sha" run_gate "deploy ${MAIN_SHA:0:7}"

# ========== 4) invalid / unmerged SHA ==========
assert_false "runner rejects invalid sha" \
  "$RUNNER" deploy 'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz'
assert_false "runner rejects unmerged sha" \
  "$RUNNER" deploy "$UNMERGED_SHA"

# ========== 5) first deploy baseline + valid SHA ==========
: >"$ETERNAL_SYSTEMCTL_LOG"
# No CURRENT yet — first cutover must capture baseline of active FE/BE.
rm -f "$CURRENT"
out_ok="$HARNESS/ok.txt"
if "$RUNNER" deploy "$MAIN_SHA" >"$out_ok" 2>"$HARNESS/ok.err"; then
  log "PASS: valid sha deploy exit 0"
  PASS=$((PASS + 1))
else
  log "FAIL: valid sha deploy exit nonzero"
  cat "$HARNESS/ok.err" >>"$REPORT" || true
  FAIL=$((FAIL + 1))
fi
assert_true "valid deploy prints DEPLOY_OK" grep -q "DEPLOY_OK sha=$MAIN_SHA" "$out_ok"
assert_true "current symlink points at release" \
  bash -c "[[ \"\$(readlink -f \"$CURRENT\")\" == \"$(readlink -f "$RELEASES/$MAIN_SHA")\" ]]"
assert_true "legacy dirty tree untouched" grep -q 'dirty-local' "$LEGACY/DO_NOT_TOUCH"
assert_true "baseline marker captured" test -f "$ETERNAL_BASELINE_DIR/.baseline"
assert_true "baseline frontend snapshot kept" test -f "$ETERNAL_BASELINE_DIR/frontend-snapshot/active.txt"
assert_true "deploy-ok marker written" test -f "$RELEASES/$MAIN_SHA/.deploy-ok"

# ========== 6) health requires mongo=true mode=operational ==========
assert_true "health body ok helper accepts operational" \
  eternal_health_body_ok '{"mongo":true,"mode":"operational"}'
assert_false "health body ok rejects degraded" \
  eternal_health_body_ok '{"mongo":false,"mode":"degraded"}'
export ETERNAL_HEALTH_PARTIAL=1
assert_false "deploy fails when health JSON not operational" \
  "$RUNNER" deploy "$MAIN_SHA"
export ETERNAL_HEALTH_PARTIAL=0

# ========== 7) frontend-only skips backend restart; FE after health ==========
git -C "$MIRROR_SRC" checkout -q main
printf 'frontend-only\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js
git -C "$MIRROR_SRC" commit -q -m "frontend only"
FE_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
: >"$ETERNAL_SYSTEMCTL_LOG"
# Ensure healthy current release pointer
ln -sfn "$RELEASES/$MAIN_SHA" "$CURRENT"
printf 'sha=%s\n' "$MAIN_SHA" >"$RELEASES/$MAIN_SHA/.deploy-ok"
"$RUNNER" deploy "$FE_SHA" >"$HARNESS/fe.txt" 2>"$HARNESS/fe.err"
assert_eq "frontend-only skips backend restart" "" "$(cat "$ETERNAL_SYSTEMCTL_LOG")"
assert_true "frontend-only still DEPLOY_OK" grep -q "DEPLOY_OK sha=$FE_SHA" "$HARNESS/fe.txt"

# ========== 8) FE not published before backend health (health fail path) ==========
git -C "$MIRROR_SRC" checkout -q main
printf 'be-for-health\n' >"$MIRROR_SRC/backend/requirements.txt"
git -C "$MIRROR_SRC" add backend/requirements.txt
git -C "$MIRROR_SRC" commit -q -m "backend for health-order"
BE_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
printf 'pre-health-fe\n' >"$FRONTEND/live/keep-order.txt"
export ETERNAL_HEALTH_FORCE_FAIL=1
set +e
"$RUNNER" deploy "$BE_SHA" >"$HARNESS/order.txt" 2>"$HARNESS/order.err"
order_ec=$?
set -e
export ETERNAL_HEALTH_FORCE_FAIL=0
assert_eq "health-fail before FE publish exits nonzero" "1" "$order_ec"
assert_true "frontend unchanged when health fails pre-publish" test -f "$FRONTEND/live/keep-order.txt"
assert_true "health-fail log says frontend not published" \
  grep -q 'frontend not published' "$HARNESS/order.err"

# ========== 9) first-deploy rollback restores baseline FE + backend target ==========
# Reset to no CURRENT (simulate first cutover failure after switch)
rm -f "$LOCK" "$CURRENT"
rm -rf "$RELEASES/$BE_SHA"
printf 'baseline-live\n' >"$FRONTEND/live/index.html"
printf 'baseline-live\n' >"$FRONTEND/live/active.txt"
export ETERNAL_HEALTH_FORCE_FAIL=1
set +e
"$RUNNER" deploy "$BE_SHA" >"$HARNESS/firstfail.txt" 2>"$HARNESS/firstfail.err"
ff_ec=$?
set -e
export ETERNAL_HEALTH_FORCE_FAIL=0
assert_eq "first-deploy health failure exits nonzero" "1" "$ff_ec"
assert_true "first-deploy rollback restores baseline frontend content" \
  grep -q 'baseline-live' "$FRONTEND/live/active.txt"
assert_true "first-deploy rollback points current at baseline tree" \
  bash -c "[[ \"\$(readlink -f \"$CURRENT\")\" == \"$(readlink -f "$ETERNAL_BASELINE_DIR/tree")\" ]]"

# ========== 10) publish failure rolls back frontend ==========
rm -f "$LOCK"
# Establish good release at FE_SHA
export ETERNAL_HEALTH_FORCE_FAIL=0
"$RUNNER" deploy "$FE_SHA" >"$HARNESS/refe.txt" 2>"$HARNESS/refe.err" || true
printf 'keep-pub\n' >"$FRONTEND/live/keep-pub.txt"
PREV_LINK="$(readlink -f "$CURRENT")"
export ETERNAL_FORCE_PUBLISH_FAIL=1
# Need a newer SHA with frontend change so publish is attempted
git -C "$MIRROR_SRC" checkout -q main
printf 'pub-fail\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js
git -C "$MIRROR_SRC" commit -q -m "pub fail candidate"
PUB_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
set +e
"$RUNNER" deploy "$PUB_SHA" >"$HARNESS/pub.txt" 2>"$HARNESS/pub.err"
pub_ec=$?
set -e
export ETERNAL_FORCE_PUBLISH_FAIL=0
assert_eq "publish failure exits nonzero" "1" "$pub_ec"
assert_true "publish failure restores previous frontend file" test -f "$FRONTEND/live/keep-pub.txt"

# ========== 11) deploy lock ==========
printf 'other\n' >"$LOCK"
assert_false "deploy lock blocks second deployer" "$RUNNER" deploy "$FE_SHA"
rm -f "$LOCK"

# ========== 12) local conflict ==========
git -C "$MIRROR_SRC" checkout -q main
printf 'conflict-trigger\n' >"$MIRROR_SRC/backend/requirements.txt"
git -C "$MIRROR_SRC" add backend/requirements.txt
git -C "$MIRROR_SRC" commit -q -m "conflict candidate"
CONFLICT_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
mkdir -p "$RELEASES/$CONFLICT_SHA"
printf 'partial\n' >"$RELEASES/$CONFLICT_SHA/junk"
assert_false "incomplete release dir is local conflict" "$RUNNER" deploy "$CONFLICT_SHA"
rm -rf "$RELEASES/$CONFLICT_SHA"
touch "$FRONTEND/live/.deploy-in-progress"
assert_false "frontend in-progress marker is local conflict" "$RUNNER" deploy "$CONFLICT_SHA"
rm -f "$FRONTEND/live/.deploy-in-progress"

# ========== 13) migration guard ==========
git -C "$MIRROR_SRC" checkout -q main
git -C "$MIRROR_SRC" merge -q --no-ff "$MIGRATION_SHA" -m "merge migration"
MIG_MAIN="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
rm -f "$LOCK"
ln -sfn "$RELEASES/$FE_SHA" "$CURRENT"
printf 'sha=%s\n' "$FE_SHA" >"$RELEASES/$FE_SHA/.deploy-ok"
assert_false "unapproved migration paths refused" \
  env ETERNAL_ALLOW_MIGRATIONS=0 "$RUNNER" deploy "$MIG_MAIN"

# ========== 14) secret masking ==========
FAKE_KEY=$'-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEYMATERIAL1234567890\n-----END OPENSSH PRIVATE KEY-----'
redacted="$(eternal_redact_line "key=$FAKE_KEY")"
assert_true "redactor masks private key block" grep -q 'REDACTED_PRIVATE_KEY' <<<"$redacted"
assert_false "redactor output lacks raw key material" grep -q 'SECRETKEYMATERIAL1234567890' <<<"$redacted"
export DEPLOY_SSH_KEY="$FAKE_KEY"
set +e
SSH_ORIGINAL_COMMAND="deploy $FE_SHA" "$GATE" >"$HARNESS/mask.out" 2>"$HARNESS/mask.err"
set -e
assert_false "gate/runner logs do not leak private key" \
  grep -q 'SECRETKEYMATERIAL1234567890' "$HARNESS/mask.out" "$HARNESS/mask.err"

# ========== 15) bootstrap least privilege (dry-run) ==========
boot_out="$(ETERNAL_SUDOERS_PATH="$HARNESS/sudoers" ETERNAL_BOOTSTRAP_DEPLOY_USER=deploy \
  "$BOOTSTRAP" 2>"$HARNESS/boot.err")"
assert_true "bootstrap dry-run prints sudoers alias" grep -q 'ETERNAL_PACKAGING_SYSTEMCTL' <<<"$boot_out"
assert_false "bootstrap sudoers rule is not NOPASSWD:ALL" \
  grep -qE 'NOPASSWD:[[:space:]]*ALL([[:space:]]|$)' <<<"$boot_out"
assert_true "bootstrap sudoers scopes systemctl alias only" \
  grep -qE 'NOPASSWD:[[:space:]]*ETERNAL_PACKAGING_SYSTEMCTL' <<<"$boot_out"
assert_true "bootstrap mentions releases root" grep -q 'releases_root=' <<<"$boot_out"

# ========== 16) systemd unit uses current symlink + legacy EnvironmentFile ==========
UNIT="$ROOT/scripts/deploy/systemd/eternal-packaging-backend.service"
assert_true "unit WorkingDirectory uses current symlink" \
  grep -q 'WorkingDirectory=/opt/eternal-packaging-current/backend' "$UNIT"
assert_true "unit EnvironmentFile keeps legacy env" \
  grep -q 'EnvironmentFile=-/opt/eternal-packaging/backend/.env' "$UNIT"
assert_true "unit ExecStart uses current symlink" \
  grep -q 'ExecStart=/opt/eternal-packaging-current/backend' "$UNIT"

# ========== 17) explicit CI verification ==========
MOCK="$HARNESS/gh-mock"
mkdir -p "$MOCK"
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
ci_ok="$(ETERNAL_GH_API_MOCK_DIR="$MOCK" "$VERIFY_CI" "prasnda21-tech/eternal-packaging" "$MAIN_SHA" 2>"$HARNESS/ci.err")"
assert_true "explicit CI PASS with all required jobs" grep -q 'CI_OK' <<<"$ci_ok"

cat >"$MOCK/check-runs.json" <<'EOF'
{"check_runs":[
  {"name":"Backend focused tests","status":"completed","conclusion":"success"},
  {"name":"Frontend tests and build","status":"completed","conclusion":"success"}
]}
EOF
assert_false "explicit CI FAIL when required job missing" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" "$VERIFY_CI" "prasnda21-tech/eternal-packaging" "$MAIN_SHA"

cat >"$MOCK/workflow-runs.json" <<'EOF'
{"workflow_runs":[{"id":1,"status":"completed","conclusion":"failure"}]}
EOF
cat >"$MOCK/check-runs.json" <<'EOF'
{"check_runs":[
  {"name":"Backend focused tests","status":"completed","conclusion":"success"},
  {"name":"Backend stock compatibility (replica set)","status":"completed","conclusion":"success"},
  {"name":"Frontend tests and build","status":"completed","conclusion":"success"}
]}
EOF
assert_false "explicit CI FAIL when workflow run not success" \
  env ETERNAL_GH_API_MOCK_DIR="$MOCK" "$VERIFY_CI" "prasnda21-tech/eternal-packaging" "$MAIN_SHA"

# ========== 18) approval token helper + workflow shape ==========
assert_true "constant-time eq accepts match" eternal_constant_time_eq "token-a" "token-a"
assert_false "constant-time eq rejects mismatch" eternal_constant_time_eq "token-a" "token-b"

WF="$ROOT/.github/workflows/deploy-production.yml"
assert_true "workflow is workflow_dispatch" grep -q 'workflow_dispatch' "$WF"
assert_true "workflow has authorize job" grep -q 'name: Separate production authorization' "$WF"
assert_true "workflow requires approval_token input" grep -q 'approval_token' "$WF"
assert_true "workflow checks DEPLOY_APPROVAL_TOKEN" grep -q 'DEPLOY_APPROVAL_TOKEN' "$WF"
assert_true "deploy needs authorize" grep -q 'needs: \[preflight, authorize\]' "$WF"
assert_true "workflow uses verify_ci_for_sha" grep -q 'verify_ci_for_sha.sh' "$WF"
assert_true "workflow concurrency group set" grep -q 'eternal-packaging-production-deploy' "$WF"
assert_true "workflow restricts main" grep -q "github.ref == 'refs/heads/main'" "$WF"
assert_true "preflight has no DEPLOY_SSH_KEY" \
  bash -c "! awk '/^  preflight:/,/^  [a-z]/ {print}' '$WF' | grep -q DEPLOY_SSH_KEY"

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi
exit 0
