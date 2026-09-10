"""Delivery schedule + reschedule — append-only history, original deadline preserved.

effective_delivery_date:
  confirmed scheduled_delivery_date / latest scheduled reschedule
  else delivery_deadline_manual (existing manual)
  else original order.deadline

Never mutates order.deadline, packing, loading, proofs, or delivery_status.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.dashboard_today import wib_today
from services.shipment_status import shipment_status_row
from quantity_rules import qty_packed

STATUS_PENDING = "pending"
STATUS_SCHEDULED = "scheduled"

DISPLAY_BELUM = "Belum Dijadwalkan"
DISPLAY_TERJADWAL = "Terjadwal"
DISPLAY_HARI_INI = "Hari Ini"
DISPLAY_TERLAMBAT = "Terlambat"
DISPLAY_TERKIRIM = "Terkirim"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEDULE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "deadline": 1,
    "delivery_deadline_manual": 1,
    "scheduled_delivery_date": 1,
    "delivery_reschedules": 1,
    "delivery_status": 1,
    "current_stage": 1,
    "delivered_at": 1,
    "qty_delivered": 1,
    "total_quantity": 1,
    "line_items.name": 1,
    "line_items.quantity": 1,
    "line_items.production_process": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.line_idx": 1,
    "shipments.id": 1,
    "shipments.number": 1,
    "shipments.lines": 1,
    "loading_checklist.driver_name": 1,
    "packing_info": 1,
    "priority": 1,
    "cancelled": 1,
}

RESCHEDULE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "deadline": 1,
    "delivery_deadline_manual": 1,
    "scheduled_delivery_date": 1,
    "delivery_reschedules": 1,
    "delivery_status": 1,
    "shipments.id": 1,
    "shipments.number": 1,
    "cancelled": 1,
}


class RescheduleError(ValueError):
    pass


def parse_date_key(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    key = text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else text
    if not DATE_RE.match(key):
        return None
    try:
        datetime.strptime(key, "%Y-%m-%d")
    except ValueError:
        return None
    return key


def today_key(now: Optional[datetime] = None) -> str:
    """Business date for Jadwal / Pengiriman Hari Ini — WIB, same as dashboard_today.wib_today."""
    return wib_today(now).isoformat()


def original_deadline(order: dict) -> Optional[str]:
    return parse_date_key(order.get("deadline"))


def manual_schedule_date(order: dict) -> Optional[str]:
    scheduled = parse_date_key(order.get("scheduled_delivery_date"))
    if scheduled:
        return scheduled
    for rec in reversed(order.get("delivery_reschedules") or []):
        if rec.get("status") == STATUS_SCHEDULED:
            d = parse_date_key(rec.get("new_date"))
            if d:
                return d
    return parse_date_key(order.get("delivery_deadline_manual"))


def effective_delivery_date(order: dict) -> Optional[str]:
    manual = manual_schedule_date(order)
    if manual:
        return manual
    return original_deadline(order)


def is_delivered(order: dict) -> bool:
    if order.get("delivery_status") == "Terkirim":
        return True
    try:
        stage = int(order.get("current_stage") or 0)
    except (TypeError, ValueError):
        stage = 0
    return stage >= 14 and str(order.get("delivery_status") or "") == "Terkirim"


def schedule_status_code(order: dict, today: Optional[str] = None) -> str:
    if is_delivered(order):
        return "terkirim"
    eff = effective_delivery_date(order)
    if not eff:
        return "belum_dijadwalkan"
    day = today or today_key()
    if eff == day:
        return "hari_ini"
    if eff < day:
        return "terlambat"
    if manual_schedule_date(order):
        return "terjadwal"
    return "belum_dijadwalkan"


def schedule_status_label(code: str) -> str:
    return {
        "belum_dijadwalkan": DISPLAY_BELUM,
        "terjadwal": DISPLAY_TERJADWAL,
        "hari_ini": DISPLAY_HARI_INI,
        "terlambat": DISPLAY_TERLAMBAT,
        "terkirim": DISPLAY_TERKIRIM,
    }.get(code or "", DISPLAY_BELUM)


def _item_label(order: dict) -> str:
    names = []
    for it in order.get("line_items") or []:
        n = (it or {}).get("name")
        if n:
            names.append(str(n))
    if not names:
        ships = order.get("shipments") or []
        for sh in ships:
            num = (sh or {}).get("number")
            if num:
                names.append(str(num))
    if len(names) == 1:
        return names[0]
    if names:
        return f"{len(names)} item"
    return ""


def _driver_name(order: dict) -> str:
    lc = order.get("loading_checklist") or {}
    return (lc.get("driver_name") or "").strip()


def schedule_row(order: dict, today: Optional[str] = None) -> dict:
    code = schedule_status_code(order, today)
    # Same canonical Terkirim / Dapat Dikirim as Status Pengiriman (no new formula).
    ship = shipment_status_row(order)
    sj_numbers = []
    for sh in order.get("shipments") or []:
        num = str(sh.get("number") or sh.get("sj_number") or "").strip()
        if num and num not in sj_numbers:
            sj_numbers.append(num)
    return {
        "id": order.get("id"),
        "order_number": order.get("po_number") or "",
        "customer_name": order.get("customer_name") or order.get("customer") or "",
        "item_label": _item_label(order),
        "deadline": original_deadline(order),
        "jadwal_manual": manual_schedule_date(order),
        "effective_delivery_date": effective_delivery_date(order),
        "status": code,
        "status_label": schedule_status_label(code),
        "driver_name": _driver_name(order) or None,
        "delivery_status": order.get("delivery_status") or "",
        "qty_total": ship.get("qty_total"),
        "qty_delivered": ship.get("qty_delivered"),
        "qty_packed": qty_packed(order),
        "qty_printed_operator": ship.get("qty_printed_operator"),
        "print_qty_total": ship.get("print_qty_total"),
        "percent_delivered": ship.get("percent_delivered"),
        "percent_delivered_display": ship.get("percent_delivered_display"),
        "percent_ready_to_ship": ship.get("percent_ready_to_ship"),
        "percent_ready_to_ship_display": ship.get("percent_ready_to_ship_display"),
        "sj_numbers": sj_numbers,
        "sj_label": ", ".join(sj_numbers) if sj_numbers else "",
        "urgent": str(order.get("priority") or "").lower() == "urgent",
    }


def matches_schedule_filters(row: dict, filters: Optional[dict]) -> bool:
    f = filters or {}
    po = (f.get("po") or "").strip().lower()
    if po and po not in str(row.get("order_number") or "").lower():
        return False
    cust = (f.get("customer") or "").strip().lower()
    if cust and cust not in str(row.get("customer_name") or "").lower():
        return False
    status = (f.get("status") or "").strip().lower()
    if status and status not in ("", "semua") and row.get("status") != status:
        return False
    date_from = parse_date_key(f.get("date_from"))
    date_to = parse_date_key(f.get("date_to"))
    eff = parse_date_key(row.get("effective_delivery_date"))
    if date_from and (not eff or eff < date_from):
        return False
    if date_to and (not eff or eff > date_to):
        return False
    driver = (f.get("driver") or "").strip().lower()
    if driver and driver not in str(row.get("driver_name") or "").lower():
        return False
    return True


def find_reschedule_by_action_id(order: dict, action_id: Optional[str]) -> Optional[dict]:
    aid = (action_id or "").strip()
    if not aid:
        return None
    for rec in order.get("delivery_reschedules") or []:
        if str(rec.get("action_id") or "") == aid:
            return rec
    return None


def find_reschedule(order: dict, reschedule_id: str) -> Optional[dict]:
    rid = (reschedule_id or "").strip()
    if not rid:
        return None
    for rec in order.get("delivery_reschedules") or []:
        if str(rec.get("reschedule_id") or "") == rid:
            return rec
    return None


def _actor_fields(user: dict, prefix: str) -> dict:
    uid = user.get("id") or user.get("user_id") or user.get("email")
    name = user.get("name") or user.get("email") or ""
    return {
        f"{prefix}_id": uid,
        f"{prefix}_name": name,
    }


def apply_reschedule(
    order: dict,
    *,
    reason: str,
    new_date: Optional[str],
    can_set_date: bool,
    user: dict,
    action_id: Optional[str] = None,
    shipment_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[dict, bool]:
    """Append one reschedule record. Returns (record, mutated).

    If can_set_date is False, new_date is ignored and status is pending.
    Does not mutate deadline, proofs, packing, loading, or delivery_status.
    """
    existing = find_reschedule_by_action_id(order, action_id)
    if existing:
        return existing, False
    trimmed = (reason or "").strip()
    if not trimmed:
        raise RescheduleError("Alasan penjadwalan ulang wajib diisi.")
    date_key = parse_date_key(new_date)
    if can_set_date:
        if not date_key:
            raise RescheduleError("Tanggal pengiriman baru wajib diisi.")
        status = STATUS_SCHEDULED
    else:
        date_key = None
        status = STATUS_PENDING

    previous = effective_delivery_date(order)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    rec = {
        "reschedule_id": str(uuid.uuid4()),
        "order_id": order.get("id"),
        "shipment_id": (shipment_id or "").strip() or None,
        "previous_date": previous,
        "new_date": date_key,
        "reason": trimmed,
        "status": status,
        "created_at": ts,
        "action_id": (action_id or "").strip() or None,
        "original_deadline": original_deadline(order),
        **_actor_fields(user, "requested_by"),
    }
    if status == STATUS_SCHEDULED:
        rec.update(_actor_fields(user, "scheduled_by"))
    else:
        rec["scheduled_by_id"] = None
        rec["scheduled_by_name"] = None

    history = list(order.get("delivery_reschedules") or [])
    history.append(rec)
    order["delivery_reschedules"] = history
    if status == STATUS_SCHEDULED and date_key:
        order["scheduled_delivery_date"] = date_key
    return rec, True


def apply_set_date(
    order: dict,
    reschedule_id: str,
    *,
    new_date: str,
    user: dict,
    reason: Optional[str] = None,
    action_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[dict, bool]:
    """Append a scheduled confirmation for a pending request. Append-only."""
    existing = find_reschedule_by_action_id(order, action_id)
    if existing:
        return existing, False
    pending = find_reschedule(order, reschedule_id)
    if not pending:
        raise RescheduleError("Permintaan penjadwalan ulang tidak ditemukan.")
    date_key = parse_date_key(new_date)
    if not date_key:
        raise RescheduleError("Tanggal pengiriman baru wajib diisi.")
    trimmed = (reason or pending.get("reason") or "").strip()
    if not trimmed:
        raise RescheduleError("Alasan penjadwalan ulang wajib diisi.")
    previous = effective_delivery_date(order)
    ts = (now or datetime.now(timezone.utc)).isoformat()
    rec = {
        "reschedule_id": str(uuid.uuid4()),
        "order_id": order.get("id"),
        "shipment_id": pending.get("shipment_id"),
        "previous_date": previous,
        "new_date": date_key,
        "reason": trimmed,
        "status": STATUS_SCHEDULED,
        "created_at": ts,
        "action_id": (action_id or "").strip() or None,
        "original_deadline": original_deadline(order),
        "fulfills_reschedule_id": pending.get("reschedule_id"),
        "requested_by_id": pending.get("requested_by_id"),
        "requested_by_name": pending.get("requested_by_name"),
        **_actor_fields(user, "scheduled_by"),
    }
    history = list(order.get("delivery_reschedules") or [])
    history.append(rec)
    order["delivery_reschedules"] = history
    order["scheduled_delivery_date"] = date_key
    return rec, True


def active_reschedule(order: dict) -> Optional[dict]:
    hist = list(order.get("delivery_reschedules") or [])
    if not hist:
        return None
    fulfilled = {
        str(r.get("fulfills_reschedule_id") or "")
        for r in hist
        if r.get("fulfills_reschedule_id")
    }
    for rec in reversed(hist):
        rid = str(rec.get("reschedule_id") or "")
        if rec.get("status") == STATUS_PENDING and rid in fulfilled:
            continue
        return rec
    return hist[-1]


def reschedule_rows(order: dict) -> List[dict]:
    rows = []
    for rec in order.get("delivery_reschedules") or []:
        rows.append({
            "reschedule_id": rec.get("reschedule_id"),
            "order_id": order.get("id"),
            "order_number": order.get("po_number") or "",
            "customer_name": order.get("customer_name") or order.get("customer") or "",
            "shipment_id": rec.get("shipment_id"),
            "previous_date": rec.get("previous_date"),
            "new_date": rec.get("new_date"),
            "reason": rec.get("reason") or "",
            "status": rec.get("status"),
            "requested_by_id": rec.get("requested_by_id"),
            "requested_by_name": rec.get("requested_by_name") or "",
            "scheduled_by_id": rec.get("scheduled_by_id"),
            "scheduled_by_name": rec.get("scheduled_by_name") or "",
            "created_at": rec.get("created_at"),
            "original_deadline": rec.get("original_deadline") or original_deadline(order),
            "effective_delivery_date": effective_delivery_date(order),
            "fulfills_reschedule_id": rec.get("fulfills_reschedule_id"),
        })
    return rows


def public_reschedule_snippet(order: dict) -> Optional[Dict[str, str]]:
    """Customer-safe: date only, no reason/actors."""
    if is_delivered(order):
        return None
    date_key = parse_date_key(order.get("scheduled_delivery_date"))
    if not date_key:
        for rec in reversed(order.get("delivery_reschedules") or []):
            if rec.get("status") == STATUS_SCHEDULED:
                date_key = parse_date_key(rec.get("new_date"))
                if date_key:
                    break
    if not date_key:
        return None
    return {"scheduled_date": date_key}


def attach_schedule_snapshot(order: dict) -> None:
    order["effective_delivery_date"] = effective_delivery_date(order)
    order["delivery_schedule_status"] = schedule_status_code(order)
