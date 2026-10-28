#!/usr/bin/env bash
# Focused FE artifact deploy proofs (no full infra suite). Fail-closed. No prod.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RUNNER="$ROOT/scripts/deploy/eternal-deploy-runner.sh"
PASS=0; FAIL=0
REPORT="${ETERNAL_TEST_REPORT:-/opt/cursor/artifacts/frontend-artifact-focused.log}"
mkdir -p "$(dirname "$REPORT")"; : >"$REPORT"
log() { printf '%s\n' "$*" | tee -a "$REPORT"; }
assert_eq() {
  local l="$1" e="$2" a="$3"
  if [[ "$e" == "$a" ]]; then log "PASS: $l"; PASS=$((PASS+1))
  else log "FAIL: $l expected=[$e] actual=[$a]"; FAIL=$((FAIL+1)); fi
}
assert_true() {
  local l="$1"; shift
  if "$@"; then log "PASS: $l"; PASS=$((PASS+1)); else log "FAIL: $l"; FAIL=$((FAIL+1)); fi
}
assert_false() {
  local l="$1"; shift
  if "$@"; then log "FAIL: $l (expected failure)"; FAIL=$((FAIL+1)); else log "PASS: $l"; PASS=$((PASS+1)); fi
}

WORKDIR="$(mktemp -d)"
cleanup() { chmod -R u+w "$WORKDIR" 2>/dev/null || true; rm -rf "$WORKDIR"; }
trap cleanup EXIT
BIN="$WORKDIR/bin"; HARNESS="$WORKDIR/harness"
MIRROR_SRC="$WORKDIR/repo"; RUNTIME="$HARNESS/runtime"; RELEASES="$RUNTIME/releases"
FRONTEND="$HARNESS/www"; LEGACY="$HARNESS/legacy-dirty"; CURRENT="$RUNTIME/current"
FRONTEND_PREV="$RUNTIME/frontend-prev"; LOCK="$HARNESS/deploy.lock"; BASELINE="$RELEASES/baseline-active"
mkdir -p "$BIN" "$RELEASES" "$FRONTEND_PREV" "$FRONTEND/live/static/js" "$LEGACY/backend"

git init -q -b main "$MIRROR_SRC"
git -C "$MIRROR_SRC" config user.email "synthetic@example.com"
git -C "$MIRROR_SRC" config user.name "Synthetic"
mkdir -p "$MIRROR_SRC/backend" "$MIRROR_SRC/frontend/src" "$MIRROR_SRC/frontend/public" "$MIRROR_SRC/scripts/deploy"
printf 'ok\n' >"$MIRROR_SRC/backend/requirements.txt"
printf 'ok-portable\n' >"$MIRROR_SRC/backend/requirements.portable.txt"
cat >"$MIRROR_SRC/frontend/package.json" <<'EOF'
{"name":"frontend","private":true,"scripts":{"build":"true"}}
EOF
printf 'yarn lockfile v1\n' >"$MIRROR_SRC/frontend/yarn.lock"
printf 'app\n' >"$MIRROR_SRC/frontend/src/index.js"
printf 'pub\n' >"$MIRROR_SRC/frontend/public/robots.txt"
cp "$ROOT/scripts/deploy/eternal-actions-gate" "$ROOT/scripts/deploy/eternal-deploy-runner.sh" \
  "$ROOT/scripts/deploy/lib.sh" "$MIRROR_SRC/scripts/deploy/"
git -C "$MIRROR_SRC" add . && git -C "$MIRROR_SRC" commit -q -m base
git clone -q --mirror "$MIRROR_SRC" "$RELEASES/mirror.git"
EXPECTED_WD="$CURRENT/backend"

# Minimal curl/systemctl stubs (health + FE HTTP)
cat >"$BIN/systemctl" <<EOF
#!/usr/bin/env bash
echo "systemctl \$*" >>"$HARNESS/systemctl.log"
if [[ "\$1" == "show" ]]; then
  if [[ "\$#" -ne 5 || "\$2" != "-p" || "\$4" != "--value" ]]; then
    echo "systemctl show argv bad: \$*" >&2; exit 2
  fi
