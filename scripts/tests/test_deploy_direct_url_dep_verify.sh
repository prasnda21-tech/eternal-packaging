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
HASH_HEX='adf398c513273de9341f61822296c6b2145f7f2dc4a69daf3ac04829f5bde3f8'
HASH="sha256=${HASH_HEX}"
REQ_LINE="litellm @ ${URL}#${HASH}"
REQ_LINE_NOFRAG="litellm @ ${URL}"

# mode: legacy_hash | hashes | none
write_dist() {
  local purelib="$1"
  local url="${2:-$URL}"
  local mode="${3:-legacy_hash}"
  local hash_hex="${4:-$HASH_HEX}"
  local dist="$purelib/litellm-1.80.0.dist-info"
  mkdir -p "$dist"
  cat >"$dist/METADATA" <<'EOF'
Metadata-Version: 2.1
Name: litellm
Version: 1.80.0
EOF
  case "$mode" in
    legacy_hash)
      cat >"$dist/direct_url.json" <<EOF
{"url": "${url}", "archive_info": {"hash": "sha256=${hash_hex}"}}
EOF
      ;;
    hashes)
      cat >"$dist/direct_url.json" <<EOF
{"url": "${url}", "archive_info": {"hashes": {"sha256": "${hash_hex}"}}}
EOF
      ;;
    none)
      ;;
    url_only)
      cat >"$dist/direct_url.json" <<EOF
{"url": "${url}"}
EOF
      ;;
    *) log "FAIL: bad write_dist mode=$mode"; FAIL=$((FAIL+1)); return 1 ;;
  esac
}

# --- URL match PASS (legacy archive_info.hash) ---
PURE_OK="$WORKDIR/ok/site"
mkdir -p "$PURE_OK"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/ok/requirements.txt"
write_dist "$PURE_OK" "$URL" legacy_hash
assert_true "direct URL match PASS (legacy hash)" \
  python3 "$VERIFIER" "$WORKDIR/ok/requirements.txt" --purelib "$PURE_OK"

# --- URL match PASS (archive_info.hashes) ---
PURE_HASHES="$WORKDIR/hashes/site"
mkdir -p "$PURE_HASHES"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/hashes/requirements.txt"
write_dist "$PURE_HASHES" "$URL" hashes
assert_true "direct URL match PASS (archive_info.hashes)" \
  python3 "$VERIFIER" "$WORKDIR/hashes/requirements.txt" --purelib "$PURE_HASHES"

# --- URL without fragment: url match only PASS ---
PURE_NOFRAG="$WORKDIR/nofrag/site"
mkdir -p "$PURE_NOFRAG"
printf '%s\n' "$REQ_LINE_NOFRAG" >"$WORKDIR/nofrag/requirements.txt"
write_dist "$PURE_NOFRAG" "$URL" url_only
assert_true "direct URL without fragment PASS when url matches" \
  python3 "$VERIFIER" "$WORKDIR/nofrag/requirements.txt" --purelib "$PURE_NOFRAG"

# --- URL without fragment but wrong url FAIL ---
PURE_NOFRAG_BAD="$WORKDIR/nofrag-bad/site"
mkdir -p "$PURE_NOFRAG_BAD"
printf '%s\n' "$REQ_LINE_NOFRAG" >"$WORKDIR/nofrag-bad/requirements.txt"
write_dist "$PURE_NOFRAG_BAD" 'https://example.invalid/other.whl' url_only
err_nf="$(python3 "$VERIFIER" "$WORKDIR/nofrag-bad/requirements.txt" --purelib "$PURE_NOFRAG_BAD" 2>&1 || true)"
assert_false "direct URL without fragment fails on url mismatch" \
  python3 "$VERIFIER" "$WORKDIR/nofrag-bad/requirements.txt" --purelib "$PURE_NOFRAG_BAD"
assert_true "nofrag mismatch mentions direct URL mismatch" \
  grep -q 'direct URL mismatch for litellm' <<<"$err_nf"

