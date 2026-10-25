"""Authoritative Tanggal Pengiriman resolver — single source of truth.

Precedence (strict):
  1. Manual Tanggal Pengiriman (delivery_deadline_manual and/or scheduled_delivery_date
     from Detail Informasi PO / reschedule) — use the calendar date exactly.
  2. Otherwise automatic: latest completed required approval
     (Approval Design, Approval Payment) + lead_time calendar days (default 3).

Legacy ``order.deadline`` is never used for scheduling decisions. It may remain
on historical documents for backward compatibility only.

Timezone: business calendar dates use WIB (UTC+7), matching dashboard_today.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

WIB = timezone(timedelta(hours=7))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_LEAD_TIME_DAYS = 3
ALLOWED_SLA_DAYS = (1, 3, 5)
STATUS_SCHEDULED = "scheduled"


def parse_calendar_date(raw: Any) -> Optional[str]:
    """Return YYYY-MM-DD or None. Never invents a date."""
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


def _parse_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def wib_calendar_date(raw: Any) -> Optional[str]:
    """Extract WIB calendar date from an ISO timestamp or date string.

    Prevents UTC midnight storage from shifting the displayed business day.
    Date-only strings (YYYY-MM-DD) are returned as-is (already calendar keys).
    """
    key = parse_calendar_date(raw)
    if key and (not isinstance(raw, str) or "T" not in str(raw)):
        # Pure date key or non-string — treat as calendar date (no TZ shift).
        if isinstance(raw, str) and len(str(raw).strip()) == 10:
            return key
    dt = _parse_dt(raw)
    if dt is None:
        return key
    return dt.astimezone(WIB).date().isoformat()


def calendar_date_to_wib_iso(date_key: str) -> str:
    """Persist a date-only picker value as WIB midnight ISO (stable calendar day)."""
    key = parse_calendar_date(date_key)
    if not key:
        raise ValueError("Format tanggal tidak valid")
    y, m, d = map(int, key.split("-"))
    return datetime(y, m, d, 0, 0, 0, tzinfo=WIB).isoformat()


def latest_required_approval_at(order: dict) -> Optional[datetime]:
    """Latest completed required approval among Design and Payment.

    Both must be complete (approved or bypassed). Does not fabricate timestamps.
    Design: design_approved_at or design_bypassed_at
    Payment: payment_approved_at or payment_bypassed_at
    """
    design_at = order.get("design_approved_at") or order.get("design_bypassed_at")
    payment_at = order.get("payment_approved_at") or order.get("payment_bypassed_at")
    if not (design_at and payment_at):
        return None
    dd = _parse_dt(design_at)
    pd_ = _parse_dt(payment_at)
    if dd is None or pd_ is None:
        return None
    return max(dd, pd_)


def automatic_tanggal_pengiriman(
    order: dict,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
) -> Optional[str]:
    """latest(Approval Design, Approval Payment) + lead_time calendar days (WIB).

    Kept for diagnostics and stamped-order ``calculated`` candidates. Active
    Legacy automatic dates use :func:`automatic_working_tanggal_pengiriman`.
    """
    latest = latest_required_approval_at(order)
    if latest is None:
        return None
    base = latest.astimezone(WIB).date()
    days = int(lead_time_days) if lead_time_days is not None else DEFAULT_LEAD_TIME_DAYS
    return (base + timedelta(days=days)).isoformat()


def _normalize_sla_days(lead_time_days: Any) -> int:
    try:
        days = int(lead_time_days) if lead_time_days is not None else DEFAULT_LEAD_TIME_DAYS
    except (TypeError, ValueError):
        days = DEFAULT_LEAD_TIME_DAYS
    if days not in ALLOWED_SLA_DAYS:
        return DEFAULT_LEAD_TIME_DAYS
    return days


def automatic_working_tanggal_pengiriman(
    order: dict,
    working_days: int = DEFAULT_LEAD_TIME_DAYS,
    holidays: Optional[Any] = None,
) -> Optional[str]:
    """latest required approval (WIB date) + N working days.

    Monday–Saturday count. Sunday and ``operational_holidays`` dates are skipped.
    Reuses :func:`services.production_wave2.add_working_days`. Never writes.
    """
    latest = latest_required_approval_at(order)
    if latest is None:
        return None
    from services.production_wave2 import add_working_days

    days = _normalize_sla_days(working_days)
    holiday_list = sorted({
        key for key in (parse_calendar_date(row) for row in (holidays or [])) if key
    })
    return add_working_days(latest.astimezone(WIB).date(), days, holiday_list).isoformat()


def _scheduled_from_reschedule(order: dict) -> Optional[str]:
    scheduled = parse_calendar_date(order.get("scheduled_delivery_date"))
    if scheduled:
        return scheduled
    for rec in reversed(order.get("delivery_reschedules") or []):
        if rec.get("status") == STATUS_SCHEDULED:
            d = parse_calendar_date(rec.get("new_date"))
            if d:
                return d
    return None


def manual_tanggal_pengiriman(order: dict) -> Optional[str]:
    """User-set Tanggal Pengiriman — never falls back to legacy deadline.

    Sources (first match wins for a single calendar date):
      - delivery_deadline_manual (Detail Informasi PO / legacy deadline panel)
      - scheduled_delivery_date / scheduled reschedule history
    """
    manual = wib_calendar_date(order.get("delivery_deadline_manual"))
    if manual:
        return manual
    return _scheduled_from_reschedule(order)


def _today_key(today: Any) -> str:
    if isinstance(today, datetime):
        return today.astimezone(WIB).date().isoformat()
    if isinstance(today, date):
        return today.isoformat()
    key = parse_calendar_date(today)
    if key:
        return key
    return datetime.now(WIB).date().isoformat()


def _public_sla(fact: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": fact.get("date"),
        "source": fact.get("source"),
        "state": fact.get("state"),
        "contract": fact.get("contract"),
        "countable": bool(fact.get("countable")),
    }


def classify_delivery_deadline_setting(doc: Optional[dict]) -> Dict[str, Any]:
    """Split a stored lead-time document from the new working-day SLA.

    A document with no unit, or unit ``calendar_days``, stays the legacy
    calendar formula. It is not treated as 1/3/5 working days. A missing
    document uses the default of 3 working days and has no legacy row.
    """
    if not isinstance(doc, dict):
        return {
            "sla_working_days": DEFAULT_LEAD_TIME_DAYS,
            "sla_unit": "working_days",
            "sla_source": "default",
            "calendar_lead_time_days": DEFAULT_LEAD_TIME_DAYS,
            "legacy_unit": None,
        }
    raw = doc.get("sla_working_days", doc.get("lead_time_days", DEFAULT_LEAD_TIME_DAYS))
    try:
        raw_days = int(raw)
    except (TypeError, ValueError):
        raw_days = DEFAULT_LEAD_TIME_DAYS
    if raw_days not in ALLOWED_SLA_DAYS:
        raw_days = DEFAULT_LEAD_TIME_DAYS
    unit = str(doc.get("unit") or doc.get("sla_unit") or "").strip().lower()
    if unit == "working_days":
        legacy_raw = doc.get("legacy_lead_time_days")
        try:
            legacy_days = int(legacy_raw) if legacy_raw is not None else None
        except (TypeError, ValueError):
            legacy_days = None
        if legacy_days not in ALLOWED_SLA_DAYS:
            legacy_days = None
        return {
            "sla_working_days": raw_days,
            "sla_unit": "working_days",
            "sla_source": "setting",
            "calendar_lead_time_days": legacy_days if legacy_days is not None else DEFAULT_LEAD_TIME_DAYS,
            "legacy_unit": "calendar_days" if legacy_days is not None else None,
        }
    try:
        calendar_days = int(doc.get("lead_time_days", DEFAULT_LEAD_TIME_DAYS))
    except (TypeError, ValueError):
        calendar_days = DEFAULT_LEAD_TIME_DAYS
    if calendar_days not in ALLOWED_SLA_DAYS:
        calendar_days = DEFAULT_LEAD_TIME_DAYS
    return {
        "sla_working_days": DEFAULT_LEAD_TIME_DAYS,
        "sla_unit": "working_days",
        "sla_source": "default",
        "calendar_lead_time_days": calendar_days,
        "legacy_unit": "calendar_days",
    }


def delivery_sla_effective(
    order: dict,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    today: Any = None,
    holidays: Optional[Any] = None,
) -> Dict[str, Any]:
    """One read contract for the customer delivery date.

    Urgent uses only the stored Jadwal Pengiriman date.
    A locked ``delivery_sla_v2.automatic_date`` wins and is not recomputed when
    holidays or settings change.
    Unstamped automatic orders use working days (``legacy_working_days``),
    ``countable=false``. ``lead_time_days`` is the official ``sla_working_days``.
    This function never writes the order.
    """
    from quantity_rules import is_fully_delivered

    days = _normalize_sla_days(lead_time_days)
    working = automatic_working_tanggal_pengiriman(order, days, holidays)
    calendar = automatic_tanggal_pengiriman(order, days)
    manual = manual_tanggal_pengiriman(order)
    urgent = str(order.get("priority") or "").lower() == "urgent"
    stamp = order.get("delivery_sla_v2") if isinstance(order.get("delivery_sla_v2"), dict) else None
    stamp_date = parse_calendar_date((stamp or {}).get("automatic_date"))
    if urgent:
        if manual:
            fact = {"date": manual, "source": "jadwal_pengiriman", "contract": "urgent", "countable": True}
        else:
            fact = {"date": None, "source": "belum_dijadwalkan", "contract": "urgent", "countable": False}
    elif manual:
        fact = {
            "date": manual,
            "source": "manual",
            "contract": "sla_v2" if stamp_date else "manual",
            "countable": True,
        }
    elif stamp_date:
        fact = {"date": stamp_date, "source": "otomatis", "contract": "sla_v2", "countable": True}
    elif working:
        fact = {
            "date": working,
            "source": "legacy",
            "contract": "legacy_working_days",
            "countable": False,
        }
    else:
        fact = {"date": None, "source": "legacy", "contract": "incomplete", "countable": False}
    # Automatic candidate for mirrors: working-day estimate (not calendar days).
    fact["calendar_date"] = working
    fact["legacy_calendar_date"] = calendar
    fact["lead_time_days"] = days
    if order.get("buffer_stock"):
        state = "buffer"
    elif is_fully_delivered(order):
        state = "delivered"
    elif not fact["countable"] or not fact["date"]:
        state = "unscheduled" if fact["source"] == "belum_dijadwalkan" else "legacy"
    elif _today_key(today) > fact["date"]:
        state = "overdue"
    else:
        state = "scheduled"
    fact["state"] = state
    return fact


def resolve_tanggal_pengiriman(
    order: dict,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    holidays: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return authoritative Tanggal Pengiriman snapshot.

    Keys:
      tanggal_pengiriman — YYYY-MM-DD or None
      source — manual | automatic | none (legacy readers and Rencana Kirim)
      calculated — working-day automatic candidate (not used when manual/stamp wins)
      lead_time_days — official sla_working_days used for the automatic candidate
      is_manual — bool
      delivery_sla_effective — shared read contract
    """
    fact = delivery_sla_effective(order, lead_time_days, holidays=holidays)
    if fact["source"] in ("manual", "jadwal_pengiriman"):
        source, is_manual = "manual", True
    elif fact["source"] == "otomatis" or fact["contract"] in ("legacy_calendar", "legacy_working_days"):
        source, is_manual = "automatic", False
    else:
        source, is_manual = "none", False
    date_key = None if source == "none" else fact["date"]
    return {
        "tanggal_pengiriman": date_key,
        "source": source,
        "calculated": fact.get("calendar_date"),
        "lead_time_days": fact["lead_time_days"],
        "is_manual": is_manual,
        "delivery_sla_effective": _public_sla(fact),
    }


