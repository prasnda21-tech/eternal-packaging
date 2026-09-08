"""S3-compatible storage provider (AWS S3, Cloudflare R2, MinIO, etc.).

Uses boto3 (already vendored in requirements.txt). All buckets/objects are
PRIVATE — the app remains the authorization boundary via `/api/files/*`.

### Env vars (nama saja, NEVER logged)
- S3_BUCKET                 (required)
- S3_REGION                 (required for AWS; may be dummy for R2/MinIO)
- S3_ENDPOINT_URL           (optional; set for R2/MinIO/etc.)
- S3_ACCESS_KEY_ID          (required)
- S3_SECRET_ACCESS_KEY      (required)
- S3_PREFIX                 (optional; prepended to every key, e.g. "eternal/")
- S3_FORCE_PATH_STYLE       (optional; "1" for MinIO/etc.)

### Preserves object key semantics
The path passed by callers (e.g. `eternal-packaging/delivery-proof/PO-1050/xx.jpg`)
is used as-is unless `S3_PREFIX` is set, in which case prefix is prepended.
This keeps MongoDB `files.storage_path` records rekonsiliable after migration.
"""
from __future__ import annotations
import os
import logging
from typing import Tuple

from .base import StorageProvider

logger = logging.getLogger(__name__)


class S3StorageProvider(StorageProvider):
    name = "s3"

    def __init__(self) -> None:
        self._client = None
        self._bucket: str | None = None
        self._prefix: str = ""

    def is_configured(self) -> bool:
        return bool(
            os.environ.get("S3_BUCKET")
            and os.environ.get("S3_ACCESS_KEY_ID")
            and os.environ.get("S3_SECRET_ACCESS_KEY")
        )

    def init(self) -> None:
        if self._client is not None:
            return
        if not self.is_configured():
            logger.warning("S3 storage not configured (S3_BUCKET / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY missing).")
            return
        # Import boto3 lazily so environments without S3 aren't required to have it loaded
        import boto3  # noqa: WPS433
        from botocore.config import Config  # noqa: WPS433

        endpoint = os.environ.get("S3_ENDPOINT_URL") or None
        region = os.environ.get("S3_REGION") or "us-east-1"
        force_path_style = os.environ.get("S3_FORCE_PATH_STYLE") == "1"
        cfg = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if force_path_style else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
            config=cfg,
        )
        self._bucket = os.environ["S3_BUCKET"]
        self._prefix = (os.environ.get("S3_PREFIX") or "").strip().lstrip("/")
        # Do NOT log credentials. Only log bucket + endpoint host (not full URL).
        logger.info("S3 provider initialized bucket=%s endpoint=%s prefix=%s",
                    self._bucket, endpoint or "aws-default", self._prefix or "-")

    def _key(self, path: str) -> str:
        p = path.lstrip("/")
        if self._prefix:
            return f"{self._prefix.rstrip('/')}/{p}"
        return p

    def _require_ready(self):
        self.init()
        if self._client is None or not self._bucket:
            raise RuntimeError("S3 storage tidak terkonfigurasi (S3_BUCKET/keys belum di-set)")

    def put_object(self, path: str, data: bytes, content_type: str) -> dict:
        self._require_ready()
        key = self._key(path)
        # Explicit private ACL is not set; buckets should be created with
        # "Bucket Owner Enforced" / no public ACLs. We rely on bucket policy
        # to enforce PRIVATE default.
        resp = self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
        return {
            "path": path,
            "key": key,
            "size": len(data),
            "content_type": content_type,
            "etag": (resp.get("ETag") or "").strip('"'),
        }

    def get_object(self, path: str) -> Tuple[bytes, str]:
        self._require_ready()
        key = self._key(path)
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        body = resp["Body"].read()
        ct = resp.get("ContentType") or "application/octet-stream"
        return body, ct

    def head_object(self, path: str) -> dict:
        self._require_ready()
        key = self._key(path)
        resp = self._client.head_object(Bucket=self._bucket, Key=key)
        return {
            "size": int(resp.get("ContentLength") or 0),
            "content_type": resp.get("ContentType") or "application/octet-stream",
            "etag": (resp.get("ETag") or "").strip('"'),
        }

    def delete_object(self, path: str) -> bool:
        self._require_ready()
        key = self._key(path)
        self._client.delete_object(Bucket=self._bucket, Key=key)
        logger.info("S3 storage deleted %s", key)
        return True