fi
case "\$1" in
  show)
    case "\$3" in
      WorkingDirectory) printf '%s\n' "$EXPECTED_WD"; exit 0 ;;
      ExecStart) printf '%s\n' "$EXPECTED_WD/.venv/bin/uvicorn server:app"; exit 0 ;;
      MainPID)
        if [[ -f "$HARNESS/backend.pid" ]]; then cat "$HARNESS/backend.pid"; else printf '0\n'; fi
        exit 0 ;;
      ActiveState) printf 'active\n'; exit 0 ;;
      *) exit 3 ;;
    esac ;;
  is-active) printf 'active\n'; exit 0 ;;
  restart)
    if [[ "\${ETERNAL_FORCE_RESTART_FAIL:-0}" == "1" ]]; then exit 1; fi
    cwd="\$(readlink -f "${CURRENT}/backend" 2>/dev/null || true)"
    if [[ -n "\$cwd" ]]; then
      mkdir -p "\$cwd"
      if [[ -f "$HARNESS/backend.pid" ]]; then kill "\$(cat "$HARNESS/backend.pid")" 2>/dev/null || true; fi
      ( cd "\$cwd" && exec sleep 3600 ) &
      echo \$! >"$HARNESS/backend.pid"
    fi
    exit 0 ;;
esac
exit 0
EOF
chmod +x "$BIN/systemctl"
trap 'if [[ -f "$HARNESS/backend.pid" ]]; then kill "$(cat "$HARNESS/backend.pid")" 2>/dev/null || true; fi; cleanup' EXIT
cat >"$BIN/curl" <<'EOF'
#!/usr/bin/env bash
out_file=""; dump_header=""; url=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--output) out_file="$2"; shift 2 ;;
    -D|--dump-header) dump_header="$2"; shift 2 ;;
    -sS|-s|-S|-f|-L|--fail|--location|-k) shift ;;
    --connect-timeout|--max-time|-w|--write-out) shift 2 ;;
    http*|https*) url="$1"; shift ;;
    *) shift ;;
  esac
done
if [[ -n "${ETERNAL_FRONTEND_HTTP_FAIL_STATE:-}" && -f "${ETERNAL_FRONTEND_HTTP_FAIL_STATE}" ]]; then
  n="$(cat "${ETERNAL_FRONTEND_HTTP_FAIL_STATE}")"
  if [[ "${n:-0}" -gt 0 ]]; then
    echo $((n-1)) >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
    [[ -n "$dump_header" ]] && printf 'HTTP/1.1 403\r\n\r\n' >"$dump_header"
    [[ -n "$out_file" ]] && : >"$out_file"
    printf '403'; exit 0
  fi
fi
if [[ "$url" == *"/api/system/health"* || "$url" == *"health"* ]]; then
  body='{"mongo":true,"mode":"operational"}'
  [[ -n "$out_file" ]] && printf '%s' "$body" >"$out_file"
  [[ -n "$dump_header" ]] && printf 'HTTP/1.1 200\r\n\r\n' >"$dump_header"
  printf '200'; exit 0
fi
# FE asset/index
[[ -n "$dump_header" ]] && printf 'HTTP/1.1 200\r\n\r\n' >"$dump_header"
[[ -n "$out_file" ]] && printf 'ok' >"$out_file"
printf '200'
exit 0
EOF
chmod +x "$BIN/curl"
: >"$HARNESS/yarn.cmd.log"
cat >"$BIN/yarn" <<EOF
#!/usr/bin/env bash
echo "YARN \$*" >>"$HARNESS/yarn.cmd.log"
exit 0
EOF
chmod +x "$BIN/yarn"
export PATH="$BIN:$PATH"

