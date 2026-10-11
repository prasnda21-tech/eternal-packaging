#!/usr/bin/env bash
# Real lib.sh → verifier → finalizer path using an existing portable venv.
# Does NOT reinstall packages; refuses to run if portable venv evidence is missing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=../deploy/lib.sh
source "$ROOT/scripts/deploy/lib.sh"

FINALIZE="$ROOT/scripts/deploy/finalize-existing-baseline.sh"
REQ="$ROOT/backend/requirements.portable.txt"
EV="${ETERNAL_PORTABLE_VENV_EVIDENCE:-$ROOT/.cache/eternal-portable-venv}"
PASS=0
FAIL=0
REPORT="${ETERNAL_TEST_REPORT:-/opt/cursor/artifacts/finalize_real_portable_venv.log}"
mkdir -p "$(dirname "$REPORT")"
: >"$REPORT"

log() { printf '%s\n' "$*" | tee -a "$REPORT"; }
assert_true() {
  local label="$1"; shift
  if "$@"; then log "PASS: $label"; PASS=$((PASS+1))
  else log "FAIL: $label"; FAIL=$((FAIL+1)); fi
}
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then log "PASS: $label"; PASS=$((PASS+1))
  else log "FAIL: $label expected=[$expected] actual=[$actual]"; FAIL=$((FAIL+1)); fi
}
assert_false() {
  local label="$1"; shift
  if "$@"; then log "FAIL: $label (expected failure)"; FAIL=$((FAIL+1))
  else log "PASS: $label"; PASS=$((PASS+1)); fi
}

REQ_HASH="$(sha256sum "$REQ" | awk '{print $1}')"
log "requirements.portable.txt sha256=$REQ_HASH"
log "portable_venv_evidence=$EV"

if [[ ! -x "$EV/bin/python" || ! -x "$EV/bin/pip" || ! -x "$EV/bin/uvicorn" ]]; then
  log "BLOCKED: portable venv evidence missing at $EV (need python/pip/uvicorn)"
  log "SUMMARY pass=$PASS fail=$FAIL blocked=1"
  exit 2
fi
if [[ ! -f "$EV/.eternal-req-sha256" ]] || ! grep -qx "$REQ_HASH" "$EV/.eternal-req-sha256"; then
  log "BLOCKED: portable venv hash stamp mismatch/missing (expected $REQ_HASH)"
  log "SUMMARY pass=$PASS fail=$FAIL blocked=1"
  exit 2
fi

# 1) Full portable requirements against real venv (lib.sh path includes pip check + verifier).
BACKEND_WRAP="$(mktemp -d)/backend"
mkdir -p "$(dirname "$BACKEND_WRAP")"
# Place requirements + link the real venv (no copy/reinstall).
mkdir -p "$BACKEND_WRAP"
cp "$REQ" "$BACKEND_WRAP/requirements.portable.txt"
cp "$ROOT/backend/requirements.txt" "$BACKEND_WRAP/requirements.txt" 2>/dev/null || true
ln -sfn "$EV" "$BACKEND_WRAP/.venv"
# uvicorn may be missing name in some venvs — ensure validate can see it
if [[ ! -x "$BACKEND_WRAP/.venv/bin/uvicorn" ]]; then
  log "BLOCKED: portable venv has no uvicorn executable"
  exit 2
fi

log "=== verifier on full portable requirements (venv python) ==="
set +e
"$EV/bin/python" "$ROOT/scripts/deploy/verify_requirements_installed.py" "$REQ" \
  >"$REPORT.verifier.out" 2>"$REPORT.verifier.err"
ver_ec=$?
set -e
cat "$REPORT.verifier.out" "$REPORT.verifier.err" | tee -a "$REPORT" >/dev/null || true
assert_eq "full portable verifier exit 0" "0" "$ver_ec"

log "=== lib.sh eternal_verify_requirements_installed (full mode) ==="
set +e
ETERNAL_RUNTIME_MODE=full eternal_verify_requirements_installed "$BACKEND_WRAP" requirements.portable.txt \
  >"$REPORT.lib.out" 2>"$REPORT.lib.err"
