# shellcheck shell=bash
# Safe deploy defaults for Eternal Packaging production.
# Override via environment. Never put secrets here.

# Paths (production layout)
: "${APP_ROOT:=/opt/eternal-packaging}"
: "${BACKEND_DIR:=${APP_ROOT}/backend}"
: "${FRONTEND_DIR:=${APP_ROOT}/frontend}"
: "${WEBROOT:=/var/www/eternal-packaging}"
: "${RELEASES_ROOT:=/var/www/eternal-packaging-releases}"
: "${PREVIOUS_WEBROOT:=/var/www/eternal-packaging-previous}"
: "${DEPLOY_LOCK_FILE:=/var/lock/eternal-packaging-deploy.lock}"
: "${DEPLOY_HISTORY_DIR:=/var/log/eternal-packaging-deploy}"
: "${WORKTREE_ROOT:=/opt/eternal-packaging/releases}"

# Services / health
: "${BACKEND_SERVICE:=eternal-packaging-backend}"
: "${NGINX_SERVICE:=nginx}"
: "${HEALTH_URL:=http://127.0.0.1:8000/api/system/health}"
: "${FRONTEND_HEALTH_URL:=http://127.0.0.1/login}"
: "${FRONTEND_HTTPS_URL:=https://127.0.0.1/login}"

# Resource guards (FAIL CLOSED if below / above thresholds)
: "${MIN_AVAIL_MEM_MB:=1536}"          # require >= 1.5 GiB available
: "${MAX_LOAD_PER_CPU:=2.0}"           # load1 / nproc
: "${MIN_DISK_AVAIL_MB:=4096}"         # on APP_ROOT filesystem

# Timeouts (seconds)
: "${SSH_CONNECT_TIMEOUT:=15}"
: "${SSH_COMMAND_TIMEOUT:=120}"
: "${PREFLIGHT_TIMEOUT:=90}"
: "${DATA_PREFLIGHT_TIMEOUT:=60}"
: "${FRONTEND_BUILD_TIMEOUT:=900}"
: "${HEALTH_TIMEOUT:=30}"
: "${HEALTH_RETRIES:=8}"
: "${HEALTH_RETRY_SLEEP:=2}"
: "${CUTOVER_STEP_TIMEOUT:=60}"
: "${LOCK_STALE_SECONDS:=7200}"        # 2h — stale lock reclaimable

# Critical SKU check (read-only)
: "${CRITICAL_SKU:=100442}"
: "${CRITICAL_SKU_MATERIAL_ID:=cf11da27-f4e8-484b-9fd0-b18aac58e5e8}"

# Behavior flags
: "${DRY_RUN:=0}"
: "${SKIP_FRONTEND_BUILD:=0}"         # 1 if FRONTEND_ARTIFACT_DIR provided
: "${ALLOW_DIRTY_WORKTREE:=0}"
: "${REQUIRE_CLEAN_MAIN_COLLISION_CHECK:=1}"
