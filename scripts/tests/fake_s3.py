"""In-memory S3 double for MongoDB backup tests. No network."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from botocore.exceptions import ClientError


def _error(code: str, message: str = "", status: int = 400) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": message or code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "FakeS3",
    )


@dataclass
class _Obj:
    body: bytes
    last_modified: datetime
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class _Multipart:
    key: str
    upload_id: str
    initiated: datetime


class FakeS3:
    """Minimal S3 client double covering backup helper operations."""

    def __init__(self) -> None:
        self.objects: dict[str, dict[str, _Obj]] = {}
        self.multiparts: list[_Multipart] = []
        self.quota_bytes: Optional[int] = None
        self.fail_list = False
        self.fail_upload_once = False
        self.fail_upload_always = False
        self.fail_upload_timeout_once = False
        self.upload_timeout_but_store = False
        self.upload_calls = 0
        self.deleted_keys: list[str] = []
        self.aborted_multiparts: list[str] = []
        self.corrupt_metadata_on_head_for: Optional[str] = None
        self.corrupt_body_after_upload_for: Optional[str] = None
        self.list_mutate_remove_key: Optional[str] = None
        self._list_calls = 0

    def _bucket(self, bucket: str) -> dict[str, _Obj]:
        return self.objects.setdefault(bucket, {})

    def put_bytes(
        self,
        bucket: str,
        key: str,
        body: bytes,
        *,
        metadata: Optional[dict[str, str]] = None,
        last_modified: Optional[datetime] = None,
    ) -> None:
        self._bucket(bucket)[key] = _Obj(
            body=body,
            last_modified=last_modified or datetime.now(timezone.utc),
            metadata={k.lower(): v for k, v in (metadata or {}).items()},
        )

    def list_objects_v2(self, Bucket: str, Prefix: str = "", **_kwargs: Any) -> dict:
        if self.fail_list:
            raise _error("InternalError", "list failed", 500)
        self._list_calls += 1
        # Simulate concurrent listing drift after first list
        if (
            self.list_mutate_remove_key
            and self._list_calls >= 2
            and self.list_mutate_remove_key in self._bucket(Bucket)
        ):
            self._bucket(Bucket).pop(self.list_mutate_remove_key, None)
        contents = []
        for key, obj in sorted(self._bucket(Bucket).items()):
            if key.startswith(Prefix):
                contents.append(
                    {
                        "Key": key,
                        "Size": len(obj.body),
                        "LastModified": obj.last_modified,
                    }
                )
        return {"Contents": contents}

    def get_paginator(self, name: str):
        if name != "list_objects_v2":
            raise NotImplementedError(name)

        outer = self

        class _P:
            def paginate(self, Bucket: str, Prefix: str = "", **kwargs: Any):
                if outer.fail_list:
                    raise _error("InternalError", "list failed", 500)
                yield outer.list_objects_v2(Bucket=Bucket, Prefix=Prefix)

        return _P()

    def head_object(self, Bucket: str, Key: str, **_kwargs: Any) -> dict:
        bucket = self._bucket(Bucket)
        if Key not in bucket:
            raise _error("404", "Not Found", 404)
        obj = bucket[Key]
        meta = dict(obj.metadata)
        if self.corrupt_metadata_on_head_for == Key:
            meta["sha256"] = "0" * 64
        return {
            "ContentLength": len(obj.body),
            "LastModified": obj.last_modified,
            "Metadata": meta,
            "ETag": '"multipart-etag-not-md5"',
        }

    def get_object(self, Bucket: str, Key: str, **_kwargs: Any) -> dict:
        bucket = self._bucket(Bucket)
        if Key not in bucket:
            raise _error("NoSuchKey", "Not Found", 404)
        obj = bucket[Key]
        return {"Body": io.BytesIO(obj.body), "ContentLength": len(obj.body)}

    def delete_object(self, Bucket: str, Key: str, **_kwargs: Any) -> dict:
        self._bucket(Bucket).pop(Key, None)
        self.deleted_keys.append(Key)
        return {}

    def upload_file(
        self,
        Filename: str,
        Bucket: str,
        Key: str,
        ExtraArgs: Optional[dict] = None,
        **_kwargs: Any,
    ) -> None:
        self.upload_calls += 1
        if self.fail_upload_always:
            raise _error("QuotaExceeded", "quota", 400)
        if self.fail_upload_once:
            self.fail_upload_once = False
            raise _error("QuotaExceeded", "quota", 400)
        data = Path(Filename).read_bytes()
        meta = {}
        if ExtraArgs and "Metadata" in ExtraArgs:
            meta = {k.lower(): v for k, v in ExtraArgs["Metadata"].items()}
        if self.fail_upload_timeout_once:
            self.fail_upload_timeout_once = False
            if self.upload_timeout_but_store:
                self.put_bytes(Bucket, Key, data, metadata=meta)
            raise _error("RequestTimeout", "read timed out", 400)
        used = sum(len(o.body) for o in self._bucket(Bucket).values())
        if Key in self._bucket(Bucket):
            used -= len(self._bucket(Bucket)[Key].body)
        if self.quota_bytes is not None and used + len(data) > self.quota_bytes:
            raise _error("QuotaExceeded", "UploadPart quota exceeded", 400)
        body = data
        if self.corrupt_body_after_upload_for == Key and data:
            # Same length, different bytes; keep uploader-provided metadata
            flipped = bytes([(data[0] ^ 0xFF) & 0xFF]) + data[1:]
            body = flipped
        self.put_bytes(Bucket, Key, body, metadata=meta)

    def list_multipart_uploads(self, Bucket: str, Prefix: str = "", **_kwargs: Any) -> dict:
        uploads = []
        for mp in self.multiparts:
            if mp.key.startswith(Prefix):
                uploads.append(
                    {
                        "Key": mp.key,
                        "UploadId": mp.upload_id,
                        "Initiated": mp.initiated,
                    }
                )
        return {"Uploads": uploads}

    def abort_multipart_upload(
        self, Bucket: str, Key: str, UploadId: str, **_kwargs: Any
    ) -> dict:
        self.multiparts = [
            m
            for m in self.multiparts
            if not (m.key == Key and m.upload_id == UploadId)
        ]
        self.aborted_multiparts.append(Key)
        return {}