lib_ec=$?
set -e
cat "$REPORT.lib.out" "$REPORT.lib.err" | tee -a "$REPORT" >/dev/null || true
assert_eq "lib.sh dependency verify exit 0" "0" "$lib_ec"
assert_true "lib.sh logged pip check PASS" grep -q 'pip check PASS' "$REPORT.lib.err" || grep -q 'pip check PASS' "$REPORT.lib.out"

# 2) Legacy baseline fixture with real venv + valid integrity manifest
HARNESS="$(mktemp -d)"
RUNTIME="$HARNESS/runtime"
RELEASES="$RUNTIME/releases"
BASELINE="$RELEASES/baseline-active"
BACKEND="$BASELINE/tree/backend"
LOCK="$HARNESS/deploy.lock"
CURRENT="$RUNTIME/current"
mkdir -p "$BACKEND" "$BASELINE/frontend-snapshot/static" "$CURRENT" "$RELEASES"
: >"$LOCK"

# Minimal backend tree (sources) + real venv copy (reuse packages; fix shebangs to this path)
printf 'app\n' >"$BACKEND/server.py"
cp "$REQ" "$BACKEND/requirements.portable.txt"
cp "$ROOT/backend/requirements.txt" "$BACKEND/requirements.txt" 2>/dev/null || printf 'x\n' >"$BACKEND/requirements.txt"
rm -rf "$BACKEND/.venv"
cp -a "$EV" "$BACKEND/.venv"
# Relocate only text config/shebangs (never rewrite bytecode/wheels).
if [[ -f "$BACKEND/.venv/pyvenv.cfg" ]]; then
  sed -i "s|$EV|$BACKEND/.venv|g" "$BACKEND/.venv/pyvenv.cfg"
