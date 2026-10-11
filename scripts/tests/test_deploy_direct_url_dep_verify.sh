#!/usr/bin/env bash
# Targeted proofs for PEP 508/610 direct-URL dependency verification.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VERIFIER="$ROOT/scripts/deploy/verify_requirements_installed.py"
PASS=0
FAIL=0

log() { printf '%s\n' "$*"; }
assert_true() {
  local label="$1"; shift
  if "$@"; then log "PASS: $label"; PASS=$((PASS+1))
  else log "FAIL: $label"; FAIL=$((FAIL+1)); fi
}
assert_false() {
  local label="$1"; shift
  if "$@"; then log "FAIL: $label (expected failure)"; FAIL=$((FAIL+1))
  else log "PASS: $label"; PASS=$((PASS+1)); fi
}

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

URL='https://customer-assets.emergentagent.com/internal-asset/library/litellm-1.80.0-py3-none-any.whl'
HASH='sha256=adf398c513273de9341f61822296c6b2145f7f2dc4a69daf3ac04829f5bde3f8'
REQ_LINE="litellm @ ${URL}#${HASH}"

write_dist() {
  local purelib="$1"
  local url="${2:-$URL}"
  local hash="${3:-$HASH}"
  local with_direct="${4:-1}"
  local dist="$purelib/litellm-1.80.0.dist-info"
  mkdir -p "$dist"
  cat >"$dist/METADATA" <<'EOF'
Metadata-Version: 2.1
Name: litellm
Version: 1.80.0
EOF
  if [[ "$with_direct" == "1" ]]; then
    cat >"$dist/direct_url.json" <<EOF
{"url": "${url}", "archive_info": {"hash": "${hash}"}}
EOF
  fi
}

# --- URL match PASS ---
PURE_OK="$WORKDIR/ok/site"
mkdir -p "$PURE_OK"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/ok/requirements.txt"
write_dist "$PURE_OK"
assert_true "direct URL match PASS" \
  python3 "$VERIFIER" "$WORKDIR/ok/requirements.txt" --purelib "$PURE_OK"

# --- package missing FAIL ---
PURE_MISS="$WORKDIR/miss/site"
mkdir -p "$PURE_MISS"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/miss/requirements.txt"
# empty site-packages (no litellm)
err_miss="$(python3 "$VERIFIER" "$WORKDIR/miss/requirements.txt" --purelib "$PURE_MISS" 2>&1 || true)"
assert_false "package missing fails" \
  python3 "$VERIFIER" "$WORKDIR/miss/requirements.txt" --purelib "$PURE_MISS"
assert_true "package missing mentions not installed" \
  grep -q 'package not installed: litellm' <<<"$err_miss"

# --- URL different FAIL ---
PURE_DIFF="$WORKDIR/diff/site"
mkdir -p "$PURE_DIFF"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/diff/requirements.txt"
write_dist "$PURE_DIFF" 'https://example.invalid/other-litellm.whl' "$HASH"
err_diff="$(python3 "$VERIFIER" "$WORKDIR/diff/requirements.txt" --purelib "$PURE_DIFF" 2>&1 || true)"
assert_false "URL mismatch fails" \
  python3 "$VERIFIER" "$WORKDIR/diff/requirements.txt" --purelib "$PURE_DIFF"
assert_true "URL mismatch mentions direct URL mismatch" \
  grep -q 'direct URL mismatch for litellm' <<<"$err_diff"

# --- direct_url.json missing FAIL ---
PURE_NOD="$WORKDIR/nodirect/site"
mkdir -p "$PURE_NOD"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/nodirect/requirements.txt"
write_dist "$PURE_NOD" "$URL" "$HASH" 0
err_nod="$(python3 "$VERIFIER" "$WORKDIR/nodirect/requirements.txt" --purelib "$PURE_NOD" 2>&1 || true)"
assert_false "missing direct_url.json fails" \
  python3 "$VERIFIER" "$WORKDIR/nodirect/requirements.txt" --purelib "$PURE_NOD"
assert_true "missing metadata mentions direct_url.json missing" \
  grep -q 'direct_url.json missing for litellm' <<<"$err_nod"

# --- hash mismatch FAIL ---
PURE_HASH="$WORKDIR/hash/site"
mkdir -p "$PURE_HASH"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/hash/requirements.txt"
write_dist "$PURE_HASH" "$URL" 'sha256=0000000000000000000000000000000000000000000000000000000000000000'
err_hash="$(python3 "$VERIFIER" "$WORKDIR/hash/requirements.txt" --purelib "$PURE_HASH" 2>&1 || true)"
assert_false "hash mismatch fails" \
  python3 "$VERIFIER" "$WORKDIR/hash/requirements.txt" --purelib "$PURE_HASH"
assert_true "hash mismatch mentions hash mismatch" \
  grep -q 'direct URL hash mismatch for litellm' <<<"$err_hash"

# lib.sh wires verifier (not freeze-grep for URL lines)
assert_true "lib.sh invokes verify_requirements_installed.py" \
  grep -q 'verify_requirements_installed.py' "$ROOT/scripts/deploy/lib.sh"
assert_false "lib.sh no longer rejects URL as unsupported pin-only" \
  grep -q 'unsupported requirements line (need name==version)' "$ROOT/scripts/deploy/lib.sh"

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
