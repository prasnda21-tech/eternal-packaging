"""Copy-safe Repeat Order source lookup for Pesanan Penjualan.

Read-only. Never mutates source orders. Does not deep-copy Mongo documents.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.order_prediction import is_valid_business_order

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
    # Drop any unexpected keys — never carry line_id / workflow / discount.
    return {k: out[k] for k in _LINE_COPY_FIELDS}


def _copy_design_file(raw: Any) -> Optional[dict]:
    """Design classification is order.design_files array membership (canonical)."""
    if not isinstance(raw, dict):
        return None
    data_url = raw.get("data_url")
    if not isinstance(data_url, str) or not data_url.startswith("data:"):
        return None
    name = _clean_str(raw.get("name")) or "design"
    content_type = _clean_str(raw.get("content_type")) or ""
    # Do not reuse source file id — caller/create path assigns new identity.
    return {
        "name": name,
        "data_url": data_url,
        "content_type": content_type,
    }


def build_repeat_source_dto(order: dict) -> Dict[str, Any]:
    """Explicit copy-safe DTO. Excludes operational identity and workflow history."""
    if not isinstance(order, dict):
        raise ValueError("order must be a dict")

    line_items: List[dict] = []
    for raw in order.get("line_items") or []:
        mapped = _copy_line_item(raw)
        if mapped:
            line_items.append(mapped)

    design_files: List[dict] = []
    for raw in order.get("design_files") or []:
        mapped = _copy_design_file(raw)
        if mapped:
            design_files.append(mapped)

    header = {field: order.get(field) for field in _HEADER_COPY_FIELDS}
    # Normalize strings for form-friendly defaults.
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
            header[key] = _clean_str(val)
        else:
            header[key] = str(val).strip() if val is not None else ""

    return {
        # Display / confirmation context only — never used as new PO identity.
        "source_po_number": _clean_str(order.get("po_number")) or "",
        "source_created_at": _clean_str(order.get("created_at")) or "",
        "source_order_date": _clean_str(order.get("order_date")) or "",
        "line_count": len(line_items),
        "design_file_count": len(design_files),
        # Reusable create-form fields
        **header,
        "line_items": line_items,
        "design_files": design_files,
    }


async def find_latest_valid_repeat_source(db, customer_id: str) -> Optional[dict]:
    """Most recently created valid PO for exact customer_id.

    Uses created_at (system entry timestamp), then id for deterministic ties.
    Excludes cancelled and invalid business orders. Read-only.
    """
    cid = _clean_str(customer_id)
    if not cid:
        return None

    query = {
        "customer_id": cid,
        "cancelled": {"$ne": True},
    }
    # Fetch a small window so invalid/test rows can be skipped without N+1.
    rows = await (
        db.orders.find(query, {"_id": 0})
        .sort([("created_at", -1), ("id", -1)])
        .limit(30)
        .to_list(30)
    )
    for row in rows:
        if not is_valid_business_order(row):
            continue
        if _clean_str(row.get("customer_id")) != cid:
            continue
        return row
    return None
