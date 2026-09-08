"""Storage boundary — thin dispatcher over pluggable providers.

Historic public API preserved so existing callers do NOT change:
    APP_NAME
    is_configured() -> bool
    init_storage() -> Optional[str]    (returns provider name-ish for legacy compat)
    put_object(path, data, content_type) -> dict
    get_object(path) -> (bytes, content_type)
    ext_for(content_type, fallback="bin") -> str

Provider selection via env `STORAGE_PROVIDER`:
    - "emergent"  (legacy / current production Emergent object store)
    - "s3"        (portable S3-compatible / Biznet)
    - "local"     (filesystem; local preview only)

If STORAGE_PROVIDER is unset, production stays on `emergent` when that
provider is configured. Local Mongo without Emergent/S3 credentials uses
the filesystem adapter — never a production S3 account.
"""
from __future__ import annotations
import os
import logging
from typing import Tuple

from .providers.base import StorageProvider
from .providers.emergent import EmergentStorageProvider
from .providers.local import LocalFilesystemStorageProvider
from .providers.s3 import S3StorageProvider

logger = logging.getLogger(__name__)

APP_NAME = "eternal-packaging"

_provider: StorageProvider | None = None


def _mongo_is_local() -> bool:
    url = (os.environ.get("MONGO_URL") or "").lower()
    return "localhost" in url or "127.0.0.1" in url


def _select_provider() -> StorageProvider:
    global _provider
    if _provider is not None:
        return _provider
    name = (os.environ.get("STORAGE_PROVIDER") or "").strip().lower()
    if name in ("local", "filesystem"):
        _provider = LocalFilesystemStorageProvider()
    elif name == "s3":
        s3 = S3StorageProvider()
        if s3.is_configured():
            _provider = s3
        elif _mongo_is_local():
            logger.warning("S3 not configured; using local filesystem storage for preview.")
            _provider = LocalFilesystemStorageProvider()
        else:
            _provider = s3
    elif name == "emergent":
        em = EmergentStorageProvider()
        if em.is_configured():
            _provider = em
        elif _mongo_is_local():
            logger.warning("Emergent object storage not configured; using local filesystem storage for preview.")
            _provider = LocalFilesystemStorageProvider()
        else:
            _provider = em
    elif not name:
        em = EmergentStorageProvider()
        s3 = S3StorageProvider()
        if em.is_configured():
            _provider = em
        elif s3.is_configured():
            _provider = s3
        elif _mongo_is_local():
            logger.warning("No remote object storage configured; using local filesystem storage for preview.")
            _provider = LocalFilesystemStorageProvider()
        else:
            _provider = em
    else:
        logger.warning("Unknown STORAGE_PROVIDER=%s, falling back to local filesystem.", name)
        _provider = LocalFilesystemStorageProvider() if _mongo_is_local() else EmergentStorageProvider()
    logger.info("Storage provider selected: %s", _provider.name)
    return _provider


def _reset_provider_for_tests() -> None:
    """Internal — used by unit tests to reset cached provider between cases."""
    global _provider
    _provider = None


def is_configured() -> bool:
    return _select_provider().is_configured()


def init_storage() -> str | None:
    """Idempotent init. Returns provider name on success (or None if not
    configured) — retains "returns truthy on success" contract of the legacy
    function without exposing internal session tokens."""
    p = _select_provider()
    if not p.is_configured():
        return None
    p.init()
    return p.name


def put_object(path: str, data: bytes, content_type: str) -> dict:
    return _select_provider().put_object(path, data, content_type)


def get_object(path: str) -> Tuple[bytes, str]:
    return _select_provider().get_object(path)


def head_object(path: str) -> dict:
    return _select_provider().head_object(path)


def delete_object(path: str) -> bool:
    return _select_provider().delete_object(path)


MIME_TO_EXT = {

    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "application/pdf": "pdf",
}


def ext_for(content_type: str, fallback: str = "bin") -> str:
    return MIME_TO_EXT.get((content_type or "").lower(), fallback)
