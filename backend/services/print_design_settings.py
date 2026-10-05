"""Design Cetakan settings (app_settings key=design_cetakan).

Presentation-only letterhead for Eternal sales PDFs.
Defaults MUST match the deployed hardcoded PDF constants so an empty
settings document leaves PDF output unchanged.

Phase 3 adds optional object-storage asset refs (logo + Faktur signatures).
Binary is never stored in app_settings.
"""
from __future__ import annotations

import copy
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services import pdf_layout as layout
from services import storage as obj_storage

logger = logging.getLogger(__name__)

SETTINGS_KEY = "design_cetakan"
PERM_VIEW = "settings.design_cetakan.view"
PERM_MANAGE = "settings.design_cetakan.manage"
FILE_KIND = "print_design"

SLOT_COMPANY_LOGO = "company_logo"
SLOT_PREPARED = "prepared_signature_image"
SLOT_APPROVED = "approved_signature_image"
ASSET_SLOTS = frozenset({SLOT_COMPANY_LOGO, SLOT_PREPARED, SLOT_APPROVED})

MAX_LOGO_BYTES = 2_000_000
MAX_SIGNATURE_BYTES = 1_500_000
MIN_IMAGE_BYTES = 64

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_TEXT = 200
_MAX_LINE = 240
_MAX_ADDRESS_LINES = 6
_MAX_ESTIMASI_LINES = 8
_MAX_BANKS = 4

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_bank_accounts() -> List[Dict[str, str]]:
    return [
        {
            "bank_label": "BRI",
            "account_number": "024701002548569",
            "account_name": "DIMAS AJI PRASETYA",
        },
        {
            "bank_label": "BCA",
            "account_number": "8467072051",
            "account_name": "SISWADI",
        },
    ]


def default_settings() -> Dict[str, Any]:
    return {
        "key": SETTINGS_KEY,
        "company_name": layout.COMPANY_NAME,
        "address_lines": list(layout.COMPANY_ADDRESS_LINES),
        "accent_color": layout.ACCENT_COLOR_HEX,
        "company_logo": None,
        "po": {
            "signature_labels": ["Operator", "Pengiriman", "Penerima"],
            "footer_name_label": "Nama",
        },
        "invoice": {
            "bank_accounts": default_bank_accounts(),
            "estimasi_title": layout.ESTIMASI_TITLE,
            "estimasi_lines": list(layout.ESTIMASI_LINES),
            "prepared_label": "Disiapkan Oleh",
            "prepared_footer": "Admin",
            "approved_label": "Disetujui Oleh",
            "approved_footer": "Pengelola",
            "prepared_signature_image": None,
            "approved_signature_image": None,
        },
        "surat_jalan": {
            "signature_labels": ["Gudang", "Cek Barang", "Kurir", "Penerima"],
            "date_label": "Tgl.",
        },
        "updated_at": None,
        "updated_by": None,
    }


def bank_display_lines(accounts: Optional[List[dict]]) -> List[str]:
    """Format structured bank rows into the PDF letterhead lines."""
    lines: List[str] = []
    for row in accounts or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("bank_label") or "").strip()
        number = str(row.get("account_number") or "").strip()
        name = str(row.get("account_name") or "").strip()
        if label or number:
            lines.append(f"{label} : {number}")
        if name:
            lines.append(f"A.N : {name}")
    return lines


def _clip(value: Any, limit: int = _MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _normalize_hex(value: Any) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    if not raw.startswith("#"):
        raw = f"#{raw}"
    if not _HEX_RE.match(raw):
        return None
    body = raw[1:]
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)
    return f"#{body.lower()}"


def _string_list(raw: Any, *, fallback: List[str], max_items: int, max_len: int = _MAX_LINE) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return list(fallback)
    out: List[str] = []
    for item in raw:
        text = _clip(item, max_len)
        if text:
            out.append(text)
        if len(out) >= max_items:
            break
    return out or list(fallback)


