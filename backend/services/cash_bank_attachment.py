"""Optional bukti/lampiran for Kas & Bank Penerimaan / Pembayaran.

Reuses object-storage + db.files pattern. Never exposes raw storage URLs.
Attachment does not affect amount / status / journal.
"""
from __future__ import annotations

import base64
import logging
import re
import uuid
from typing import Any, Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException

from services import storage as obj_storage
from services.purchase_common import now_iso

logger = logging.getLogger(__name__)

KIND = "cash_bank_attachment"
SOURCE_TYPE = "CASH_BANK_ATTACHMENT"

MAX_BYTES = 6 * 1024 * 1024
MAX_DATA_URL_CHARS = 8_000_000

ALLOWED_MIME = frozenset({
    "image/jpeg",
    "image/jpg",
    "image/png",
    "application/pdf",
})

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def public_attachment(entry: Optional[dict]) -> Optional[dict]:
    if not isinstance(entry, dict) or not _safe_str(entry.get("id")):
        return None
    return {
        "id": entry.get("id"),
        "name": entry.get("name") or entry.get("original_filename"),
        "content_type": entry.get("content_type"),
        "uploaded_at": entry.get("uploaded_at"),
        "uploaded_by": entry.get("uploaded_by"),
        "voucher_id": entry.get("voucher_id"),
        "voucher_kind": entry.get("voucher_kind"),
        "kind": KIND,
        "source_type": SOURCE_TYPE,
    }


def decode_data_url(data_url: str) -> Tuple[bytes, str]:
    raw = (data_url or "").strip()
    if not raw.startswith("data:"):
        raise HTTPException(400, "Format data URL tidak valid")
    if len(raw) > MAX_DATA_URL_CHARS:
        raise HTTPException(400, "File terlalu besar (maks ~6MB)")
    header, _, b64 = raw.partition(",")
    if not b64:
        raise HTTPException(400, "Isi file kosong")
    ct = "application/octet-stream"
    try:
        ct = header.split("data:", 1)[1].split(";", 1)[0].lower() or ct
    except Exception:
        pass
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise HTTPException(400, "File tidak dapat dibaca") from e
    if not data:
        raise HTTPException(400, "Isi file kosong")
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "File terlalu besar (maks ~6MB)")
    return data, ct


def sniff_content_type(data: bytes, filename: str = "", claimed: str = "") -> str:
    name = (filename or "").lower()
    claimed = (claimed or "").split(";", 1)[0].strip().lower()
    head = data[:8] if data else b""
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if claimed in ("image/jpeg", "image/jpg") or name.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if claimed == "image/png" or name.endswith(".png"):
        return "image/png"
    if claimed == "application/pdf" or name.endswith(".pdf"):
        return "application/pdf"
    return claimed or "application/octet-stream"


def validate_bytes(data: bytes, filename: str, claimed_ct: str) -> str:
    if not data:
        raise HTTPException(400, "Isi file kosong")
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "File terlalu besar (maks ~6MB)")
    if data[:2] == b"MZ" or data[:4] == b"\x7fELF":
        raise HTTPException(400, "Tipe file tidak didukung. Gunakan JPG, JPEG, PNG, atau PDF.")
    ct = sniff_content_type(data, filename, claimed_ct)
    if ct == "image/jpg":
        ct = "image/jpeg"
    if ct not in ALLOWED_MIME:
        raise HTTPException(
            400,
            f"Tipe file tidak didukung ({ct or 'unknown'}). Gunakan JPG, JPEG, PNG, atau PDF.",
        )
    if ct == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, "Isi file bukan gambar JPEG yang valid.")
    if ct == "image/png" and not data.startswith(b"\x89PNG"):
        raise HTTPException(400, "Isi file bukan gambar PNG yang valid.")
    if ct == "application/pdf" and not data.startswith(b"%PDF"):
        raise HTTPException(400, "Isi file bukan PDF yang valid.")
    return ct


