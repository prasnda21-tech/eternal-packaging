#!/usr/bin/env bash
# Synthetic fail-closed proofs for deploy gate + runner (no real VPS / secrets).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
GATE="$ROOT/scripts/deploy/eternal-actions-gate"
RUNNER="$ROOT/scripts/deploy/eternal-deploy-runner.sh"
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
mkdir -p "$BIN" "$RELEASES" "$FRONTEND" "$LEGACY" "$HARNESS/prev"

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
cp "$GATE" "$RUNNER" "$ROOT/scripts/deploy/lib.sh" "$MIRROR_SRC/scripts/deploy/"
git -C "$MIRROR_SRC" add .
git -C "$MIRROR_SRC" commit -q -m "base"
BASE_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"

# second main commit (backend-only change)
printf 'ok\n# pin\n' >"$MIRROR_SRC/backend/requirements.txt"
git -C "$MIRROR_SRC" add backend/requirements.txt
git -C "$MIRROR_SRC" commit -q -m "backend bump"
MAIN_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"

# unmerged feature commit
git -C "$MIRROR_SRC" checkout -q -b feature/unmerged
printf 'secret-feature\n' >"$MIRROR_SRC/backend/feature.txt"
git -C "$MIRROR_SRC" add backend/feature.txt
git -C "$MIRROR_SRC" commit -q -m "unmerged"
UNMERGED_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git -C "$MIRROR_SRC" checkout -q main

# migration commit on main for guard test (separate tip we can reset)
git -C "$MIRROR_SRC" checkout -q -b tmp/migration
mkdir -p "$MIRROR_SRC/backend/scripts"
printf 'print("migrate")\n' >"$MIRROR_SRC/backend/scripts/migrate_storage_dryrun.py"
git -C "$MIRROR_SRC" add backend/scripts/migrate_storage_dryrun.py
git -C "$MIRROR_SRC" commit -q -m "migration"
MIGRATION_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git -C "$MIRROR_SRC" checkout -q main

