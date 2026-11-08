#!/usr/bin/env bash
# Per-boot startup for Eternal Packaging: bring up MongoDB and wait until it is
# ready to accept connections. Idempotent and safe to re-run.
set -euo pipefail

sudo mkdir -p /data/db
sudo chown -R "$(id -u):$(id -g)" /data/db 2>/dev/null || true

if ! pgrep -x mongod >/dev/null 2>&1; then
  echo "==> Starting mongod"
  mongod --dbpath /data/db --bind_ip 127.0.0.1 --port 27017 \
    --fork --logpath "$HOME/mongod.log"
else
  echo "==> mongod already running"
fi

echo "==> Waiting for MongoDB to accept connections"
for _ in $(seq 1 30); do
  if mongosh --quiet --eval 'db.runCommand({ ping: 1 })' >/dev/null 2>&1; then
    echo "==> MongoDB is ready"
    exit 0
  fi
  sleep 1
done

echo "!! MongoDB did not become ready in time" >&2
exit 1