export ETERNAL_HEALTH_FAIL_STATE="$HARNESS/health-fail.remaining"; echo 0 >"$ETERNAL_HEALTH_FAIL_STATE"
export ETERNAL_HEALTH_HTTP_FAIL_STATE="$HARNESS/health-http-fail.remaining"; echo 0 >"$ETERNAL_HEALTH_HTTP_FAIL_STATE"
export ETERNAL_FRONTEND_HTTP_FAIL_STATE="$HARNESS/frontend-http-fail.remaining"; echo 0 >"$ETERNAL_FRONTEND_HTTP_FAIL_STATE"
export ETERNAL_FRONTEND_PUBLIC_URL="https://fixture.nginx.local"
export ETERNAL_FRONTEND_HTTP_TIMEOUT_S=2
export ETERNAL_RUNTIME_ROOT="$RUNTIME" ETERNAL_RELEASES_ROOT="$RELEASES" ETERNAL_CURRENT_LINK="$CURRENT"
export ETERNAL_LEGACY_ROOT="$LEGACY" ETERNAL_FRONTEND_ROOT="$FRONTEND/live" ETERNAL_FRONTEND_PREV="$FRONTEND_PREV"
export ETERNAL_DEPLOY_LOCK="$LOCK" ETERNAL_SERVICE_NAME="eternal-packaging-backend.service"
export ETERNAL_HEALTH_URL="http://127.0.0.1:9/api/system/health" ETERNAL_HEALTH_RETRIES=2 ETERNAL_HEALTH_SLEEP_S=0
export ETERNAL_MIRROR_DIR="$RELEASES/mirror.git" ETERNAL_SKIP_FETCH=1
export ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl" ETERNAL_USE_SYSTEMCTL_SUDO=0 ETERNAL_CURL_BIN="$BIN/curl"
export ETERNAL_SYSTEMCTL_LOG="$HARNESS/systemctl.log"; : >"$ETERNAL_SYSTEMCTL_LOG"
export ETERNAL_BASELINE_DIR="$BASELINE" ETERNAL_EXPECTED_WD="$EXPECTED_WD" ETERNAL_EXPECTED_EXEC_PREFIX="$EXPECTED_WD"
export ETERNAL_REQUIRE_PROCCWD=1 ETERNAL_REQUIRE_VERIFIED_BASELINE=1 ETERNAL_MIN_DISK_KB=1
export ETERNAL_RUNTIME_MODE=stub
printf 'old\n<script src="/static/js/old.js"></script>\n' >"$FRONTEND/live/index.html"
printf 'old-asset\n' >"$FRONTEND/live/static/js/old.js"
printf 'active\n' >"$FRONTEND/live/active.txt"
printf 'legacy\n' >"$LEGACY/backend/app.py"
printf 'ok\n' >"$LEGACY/backend/requirements.txt"
printf 'ok-portable\n' >"$LEGACY/backend/requirements.portable.txt"

source "$ROOT/scripts/deploy/lib.sh"
make_verified_baseline() {
  mkdir -p "$BASELINE/tree/backend" "$BASELINE/frontend-snapshot/static/js"
  cp -a "$LEGACY/backend"/. "$BASELINE/tree/backend"/
  rm -rf "$BASELINE/tree/backend/.venv"
  cp -a "$FRONTEND/live"/. "$BASELINE/frontend-snapshot"/
  { printf 'kind=verified-baseline\nsha=baseline-active\nimmutable=1\n'; } >"$BASELINE/.deploy-ok"
  : >"$BASELINE/.baseline"
  ETERNAL_RUNTIME_MODE=stub eternal_prepare_backend_runtime "$BASELINE/tree/backend"
  eternal_baseline_write_integrity "$BASELINE" "$LEGACY" "$EXPECTED_WD" "$EXPECTED_WD/.venv/bin/uvicorn"
}
start_backend() {
  local cwd="$1"
  mkdir -p "$cwd"
  if [[ -f "$HARNESS/backend.pid" ]]; then kill "$(cat "$HARNESS/backend.pid")" 2>/dev/null || true; fi
  ( cd "$cwd" && exec sleep 3600 ) &
  echo $! >"$HARNESS/backend.pid"
}

