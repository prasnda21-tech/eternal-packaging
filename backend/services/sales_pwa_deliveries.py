"""Today's deliveries for PWA Sales.

Source of truth is Jadwal Pengiriman (`services.delivery_schedule.schedule_rows`):
one active canonical Surat Jalan (`orders.shipments` with delivery_date, SJ id,
and SJ number). The business day is Asia/Jakarta, the same clock as
`delivery_schedule.today_key`.

Ownership is `orders.sales_user_id ==` the authenticated users.id. Callers must
not pass a sales id from the request. A shipment that also carries lines for
another PO or sales user contributes only the logged-in sales portion; counts
use that portion. PO created_at, invoice dates, and estimated delivery are ignored.
Scheduled line qty is shown, never the PO total. Sales can only read this list.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from services.delivery_schedule import (
    SCHEDULE_PROJECTION,
    parse_date_key,
    schedule_rows,
    today_key,
)
from services.sales_pwa import owns_order, require_sales_pwa
from services.sales_user_mapping import session_user_id
from services.tanggal_pengiriman import WIB

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")

SALES_TODAY_PROJECTION = {
    **SCHEDULE_PROJECTION,
    "sales_user_id": 1,
    "outlet": 1,
    "recipient": 1,
    "shipments.address": 1,
    "shipments.shipping_address": 1,
    "shipments.outlet": 1,
    "shipments.recipient": 1,
    "shipments.delivery_time": 1,
    "shipments.time": 1,
    "shipments.schedule_time": 1,
    "shipments.cancelled": 1,
    "shipments.is_cancelled": 1,
    "shipments.deleted": 1,
    "shipments.is_deleted": 1,
    "shipments.superseded": 1,
    "shipments.superseded_by_shipment_id": 1,
    "shipments.status": 1,
    "shipments.sj_status": 1,
}

_OWN_LINE_KEYS = ("po_id", "order_id", "sales_order_id")


def jakarta_today_key(now: Optional[datetime] = None) -> str:
    """Calendar date of Jadwal Pengiriman. Fixed UTC+7, same as Asia/Jakarta."""
    return today_key(now)


def _parse_instant(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text or "T" not in text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return dt


def schedule_clock(raw: Any) -> Optional[str]:
    """HH:MM in Asia/Jakarta when a real clock is stored. Date-only stays blank."""
    text = str(raw or "").strip()
    if not text:
        return None
    match = _HHMM.match(text)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
        return None
    instant = _parse_instant(text)
    if instant is None:
        return None
    local = instant.astimezone(WIB)
    if local.hour == 0 and local.minute == 0 and local.second == 0 and local.microsecond == 0:
        return None
    return f"{local.hour:02d}:{local.minute:02d}"


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _location_address(raw: Any) -> str:
    if not isinstance(raw, dict):
        return ""
    return _first_text(raw.get("formatted_address"), raw.get("address"))


def delivery_destination(order: dict, shipment: Optional[dict]) -> str:
    sh = shipment if isinstance(shipment, dict) else {}
    return _first_text(
        sh.get("address"),
        sh.get("shipping_address"),
        _location_address(sh.get("delivery_location")),
        _location_address(order.get("delivery_location")),
        order.get("address"),
        sh.get("outlet"),
        order.get("outlet"),
        sh.get("recipient"),
        order.get("recipient"),
    )


def _shipment_for_row(order: dict, row: dict) -> Optional[dict]:
    want = str(row.get("shipment_id") or "").strip()
    if not want:
        return None
    for sh in order.get("shipments") or []:
        if not isinstance(sh, dict):
            continue
        sid = str(sh.get("id") or sh.get("sales_delivery_id") or "").strip()
        if sid == want:
            return sh
    return None


def _line_is_foreign(line: dict, order: dict, owner_id: str) -> bool:
    own_refs = {
        str(order.get("id") or "").strip(),
        str(order.get("po_number") or "").strip(),
    }
    own_refs.discard("")
    for key in _OWN_LINE_KEYS:
        ref = str(line.get(key) or "").strip()
        if ref and ref not in own_refs:
            return True
    sales_ref = str(line.get("sales_user_id") or "").strip()
    if sales_ref and owner_id and sales_ref != owner_id:
        return True
    return False


def _owned_shipment(order: dict, shipment: dict, owner_id: str) -> Tuple[dict, bool]:
    """Drop lines that belong to another PO or sales user. Qty must not keep them."""
    lines = shipment.get("lines")
    if not isinstance(lines, list) or not lines:
        return shipment, False
    kept: List[dict] = []
    foreign = False
    for line in lines:
        if isinstance(line, dict) and _line_is_foreign(line, order, owner_id):
            foreign = True
            continue
        if isinstance(line, dict):
            kept.append(line)
    if not foreign:
        return shipment, False
    clone = dict(shipment)
    clone["lines"] = kept
    for key in ("total_qty", "qty", "quantity"):
        clone.pop(key, None)
    return clone, True


def _scheduled_items(shipment: dict) -> List[dict]:
    from services.delivery_schedule import sj_item_lines_for_shipment

    items = []
    for line in sj_item_lines_for_shipment(shipment):
        items.append({
            "name": str(line.get("name") or ""),
            "qty": line.get("qty"),
        })
    return items


def _scheduled_qty(row: dict, shipment: dict, restricted: bool) -> int:
    from quantity_rules import delivery_batch_qty

    if restricted:
        return int(delivery_batch_qty(shipment) or 0)
    try:
        return int(row.get("qty_sj") or 0)
    except (TypeError, ValueError):
        return int(delivery_batch_qty(shipment) or 0)


def _public_row(order: dict, row: dict, shipment: dict, *, owner_id: str) -> Optional[dict]:
    view, restricted = _owned_shipment(order, shipment, owner_id)
    if restricted and not (view.get("lines") or []):
        return None
    items = _scheduled_items(view)
    qty = _scheduled_qty(row, view, restricted)
    if restricted and qty <= 0 and not items:
        return None
    clock = schedule_clock(shipment.get("delivery_time")) or schedule_clock(shipment.get("schedule_time")) or schedule_clock(shipment.get("time"))
    if not clock:
        clock = schedule_clock(shipment.get("delivery_date"))
    order_id = str(order.get("id") or "")
    shipment_id = str(row.get("shipment_id") or "")
    return {
        "id": order_id,
        "row_id": f"{order_id}:{shipment_id}",
        "shipment_id": shipment_id,
        "po_number": str(row.get("order_number") or order.get("po_number") or ""),
        "customer": str(row.get("customer_name") or order.get("customer_name") or order.get("customer") or ""),
        "schedule_date": parse_date_key(row.get("tanggal_pengiriman")) or "",
        "schedule_time": clock,
        "destination": delivery_destination(order, shipment),
        "status": row.get("status") or "",
        "status_label": row.get("status_label") or "",
        "qty": qty,
        "items": items,
    }


def build_sales_today_deliveries(
    orders: List[dict],
    user: dict,
    *,
    today: Optional[str] = None,
) -> dict:
    """Rows and totals for one sales user. Foreign POs never enter the summary."""
    day = parse_date_key(today) or jakarta_today_key()
    owner_id = session_user_id(user)
    rows: List[dict] = []
    for order in orders or []:
        if not isinstance(order, dict) or order.get("cancelled"):
            continue
        if not owns_order(order, user):
            continue
        for row in schedule_rows(order, today=day):
            if parse_date_key(row.get("tanggal_pengiriman")) != day:
                continue
            shipment = _shipment_for_row(order, row)
            if shipment is None:
                continue
            public = _public_row(order, row, shipment, owner_id=owner_id)
            if public is None:
                continue
            rows.append(public)
    rows.sort(key=lambda row: (
        row.get("schedule_time") or "99:99",
        row.get("po_number") or "",
        row.get("shipment_id") or "",
    ))
    po_ids = {row.get("id") for row in rows if row.get("id")}
    return {
        "date": day,
        "timezone": "Asia/Jakarta",
        "source": "delivery_schedule.schedule_rows",
        "sales_user_id": owner_id,
        "total": len(rows),
        "po_count": len(po_ids),
        "qty": sum(int(row.get("qty") or 0) for row in rows),
        "rows": rows,
    }


async def list_sales_today_deliveries(db, user: dict, *, now: Optional[datetime] = None) -> dict:
    """Read today's Jadwal Pengiriman rows owned by the session user."""
    uid = require_sales_pwa(user)
    day = jakarta_today_key(now)
    cursor = db.orders.find(
        {
            "cancelled": {"$ne": True},
            "sales_user_id": uid,
            "shipments": {
                "$elemMatch": {
                    "delivery_date": {"$exists": True, "$nin": [None, ""]},
                }
            },
        },
        SALES_TODAY_PROJECTION,
    )
    docs: List[dict] = []
    async for doc in cursor:
        docs.append(doc)
    return build_sales_today_deliveries(docs, user, today=day)