def effective_tanggal_pengiriman(
    order: dict,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    holidays: Optional[Any] = None,
) -> Optional[str]:
    return resolve_tanggal_pengiriman(order, lead_time_days, holidays=holidays)["tanggal_pengiriman"]


def apply_manual_tanggal_pengiriman(order: dict, date_key: str, *, user: Optional[dict] = None, reason: str = "") -> str:
    """Set manual Tanggal Pengiriman on the order dict (in-memory).

    Writes delivery_deadline_manual + scheduled_delivery_date so Jadwal Pengiriman
    and Detail Informasi PO stay aligned. Does not touch legacy ``deadline``.
    Returns the normalized YYYY-MM-DD key.
    """
    key = parse_calendar_date(date_key)
    if not key:
        raise ValueError("Tanggal Pengiriman tidak valid")
    iso = calendar_date_to_wib_iso(key)
    before = order.get("delivery_deadline_manual")
    order["delivery_deadline_manual"] = iso
    order["scheduled_delivery_date"] = key
    if user:
        order["delivery_deadline_manual_by"] = user.get("email")
        order["delivery_deadline_manual_by_name"] = user.get("name")
        order["delivery_deadline_manual_at"] = datetime.now(timezone.utc).isoformat()
        if reason:
            order["delivery_deadline_manual_reason"] = reason
        order.setdefault("delivery_deadline_history", []).append({
            "action": "manual.set",
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": datetime.now(timezone.utc).isoformat(),
            "before": before,
            "after": iso,
            "note": reason or "Tanggal Pengiriman (Detail Informasi PO)",
        })
    return key


