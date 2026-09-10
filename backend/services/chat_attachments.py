"""Private chat attachment storage backed by object storage plus db.files."""
from __future__ import annotations

import base64
import logging
import re
import uuid
from typing import Tuple
from urllib.parse import quote

from fastapi import HTTPException

from services import storage as obj_storage
from services.chat_constants import (
    ALLOWED_MIME,
    CHAT_FILE_KIND,
    CHAT_FILE_SOURCE_TYPE,
    COLL_ATTACHMENTS,
    DANGEROUS_EXTENSIONS,
    DANGEROUS_MAGIC,
    EXTENSION_MIME,
    MAX_ATTACHMENT_BYTES,
)
from services.purchase_common import now_iso

logger = logging.getLogger(__name__)

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str) -> str:
    return _FILENAME_SAFE.sub("_", str(name or "lampiran").strip()) or "lampiran"


def _ext(filename: str) -> str:
    name = str(filename or "").lower().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].strip()


def decode_data_url(data_url: str) -> Tuple[bytes, str]:
    raw = str(data_url or "").strip()
    if not raw.startswith("data:") or "," not in raw:
        raise HTTPException(400, "Format data URL tidak valid")
    header, b64 = raw.split(",", 1)
    claimed = header.split("data:", 1)[1].split(";", 1)[0].lower() or "application/octet-stream"
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception as exc:
        raise HTTPException(400, "File tidak dapat dibaca") from exc
    return data, claimed


def sniff_content_type(data: bytes, filename: str, claimed: str = "") -> str:
    ext = _ext(filename)
    claimed = str(claimed or "").split(";", 1)[0].strip().lower()
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF"):
        return "application/pdf"
    # Office files are either OLE compound documents or zip packages. Use the
    # extension to distinguish doc/docx/xls/xlsx after rejecting executables.
    if ext in {"doc", "xls"} and data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return EXTENSION_MIME[ext]
    if ext in {"docx", "xlsx"} and data.startswith(b"PK\x03\x04"):
        return EXTENSION_MIME[ext]
    return claimed or EXTENSION_MIME.get(ext) or "application/octet-stream"


def validate_bytes(data: bytes, filename: str, claimed_ct: str = "") -> str:
    if not data:
        raise HTTPException(400, "Isi file kosong")
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(400, "File terlalu besar (maks 20MB)")
    ext = _ext(filename)
    if ext in DANGEROUS_EXTENSIONS:
        raise HTTPException(400, "Tipe file tidak didukung")
    if any(data.startswith(sig) for sig in DANGEROUS_MAGIC):
        raise HTTPException(400, "Tipe file tidak didukung")
    ct = sniff_content_type(data, filename, claimed_ct)
    if ct == "image/jpg":
        ct = "image/jpeg"
    expected = EXTENSION_MIME.get(ext)
    if expected and expected != ct:
        raise HTTPException(400, "Ekstensi file tidak sesuai dengan isi file")
    if ct not in ALLOWED_MIME:
        raise HTTPException(400, f"Tipe file tidak didukung ({ct or 'unknown'})")
    if ct == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, "Isi file bukan gambar JPEG yang valid")
    if ct == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, "Isi file bukan gambar PNG yang valid")
    if ct == "image/webp" and not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise HTTPException(400, "Isi file bukan gambar WEBP yang valid")
    if ct == "application/pdf" and not data.startswith(b"%PDF"):
        raise HTTPException(400, "Isi file bukan PDF yang valid")
    return ct


def content_disposition(filename: str, *, download: bool = True) -> str:
    name = _safe_filename(filename)
    disp = "attachment" if download else "inline"
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "lampiran"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def storage_key(room_id: str, attachment_id: str, ext: str) -> str:
    safe_room = _safe_filename(room_id)
    safe_id = _safe_filename(attachment_id)
    safe_ext = _FILENAME_SAFE.sub("", (ext or "bin").lstrip(".")) or "bin"
    return f"{obj_storage.APP_NAME}/chat/{safe_room}/{safe_id}.{safe_ext}"


def public_attachment(doc: dict) -> dict:
    return {
        "attachment_id": doc.get("attachment_id") or doc.get("id"),
        "id": doc.get("id") or doc.get("attachment_id"),
        "room_id": doc.get("room_id"),
        "message_id": doc.get("message_id"),
        "original_filename": doc.get("original_filename"),
        "content_type": doc.get("content_type"),
        "size": doc.get("size"),
        "uploaded_at": doc.get("uploaded_at"),
        "uploaded_by": doc.get("uploaded_by"),
        "kind": CHAT_FILE_KIND,
    }


async def store_attachment_bytes(db, *, room_id: str, user: dict, filename: str, data: bytes, content_type: str = "") -> dict:
    ct = validate_bytes(data, filename, content_type)
    attachment_id = str(uuid.uuid4())
    ext = _ext(filename) or obj_storage.ext_for(ct, "bin")
    path = storage_key(room_id, attachment_id, ext)
    now = now_iso()
    try:
        stored = obj_storage.put_object(path, data, ct)
    except Exception as exc:
        logger.exception("Chat attachment storage failed for room %s", room_id)
        raise HTTPException(502, "Gagal menyimpan lampiran ke Object Storage.") from exc
    storage_path = stored.get("path") or path
    doc = {
        "attachment_id": attachment_id,
        "id": attachment_id,
        "room_id": room_id,
        "message_id": None,
        "storage_path": storage_path,
        "original_filename": _safe_filename(filename),
        "content_type": ct,
        "size": stored.get("size") or len(data),
        "uploaded_by": (user or {}).get("id") or (user or {}).get("user_id"),
        "uploaded_by_name": (user or {}).get("name"),
        "uploaded_by_email": (user or {}).get("email"),
        "uploaded_at": now,
        "created_at": now,
        "updated_at": now,
        "kind": CHAT_FILE_KIND,
        "source_type": CHAT_FILE_SOURCE_TYPE,
        "is_deleted": False,
    }
    await db[COLL_ATTACHMENTS].insert_one(dict(doc))
    await db.files.insert_one(dict(doc))
    return public_attachment(doc)


async def store_attachment_data_url(db, *, room_id: str, user: dict, filename: str, data_url: str) -> dict:
    data, claimed = decode_data_url(data_url)
    return await store_attachment_bytes(db, room_id=room_id, user=user, filename=filename, data=data, content_type=claimed)


async def load_attachment_bytes(db, *, attachment_id: str) -> Tuple[bytes, str, str, dict]:
    rec = await db[COLL_ATTACHMENTS].find_one({"attachment_id": str(attachment_id), "is_deleted": {"$ne": True}}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Lampiran tidak ditemukan")
    path = str(rec.get("storage_path") or "")
    if not path:
        raise HTTPException(404, "Lampiran tidak ditemukan")
    try:
        data, ct = obj_storage.get_object(path)
    except FileNotFoundError:
        raise HTTPException(404, "Lampiran tidak ditemukan")
    except Exception as exc:
        logger.exception("Chat attachment read failed for %s", attachment_id)
        raise HTTPException(502, "Gagal membaca lampiran dari Object Storage.") from exc
    return data, rec.get("content_type") or ct, rec.get("original_filename") or "lampiran", rec
