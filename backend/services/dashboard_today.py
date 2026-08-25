"""Today's production work + shipments for Dashboard cards.

Timezone: WIB (UTC+7), same convention as operator_analytics / Analitik Output.
Pengerjaan uses Jadwal Produksi `work_date` (order + operator_splits), never
PO created_at as the sole signal.
Pengiriman uses Surat Jalan / shipment / loading / bukti / delivered timestamps.
Partial shipment stays partial — never treated as full PO terkirim.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from quantity_rules import (
    packing_required_total,
    qty_delivered,
    qty_loaded,
    qty_shipped,
    shipments_stored,
)

WIB = timezone(timedelta(hours=7))
PREVIEW_LIMIT = 8
STAGES = (
    "Order Masuk", "Design", "Approval Design", "Payment", "Approval Payment",
    "Penjadwalan", "Screener", "Persiapan Helper", "Operator", "Konfirmasi Sample",
    "Cetak", "Packing", "Surat Jalan", "Loading", "Terkirim",
)

WORK_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "outlet": 1,
    "cancelled": 1,
    "current_stage": 1,
    "status_label": 1,
    "work_date": 1,
    "total_quantity": 1,
    "line_items.name": 1,
    "line_items.quantity": 1,
    "operator_splits.operator_name": 1,
    "operator_splits.work_date": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.quantity": 1,
}

SHIP_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "outlet": 1,
    "cancelled": 1,
    "current_stage": 1,
    "status_label": 1,
    "delivery_status": 1,
    "delivery_note_number": 1,
    "delivery_note_created_at": 1,
    "delivered_at": 1,
    "loading_completed_at": 1,
    "qty_delivered": 1,
    "total_quantity": 1,
    "line_items.name": 1,
    "line_items.quantity": 1,
    "shipments": 1,
    "delivery_proofs.uploaded_at": 1,
    "loading_info": 1,
    "packing_info": 1,
    "terkirim_confirmed": 1,
}


def wib_today(now: Optional[datetime] = None) -> date:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(WIB).date()


def wib_day_bounds(day: date) -> Tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=WIB)
    end = start + timedelta(days=1)
    return start, end


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            d = date.fromisoformat(text)
            return datetime(d.year, d.month, d.day, tzinfo=WIB)
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def date_key(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    dt = parse_dt(value)
    if not dt:
        return None
    return dt.astimezone(WIB).date().isoformat()


def iso_in_wib_day(value: Any, day: date) -> bool:
    dt = parse_dt(value)
    if not dt:
        return False
    return dt.astimezone(WIB).date() == day


def stage_label(order: dict) -> str:
    idx = int(order.get("current_stage") or 0)
    if 0 <= idx < len(STAGES):
        return STAGES[idx]
    return order.get("status_label") or "—"


def _item_summary(order: dict) -> str:
    names = []
    for li in (order.get("line_items") or []):
        n = (li.get("name") or "").strip()
        if n:
            names.append(n)
    if not names:
        return "—"
    if len(names) == 1:
        return names[0]
    return f"{names[0]} +{len(names) - 1}"


def _qty_int(v: Any) -> int:
    try:
        if v is None or str(v).strip() == "":
            return 0
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def work_date_keys(order: dict) -> List[str]:
    keys = []
    wd = date_key(order.get("work_date"))
    if wd:
        keys.append(wd)
    for s in (order.get("operator_splits") or []):
        sk = date_key(s.get("work_date"))
        if sk:
            keys.append(sk)
    return keys


def is_work_today(order: dict, today: date) -> bool:
    if order.get("cancelled"):
        return False
    day = today.isoformat()
    return day in work_date_keys(order)


def work_operators_today(order: dict, today: date) -> str:
    day = today.isoformat()
    names = []
    seen = set()
    for s in (order.get("operator_splits") or []):
        if date_key(s.get("work_date")) != day:
            continue
        n = (s.get("operator_name") or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    if names:
        return ", ".join(names)
    for s in (order.get("operator_splits") or []):
        n = (s.get("operator_name") or "").strip()
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    return ", ".join(names) if names else "—"


def work_qty(order: dict, today: date) -> Optional[int]:
    day = today.isoformat()
    splits = [s for s in (order.get("operator_splits") or []) if date_key(s.get("work_date")) == day]
    if splits:
        return sum(_qty_int(s.get("quantity")) for s in splits)
    total = _qty_int(order.get("total_quantity"))
    return total if total else None


def slim_work_row(order: dict, today: date) -> dict:
    return {
        "id": order.get("id"),
        "po_number": order.get("po_number"),
        "customer": order.get("customer") or "",
        "outlet": order.get("outlet") or "",
        "item": _item_summary(order),
        "stage": stage_label(order),
        "operator": work_operators_today(order, today),
        "qty": work_qty(order, today),
        "work_date": today.isoformat(),
    }


def build_work_today(orders: Iterable[dict], today: Optional[date] = None, *, limit: int = PREVIEW_LIMIT) -> dict:
    day = today or wib_today()
    rows = [slim_work_row(o, day) for o in orders if is_work_today(o, day)]
    rows.sort(key=lambda r: (r.get("po_number") or "", r.get("id") or ""))
    total = len(rows)
    return {
        "date": day.isoformat(),
        "timezone": "WIB",
        "total": total,
        "preview": rows[: max(0, int(limit))],
        "has_more": total > int(limit),
        "source": "work_date (order + operator_splits)",
    }


def _shipment_events_today(order: dict, today: date) -> List[dict]:
    events = []
    for sh in shipments_stored(order):
        if iso_in_wib_day(sh.get("created_at"), today):
            events.append({"kind": "surat_jalan", "at": sh.get("created_at"), "shipment": sh})
    if iso_in_wib_day(order.get("delivery_note_created_at"), today) and not events:
        events.append({"kind": "surat_jalan", "at": order.get("delivery_note_created_at"), "shipment": None})
    if iso_in_wib_day(order.get("loading_completed_at"), today):
        events.append({"kind": "loading", "at": order.get("loading_completed_at")})
    for ev in ((order.get("loading_info") or {}).get("events") or []):
        if iso_in_wib_day(ev.get("at") or ev.get("created_at"), today):
            events.append({"kind": "loading", "at": ev.get("at") or ev.get("created_at")})
    for p in (order.get("delivery_proofs") or []):
        if iso_in_wib_day(p.get("uploaded_at"), today):
            events.append({"kind": "bukti", "at": p.get("uploaded_at")})
    if iso_in_wib_day(order.get("delivered_at"), today):
        events.append({"kind": "terkirim", "at": order.get("delivered_at")})
    return events


def is_shipment_today(order: dict, today: date) -> bool:
    if order.get("cancelled"):
        return False
    return bool(_shipment_events_today(order, today))


def delivery_status_label(order: dict) -> str:
    """Never promote a partial shipment to full Terkirim."""
    raw = (order.get("delivery_status") or "").strip()
    shipped = qty_shipped(order)
    delivered = qty_delivered(order)
    required = packing_required_total(order)
    loaded = qty_loaded(order)
    if raw in ("Sebagian Terkirim", "Sebagian"):
        return "Sebagian Terkirim"
    full = (
        raw == "Terkirim"
        and (order.get("current_stage") or 0) >= 14
        and (required <= 0 or delivered >= required or (shipped >= required and delivered >= required))
    )
    if raw == "Terkirim" and not full:
        return "Sebagian Terkirim"
    if delivered > 0 and required > 0 and delivered < required:
        return "Sebagian Terkirim"
    if shipped > 0 and required > 0 and shipped < required and raw != "Terkirim":
        if loaded > 0:
            return "Loading parsial"
        return "SJ parsial"
    if raw == "Terkirim" and full:
        return "Terkirim"
    if loaded > 0 and shipped > 0:
        if required > 0 and loaded >= shipped:
            return "Loading"
        return "Loading parsial"
    if shipped > 0:
        return "Surat Jalan"
    if raw:
        return raw
    return "Pengiriman"


def _today_sj_number(order: dict, today: date) -> Optional[str]:
    for sh in shipments_stored(order):
        if iso_in_wib_day(sh.get("created_at"), today) and sh.get("number"):
            return sh.get("number")
    return order.get("delivery_note_number") or None


def _today_ship_qty(order: dict, today: date) -> Optional[int]:
    total = 0
    matched = False
    for sh in shipments_stored(order):
        if not iso_in_wib_day(sh.get("created_at"), today):
            continue
        matched = True
        for it in (sh.get("lines") or []):
            total += max(0, _qty_int(it.get("qty") if it.get("qty") is not None else it.get("quantity")))
    if matched:
        return total
    if iso_in_wib_day(order.get("delivered_at"), today):
        d = qty_delivered(order)
        return d if d else None
    return None


def slim_ship_row(order: dict, today: date) -> dict:
    lines = order.get("line_items") or []
    line_count = len(lines) if lines else 1
    return {
        "id": order.get("id"),
        "po_number": order.get("po_number"),
        "customer": order.get("customer") or "",
        "outlet": order.get("outlet") or "",
        "surat_jalan": _today_sj_number(order, today),
        "item": _item_summary(order),
        "line_count": line_count,
        "qty": _today_ship_qty(order, today),
        "status": delivery_status_label(order),
        "partial": delivery_status_label(order) != "Terkirim",
    }


def build_shipments_today(orders: Iterable[dict], today: Optional[date] = None, *, limit: int = PREVIEW_LIMIT) -> dict:
    day = today or wib_today()
    rows = [slim_ship_row(o, day) for o in orders if is_shipment_today(o, day)]
    rows.sort(key=lambda r: (r.get("po_number") or "", r.get("id") or ""))
    total = len(rows)
    return {
        "date": day.isoformat(),
        "timezone": "WIB",
        "total": total,
        "preview": rows[: max(0, int(limit))],
        "has_more": total > int(limit),
        "source": "shipments.created_at / delivery_note_created_at / loading / delivery_proofs / delivered_at",
    }


def work_today_mongo_query(today: date) -> dict:
    day = today.isoformat()
    return {
        "cancelled": {"$ne": True},
        "$or": [
            {"work_date": day},
            {"operator_splits.work_date": day},
        ],
    }


def shipments_today_mongo_query(today: date) -> dict:
    """Candidate set = POs already in the shipping pipeline. Exact 'today'
    match is applied in Python using WIB so mixed ISO offsets stay correct.
    Does not scan the full orders collection."""
    return {
        "cancelled": {"$ne": True},
        "$or": [
            {"current_stage": {"$gte": 12}},
            {"shipments.0": {"$exists": True}},
            {"delivered_at": {"$exists": True, "$nin": [None, ""]}},
            {"delivery_note_created_at": {"$exists": True, "$nin": [None, ""]}},
            {"loading_completed_at": {"$exists": True, "$nin": [None, ""]}},
            {"delivery_proofs.0": {"$exists": True}},
        ],
    }
