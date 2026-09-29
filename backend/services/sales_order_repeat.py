"""Copy-safe Repeat Order source lookup for Pesanan Penjualan.

Read-only. Never mutates source orders. Does not deep-copy Mongo documents.
Artwork/design files are never included — user must re-upload.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.customer_identity import (
    display_customer_name,
    extract_stable_customer_id,
    is_legacy_composite_customer_id,
    normalize_customer_name,
)
from services.order_prediction import is_valid_business_order
from services.sales_customers import COLLECTION as SALES_CUSTOMERS

# Explicit reusable create-form line fields only.
_LINE_COPY_FIELDS = (
    "item_code",
    "name",
    "quantity",
    "unit",
    "color",
    "production_process",
    "notes",
    "accurate_item_id",
    "master_item_id",
)

# Header fields that map onto SalesOrderCreatePayload / create form.
_HEADER_COPY_FIELDS = (
    "customer",
    "customer_id",
    "customer_phone",
    "outlet",
    "recipient",
    "address",
    "delivery_zone",
    "payment_terms",
    "sales",
    "expedition",
    "estimated_delivery",
    "deadline",
    "notes",
    "order_date",
)

# Lean projection — never pull attachment blobs or workflow history.
REPEAT_SOURCE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "created_at": 1,
    "order_date": 1,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
    "customer_phone": 1,
    "outlet": 1,
    "recipient": 1,
    "address": 1,
    "delivery_zone": 1,
    "payment_terms": 1,
    "sales": 1,
    "sales_user_id": 1,
    "expedition": 1,
    "estimated_delivery": 1,
    "deadline": 1,
    "notes": 1,
    "line_items": 1,
    "cancelled": 1,
    "is_test": 1,
    "is_demo": 1,
    "is_preview": 1,
    "test_record": 1,
}


def _clean_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _copy_line_item(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    name = _clean_str(raw.get("name"))
    if not name:
        return None
    try:
        qty = int(raw.get("quantity"))
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return None
    proc = (_clean_str(raw.get("production_process")) or "print").lower()
    if proc not in ("print", "non_print"):
        proc = "print"
    out = {
        "item_code": _clean_str(raw.get("item_code")),
        "name": name,
        "quantity": qty,
        "unit": _clean_str(raw.get("unit")) or "pcs",
        "color": _clean_str(raw.get("color")) or "",
        "production_process": proc,
        "notes": _clean_str(raw.get("notes")) or "",
        "accurate_item_id": _clean_str(raw.get("accurate_item_id")),
        "master_item_id": _clean_str(raw.get("master_item_id")),
    }
    return {k: out[k] for k in _LINE_COPY_FIELDS}


def build_repeat_source_dto(order: dict) -> Dict[str, Any]:
    """Explicit copy-safe DTO. Excludes operational identity, workflow, artwork."""
    if not isinstance(order, dict):
        raise ValueError("order must be a dict")

    line_items: List[dict] = []
    for raw in order.get("line_items") or []:
        mapped = _copy_line_item(raw)
        if mapped:
            line_items.append(mapped)

    header = {field: order.get(field) for field in _HEADER_COPY_FIELDS}
    for key in (
        "customer",
        "customer_id",
        "customer_phone",
        "outlet",
        "recipient",
        "address",
        "delivery_zone",
        "payment_terms",
        "sales",
        "expedition",
        "notes",
    ):
        val = header.get(key)
        if val is None:
            header[key] = "" if key != "customer_id" else None
        elif key == "customer_id":
            text = _clean_str(val)
            if text and is_legacy_composite_customer_id(
                text,
                customer=display_customer_name(order),
                outlet=str(order.get("outlet") or ""),
            ):
                header[key] = None
            else:
                header[key] = text
        else:
            header[key] = str(val).strip() if val is not None else ""

    return {
        "sales_user_id": _clean_str(order.get("sales_user_id")) or "",
        "source_po_number": _clean_str(order.get("po_number")) or "",
        "source_created_at": _clean_str(order.get("created_at")) or "",
        "source_order_date": _clean_str(order.get("order_date")) or "",
        "line_count": len(line_items),
        "design_file_count": 0,
        **header,
        "line_items": line_items,
        "design_files": [],
    }


def _is_unlinked_legacy(order: dict) -> bool:
    return not extract_stable_customer_id(order)


async def find_repeat_source_by_order_id(db, order_id: str) -> Optional[dict]:
    """Exact selected PO. Read-only. Skips cancelled/invalid business orders."""
    oid = _clean_str(order_id)
    if not oid:
        return None
    row = await db.orders.find_one({"id": oid}, REPEAT_SOURCE_PROJECTION)
    if not row or not is_valid_business_order(row):
        return None
    return row


async def _latest_by_customer_id(db, customer_id: str) -> Optional[dict]:
    cid = _clean_str(customer_id)
    if not cid:
        return None
    rows = await (
        db.orders.find(
            {"customer_id": cid, "cancelled": {"$ne": True}},
            REPEAT_SOURCE_PROJECTION,
        )
        .sort([("created_at", -1), ("id", -1)])
        .limit(30)
        .to_list(30)
    )
    for row in rows:
        if not is_valid_business_order(row):
            continue
        if extract_stable_customer_id(row) != cid:
            continue
        return row
    return None


async def _latest_unlinked_name_fallback(db, customer_id: str, master_name: str) -> Optional[dict]:
    """Legacy rows still missing canonical customer_id. Exact normalized name only."""
    cid = _clean_str(customer_id)
    display = (master_name or "").strip()
    norm = normalize_customer_name(display)
    if not cid or not norm:
        return None
    pattern = f"^{re.escape(display)}$"
    query = {
        "cancelled": {"$ne": True},
        "$or": [
            {"customer": {"$regex": pattern, "$options": "i"}},
            {"customer_name": {"$regex": pattern, "$options": "i"}},
        ],
    }
    rows = await (
        db.orders.find(query, REPEAT_SOURCE_PROJECTION)
        .sort([("created_at", -1), ("id", -1)])
        .limit(50)
        .to_list(50)
    )
    for row in rows:
        if not is_valid_business_order(row):
            continue
        stable = extract_stable_customer_id(row)
        if stable and stable != cid:
            continue
        if stable == cid:
            return row
        if not _is_unlinked_legacy(row):
            continue
        if normalize_customer_name(display_customer_name(row)) != norm:
            continue
        return row
    return None


async def find_latest_valid_repeat_source(db, customer_id: str) -> Optional[dict]:
    """Latest valid PO for canonical customer_id, then unlinked exact-name fallback."""
    cid = _clean_str(customer_id)
    if not cid:
        return None

    found = await _latest_by_customer_id(db, cid)
    if found:
        return found

    master = await db[SALES_CUSTOMERS].find_one({"id": cid}, {"_id": 0, "id": 1, "name": 1})
    master_name = (master or {}).get("name") or ""
    return await _latest_unlinked_name_fallback(db, cid, master_name)


def stamp_canonical_customer(dto: dict, customer_id: str, customer_name: str = "") -> dict:
    """Selected master remains lookup authority on latest-customer Repeat."""
    out = dict(dto or {})
    cid = _clean_str(customer_id)
    if cid:
        out["customer_id"] = cid
    name = (customer_name or "").strip()
    if name:
        out["customer"] = name
    return out