# --- package missing FAIL ---
PURE_MISS="$WORKDIR/miss/site"
mkdir -p "$PURE_MISS"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/miss/requirements.txt"
err_miss="$(python3 "$VERIFIER" "$WORKDIR/miss/requirements.txt" --purelib "$PURE_MISS" 2>&1 || true)"
assert_false "package missing fails" \
  python3 "$VERIFIER" "$WORKDIR/miss/requirements.txt" --purelib "$PURE_MISS"
assert_true "package missing mentions not installed" \
  grep -q 'package not installed: litellm' <<<"$err_miss"

# --- URL different FAIL ---
PURE_DIFF="$WORKDIR/diff/site"
mkdir -p "$PURE_DIFF"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/diff/requirements.txt"
write_dist "$PURE_DIFF" 'https://example.invalid/other-litellm.whl' legacy_hash
err_diff="$(python3 "$VERIFIER" "$WORKDIR/diff/requirements.txt" --purelib "$PURE_DIFF" 2>&1 || true)"
assert_false "URL mismatch fails" \
  python3 "$VERIFIER" "$WORKDIR/diff/requirements.txt" --purelib "$PURE_DIFF"
assert_true "URL mismatch mentions direct URL mismatch" \
  grep -q 'direct URL mismatch for litellm' <<<"$err_diff"

# --- direct_url.json missing FAIL ---
PURE_NOD="$WORKDIR/nodirect/site"
mkdir -p "$PURE_NOD"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/nodirect/requirements.txt"
write_dist "$PURE_NOD" "$URL" none
err_nod="$(python3 "$VERIFIER" "$WORKDIR/nodirect/requirements.txt" --purelib "$PURE_NOD" 2>&1 || true)"
assert_false "missing direct_url.json fails" \
  python3 "$VERIFIER" "$WORKDIR/nodirect/requirements.txt" --purelib "$PURE_NOD"
assert_true "missing metadata mentions direct_url.json missing" \
  grep -q 'direct_url.json missing for litellm' <<<"$err_nod"

# --- hashes format present but wrong digest FAIL ---
PURE_HASH="$WORKDIR/hash/site"
mkdir -p "$PURE_HASH"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/hash/requirements.txt"
write_dist "$PURE_HASH" "$URL" hashes '0000000000000000000000000000000000000000000000000000000000000000'
err_hash="$(python3 "$VERIFIER" "$WORKDIR/hash/requirements.txt" --purelib "$PURE_HASH" 2>&1 || true)"
assert_false "hashes digest mismatch fails" \
  python3 "$VERIFIER" "$WORKDIR/hash/requirements.txt" --purelib "$PURE_HASH"
assert_true "hashes mismatch mentions hash mismatch" \
  grep -q 'direct URL hash mismatch for litellm' <<<"$err_hash"

# --- requirement asks for hash but only url in direct_url (no hash fields) FAIL ---
PURE_NEEDHASH="$WORKDIR/needhash/site"
mkdir -p "$PURE_NEEDHASH"
printf '%s\n' "$REQ_LINE" >"$WORKDIR/needhash/requirements.txt"
write_dist "$PURE_NEEDHASH" "$URL" url_only
err_nh="$(python3 "$VERIFIER" "$WORKDIR/needhash/requirements.txt" --purelib "$PURE_NEEDHASH" 2>&1 || true)"
assert_false "required hash missing from direct_url fails" \
  python3 "$VERIFIER" "$WORKDIR/needhash/requirements.txt" --purelib "$PURE_NEEDHASH"
assert_true "missing required hash mentions archive_info.hashes" \
  grep -q 'archive_info.hashes' <<<"$err_nh"

# lib.sh wires verifier (not freeze-grep for URL lines)
assert_true "lib.sh invokes verify_requirements_installed.py" \
  grep -q 'verify_requirements_installed.py' "$ROOT/scripts/deploy/lib.sh"
assert_false "lib.sh no longer rejects URL as unsupported pin-only" \
  grep -q 'unsupported requirements line (need name==version)' "$ROOT/scripts/deploy/lib.sh"

log ""
log "SUMMARY pass=$PASS fail=$FAIL"
[[ "$FAIL" -eq 0 ]]
