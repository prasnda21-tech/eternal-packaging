"""Public Catalog Settings — Phase A + Phase B overlay.

Owns global catalog presentation settings, explicit categories, and
pricing-group presentation overlays. Does not mutate customer pricing
groups, versions, tiers, or HPP.
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
CATALOG_GROUPS = "public_catalog_groups"
PRICING_GROUPS = "customer_pricing_groups"

PERM_VIEW = "settings.public_catalog.view"
PERM_MANAGE = "settings.public_catalog.manage"

MAX_NAME_LEN = 120
MAX_HERO_LEN = 280
MAX_PHONE_LEN = 40
MAX_SLUG_LEN = 80
MAX_META_TITLE_LEN = 160
MAX_META_DESC_LEN = 320

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
    await db[CATALOG_GROUPS].create_index("id", unique=True)
    await db[CATALOG_GROUPS].create_index("pricing_group_id", unique=True)
    await db[CATALOG_GROUPS].create_index(
        "slug",
        unique=True,
        partialFilterExpression={"slug": {"$type": "string", "$gt": ""}},
        name="uniq_public_catalog_group_slug",
    )
    await db[CATALOG_GROUPS].create_index(
        [("category_id", 1), ("display_order", 1)],
        name="public_catalog_group_category_order",
    )
    await db[CATALOG_GROUPS].create_index(
        [("visible", 1), ("display_order", 1)],
        name="public_catalog_group_visible_order",
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


async def _allocate_group_slug(
    db, desired: str, *, exclude_pricing_group_id: Optional[str] = None
) -> str:
    base = slugify(desired)
    if not base:
        return ""
    candidate = base
    index = 2
    while True:
        query: Dict[str, Any] = {"slug": candidate}
        if exclude_pricing_group_id:
            query["pricing_group_id"] = {"$ne": exclude_pricing_group_id}
        exists = await db[CATALOG_GROUPS].find_one(query, {"_id": 0, "id": 1})
        if not exists:
            return candidate
        candidate = f"{base}-{index}"[:MAX_SLUG_LEN]
        index += 1
        if index > 1000:
            raise HTTPException(409, "Tidak dapat membuat slug unik untuk kelompok.")


async def _resolve_category(db, category_id: Optional[str]) -> Optional[dict]:
    if category_id in (None, ""):
        return None
    category_id = str(category_id).strip()
    if not category_id:
        return None
    row = await db[CATEGORIES].find_one({"id": category_id}, {"_id": 0})
    if not row:
        raise HTTPException(400, "Kategori katalog tidak ditemukan.")
    return row


def _merge_group_row(pricing: dict, overlay: Optional[dict], category: Optional[dict]) -> dict:
    configured = bool(overlay)
    public_name = ""
    if overlay:
        public_name = str(overlay.get("public_name") or "").strip()
    fallback_name = str(pricing.get("group_name") or "")
    display_name = public_name or fallback_name
    catalog_image = pricing.get("catalog_image")
    has_image = isinstance(catalog_image, dict) and bool(catalog_image.get("file_id"))
    return {
        "pricing_group_id": pricing.get("id"),
        "public_id": pricing.get("public_id"),
        "group_name": pricing.get("group_name"),
        "pricing_enabled": bool(pricing.get("enabled", True)),
        "current_published_version_id": pricing.get("current_published_version_id"),
        "public_description": pricing.get("public_description") or "",
        "catalog_image_available": has_image,
        "catalog_image": catalog_image if has_image else None,
        "catalog": {
            "configured": configured,
            "category_id": (overlay or {}).get("category_id"),
            "category_name": (category or {}).get("name"),
            "category_enabled": (
                None if not category else bool(category.get("enabled", True))
            ),
            "public_name": public_name,
            "public_name_display": display_name,
            "slug": str((overlay or {}).get("slug") or ""),
            "display_order": (
                None
                if not configured
                else int((overlay or {}).get("display_order") or 0)
            ),
            "visible": True if not configured else bool((overlay or {}).get("visible", True)),
            "meta_title": str((overlay or {}).get("meta_title") or ""),
            "meta_description": str((overlay or {}).get("meta_description") or ""),
            "updated_at": (overlay or {}).get("updated_at"),
        },
    }


async def list_presentation_groups(db, *, q: str = "") -> List[dict]:
    pricing_rows = await db[PRICING_GROUPS].find({}, {"_id": 0}).to_list(2000)
    overlays = await db[CATALOG_GROUPS].find({}, {"_id": 0}).to_list(2000)
    overlay_by_pg = {row.get("pricing_group_id"): row for row in overlays}
    category_ids = {
        row.get("category_id")
        for row in overlays
        if row.get("category_id")
    }
    categories = {}
    if category_ids:
        cats = await db[CATEGORIES].find(
            {"id": {"$in": list(category_ids)}}, {"_id": 0}
        ).to_list(1000)
        categories = {cat.get("id"): cat for cat in cats}

    rows = []
    needle = str(q or "").strip().lower()
    for pricing in pricing_rows:
        overlay = overlay_by_pg.get(pricing.get("id"))
        category = categories.get((overlay or {}).get("category_id")) if overlay else None
        merged = _merge_group_row(pricing, overlay, category)
        if needle:
            catalog = merged["catalog"]
            hay = " ".join(
                [
                    str(merged.get("group_name") or ""),
                    str(catalog.get("public_name") or ""),
                    str(catalog.get("public_name_display") or ""),
                    str(catalog.get("slug") or ""),
                    str(catalog.get("category_name") or ""),
                ]
            ).lower()
            if needle not in hay:
                continue
        rows.append(merged)

    rows.sort(
        key=lambda row: (
            10**9
            if row["catalog"]["display_order"] is None
            else int(row["catalog"]["display_order"]),
            str(row["catalog"].get("public_name_display") or "").casefold(),
            str(row.get("pricing_group_id") or ""),
        )
    )
    return rows


async def get_presentation_group(db, pricing_group_id: str) -> dict:
    pricing = await db[PRICING_GROUPS].find_one({"id": pricing_group_id}, {"_id": 0})
    if not pricing:
        raise HTTPException(404, "Kelompok harga tidak ditemukan.")
    overlay = await db[CATALOG_GROUPS].find_one(
        {"pricing_group_id": pricing_group_id}, {"_id": 0}
    )
    category = None
    if overlay and overlay.get("category_id"):
        category = await db[CATEGORIES].find_one(
            {"id": overlay["category_id"]}, {"_id": 0}
        )
    return _merge_group_row(pricing, overlay, category)


async def upsert_presentation_group(
    db, pricing_group_id: str, payload: dict, user: dict
) -> dict:
    pricing = await db[PRICING_GROUPS].find_one({"id": pricing_group_id}, {"_id": 0})
    if not pricing:
        raise HTTPException(404, "Kelompok harga tidak ditemukan.")
    payload = payload or {}
    current = await db[CATALOG_GROUPS].find_one(
        {"pricing_group_id": pricing_group_id}, {"_id": 0}
    )

    if "category_id" in payload:
        category = await _resolve_category(db, payload.get("category_id"))
        category_id = None if not category else category.get("id")
    else:
        category_id = (current or {}).get("category_id")

    if "public_name" in payload:
        public_name = _trim(
            payload.get("public_name"), max_len=MAX_NAME_LEN, field="Nama publik"
        )
    else:
        public_name = str((current or {}).get("public_name") or "")

    if "slug" in payload:
        raw_slug = str(payload.get("slug") or "").strip()
        if raw_slug:
            slug = await _allocate_group_slug(
                db, raw_slug, exclude_pricing_group_id=pricing_group_id
            )
        else:
            slug = ""
    else:
        slug = str((current or {}).get("slug") or "")

    if "display_order" in payload:
        display_order = _parse_display_order(payload.get("display_order"))
    else:
        display_order = int((current or {}).get("display_order") or 0)

    if "visible" in payload:
        visible = bool(payload.get("visible"))
    else:
        visible = True if not current else bool(current.get("visible", True))

    if "meta_title" in payload:
        meta_title = _trim(
            payload.get("meta_title"), max_len=MAX_META_TITLE_LEN, field="Meta title"
        )
    else:
        meta_title = str((current or {}).get("meta_title") or "")

    if "meta_description" in payload:
        meta_description = _trim(
            payload.get("meta_description"),
            max_len=MAX_META_DESC_LEN,
            field="Meta description",
        )
    else:
        meta_description = str((current or {}).get("meta_description") or "")

    now = now_iso()
    actor = actor_snapshot(user)
    if current:
        doc = {
            **current,
            "category_id": category_id,
            "public_name": public_name,
            "slug": slug,
            "display_order": display_order,
            "visible": visible,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "updated_at": now,
            "updated_by": actor,
        }
        try:
            await db[CATALOG_GROUPS].update_one(
                {"pricing_group_id": pricing_group_id},
                {
                    "$set": {
                        "category_id": category_id,
                        "public_name": public_name,
                        "slug": slug,
                        "display_order": display_order,
                        "visible": visible,
                        "meta_title": meta_title,
                        "meta_description": meta_description,
                        "updated_at": now,
                        "updated_by": actor,
                    }
                },
            )
        except DuplicateKeyError:
            raise HTTPException(409, "Slug kelompok sudah digunakan.")
    else:
        doc = {
            "id": _new_id(),
            "pricing_group_id": pricing_group_id,
            "category_id": category_id,
            "public_name": public_name,
            "slug": slug,
            "display_order": display_order,
            "visible": visible,
            "meta_title": meta_title,
            "meta_description": meta_description,
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "updated_by": actor,
        }
        try:
            await db[CATALOG_GROUPS].insert_one(doc)
        except DuplicateKeyError:
            raise HTTPException(409, "Slug kelompok sudah digunakan.")

    return await get_presentation_group(db, pricing_group_id)