def clear_manual_tanggal_pengiriman(order: dict, *, user: Optional[dict] = None) -> None:
    """Clear manual override so automatic latest-approval+N applies again."""
    before = order.get("delivery_deadline_manual")
    order["delivery_deadline_manual"] = None
    order["delivery_deadline_manual_by"] = None
    order["delivery_deadline_manual_by_name"] = None
    order["delivery_deadline_manual_at"] = None
    order["delivery_deadline_manual_reason"] = None
    order["scheduled_delivery_date"] = None
    if user:
        order.setdefault("delivery_deadline_history", []).append({
            "action": "manual.reset",
            "by_email": user.get("email"),
            "by_name": user.get("name"),
            "at": datetime.now(timezone.utc).isoformat(),
            "before": before,
            "after": None,
            "note": "Clear Tanggal Pengiriman → otomatis",
        })


def attach_tanggal_pengiriman_snapshot(
    order: dict,
    lead_time_days: int = DEFAULT_LEAD_TIME_DAYS,
    holidays: Optional[Any] = None,
) -> Dict[str, Any]:
    """Attach derived non-persisted fields used by Web/PWA/API responses.

    Also mirrors legacy delivery_deadline_* keys so existing consumers keep
    working without treating legacy ``deadline`` as authoritative.
    """
    snap = resolve_tanggal_pengiriman(order, lead_time_days, holidays=holidays)
    date_key = snap["tanggal_pengiriman"]
    order["delivery_sla_effective"] = snap.get("delivery_sla_effective") or {}
    # Prefer WIB midnight ISO for timestamp-shaped legacy fields when we have a date.
    effective_iso = calendar_date_to_wib_iso(date_key) if date_key else None
    calc_iso = calendar_date_to_wib_iso(snap["calculated"]) if snap.get("calculated") else None

    order["tanggal_pengiriman"] = date_key
    order["tanggal_pengiriman_source"] = snap["source"]
    order["tanggal_pengiriman_calculated"] = snap["calculated"]
    order["tanggal_pengiriman_lead_time_days"] = snap["lead_time_days"]

    # Legacy mirrors (read-only derived) — UI must not label these as Deadline.
    order["delivery_deadline_calculated"] = calc_iso
    order["delivery_deadline_effective"] = effective_iso
    order["delivery_deadline_source"] = (
        "manual" if snap["source"] == "manual" else ("automatic" if snap["source"] == "automatic" else "none")
    )
    order["delivery_deadline_lead_time_days"] = snap["lead_time_days"]
    order["effective_delivery_date"] = date_key
    return snap
