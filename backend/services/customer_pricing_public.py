"""Read-only public Customer Pricing catalog and calculator.

Only the current Published version is read. Final selling prices come from the
frozen Phase 1 product ``tier_prices`` snapshots; live HPP services are never
consulted.

Public Catalog Settings (A/B/C) enrich presentation only — no pricing writes.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services import customer_pricing as cp
from services import customer_pricing_catalog as catalog
from services import public_catalog as pc
from services.purchase_common import money_round, now_iso

MAX_PUBLIC_GROUPS = 200
PUBLIC_NOT_FOUND = "Produk harga tidak tersedia."
CATALOG_DISABLED = "Katalog harga sedang tidak tersedia."


def _public_ref(value: Any) -> Optional[str]:
    reference = str(value or "").strip()
    return reference if 1 <= len(reference) <= 128 else None


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 and parsed == value else None


def _positive_money(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return money_round(parsed) if parsed > 0 else None


def _public_tiers(version: dict, product: dict) -> Optional[List[dict]]:
    tiers = version.get("tiers") or []
    prices = product.get("tier_prices") or []
    if not tiers or len(tiers) != len(prices):
        return None
    output = []
    for tier, price in zip(tiers, prices):
        minimum = _positive_int(tier.get("min_qty"))
        raw_maximum = tier.get("max_qty")
        maximum = None if raw_maximum is None else _positive_int(raw_maximum)
        unit_price = _positive_money(price)
        if minimum is None or (raw_maximum is not None and maximum is None) or unit_price is None:
            return None
        output.append(
            {
                "min_qty": minimum,
                "max_qty": maximum,
                "unit_price": unit_price,
            }
        )
    return output


def _min_unit_price(variants: List[dict]) -> Optional[float]:
    prices = []
    for variant in variants:
        for tier in variant.get("tiers") or []:
            price = _positive_money(tier.get("unit_price"))
            if price is not None:
                prices.append(price)
    return min(prices) if prices else None


def _size_summary(variants: List[dict]) -> Optional[str]:
    sizes = []
    seen = set()
    for variant in variants:
        size = str(variant.get("size") or "").strip()
        if not size:
            continue
        key = size.casefold()
        if key in seen:
            continue
        seen.add(key)
        sizes.append(size)
    if not sizes:
        return None
    if len(sizes) == 1:
        return sizes[0]
    return f"{sizes[0]} – {sizes[-1]}"


async def get_public_catalog_settings(db) -> dict:
    settings = await pc.get_global_settings(db)
    return {
        "catalog_name": settings.get("catalog_name") or "",
        "hero_title": settings.get("hero_title") or "",
        "hero_subtitle": settings.get("hero_subtitle") or "",
        "public_whatsapp_phone": settings.get("public_whatsapp_phone") or "",
        "catalog_enabled": bool(settings.get("catalog_enabled", False)),
    }


async def list_public_categories(db) -> List[dict]:
    rows = await db[pc.CATEGORIES].find(
        {"enabled": True}, {"_id": 0}
    ).to_list(1000)
    rows.sort(
        key=lambda row: (
            int(row.get("display_order") or 0),
            str(row.get("name") or "").casefold(),
            str(row.get("id") or ""),
        )
    )
    output = []
    for row in rows:
        category_id = _public_ref(row.get("id"))
        name = str(row.get("name") or "").strip()
        if not category_id or not name:
            continue
        image = row.get("image") if isinstance(row.get("image"), dict) else {}
        file_id = _public_ref(image.get("file_id"))
        image_url = None
        if file_id:
            image_url = (
                f"/api/public/catalog/categories/{category_id}/image?v={file_id}"
            )
        output.append(
            {
                "id": category_id,
                "name": name,
                "slug": str(row.get("slug") or ""),
                "display_order": int(row.get("display_order") or 0),
                "image_url": image_url,
            }
        )
    return output


async def get_public_category_image(db, category_id: str):
    from services import public_catalog_images as images

    category = await db[pc.CATEGORIES].find_one(
        {"id": category_id, "enabled": True}, {"_id": 0, "id": 1}
    )
    if not category:
        raise HTTPException(404, "Gambar kategori tidak tersedia.")
    return await images.get_category_image(db, category_id=category_id)


def _serialize_variant_public(
    *,
    public_id: str,
    product: dict,
    tiers: List[dict],
    overlay: Optional[dict],
    gallery_rows: List[dict],
    legacy_image_url: Optional[str],
) -> dict:
    public_variant_id = _public_ref(product.get("public_variant_id"))
    display_name = str(product.get("display_name") or "").strip()
    size = str((overlay or {}).get("size") or "").strip()
    color = str((overlay or {}).get("color") or "").strip()
    finishing = str((overlay or {}).get("finishing") or "").strip()
    images = []
    for row in gallery_rows:
        image_id = _public_ref(row.get("id"))
        if not image_id:
            continue
        role = str(row.get("role") or "OTHER").upper()
        if role not in pc.IMAGE_ROLES:
            role = "OTHER"
        images.append(
            {
                "id": image_id,
                "role": role,
                "role_label": pc.IMAGE_ROLE_LABELS.get(role, role),
                "display_order": int(row.get("display_order") or 0),
                "is_default": bool(row.get("is_default")),
                "url": (
                    f"/api/public/customer-pricing/groups/{public_id}/variants/"
                    f"{public_variant_id}/gallery/{image_id}/image"
                ),
            }
        )
    images.sort(key=lambda item: (item["display_order"], item["id"]))
    default_image = next((img for img in images if img["is_default"]), None)
    primary = None
    if default_image:
        primary = default_image["url"]
    elif images:
        primary = images[0]["url"]
    elif legacy_image_url:
        primary = legacy_image_url
    return {
        "public_variant_id": public_variant_id,
        "display_name": display_name,
        "unit": str(product.get("unit") or "").strip() or None,
        "minimum_quantity": tiers[0]["min_qty"],
        "tiers": tiers,
        "size": size or None,
        "color": color or None,
        "finishing": finishing or None,
        "display_order": (
            None
            if not overlay
            else int((overlay or {}).get("display_order") or 0)
        ),
        "image_url": primary or legacy_image_url,
        "images": images,
        # internal only — stripped before response
        "_accurate_item_id": str(product.get("accurate_item_id") or ""),
    }


def _strip_internal(variant: dict) -> dict:
    return {key: value for key, value in variant.items() if not key.startswith("_")}


def _serialize_group(
    group: dict,
    version: dict,
    active_accurate_item_ids: set[str],
    *,
    overlay: Optional[dict] = None,
    category: Optional[dict] = None,
    variant_overlays: Optional[Dict[str, dict]] = None,
    gallery_by_aid: Optional[Dict[str, List[dict]]] = None,
) -> Optional[dict]:
    if (
        version.get("status") != cp.STATUS_PUBLISHED
        or version.get("id") != group.get("current_published_version_id")
        or version.get("pricing_group_id") != group.get("id")
    ):
        return None
    if overlay and overlay.get("visible") is False:
        return None
    public_id = _public_ref(group.get("public_id"))
    fallback_name = str(version.get("group_name") or group.get("group_name") or "").strip()
    public_name = str((overlay or {}).get("public_name") or "").strip()
    name = public_name or fallback_name
    if not public_id or not name:
        return None

    variant_overlays = variant_overlays or {}
    gallery_by_aid = gallery_by_aid or {}
    variants = []
    seen_variant_ids = set()
    product_images = group.get("product_images") or {}
    for product in version.get("products") or []:
        accurate_item_id = str(product.get("accurate_item_id") or "")
        if product.get("is_active") is False or accurate_item_id not in active_accurate_item_ids:
            continue
        v_overlay = variant_overlays.get(accurate_item_id)
        if v_overlay and v_overlay.get("visible") is False:
            continue
        public_variant_id = _public_ref(product.get("public_variant_id"))
        display_name = str(product.get("display_name") or "").strip()
        tiers = _public_tiers(version, product)
        if not public_variant_id or not display_name or not tiers:
            continue
        if public_variant_id in seen_variant_ids:
            return None
        seen_variant_ids.add(public_variant_id)
        prod_img = product_images.get(accurate_item_id) or {}
        prod_img_file_id = _public_ref(prod_img.get("file_id"))
        legacy_image_url = None
        if prod_img_file_id:
            legacy_image_url = (
                f"/api/public/customer-pricing/groups/{public_id}/variants/"
                f"{public_variant_id}/image"
            )
        gallery_rows = [
            row
            for row in gallery_by_aid.get(accurate_item_id, [])
            if row.get("enabled") is not False
        ]
        variants.append(
            _serialize_variant_public(
                public_id=public_id,
                product=product,
                tiers=tiers,
                overlay=v_overlay,
                gallery_rows=gallery_rows,
                legacy_image_url=legacy_image_url,
            )
        )
    if not variants:
        return None

    variants.sort(
        key=lambda row: (
            10**9 if row.get("display_order") is None else int(row["display_order"]),
            str(row.get("display_name") or "").casefold(),
            str(row.get("public_variant_id") or ""),
        )
    )
    image = group.get("catalog_image") or {}
    image_file_id = _public_ref(image.get("file_id"))
    image_url = None
    if image_file_id:
        image_url = f"/api/public/customer-pricing/groups/{public_id}/image"
    description = (
        str(group.get("public_description") or "").strip()[
            : cp.MAX_PUBLIC_DESCRIPTION_LENGTH
        ]
        or None
    )
    slug = str((overlay or {}).get("slug") or "").strip() or None
    category_payload = None
    if category and category.get("enabled") is not False:
        category_payload = {
            "id": category.get("id"),
            "name": category.get("name"),
            "slug": category.get("slug") or "",
        }
    min_price = _min_unit_price(variants)
    public_variants = [_strip_internal(variant) for variant in variants]
    return {
        "public_id": public_id,
        "slug": slug,
        "name": name,
        "public_description": description,
        "image_url": image_url,
        "category": category_payload,
        "display_order": (
            None
            if not overlay
            else int((overlay or {}).get("display_order") or 0)
        ),
        "min_unit_price": min_price,
        "variant_count": len(public_variants),
        "size_summary": _size_summary(public_variants),
        "meta_title": str((overlay or {}).get("meta_title") or "").strip() or None,
        "meta_description": (
            str((overlay or {}).get("meta_description") or "").strip() or None
        ),
        "variants": public_variants,
        "search_text": " ".join(
            filter(
                None,
                [
                    name,
                    fallback_name,
                    description or "",
                    (category_payload or {}).get("name") or "",
                    *[
                        " ".join(
                            filter(
                                None,
                                [
                                    variant.get("display_name"),
                                    variant.get("size"),
                                    variant.get("color"),
                                    variant.get("finishing"),
                                ]
                            )
                        )
                        for variant in public_variants
                    ],
                ],
            )
        ),
    }


async def _load_presentation_maps(db, groups: List[dict]) -> Tuple[
    Dict[str, dict], Dict[str, dict], Dict[str, dict], Dict[str, List[dict]]
]:
    group_ids = [str(group.get("id") or "") for group in groups if group.get("id")]
    overlays = []
    if group_ids:
        overlays = await db[pc.CATALOG_GROUPS].find(
            {"pricing_group_id": {"$in": group_ids}}, {"_id": 0}
        ).to_list(MAX_PUBLIC_GROUPS)
    overlay_by_pg = {row.get("pricing_group_id"): row for row in overlays}
    category_ids = {
        row.get("category_id") for row in overlays if row.get("category_id")
    }
    categories = {}
    if category_ids:
        cats = await db[pc.CATEGORIES].find(
            {"id": {"$in": list(category_ids)}}, {"_id": 0}
        ).to_list(1000)
        categories = {cat.get("id"): cat for cat in cats}

    variant_by_pg: Dict[str, Dict[str, dict]] = {}
    gallery_by_pg: Dict[str, Dict[str, List[dict]]] = {}
    if not group_ids:
        return overlay_by_pg, categories, variant_by_pg, gallery_by_pg

    variant_overlays = await db[pc.CATALOG_VARIANTS].find(
        {"pricing_group_id": {"$in": group_ids}},
        {"_id": 0},
    ).to_list(10000)
    for row in variant_overlays:
        pg = row.get("pricing_group_id")
        aid = row.get("accurate_item_id")
        if not pg or not aid:
            continue
        variant_by_pg.setdefault(pg, {})[aid] = row

    gallery_rows = await db[pc.CATALOG_IMAGES].find(
        {"pricing_group_id": {"$in": group_ids}},
        {"_id": 0},
    ).to_list(20000)
    for row in gallery_rows:
        pg = row.get("pricing_group_id")
        aid = row.get("accurate_item_id")
        if not pg or not aid:
            continue
        gallery_by_pg.setdefault(pg, {}).setdefault(aid, []).append(row)
    return overlay_by_pg, categories, variant_by_pg, gallery_by_pg


async def _load_catalog(db, *, group_public_id: Optional[str] = None) -> List[dict]:
    settings = await get_public_catalog_settings(db)
    if not settings.get("catalog_enabled"):
        return []

    group_filter: Dict[str, Any] = {
        "enabled": True,
        "current_published_version_id": {"$nin": [None, ""]},
    }
    if group_public_id is not None:
        group_filter["public_id"] = str(group_public_id or "").strip()
    groups = await db[cp.GROUPS].find(group_filter, {"_id": 0}).to_list(MAX_PUBLIC_GROUPS)
    version_ids = [
        group.get("current_published_version_id")
        for group in groups
        if group.get("current_published_version_id")
    ]
    if not version_ids:
        return []
    versions = await db[cp.VERSIONS].find(
        {"id": {"$in": version_ids}, "status": cp.STATUS_PUBLISHED}, {"_id": 0}
    ).to_list(MAX_PUBLIC_GROUPS)
    versions_by_id = {version.get("id"): version for version in versions}
    accurate_item_ids = {
        str(product.get("accurate_item_id") or "")
        for version in versions
        for product in (version.get("products") or [])
        if product.get("accurate_item_id")
    }
    active_rows = []
    if accurate_item_ids:
        active_rows = await db.master_items.find(
            {
                "accurate_item_id": {"$in": list(accurate_item_ids)},
                "is_active": {"$ne": False},
            },
            {"_id": 0, "accurate_item_id": 1},
        ).to_list(len(accurate_item_ids))
    active_ids = {str(row.get("accurate_item_id") or "") for row in active_rows}

    overlay_by_pg, categories, variant_by_pg, gallery_by_pg = await _load_presentation_maps(
        db, groups
    )

    output = []
    for group in groups:
        version = versions_by_id.get(group.get("current_published_version_id"))
        if not version:
            continue
        overlay = overlay_by_pg.get(group.get("id"))
        category = None
        if overlay and overlay.get("category_id"):
            category = categories.get(overlay.get("category_id"))
        serialized = _serialize_group(
            group,
            version,
            active_ids,
            overlay=overlay,
            category=category,
            variant_overlays=variant_by_pg.get(group.get("id"), {}),
            gallery_by_aid=gallery_by_pg.get(group.get("id"), {}),
        )
        if serialized:
            output.append(serialized)
    output.sort(
        key=lambda item: (
            10**9 if item.get("display_order") is None else int(item["display_order"]),
            str(item.get("name") or "").casefold(),
            str(item.get("public_id") or ""),
        )
    )
    return output


async def list_public_groups(db) -> List[dict]:
    groups = await _load_catalog(db)
    return [
        {
            "public_id": group["public_id"],
            "slug": group.get("slug"),
            "name": group["name"],
            "public_description": group["public_description"],
            "image_url": group["image_url"],
            "category": group.get("category"),
            "display_order": group.get("display_order"),
            "min_unit_price": group.get("min_unit_price"),
            "variant_count": group.get("variant_count"),
            "size_summary": group.get("size_summary"),
            "search_text": group.get("search_text"),
            "variants": [
                {
                    "public_variant_id": variant["public_variant_id"],
                    "display_name": variant["display_name"],
                    "unit": variant["unit"],
                    "image_url": variant.get("image_url"),
                    "size": variant.get("size"),
                    "color": variant.get("color"),
                    "finishing": variant.get("finishing"),
                }
                for variant in group["variants"]
            ],
        }
        for group in groups
    ]


async def get_public_catalog_home(db) -> dict:
    settings = await get_public_catalog_settings(db)
    if not settings.get("catalog_enabled"):
        return {
            "settings": settings,
            "categories": [],
            "groups": [],
            "message": CATALOG_DISABLED,
        }
    categories = await list_public_categories(db)
    groups = await list_public_groups(db)
    return {
        "settings": settings,
        "categories": categories,
        "groups": groups,
        "message": None,
    }


async def get_public_group(db, group_public_id: str) -> dict:
    settings = await get_public_catalog_settings(db)
    if not settings.get("catalog_enabled"):
        raise HTTPException(404, CATALOG_DISABLED)
    groups = await _load_catalog(db, group_public_id=group_public_id)
    if len(groups) != 1:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    group = groups[0]
    return {
        "public_id": group["public_id"],
        "slug": group.get("slug"),
        "name": group["name"],
        "public_description": group["public_description"],
        "image_url": group["image_url"],
        "category": group.get("category"),
        "display_order": group.get("display_order"),
        "min_unit_price": group.get("min_unit_price"),
        "variant_count": group.get("variant_count"),
        "size_summary": group.get("size_summary"),
        "meta_title": group.get("meta_title"),
        "meta_description": group.get("meta_description"),
        "variants": group["variants"],
        "contact": {
            "public_whatsapp_phone": settings.get("public_whatsapp_phone") or "",
        },
    }


async def get_public_group_by_slug(db, slug: str) -> dict:
    clean = str(slug or "").strip().lower()
    if not clean:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    overlay = await db[pc.CATALOG_GROUPS].find_one({"slug": clean}, {"_id": 0})
    if not overlay or overlay.get("visible") is False:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    group = await db[cp.GROUPS].find_one(
        {"id": overlay.get("pricing_group_id")}, {"_id": 0, "public_id": 1}
    )
    public_id = _public_ref((group or {}).get("public_id"))
    if not public_id:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    return await get_public_group(db, public_id)


async def get_public_group_image(db, group_public_id: str):
    public_group = await get_public_group(db, group_public_id)
    group = await db[cp.GROUPS].find_one(
        {"public_id": public_group["public_id"]}, {"_id": 0, "id": 1}
    )
    if not group:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    return await catalog.get_catalog_image(db, group_id=group["id"])


async def get_public_variant_image(
    db, group_public_id: str, public_variant_id: str
):
    public_group = await get_public_group(db, group_public_id)
    target_vid = str(public_variant_id or "").strip()
    variant = next(
        (
            v
            for v in (public_group.get("variants") or [])
            if v.get("public_variant_id") == target_vid
        ),
        None,
    )
    if not variant:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    group = await db[cp.GROUPS].find_one(
        {"public_id": public_group["public_id"]},
        {"_id": 0, "id": 1, "current_published_version_id": 1},
    )
    if not group or not group.get("current_published_version_id"):
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    version = await db[cp.VERSIONS].find_one(
        {"id": group["current_published_version_id"]},
        {"_id": 0, "products": 1},
    )
    product = next(
        (
            p
            for p in (version or {}).get("products") or []
            if p.get("public_variant_id") == target_vid
        ),
        None,
    )
    if not product or not product.get("accurate_item_id"):
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    return await catalog.get_product_image(
        db, group_id=group["id"], accurate_item_id=product["accurate_item_id"]
    )


async def get_public_gallery_image(
    db, group_public_id: str, public_variant_id: str, image_id: str
):
    from services import public_catalog_images as images

    public_group = await get_public_group(db, group_public_id)
    target_vid = str(public_variant_id or "").strip()
    variant = next(
        (
            v
            for v in (public_group.get("variants") or [])
            if v.get("public_variant_id") == target_vid
        ),
        None,
    )
    if not variant:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    gallery_ids = {img.get("id") for img in (variant.get("images") or [])}
    if image_id not in gallery_ids:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    group = await db[cp.GROUPS].find_one(
        {"public_id": public_group["public_id"]},
        {"_id": 0, "id": 1, "current_published_version_id": 1},
    )
    version = await db[cp.VERSIONS].find_one(
        {"id": (group or {}).get("current_published_version_id")},
        {"_id": 0, "products": 1},
    )
    product = next(
        (
            p
            for p in (version or {}).get("products") or []
            if p.get("public_variant_id") == target_vid
        ),
        None,
    )
    if not group or not product or not product.get("accurate_item_id"):
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    return await images.get_variant_gallery_image(
        db,
        pricing_group_id=group["id"],
        accurate_item_id=product["accurate_item_id"],
        image_id=image_id,
    )


def _validate_quantity(quantity: Any) -> int:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise HTTPException(400, "Jumlah pesanan tidak valid.")
    if quantity > cp.MAX_CUSTOMER_PRICING_QTY:
        raise HTTPException(
            400,
            f"Jumlah pesanan melebihi batas maksimum "
            f"{cp.MAX_CUSTOMER_PRICING_QTY:,} pcs.".replace(",", "."),
        )
    return quantity


async def calculate_public_price(
    db, *, group_public_id: str, public_variant_id: str, quantity: Any
) -> dict:
    quantity = _validate_quantity(quantity)
    group = await get_public_group(db, group_public_id)
    variants = [
        variant
        for variant in group["variants"]
        if variant["public_variant_id"] == str(public_variant_id or "").strip()
    ]
    if len(variants) != 1:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    variant = variants[0]
    minimum = variant["minimum_quantity"]
    if quantity < minimum:
        formatted_minimum = f"{minimum:,}".replace(",", ".")
        unit = variant.get("unit") or "pcs"
        raise HTTPException(
            422,
            f"Minimum pemesanan untuk produk ini adalah {formatted_minimum} {unit}.",
        )
    resolved = cp.resolve_quantity_tier(variant["tiers"], quantity)
    if not resolved:
        raise HTTPException(
            422, "Jumlah pesanan berada di luar tier harga yang tersedia."
        )
    _tier_index, selected_tier = resolved
    unit_price = selected_tier["unit_price"]
    total = money_round(Decimal(quantity) * Decimal(str(unit_price)))
    return {
        "group": {"public_id": group["public_id"], "name": group["name"]},
        "variant": {
            "public_variant_id": variant["public_variant_id"],
            "display_name": variant["display_name"],
            "unit": variant["unit"],
        },
        "quantity": quantity,
        "selected_tier": {
            "min_qty": selected_tier["min_qty"],
            "max_qty": selected_tier["max_qty"],
        },
        "unit_price": unit_price,
        "total": total,
        "currency": "IDR",
        "calculated_at": now_iso(),
    }