make_verified_baseline
start_backend "$BASELINE/tree/backend"
ln -sfn "$BASELINE/tree" "$CURRENT"
export ETERNAL_ACK_REPLACE_LIVE_FRONTEND=1
export ETERNAL_DRY_BUILD=1
export ETERNAL_FRONTEND_BUILD_MODE=dry
# First cutover from baseline with dry FE so CURRENT is a real release
FIRST_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
set +e
"$RUNNER" deploy "$FIRST_SHA" >"$HARNESS/first.txt" 2>"$HARNESS/first.err"
first_ec=$?
set -e
assert_eq "first cutover dry deploy" "0" "$first_ec"
assert_true "first DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/first.txt"
if [[ "$first_ec" != "0" ]]; then
  log "first.err:"; tail -n 50 "$HARNESS/first.err" | tee -a "$REPORT" || true
fi
start_backend "$RELEASES/$FIRST_SHA/backend"

# Pack artifact for FE bump
printf 'artifact-src-v1\n' >"$MIRROR_SRC/frontend/src/index.js"
git -C "$MIRROR_SRC" add frontend/src/index.js && git -C "$MIRROR_SRC" commit -q -m 'fe artifact'
ART_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
ART_SRC="$HARNESS/art-src"; ART_OUT="$HARNESS/art-out"
rm -rf "$ART_SRC" "$ART_OUT"
mkdir -p "$ART_SRC"
git --git-dir="$RELEASES/mirror.git" archive --format=tar "$ART_SHA" | tar -x -C "$ART_SRC"
mkdir -p "$ART_SRC/frontend/build/static/js"
# Unique hashed-like name — avoid collision with dry-build static/js/app.js
printf 'art-asset\n' >"$ART_SRC/frontend/build/static/js/main.art1111.js"
printf '<script src="/static/js/main.art1111.js"></script>\n' >"$ART_SRC/frontend/build/index.html"
printf '{"files":{"main.js":"/static/js/main.art1111.js"}}\n' >"$ART_SRC/frontend/build/asset-manifest.json"
export FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL=""
bash "$ROOT/scripts/deploy/pack_frontend_artifact.sh" \
  --frontend-dir "$ART_SRC/frontend" --out-dir "$ART_OUT" --git-sha "$ART_SHA" \
  --workflow frontend-artifact.yml --run-id 99 --run-attempt 1 >"$HARNESS/pack.out"
unset ETERNAL_FRONTEND_EXPECTED_ENV_FP || true
unset ETERNAL_FRONTEND_ENV_FP_TEST_LOCK || true
export FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL=""
export ETERNAL_DRY_BUILD=0 ETERNAL_FRONTEND_BUILD_MODE=artifact
export ETERNAL_FRONTEND_ARTIFACT_DIR="$ART_OUT"
export ETERNAL_ACK_REPLACE_LIVE_FRONTEND=0
: >"$HARNESS/yarn.cmd.log"
set +e
"$RUNNER" deploy "$ART_SHA" >"$HARNESS/art.txt" 2>"$HARNESS/art.err"
art_ec=$?
set -e
if [[ "$art_ec" != "0" ]]; then
  log "art.err tail:"; tail -n 60 "$HARNESS/art.err" | tee -a "$REPORT" || true
  log "art.txt:"; cat "$HARNESS/art.txt" | tee -a "$REPORT" || true
fi
assert_eq "artifact-mode deploy exits 0" "0" "$art_ec"
assert_true "artifact-mode DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/art.txt"
assert_true "artifact install logged" grep -q 'frontend artifact installed' "$HARNESS/art.err"
assert_false "no yarn invoked" grep -q 'YARN' "$HARNESS/yarn.cmd.log"
assert_true "provenance marker" test -f "$RELEASES/$ART_SHA/.eternal-frontend-artifact"

# Duplicate
set +e
"$RUNNER" deploy "$ART_SHA" >"$HARNESS/art2.txt" 2>"$HARNESS/art2.err"
art2_ec=$?
set -e
assert_eq "duplicate artifact deploy" "0" "$art2_ec"
assert_true "duplicate DEPLOY_OK" grep -q 'DEPLOY_OK' "$HARNESS/art2.txt"

