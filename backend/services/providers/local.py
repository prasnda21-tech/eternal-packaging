"""Local filesystem storage for development / preview.

Never used as production object storage. Callers still pass opaque keys
(`eternal-packaging/delivery-proof/...`); this provider maps them onto a
directory under the backend tree. Paths are sanitized so they cannot escape
the storage root.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Tuple

from .base import StorageProvider

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9._/-]+")

DEFAULT_DIRNAME = ".local_storage"


def default_local_root() -> Path:
    """backend/.local_storage — relative to this file (providers/ → services/ → backend/)."""
    override = (os.environ.get("LOCAL_STORAGE_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / DEFAULT_DIRNAME).resolve()


def sanitize_storage_key(path: str) -> str:
    """Normalize a storage key. Rejects absolute paths and `..` segments."""
    raw = (path or "").replace("\\", "/").strip().lstrip("/")
    if not raw:
        raise ValueError("Storage path kosong.")
    parts = []
    for seg in raw.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise ValueError("Storage path tidak valid.")
        if ":" in seg:
            raise ValueError("Storage path tidak valid.")
        clean = _UNSAFE.sub("_", seg)
        if not clean or clean in (".", ".."):
            raise ValueError("Storage path tidak valid.")
        parts.append(clean)
    if not parts:
        raise ValueError("Storage path tidak valid.")
    return "/".join(parts)


class LocalFilesystemStorageProvider(StorageProvider):
    name = "local"

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root).resolve() if root else default_local_root()

    def is_configured(self) -> bool:
        return True

    def init(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    def _disk_path(self, path: str) -> Path:
        key = sanitize_storage_key(path)
        dest = (self._root / key).resolve()
        root = self._root.resolve()
        try:
            dest.relative_to(root)
        except ValueError as exc:
            raise ValueError("Storage path tidak valid.") from exc
        return dest

    def put_object(self, path: str, data: bytes, content_type: str) -> dict:
        self.init()
        dest = self._disk_path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        key = sanitize_storage_key(path)
        logger.info("Local storage wrote %s (%s bytes)", key, len(data))
        return {
            "path": key,
            "size": len(data),
            "content_type": content_type or "application/octet-stream",
        }

    def get_object(self, path: str) -> Tuple[bytes, str]:
        dest = self._disk_path(path)
        if not dest.is_file():
            raise FileNotFoundError(path)
        return dest.read_bytes(), "application/octet-stream"

    def head_object(self, path: str) -> dict:
        dest = self._disk_path(path)
        if not dest.is_file():
            raise FileNotFoundError(path)
        return {"size": dest.stat().st_size, "content_type": "application/octet-stream"}
