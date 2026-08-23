#!/usr/bin/env bash
set -euo pipefail

BACKEND="/opt/eternal-packaging/backend"
BACKUP_DIR="/var/backups/eternal-packaging"
STAMP="$(date +'%Y-%m-%d_%H-%M-%S')"
FILE="eternal_packaging_${STAMP}.archive.gz"
LOCAL="${BACKUP_DIR}/${FILE}"

cd "$BACKEND"

set -a
source .env
set +a

echo "[backup] Starting MongoDB backup: $FILE"

mongodump \
  --uri="$MONGO_URL" \
  --db="$DB_NAME" \
  --archive="$LOCAL" \
  --gzip

gzip -t "$LOCAL"

export BACKUP_FILE="$LOCAL"
export BACKUP_NAME="$FILE"

.venv/bin/python - <<'PY'
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta

import boto3
from botocore.config import Config

local = Path(os.environ["BACKUP_FILE"])
name = os.environ["BACKUP_NAME"]

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"],
    region_name=os.environ["S3_REGION"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
    config=Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"}
    )
)

bucket = os.environ["S3_BUCKET"]
prefix = os.getenv("S3_PREFIX", "production").strip("/")
key = f"{prefix}/backups/mongodb/{name}"

s3.upload_file(str(local), bucket, key)

remote = s3.head_object(Bucket=bucket, Key=key)
assert remote["ContentLength"] == local.stat().st_size

print(f"[backup] S3_UPLOAD_OK: {key}")
print(f"[backup] SIZE: {local.stat().st_size} bytes")

# Retensi backup S3: 30 hari
cutoff = datetime.now(timezone.utc) - timedelta(days=30)
backup_prefix = f"{prefix}/backups/mongodb/"

paginator = s3.get_paginator("list_objects_v2")
deleted = 0

for page in paginator.paginate(Bucket=bucket, Prefix=backup_prefix):
    for obj in page.get("Contents", []):
        if obj["LastModified"] < cutoff:
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
            deleted += 1

print(f"[backup] OLD_S3_DELETED: {deleted}")
PY

# Simpan backup lokal maksimum 3 hari
find "$BACKUP_DIR" \
  -type f \
  -name 'eternal_packaging_*.archive.gz' \
  -mtime +3 \
  -delete

echo "[backup] BACKUP_COMPLETE"