def content_disposition(filename: str, *, download: bool) -> str:
    name = _FILENAME_SAFE.sub("_", (filename or "lampiran").strip()) or "lampiran"
    disp = "attachment" if download else "inline"
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "lampiran"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def storage_key(voucher_kind: str, voucher_id: str, file_id: str, ext: str) -> str:
    safe_kind = _FILENAME_SAFE.sub("_", _safe_str(voucher_kind)) or "voucher"
    safe_voucher = _FILENAME_SAFE.sub("_", _safe_str(voucher_id)) or "unknown"
    safe_file = _FILENAME_SAFE.sub("_", _safe_str(file_id)) or str(uuid.uuid4())
    safe_ext = _FILENAME_SAFE.sub("", (ext or "bin").lstrip(".")) or "bin"
    return f"{obj_storage.APP_NAME}/cash-bank/{safe_kind}/{safe_voucher}/{safe_file}.{safe_ext}"


async def find_file(db, *, voucher_id: str = "", file_id: str = "") -> Optional[dict]:
    if file_id:
        rec = await db.files.find_one(
            {"id": str(file_id), "kind": KIND, "is_deleted": {"$ne": True}},
            {"_id": 0},
        )
        if rec:
            return rec
    if voucher_id:
        return await db.files.find_one(
            {"voucher_id": str(voucher_id), "kind": KIND, "is_deleted": {"$ne": True}},
            {"_id": 0},
        )
    return None


async def store_attachment(
    db,
    *,
    voucher_id: str,
    voucher_kind: str,
    user: dict,
    filename: str,
    data_url: str,
) -> dict:
    voucher_id = _safe_str(voucher_id)
    voucher_kind = _safe_str(voucher_kind)
    if not voucher_id or not voucher_kind:
        raise HTTPException(400, "Lampiran membutuhkan voucher_id dan voucher_kind")

    existing = await find_file(db, voucher_id=voucher_id)
    if existing and _safe_str(existing.get("storage_path")):
        return public_attachment({
            "id": existing.get("id"),
            "name": existing.get("original_filename") or existing.get("name"),
            "content_type": existing.get("content_type"),
            "uploaded_at": existing.get("uploaded_at"),
            "uploaded_by": existing.get("uploaded_by"),
            "voucher_id": voucher_id,
            "voucher_kind": voucher_kind,
        })

    raw_bytes, claimed_ct = decode_data_url(data_url)
    ct = validate_bytes(raw_bytes, filename, claimed_ct)
    file_id = str(uuid.uuid4())
    ext = obj_storage.ext_for(ct, "bin")
    path = storage_key(voucher_kind, voucher_id, file_id, ext)
    uploaded_at = now_iso()

    try:
        stored = obj_storage.put_object(path, raw_bytes, ct)
    except Exception as e:
        logger.exception("Cash bank attachment storage failed for %s", voucher_id)
        raise HTTPException(502, "Gagal menyimpan lampiran ke Object Storage.") from e

    storage_path = stored.get("path") or path
    file_doc = {
        "id": file_id,
        "storage_path": storage_path,
        "original_filename": _safe_str(filename) or f"lampiran.{ext}",
        "content_type": ct,
        "size": stored.get("size") or len(raw_bytes),
        "kind": KIND,
        "source_type": SOURCE_TYPE,
        "voucher_id": voucher_id,
        "voucher_kind": voucher_kind,
        "uploaded_by": (user or {}).get("email") or (user or {}).get("name"),
        "uploaded_by_name": (user or {}).get("name"),
        "uploaded_by_role": (user or {}).get("role"),
        "uploaded_at": uploaded_at,
        "is_deleted": False,
    }
    await db.files.insert_one(dict(file_doc))
    return public_attachment(file_doc)


async def load_attachment_bytes(db, *, voucher_id: str, file_id: str = "") -> Tuple[bytes, str, str]:
    rec = await find_file(db, voucher_id=voucher_id, file_id=file_id)
    if not rec:
        raise HTTPException(404, "Lampiran tidak ditemukan")
    path = _safe_str(rec.get("storage_path"))
    if not path:
        raise HTTPException(404, "Lampiran tidak ditemukan")
    try:
        data = obj_storage.get_object(path)
    except Exception as e:
        logger.exception("Cash bank attachment read failed for %s", voucher_id)
        raise HTTPException(502, "Gagal membaca lampiran dari Object Storage.") from e
    if not data:
        raise HTTPException(404, "Lampiran tidak ditemukan")
    ct = rec.get("content_type") or "application/octet-stream"
    name = rec.get("original_filename") or rec.get("name") or "lampiran"
    return data, ct, name
