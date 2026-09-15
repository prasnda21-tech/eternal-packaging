"""Delivery schedule + reschedule — append-only history.

effective_delivery_date / Tanggal Pengiriman (authoritative):
  1. Manual Tanggal Pengiriman (delivery_deadline_manual / scheduled_delivery_date)
  2. Else latest(Approval Design, Approval Payment) + lead_time calendar days

Legacy order.deadline is preserved on the document for backward compatibility
but never drives scheduling. See services.tanggal_pengiriman.

Never mutates packing, loading, proofs, or delivery_status.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from services.dashboard_today import wib_today
from services.shipment_status import shipment_status_row
from services.workflow_stage_status import role_status_for_schedule_row
from services.tanggal_pengiriman import (
    DEFAULT_LEAD_TIME_DAYS,
    effective_tanggal_pengiriman,
    manual_tanggal_pengiriman,
    parse_calendar_date,
)
from quantity_rules import qty_packed

STATUS_PENDING = "pending"
STATUS_SCHEDULED = "scheduled"

DISPLAY_BELUM = "Belum Dijadwalkan"
DISPLAY_TERJADWAL = "Terjadwal"
DISPLAY_HARI_INI = "Hari Ini"
DISPLAY_TERLAMBAT = "Terlambat"
DISPLAY_TERKIRIM = "Terkirim"

SCHEDULE_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
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
    "delivered_at": 1,
    "qty_delivered": 1,
    "total_quantity": 1,
    "line_items.name": 1,
    "line_items.quantity": 1,
    "line_items.production_process": 1,
    "line_items.id": 1,
    "line_items.line_id": 1,
    "line_items.accurate_item_id": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.line_idx": 1,
    "operator_splits.quantity": 1,
    "shipments.id": 1,
    "shipments.number": 1,
    "shipments.sj_number": 1,
    "shipments.sales_delivery_id": 1,
    "shipments.shipment_index": 1,
    "shipments.delivery_date": 1,
    "shipments.created_at": 1,
    "shipments.lines": 1,
    "shipments.delivery_location": 1,
    "shipments.delivered_at": 1,
    "shipments.delivery_status": 1,
    "shipments.qty_delivered": 1,
    "loading_checklist.driver_name": 1,
    "loading_checklist.driver_user_id": 1,
    "loading_checklist.items_loaded": 1,
    "loading_checklist.seal_ok": 1,
    "loading_confirmations": 1,
    "loading_completed": 1,
    "loading_info.lines": 1,
    "loading_info.events": 1,
    "delivery_outlet_id": 1,
    "delivery_location": 1,
    "address": 1,
    "packing_info": 1,
    "helper_prep": 1,
    "screener_info": 1,
    "sample_approved": 1,
    "sample_files.id": 1,
    "work_date": 1,
    "design_status": 1,
    "design_bypassed": 1,
    "payment_status": 1,
    "payment_bypassed": 1,
    "design_files.id": 1,
    "payment_files.id": 1,
    "surat_jalan_created": 1,
    "surat_jalan_files.id": 1,
    "surat_jalan_files.shipment_id": 1,
    "surat_jalan_files.number": 1,
    "surat_jalan_files.sj_number": 1,
    "surat_jalan_files.name": 1,
    "surat_jalan_files.uploaded_at": 1,
    "surat_jalan_files.is_deleted": 1,
    "surat_jalan_files.has_file": 1,
    "delivery_confirmations": 1,
    "terkirim_confirmed": 1,
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
    return parse_calendar_date(raw)


def today_key(now: Optional[datetime] = None) -> str:
    """Business date for Jadwal Pengiriman — WIB, same as dashboard_today.wib_today."""
    return wib_today(now).isoformat()


def original_deadline(order: dict) -> Optional[str]:
    """Legacy deadline field (audit only — not authoritative for scheduling)."""
    return parse_date_key(order.get("deadline"))


def manual_schedule_date(order: dict) -> Optional[str]:
    """Manual Tanggal Pengiriman only (Detail Informasi PO / reschedule)."""
    return manual_tanggal_pengiriman(order)


def effective_delivery_date(
    order: dict,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
) -> Optional[str]:
    """Authoritative Tanggal Pengiriman — never falls back to legacy deadline."""
    return effective_tanggal_pengiriman(order, lead_time_days)


def is_delivered(order: dict) -> bool:
    if order.get("delivery_status") == "Terkirim":
        return True
    try:
        stage = int(order.get("current_stage") or 0)
    except (TypeError, ValueError):
        stage = 0
    return stage >= 14 and str(order.get("delivery_status") or "") == "Terkirim"


def schedule_status_code(
    order: dict,
    today: Optional[str] = None,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    schedule_date: Optional[str] = None,
) -> str:
    if is_delivered(order):
        return "terkirim"
    eff = parse_date_key(schedule_date) or effective_delivery_date(order, lead_time_days)
    if not eff:
        return "belum_dijadwalkan"
    day = today or today_key()
    if eff == day:
        return "hari_ini"
    if eff < day:
        return "terlambat"
    # Any future authoritative Tanggal Pengiriman (manual or automatic) is scheduled.
    return "terjadwal"


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


def _driver_user_id(order: dict) -> Optional[str]:
    lc = order.get("loading_checklist") or {}
    uid = str(lc.get("driver_user_id") or order.get("driver_user_id") or "").strip()
    return uid or None


def _optional_int(value: Any) -> Any:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _shipment_sj_number(sh: Optional[dict]) -> Optional[str]:
    if not isinstance(sh, dict):
        return None
    num = str(sh.get("number") or sh.get("sj_number") or "").strip()
    return num or None


def _shipment_sj_id(sh: Optional[dict]) -> Optional[str]:
    """Canonical Surat Jalan id: shipment.id || sales_delivery_id."""
    if not isinstance(sh, dict):
        return None
    sid = str(sh.get("id") or sh.get("sales_delivery_id") or "").strip()
    return sid or None


def shipment_delivery_date(sh: Optional[dict]) -> Optional[str]:
    """Persisted Pengiriman delivery_date on the shipment — not invented from SJ suffix."""
    if not isinstance(sh, dict):
        return None
    return parse_date_key(sh.get("delivery_date"))


def dated_schedule_shipments(order: dict) -> List[dict]:
    raw = order.get("shipments")
    if not isinstance(raw, list):
        return []
    out = []
    for sh in raw:
        if isinstance(sh, dict) and shipment_delivery_date(sh):
            out.append(sh)
    return out


def _shipment_index(sh: dict) -> int:
    try:
        return int(sh.get("shipment_index"))
    except (TypeError, ValueError):
        return 0


def sj_item_lines_for_shipment(sh: Optional[dict]) -> List[dict]:
    """Exact lines of ONE shipment. Never PO line_items / item_label / other SJ batches."""
    out: List[dict] = []
    if not isinstance(sh, dict):
        return out
    shipment_id = str(sh.get("id") or sh.get("sales_delivery_id") or "").strip() or None
    sj_number = _shipment_sj_number(sh)
    lines = sh.get("lines")
    if not isinstance(lines, list):
        return out
    for ln in lines:
        if not isinstance(ln, dict):
            continue
        line_id = ln.get("line_id")
        line_id_out = str(line_id).strip() if line_id is not None and str(line_id).strip() else None
        qty_raw = ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")
        name = ln.get("name")
        out.append({
            "shipment_id": shipment_id,
            "sj_number": sj_number,
            "line_id": line_id_out,
            "line_idx": _optional_int(ln.get("line_idx")),
            "name": str(name) if name is not None else "",
            "qty": _optional_int(qty_raw),
        })
    return out


def sj_item_lines(order: dict, shipment: Optional[dict] = None) -> List[dict]:
    """Jadwal Pengiriman Item Line source: the matched shipment only.

    Does not default to active_delivery_batch() (Pengiriman current-batch helper).
    """
    if isinstance(shipment, dict):
        return sj_item_lines_for_shipment(shipment)
    return []


def public_delivery_location(raw: Any) -> dict:
    """Valid nested delivery_location, or empty lat/lng.

    Validity is is_valid_coordinate: finite pair, in range, not (0, 0).
    Incomplete / one-sided / out-of-range pairs are not valid.
    """
    from services.delivery_stops import parse_location
    from services.independent_route.geo import is_valid_coordinate
    loc = parse_location(raw)
    if is_valid_coordinate(loc.get("lat"), loc.get("lng")):
        return {
            "lat": float(loc["lat"]),
            "lng": float(loc["lng"]),
            "place_id": loc.get("place_id"),
            "formatted_address": loc.get("formatted_address"),
        }
    return {
        "lat": None,
        "lng": None,
        "place_id": loc.get("place_id"),
        "formatted_address": loc.get("formatted_address"),
    }


def persistable_delivery_location(raw: Any) -> Optional[dict]:
    """Normalized SJ override for storage. Invalid/empty → None (do not persist)."""
    if not isinstance(raw, dict) or not raw:
        return None
    loc = public_delivery_location(raw)
    if loc.get("lat") is None or loc.get("lng") is None:
        return None
    out = {"lat": loc["lat"], "lng": loc["lng"]}
    if loc.get("place_id"):
        out["place_id"] = loc["place_id"]
        out["google_place_id"] = loc["place_id"]
    if loc.get("formatted_address"):
        out["formatted_address"] = loc["formatted_address"]
    return out


def effective_delivery_location(
    shipment: Optional[dict] = None,
    order: Optional[dict] = None,
) -> dict:
    """SJ override if valid, else PO location if valid, else empty."""
    sj_raw = shipment.get("delivery_location") if isinstance(shipment, dict) else None
    sj = public_delivery_location(sj_raw)
    if sj.get("lat") is not None:
        return sj
    po_raw = order.get("delivery_location") if isinstance(order, dict) else None
    return public_delivery_location(po_raw)


def apply_effective_location_to_atom(atom: dict, shipment_override: Any) -> dict:
    """If the SJ unit has a valid override, replace atom coords. Else keep PO coords."""
    sj = public_delivery_location(shipment_override)
    if sj.get("lat") is None:
        return atom
    atom["lat"] = sj["lat"]
    atom["lng"] = sj["lng"]
    snap = dict(atom.get("planning_snapshot") or {})
    snap["lat"] = sj["lat"]
    snap["lng"] = sj["lng"]
    if sj.get("formatted_address"):
        snap["address"] = sj["formatted_address"]
        atom["address"] = sj["formatted_address"]
    atom["planning_snapshot"] = snap
    return atom


def schedule_row(
    order: dict,
    today: Optional[str] = None,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    shipment: Optional[dict] = None,
) -> dict:
    sh = shipment if isinstance(shipment, dict) else None
    batch_date = shipment_delivery_date(sh)
    tanggal = batch_date or effective_delivery_date(order, lead_time_days)
    code = schedule_status_code(order, today, lead_time_days, schedule_date=tanggal)
    # Same canonical Terkirim / Dapat Dikirim as Status Pengiriman (no new formula).
    ship = shipment_status_row(order)
    loc = effective_delivery_location(sh, order)
    matched_sj = _shipment_sj_number(sh)
    shipment_id = _shipment_sj_id(sh)
    return {
        "id": order.get("id"),
        "order_number": order.get("po_number") or "",
        "customer_name": order.get("customer_name") or order.get("customer") or "",
        "customer_id": order.get("customer_id"),
        "item_label": _item_label(order),
        # Authoritative scheduling date only — Deadline / Jadwal Manual columns removed.
        "tanggal_pengiriman": tanggal,
        "effective_delivery_date": tanggal,
        # Legacy keys kept null/omitted from UI; deadline retained for audit consumers only.
        "deadline": None,
        "jadwal_manual": None,
        "status": code,
        "status_label": schedule_status_label(code),
        "driver_name": _driver_name(order) or None,
        "driver_user_id": _driver_user_id(order),
        "delivery_outlet_id": order.get("delivery_outlet_id"),
        "delivery_status": order.get("delivery_status") or "",
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "qty_total": ship.get("qty_total"),
        "qty_delivered": ship.get("qty_delivered"),
        "qty_packed": qty_packed(order),
        "qty_printed_operator": ship.get("qty_printed_operator"),
        "print_qty_total": ship.get("print_qty_total"),
        "percent_delivered": ship.get("percent_delivered"),
        "percent_delivered_display": ship.get("percent_delivered_display"),
        "percent_ready_to_ship": ship.get("percent_ready_to_ship"),
        "percent_ready_to_ship_display": ship.get("percent_ready_to_ship_display"),
        "shipment_id": shipment_id,
        "shipment_index": _shipment_index(sh) if sh else None,
        "sj_numbers": [matched_sj] if matched_sj else [],
        "sj_label": matched_sj or "",
        "latest_sj_number": matched_sj,
        "sj_item_lines": sj_item_lines(order, sh),
        "urgent": str(order.get("priority") or "").lower() == "urgent",
        "role_status": role_status_for_schedule_row(order, sh),
    }


def schedule_rows(
    order: dict,
    today: Optional[str] = None,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
) -> List[dict]:
    """One Jadwal Pengiriman row per dated canonical Surat Jalan.

    Eligible shipment: delivery_date AND canonical SJ id AND canonical SJ number.
    Same-date shipments stay separate; shipment_index is the existing sequence
    tie-break (Pengiriman Kirim #), never SJ-suffix parsing.
    No valid canonical SJ → no rows (never a PO-level fallback).
    """
    dated = [
        sh for sh in dated_schedule_shipments(order)
        if _shipment_sj_id(sh) and _shipment_sj_number(sh)
    ]
    if not dated:
        return []
    dated = sorted(
        dated,
        key=lambda sh: (shipment_delivery_date(sh) or "", _shipment_index(sh), str(sh.get("created_at") or "")),
    )
    return [
        schedule_row(order, today=today, lead_time_days=lead_time_days, shipment=sh)
        for sh in dated
    ]


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


def attach_schedule_snapshot(
    order: dict,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
) -> None:
    from services.tanggal_pengiriman import attach_tanggal_pengiriman_snapshot

    attach_tanggal_pengiriman_snapshot(order, lead_time_days)
    order["delivery_schedule_status"] = schedule_status_code(order, lead_time_days=lead_time_days)
