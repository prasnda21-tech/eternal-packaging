"""Read-only effective Published Customer Pricing HPP resolution."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

from services.customer_pricing import (
    GROUPS,
    HPP_MANUAL,
    MODE_HPP_FORMULA,
    STATUS_PUBLISHED,
    VERSIONS,
)
from services.purchase_common import money_round

SOURCE_PUBLISHED_FALLBACK = "PUBLISHED_CUSTOMER_PRICING_FALLBACK"


def _positive_money(value: Any):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0:
        return None
    return money_round(parsed)


async def load_effective_published_hpp(db) -> Dict[str, List[dict]]:
    """Use only enabled groups' current Published pointer; never Draft/history."""
    groups = await db[GROUPS].find(
        {
            "enabled": {"$ne": False},
            "current_published_version_id": {"$type": "string"},
        },
        {
            "_id": 0,
            "id": 1,
            "group_name": 1,
            "current_published_version_id": 1,
        },
    ).to_list(5000)
    version_ids = [
        str(group.get("current_published_version_id") or "")
        for group in groups
        if group.get("current_published_version_id")
    ]
    versions = await db[VERSIONS].find(
        {"id": {"$in": version_ids}, "status": STATUS_PUBLISHED},
        {"_id": 0},
    ).to_list(len(version_ids) or 1)
    versions_by_id = {str(version.get("id")): version for version in versions}
    result: Dict[str, List[dict]] = {}
    for group in groups:
        version = versions_by_id.get(str(group.get("current_published_version_id") or ""))
        if not version:
            continue
        valid_source = (
            version.get("pricing_mode") == MODE_HPP_FORMULA
            and version.get("hpp_source") == HPP_MANUAL
        )
        for product in version.get("products") or []:
            aid = str(product.get("accurate_item_id") or "").strip()
            if not aid:
                continue
            result.setdefault(aid, []).append({
                "source": SOURCE_PUBLISHED_FALLBACK,
                "pricing_group_id": group.get("id"),
                "group_name": group.get("group_name"),
                "version_id": version.get("id"),
                "version_number": version.get("version_number"),
                "revision": version.get("revision") or 1,
                "published_at": version.get("published_at") or version.get("effective_at"),
                "published_master_item_id": product.get("master_item_id"),
                "hpp": _positive_money(product.get("hpp_snapshot")) if valid_source else None,
                "source_valid": valid_source,
            })
    return result


def exact_published_candidate(
    candidates: Dict[str, List[dict]],
    *,
    accurate_item_id: str,
    master_item_id: str,
):
    options = candidates.get(str(accurate_item_id or "").strip()) or []
    if len(options) != 1:
        return None
    candidate = options[0]
    if (
        not candidate.get("source_valid")
        or candidate.get("hpp") is None
        or candidate.get("published_master_item_id") not in (None, "", master_item_id)
    ):
        return None
    return candidate
