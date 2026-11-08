#!/usr/bin/env bash
# Idempotent Cloud Agent install for Eternal Packaging.
#
# Prepares everything an agent needs after the repository is checked out:
#   * system dependencies (MongoDB server + tools, python venv support)
#   * backend Python virtualenv and dependencies
#   * frontend yarn dependencies
#   * mobile npm dependencies (best-effort; not required for web e2e)
#   * local .env files with sane development defaults
#
# Safe to run repeatedly and to bake into an environment build.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Ensuring system dependencies (MongoDB, python venv)"
if ! command -v mongod >/dev/null 2>&1; then
  curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc \
    | sudo gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor --yes
  echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] https://repo.mongodb.org/apt/ubuntu noble/mongodb-org/8.0 multiverse" \
    | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list >/dev/null
  sudo apt-get update -y
fi
sudo apt-get install -y mongodb-org python3-venv

echo "==> Preparing MongoDB data directory"
sudo mkdir -p /data/db
sudo chown -R "$(id -u):$(id -g)" /data/db

echo "==> Backend: Python virtualenv + dependencies"
cd "$REPO_ROOT/backend"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.portable.txt

if [ ! -f "$REPO_ROOT/backend/.env" ]; then
  echo "==> Writing backend/.env (local dev defaults)"
  cat > "$REPO_ROOT/backend/.env" <<'EOF'
MONGO_URL=mongodb://localhost:27017
DB_NAME=eternal_packaging
JWT_SECRET=dev-local-secret-please-change-0123456789abcdef
CORS_ORIGINS=http://localhost:3000,http://localhost:3002
ACCURATE_API_TOKEN=
ACCURATE_DB_ID=
ACCURATE_API_SIGNATURE_SECRET=
STORAGE_PROVIDER=local
STORAGE_MIGRATE_LIVE=0
TRIAL_DEFAULT_PASSWORD=Eternal123!
SEED_DEMO_ORDERS=1
PLACES_PROVIDER=mock
GOOGLE_PLACES_API_KEY=
EOF
fi

echo "==> Frontend: yarn dependencies"
cd "$REPO_ROOT/frontend"
corepack yarn install --frozen-lockfile

if [ ! -f "$REPO_ROOT/frontend/.env" ]; then
  echo "==> Writing frontend/.env (local dev defaults)"
  cat > "$REPO_ROOT/frontend/.env" <<'EOF'
REACT_APP_BACKEND_URL=http://localhost:8000
PORT=3000
REACT_APP_GOOGLE_MAPS_BROWSER_KEY=
EOF
fi

echo "==> Mobile: npm dependencies (best-effort)"
cd "$REPO_ROOT/mobile"
if [ -f package-lock.json ]; then
  npm ci || npm install || echo "!! mobile deps install failed (non-fatal for web e2e)"
else
  npm install || echo "!! mobile deps install failed (non-fatal for web e2e)"
fi

if [ ! -f "$REPO_ROOT/mobile/.env" ]; then
  echo "==> Writing mobile/.env (local dev defaults)"
  cat > "$REPO_ROOT/mobile/.env" <<'EOF'
EXPO_PUBLIC_API_URL=http://localhost:8000
EOF
fi

echo "==> Install complete"
