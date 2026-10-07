"""Public Catalog Settings — Phase A.

Owns global catalog presentation settings and explicit categories only.
Does not mutate customer pricing groups, versions, tiers, or HPP.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.purchase_common import actor_snapshot, now_iso

SETTINGS_KEY = "public_catalog"
CATEGORIES = "public_catalog_categories"

PERM_VIEW = "settings.public_catalog.view"
PERM_MANAGE = "settings.public_catalog.manage"

MAX_NAME_LEN = 120
MAX_HERO_LEN = 280
MAX_PHONE_LEN = 40
MAX_SLUG_LEN = 80

_PHONE_RE = re.compile(r"^[0-9+\-\s()]+$")
_SLUG_SAFE = re.compile(r"[^a-z0-9]+")


def _new_id() -> str:
    return str(uuid.uuid4())


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_SAFE.sub("-", ascii_text).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:MAX_SLUG_LEN]


def default_global_settings() -> dict:
    return {
        "key": SETTINGS_KEY,
        "catalog_name": "",
        "hero_title": "",
        "hero_subtitle": "",
        "public_whatsapp_phone": "",
        "catalog_enabled": False,
        "updated_at": None,
        "updated_by": None,
    }


def public_global_settings(doc: Optional[dict]) -> dict:
    base = default_global_settings()
    if not doc:
        return {
            "catalog_name": "",
            "hero_title": "",
            "hero_subtitle": "",
            "public_whatsapp_phone": "",
            "catalog_enabled": False,
            "updated_at": None,
        }
    return {
        "catalog_name": str(doc.get("catalog_name") or ""),
        "hero_title": str(doc.get("hero_title") or ""),
        "hero_subtitle": str(doc.get("hero_subtitle") or ""),
        "public_whatsapp_phone": str(doc.get("public_whatsapp_phone") or ""),
        "catalog_enabled": bool(doc.get("catalog_enabled", False)),
        "updated_at": doc.get("updated_at") or base["updated_at"],
    }


def _trim(value: Any, *, max_len: int, field: str) -> str:
    text = str(value or "").strip()
    if len(text) > max_len:
        raise HTTPException(400, f"{field} maksimal {max_len} karakter.")
    return text


async def get_global_settings(db) -> dict:
    return public_global_settings(
        await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    )


async def update_global_settings(db, payload: dict, user: dict) -> dict:
    payload = payload or {}
    catalog_name = _trim(payload.get("catalog_name"), max_len=MAX_NAME_LEN, field="Nama katalog")
    hero_title = _trim(payload.get("hero_title"), max_len=MAX_NAME_LEN, field="Judul hero")
    hero_subtitle = _trim(
        payload.get("hero_subtitle"), max_len=MAX_HERO_LEN, field="Subjudul hero"
    )
    phone = _trim(
        payload.get("public_whatsapp_phone"),
        max_len=MAX_PHONE_LEN,
        field="Nomor WhatsApp publik",
    )
    if phone and not _PHONE_RE.fullmatch(phone):
        raise HTTPException(
            400, "Nomor WhatsApp publik hanya boleh berisi angka dan tanda telepon."
        )
    if "catalog_enabled" in payload:
        catalog_enabled = bool(payload.get("catalog_enabled"))
    else:
        current = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0}) or {}
        catalog_enabled = bool(current.get("catalog_enabled", False))

    saved = {
        "key": SETTINGS_KEY,
        "catalog_name": catalog_name,
        "hero_title": hero_title,
        "hero_subtitle": hero_subtitle,
        "public_whatsapp_phone": phone,
        "catalog_enabled": catalog_enabled,
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return public_global_settings(saved)


def _image_meta(doc: dict) -> Optional[dict]:
    image = doc.get("image")
    if not isinstance(image, dict) or not image.get("file_id"):
        return None
    return {
        "file_id": str(image["file_id"]),
        "content_type": image.get("content_type"),
        "original_filename": image.get("original_filename"),
        "size": image.get("size"),
        "uploaded_at": image.get("uploaded_at"),
        "url": f"/api/public-catalog/categories/{doc.get('id')}/image?v={image['file_id']}",
    }


def public_category(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "slug": doc.get("slug"),
        "image": _image_meta(doc),
        "display_order": int(doc.get("display_order") or 0),
        "enabled": bool(doc.get("enabled", True)),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": doc.get("created_by"),
        "updated_by": doc.get("updated_by"),
    }


async def ensure_indexes(db) -> None:
    await db[CATEGORIES].create_index("id", unique=True)
    await db[CATEGORIES].create_index(
        "slug",
        unique=True,
        partialFilterExpression={"slug": {"$type": "string", "$gt": ""}},
        name="uniq_public_catalog_category_slug",
    )
    await db[CATEGORIES].create_index(
        [("enabled", 1), ("display_order", 1)],
        name="public_catalog_category_enabled_order",
    )


async def _allocate_slug(db, desired: str, *, exclude_id: Optional[str] = None) -> str:
    base = slugify(desired)
    if not base:
        raise HTTPException(400, "Slug kategori wajib diisi.")
    candidate = base
    index = 2
    while True:
        query: Dict[str, Any] = {"slug": candidate}
        if exclude_id:
            query["id"] = {"$ne": exclude_id}
        exists = await db[CATEGORIES].find_one(query, {"_id": 0, "id": 1})
        if not exists:
            return candidate
        candidate = f"{base}-{index}"[:MAX_SLUG_LEN]
        index += 1
        if index > 1000:
            raise HTTPException(409, "Tidak dapat membuat slug unik untuk kategori.")


def _parse_display_order(raw: Any) -> int:
    if raw in (None, ""):
        return 0
    try:
        if isinstance(raw, bool):
            raise ValueError
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "Urutan tampil harus bilangan bulat.")
    if value < 0 or value > 1_000_000:
        raise HTTPException(400, "Urutan tampil harus antara 0–1000000.")
    return value


async def list_categories(db, *, q: str = "") -> List[dict]:
    rows = await db[CATEGORIES].find({}, {"_id": 0}).to_list(1000)
    needle = str(q or "").strip().lower()
    if needle:
        rows = [
            row
            for row in rows
            if needle in str(row.get("name") or "").lower()
            or needle in str(row.get("slug") or "").lower()
        ]
    rows.sort(
        key=lambda row: (
            int(row.get("display_order") or 0),
            str(row.get("name") or "").casefold(),
            str(row.get("id") or ""),
        )
    )
    return [public_category(row) for row in rows]


async def get_category(db, category_id: str) -> dict:
    row = await db[CATEGORIES].find_one({"id": category_id}, {"_id": 0})
    if not row:
        raise HTTPException(404, "Kategori katalog tidak ditemukan.")
    return public_category(row)


async def create_category(db, payload: dict, user: dict) -> dict:
    payload = payload or {}
    name = _trim(payload.get("name"), max_len=MAX_NAME_LEN, field="Nama kategori")
    if not name:
        raise HTTPException(400, "Nama kategori wajib diisi.")
    slug_source = payload.get("slug") if str(payload.get("slug") or "").strip() else name
    slug = await _allocate_slug(db, slug_source)
    display_order = _parse_display_order(payload.get("display_order"))
    enabled = True if "enabled" not in payload else bool(payload.get("enabled"))
    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": _new_id(),
        "name": name,
        "slug": slug,
        "image": None,
        "display_order": display_order,
        "enabled": enabled,
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "updated_by": actor,
    }
    try:
        await db[CATEGORIES].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Slug kategori sudah digunakan.")
    return public_category(doc)


async def update_category(db, category_id: str, payload: dict, user: dict) -> dict:
    current = await db[CATEGORIES].find_one({"id": category_id}, {"_id": 0})
    if not current:
        raise HTTPException(404, "Kategori katalog tidak ditemukan.")
    payload = payload or {}
    updates: Dict[str, Any] = {
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }
    if "name" in payload:
        name = _trim(payload.get("name"), max_len=MAX_NAME_LEN, field="Nama kategori")
        if not name:
            raise HTTPException(400, "Nama kategori wajib diisi.")
        updates["name"] = name
    if "slug" in payload:
        updates["slug"] = await _allocate_slug(
            db, payload.get("slug") or current.get("slug") or current.get("name"),
            exclude_id=category_id,
        )
    if "display_order" in payload:
        updates["display_order"] = _parse_display_order(payload.get("display_order"))
    if "enabled" in payload:
        updates["enabled"] = bool(payload.get("enabled"))
    try:
        await db[CATEGORIES].update_one({"id": category_id}, {"$set": updates})
    except DuplicateKeyError:
        raise HTTPException(409, "Slug kategori sudah digunakan.")
    return await get_category(db, category_id)