def _labels(raw: Any, fallback: List[str]) -> List[str]:
    if not isinstance(raw, (list, tuple)):
        return list(fallback)
    out: List[str] = []
    for idx, fallback_label in enumerate(fallback):
        if idx < len(raw):
            text = _clip(raw[idx], _MAX_TEXT)
            out.append(text or fallback_label)
        else:
            out.append(fallback_label)
    return out


def _bank_accounts(raw: Any, fallback: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not isinstance(raw, list) or not raw:
        return copy.deepcopy(fallback)
    out: List[Dict[str, str]] = []
    for row in raw[:_MAX_BANKS]:
        if not isinstance(row, dict):
            continue
        label = _clip(row.get("bank_label"))
        number = _clip(row.get("account_number"), 64)
        name = _clip(row.get("account_name"))
        if not (label or number or name):
            continue
        out.append({
            "bank_label": label or "Bank",
            "account_number": number,
            "account_name": name,
        })
    return out or copy.deepcopy(fallback)


def _sanitize_asset_ref(raw: Any) -> Optional[Dict[str, Any]]:
    """Keep only a complete server-owned asset reference. Malformed → None."""
    if not isinstance(raw, dict):
        return None
    file_id = _clip(raw.get("file_id"), 80)
    storage_path = _clip(raw.get("storage_path"), 500)
    content_type = _clip(raw.get("content_type"), 80).lower()
    if not file_id or not storage_path:
        return None
    if not storage_path.startswith(f"{obj_storage.APP_NAME}/print-design/"):
        return None
    if ".." in storage_path or storage_path.startswith("/"):
        return None
    try:
        size = int(raw.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return {
        "file_id": file_id,
        "storage_path": storage_path,
        "content_type": content_type or "application/octet-stream",
        "size": max(size, 0),
        "original_filename": _clip(raw.get("original_filename"), 180),
        "updated_at": raw.get("updated_at"),
    }


def _public_asset(ref: Optional[dict]) -> Dict[str, Any]:
    clean = _sanitize_asset_ref(ref)
    if not clean:
        return {"has_custom": False}
    return {
        "has_custom": True,
        "filename": clean.get("original_filename") or "asset",
        "content_type": clean.get("content_type"),
        "updated_at": clean.get("updated_at"),
    }


def resolve_print_design(raw: Optional[dict] = None) -> Dict[str, Any]:
    """Merge saved/partial settings with defaults. Never raises for PDF use."""
    base = default_settings()
    doc = raw if isinstance(raw, dict) else {}

    company = _clip(doc.get("company_name"), _MAX_TEXT) or base["company_name"]
    address = _string_list(
        doc.get("address_lines"),
        fallback=base["address_lines"],
        max_items=_MAX_ADDRESS_LINES,
    )
    accent = _normalize_hex(doc.get("accent_color")) or base["accent_color"]

    po_raw = doc.get("po") if isinstance(doc.get("po"), dict) else {}
    inv_raw = doc.get("invoice") if isinstance(doc.get("invoice"), dict) else {}
    sj_raw = doc.get("surat_jalan") if isinstance(doc.get("surat_jalan"), dict) else {}

    po = {
        "signature_labels": _labels(po_raw.get("signature_labels"), base["po"]["signature_labels"]),
        "footer_name_label": _clip(po_raw.get("footer_name_label")) or base["po"]["footer_name_label"],
    }
    invoice = {
        "bank_accounts": _bank_accounts(inv_raw.get("bank_accounts"), base["invoice"]["bank_accounts"]),
        "estimasi_title": _clip(inv_raw.get("estimasi_title"), _MAX_LINE) or base["invoice"]["estimasi_title"],
        "estimasi_lines": _string_list(
            inv_raw.get("estimasi_lines"),
            fallback=base["invoice"]["estimasi_lines"],
            max_items=_MAX_ESTIMASI_LINES,
        ),
        "prepared_label": _clip(inv_raw.get("prepared_label")) or base["invoice"]["prepared_label"],
        "prepared_footer": _clip(inv_raw.get("prepared_footer")) or base["invoice"]["prepared_footer"],
        "approved_label": _clip(inv_raw.get("approved_label")) or base["invoice"]["approved_label"],
        "approved_footer": _clip(inv_raw.get("approved_footer")) or base["invoice"]["approved_footer"],
        "prepared_signature_image": _sanitize_asset_ref(inv_raw.get("prepared_signature_image")),
        "approved_signature_image": _sanitize_asset_ref(inv_raw.get("approved_signature_image")),
    }
    surat_jalan = {
        "signature_labels": _labels(
            sj_raw.get("signature_labels"),
            base["surat_jalan"]["signature_labels"],
        ),
        "date_label": _clip(sj_raw.get("date_label"), 32) or base["surat_jalan"]["date_label"],
    }

    out = {
        "key": SETTINGS_KEY,
        "company_name": company,
        "address_lines": address,
        "accent_color": accent,
        "company_logo": _sanitize_asset_ref(doc.get("company_logo")),
        "po": po,
        "invoice": invoice,
        "surat_jalan": surat_jalan,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }
    # Prefetched bytes from get_print_design_for_pdf — pass through for sync renderers.
    if isinstance(doc.get("_pdf_assets"), dict):
        out["_pdf_assets"] = doc["_pdf_assets"]
    return out


def public_settings(doc: Optional[dict] = None) -> Dict[str, Any]:
    """API-safe view: text fields + asset presentation without storage paths."""
    resolved = resolve_print_design(doc)
    inv = resolved.get("invoice") or {}
    return {
        "key": SETTINGS_KEY,
        "company_name": resolved.get("company_name"),
        "address_lines": resolved.get("address_lines"),
        "accent_color": resolved.get("accent_color"),
        "company_logo": _public_asset(resolved.get("company_logo")),
        "po": resolved.get("po"),
        "invoice": {
            "bank_accounts": inv.get("bank_accounts"),
            "estimasi_title": inv.get("estimasi_title"),
            "estimasi_lines": inv.get("estimasi_lines"),
            "prepared_label": inv.get("prepared_label"),
            "prepared_footer": inv.get("prepared_footer"),
            "approved_label": inv.get("approved_label"),
            "approved_footer": inv.get("approved_footer"),
            "prepared_signature_image": _public_asset(inv.get("prepared_signature_image")),
            "approved_signature_image": _public_asset(inv.get("approved_signature_image")),
        },
        "surat_jalan": resolved.get("surat_jalan"),
        "updated_at": resolved.get("updated_at"),
        "updated_by": resolved.get("updated_by"),
    }


def _validate_put_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(400, "Payload tidak valid")

    if "accent_color" in payload and payload.get("accent_color") not in (None, ""):
        if _normalize_hex(payload.get("accent_color")) is None:
            raise HTTPException(400, "Warna aksen harus format #RGB atau #RRGGBB")

    invoice_payload = payload.get("invoice") if isinstance(payload.get("invoice"), dict) else {}
    if "bank_accounts" in invoice_payload:
        banks = invoice_payload.get("bank_accounts")
        if banks is not None and not isinstance(banks, list):
            raise HTTPException(400, "Daftar rekening harus berupa array")
        if isinstance(banks, list) and len(banks) > _MAX_BANKS:
            raise HTTPException(400, f"Maksimal {_MAX_BANKS} rekening pada Design Cetakan")

    resolved = resolve_print_design(payload)
    # Client PUT must not set asset refs — preserved from existing on save.
    resolved["company_logo"] = None
    resolved["invoice"]["prepared_signature_image"] = None
    resolved["invoice"]["approved_signature_image"] = None
    resolved["updated_at"] = None
    resolved["updated_by"] = None
    return resolved


def _actor_snapshot(user: Optional[dict]) -> Optional[dict]:
    if not isinstance(user, dict):
        return None
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": user.get("name") or user.get("email") or "User",
        "email": user.get("email") or "",
        "role": user.get("role") or "",
    }


async def get_print_design_settings(db) -> Dict[str, Any]:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return public_settings(doc)


async def get_print_design_internal(db) -> Dict[str, Any]:
    """Resolved settings including internal asset refs for PDF/asset ops."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return resolve_print_design(doc)


async def save_print_design_settings(db, payload: Dict[str, Any], user: Optional[dict] = None) -> Dict[str, Any]:
    cleaned = _validate_put_payload(payload)
    existing = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0}) or {}
    existing_resolved = resolve_print_design(existing)
    cleaned["company_logo"] = existing_resolved.get("company_logo")
    cleaned["invoice"]["prepared_signature_image"] = (existing_resolved.get("invoice") or {}).get(
        "prepared_signature_image"
    )
    cleaned["invoice"]["approved_signature_image"] = (existing_resolved.get("invoice") or {}).get(
        "approved_signature_image"
    )
    saved = {
        **cleaned,
        "key": SETTINGS_KEY,
        "updated_at": now_iso(),
        "updated_by": _actor_snapshot(user),
    }
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": saved},
        upsert=True,
    )
    return public_settings(saved)


def _slot_max_bytes(slot: str) -> int:
    if slot == SLOT_COMPANY_LOGO:
        return MAX_LOGO_BYTES
    return MAX_SIGNATURE_BYTES


def _sniff_image_type(body: bytes, content_type: Optional[str] = None) -> Optional[str]:
    if not body:
        return None
    if body.startswith(PNG_MAGIC):
        return "image/png"
    if body.startswith(JPEG_MAGIC):
        return "image/jpeg"
    # WEBP: RIFF....WEBP
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct == "image/jpg":
        return "image/jpeg"
    if ct in {"image/png", "image/jpeg", "image/webp"}:
        return ct
    return None


def validate_design_image(
    body: bytes,
    content_type: Optional[str],
    *,
    max_bytes: int,
) -> str:
    """Return media type or raise HTTP 400."""
    if not body:
        raise HTTPException(400, "File kosong.")
    if len(body) < MIN_IMAGE_BYTES:
        raise HTTPException(400, "File terlalu kecil.")
    if len(body) > max_bytes:
        mb = "2MB" if max_bytes >= 2_000_000 else "1.5MB"
        raise HTTPException(400, f"File terlalu besar (maks {mb}).")
    if body[:2] == b"MZ" or body[:4] == b"\x7fELF":
        raise HTTPException(400, "Tipe file tidak didukung.")
    head = body[:512].lower()
    claimed = (content_type or "").lower()
    if b"<svg" in head or "image/svg" in claimed:
        raise HTTPException(400, "SVG tidak diizinkan untuk Design Cetakan.")
    if body[:4] == b"%PDF" or claimed == "application/pdf":
        raise HTTPException(400, "PDF tidak diizinkan. Gunakan PNG, JPEG, atau WEBP.")
    media = _sniff_image_type(body, content_type)
    if not media:
        raise HTTPException(400, "File harus berupa gambar PNG, JPEG, atau WEBP.")
    if media == "image/png" and not body.startswith(PNG_MAGIC):
        raise HTTPException(400, "Isi file bukan PNG yang valid.")
    if media == "image/jpeg" and not body.startswith(JPEG_MAGIC):
        raise HTTPException(400, "Isi file bukan JPEG yang valid.")
    if media == "image/webp" and not (len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP"):
        raise HTTPException(400, "Isi file bukan WEBP yang valid.")
    return media


def _storage_key(slot: str, file_id: str, ext: str) -> str:
    safe_slot = _FILENAME_SAFE.sub("_", slot) or "asset"
    safe_id = _FILENAME_SAFE.sub("_", file_id) or str(uuid.uuid4())
    safe_ext = _FILENAME_SAFE.sub("", (ext or "bin").lstrip(".")) or "bin"
    return f"{obj_storage.APP_NAME}/print-design/{safe_slot}/{safe_id}.{safe_ext}"


def _get_slot_ref(settings: dict, slot: str) -> Optional[dict]:
    if slot == SLOT_COMPANY_LOGO:
        return _sanitize_asset_ref(settings.get("company_logo"))
    inv = settings.get("invoice") or {}
    if slot == SLOT_PREPARED:
        return _sanitize_asset_ref(inv.get("prepared_signature_image"))
    if slot == SLOT_APPROVED:
        return _sanitize_asset_ref(inv.get("approved_signature_image"))
    return None


def _set_slot_ref(settings: dict, slot: str, ref: Optional[dict]) -> dict:
    out = copy.deepcopy(settings)
    if slot == SLOT_COMPANY_LOGO:
        out["company_logo"] = ref
    elif slot == SLOT_PREPARED:
        out.setdefault("invoice", {})["prepared_signature_image"] = ref
    elif slot == SLOT_APPROVED:
        out.setdefault("invoice", {})["approved_signature_image"] = ref
    return out


async def _persist_settings_doc(db, doc: dict, user: Optional[dict] = None) -> Dict[str, Any]:
    saved = {
        **resolve_print_design(doc),
        "key": SETTINGS_KEY,
        "updated_at": now_iso(),
        "updated_by": _actor_snapshot(user),
    }
    saved.pop("_pdf_assets", None)
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return saved


async def upload_print_design_asset(
    db,
    *,
    slot: str,
    body: bytes,
    content_type: Optional[str],
    filename: str,
    user: Optional[dict],
) -> Dict[str, Any]:
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    media = validate_design_image(body, content_type, max_bytes=_slot_max_bytes(slot))
    file_id = str(uuid.uuid4())
    ext = obj_storage.ext_for(media, "bin")
    path = _storage_key(slot, file_id, ext)
    stamp = now_iso()
    safe_name = _FILENAME_SAFE.sub("_", (filename or f"{slot}.{ext}").strip()) or f"{slot}.{ext}"

    try:
        stored = obj_storage.put_object(path, body, media)
    except Exception as exc:
        logger.exception("print_design put_object failed slot=%s", slot)
        raise HTTPException(502, "Gagal menyimpan asset Design Cetakan.") from exc

    storage_path = stored.get("path") or path
    new_ref = {
        "file_id": file_id,
        "storage_path": storage_path,
        "content_type": media,
        "size": stored.get("size") or len(body),
        "original_filename": safe_name[:180],
        "updated_at": stamp,
    }

    current = await get_print_design_internal(db)
    old_ref = _get_slot_ref(current, slot)
    next_doc = _set_slot_ref(current, slot, new_ref)

    try:
        saved = await _persist_settings_doc(db, next_doc, user=user)
    except Exception:
        try:
            obj_storage.delete_object(storage_path)
        except Exception:
            logger.warning("cleanup new print_design object failed path=%s", storage_path, exc_info=True)
        raise

    file_doc = {
        "id": file_id,
        "storage_path": storage_path,
        "original_filename": safe_name[:180],
        "content_type": media,
        "size": new_ref["size"],
        "kind": FILE_KIND,
        "slot": slot,
        "uploaded_at": stamp,
        "uploaded_by": (user or {}).get("email") or (user or {}).get("name"),
        "uploaded_by_name": (user or {}).get("name"),
        "uploaded_by_role": (user or {}).get("role"),
        "is_deleted": False,
    }
    try:
        await db.files.insert_one(dict(file_doc))
    except Exception:
        logger.warning("print_design files metadata insert failed id=%s", file_id, exc_info=True)

    if old_ref and old_ref.get("storage_path") and old_ref.get("storage_path") != storage_path:
        await _best_effort_delete_asset(db, old_ref)

    return public_settings(saved)


async def remove_print_design_asset(db, *, slot: str, user: Optional[dict]) -> Dict[str, Any]:
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    current = await get_print_design_internal(db)
    old_ref = _get_slot_ref(current, slot)
    next_doc = _set_slot_ref(current, slot, None)
    saved = await _persist_settings_doc(db, next_doc, user=user)
    if old_ref:
        await _best_effort_delete_asset(db, old_ref)
    return public_settings(saved)


async def _best_effort_delete_asset(db, ref: dict) -> None:
    path = str((ref or {}).get("storage_path") or "").strip()
    file_id = str((ref or {}).get("file_id") or "").strip()
    if path:
        try:
            obj_storage.delete_object(path)
        except Exception:
            logger.warning("print_design delete_object failed path=%s", path, exc_info=True)
    if file_id:
        try:
            await db.files.update_one(
                {"id": file_id, "kind": FILE_KIND},
                {"$set": {"is_deleted": True, "deleted_at": now_iso()}},
            )
        except Exception:
            logger.warning("print_design files soft-delete failed id=%s", file_id, exc_info=True)


async def load_slot_bytes(db, slot: str) -> Tuple[bytes, str, str]:
    """Return (bytes, content_type, filename) for preview. Raises 404 if absent."""
    slot = str(slot or "").strip()
    if slot not in ASSET_SLOTS:
        raise HTTPException(400, "Slot asset tidak valid.")
    settings = await get_print_design_internal(db)
    ref = _get_slot_ref(settings, slot)
    if not ref:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.")
    path = ref["storage_path"]
    rec = await db.files.find_one(
        {"id": ref["file_id"], "kind": FILE_KIND, "is_deleted": {"$ne": True}},
        {"_id": 0},
    )
    if rec and str(rec.get("storage_path") or "") != path:
        path = str(rec.get("storage_path") or path)
    try:
        data, ct = obj_storage.get_object(path)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.") from exc
    except Exception as exc:
        logger.exception("print_design get_object failed slot=%s", slot)
        raise HTTPException(502, "Gagal membaca asset Design Cetakan.") from exc
    if not data:
        raise HTTPException(404, "Asset Design Cetakan tidak ditemukan.")
    media = (rec or {}).get("content_type") or ref.get("content_type") or ct or "application/octet-stream"
    name = (rec or {}).get("original_filename") or ref.get("original_filename") or f"{slot}.bin"
    return data, media, name


async def prefetch_pdf_assets(db, design: Optional[dict] = None) -> Dict[str, Optional[bytes]]:
    """Load custom asset bytes for sync PDF renderers. Failures → None (fallback)."""
    settings = design if isinstance(design, dict) else await get_print_design_internal(db)
    out: Dict[str, Optional[bytes]] = {
        "company_logo": None,
        "prepared_signature_image": None,
        "approved_signature_image": None,
    }
    for slot, key in (
        (SLOT_COMPANY_LOGO, "company_logo"),
        (SLOT_PREPARED, "prepared_signature_image"),
        (SLOT_APPROVED, "approved_signature_image"),
    ):
        ref = _get_slot_ref(settings, slot)
        if not ref:
            continue
        try:
            data, _ct = obj_storage.get_object(ref["storage_path"])
            if data and len(data) >= MIN_IMAGE_BYTES:
                out[key] = data
        except Exception:
            logger.warning("prefetch print_design asset failed slot=%s", slot, exc_info=True)
            out[key] = None
    return out


async def get_print_design_for_pdf(db) -> Dict[str, Any]:
    """Resolved design + `_pdf_assets` bytes map for sync generators."""
    design = await get_print_design_internal(db)
    design["_pdf_assets"] = await prefetch_pdf_assets(db, design)
    return design
