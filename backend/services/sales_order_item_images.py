"""Read-only product photos for Pesanan Penjualan.

Images are the Customer Pricing product photos stored on a pricing group and
keyed by accurate_item_id. Lookup is by that id (or master_item_id →
accurate_item_id). Names are never matched, and the group cover is never used
as a fallback. The payload is only an image URL — no HPP, margin, or price
configuration.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

from services import customer_pricing as cp

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _clean_ids(values: Iterable) -> List[str]:
    seen = []
    for raw in values or []:
        text = str(raw or "").strip()
        if not text or not _SAFE_ID.match(text) or text in seen:
            continue
        seen.append(text)
        if len(seen) >= 80:
            break
    return seen


def split_id_param(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return _clean_ids(str(raw).split(","))


def _image_url(accurate_item_id: str, file_id: str) -> str:
    return f"/api/sales-orders/item-images/{accurate_item_id}?v={file_id}"


async def resolve_product_image_refs(
    db,
    *,
    accurate_item_ids: Iterable = (),
    master_item_ids: Iterable = (),
) -> Dict[str, dict]:
    """Map accurate_item_id → {accurate_item_id, url, group_id, file_id}.

    group_id and file_id stay server-side so the public caller can drop them.
    """
    aids = _clean_ids(accurate_item_ids)
    mids = _clean_ids(master_item_ids)
    if mids:
        rows = await db.master_items.find(
            {"id": {"$in": mids}},
            {"_id": 0, "id": 1, "accurate_item_id": 1},
        ).to_list(len(mids))
        for row in rows:
            aid = str(row.get("accurate_item_id") or "").strip()
            if _SAFE_ID.match(aid) and aid not in aids:
                aids.append(aid)
    if not aids:
        return {}

    groups = await db[cp.GROUPS].find(
        {"$or": [
            {f"product_images.{aid}.file_id": {"$exists": True, "$nin": [None, ""]}}
            for aid in aids
        ]},
        {
            "_id": 0,
            "id": 1,
            "updated_at": 1,
            "active_accurate_item_ids": 1,
            "product_images": 1,
        },
    ).to_list(200)

    chosen: Dict[str, dict] = {}
    for group in groups:
        images = group.get("product_images") or {}
        if not isinstance(images, dict):
            continue
        active = {str(item) for item in (group.get("active_accurate_item_ids") or [])}
        updated = str(group.get("updated_at") or "")
        group_id = str(group.get("id") or "")
        for aid in aids:
            image = images.get(aid)
            if not isinstance(image, dict):
                continue
            file_id = str(image.get("file_id") or "").strip()
            if not file_id:
                continue
            rank = (1 if aid in active else 0, updated, group_id)
            previous = chosen.get(aid)
            if previous and previous["_rank"] >= rank:
                continue
            chosen[aid] = {
                "_rank": rank,
                "accurate_item_id": aid,
                "group_id": group_id,
                "file_id": file_id,
                "url": _image_url(aid, file_id),
            }
    return chosen


def public_image_map(refs: Dict[str, dict]) -> Dict[str, dict]:
    """Drop storage and pricing-group internals. URL only."""
    public = {}
    for aid, ref in (refs or {}).items():
        url = str(ref.get("url") or "")
        if not url:
            continue
        public[str(aid)] = {
            "accurate_item_id": str(aid),
            "url": url,
        }
    return public