fi
for f in "$BACKEND/.venv/bin"/*; do
  [[ -e "$f" && ! -L "$f" && -f "$f" ]] || continue
  if head -c 64 "$f" 2>/dev/null | grep -q '^#!'; then
    sed -i "s|$EV|$BACKEND/.venv|g" "$f"
  fi
done
printf 'index\n' >"$BASELINE/frontend-snapshot/index.html"
printf 'a\n' >"$BASELINE/frontend-snapshot/static/app.js"
venv_inode_before="$(stat -c '%i' "$BACKEND/.venv")"

# Build integrity manifest excluding .venv (same prune as lib)
(
  cd "$BASELINE"
  find tree/backend frontend-snapshot \
    \( -path 'tree/backend/.venv' -o -path 'tree/backend/.venv/*' \
       -o -path 'tree/backend/.eternal-requirements-file' \
       -o -path 'tree/backend/.eternal-requirements-sha256' \
       -o -path '*/__pycache__' -o -path '*/__pycache__/*' \) -prune -o \
    -type f -print0 | sort -z | xargs -0 sha256sum
) >"$BASELINE/.baseline-integrity"
manifest_hash="$(sha256sum "$BASELINE/.baseline-integrity" | awk '{print $1}')"
{
  printf 'version=3\n'
  printf 'kind=verified-baseline\n'
  printf 'immutable=1\n'
  printf 'content_digests_verified_at_write=1\n'
  printf 'backend_src=fixture\n'
  printf 'frontend_src=fixture\n'
  printf 'integrity_manifest=.baseline-integrity\n'
  printf 'integrity_sha256=%s\n' "$manifest_hash"
  printf 'file_count=%s\n' "$(wc -l <"$BASELINE/.baseline-integrity" | tr -d ' ')"
  printf 'frontend_file_count=2\n'
  printf 'runtime_python=%s\n' "$BACKEND/.venv/bin/python"
  printf 'runtime_uvicorn=%s\n' "$BACKEND/.venv/bin/uvicorn"
  printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'note=legacy fixture without requirements provenance\n'
} >"$BASELINE/.baseline-verified"
# Explicitly no requirements_file / no install stamps
rm -f "$BACKEND/.eternal-requirements-file" "$BACKEND/.eternal-requirements-sha256" \
  "$BASELINE/.baseline" "$BASELINE/.deploy-ok"
cp "$BASELINE/.baseline-verified" "$HARNESS/before-verified"

# Fake systemctl (must not be used for activation by finalize)
BIN="$HARNESS/bin"
mkdir -p "$BIN"
cat >"$BIN/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "UNEXPECTED systemctl $*" >&2
exit 99
EOF
chmod +x "$BIN/systemctl"

export ETERNAL_RUNTIME_MODE=full
export ETERNAL_BASELINE_DIR="$BASELINE"
export ETERNAL_RELEASES_ROOT="$RELEASES"
export ETERNAL_RUNTIME_ROOT="$RUNTIME"
export ETERNAL_DEPLOY_LOCK="$LOCK"
export ETERNAL_SYSTEMCTL_BIN="$BIN/systemctl"
export ETERNAL_USE_SYSTEMCTL_SUDO=0
export ETERNAL_EXPECTED_WD="$CURRENT/backend"
export PATH="$BIN:$PATH"

log "=== finalize dry-run (legacy, no attest) ==="
set +e
dry_out="$("$FINALIZE" 2>"$HARNESS/dry.err")"
dry_ec=$?
set -e
printf '%s\n' "$dry_out" | tee -a "$REPORT"
cat "$HARNESS/dry.err" | tee -a "$REPORT" >/dev/null || true
assert_eq "dry-run exit 0" "0" "$dry_ec"
assert_true "dry-run needs attest" grep -q 'FINALIZE_DRY_RUN_NEEDS_ATTEST' <<<"$dry_out"
assert_true "dry-run metadata unchanged" cmp -s "$HARNESS/before-verified" "$BASELINE/.baseline-verified"
assert_false "dry-run wrote no .baseline" test -f "$BASELINE/.baseline"

log "=== finalize --apply --attest portable ==="
set +e
"$FINALIZE" --apply --attest-requirements-file=requirements.portable.txt \
  >"$HARNESS/apply.out" 2>"$HARNESS/apply.err"
apply_ec=$?
set -e
cat "$HARNESS/apply.out" "$HARNESS/apply.err" | tee -a "$REPORT" >/dev/null || true
assert_eq "apply exit 0" "0" "$apply_ec"
assert_true "apply BASELINE_FINALIZE_OK" grep -q 'BASELINE_FINALIZE_OK' "$HARNESS/apply.out"
assert_true "marker .baseline present" test -f "$BASELINE/.baseline"
assert_true "marker .deploy-ok present" test -f "$BASELINE/.deploy-ok"
assert_true "provenance owner_attested" grep -q '^provenance_source=owner_attested' "$BASELINE/.baseline-verified"
assert_true "requirements_file portable recorded" \
  grep -q '^requirements_file=requirements.portable.txt' "$BASELINE/.baseline-verified"
assert_true "requirements_sha256 recorded matches file" \
  grep -q "^requirements_sha256=${REQ_HASH}$" "$BASELINE/.baseline-verified"
assert_true "full integrity verify after finalize" \
  bash -c 'source "'"$ROOT"'/scripts/deploy/lib.sh"; ETERNAL_RUNTIME_MODE=full eternal_baseline_verify_integrity "'"$BASELINE"'"'
venv_inode_after="$(stat -c '%i' "$BACKEND/.venv")"
assert_eq "fixture .venv inode unchanged (not recreated)" "$venv_inode_before" "$venv_inode_after"
assert_true "fixture .venv still has litellm (no reinstall wipe)" \
  "$BACKEND/.venv/bin/python" -c 'import litellm'
assert_false "CURRENT not created/switched" test -e "$CURRENT/backend"
assert_false "systemctl never invoked" grep -q UNEXPECTED "$HARNESS/apply.err"
assert_true "lock released" flock -n "$LOCK" true

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
log "AFTER requirements_file=$(sed -n 's/^requirements_file=//p' "$BASELINE/.baseline-verified" | head -1)"
log "AFTER provenance_source=$(sed -n 's/^provenance_source=//p' "$BASELINE/.baseline-verified" | head -1)"
log "AFTER requirements_sha256=$(sed -n 's/^requirements_sha256=//p' "$BASELINE/.baseline-verified" | head -1)"
[[ "$FAIL" -eq 0 ]]
