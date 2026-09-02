"""Read-only Pesanan Penjualan helpers. Does not mutate orders."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from services.order_prediction import is_valid_business_order, parse_iso_date
from services.sales_activity.clock import parse_dt
from services.sales_activity.constants import KIND_CUSTOMER, KIND_DORMANT, KIND_PROSPECT
from services.sales_activity.util import is_blank, normalize_text, strong_text


def _customer_name(order: dict) -> str:
    return str(order.get("customer") or order.get("customer_name") or "").strip()


def _outlet_name(order: dict) -> str:
    return str(order.get("outlet") or "").strip()


def _customer_id(order: dict) -> Optional[str]:
    text = str(order.get("customer_id") or "").strip()
    return text or None


def last_order_at(order: Optional[dict]):
    if not order:
        return None
    dt = parse_dt(order.get("order_date") or order.get("created_at"))
    return dt


def _name_filter(field: str, value: str) -> dict:
    return {field: {"$regex": f"^{re.escape(value)}$", "$options": "i"}}


async def find_matching_orders(db, *, customer_name: str = "", outlet_name: str = "", customer_id: str = "") -> List[dict]:
    query: Dict[str, Any] = {"cancelled": {"$ne": True}}
    cid = strong_text(customer_id)
    cname = strong_text(customer_name)
    oname = strong_text(outlet_name)
    if cid:
        query["customer_id"] = cid
    elif cname:
        query.update(_name_filter("customer", cname))
        if oname:
            query.update(_name_filter("outlet", oname))
    else:
        return []
    rows = await db.orders.find(query, {"_id": 0}).sort([("order_date", -1), ("created_at", -1)]).to_list(50)
    return [row for row in rows if is_valid_business_order(row)]


async def last_valid_sales_order(db, *, customer_name: str = "", outlet_name: str = "", customer_id: str = "") -> Optional[dict]:
    rows = await find_matching_orders(db, customer_name=customer_name, outlet_name=outlet_name, customer_id=customer_id)
    return rows[0] if rows else None


def classify_customer(last_order: Optional[dict], dormant_days: int, now) -> Tuple[str, Optional[str]]:
    if not last_order:
        return KIND_PROSPECT, None
    at = last_order_at(last_order)
    iso = at.isoformat() if at else None
    if at is None:
        return KIND_CUSTOMER, iso
    threshold = int(dormant_days or 90)
    age = (now - at).total_seconds() / 86400.0
    if age >= threshold:
        return KIND_DORMANT, iso
    return KIND_CUSTOMER, iso


def _group_key(order: dict) -> tuple:
    return (
        _customer_id(order) or normalize_text(_customer_name(order)),
        normalize_text(_outlet_name(order)),
    )


def match_outlet_to_orders(outlet: dict, orders_index: List[dict]) -> dict:
    """Safe exact identity match. Ambiguous names become a conflict, never a guess."""
    cid = strong_text(outlet.get("linked_customer_id") or outlet.get("customer_id"))
    name = strong_text(outlet.get("name"))
    name_n = normalize_text(name or "")
    hits = []
    if cid:
        hits = [o for o in orders_index if _customer_id(o) == cid]
    elif name:
        customer_hits = [o for o in orders_index if normalize_text(_customer_name(o)) == name_n]
        outlet_hits = [o for o in customer_hits if normalize_text(_outlet_name(o)) == name_n]
        if outlet_hits:
            hits = outlet_hits
        elif customer_hits:
            hits = customer_hits
        else:
            hits = [o for o in orders_index if normalize_text(_outlet_name(o)) == name_n]
    unique_customers = []
    seen = set()
    for order in hits:
        key = _group_key(order)
        if key in seen:
            continue
        seen.add(key)
        unique_customers.append(order)
    if not unique_customers:
        return {"status": "NONE", "order": None, "ambiguous": False}
    if len(unique_customers) > 1:
        return {"status": "AMBIGUOUS", "order": None, "ambiguous": True, "candidates": unique_customers[:8]}
    return {"status": "MATCHED", "order": unique_customers[0], "ambiguous": False}
