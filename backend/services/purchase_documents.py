"""Reusable purchase transaction documents (lampiran).

Metadata lives in Mongo `files`; bytes live in object storage (DEV = local).
Never stores file binary in Mongo. Never exposes raw storage paths to clients.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException

from services import storage as obj_storage
from services.purchase_common import actor_snapshot, now_iso

logger = logging.getLogger(__name__)

KIND = "purchase_document"
SOURCE_TYPE = "PURCHASE_DOCUMENT"

ENTITY_PURCHASE_ORDER = "purchase_order"
ENTITY_GOODS_RECEIPT = "purchase_goods_receipt"
ENTITY_INVOICE = "purchase_invoice"
ENTITY_PAYMENT = "purchase_payment"

ALLOWED_ENTITY_TYPES = frozenset({
    ENTITY_PURCHASE_ORDER,
    ENTITY_GOODS_RECEIPT,
    ENTITY_INVOICE,
    ENTITY_PAYMENT,
})

MAX_BYTES = 8 * 1024 * 1024
MAX_DOCS_PER_ENTITY = 30

ALLOWED_MIME = frozenset({
    "image/jpeg",
    "image/jpg",
    "image/png",
    "application/pdf",
})

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_str(v: Any) -> str:
    return str(v or "").strip()


def _safe_filename(name: str) -> str:
    base = _FILENAME_SAFE.sub("_", (name or "dokumen").strip()) or "dokumen"
    return base[:180]


def public_document(doc: Optional[dict]) -> Optional[dict]:
    if not isinstance(doc, dict) or not _safe_str(doc.get("id")):
        return None
    uploader = doc.get("uploaded_by")
    uploader_name = ""
    if isinstance(uploader, dict):
        uploader_name = _safe_str(uploader.get("name") or uploader.get("email"))
    uploader_name = uploader_name or _safe_str(doc.get("uploaded_by_name"))
    return {
        "id": doc.get("id"),
        "document_name": doc.get("document_name") or doc.get("original_filename") or "Dokumen",
        "original_filename": doc.get("original_filename") or doc.get("filename") or "dokumen",
        "content_type": doc.get("content_type"),
        "size": doc.get("size") or 0,
        "entity_type": doc.get("entity_type"),
        "entity_id": doc.get("entity_id"),
        "uploaded_at": doc.get("uploaded_at"),
        "uploaded_by": uploader if isinstance(uploader, dict) else None,
        "uploaded_by_name": uploader_name or "Tidak tersedia",
        "kind": KIND,
        "source_type": SOURCE_TYPE,
    }


def content_disposition(filename: str, *, download: bool) -> str:
    name = _safe_filename(filename)
    disp = "attachment" if download else "inline"
    ascii_name = name.encode("ascii", "ignore").decode("ascii") or "dokumen"
    return f"{disp}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


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
        raise HTTPException(400, "File terlalu besar (maks ~8MB)")
    if data[:2] == b"MZ" or data[:4] == b"\x7fELF":
        raise HTTPException(400, "Tipe file tidak didukung. Gunakan PDF, JPG, JPEG, atau PNG.")
    ct = sniff_content_type(data, filename, claimed_ct)
    if ct == "image/jpg":
        ct = "image/jpeg"
    if ct not in ALLOWED_MIME:
        raise HTTPException(
            400,
            f"Tipe file tidak didukung ({ct or 'unknown'}). Gunakan PDF, JPG, JPEG, atau PNG.",
        )
    if ct == "image/jpeg" and not data.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, "Isi file bukan gambar JPEG yang valid.")
    if ct == "image/png" and not data.startswith(b"\x89PNG"):
        raise HTTPException(400, "Isi file bukan gambar PNG yang valid.")
    if ct == "application/pdf" and not data.startswith(b"%PDF"):
        raise HTTPException(400, "Isi file bukan PDF yang valid.")
    return ct


def storage_key(entity_type: str, entity_id: str, file_id: str, ext: str) -> str:
    safe_type = _FILENAME_SAFE.sub("_", entity_type) or "entity"
    safe_entity = _FILENAME_SAFE.sub("_", entity_id) or "unknown"
    safe_file = _FILENAME_SAFE.sub("_", file_id) or str(uuid.uuid4())
    safe_ext = _FILENAME_SAFE.sub("", (ext or "bin").lstrip(".")) or "bin"
    return f"{obj_storage.APP_NAME}/purchase-docs/{safe_type}/{safe_entity}/{safe_file}.{safe_ext}"


def _normalize_entity(entity_type: str, entity_id: str) -> Tuple[str, str]:
    et = _safe_str(entity_type)
    eid = _safe_str(entity_id)
    if et not in ALLOWED_ENTITY_TYPES:
        raise HTTPException(400, "entity_type tidak valid untuk dokumen pembelian.")
    if not eid:
        raise HTTPException(400, "entity_id wajib diisi.")
    return et, eid


async def _assert_entity_exists(db, entity_type: str, entity_id: str) -> None:
    collection_map = {
        ENTITY_PURCHASE_ORDER: "purchase_orders",
        ENTITY_GOODS_RECEIPT: "purchase_goods_receipts",
        ENTITY_INVOICE: "purchase_invoices",
        ENTITY_PAYMENT: "purchase_payments",
    }
    coll = collection_map[entity_type]
    found = await db[coll].find_one({"id": entity_id}, {"_id": 1})
    if not found:
        raise HTTPException(404, "Transaksi terkait dokumen tidak ditemukan.")


async def list_documents(db, *, entity_type: str, entity_id: str) -> List[dict]:
    et, eid = _normalize_entity(entity_type, entity_id)
    rows = await db.files.find(
        {
            "kind": KIND,
            "entity_type": et,
            "entity_id": eid,
            "is_deleted": {"$ne": True},
        },
        {"_id": 0},
    ).sort("uploaded_at", -1).to_list(MAX_DOCS_PER_ENTITY)
    out = []
    for r in rows:
        pub = public_document(r)
        if pub:
            out.append(pub)
    return out


async def store_document_bytes(
    db,
    *,
    entity_type: str,
    entity_id: str,
    user: dict,
    filename: str,
    data: bytes,
    content_type: str = "",
    document_name: str = "",
) -> dict:
    et, eid = _normalize_entity(entity_type, entity_id)
    await _assert_entity_exists(db, et, eid)

    count = await db.files.count_documents(
        {"kind": KIND, "entity_type": et, "entity_id": eid, "is_deleted": {"$ne": True}}
    )
    if count >= MAX_DOCS_PER_ENTITY:
        raise HTTPException(400, f"Maksimum {MAX_DOCS_PER_ENTITY} dokumen per transaksi.")

    ct = validate_bytes(data, filename, content_type)
    file_id = str(uuid.uuid4())
    ext = obj_storage.ext_for(ct, "bin")
    path = storage_key(et, eid, file_id, ext)
    uploaded_at = now_iso()
    actor = actor_snapshot(user)

    try:
        stored = obj_storage.put_object(path, data, ct)
    except Exception as exc:
        logger.exception("Purchase document storage failed for %s/%s", et, eid)
        raise HTTPException(502, "Gagal menyimpan dokumen ke Object Storage.") from exc

    doc_name = _safe_str(document_name) or _safe_filename(filename) or f"dokumen.{ext}"
    file_doc: Dict[str, Any] = {
        "id": file_id,
        "storage_path": stored.get("path") or path,
        "document_name": doc_name,
        "original_filename": _safe_filename(filename) or f"dokumen.{ext}",
        "content_type": ct,
        "size": stored.get("size") or len(data),
        "kind": KIND,
        "source_type": SOURCE_TYPE,
        "entity_type": et,
        "entity_id": eid,
        "uploaded_by": actor,
        "uploaded_by_name": actor.get("name") or actor.get("email") or "",
        "uploaded_by_user_id": actor.get("id") or "",
        "uploaded_at": uploaded_at,
        "is_deleted": False,
    }
    await db.files.insert_one(dict(file_doc))
    return public_document(file_doc)


async def get_document(db, document_id: str) -> dict:
    rec = await db.files.find_one(
        {"id": str(document_id), "kind": KIND, "is_deleted": {"$ne": True}},
        {"_id": 0},
    )
    if not rec:
        raise HTTPException(404, "Dokumen tidak ditemukan.")
    return rec


async def load_document_bytes(db, document_id: str) -> Tuple[bytes, str, str, dict]:
    rec = await get_document(db, document_id)
    path = _safe_str(rec.get("storage_path"))
    if not path:
        raise HTTPException(404, "Dokumen tidak ditemukan.")
    try:
        data, ct = obj_storage.get_object(path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Dokumen tidak ditemukan.") from exc
    except Exception as exc:
        logger.exception("Purchase document read failed for %s", document_id)
        raise HTTPException(502, "Gagal membaca dokumen dari Object Storage.") from exc
    name = rec.get("original_filename") or rec.get("document_name") or "dokumen"
    return data, rec.get("content_type") or ct, name, rec


async def soft_delete_document(db, document_id: str, user: dict) -> dict:
    rec = await get_document(db, document_id)
    actor = actor_snapshot(user)
    await db.files.update_one(
        {"id": rec["id"], "kind": KIND},
        {
            "$set": {
                "is_deleted": True,
                "deleted_at": now_iso(),
                "deleted_by": actor,
                "deleted_by_name": actor.get("name") or actor.get("email") or "",
            }
        },
    )
    # Best-effort storage cleanup; metadata soft-delete is source of truth.
    path = _safe_str(rec.get("storage_path"))
    if path:
        try:
            obj_storage.delete_object(path)
        except Exception:
            logger.warning("Purchase document storage delete skipped for %s", path)
    pub = public_document(rec) or {"id": rec["id"]}
    pub["deleted"] = True
    return pub