git clone -q --mirror "$MIRROR_SRC" "$RELEASES/mirror.git"
# include feature and migration refs in mirror
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/*:refs/heads/*'

# mock systemctl + curl
cat >"$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >>"${ETERNAL_SYSTEMCTL_LOG}"
exit 0
EOF
chmod +x "$BIN/systemctl"

cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
if [[ "${ETERNAL_HEALTH_FORCE_FAIL:-0}" == "1" ]]; then
  echo "curl mock health fail" >&2
  exit 22
fi
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
export ETERNAL_CURL_BIN="$BIN/curl"
export ETERNAL_SYSTEMCTL_LOG="$HARNESS/systemctl.log"
export ETERNAL_DEPLOY_RUNNER="$RUNNER"
: >"$ETERNAL_SYSTEMCTL_LOG"
mkdir -p "$FRONTEND/live"
printf 'old-frontend\n' >"$FRONTEND/live/index.html"

run_gate() {
  SSH_ORIGINAL_COMMAND="$*" "$GATE"
}

# ========== 1) probe PASS ==========
probe_out="$(run_gate probe)"
assert_true "gate probe PASS" grep -q 'PASS probe' <<<"$probe_out"

# ========== 2) invalid commands rejected ==========
assert_false "gate rejects arbitrary shell" run_gate 'bash -lc id'
assert_false "gate rejects path" run_gate 'cat /etc/passwd'
assert_false "gate rejects metacharacters" run_gate 'deploy $(id)'
assert_false "gate rejects short sha" run_gate "deploy ${MAIN_SHA:0:7}"

# ========== 3) invalid SHA ==========
assert_false "runner rejects invalid sha" env ETERNAL_ALLOW_MIGRATIONS=0 \
  "$RUNNER" deploy 'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz'

# ========== 4) unmerged SHA ==========
assert_false "runner rejects unmerged sha" \
  "$RUNNER" deploy "$UNMERGED_SHA"

# ========== 5) valid SHA deploy ==========
: >"$ETERNAL_SYSTEMCTL_LOG"
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
assert_true "deploy-ok marker written" test -f "$RELEASES/$MAIN_SHA/.deploy-ok"

# ========== 6) skip restart when only frontend would change / backend-only already deployed ==========
# Deploy same SHA again (reuse release): classification vs itself → no backend/frontend need,
# but prev_sha == target means empty diff. Force a frontend-only commit on main tip in a copy?
# Instead: create frontend-only SHA on main and deploy it; systemctl log should stay empty.
git -C "$MIRROR_SRC" checkout -q main
printf 'frontend-only\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js
git -C "$MIRROR_SRC" commit -q -m "frontend only"
FE_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
: >"$ETERNAL_SYSTEMCTL_LOG"
"$RUNNER" deploy "$FE_SHA" >"$HARNESS/fe.txt" 2>"$HARNESS/fe.err"
assert_eq "frontend-only skips backend restart" "" "$(cat "$ETERNAL_SYSTEMCTL_LOG")"
assert_true "frontend-only still DEPLOY_OK" grep -q "DEPLOY_OK sha=$FE_SHA" "$HARNESS/fe.txt"

# ========== 7) deploy lock ==========
printf 'other\n' >"$LOCK"
assert_false "deploy lock blocks second deployer" "$RUNNER" deploy "$FE_SHA"
rm -f "$LOCK"

# ========== 8) local conflict (incomplete release dir) ==========
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

# also frontend in-progress marker
touch "$FRONTEND/live/.deploy-in-progress"
assert_false "frontend in-progress marker is local conflict" "$RUNNER" deploy "$CONFLICT_SHA"
rm -f "$FRONTEND/live/.deploy-in-progress"

# ========== 9) health fail + rollback ==========
# Establish known previous release at FE_SHA
rm -f "$LOCK"
"$RUNNER" deploy "$FE_SHA" >"$HARNESS/refe.txt" 2>"$HARNESS/refe.err" || true
PREV_LINK="$(readlink -f "$CURRENT")"
printf 'keep-me\n' >"$FRONTEND/live/keep.txt"
export ETERNAL_HEALTH_FORCE_FAIL=1
set +e
"$RUNNER" deploy "$CONFLICT_SHA" >"$HARNESS/hf.txt" 2>"$HARNESS/hf.err"
hf_ec=$?
set -e
export ETERNAL_HEALTH_FORCE_FAIL=0
assert_eq "health failure exits nonzero" "1" "$hf_ec"
assert_true "rollback restores previous current" \
  bash -c "[[ \"\$(readlink -f \"$CURRENT\")\" == \"$PREV_LINK\" ]]"
assert_true "rollback restores previous frontend marker file" test -f "$FRONTEND/live/keep.txt"

# ========== 10) migration guard ==========
# Move main to migration sha in mirror only for ancestry: attach migration commit onto main
git -C "$MIRROR_SRC" checkout -q main
git -C "$MIRROR_SRC" merge -q --no-ff "$MIGRATION_SHA" -m "merge migration"
MIG_MAIN="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
# Ensure current is FE_SHA so diff includes migration file
rm -f "$LOCK"
# reset current to FE release for migration range
ln -sfn "$RELEASES/$FE_SHA" "$CURRENT"
printf 'sha=%s\n' "$FE_SHA" >"$RELEASES/$FE_SHA/.deploy-ok"
assert_false "unapproved migration paths refused" \
  env ETERNAL_ALLOW_MIGRATIONS=0 "$RUNNER" deploy "$MIG_MAIN"

# ========== 11) secret masking ==========
# Ensure redactor strips private key PEM and that runner/gate output does not echo SSH key env.
FAKE_KEY=$'-----BEGIN OPENSSH PRIVATE KEY-----\nSECRETKEYMATERIAL1234567890\n-----END OPENSSH PRIVATE KEY-----'
# shellcheck disable=SC1091
source "$ROOT/scripts/deploy/lib.sh"
redacted="$(eternal_redact_line "key=$FAKE_KEY")"
assert_true "redactor masks private key block" grep -q 'REDACTED_PRIVATE_KEY' <<<"$redacted"
assert_false "redactor output lacks raw key material" grep -q 'SECRETKEYMATERIAL1234567890' <<<"$redacted"

export DEPLOY_SSH_KEY="$FAKE_KEY"
set +e
SSH_ORIGINAL_COMMAND="deploy $FE_SHA" "$GATE" >"$HARNESS/mask.out" 2>"$HARNESS/mask.err"
set -e
assert_false "gate/runner logs do not leak private key" \
  grep -q 'SECRETKEYMATERIAL1234567890' "$HARNESS/mask.out" "$HARNESS/mask.err"

# ========== 12) workflow file sanity ==========
WF="$ROOT/.github/workflows/deploy-production.yml"
assert_true "workflow is workflow_dispatch" grep -q 'workflow_dispatch' "$WF"
assert_true "workflow uses production environment" grep -q 'environment: production' "$WF"
assert_true "workflow concurrency group set" grep -q 'eternal-packaging-production-deploy' "$WF"
assert_true "workflow restricts main" grep -q "github.ref == 'refs/heads/main'" "$WF"
assert_true "preflight has no secrets context for deploy key" \
  bash -c "! grep -n 'DEPLOY_SSH_KEY' '$WF' | grep -q preflight"

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
if [[ "$FAIL" -ne 0 ]]; then
  exit 1
fi
exit 0
