"""PWA Sales confirmed-delivery history (read-only).

Source of truth is official ``orders.delivery_confirmations`` written by
``POST /orders/{id}/confirm-delivered``. Scheduled SJ rows and
``sales_deliveries.status`` SELESAI/SEBAGIAN are never treated as delivered.

v1 excludes synthetic legacy rows (orders without confirmations). Cancelled or
superseded shipments are excluded. Ownership is always
``orders.sales_user_id ==`` the authenticated session user; request sales ids
must be ignored by the HTTP layer.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from quantity_rules import find_shipment, shipment_is_active
from services.customer_identity import display_customer_name
from services.sales_pwa import require_sales_pwa
from services.sales_user_mapping import sales_owner_match, session_user_id
from services.tanggal_pengiriman import WIB, parse_calendar_date

_WS_RE = re.compile(r"\s+")

HISTORY_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "outlet": 1,
    "sales_user_id": 1,
    "cancelled": 1,
    "line_items": 1,
    "shipments": 1,
    "delivery_confirmations": 1,
    "delivery_proofs": 1,
    "delivery_recipient_signature": 1,
    "delivery_driver_signature": 1,
}

HISTORY_PAGE_SIZE_DEFAULT = 25
HISTORY_PAGE_SIZE_MAX = 100
STATUS_TERKIRIM = "Terkirim"


def _norm(text: Any) -> str:
    return _WS_RE.sub(" ", str(text or "").strip().lower())


def _keywords(text: Any) -> List[str]:
    return [part for part in _norm(text).split(" ") if part]


def _text_match(haystack: Any, query: Any) -> bool:
    """Multi-keyword AND contains; case-insensitive; order-independent."""
    needles = _keywords(query)
    if not needles:
        return True
    hay = _norm(haystack)
    return all(n in hay for n in needles)


def _parse_instant(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=WIB)
    return dt


def confirmed_day_wib(raw: Any) -> Optional[str]:
    """Calendar day of confirmed_at in Asia/Jakarta (YYYY-MM-DD)."""
    instant = _parse_instant(raw)
    if instant is None:
        return None
    local = instant.astimezone(WIB)
    return f"{local.year:04d}-{local.month:02d}-{local.day:02d}"


def _safe_int(raw: Any) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _confirmation_active(rec: dict) -> bool:
    if not isinstance(rec, dict):
        return False
    if rec.get("is_cancelled") or rec.get("cancelled") or rec.get("is_deleted") or rec.get("deleted"):
        return False
    return True


def _resolve_unit(order: dict, item: dict) -> Tuple[Optional[str], Optional[str]]:
    """Return (unit, ambiguity_note). Unit only when line mapping is unambiguous."""
    lis = [li for li in (order.get("line_items") or []) if isinstance(li, dict)]
    line_id = str(item.get("line_id") or "").strip()
    idx = _safe_int(item.get("line_idx"))

    by_id: List[dict] = []
    if line_id:
        for li in lis:
            lid = str(li.get("line_id") or li.get("id") or "").strip()
            if lid and lid == line_id:
                by_id.append(li)
        if len(by_id) > 1:
            return None, "line_id_ambiguous"
        if len(by_id) == 1:
            unit = str(by_id[0].get("unit") or "").strip()
            return (unit or None), None

    if idx is not None and 0 <= idx < len(lis):
        if line_id:
            # Claimed line_id absent from SO — do not invent unit from index alone.
            return None, "line_id_unmatched"
        unit = str(lis[idx].get("unit") or "").strip()
        return (unit or None), None

    if line_id or idx is not None:
        return None, "line_unresolved"
    return None, None


def _sj_number(order: dict, rec: dict, shipment: Optional[dict]) -> str:
    for raw in (
        rec.get("sj_number"),
        (shipment or {}).get("number"),
        (shipment or {}).get("sj_number"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _customer_label(order: dict) -> str:
    return str(display_customer_name(order) or "").strip()


def _outlet_label(order: dict, shipment: Optional[dict]) -> str:
    for raw in (
        (shipment or {}).get("outlet"),
        order.get("outlet"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _proof_summaries(order: dict, proof_ids: List[str]) -> List[dict]:
    by_id = {
        str(p.get("id") or "").strip(): p
        for p in (order.get("delivery_proofs") or [])
        if isinstance(p, dict) and str(p.get("id") or "").strip()
    }
    out: List[dict] = []
    for pid in proof_ids:
        p = by_id.get(pid)
        if not p:
            continue
        out.append({
            "id": pid,
            "uploaded_at": p.get("uploaded_at") or p.get("created_at"),
            "content_type": p.get("content_type"),
            "available": bool(str(p.get("storage_path") or "").strip()),
        })
    return out


def flatten_confirmation_rows(order: dict) -> List[dict]:
    """One public row per confirmation item on an active shipment. No legacy synthetic."""
    if not isinstance(order, dict) or order.get("cancelled"):
        return []
    order_id = str(order.get("id") or "").strip()
    if not order_id:
        return []
    rows: List[dict] = []
    seen: set = set()
    for rec in order.get("delivery_confirmations") or []:
        if not _confirmation_active(rec):
            continue
        conf_id = str(rec.get("id") or "").strip()
        if not conf_id:
            continue
        confirmed_at = str(rec.get("confirmed_at") or "").strip()
        if not confirmed_at:
            continue
        day = confirmed_day_wib(confirmed_at)
        if not day:
            continue
        shipment = find_shipment(order, rec.get("shipment_id"))
        if shipment is None or not shipment_is_active(shipment):
            continue
        items = [it for it in (rec.get("items") or []) if isinstance(it, dict)]
        if not items:
            continue
        sj = _sj_number(order, rec, shipment)
        customer = _customer_label(order)
        outlet = _outlet_label(order, shipment)
        proof_ids = [str(x).strip() for x in (rec.get("proof_ids") or []) if str(x or "").strip()]
        for item_idx, item in enumerate(items):
            qty = _safe_int(item.get("qty"))
            if qty is None or qty <= 0:
                continue
            line_idx = _safe_int(item.get("line_idx"))
            line_id = str(item.get("line_id") or "").strip() or None
            dedupe = f"{conf_id}:{line_id or ''}:{line_idx if line_idx is not None else ''}:{item_idx}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            unit, unit_note = _resolve_unit(order, item)
            name = str(item.get("name") or "").strip()
            row_id = f"{order_id}:{conf_id}:{item_idx}"
            rows.append({
                "id": row_id,
                "order_id": order_id,
                "confirmation_id": conf_id,
                "shipment_id": str(rec.get("shipment_id") or "").strip() or None,
                "confirmed_at": confirmed_at,
                "confirmed_day": day,
                "customer": customer,
                "outlet": outlet,
                "po_number": str(order.get("po_number") or "").strip(),
                "sj_number": sj,
                "item_name": name,
                "qty": qty,
                "unit": unit,
                "unit_ambiguous": bool(unit_note),
                "unit_note": unit_note,
                "status": STATUS_TERKIRIM,
                "status_label": STATUS_TERKIRIM,
                "line_idx": line_idx,
                "line_id": line_id,
                "recipient_name": str(rec.get("recipient_name") or "").strip() or None,
                "has_recipient_signature": bool(rec.get("has_recipient_signature")),
                "has_driver_signature": bool(rec.get("has_driver_signature")),
                "proof_ids": proof_ids,
                "proofs": _proof_summaries(order, proof_ids),
                "driver_name": str(rec.get("driver_name") or "").strip() or None,
                "vehicle_plate": str(rec.get("vehicle_plate") or "").strip() or None,
            })
    return rows


def _row_sort_key(row: dict) -> tuple:
    return (
        str(row.get("confirmed_at") or ""),
        str(row.get("po_number") or ""),
        str(row.get("sj_number") or ""),
        str(row.get("confirmation_id") or ""),
        int(row.get("line_idx") if row.get("line_idx") is not None else 10**9),
        str(row.get("id") or ""),
    )


def filter_history_rows(
    rows: List[dict],
    *,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
    po_q: str = "",
    sj_q: str = "",
    item_q: str = "",
    qty_min: Optional[int] = None,
    qty_max: Optional[int] = None,
    status: str = "",
) -> List[dict]:
    day_from = parse_calendar_date(date_from) or ""
    day_to = parse_calendar_date(date_to) or ""
    status_needle = str(status or "").strip().lower()
    out: List[dict] = []
    for row in rows:
        day = str(row.get("confirmed_day") or "")
        if day_from and (not day or day < day_from):
            continue
        if day_to and (not day or day > day_to):
            continue
        customer_hay = f"{row.get('customer') or ''} {row.get('outlet') or ''}"
        if not _text_match(customer_hay, customer_q):
            continue
        if not _text_match(row.get("po_number"), po_q):
            continue
        if not _text_match(row.get("sj_number"), sj_q):
            continue
        if not _text_match(row.get("item_name"), item_q):
            continue
        qty = _safe_int(row.get("qty"))
        if qty_min is not None and (qty is None or qty < qty_min):
            continue
        if qty_max is not None and (qty is None or qty > qty_max):
            continue
        if status_needle and status_needle not in ("all", "semua", "semua status"):
            label = _norm(row.get("status") or row.get("status_label"))
            if status_needle not in label and label != status_needle:
                continue
        out.append(row)
    return out


def build_sales_delivery_history(
    orders: List[dict],
    user: dict,
    *,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
    po_q: str = "",
    sj_q: str = "",
    item_q: str = "",
    qty_min: Optional[int] = None,
    qty_max: Optional[int] = None,
    status: str = "",
    skip: int = 0,
    limit: int = HISTORY_PAGE_SIZE_DEFAULT,
) -> dict:
    owner_id = session_user_id(user)
    flat: List[dict] = []
    for order in orders or []:
        if not isinstance(order, dict):
            continue
        if str(order.get("sales_user_id") or "").strip() != owner_id:
            continue
        flat.extend(flatten_confirmation_rows(order))

    filtered = filter_history_rows(
        flat,
        date_from=date_from,
        date_to=date_to,
        customer_q=customer_q,
        po_q=po_q,
        sj_q=sj_q,
        item_q=item_q,
        qty_min=qty_min,
        qty_max=qty_max,
        status=status,
    )
    filtered.sort(key=_row_sort_key, reverse=True)

    skip_n = max(0, int(skip or 0))
    limit_n = max(1, min(int(limit or HISTORY_PAGE_SIZE_DEFAULT), HISTORY_PAGE_SIZE_MAX))
    page = filtered[skip_n:skip_n + limit_n]
    unit_ambiguous_count = sum(1 for row in filtered if row.get("unit_ambiguous"))

    return {
        "rows": page,
        "total": len(filtered),
        "skip": skip_n,
        "limit": limit_n,
        "timezone": "Asia/Jakarta",
        "source": "orders.delivery_confirmations",
        "sales_user_id": owner_id,
        "unit_ambiguous_count": unit_ambiguous_count,
        "loaded_at": datetime.now(timezone.utc).isoformat(),
    }


async def list_sales_delivery_history(
    db,
    user: dict,
    *,
    date_from: str = "",
    date_to: str = "",
    customer_q: str = "",
    po_q: str = "",
    sj_q: str = "",
    item_q: str = "",
    qty_min: Optional[int] = None,
    qty_max: Optional[int] = None,
    status: str = "",
    skip: int = 0,
    limit: int = HISTORY_PAGE_SIZE_DEFAULT,
) -> dict:
    """Read-only confirmed delivery history for the session sales user."""
    require_sales_pwa(user)
    match = {
        "cancelled": {"$ne": True},
        **sales_owner_match(user),
        "delivery_confirmations.0": {"$exists": True},
    }
    cursor = db.orders.find(match, HISTORY_PROJECTION)
    docs: List[dict] = []
    async for doc in cursor:
        docs.append(doc)
    return build_sales_delivery_history(
        docs,
        user,
        date_from=date_from,
        date_to=date_to,
        customer_q=customer_q,
        po_q=po_q,
        sj_q=sj_q,
        item_q=item_q,
        qty_min=qty_min,
        qty_max=qty_max,
        status=status,
        skip=skip,
        limit=limit,
    )
