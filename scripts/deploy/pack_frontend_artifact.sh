#!/usr/bin/env bash
# Pack a verified frontend/build tree into an immutable artifact + manifest.
# Fail-closed. Never prints secret values. Never includes server-side .env files.
#
# Usage:
#   pack_frontend_artifact.sh \
#     --frontend-dir frontend \
#     --out-dir dist/frontend-artifact \
#     --git-sha <40-hex> \
#     --workflow frontend-artifact.yml \
#     --run-id <id> \
#     --run-attempt <n>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

FRONTEND_DIR=""
OUT_DIR=""
GIT_SHA=""
WORKFLOW="frontend-artifact.yml"
RUN_ID="${GITHUB_RUN_ID:-0}"
RUN_ATTEMPT="${GITHUB_RUN_ATTEMPT:-1}"
NODE_VERSION=""
YARN_VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --frontend-dir) FRONTEND_DIR="${2:-}"; shift 2 ;;
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    --git-sha) GIT_SHA="${2:-}"; shift 2 ;;
    --workflow) WORKFLOW="${2:-}"; shift 2 ;;
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --run-attempt) RUN_ATTEMPT="${2:-}"; shift 2 ;;
    --node-version) NODE_VERSION="${2:-}"; shift 2 ;;
    --yarn-version) YARN_VERSION="${2:-}"; shift 2 ;;
    *) eternal_deploy_die "unknown arg: $1" ;;
  esac
done

[[ -n "$FRONTEND_DIR" && -d "$FRONTEND_DIR" ]] || eternal_deploy_die "--frontend-dir missing"
[[ -n "$OUT_DIR" ]] || eternal_deploy_die "--out-dir missing"
eternal_require_sha40 "$GIT_SHA"
[[ -f "$FRONTEND_DIR/build/index.html" ]] \
  || eternal_deploy_die "frontend/build/index.html missing; build before packing"
[[ -f "$FRONTEND_DIR/build/asset-manifest.json" ]] \
  || eternal_deploy_die "frontend/build/asset-manifest.json missing"

mkdir -p "$OUT_DIR"
ARCHIVE_NAME="frontend-build-${GIT_SHA}.tar.gz"
ARCHIVE_PATH="$OUT_DIR/$ARCHIVE_NAME"
MANIFEST_PATH="$OUT_DIR/manifest.json"

# Archive only build outputs; paths relative to build/ (index.html at archive root).
tar -C "$FRONTEND_DIR/build" \
  --owner=0 --group=0 --numeric-owner \
  --mtime="@0" \
  -czf "$ARCHIVE_PATH" .

artifact_sha256="$(sha256sum "$ARCHIVE_PATH" | awk '{print $1}')"
index_sha256="$(sha256sum "$FRONTEND_DIR/build/index.html" | awk '{print $1}')"
asset_manifest_sha256="$(sha256sum "$FRONTEND_DIR/build/asset-manifest.json" | awk '{print $1}')"
input_fp="$(eternal_frontend_input_fingerprint "$FRONTEND_DIR")"
env_fp="$(eternal_frontend_public_env_fingerprint)"

if [[ -z "$NODE_VERSION" ]]; then
  NODE_VERSION="$(node -v 2>/dev/null | tr -d 'v' || true)"
fi
if [[ -z "$YARN_VERSION" ]]; then
  YARN_VERSION="$(yarn -v 2>/dev/null || true)"
fi

pkg_manager=""
if [[ -f "$FRONTEND_DIR/package.json" ]] && command -v python3 >/dev/null 2>&1; then
  pkg_manager="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("packageManager",""))' "$FRONTEND_DIR/package.json")"
fi

export GIT_SHA WORKFLOW
export RUN_ID RUN_ATTEMPT
export ARCHIVE_NAME
export ARTIFACT_SHA256="$artifact_sha256"
export INDEX_SHA256="$index_sha256"
export ASSET_MANIFEST_SHA256="$asset_manifest_sha256"
export INPUT_FP="$input_fp"
export ENV_FP="$env_fp"
export NODE_VERSION YARN_VERSION
export PKG_MANAGER="$pkg_manager"
export RETENTION_DAYS="${ETERNAL_FRONTEND_ARTIFACT_RETENTION_DAYS:-30}"

python3 - "$MANIFEST_PATH" <<'PY'
import json, os, sys
path = sys.argv[1]
doc = {
  "schema": "eternal.frontend-artifact.v1",
  "git_sha": os.environ["GIT_SHA"],
  "workflow": os.environ["WORKFLOW"],
  "run_id": str(os.environ["RUN_ID"]),
  "run_attempt": str(os.environ["RUN_ATTEMPT"]),
  "node": os.environ.get("NODE_VERSION", ""),
  "yarn": os.environ.get("YARN_VERSION", ""),
  "package_manager_field": os.environ.get("PKG_MANAGER", ""),
  "build_command": "yarn build",
  "archive_name": os.environ["ARCHIVE_NAME"],
  "artifact_sha256": os.environ["ARTIFACT_SHA256"],
  "index_sha256": os.environ["INDEX_SHA256"],
  "asset_manifest_sha256": os.environ["ASSET_MANIFEST_SHA256"],
  "input_fingerprint_sha256": os.environ["INPUT_FP"],
  "env_fingerprint_sha256": os.environ["ENV_FP"],
  "retention_days": int(os.environ.get("RETENTION_DAYS", "30")),
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2, sort_keys=True)
    f.write("\n")
PY

manifest_sha256="$(sha256sum "$MANIFEST_PATH" | awk '{print $1}')"
printf 'FRONTEND_ARTIFACT_OK archive=%s artifact_sha256=%s manifest_sha256=%s input_fp=%s env_fp=%s\n' \
  "$ARCHIVE_NAME" "$artifact_sha256" "$manifest_sha256" "$input_fp" "$env_fp"
