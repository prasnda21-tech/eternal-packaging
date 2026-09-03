"""Read-only public Customer Pricing catalog and calculator.

Only the current Published version is read. Final selling prices come from the
frozen Phase 1 product ``tier_prices`` snapshots; live HPP services are never
consulted.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services import customer_pricing as cp
from services import customer_pricing_catalog as catalog
from services.purchase_common import money_round, now_iso

MAX_PUBLIC_GROUPS = 200
PUBLIC_NOT_FOUND = "Produk harga tidak tersedia."


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


def _serialize_group(
    group: dict, version: dict, active_accurate_item_ids: set[str]
) -> Optional[dict]:
    if (
        version.get("status") != cp.STATUS_PUBLISHED
        or version.get("id") != group.get("current_published_version_id")
        or version.get("pricing_group_id") != group.get("id")
    ):
        return None
    public_id = _public_ref(group.get("public_id"))
    name = str(version.get("group_name") or group.get("group_name") or "").strip()
    if not public_id or not name:
        return None
    variants = []
    seen_variant_ids = set()
    for product in version.get("products") or []:
        accurate_item_id = str(product.get("accurate_item_id") or "")
        if product.get("is_active") is False or accurate_item_id not in active_accurate_item_ids:
            continue
        public_variant_id = _public_ref(product.get("public_variant_id"))
        display_name = str(product.get("display_name") or "").strip()
        tiers = _public_tiers(version, product)
        if not public_variant_id or not display_name or not tiers:
            continue
        if public_variant_id in seen_variant_ids:
            return None
        seen_variant_ids.add(public_variant_id)
        variants.append(
            {
                "public_variant_id": public_variant_id,
                "display_name": display_name,
                "unit": str(product.get("unit") or "").strip() or None,
                "minimum_quantity": tiers[0]["min_qty"],
                "tiers": tiers,
            }
        )
    if not variants:
        return None
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
    return {
        "public_id": public_id,
        "name": name,
        "public_description": description,
        "image_url": image_url,
        "variants": variants,
    }


async def _load_catalog(db, *, group_public_id: Optional[str] = None) -> List[dict]:
    group_filter: Dict[str, Any] = {
        "enabled": True,
        "current_published_version_id": {"$nin": [None, ""]},
    }
    if group_public_id is not None:
        group_filter["public_id"] = str(group_public_id or "").strip()
    groups = await db[cp.GROUPS].find(group_filter, {"_id": 0}).sort(
        "group_name", 1
    ).to_list(MAX_PUBLIC_GROUPS)
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
    output = []
    for group in groups:
        version = versions_by_id.get(group.get("current_published_version_id"))
        if not version:
            continue
        serialized = _serialize_group(group, version, active_ids)
        if serialized:
            output.append(serialized)
    output.sort(key=lambda item: item["name"].casefold())
    return output


async def list_public_groups(db) -> List[dict]:
    groups = await _load_catalog(db)
    return [
        {
            "public_id": group["public_id"],
            "name": group["name"],
            "public_description": group["public_description"],
            "image_url": group["image_url"],
            "variants": [
                {
                    "public_variant_id": variant["public_variant_id"],
                    "display_name": variant["display_name"],
                    "unit": variant["unit"],
                }
                for variant in group["variants"]
            ],
        }
        for group in groups
    ]


async def get_public_group(db, group_public_id: str) -> dict:
    groups = await _load_catalog(db, group_public_id=group_public_id)
    if len(groups) != 1:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    return groups[0]


async def get_public_group_image(db, group_public_id: str):
    public_group = await get_public_group(db, group_public_id)
    group = await db[cp.GROUPS].find_one(
        {"public_id": public_group["public_id"]}, {"_id": 0, "id": 1}
    )
    if not group:
        raise HTTPException(404, PUBLIC_NOT_FOUND)
    return await catalog.get_catalog_image(db, group_id=group["id"])


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