# Backend-only without artifact dir
printf 'be\n' >>"$MIRROR_SRC/backend/requirements.portable.txt"
git -C "$MIRROR_SRC" add backend/requirements.portable.txt && git -C "$MIRROR_SRC" commit -q -m be
BE_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
unset ETERNAL_FRONTEND_ARTIFACT_DIR
start_backend "$RELEASES/$ART_SHA/backend"
set +e
"$RUNNER" deploy "$BE_SHA" >"$HARNESS/be.txt" 2>"$HARNESS/be.err"
be_ec=$?
set -e
if [[ "$be_ec" != "0" ]]; then log "be.err:"; tail -n 40 "$HARNESS/be.err" | tee -a "$REPORT" || true; fi
assert_eq "backend-only deploy" "0" "$be_ec"
assert_true "backend-only skips FE build" grep -q 'frontend unchanged; skip build' "$HARNESS/be.err"

# Wrong env override refused
set +e
ov_err="$(
  FRONTEND_PROD_ENV_MODE=same-origin DISABLE_ESLINT_PLUGIN=true REACT_APP_BACKEND_URL= \
  ETERNAL_FRONTEND_ENV_FP_TEST_LOCK=1 \
  ETERNAL_FRONTEND_EXPECTED_ENV_FP=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; eternal_frontend_expected_env_fingerprint' 2>&1
)"
ov_ec=$?
set -e
assert_eq "wrong env override rejected" "1" "$ov_ec"

# Path-independent fingerprint behavior
FA="$HARNESS/fa/frontend"; FB="$HARNESS/fb/frontend"
mkdir -p "$FA/src" "$FB/src" "$FA/public" "$FB/public"
cp -a "$ART_SRC/frontend/package.json" "$ART_SRC/frontend/yarn.lock" "$FA/"
cp -a "$ART_SRC/frontend/package.json" "$ART_SRC/frontend/yarn.lock" "$FB/"
cp -a "$ART_SRC/frontend/src/." "$FA/src/"; cp -a "$ART_SRC/frontend/src/." "$FB/src/"
cp -a "$ART_SRC/frontend/public/." "$FA/public/"; cp -a "$ART_SRC/frontend/public/." "$FB/public/"
assert_eq "fingerprint path-independent" \
  "$(eternal_frontend_input_fingerprint "$FA")" \
  "$(eternal_frontend_input_fingerprint "$FB")"

# Docs-only noop
export ETERNAL_DRY_BUILD=1 ETERNAL_FRONTEND_BUILD_MODE=dry
unset ETERNAL_FRONTEND_ARTIFACT_DIR
active_docs_before="$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"
mkdir -p "$MIRROR_SRC/docs"
printf 'note\n' >"$MIRROR_SRC/docs/x.md"
git -C "$MIRROR_SRC" add docs/x.md && git -C "$MIRROR_SRC" commit -q -m docs
DOCS_SHA="$(git -C "$MIRROR_SRC" rev-parse HEAD)"
git --git-dir="$RELEASES/mirror.git" fetch -q "$MIRROR_SRC" '+refs/heads/main:refs/heads/main'
set +e
"$RUNNER" deploy "$DOCS_SHA" >"$HARNESS/docs.txt" 2>"$HARNESS/docs.err"
docs_ec=$?
set -e
assert_eq "docs-only exit 0" "0" "$docs_ec"
assert_true "docs-only NOOP" grep -q 'DEPLOY_OK_NOOP' "$HARNESS/docs.txt"
assert_eq "docs-only active preserved" "$active_docs_before" \
  "$(sed -n 's/^sha=//p' "$RELEASES/active-release.txt" | head -n1)"

assert_true "pack OK" grep -q FRONTEND_ARTIFACT_OK "$HARNESS/pack.out"
assert_true "actions run helper present" \
  grep -q 'eternal_verify_frontend_artifact_actions_run' "$ROOT/scripts/deploy/lib.sh"

log ""; log "SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
