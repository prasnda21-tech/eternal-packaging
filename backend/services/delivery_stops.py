"""Ready-to-ship delivery stops from existing shipment / qty truth.

Unit = delivery stop / shipment stop, not always one PO.
Partial: only remaining eligible packed/SJ qty enters the queue.
One aggregation-style projection; callers must not fetch POs one-by-one.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from quantity_rules import (
    eligible_delivery_qty,
    is_fully_delivered,
    qty_delivered,
    shipments_stored,
)
from services.delivery_schedule import effective_delivery_date, original_deadline

PRIORITY_NORMAL = "normal"
PRIORITY_PRIORITAS = "prioritas"
PRIORITY_MENDESAK = "mendesak"
PRIORITY_VALUES = (PRIORITY_NORMAL, PRIORITY_PRIORITAS, PRIORITY_MENDESAK)
PRIORITY_LABELS = {
    PRIORITY_NORMAL: "Normal",
    PRIORITY_PRIORITAS: "Prioritas",
    PRIORITY_MENDESAK: "Mendesak",
}

LOCATION_OK = "ok"
LOCATION_NEED = "need_location"
LOCATION_LABEL = {
    LOCATION_OK: "Lokasi Siap",
    LOCATION_NEED: "Belum Ada Lokasi",
}
MISSING_LOCATION_REASON = "Lokasi outlet belum diatur"

QUEUE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "outlet": 1,
    "address": 1,
    "recipient": 1,
    "deadline": 1,
    "delivery_deadline_manual": 1,
    "scheduled_delivery_date": 1,
    "design_approved_at": 1,
    "design_bypassed_at": 1,
    "payment_approved_at": 1,
    "payment_bypassed_at": 1,
    "delivery_reschedules": 1,
    "delivery_status": 1,
    "current_stage": 1,
    "qty_delivered": 1,
    "line_items.name": 1,
    "line_items.quantity": 1,
    "shipments.id": 1,
    "shipments.number": 1,
    "shipments.lines": 1,
    "packing_info": 1,
    "loading_info": 1,
    "loading_checklist": 1,
    "delivery_location": 1,
    "delivery_outlet_id": 1,
    "customer_id": 1,
    "urgent": 1,
    "cancelled": 1,
    "total_quantity": 1,
    "quantity": 1,
}

MONGO_QUEUE_FILTER = {
    "cancelled": {"$ne": True},
    "delivery_status": {"$ne": "Terkirim"},
}


def _safe_int(v, default: int = 0) -> int:
    try:
        if v is None or str(v).strip() == "":
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v) -> Optional[float]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _customer(order: dict) -> str:
    return (order.get("customer_name") or order.get("customer") or order.get("outlet") or "").strip()


def _address(order: dict) -> str:
    loc = order.get("delivery_location") or {}
    formatted = (loc.get("formatted_address") or loc.get("address") or "").strip()
    if formatted:
        return formatted
    return (order.get("address") or "").strip()


def parse_location(raw: Any) -> dict:
    loc = raw if isinstance(raw, dict) else {}
    lat = _safe_float(loc.get("lat") if loc.get("lat") is not None else loc.get("latitude"))
    lng = _safe_float(loc.get("lng") if loc.get("lng") is not None else loc.get("longitude"))
    place_id = str(loc.get("place_id") or loc.get("google_place_id") or "").strip() or None
    address = str(loc.get("formatted_address") or loc.get("address") or "").strip() or None
    ok = lat is not None and lng is not None
    return {
        "lat": lat,
        "lng": lng,
        "place_id": place_id,
        "formatted_address": address,
        "location_status": LOCATION_OK if ok else LOCATION_NEED,
    }


def order_location(order: dict, outlets_index: Optional[dict] = None) -> dict:
    if outlets_index is not None:
        from services.delivery_outlets import resolved_location
        return resolved_location(order, outlets_index)
    loc = parse_location(order.get("delivery_location"))
    loc["outlet_id"] = order.get("delivery_outlet_id")
    loc["outlet_name"] = (order.get("outlet") or "").strip() or None
    if not loc.get("formatted_address"):
        loc["formatted_address"] = (order.get("address") or "").strip() or None
    return loc


def normalize_priority(raw: Any) -> str:
    key = str(raw or "").strip().lower()
    if key in PRIORITY_VALUES:
        return key
    if key in ("urgent", "mendesak", "high"):
        return PRIORITY_MENDESAK
    if key in ("prioritas", "priority", "medium"):
        return PRIORITY_PRIORITAS
    return PRIORITY_NORMAL


def normalize_closing_time(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) >= 5 and text[2] == ":":
        hh, mm = text[:2], text[3:5]
        try:
            h, m = int(hh), int(mm)
        except ValueError:
            return None
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    return None


def remaining_eligible_qty(order: dict) -> int:
    """Confirmed Loading (or historical SJ qty) not yet delivered."""
    if is_fully_delivered(order):
        return 0
    eligible = eligible_delivery_qty(order)
    delivered = qty_delivered(order)
    return max(0, eligible - delivered)


def _shipment_qty(sh: dict) -> int:
    total = 0
    for ln in sh.get("lines") or []:
        total += max(0, int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity") or 0) or 0)
    if total <= 0:
        total = max(0, int(sh.get("qty") or sh.get("quantity") or 0) or 0)
    return total


def _stable_stop_id(order_id: str, shipment_id: Optional[str]) -> str:
    sid = (shipment_id or "").strip() or "po"
    return f"ds:{order_id}:{sid}"


def split_remaining_by_shipment(order: dict, remaining: int) -> List[dict]:
    """FIFO remaining eligible qty onto shipment stops. Partial PO → multiple stops."""
    ships = [s for s in shipments_stored(order) if isinstance(s, dict)]
    if not ships:
        return [{
            "shipment_id": None,
            "shipment_number": None,
            "qty": remaining,
        }]
    delivered = qty_delivered(order)
    leftover_delivered = max(0, delivered)
    out = []
    leftover_remaining = remaining
    for sh in ships:
        if leftover_remaining <= 0:
            break
        cap = _shipment_qty(sh)
        if cap <= 0:
            continue
        already = min(cap, leftover_delivered)
        leftover_delivered -= already
        ship_remain = cap - already
        take = min(ship_remain, leftover_remaining)
        if take <= 0:
            continue
        leftover_remaining -= take
        out.append({
            "shipment_id": str(sh.get("id") or sh.get("accurate_shipment_id") or "").strip() or None,
            "shipment_number": str(sh.get("number") or "").strip() or None,
            "qty": take,
        })
    if leftover_remaining > 0 and not out:
        out.append({"shipment_id": None, "shipment_number": None, "qty": leftover_remaining})
    elif leftover_remaining > 0 and out:
        out[-1]["qty"] = int(out[-1]["qty"]) + leftover_remaining
    return out


def build_queue_stops(
    orders: Iterable[dict],
    *,
    occupied_keys: Optional[Set[str]] = None,
    outlets: Optional[Iterable[dict]] = None,
) -> List[dict]:
    """Build SIAP DIKIRIM stops. occupied_keys = already in an active batch."""
    busy = occupied_keys or set()
    outlets_index = None
    if outlets is not None:
        from services.delivery_outlets import index_outlets
        outlets_index = index_outlets(outlets)
    rows = []
    for order in orders:
        if order.get("cancelled"):
            continue
        oid = str(order.get("id") or "").strip()
        if not oid:
            continue
        remaining = remaining_eligible_qty(order)
        if remaining <= 0:
            continue
        loc = order_location(order, outlets_index)
        loc_status = loc["location_status"]
        deadline = None  # Legacy — not authoritative; UI uses effective_delivery_date.
        outlet_name = loc.get("outlet_name") or (order.get("outlet") or "").strip() or None
        for part in split_remaining_by_shipment(order, remaining):
            stop_id = _stable_stop_id(oid, part.get("shipment_id"))
            key = stop_id
            occupied = key in busy
            rows.append({
                "stop_id": stop_id,
                "shipment_id": part.get("shipment_id"),
                "shipment_number": part.get("shipment_number"),
                "order_id": oid,
                "order_number": order.get("po_number") or "",
                "customer": _customer(order),
                "customer_id": order.get("customer_id"),
                "outlet_id": loc.get("outlet_id") or order.get("delivery_outlet_id"),
                "outlet_name": outlet_name,
                "address": loc.get("formatted_address") or _address(order),
                "lat": loc.get("lat"),
                "lng": loc.get("lng"),
                "place_id": loc.get("place_id"),
                "google_place_id": loc.get("place_id"),
                "qty": part.get("qty") or 0,
                "package_count": None,
                "deadline": deadline,
                "effective_delivery_date": effective_delivery_date(order),
                "tanggal_pengiriman": effective_delivery_date(order),
                "priority": PRIORITY_MENDESAK if order.get("urgent") else PRIORITY_NORMAL,
                "priority_label": PRIORITY_LABELS[PRIORITY_MENDESAK if order.get("urgent") else PRIORITY_NORMAL],
                "outlet_closing_time": None,
                "status": LOCATION_LABEL[loc_status] if loc_status == LOCATION_NEED else "SIAP DIKIRIM",
                "location_status": loc_status,
                "location_label": LOCATION_LABEL[loc_status],
                "selectable": (not occupied) and loc_status == LOCATION_OK,
                "occupied": occupied,
                "exclude_reason": (
                    "Sudah masuk batch aktif" if occupied
                    else (MISSING_LOCATION_REASON if loc_status == LOCATION_NEED else None)
                ),
            })
    rows.sort(key=lambda r: (r.get("effective_delivery_date") or "9999", r.get("order_number") or "", r.get("stop_id") or ""))
    return rows


def snapshot_stop(queue_row: dict, *, priority=None, outlet_closing_time=None, location=None) -> dict:
    src_loc = location if isinstance(location, dict) else {}
    loc = parse_location(location) if location is not None else parse_location({
        "lat": queue_row.get("lat"),
        "lng": queue_row.get("lng"),
        "place_id": queue_row.get("place_id") or queue_row.get("google_place_id"),
        "formatted_address": queue_row.get("address"),
    })
    pr = normalize_priority(priority if priority is not None else queue_row.get("priority"))
    closing = normalize_closing_time(
        outlet_closing_time if outlet_closing_time is not None else queue_row.get("outlet_closing_time")
    )
    loc_status = loc["location_status"]
    outlet_id = src_loc.get("outlet_id") or queue_row.get("outlet_id")
    outlet_name = src_loc.get("outlet_name") or queue_row.get("outlet_name")
    return {
        "stop_id": queue_row["stop_id"],
        "shipment_id": queue_row.get("shipment_id"),
        "shipment_number": queue_row.get("shipment_number"),
        "order_id": queue_row.get("order_id"),
        "order_number": queue_row.get("order_number"),
        "customer": queue_row.get("customer"),
        "outlet_id": outlet_id,
        "outlet_name": outlet_name,
        "address": loc.get("formatted_address") or queue_row.get("address"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "place_id": loc.get("place_id"),
        "google_place_id": loc.get("place_id"),
        "qty": int(queue_row.get("qty") or 0),
        "package_count": queue_row.get("package_count"),
        "deadline": queue_row.get("deadline"),
        "priority": pr,
        "priority_label": PRIORITY_LABELS[pr],
        "outlet_closing_time": closing,
        "status": "queued",
        "location_status": loc_status,
        "location_label": LOCATION_LABEL[loc_status],
        "unassigned_reason": None,
    }


def occupied_stop_keys(batches: Iterable[dict]) -> Set[str]:
    keys: Set[str] = set()
    active = {"draft", "published", "terjadwal", "berjalan"}
    for b in batches or []:
        if (b.get("status") or "") not in active and (b.get("route_status") or "") not in active:
            continue
        for st in b.get("stops") or []:
            sid = str(st.get("stop_id") or "").strip()
            if sid:
                keys.add(sid)
    return keys
