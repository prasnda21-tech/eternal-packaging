#!/usr/bin/env python3
"""MongoDB backup S3 upload, verification, and retention helper.

Designed for scripts/backup-mongodb.sh and focused mocked unit tests.
Does not invent Biznet quota APIs. Never logs credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from botocore.exceptions import ClientError


SHA256_METADATA_KEY = "sha256"
DEFAULT_EXACT_PREFIX = "production/backups/mongodb/"
DEFAULT_PROTECTED_NAME = "eternal_packaging_2026-09-30_08-11-33.archive.gz"
DEFAULT_S3_KEEP = 3
DEFAULT_SAFETY_RESERVE_BYTES = 0


class BackupS3Error(Exception):
    """Visible backup failure."""


@dataclass
class BackupObject:
    key: str
    size: int
    last_modified: datetime
    sha256: Optional[str] = None

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]


@dataclass
class RetentionPlan:
    protected_keys: list[str] = field(default_factory=list)
    newest_key: Optional[str] = None
    keep_keys: list[str] = field(default_factory=list)
    eligible_delete_keys: list[str] = field(default_factory=list)
    legacy_without_checksum: list[str] = field(default_factory=list)


@dataclass
class BackupConfig:
    bucket: str
    exact_prefix: str = DEFAULT_EXACT_PREFIX
    keep: int = DEFAULT_S3_KEEP
    protected_name: str = DEFAULT_PROTECTED_NAME
    safety_reserve_bytes: int = DEFAULT_SAFETY_RESERVE_BYTES
    assumed_capacity_bytes: Optional[int] = None
    abort_incomplete_multipart: bool = False
    multipart_abort_age_seconds: int = 86400

    def __post_init__(self) -> None:
        prefix = self.exact_prefix.strip()
        if not prefix.endswith("/"):
            prefix += "/"
        self.exact_prefix = prefix
        if self.keep < 1:
            raise BackupS3Error("BACKUP_S3_KEEP must be >= 1")

    @property
    def protected_key(self) -> str:
        return f"{self.exact_prefix}{self.protected_name}"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_s3_stream(
    s3: Any,
    *,
    bucket: str,
    key: str,
    chunk_size: int = 1024 * 1024,
) -> tuple[int, str]:
    """Stream remote object bytes into SHA-256 without writing another disk copy.

    Never uses ETag. Body is read in chunks from the S3 response stream.
    """
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise BackupS3Error(
            f"S3 get_object verify failed: {_client_error_code(exc)}"
        ) from exc
    body = resp["Body"]
    digest = hashlib.sha256()
    total = 0
    read_fn = getattr(body, "read", None)
    if read_fn is None:
        raise BackupS3Error("S3 get_object Body is not readable")
    while True:
        chunk = read_fn(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
    return total, digest.hexdigest()


def _client_error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", "") or "")


def assert_key_under_prefix(key: str, exact_prefix: str) -> None:
    if not key.startswith(exact_prefix):
        raise BackupS3Error(
            f"Refusing S3 operation outside exact backup prefix: {key!r}"
        )
    # Block path traversal / operational prefixes
    remainder = key[len(exact_prefix) :]
    if not remainder or "/" in remainder or remainder.startswith("."):
        raise BackupS3Error(f"Invalid MongoDB backup object key: {key!r}")


def list_backup_objects(s3: Any, bucket: str, exact_prefix: str) -> list[BackupObject]:
    if not exact_prefix.endswith("/"):
        raise BackupS3Error("exact_prefix must end with '/'")
    objects: list[BackupObject] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=exact_prefix):
            for obj in page.get("Contents", []) or []:
                key = obj["Key"]
                if key == exact_prefix:
                    continue
                # Only direct children of the backup prefix (no nested operational paths)
                rel = key[len(exact_prefix) :]
                if not rel or "/" in rel:
                    continue
                if not rel.startswith("eternal_packaging_") or not rel.endswith(
                    ".archive.gz"
                ):
                    continue
                sha = None
                # Prefer listing metadata if present; otherwise leave unknown (legacy OK)
                if "Metadata" in obj and isinstance(obj["Metadata"], dict):
                    sha = obj["Metadata"].get(SHA256_METADATA_KEY)
                objects.append(
                    BackupObject(
                        key=key,
                        size=int(obj.get("Size") or 0),
                        last_modified=obj["LastModified"],
                        sha256=sha,
                    )
                )
    except ClientError as exc:
        raise BackupS3Error(f"S3 listing failed: {_client_error_code(exc)}") from exc
    except Exception as exc:  # noqa: BLE001 — surface listing failures clearly
        raise BackupS3Error(f"S3 listing failed: {exc}") from exc
    return objects


def head_backup_object(s3: Any, bucket: str, key: str, exact_prefix: str) -> BackupObject:
    assert_key_under_prefix(key, exact_prefix)
    try:
        remote = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        raise BackupS3Error(
            f"S3 head_object failed for {key}: {_client_error_code(exc)}"
        ) from exc
    meta = remote.get("Metadata") or {}
    # boto3 lowercases metadata keys
    sha = meta.get(SHA256_METADATA_KEY) or meta.get("Sha256")
    return BackupObject(
        key=key,
        size=int(remote["ContentLength"]),
        last_modified=remote.get("LastModified") or datetime.now(timezone.utc),
        sha256=sha,
    )


def enrich_checksum_metadata(
    s3: Any, bucket: str, objects: Iterable[BackupObject], exact_prefix: str
) -> list[BackupObject]:
    """HEAD objects missing sha256 from listing. Legacy without metadata stay None."""
    enriched: list[BackupObject] = []
    for obj in objects:
        if obj.sha256:
            enriched.append(obj)
            continue
        try:
            headed = head_backup_object(s3, bucket, obj.key, exact_prefix)
            enriched.append(
                BackupObject(
                    key=obj.key,
                    size=headed.size or obj.size,
                    last_modified=obj.last_modified,
                    sha256=headed.sha256,
                )
            )
        except BackupS3Error:
            # Keep legacy object; do not mark invalid or delete
            enriched.append(obj)
    return enriched


def build_retention_plan(
    objects: list[BackupObject],
    *,
    exact_prefix: str,
    keep: int,
    protected_name: str,
    incoming_name: Optional[str] = None,
) -> RetentionPlan:
    protected_present = [o for o in objects if o.name == protected_name]
    protected_keys = [o.key for o in protected_present]

    daily = [o for o in objects if o.name != protected_name]
    daily_sorted = sorted(
        daily,
        key=lambda o: (o.last_modified, o.name),
        reverse=True,
    )

    newest_key = daily_sorted[0].key if daily_sorted else (
        protected_keys[0] if protected_keys else None
    )

    # Newest-first daily names; incoming (just uploaded) is newest.
    ordered_names: list[str] = []
    if incoming_name and incoming_name != protected_name:
        ordered_names.append(incoming_name)
    for obj in daily_sorted:
        if obj.name not in ordered_names:
            ordered_names.append(obj.name)

    daily_keep = ordered_names[:keep]
    keep_keys = [f"{exact_prefix}{n}" for n in daily_keep]
    keep_keys = list(dict.fromkeys(keep_keys + protected_keys))

    keep_set = set(keep_keys)
    sole_remote = len(objects) == 1

    eligible: list[str] = []
    legacy: list[str] = []
    for obj in objects:
        if obj.sha256 is None:
            legacy.append(obj.key)
        if obj.key in keep_set:
            continue
        if sole_remote:
            continue
        eligible.append(obj.key)

    return RetentionPlan(
        protected_keys=protected_keys,
        newest_key=newest_key,
        keep_keys=keep_keys,
        eligible_delete_keys=eligible,
        legacy_without_checksum=legacy,
    )


def pre_upload_eligible_keys(
    objects: list[BackupObject],
    *,
    exact_prefix: str,
    keep: int,
    protected_name: str,
) -> list[str]:
    """Keys safe to delete before upload to free space.

    After upload we keep `keep` newest daily + protected. The new object consumes
    one daily slot, so current daily objects beyond (keep - 1) are post-upload
    surplus and may be pruned pre-upload. Newest and protected and sole backup
    are never eligible.
    """
    if keep < 1:
        return []
    protected = {f"{exact_prefix}{protected_name}"}
    daily = [o for o in objects if o.name != protected_name]
    if len(objects) <= 1:
        return []
    daily_sorted = sorted(
        daily, key=lambda o: (o.last_modified, o.name), reverse=True
    )
    # Preserve up to (keep - 1) current daily backups for post-upload top-N
    preserve_count = max(keep - 1, 0)
    preserve = {o.key for o in daily_sorted[:preserve_count]} | protected
    if daily_sorted:
        preserve.add(daily_sorted[0].key)  # newest always preserved pre-upload

    eligible: list[str] = []
    for obj in daily_sorted[preserve_count:]:
        if obj.key in preserve:
            continue
        if obj.key in protected:
            continue
        eligible.append(obj.key)
    return eligible


def used_backup_bytes(objects: Iterable[BackupObject]) -> int:
    return sum(max(0, o.size) for o in objects)


def capacity_shortfall(
    *,
    used_bytes: int,
    local_size: int,
    safety_reserve_bytes: int,
    assumed_capacity_bytes: Optional[int],
) -> Optional[int]:
    """Return bytes short if assumed capacity is configured; else None (unknown)."""
    if assumed_capacity_bytes is None:
        return None
    available = assumed_capacity_bytes - used_bytes - safety_reserve_bytes
    if available >= local_size:
        return 0
    return local_size - max(0, available)


def delete_keys(
    s3: Any,
    bucket: str,
    keys: Iterable[str],
    exact_prefix: str,
    *,
    protected_keys: Iterable[str] = (),
    refuse_keys: Iterable[str] = (),
) -> list[str]:
    protected = set(protected_keys)
    refuse = set(refuse_keys) | protected
    deleted: list[str] = []
    for key in keys:
        assert_key_under_prefix(key, exact_prefix)
        if key in refuse:
            raise BackupS3Error(f"Refusing to delete protected/refused key: {key}")
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            deleted.append(key)
            print(f"[backup] S3_DELETED: {key}")
        except ClientError as exc:
            raise BackupS3Error(
                f"S3 delete failed for {key}: {_client_error_code(exc)}"
            ) from exc
    return deleted


def object_exists_same_content(
    s3: Any,
    bucket: str,
    key: str,
    exact_prefix: str,
    local_size: int,
    local_sha256: str,
) -> bool:
    assert_key_under_prefix(key, exact_prefix)
    try:
        remote = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = _client_error_code(exc)
        if code in {"404", "NotFound", "NoSuchKey"}:
            return False
        # Some S3 clones use 404 status without standard code
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            return False
        raise BackupS3Error(f"S3 head before upload failed: {code}") from exc

    meta = remote.get("Metadata") or {}
    remote_sha = meta.get(SHA256_METADATA_KEY) or meta.get("Sha256")
    remote_size = int(remote["ContentLength"])
    if remote_size == local_size and remote_sha == local_sha256:
        return True
    if remote_size != local_size or (remote_sha and remote_sha != local_sha256):
        raise BackupS3Error(
            f"Refusing overwrite of existing object with different content: {key}"
        )
    # Same size but missing checksum metadata — do not overwrite
    raise BackupS3Error(
        f"Refusing overwrite of existing object without matching sha256 metadata: {key}"
    )


def upload_with_sha256_metadata(
    s3: Any,
    *,
    local_path: Path,
    bucket: str,
    key: str,
    exact_prefix: str,
    local_sha256: str,
) -> None:
    assert_key_under_prefix(key, exact_prefix)
    extra = {"Metadata": {SHA256_METADATA_KEY: local_sha256}}
    try:
        s3.upload_file(str(local_path), bucket, key, ExtraArgs=extra)
    except TypeError:
        # Fake clients / put_object path
        s3.upload_file(str(local_path), bucket, key, ExtraArgs=extra)
    except ClientError as exc:
        code = _client_error_code(exc)
        raise BackupS3Error(f"S3 upload failed: {code}") from exc


def verify_remote_sha256(
    s3: Any,
    *,
    bucket: str,
    key: str,
    exact_prefix: str,
    local_size: int,
    local_sha256: str,
    require_content_verify: bool = True,
) -> None:
    """Verify a newly uploaded backup against local size and SHA-256.

    Default: stream remote bytes and hash them. Success is never declared from
    HEAD size and uploader-provided metadata alone. Multipart ETag is ignored
    (never treated as MD5). Streaming avoids writing a second large disk copy.
    """
    assert_key_under_prefix(key, exact_prefix)
    remote = head_backup_object(s3, bucket, key, exact_prefix)
    if remote.size != local_size:
        raise BackupS3Error(
            f"Remote size mismatch for {key}: {remote.size} != {local_size}"
        )
    # Metadata is advisory for operators; content stream is authoritative.
    if remote.sha256 and remote.sha256 != local_sha256:
        raise BackupS3Error(f"Remote sha256 metadata mismatch for {key}")

    if not require_content_verify:
        raise BackupS3Error(
            "Refusing success without remote content SHA-256 verification"
        )

    streamed_size, actual = sha256_s3_stream(s3, bucket=bucket, key=key)
    if streamed_size != local_size:
        raise BackupS3Error(
            f"Remote streamed size mismatch for {key}: "
            f"{streamed_size} != {local_size}"
        )
    if actual != local_sha256:
        raise BackupS3Error(f"Remote content sha256 mismatch for {key}")
    print(f"[backup] S3_CONTENT_SHA256_OK: {key}")


def abort_incomplete_multiparts(
    s3: Any,
    *,
    bucket: str,
    exact_prefix: str,
    max_age_seconds: int,
    now: Optional[datetime] = None,
) -> list[str]:
    """Optional cleanup — disabled by default in the shell wrapper."""
    now = now or datetime.now(timezone.utc)
    aborted: list[str] = []
    try:
        resp = s3.list_multipart_uploads(Bucket=bucket, Prefix=exact_prefix)
    except ClientError as exc:
        raise BackupS3Error(
            f"list_multipart_uploads failed: {_client_error_code(exc)}"
        ) from exc
    for upload in resp.get("Uploads", []) or []:
        key = upload["Key"]
        if not key.startswith(exact_prefix):
            continue
        rel = key[len(exact_prefix) :]
        if "/" in rel:
            continue
        initiated = upload.get("Initiated")
        if initiated is not None:
            if initiated.tzinfo is None:
                initiated = initiated.replace(tzinfo=timezone.utc)
            age = (now - initiated).total_seconds()
            if age < max_age_seconds:
                continue
        try:
            s3.abort_multipart_upload(
                Bucket=bucket, Key=key, UploadId=upload["UploadId"]
            )
            aborted.append(key)
            print(f"[backup] MULTIPART_ABORTED: {key}")
        except ClientError as exc:
            raise BackupS3Error(
                f"abort_multipart_upload failed: {_client_error_code(exc)}"
            ) from exc
    return aborted


def prune_until_space(
    s3: Any,
    *,
    bucket: str,
    exact_prefix: str,
    eligible_keys: list[str],
    objects: list[BackupObject],
    need_bytes: int,
    protected_keys: Iterable[str],
    refuse_keys: Iterable[str],
) -> list[str]:
    """Delete oldest eligible first until need_bytes freed or keys exhausted."""
    by_key = {o.key: o for o in objects}
    # eligible_keys expected oldest-last from pre_upload; delete from end (oldest)
    ordered = list(eligible_keys)
    # Sort by last_modified ascending (oldest first)
    ordered.sort(key=lambda k: (by_key[k].last_modified, k) if k in by_key else (datetime.min.replace(tzinfo=timezone.utc), k))
    freed = 0
    deleted: list[str] = []
    for key in ordered:
        if freed >= need_bytes:
            break
        deleted.extend(
            delete_keys(
                s3,
                bucket,
                [key],
                exact_prefix,
                protected_keys=protected_keys,
                refuse_keys=refuse_keys,
            )
        )
        freed += by_key.get(key, BackupObject(key, 0, datetime.now(timezone.utc))).size
    return deleted


def run_s3_backup_phase(
    s3: Any,
    *,
    local_path: Path,
    backup_name: str,
    config: BackupConfig,
    local_sha256: Optional[str] = None,
    require_content_verify: bool = True,
) -> dict[str, Any]:
    """Execute S3 upload + retention. Raises BackupS3Error on failure."""
    if not local_path.is_file():
        raise BackupS3Error(f"Local backup archive missing: {local_path}")
    local_size = local_path.stat().st_size
    if local_size <= 0:
        raise BackupS3Error("Local backup archive is empty")
    digest = local_sha256 or sha256_file(local_path)
    key = f"{config.exact_prefix}{backup_name}"
    assert_key_under_prefix(key, config.exact_prefix)
    if not require_content_verify:
        raise BackupS3Error(
            "Content SHA-256 verification is mandatory for MongoDB backups"
        )

    summary: dict[str, Any] = {
        "status": "FAIL",
        "key": key,
        "local_size": local_size,
        "local_sha256": digest,
        "pre_deleted": [],
        "post_deleted": [],
        "multipart_aborted": [],
        "idempotent": False,
    }

    if config.abort_incomplete_multipart:
        summary["multipart_aborted"] = abort_incomplete_multiparts(
            s3,
            bucket=config.bucket,
            exact_prefix=config.exact_prefix,
            max_age_seconds=config.multipart_abort_age_seconds,
        )

    objects = list_backup_objects(s3, config.bucket, config.exact_prefix)
    objects = enrich_checksum_metadata(s3, config.bucket, objects, config.exact_prefix)
    used = used_backup_bytes(objects)
    protected_key = config.protected_key
    newest = None
    daily = [o for o in objects if o.name != config.protected_name]
    if daily:
        newest = sorted(daily, key=lambda o: (o.last_modified, o.name), reverse=True)[0].key

    eligible = pre_upload_eligible_keys(
        objects,
        exact_prefix=config.exact_prefix,
        keep=config.keep,
        protected_name=config.protected_name,
    )
    refuse = {k for k in [newest, protected_key] if k}

    shortfall = capacity_shortfall(
        used_bytes=used,
        local_size=local_size,
        safety_reserve_bytes=config.safety_reserve_bytes,
        assumed_capacity_bytes=config.assumed_capacity_bytes,
    )

    if shortfall is None:
        print("[backup] S3_QUOTA: unknown (no assumed capacity configured)")
    elif shortfall == 0:
        print("[backup] S3_QUOTA: assumed capacity sufficient for upload")
    else:
        print(f"[backup] S3_QUOTA: shortfall_bytes={shortfall}")
        if not eligible:
            raise BackupS3Error(
                "Insufficient assumed S3 capacity and no safe pre-upload deletions possible"
            )
        summary["pre_deleted"] = prune_until_space(
            s3,
            bucket=config.bucket,
            exact_prefix=config.exact_prefix,
            eligible_keys=eligible,
            objects=objects,
            need_bytes=shortfall,
            protected_keys=[protected_key],
            refuse_keys=refuse,
        )
        objects = list_backup_objects(s3, config.bucket, config.exact_prefix)
        used = used_backup_bytes(objects)
        shortfall = capacity_shortfall(
            used_bytes=used,
            local_size=local_size,
            safety_reserve_bytes=config.safety_reserve_bytes,
            assumed_capacity_bytes=config.assumed_capacity_bytes,
        )
        if shortfall not in (None, 0):
            raise BackupS3Error(
                "Insufficient assumed S3 capacity after safe pre-upload pruning"
            )

    # Idempotent / clobber checks (metadata/size only — content verified below)
    try:
        if object_exists_same_content(
            s3,
            config.bucket,
            key,
            config.exact_prefix,
            local_size,
            digest,
        ):
            summary["idempotent"] = True
            print(f"[backup] S3_OBJECT_EXISTS_SAME_META: {key}")
        else:
            pass
    except BackupS3Error:
        raise

    uploaded_this_run = False

    def _do_upload() -> None:
        nonlocal uploaded_this_run
        upload_with_sha256_metadata(
            s3,
            local_path=local_path,
            bucket=config.bucket,
            key=key,
            exact_prefix=config.exact_prefix,
            local_sha256=digest,
        )
        uploaded_this_run = True

    def _quota_preprune_and_retry(exc: BackupS3Error) -> None:
        print("[backup] S3_UPLOAD QuotaExceeded — attempting safe pre-prune retry")
        objs = list_backup_objects(s3, config.bucket, config.exact_prefix)
        elig = pre_upload_eligible_keys(
            objs,
            exact_prefix=config.exact_prefix,
            keep=config.keep,
            protected_name=config.protected_name,
        )
        daily_objs = [o for o in objs if o.name != config.protected_name]
        newest_key = (
            sorted(daily_objs, key=lambda o: (o.last_modified, o.name), reverse=True)[0].key
            if daily_objs
            else None
        )
        refuse_keys = {k for k in [newest_key, protected_key] if k}
        if not elig:
            raise BackupS3Error(
                "S3 QuotaExceeded and no safe pre-upload deletions possible"
            ) from exc
        summary["pre_deleted"] = prune_until_space(
            s3,
            bucket=config.bucket,
            exact_prefix=config.exact_prefix,
            eligible_keys=elig,
            objects=objs,
            need_bytes=local_size + config.safety_reserve_bytes,
            protected_keys=[protected_key],
            refuse_keys=refuse_keys,
        )
        try:
            _do_upload()
        except BackupS3Error as retry_exc:
            raise BackupS3Error(
                f"S3 upload failed after safe pre-prune: {retry_exc}"
            ) from retry_exc

    def _is_timeout_error(exc: BaseException) -> bool:
        text = str(exc).lower()
        return any(
            token in text
            for token in ("timeout", "timed out", "requesttimeout", "read timeout")
        )

    if not summary["idempotent"]:
        try:
            _do_upload()
        except BackupS3Error as exc:
            msg = str(exc)
            if "QuotaExceeded" in msg or "quota" in msg.lower():
                _quota_preprune_and_retry(exc)
            elif _is_timeout_error(exc):
                # Upload may have completed server-side; verify before retrying.
                print("[backup] S3_UPLOAD timeout — checking whether object landed")
                try:
                    verify_remote_sha256(
                        s3,
                        bucket=config.bucket,
                        key=key,
                        exact_prefix=config.exact_prefix,
                        local_size=local_size,
                        local_sha256=digest,
                        require_content_verify=True,
                    )
                    uploaded_this_run = True
                    print(f"[backup] S3_UPLOAD_OK after timeout verify: {key}")
                except BackupS3Error:
                    print("[backup] S3_UPLOAD timeout — retrying upload once")
                    try:
                        _do_upload()
                    except BackupS3Error as retry_exc:
                        raise BackupS3Error(
                            f"S3 upload failed after timeout retry: {retry_exc}"
                        ) from retry_exc
            else:
                raise

    # Full streamed content verification is mandatory before SUCCESS
    # (including idempotent retries — never trust metadata alone).
    try:
        verify_remote_sha256(
            s3,
            bucket=config.bucket,
            key=key,
            exact_prefix=config.exact_prefix,
            local_size=local_size,
            local_sha256=digest,
            require_content_verify=True,
        )
    except BackupS3Error:
        # Fail closed: remove only an unverified object written this run.
        if uploaded_this_run:
            try:
                delete_keys(
                    s3,
                    config.bucket,
                    [key],
                    config.exact_prefix,
                    protected_keys=[protected_key],
                    refuse_keys=[protected_key],
                )
                print(f"[backup] S3_UNVERIFIED_UPLOAD_REMOVED: {key}")
            except BackupS3Error as cleanup_exc:
                print(f"[backup] S3_UNVERIFIED_CLEANUP_FAILED: {cleanup_exc}")
        raise

    print(f"[backup] S3_UPLOAD_OK: {key}")
    print(f"[backup] SIZE: {local_size} bytes")
    print(f"[backup] SHA256: {digest}")

    # Post-upload retention: keep latest N daily + protected checkpoint
    # Re-list after verify (listing may have changed during upload).
    objects = list_backup_objects(s3, config.bucket, config.exact_prefix)
    objects = enrich_checksum_metadata(s3, config.bucket, objects, config.exact_prefix)
    plan = build_retention_plan(
        objects,
        exact_prefix=config.exact_prefix,
        keep=config.keep,
        protected_name=config.protected_name,
        incoming_name=backup_name,
    )
    keep_set = set(plan.keep_keys) | {key} | set(plan.protected_keys)
    # Always protect checkpoint + verified new key + sole remaining remote
    to_delete = [o.key for o in objects if o.key not in keep_set]
    remaining_after = len(objects) - len(to_delete)
    if remaining_after < 1:
        print("[backup] S3_RETENTION: refuse delete — would leave zero remotes")
        to_delete = []
    # Hard refuse protected + verified new key even if plan drifted
    hard_refuse = {key, protected_key} | set(plan.protected_keys)
    to_delete = [k for k in to_delete if k not in hard_refuse]
    if len(objects) == 1 and objects[0].key in to_delete:
        to_delete = []
    summary["post_deleted"] = delete_keys(
        s3,
        config.bucket,
        to_delete,
        config.exact_prefix,
        protected_keys=list(hard_refuse),
        refuse_keys=[key],
    )
    print(f"[backup] OLD_S3_DELETED: {len(summary['post_deleted'])}")
    print(f"[backup] S3_KEEP: {sorted(keep_set)}")
    summary["status"] = "SUCCESS"
    summary["keep_keys"] = sorted(keep_set)
    return summary


REGULAR_LOCAL_BACKUP_RE = (
    r"^eternal_packaging_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.archive\.gz$"
)


def is_regular_local_backup_name(name: str) -> bool:
    return re.match(REGULAR_LOCAL_BACKUP_RE, name) is not None


def retain_local_backups(
    backup_dir: Path,
    keep: int,
    *,
    is_valid: Optional[Callable[[Path], bool]] = None,
    protected_names: Optional[Iterable[str]] = None,
) -> dict[str, list[str]]:
    """Keep newest `keep` valid regular local MongoDB backup archives.

    Only exact timestamp names
    ``eternal_packaging_YYYY-MM-DD_HH-MM-SS.archive.gz`` are eligible.
    Symlinks, special/migration names, and protected names are never deleted.
    ``LOCAL_DELETED`` semantics: a name is returned in ``deleted`` only after
    the path is verified gone.
    """
    if keep < 1:
        raise BackupS3Error("BACKUP_LOCAL_KEEP must be >= 1")

    protected = {n for n in (protected_names or ()) if n}

    def _default_valid(path: Path) -> bool:
        return (
            path.is_file()
            and not path.is_symlink()
            and path.stat().st_size > 0
        )

    checker = is_valid or _default_valid
    backup_dir = backup_dir.resolve()
    names = sorted(
        (
            p.name
            for p in backup_dir.iterdir()
            if p.is_file()
            and not p.is_symlink()
            and is_regular_local_backup_name(p.name)
            and p.name not in protected
        ),
        reverse=True,
    )
    kept: list[str] = []
    deleted: list[str] = []
    ignored: list[str] = []
    for name in names:
        path = backup_dir / name
        if path.is_symlink() or not path.is_file():
            ignored.append(name)
            continue
        try:
            if path.resolve().parent != backup_dir:
                ignored.append(name)
                continue
        except OSError:
            ignored.append(name)
            continue
        if not checker(path):
            ignored.append(name)
            continue
        if len(kept) < keep:
            kept.append(name)
        else:
            # Fail-closed: never remove a valid file if keep set is empty
            if not kept:
                kept.append(name)
                continue
            path.unlink(missing_ok=True)
            if path.exists() or path.is_symlink():
                raise BackupS3Error(
                    f"Local retention delete did not remove: {name}"
                )
            deleted.append(name)
    if not kept and any(
        p
        for p in backup_dir.glob("eternal_packaging_*.archive.gz")
        if p.is_file() and not p.is_symlink() and is_regular_local_backup_name(p.name)
    ):
        raise BackupS3Error(
            "Local retention refused: would leave zero valid local backups"
        )
    return {"kept": kept, "deleted": deleted, "ignored": ignored}


def build_s3_client_from_env() -> Any:
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT_URL"],
        region_name=os.environ["S3_REGION"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def config_from_env() -> BackupConfig:
    assumed = os.getenv("BACKUP_S3_ASSUMED_CAPACITY_BYTES", "").strip()
    return BackupConfig(
        bucket=os.environ["S3_BUCKET"],
        exact_prefix=os.getenv("BACKUP_S3_EXACT_PREFIX", DEFAULT_EXACT_PREFIX),
        keep=int(os.getenv("BACKUP_S3_KEEP", str(DEFAULT_S3_KEEP))),
        protected_name=os.getenv("BACKUP_S3_PROTECTED_NAME", DEFAULT_PROTECTED_NAME),
        safety_reserve_bytes=int(
            os.getenv("BACKUP_S3_SAFETY_RESERVE_BYTES", str(DEFAULT_SAFETY_RESERVE_BYTES))
        ),
        assumed_capacity_bytes=int(assumed) if assumed else None,
        abort_incomplete_multipart=os.getenv(
            "BACKUP_S3_ABORT_INCOMPLETE_MULTIPART", "0"
        )
        == "1",
        multipart_abort_age_seconds=int(
            os.getenv("BACKUP_S3_MULTIPART_ABORT_AGE_SECONDS", "86400")
        ),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MongoDB backup S3 phase")
    parser.add_argument("--local", required=True, help="Local archive path")
    parser.add_argument("--name", required=True, help="Backup filename")
    parser.add_argument(
        "--skip-content-verify",
        action="store_true",
        help=argparse.SUPPRESS,  # unsupported — content verify is mandatory
    )
    args = parser.parse_args(argv)
    if args.skip_content_verify:
        print(
            "[backup] FAIL: remote content SHA-256 verification cannot be skipped",
            file=sys.stderr,
        )
        return 1

    # Validate exact prefix early — refuse operational prefixes
    prefix = os.getenv("BACKUP_S3_EXACT_PREFIX", DEFAULT_EXACT_PREFIX)
    if not prefix.endswith("/"):
        prefix += "/"
    if prefix != DEFAULT_EXACT_PREFIX and os.getenv(
        "BACKUP_S3_ALLOW_NONDEFAULT_PREFIX", "0"
    ) != "1":
        print(
            f"[backup] FAIL: BACKUP_S3_EXACT_PREFIX must be {DEFAULT_EXACT_PREFIX!r} "
            f"unless BACKUP_S3_ALLOW_NONDEFAULT_PREFIX=1",
            file=sys.stderr,
        )
        return 1

    try:
        cfg = config_from_env()
        # Force normalized prefix check against operational paths
        if "backups/mongodb/" not in cfg.exact_prefix:
            raise BackupS3Error("Refusing non-mongodb backup prefix")
        s3 = build_s3_client_from_env()
        local_sha = os.getenv("BACKUP_LOCAL_SHA256", "").strip() or None
        summary = run_s3_backup_phase(
            s3,
            local_path=Path(args.local),
            backup_name=args.name,
            config=cfg,
            local_sha256=local_sha,
            require_content_verify=True,
        )
        print(f"[backup] S3_PHASE_STATUS: {summary['status']}")
        return 0 if summary["status"] == "SUCCESS" else 1
    except BackupS3Error as exc:
        print(f"[backup] FAIL: {exc}", file=sys.stderr)
        return 1
    except ClientError as exc:
        # Never print credentials; code only
        print(f"[backup] FAIL: ClientError {_client_error_code(exc)}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"[backup] FAIL: missing environment variable {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
