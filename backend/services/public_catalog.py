"""Public Catalog Settings — Phase A/B/C presentation overlays.

Owns global catalog presentation settings, categories, pricing-group
presentation overlays, variant metadata, and multi-image galleries.
Does not mutate customer pricing groups, versions, tiers, or HPP.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.purchase_common import actor_snapshot, now_iso

SETTINGS_KEY = "public_catalog"
CATEGORIES = "public_catalog_categories"
CATALOG_GROUPS = "public_catalog_groups"
CATALOG_VARIANTS = "public_catalog_variants"
CATALOG_IMAGES = "public_catalog_images"
PRICING_GROUPS = "customer_pricing_groups"
PRICING_VERSIONS = "customer_pricing_versions"

PERM_VIEW = "settings.public_catalog.view"
PERM_MANAGE = "settings.public_catalog.manage"

MAX_NAME_LEN = 120
MAX_HERO_LEN = 280
MAX_PHONE_LEN = 40
MAX_SLUG_LEN = 80
MAX_META_TITLE_LEN = 160
MAX_META_DESC_LEN = 320
MAX_ATTR_LEN = 80

IMAGE_ROLES = ("MAIN", "FRONT", "SIDE", "TOP", "DETAIL", "OTHER")
IMAGE_ROLE_LABELS = {
    "MAIN": "Utama",
    "FRONT": "Depan",
    "SIDE": "Samping",
    "TOP": "Atas",
    "DETAIL": "Detail",
    "OTHER": "Lainnya",
}

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
    await db[CATALOG_VARIANTS].create_index("id", unique=True)
    await db[CATALOG_VARIANTS].create_index(
        [("pricing_group_id", 1), ("accurate_item_id", 1)],
        unique=True,
        name="uniq_public_catalog_variant_group_item",
    )
    await db[CATALOG_VARIANTS].create_index(
        [("pricing_group_id", 1), ("visible", 1), ("display_order", 1)],
        name="public_catalog_variant_group_visible_order",
    )
    await db[CATALOG_IMAGES].create_index("id", unique=True)
    await db[CATALOG_IMAGES].create_index(
        [("pricing_group_id", 1), ("accurate_item_id", 1), ("display_order", 1)],
        name="public_catalog_image_group_item_order",
    )
    await db[CATALOG_IMAGES].create_index(
        [("pricing_group_id", 1), ("accurate_item_id", 1), ("enabled", 1)],
        name="public_catalog_image_group_item_enabled",
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


# ---------------------------------------------------------------------------
# Phase C — variant metadata + gallery (admin foundation only)
# ---------------------------------------------------------------------------


def _parse_attr(raw: Any, *, field: str) -> str:
    return _trim(raw, max_len=MAX_ATTR_LEN, field=field)


def _normalize_image_role(raw: Any) -> str:
    role = str(raw or "").strip().upper()
    if role not in IMAGE_ROLES:
        raise HTTPException(
            400,
            "Role gambar harus salah satu dari: MAIN, FRONT, SIDE, TOP, DETAIL, OTHER.",
        )
    return role


async def _pricing_group_or_404(db, pricing_group_id: str) -> dict:
    group = await db[PRICING_GROUPS].find_one({"id": pricing_group_id}, {"_id": 0})
    if not group:
        raise HTTPException(404, "Kelompok harga tidak ditemukan.")
    return group


async def _membership_products(db, group: dict) -> Dict[str, dict]:
    """Membership source: draft/published version products + active_accurate_item_ids.

    Same membership convention as customer_pricing_catalog product-image writes.
    Labels (display_name/item_code/unit) come from version snapshots / master_items.
    """
    by_aid: Dict[str, dict] = {}
    draft_id = group.get("current_draft_version_id")
    pub_id = group.get("current_published_version_id")
    for version_id, flag in ((draft_id, "draft"), (pub_id, "published")):
        if not version_id:
            continue
        version = await db[PRICING_VERSIONS].find_one(
            {"id": version_id}, {"_id": 0, "products": 1}
        )
        for product in (version or {}).get("products") or []:
            aid = str(product.get("accurate_item_id") or "").strip()
            if not aid:
                continue
            previous = by_aid.get(aid) or {}
            by_aid[aid] = {
                "accurate_item_id": aid,
                "public_variant_id": product.get("public_variant_id")
                or previous.get("public_variant_id"),
                "display_name": str(
                    product.get("display_name")
                    or previous.get("display_name")
                    or ""
                ),
                "item_code": str(
                    product.get("item_code") or previous.get("item_code") or ""
                ),
                "unit": str(product.get("unit") or previous.get("unit") or ""),
                "is_active": product.get("is_active") is not False,
                "in_draft": bool(previous.get("in_draft")) or flag == "draft",
                "in_published": bool(previous.get("in_published"))
                or flag == "published",
            }

    active_ids = [
        str(value).strip()
        for value in (group.get("active_accurate_item_ids") or [])
        if str(value).strip()
    ]
    missing = [aid for aid in active_ids if aid not in by_aid]
    if missing:
        masters = await db.master_items.find(
            {"accurate_item_id": {"$in": missing}}, {"_id": 0}
        ).to_list(len(missing))
        for master in masters:
            aid = str(master.get("accurate_item_id") or "").strip()
            if not aid:
                continue
            by_aid[aid] = {
                "accurate_item_id": aid,
                "public_variant_id": None,
                "display_name": str(
                    master.get("item_name") or master.get("item_code") or aid
                ),
                "item_code": str(master.get("item_code") or ""),
                "unit": str(master.get("unit") or ""),
                "is_active": master.get("is_active") is not False,
                "in_draft": False,
                "in_published": False,
            }
        for aid in missing:
            if aid not in by_aid:
                by_aid[aid] = {
                    "accurate_item_id": aid,
                    "public_variant_id": None,
                    "display_name": aid,
                    "item_code": "",
                    "unit": "",
                    "is_active": True,
                    "in_draft": False,
                    "in_published": False,
                }
    return by_aid


async def _require_member_product(
    db, pricing_group_id: str, accurate_item_id: str
) -> Tuple[dict, dict]:
    group = await _pricing_group_or_404(db, pricing_group_id)
    aid = str(accurate_item_id or "").strip()
    if not aid:
        raise HTTPException(400, "accurate_item_id wajib diisi.")
    products = await _membership_products(db, group)
    product = products.get(aid)
    if not product:
        raise HTTPException(
            404, "Produk tidak ditemukan di dalam kelompok harga ini."
        )
    return group, product


def _merge_variant_row(product: dict, overlay: Optional[dict]) -> dict:
    configured = bool(overlay)
    return {
        "pricing_group_id": None,  # filled by caller
        "accurate_item_id": product.get("accurate_item_id"),
        "public_variant_id": product.get("public_variant_id"),
        "display_name": product.get("display_name") or "",
        "item_code": product.get("item_code") or "",
        "unit": product.get("unit") or "",
        "is_active": bool(product.get("is_active", True)),
        "in_draft": bool(product.get("in_draft")),
        "in_published": bool(product.get("in_published")),
        "catalog": {
            "configured": configured,
            "size": str((overlay or {}).get("size") or ""),
            "color": str((overlay or {}).get("color") or ""),
            "finishing": str((overlay or {}).get("finishing") or ""),
            "display_order": (
                None
                if not configured
                else int((overlay or {}).get("display_order") or 0)
            ),
            "visible": True if not configured else bool((overlay or {}).get("visible", True)),
            "updated_at": (overlay or {}).get("updated_at"),
        },
    }


async def list_presentation_variants(
    db, pricing_group_id: str, *, q: str = ""
) -> List[dict]:
    group = await _pricing_group_or_404(db, pricing_group_id)
    products = await _membership_products(db, group)
    overlays = await db[CATALOG_VARIANTS].find(
        {"pricing_group_id": pricing_group_id}, {"_id": 0}
    ).to_list(5000)
    overlay_by_aid = {row.get("accurate_item_id"): row for row in overlays}
    needle = str(q or "").strip().lower()
    rows = []
    for aid, product in products.items():
        merged = _merge_variant_row(product, overlay_by_aid.get(aid))
        merged["pricing_group_id"] = pricing_group_id
        if needle:
            catalog = merged["catalog"]
            hay = " ".join(
                [
                    str(merged.get("display_name") or ""),
                    str(merged.get("item_code") or ""),
                    str(catalog.get("size") or ""),
                    str(catalog.get("color") or ""),
                    str(catalog.get("finishing") or ""),
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
            str(row.get("display_name") or "").casefold(),
            str(row.get("accurate_item_id") or ""),
        )
    )
    return rows


async def get_presentation_variant(
    db, pricing_group_id: str, accurate_item_id: str
) -> dict:
    _group, product = await _require_member_product(
        db, pricing_group_id, accurate_item_id
    )
    overlay = await db[CATALOG_VARIANTS].find_one(
        {
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": product["accurate_item_id"],
        },
        {"_id": 0},
    )
    merged = _merge_variant_row(product, overlay)
    merged["pricing_group_id"] = pricing_group_id
    return merged


async def upsert_presentation_variant(
    db, pricing_group_id: str, accurate_item_id: str, payload: dict, user: dict
) -> dict:
    _group, product = await _require_member_product(
        db, pricing_group_id, accurate_item_id
    )
    aid = product["accurate_item_id"]
    payload = payload or {}
    current = await db[CATALOG_VARIANTS].find_one(
        {"pricing_group_id": pricing_group_id, "accurate_item_id": aid},
        {"_id": 0},
    )

    if "size" in payload:
        size = _parse_attr(payload.get("size"), field="Ukuran")
    else:
        size = str((current or {}).get("size") or "")

    if "color" in payload:
        color = _parse_attr(payload.get("color"), field="Warna")
    else:
        color = str((current or {}).get("color") or "")

    if "finishing" in payload:
        finishing = _parse_attr(payload.get("finishing"), field="Finishing")
    else:
        finishing = str((current or {}).get("finishing") or "")

    if "display_order" in payload:
        display_order = _parse_display_order(payload.get("display_order"))
    else:
        display_order = int((current or {}).get("display_order") or 0)

    if "visible" in payload:
        visible = bool(payload.get("visible"))
    else:
        visible = True if not current else bool(current.get("visible", True))

    now = now_iso()
    actor = actor_snapshot(user)
    if current:
        await db[CATALOG_VARIANTS].update_one(
            {"pricing_group_id": pricing_group_id, "accurate_item_id": aid},
            {
                "$set": {
                    "public_variant_id": product.get("public_variant_id"),
                    "size": size,
                    "color": color,
                    "finishing": finishing,
                    "display_order": display_order,
                    "visible": visible,
                    "updated_at": now,
                    "updated_by": actor,
                }
            },
        )
    else:
        doc = {
            "id": _new_id(),
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": aid,
            "public_variant_id": product.get("public_variant_id"),
            "size": size,
            "color": color,
            "finishing": finishing,
            "display_order": display_order,
            "visible": visible,
            "created_at": now,
            "updated_at": now,
            "created_by": actor,
            "updated_by": actor,
        }
        try:
            await db[CATALOG_VARIANTS].insert_one(doc)
        except DuplicateKeyError:
            raise HTTPException(409, "Metadata varian sudah ada.")
    return await get_presentation_variant(db, pricing_group_id, aid)


def public_gallery_image(doc: dict) -> dict:
    role = str(doc.get("role") or "OTHER").upper()
    return {
        "id": doc.get("id"),
        "pricing_group_id": doc.get("pricing_group_id"),
        "accurate_item_id": doc.get("accurate_item_id"),
        "file_id": doc.get("file_id"),
        "role": role,
        "role_label": IMAGE_ROLE_LABELS.get(role, role),
        "display_order": int(doc.get("display_order") or 0),
        "is_default": bool(doc.get("is_default")),
        "enabled": bool(doc.get("enabled", True)),
        "content_type": doc.get("content_type"),
        "original_filename": doc.get("original_filename"),
        "size": doc.get("size"),
        "url": (
            f"/api/settings/public-catalog/groups/{doc.get('pricing_group_id')}"
            f"/variants/{doc.get('accurate_item_id')}/images/{doc.get('id')}/content"
            f"?v={doc.get('file_id')}"
        ),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def list_gallery_images(
    db, pricing_group_id: str, accurate_item_id: str
) -> List[dict]:
    _group, product = await _require_member_product(
        db, pricing_group_id, accurate_item_id
    )
    aid = product["accurate_item_id"]
    rows = await db[CATALOG_IMAGES].find(
        {"pricing_group_id": pricing_group_id, "accurate_item_id": aid},
        {"_id": 0},
    ).to_list(500)
    rows.sort(
        key=lambda row: (
            int(row.get("display_order") or 0),
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
        )
    )
    return [public_gallery_image(row) for row in rows]


async def get_gallery_image(
    db, pricing_group_id: str, accurate_item_id: str, image_id: str
) -> dict:
    row = await db[CATALOG_IMAGES].find_one(
        {
            "id": image_id,
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": accurate_item_id,
        },
        {"_id": 0},
    )
    if not row:
        raise HTTPException(404, "Gambar galeri tidak ditemukan.")
    return public_gallery_image(row)


async def _unset_other_defaults(
    db, *, pricing_group_id: str, accurate_item_id: str, keep_id: str
) -> None:
    await db[CATALOG_IMAGES].update_many(
        {
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": accurate_item_id,
            "id": {"$ne": keep_id},
            "is_default": True,
        },
        {"$set": {"is_default": False}},
    )


async def create_gallery_image_record(
    db,
    *,
    pricing_group_id: str,
    accurate_item_id: str,
    file_id: str,
    content_type: str,
    original_filename: str,
    size: int,
    role: str,
    display_order: int,
    is_default: bool,
    enabled: bool,
    user: dict,
) -> dict:
    await _require_member_product(db, pricing_group_id, accurate_item_id)
    now = now_iso()
    actor = actor_snapshot(user)
    image_id = _new_id()
    existing = await db[CATALOG_IMAGES].count_documents(
        {
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": accurate_item_id,
            "enabled": True,
            "is_default": True,
        }
    )
    if is_default or existing == 0:
        is_default = True
    doc = {
        "id": image_id,
        "pricing_group_id": pricing_group_id,
        "accurate_item_id": accurate_item_id,
        "file_id": file_id,
        "role": role,
        "display_order": display_order,
        "is_default": bool(is_default),
        "enabled": bool(enabled),
        "content_type": content_type,
        "original_filename": original_filename,
        "size": size,
        "created_at": now,
        "updated_at": now,
        "created_by": actor,
        "updated_by": actor,
    }
    await db[CATALOG_IMAGES].insert_one(doc)
    if doc["is_default"]:
        await _unset_other_defaults(
            db,
            pricing_group_id=pricing_group_id,
            accurate_item_id=accurate_item_id,
            keep_id=image_id,
        )
    return await get_gallery_image(db, pricing_group_id, accurate_item_id, image_id)


async def update_gallery_image(
    db,
    pricing_group_id: str,
    accurate_item_id: str,
    image_id: str,
    payload: dict,
    user: dict,
) -> dict:
    await _require_member_product(db, pricing_group_id, accurate_item_id)
    current = await db[CATALOG_IMAGES].find_one(
        {
            "id": image_id,
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": accurate_item_id,
        },
        {"_id": 0},
    )
    if not current:
        raise HTTPException(404, "Gambar galeri tidak ditemukan.")
    payload = payload or {}
    updates: Dict[str, Any] = {
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }
    if "role" in payload:
        updates["role"] = _normalize_image_role(payload.get("role"))
    if "display_order" in payload:
        updates["display_order"] = _parse_display_order(payload.get("display_order"))
    if "enabled" in payload:
        updates["enabled"] = bool(payload.get("enabled"))
    make_default = None
    if "is_default" in payload:
        make_default = bool(payload.get("is_default"))
        updates["is_default"] = make_default
    await db[CATALOG_IMAGES].update_one({"id": image_id}, {"$set": updates})
    if make_default is True:
        await _unset_other_defaults(
            db,
            pricing_group_id=pricing_group_id,
            accurate_item_id=accurate_item_id,
            keep_id=image_id,
        )
    elif make_default is False and current.get("is_default"):
        # Leaving no default is valid; fallback chain handles preview.
        pass
    return await get_gallery_image(db, pricing_group_id, accurate_item_id, image_id)


async def delete_gallery_image_record(
    db, pricing_group_id: str, accurate_item_id: str, image_id: str
) -> dict:
    await _require_member_product(db, pricing_group_id, accurate_item_id)
    current = await db[CATALOG_IMAGES].find_one(
        {
            "id": image_id,
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": accurate_item_id,
        },
        {"_id": 0},
    )
    if not current:
        raise HTTPException(404, "Gambar galeri tidak ditemukan.")
    await db[CATALOG_IMAGES].delete_one({"id": image_id})
    return {"deleted": True, "id": image_id, "file_id": current.get("file_id")}


async def resolve_variant_image(
    db, pricing_group_id: str, accurate_item_id: str
) -> dict:
    """Deterministic image source for FUTURE public use / admin preview.

    Order:
    1. enabled gallery default
    2. first enabled gallery by display_order
    3. legacy customer_pricing_groups.product_images[accurate_item_id]
    4. pricing group catalog_image
    5. none
    """
    group, product = await _require_member_product(
        db, pricing_group_id, accurate_item_id
    )
    aid = product["accurate_item_id"]
    gallery = await db[CATALOG_IMAGES].find(
        {
            "pricing_group_id": pricing_group_id,
            "accurate_item_id": aid,
            "enabled": True,
        },
        {"_id": 0},
    ).to_list(500)
    gallery.sort(
        key=lambda row: (
            int(row.get("display_order") or 0),
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
        )
    )
    default = next((row for row in gallery if row.get("is_default")), None)
    if default:
        return {
            "source": "gallery_default",
            "image": public_gallery_image(default),
            "legacy_product_image": None,
            "group_catalog_image": None,
        }
    if gallery:
        return {
            "source": "gallery_first_enabled",
            "image": public_gallery_image(gallery[0]),
            "legacy_product_image": None,
            "group_catalog_image": None,
        }
    legacy = ((group.get("product_images") or {}).get(aid) or {})
    if isinstance(legacy, dict) and legacy.get("file_id"):
        return {
            "source": "legacy_variant",
            "image": None,
            "legacy_product_image": {
                "file_id": legacy.get("file_id"),
                "content_type": legacy.get("content_type"),
                "url": (
                    f"/api/customer-pricing/groups/{pricing_group_id}/products/"
                    f"{aid}/image?v={legacy.get('file_id')}"
                ),
            },
            "group_catalog_image": None,
        }
    cover = group.get("catalog_image") or {}
    if isinstance(cover, dict) and cover.get("file_id"):
        return {
            "source": "group_cover",
            "image": None,
            "legacy_product_image": None,
            "group_catalog_image": {
                "file_id": cover.get("file_id"),
                "content_type": cover.get("content_type"),
                "url": (
                    f"/api/customer-pricing/groups/{pricing_group_id}/catalog-image"
                    f"?v={cover.get('file_id')}"
                ),
            },
        }
    return {
        "source": "none",
        "image": None,
        "legacy_product_image": None,
        "group_catalog_image": None,
    }


async def gallery_context(
    db, pricing_group_id: str, accurate_item_id: str
) -> dict:
    group, product = await _require_member_product(
        db, pricing_group_id, accurate_item_id
    )
    images = await list_gallery_images(db, pricing_group_id, accurate_item_id)
    aid = product["accurate_item_id"]
    legacy = ((group.get("product_images") or {}).get(aid) or {})
    legacy_available = bool(isinstance(legacy, dict) and legacy.get("file_id"))
    resolved = await resolve_variant_image(db, pricing_group_id, aid)
    return {
        "pricing_group_id": pricing_group_id,
        "accurate_item_id": aid,
        "display_name": product.get("display_name") or "",
        "item_code": product.get("item_code") or "",
        "images": images,
        "gallery_count": len(images),
        "legacy_product_image": (
            {
                "available": True,
                "file_id": legacy.get("file_id"),
                "url": (
                    f"/api/customer-pricing/groups/{pricing_group_id}/products/"
                    f"{aid}/image?v={legacy.get('file_id')}"
                ),
                "read_only": True,
            }
            if legacy_available
            else {"available": False, "read_only": True}
        ),
        "resolved": resolved,
    }
