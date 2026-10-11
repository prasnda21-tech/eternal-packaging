#!/usr/bin/env bash
# SSH forced-command probe helper for GitHub Actions (and local mocks).
# Sends ONLY the literal remote command: probe
# Never invokes deploy runner, shell, build, or publish.
#
# Required env: DEPLOY_HOST DEPLOY_USER DEPLOY_SSH_KEY DEPLOY_KNOWN_HOSTS
# Optional: DEPLOY_SSH_PORT (default 22), ETERNAL_SSH_BIN (default ssh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

SSH_BIN="${ETERNAL_SSH_BIN:-ssh}"
port="${DEPLOY_SSH_PORT:-22}"

[[ -n "${DEPLOY_HOST:-}" ]] || eternal_deploy_die "DEPLOY_HOST missing"
[[ -n "${DEPLOY_USER:-}" ]] || eternal_deploy_die "DEPLOY_USER missing"
[[ -n "${DEPLOY_SSH_KEY:-}" ]] || eternal_deploy_die "DEPLOY_SSH_KEY missing"
[[ -n "${DEPLOY_KNOWN_HOSTS:-}" ]] || eternal_deploy_die "DEPLOY_KNOWN_HOSTS missing"

key_file="$(mktemp)"
known_hosts="$(mktemp)"
cleanup() {
  rm -f "$key_file" "$known_hosts"
}
trap cleanup EXIT

printf '%s\n' "$DEPLOY_SSH_KEY" >"$key_file"
printf '%s\n' "$DEPLOY_KNOWN_HOSTS" >"$known_hosts"
chmod 600 "$key_file" "$known_hosts"

set +e
out="$("$SSH_BIN" -i "$key_file" \
  -p "$port" \
  -o IdentitiesOnly=yes \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$known_hosts" \
  -o ConnectTimeout=15 \
  "${DEPLOY_USER}@${DEPLOY_HOST}" \
  probe 2>&1)"
ec=$?
set -e

# Cleanup key material before exit paths (trap also runs).
cleanup
trap - EXIT

if [[ "$ec" -ne 0 ]]; then
  eternal_safe_echo "ssh probe failed exit=$ec out=$out"
  eternal_deploy_die "SSH probe failed (host-key/auth/gate). No deploy fallback."
fi

# Sanitize: never echo key material; probe output should be short status lines.
sanitized="$(eternal_redact_line "$out")"
printf '%s\n' "$sanitized"

if ! eternal_probe_output_accepted "$out"; then
  eternal_deploy_die "probe output not accepted; refusing to continue"
fi

printf 'SSH_PROBE_OK gate_accepted=1\n'
