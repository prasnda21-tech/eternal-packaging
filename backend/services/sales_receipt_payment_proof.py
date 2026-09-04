"""Bukti Payment for Sales Receipts (Penerimaan Penjualan).

Proof is an attachment of an existing authoritative receipt — never a second
payment. Nominal / DP / LUNAS stay on the receipt amount. Bytes live in object
storage; API responses never expose raw storage URLs.
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import quote
import uuid

from fastapi import HTTPException

from services import storage as obj_storage

logger = logging.getLogger(__name__)

KIND = "sales_receipt_payment_proof"
SOURCE_TYPE = "SALES_RECEIPT_PAYMENT_PROOF"

# Align with existing attachment gate (~6MB decoded / ~8MB data URL).
MAX_PROOF_BYTES = 6 * 1024 * 1024
MAX_DATA_URL_CHARS = 8_000_000

ALLOWED_MIME = frozenset({
    "image/jpeg",
    "image/jpg",
    "image/png",
    "application/pdf",
})

_PUBLIC_KEYS = (
    "id",
    "name",
    "content_type",
    "uploaded_by",
    "uploaded_by_name",
    "uploaded_by_role",
    "uploaded_at",
    "receipt_id",
    "invoice_id",
    "po_id",
    "source_type",
    "kind",
)

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def public_proof(entry: Optional[dict]) -> Optional[dict]:
    """Public metadata only — no storage_path / data_url / raw object URL."""
    if not isinstance(entry, dict) or not _safe_str(entry.get("id")):
        return None
    out = {k: entry.get(k) for k in _PUBLIC_KEYS if entry.get(k) is not None}
    out["source_type"] = SOURCE_TYPE
    out["kind"] = KIND
    out.pop("storage_path", None)
    out.pop("data_url", None)
    out.pop("url", None)
    return out


def proof_from_receipt(receipt: Optional[dict]) -> Optional[dict]:
    if not isinstance(receipt, dict):
        return None
    return public_proof(receipt.get("payment_proof"))


def decode_data_url(data_url: str) -> Tuple[bytes, str]:
    raw = (data_url or "").strip()
    if not raw.startswith("data:"):
        raise HTTPException(400, "Format data URL tidak valid")
    if len(raw) > MAX_DATA_URL_CHARS:
        raise HTTPException(400, "File terlalu besar (maks ~6MB)")
    header, _, b64 = raw.partition(",")
    if not b64:
        raise HTTPException(400, "Isi file Bukti Payment kosong")
    ct = "application/octet-stream"
    try:
        ct = header.split("data:", 1)[1].split(";", 1)[0].lower() or ct
    except Exception:
        pass
    try:
        data = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise HTTPException(400, "File Bukti Payment tidak dapat dibaca") from e
    if not data:
        raise HTTPException(400, "Isi file Bukti Payment kosong")
    if len(data) > MAX_PROOF_BYTES:
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


def validate_proof_bytes(data: bytes, filename: str, claimed_ct: str) -> str:
    if not data:
        raise HTTPException(400, "Isi file Bukti Payment kosong")
    if len(data) > MAX_PROOF_BYTES:
        raise HTTPException(400, "File terlalu besar (maks ~6MB)")
    # Reject obvious executables even if mislabeled
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
    name = _FILENAME_SAFE.sub("_", (filename or "bukti-payment").strip()) or "bukti-payment"
    disp = "attachment" if download else "inline"
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "bukti-payment"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def storage_key(po_id: str, receipt_id: str, file_id: str, ext: str) -> str:
    safe_po = _FILENAME_SAFE.sub("_", _safe_str(po_id)) or "unknown-po"
    safe_receipt = _FILENAME_SAFE.sub("_", _safe_str(receipt_id)) or "unknown-receipt"
    safe_file = _FILENAME_SAFE.sub("_", _safe_str(file_id)) or str(uuid.uuid4())
    safe_ext = _FILENAME_SAFE.sub("", (ext or "bin").lstrip(".")) or "bin"
    return f"{obj_storage.APP_NAME}/sales-receipts/{safe_po}/{safe_receipt}/{safe_file}.{safe_ext}"


async def find_proof_file(db, *, receipt_id: str = "", file_id: str = "") -> Optional[dict]:
    if file_id:
        rec = await db.files.find_one(
            {"id": str(file_id), "kind": KIND, "is_deleted": {"$ne": True}},
            {"_id": 0},
        )
        if rec:
            return rec
    if receipt_id:
        return await db.files.find_one(
            {"receipt_id": str(receipt_id), "kind": KIND, "is_deleted": {"$ne": True}},
            {"_id": 0},
        )
    return None


async def store_payment_proof(
    db,
    *,
    receipt: dict,
    user: dict,
    filename: str,
    data_url: str,
    uploaded_at: str,
) -> dict:
    """Store legitimate bytes + metadata for one receipt. Idempotent if proof already exists."""
    receipt_id = _safe_str(receipt.get("id"))
    invoice_id = _safe_str(receipt.get("invoice_id"))
    po_id = _safe_str(receipt.get("po_id"))
    if not receipt_id or not invoice_id or not po_id:
        raise HTTPException(400, "Bukti Payment membutuhkan receipt_id, invoice_id, dan po_id kanonis.")

    existing_meta = proof_from_receipt(receipt)
    if existing_meta and existing_meta.get("id"):
        existing_file = await find_proof_file(db, receipt_id=receipt_id, file_id=existing_meta["id"])
        if existing_file and _safe_str(existing_file.get("storage_path")):
            return public_proof({
                **existing_meta,
                "receipt_id": receipt_id,
                "invoice_id": invoice_id,
                "po_id": po_id,
            })

    # One active proof per receipt — do not create duplicates on retry
    existing_file = await find_proof_file(db, receipt_id=receipt_id)
    if existing_file and _safe_str(existing_file.get("storage_path")):
        pub = public_proof({
            "id": existing_file.get("id"),
            "name": existing_file.get("original_filename") or existing_file.get("name"),
            "content_type": existing_file.get("content_type"),
            "uploaded_by": existing_file.get("uploaded_by"),
            "uploaded_by_name": existing_file.get("uploaded_by_name"),
            "uploaded_by_role": existing_file.get("uploaded_by_role"),
            "uploaded_at": existing_file.get("uploaded_at"),
            "receipt_id": receipt_id,
            "invoice_id": invoice_id,
            "po_id": po_id,
        })
        return pub

    raw_bytes, claimed_ct = decode_data_url(data_url)
    ct = validate_proof_bytes(raw_bytes, filename, claimed_ct)
    file_id = str(uuid.uuid4())
    ext = obj_storage.ext_for(ct, "bin")
    path = storage_key(po_id, receipt_id, file_id, ext)

    try:
        stored = obj_storage.put_object(path, raw_bytes, ct)
    except Exception as e:
        logger.exception("Sales receipt payment proof storage failed for receipt %s", receipt_id)
        raise HTTPException(502, "Gagal menyimpan Bukti Payment ke Object Storage.") from e

    storage_path = stored.get("path") or path
    file_doc = {
        "id": file_id,
        "storage_path": storage_path,
        "original_filename": _safe_str(filename) or f"bukti-payment.{ext}",
        "content_type": ct,
        "size": stored.get("size") or len(raw_bytes),
        "kind": KIND,
        "source_type": SOURCE_TYPE,
        "receipt_id": receipt_id,
        "invoice_id": invoice_id,
        "po_id": po_id,
        "order_id": po_id,
        "uploaded_by": user.get("email"),
        "uploaded_by_name": user.get("name"),
        "uploaded_by_role": user.get("role"),
        "uploaded_at": uploaded_at,
        "is_deleted": False,
    }
    await db.files.insert_one(file_doc)

    return public_proof({
        "id": file_id,
        "name": file_doc["original_filename"],
        "content_type": ct,
        "uploaded_by": file_doc["uploaded_by"],
        "uploaded_by_name": file_doc["uploaded_by_name"],
        "uploaded_by_role": file_doc["uploaded_by_role"],
        "uploaded_at": uploaded_at,
        "receipt_id": receipt_id,
        "invoice_id": invoice_id,
        "po_id": po_id,
        "source_type": SOURCE_TYPE,
        "kind": KIND,
    })


async def soft_delete_proof_for_receipt(db, receipt_id: str, *, deleted_by: str = "", reason: str = "") -> None:
    """Mark exact proof metadata deleted. Does not broad-cleanup object storage."""
    rid = _safe_str(receipt_id)
    if not rid:
        return
    from datetime import datetime, timezone
    await db.files.update_many(
        {"receipt_id": rid, "kind": KIND, "is_deleted": {"$ne": True}},
        {
            "$set": {
                "is_deleted": True,
                "deleted_at": datetime.now(timezone.utc).isoformat(),
                "deleted_by": deleted_by or None,
                "deleted_reason": reason or "sales_receipt_deleted",
            }
        },
    )


async def load_proof_bytes(db, receipt_id: str) -> Tuple[bytes, str, str]:
    """Return (bytes, content_type, filename). Pure read — no writes."""
    receipt = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not receipt:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")

    meta = proof_from_receipt(receipt) or {}
    file_id = _safe_str(meta.get("id"))
    rec = await find_proof_file(db, receipt_id=str(receipt_id), file_id=file_id)
    if not rec:
        raise HTTPException(404, "Bukti Payment tidak ditemukan untuk penerimaan ini.")

    storage_path = _safe_str(rec.get("storage_path"))
    filename = _safe_str(rec.get("original_filename") or meta.get("name") or "bukti-payment")
    ct = _safe_str(rec.get("content_type") or meta.get("content_type") or "application/octet-stream")
    if not storage_path:
        raise HTTPException(404, "Isi file Bukti Payment tidak tersedia.")

    try:
        data, fetched_ct = obj_storage.get_object(storage_path)
    except FileNotFoundError as e:
        raise HTTPException(404, "Isi file Bukti Payment tidak tersedia.") from e
    except Exception as e:
        logger.exception("Failed to fetch sales receipt proof %s", storage_path)
        raise HTTPException(502, "Gagal mengambil Bukti Payment.") from e

    if not data:
        raise HTTPException(404, "Isi file Bukti Payment tidak tersedia.")
    ct = sniff_content_type(data, filename, fetched_ct or ct)
    return data, ct, filename


def parse_proof_payload(raw: Any) -> Optional[Dict[str, str]]:
    """Normalize optional payment_proof from create payload. None if absent."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        raise HTTPException(400, "Format Bukti Payment tidak valid.")
    name = _safe_str(raw.get("name") or raw.get("filename"))
    data_url = _safe_str(raw.get("data_url") or raw.get("dataUrl"))
    if not name and not data_url:
        return None
    if not name or not data_url:
        raise HTTPException(400, "Bukti Payment membutuhkan nama file dan data file.")
    if not data_url.startswith("data:"):
        raise HTTPException(400, "Format data URL Bukti Payment tidak valid.")
    if len(data_url) > MAX_DATA_URL_CHARS:
        raise HTTPException(400, "File terlalu besar (maks ~6MB)")
    # Early MIME gate from header (same pattern as attachments)
    try:
        detected = data_url[5:].split(";", 1)[0].lower()
    except Exception:
        detected = ""
    if detected == "image/jpg":
        detected = "image/jpeg"
    if detected not in ALLOWED_MIME and detected != "image/jpeg":
        raise HTTPException(
            400,
            f"Tipe file tidak didukung ({detected or 'unknown'}). Gunakan JPG, JPEG, PNG, atau PDF.",
        )
    return {"name": name, "data_url": data_url}
