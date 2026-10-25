"""Wave 2 schedule targets, delivery SLA stamp, and read-only dashboard facts.

Schedule events are appended on the order inside the existing quantity lock.
A collection mirror is written after that lock succeeds. GET does not stamp
dates, holidays, or targets. original_quantity is never treated as Target Awal.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

from quantity_rules import is_fully_delivered, is_print_line, print_completed_for_line, qty_packed
from services.production_analytics import _packing_done, _print_done, _snapshot_pcs, parse_confirmed_at
from services.tanggal_pengiriman import delivery_sla_effective, latest_required_approval_at, parse_calendar_date
from workflow_gates import group2_complete

WIB = ZoneInfo("Asia/Jakarta")
logger = logging.getLogger("eternal.production_wave2")
EVENTS_COLLECTION = "production_schedule_events"
HOLIDAY_COLLECTION = "operational_holidays"
HOLIDAY_HISTORY = "operational_holiday_events"
FINISH_SPLIT = "__finish_target__"
WORKING_DAY_RULE = "working_days_3"
NORMAL_WORKING_DAYS = 3
ALLOWED_WORKING_DAYS = (1, 3, 5)


class ScheduleError(Exception):
    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def _now(value: Optional[str] = None) -> str:
    if value:
        return value
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number


def _text(value: Any) -> str:
    return str(value or "").strip()


def _events(order: dict) -> List[dict]:
    rows = order.get("production_schedule_events")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _schedule(order: dict) -> dict:
    raw = order.get("production_schedule")
    return raw if isinstance(raw, dict) else {}


def schedule_finalized(order: dict) -> bool:
    return _schedule(order).get("state") == "finalized"


def _line(order: dict, line_idx: Any) -> Optional[dict]:
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        return None
    rows = order.get("line_items") or []
    if idx < 0 or idx >= len(rows) or not isinstance(rows[idx], dict):
        return None
    return rows[idx]


def _line_id(order: dict, line_idx: Any) -> str:
    row = _line(order, line_idx)
    return _text((row or {}).get("line_id") or (row or {}).get("id"))


def _unit(order: dict, line_idx: Any) -> str:
    row = _line(order, line_idx)
    return _text((row or {}).get("unit"))


def _actor(user: Optional[dict]) -> dict:
    user = user or {}
    return {
        "actor_id": _text(user.get("id") or user.get("user_id")),
        "actor_email": _text(user.get("email")),
        "actor_name": _text(user.get("name")),
    }


def _split_view(split: Optional[dict]) -> Optional[dict]:
    if not isinstance(split, dict):
        return None
    return {
        "split_id": _text(split.get("id")),
        "line_idx": split.get("line_idx"),
        "operator_email": _text(split.get("operator_email")),
        "operator_name": _text(split.get("operator_name")),
        "operator_user_id": _text(split.get("operator_user_id")),
        "work_date": parse_calendar_date(split.get("work_date")),
        "qty": _safe_int(split.get("quantity")) or 0,
        "qty_completed": _safe_int(split.get("qty_completed")) or 0,
        "production_branch_id": _text(split.get("production_branch_id")),
    }


def _event(
    order: dict,
    *,
    action_id: str,
    kind: str,
    user: Optional[dict],
    now: str,
    version: int,
    split: Optional[dict] = None,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    from_operator: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> dict:
    view = after or _split_view(split) or {}
    line_idx = view.get("line_idx") if view else None
    body = {
        "id": f"{action_id}:{kind}:{view.get('split_id') or FINISH_SPLIT}",
        "action_id": action_id,
        "kind": kind,
        "order_id": _text(order.get("id")),
        "line_idx": line_idx,
        "line_id": _line_id(order, line_idx) if line_idx is not None else "",
        "split_id": view.get("split_id") or FINISH_SPLIT,
        "operator_email": view.get("operator_email") or "",
        "operator_name": view.get("operator_name") or "",
        "operator_user_id": view.get("operator_user_id") or "",
        "from_operator_email": (from_operator or {}).get("operator_email") or "",
        "from_operator_name": (from_operator or {}).get("operator_name") or "",
        "work_date": view.get("work_date"),
        "qty": view.get("qty") or 0,
        "qty_completed": view.get("qty_completed") or 0,
        "unit": _unit(order, line_idx) if line_idx is not None else "",
        "before": before,
        "after": after,
        "at": now,
        "schedule_version": version,
        **_actor(user),
    }
    if extra:
        body.update(extra)
    return body


def _seen_action(order: dict, action_id: str) -> bool:
    return any(row.get("action_id") == action_id for row in _events(order))


def _append(order: dict, events: List[dict], version: int, *, finalized_action: Optional[str] = None, now: str) -> None:
    order.setdefault("production_schedule_events", [])
    order["production_schedule_events"].extend(events)
    state = dict(_schedule(order))
    state["version"] = version
    if finalized_action:
        state["state"] = "finalized"
        state["finalized_action_id"] = finalized_action
        state["finalized_at"] = now
    elif state.get("state") != "finalized":
        state["state"] = "draft"
    order["production_schedule"] = state


def guard_finalized_move(existing: Sequence[dict], payload_rows: Sequence[dict]) -> None:
    """A finalized split with completed qty keeps its operator. The receiver is a new row."""
    by_id = {_text(row.get("id")): row for row in existing if isinstance(row, dict)}
    for raw in payload_rows:
        if not isinstance(raw, dict):
            continue
        prev = by_id.get(_text(raw.get("id")))
        if not prev:
            continue
        if (_safe_int(prev.get("qty_completed")) or 0) <= 0:
            continue
        old = _text(prev.get("operator_email")).lower()
        new = _text(raw.get("operator_email")).lower()
        if old and new and old != new:
            raise ScheduleError(
                400,
                "SCHEDULE_MOVE_NEW_ROW",
                "Pindah operator setelah finalisasi memakai baris baru. Progress operator asal tetap di baris lama.",
            )


def apply_finalize(order: dict, *, action_id: str, user: Optional[dict], now: Optional[str] = None, finish_date: Optional[str] = None) -> str:
    action_id = _text(action_id)
    if not action_id:
        raise ScheduleError(400, "ACTION_REQUIRED", "action_id wajib untuk finalisasi.")
    if _seen_action(order, action_id):
        return "replay"
    if schedule_finalized(order):
        raise ScheduleError(409, "SCHEDULE_ALREADY_FINAL", "Jadwal sudah dikunci. Perubahan berikutnya dicatat sebagai revisi.")
    splits = [row for row in (order.get("operator_splits") or []) if isinstance(row, dict) and (_safe_int(row.get("quantity")) or 0) > 0]
    if not splits and not finish_date and not (order.get("production_finish_target") or {}).get("date"):
        raise ScheduleError(400, "SCHEDULE_EMPTY", "Jadwal belum memiliki alokasi operator atau target selesai.")
    stamp = _now(now)
    version = int(_schedule(order).get("version") or 0) + 1
    events = [
        _event(order, action_id=action_id, kind="schedule_finalized", user=user, now=stamp, version=version, split=row, before=None, after=_split_view(row))
        for row in splits
    ]
    target = _lock_finish_target(order, finish_date, action_id, user, stamp, version, kind="schedule_finalized")
    events.extend(target)
    _append(order, events, version, finalized_action=action_id, now=stamp)
    return "ok"


def _finish_state(order: dict) -> dict:
    raw = order.get("production_finish_target")
    base = {"date": None, "initial_date": None, "revised_date": None}
    if isinstance(raw, dict):
        base.update({key: parse_calendar_date(raw.get(key)) for key in base})
    return base


def _lock_finish_target(order, finish_date, action_id, user, now, version, *, kind: str) -> List[dict]:
    current = _finish_state(order)
    requested = parse_calendar_date(finish_date) if finish_date else current.get("date")
    if not requested:
        return []
    if kind == "schedule_finalized":
        current["initial_date"] = requested
        current["date"] = requested
        current["revised_date"] = None
    order["production_finish_target"] = current
    after = {"split_id": FINISH_SPLIT, "work_date": requested, "qty": 0, "line_idx": None}
    return [_event(
        order, action_id=action_id, kind=kind, user=user, now=now, version=version,
        before=None, after=after, extra={"finish_target_date": requested},
    )]


def apply_finish_target(order: dict, *, action_id: str, finish_date: str, user: Optional[dict], now: Optional[str] = None) -> str:
    action_id = _text(action_id)
    key = parse_calendar_date(finish_date)
    if not action_id:
        raise ScheduleError(400, "ACTION_REQUIRED", "action_id wajib untuk target selesai.")
    if not key:
        raise ScheduleError(400, "DATE_INVALID", "Tanggal target selesai tidak valid.")
    if _seen_action(order, action_id):
        return "replay"
    stamp = _now(now)
    current = _finish_state(order)
    version = int(_schedule(order).get("version") or 0) + 1
    before_date = current.get("date")
    if schedule_finalized(order):
        if current.get("initial_date") is None:
            current["initial_date"] = before_date
        if before_date == key:
            return "replay"
        current["revised_date"] = key
        current["date"] = key
        kind = "schedule_revised"
    else:
        current["date"] = key
        kind = "schedule_revised"
        version = int(_schedule(order).get("version") or 0)
        order["production_finish_target"] = current
        return "draft"
    order["production_finish_target"] = current
    after = {"split_id": FINISH_SPLIT, "work_date": key, "qty": 0, "line_idx": None}
    before = {"split_id": FINISH_SPLIT, "work_date": before_date, "qty": 0, "line_idx": None}
    event = _event(
        order, action_id=action_id, kind=kind, user=user, now=stamp, version=version,
        before=before, after=after, extra={"finish_target_date": key},
    )
    _append(order, [event], version, now=stamp)
    return "ok"


def record_assignment_change(order: dict, before_splits: Sequence[dict], *, action_id: str, user: Optional[dict], now: Optional[str] = None) -> str:
    if not schedule_finalized(order):
        return "draft"
    action_id = _text(action_id)
    if not action_id:
        raise ScheduleError(400, "ACTION_REQUIRED", "action_id wajib untuk revisi jadwal yang sudah dikunci.")
    if _seen_action(order, action_id):
        return "replay"
    stamp = _now(now)
    version = int(_schedule(order).get("version") or 0) + 1
    before_map = {_text(row.get("id")): row for row in before_splits if isinstance(row, dict)}
    after_map = {_text(row.get("id")): row for row in (order.get("operator_splits") or []) if isinstance(row, dict)}
    events = []
    for sid, row in after_map.items():
        prev = before_map.get(sid)
        after = _split_view(row)
        before = _split_view(prev)
        if before == after:
            continue
        moved = before and after and before.get("operator_email").lower() != after.get("operator_email").lower()
        events.append(_event(
            order, action_id=action_id, kind="schedule_revised", user=user, now=stamp, version=version,
            before=before, after=after, from_operator=before if moved else None, split=row,
        ))
    for sid, prev in before_map.items():
        if sid in after_map:
            continue
        events.append(_event(
            order, action_id=action_id, kind="schedule_revised", user=user, now=stamp, version=version,
            before=_split_view(prev), after=None, split=prev,
        ))
    if not events:
        return "draft"
    _append(order, events, version, now=stamp)
    return "ok"


def record_order_work_date(order: dict, before_date: Optional[str], after_date: Optional[str], *, action_id: str, user: Optional[dict], now: Optional[str] = None) -> str:
    """Revision for the PO-level work date. Draft schedules stay unchanged."""
    if not schedule_finalized(order):
        return "draft"
    action_id = _text(action_id)
    if not action_id:
        raise ScheduleError(400, "ACTION_REQUIRED", "action_id wajib untuk revisi jadwal yang sudah dikunci.")
    if _seen_action(order, action_id):
        return "replay"
    before_key = parse_calendar_date(before_date)
    after_key = parse_calendar_date(after_date)
    if before_key == after_key:
        return "draft"
    stamp = _now(now)
    version = int(_schedule(order).get("version") or 0) + 1
    view_before = {
        "split_id": "__order_work_date__", "work_date": before_key, "qty": 0, "qty_completed": 0,
        "line_idx": None, "operator_email": "", "operator_name": "", "operator_user_id": "",
    }
    view_after = {**view_before, "work_date": after_key}
    event = _event(
        order, action_id=action_id, kind="schedule_revised", user=user, now=stamp, version=version,
        before=view_before, after=view_after,
    )
    _append(order, [event], version, now=stamp)
    return "ok"


def is_working_day(day: date, holidays: Iterable[str]) -> bool:
    if day.weekday() == 6:
        return False
    return day.isoformat() not in set(holidays or [])


def add_working_days(start: date, count: int, holidays: Iterable[str]) -> date:
    """Count working days after `start`. Sunday and listed holidays are skipped."""
    cursor = start
    left = max(0, int(count))
    guard = 0
    while left > 0 and guard < 370:
        cursor += timedelta(days=1)
        guard += 1
        if is_working_day(cursor, holidays):
            left -= 1
    return cursor


def stamp_delivery_sla(
    order: dict,
    holidays: Iterable[str],
    *,
    working_days: int = NORMAL_WORKING_DAYS,
    now: Optional[str] = None,
) -> bool:
    """Stamp the working-day estimate once. Existing stamps and GET callers stay untouched.

    ``working_days`` is 1, 3, or 5. Any other value uses the default of 3.
    A later settings or holiday change does not recompute a stored stamp.
    """
    if order.get("delivery_sla_v2"):
        return False
    if not group2_complete(order):
        return False
    anchor = latest_required_approval_at(order)
    if anchor is None:
        return False
    try:
        days = int(working_days)
    except (TypeError, ValueError):
        days = NORMAL_WORKING_DAYS
    if days not in ALLOWED_WORKING_DAYS:
        days = NORMAL_WORKING_DAYS
    holiday_list = sorted({parse_calendar_date(row) for row in (holidays or []) if parse_calendar_date(row)})
    automatic = add_working_days(anchor.astimezone(WIB).date(), days, holiday_list)
    order["delivery_sla_v2"] = {
        "rule": f"working_days_{days}",
        "working_days": days,
        "unit": "working_days",
        "anchor_at": anchor.isoformat(),
        "automatic_date": automatic.isoformat(),
        "holiday_dates": holiday_list,
        "stamped_at": _now(now),
        "legacy": False,
    }
    return True


def _effective_delivery(
    order: dict,
    today: Optional[date] = None,
    *,
    working_days: int = NORMAL_WORKING_DAYS,
    holidays: Optional[Iterable[str]] = None,
) -> dict:
    """Widget date. Same read contract as Detail PO / Status PO / Jadwal Pengiriman."""
    fact = delivery_sla_effective(order, working_days, today=today, holidays=holidays)
    return {
        "date": fact["date"] if fact["countable"] else None,
        "source": fact["source"],
        "countable": fact["countable"],
        "state": fact["state"],
        "contract_date": fact["date"],
    }


def _overdue(
    order: dict,
    today: date,
    *,
    working_days: int = NORMAL_WORKING_DAYS,
    holidays: Optional[Iterable[str]] = None,
) -> dict:
    fact = _effective_delivery(order, today, working_days=working_days, holidays=holidays)
    out = {"state": fact["state"], "source": fact["source"]}
    if fact["countable"] and fact["date"]:
        out["date"] = fact["date"]
    return out


def _parse_dt(value: Any) -> Optional[datetime]:
    return parse_confirmed_at(value)


def _aging_start(order: dict) -> Optional[datetime]:
    if order.get("priority") == "urgent":
        return _parse_dt(order.get("created_at"))
    return latest_required_approval_at(order)


def _production_end(order: dict):
    if not (_print_done(order) and _packing_done(order)):
        return None
    stamps = []
    for event in ((order.get("packing_info") or {}).get("events") or []):
        if isinstance(event, dict):
            parsed = _parse_dt(event.get("created_at"))
            if parsed:
                stamps.append(parsed)
    if not stamps:
        return "missing"
    return max(stamps)


def _delivery_end(order: dict):
    if not is_fully_delivered(order):
        return None
    parsed = _parse_dt(order.get("delivered_at"))
    return parsed or "missing"


def _aging_bucket(start, end, today: date) -> str:
    if start is None:
        return "belum"
    if end == "missing":
        return "histori"
    if end is None:
        return "aktif"
    return "selesai"


def _days_between(start: datetime, end: datetime) -> int:
    return max(0, (end.astimezone(WIB).date() - start.astimezone(WIB).date()).days)


def _finish_dates(order: dict) -> dict:
    target = _finish_state(order)
    if not schedule_finalized(order):
        return {"status": "unlocked", "initial": None, "revised": None, "current": target.get("date")}
    revised = target.get("revised_date")
    return {
        "status": "revised" if revised else "locked",
        "initial": target.get("initial_date") or target.get("date"),
        "revised": revised,
        "current": target.get("date"),
    }


def _on_time(done: datetime, target_key: Optional[str]) -> Optional[bool]:
    if done is None or done == "missing" or not target_key:
        return None
    return done.astimezone(WIB).date().isoformat() <= target_key


def operator_targets(orders: Iterable[dict]) -> dict:
    """Target Awal stays on the operator who received the finalize event."""
    awal: Dict[str, Dict[str, int]] = {}
    revisi: Dict[str, Dict[str, int]] = {}
    realisasi: Dict[str, Dict[str, int]] = {}
    names: Dict[str, str] = {}
    available = False
    has_revision = False
    for order in orders:
        if not isinstance(order, dict) or not schedule_finalized(order):
            continue
        available = True
        order_revised = any(row.get("kind") == "schedule_revised" for row in _events(order))
        has_revision = has_revision or order_revised
        finals = [row for row in _events(order) if row.get("kind") == "schedule_finalized" and row.get("split_id") != FINISH_SPLIT]
        for event in finals:
            key = _text(event.get("operator_user_id") or event.get("operator_email"))
            if not key or not event.get("unit"):
                continue
            names[key] = event.get("operator_name") or key
            awal.setdefault(key, {})
            awal[key][event["unit"]] = awal[key].get(event["unit"], 0) + int(event.get("qty") or 0)
        for split in order.get("operator_splits") or []:
            if not isinstance(split, dict):
                continue
            key = _text(split.get("operator_user_id") or split.get("operator_email"))
            unit = _unit(order, split.get("line_idx"))
            if not key or not unit:
                continue
            names[key] = split.get("operator_name") or names.get(key) or key
            realisasi.setdefault(key, {})
            realisasi[key][unit] = realisasi[key].get(unit, 0) + int(_safe_int(split.get("qty_completed")) or 0)
            if order_revised:
                revisi.setdefault(key, {})
                revisi[key][unit] = revisi[key].get(unit, 0) + int(_safe_int(split.get("quantity")) or 0)
    return {"available": available, "has_revision": has_revision, "awal": awal, "revisi": revisi, "realisasi": realisasi, "names": names}


def _percent(actual: Optional[int], target: Optional[int]) -> Optional[float]:
    if target is None or target <= 0 or actual is None:
        return None
    return round((actual / target) * 100, 1)


def _cohort(orders: Iterable[dict]) -> dict:
    """Target follows the schedule date. Realization is confirmed qty, not the calendar confirm day."""
    buckets: Dict[str, Dict[str, dict]] = {}

    def slot(day: str, unit: str) -> dict:
        return buckets.setdefault(day, {}).setdefault(unit, {"awal": 0, "revisi": None, "realisasi": 0, "revised": False})

    for order in orders:
        if not isinstance(order, dict) or not schedule_finalized(order):
            continue
        finals = {
            row.get("split_id"): row
            for row in _events(order)
            if row.get("kind") == "schedule_finalized" and row.get("split_id") not in (None, "", FINISH_SPLIT)
        }
        revisions = [row for row in _events(order) if row.get("kind") == "schedule_revised" and row.get("split_id") not in (None, "", FINISH_SPLIT)]
        revised_ids = {row.get("split_id") for row in revisions}
        for split in order.get("operator_splits") or []:
            if not isinstance(split, dict):
                continue
            sid = _text(split.get("id"))
            final = finals.get(sid)
            if not final:
                if any(row.get("split_id") == sid for row in revisions):
                    day = parse_calendar_date(split.get("work_date"))
                    fresh_unit = _unit(order, split.get("line_idx"))
                    if day and fresh_unit:
                        added = slot(day, fresh_unit)
                        added["revised"] = True
                        added["revisi"] = (added["revisi"] or 0) + int(_safe_int(split.get("quantity")) or 0)
                        added["realisasi"] += int(_safe_int(split.get("qty_completed")) or 0)
                continue
            unit = final.get("unit") or _unit(order, split.get("line_idx"))
            if not unit:
                continue
            line = _line(order, split.get("line_idx")) or {}
            awal_unit, awal_qty, _source = _snapshot_pcs(int(final.get("qty") or 0), unit, line)
            done_unit, done_qty, _done_source = _snapshot_pcs(int(_safe_int(split.get("qty_completed")) or 0), unit, line)
            current_unit, current_qty, _current_source = _snapshot_pcs(int(_safe_int(split.get("quantity")) or 0), unit, line)
            awal_day = final.get("work_date") or ""
            current_day = parse_calendar_date(split.get("work_date")) or awal_day
            if not awal_day:
                continue
            if awal_unit != done_unit or awal_unit != current_unit:
                continue
            awal_slot = slot(awal_day, awal_unit)
            awal_slot["awal"] += awal_qty
            if sid not in revised_ids or current_day == awal_day:
                awal_slot["realisasi"] += done_qty
                if sid in revised_ids:
                    awal_slot["revised"] = True
                    awal_slot["revisi"] = (awal_slot["revisi"] or 0) + current_qty
            else:
                carried = 0
                for rev in revisions:
                    if rev.get("split_id") != sid:
                        continue
                    before_day = (rev.get("before") or {}).get("work_date")
                    after_day = (rev.get("after") or {}).get("work_date")
                    if before_day and after_day and before_day != after_day:
                        carried = int((rev.get("before") or {}).get("qty_completed") or 0)
                awal_slot["realisasi"] += carried
                moved = slot(current_day, current_unit)
                moved["revised"] = True
                moved["revisi"] = (moved["revisi"] or 0) + current_qty
                moved["realisasi"] += max(0, done_qty - carried)
    series = []
    for day in sorted(buckets):
        for unit, row in buckets[day].items():
            series.append({
                "day": day,
                "unit": unit,
                "awal": row["awal"],
                "revisi": row["revisi"],
                "realisasi": row["realisasi"],
                "persen_awal": _percent(row["realisasi"], row["awal"]) if row["revisi"] is None else _percent(row["realisasi"], row["awal"]),
                "persen_revisi": _percent(row["realisasi"], row["revisi"]) if row["revisi"] else None,
            })
    return {"series": series, "locked": any(buckets.values())}


def _remaining_by_unit(order: dict) -> Dict[str, int]:
    totals: Dict[str, int] = {}
    for idx, line in enumerate(order.get("line_items") or []):
        if not isinstance(line, dict) or not is_print_line(line):
            continue
        qty = _safe_int(line.get("quantity"))
        if qty is None:
            continue
        remain = max(0, qty - print_completed_for_line(order, idx))
        packed = 0
        for row in ((order.get("packing_info") or {}).get("lines") or []):
            if isinstance(row, dict) and row.get("line_idx") == idx:
                packed = _safe_int(row.get("packed_qty")) or 0
        remain = max(remain, max(0, qty - packed))
        if remain <= 0:
            continue
        unit = _text(line.get("unit")) or "tanpa_satuan"
        totals[unit] = totals.get(unit, 0) + remain
    return totals


def build_wave2(
    orders: Iterable[dict],
    *,
    today: Optional[date] = None,
    working_days: int = NORMAL_WORKING_DAYS,
    holidays: Optional[Iterable[str]] = None,
) -> dict:
    today = today or datetime.now(WIB).date()
    rows = [order for order in orders if isinstance(order, dict) and order.get("id")]
    if not rows:
        return {"status": "zero", "population": "non_cancelled_active"}
    holiday_list = list(holidays or [])
    overdue = unscheduled = legacy = 0
    late_rows = []
    contracts = []
    aging = {
        "produksi": {"aktif": 0, "selesai": 0, "belum": 0, "histori": 0},
        "total": {"aktif": 0, "selesai": 0, "belum": 0, "histori": 0},
    }
    accuracy = {
        "produksi": {"tepat": 0, "terlambat": 0, "berjalan": 0, "tidak_tersedia": 0},
        "pengiriman": {"tepat": 0, "terlambat": 0, "berjalan": 0, "tidak_tersedia": 0},
    }
    bottleneck_po = set()
    bottleneck_lines = 0
    ship_open = set()
    bottleneck_units: Dict[str, int] = {}
    bottleneck_rows = []
    for order in rows:
        oid = str(order.get("id"))
        late = _overdue(order, today, working_days=working_days, holidays=holiday_list)
        shown = _effective_delivery(order, today, working_days=working_days, holidays=holiday_list)
        contracts.append({
            "order_id": oid,
            "po_number": order.get("po_number") or "",
            "date": shown.get("contract_date"),
            "source": late["source"],
            "state": late["state"],
        })
        if late["state"] == "overdue":
            overdue += 1
            late_rows.append({"order_id": oid, "po_number": order.get("po_number") or "", "source": late["source"], "date": late.get("date")})
        elif late["state"] == "unscheduled":
            unscheduled += 1
        elif late["state"] == "legacy":
            legacy += 1
        start = _aging_start(order)
        prod_end = _production_end(order)
        del_end = _delivery_end(order)
        aging["produksi"][_aging_bucket(start, prod_end, today)] += 1
        aging["total"][_aging_bucket(start, del_end, today)] += 1
        finish = _finish_dates(order)
        prod_mark = _on_time(prod_end, finish.get("current") if finish["status"] != "unlocked" else None)
        if finish["status"] == "unlocked" or prod_end == "missing" or (prod_end is not None and prod_mark is None and prod_end != "missing"):
            accuracy["produksi"]["tidak_tersedia"] += 1
        elif prod_end is None:
            accuracy["produksi"]["berjalan"] += 1
        elif prod_mark:
            accuracy["produksi"]["tepat"] += 1
        else:
            accuracy["produksi"]["terlambat"] += 1
        delivery_date = _effective_delivery(
            order, working_days=working_days, holidays=holiday_list,
        )
        ship_mark = _on_time(del_end, delivery_date["date"] if delivery_date["countable"] else None)
        if not delivery_date["countable"] or del_end == "missing":
            accuracy["pengiriman"]["tidak_tersedia"] += 1
        elif del_end is None:
            accuracy["pengiriman"]["berjalan"] += 1
        elif ship_mark:
            accuracy["pengiriman"]["tepat"] += 1
        else:
            accuracy["pengiriman"]["terlambat"] += 1
        hold = bool(order.get("hold"))
        pending_approval = not group2_complete(order)
        produced = _print_done(order) and _packing_done(order)
        if produced and not is_fully_delivered(order):
            ship_open.add(oid)
        if hold or pending_approval or produced:
            continue
        target = finish.get("current")
        if finish["status"] == "unlocked" or not target or today.isoformat() <= target:
            continue
        bottleneck_po.add(oid)
        units = _remaining_by_unit(order)
        for unit, qty in units.items():
            bottleneck_units[unit] = bottleneck_units.get(unit, 0) + qty
        for idx, line in enumerate(order.get("line_items") or []):
            if isinstance(line, dict) and is_print_line(line):
                qty = _safe_int(line.get("quantity"))
                if qty and print_completed_for_line(order, idx) < qty:
                    bottleneck_lines += 1
        bottleneck_rows.append({
            "order_id": oid,
            "po_number": order.get("po_number") or "",
            "target": target,
            "awal": finish.get("initial"),
            "revisi": finish.get("revised"),
            "by_unit": units,
        })
    cohort = _cohort(rows)
    return {
        "status": "complete",
        "population": "non_cancelled_active",
        "target_produksi": {
            "status": "complete" if cohort["locked"] else "unlocked",
            "note": "Target belum dikunci." if not cohort["locked"] else "Realisasi cohort mengikuti tanggal jadwal. Output kalender tetap di widget tren.",
            "series": cohort["series"],
        },
        "target_realisasi": {
            "status": "complete" if cohort["locked"] else "unlocked",
            "note": "Belum ada revisi." if cohort["locked"] and all(row.get("revisi") is None for row in cohort["series"]) else "",
            "series": cohort["series"],
        },
        "po_terlambat": {
            "status": "complete",
            "overdue": overdue,
            "unscheduled": unscheduled,
            "legacy": legacy,
            "rows": late_rows[:8],
            "contracts": contracts,
            "note": "Buffer Stock tidak masuk terlambat. Tanggal yang tidak terbukti tetap legacy.",
        },
        "aging": {"status": "complete", **aging, "note": "Aktif, selesai, belum dapat dihitung, atau histori tidak lengkap."},
        "ketepatan": {"status": "complete", **accuracy, "note": "Ketepatan produksi dan pengiriman dihitung terpisah."},
        "bottleneck": {
            "status": "complete",
            "po": len(bottleneck_po),
            "lines": bottleneck_lines,
            "by_unit": bottleneck_units,
            "pengiriman_belum_selesai": len(ship_open),
            "rows": bottleneck_rows[:8],
            "note": "HOLD dan approval yang belum selesai bukan bottleneck produksi.",
        },
    }


async def ensure_wave2_indexes(db) -> None:
    await db[EVENTS_COLLECTION].create_index(
        [("action_id", 1), ("split_id", 1), ("kind", 1)],
        unique=True,
        name="wave2_schedule_event_action",
    )
    await db[HOLIDAY_COLLECTION].create_index([("date", 1)], unique=True, name="wave2_holiday_date")
    await db[HOLIDAY_HISTORY].create_index([("action_id", 1)], unique=True, name="wave2_holiday_action")


def _mirror_key(item: dict) -> Optional[dict]:
    action_id = _text(item.get("action_id"))
    kind = _text(item.get("kind"))
    if not action_id or not kind:
        return None
    return {"action_id": action_id, "split_id": item.get("split_id"), "kind": kind}


async def mirror_schedule_events(db, events: Sequence[dict]) -> dict:
    """Idempotent derivative of the order events. Never raises and never writes the order."""
    if not events:
        return {"ok": True, "upserted": 0}
    try:
        await ensure_wave2_indexes(db)
        upserted = 0
        for row in events:
            item = {key: value for key, value in dict(row).items() if key != "_id"}
            key = _mirror_key(item)
            if not key:
                continue
            await db[EVENTS_COLLECTION].update_one(key, {"$set": item}, upsert=True)
            upserted += 1
        return {"ok": True, "upserted": upserted}
    except Exception as exc:
        logger.error("production_schedule_mirror_failed event_count=%s error=%s", len(events), exc)
        return {"ok": False, "upserted": 0, "error": "production_schedule_mirror_failed"}


async def reconcile_schedule_mirror(db, order_id: str) -> dict:
    """Copy one order's events into the mirror. Does not change qty, operator, or progress."""
    order = await db.orders.find_one(
        {"id": order_id},
        {"_id": 0, "id": 1, "production_schedule_events": 1},
    )
    if not order:
        return {"ok": False, "reason": "missing_order", "order_id": order_id}
    events = [row for row in (order.get("production_schedule_events") or []) if isinstance(row, dict)]
    result = await mirror_schedule_events(db, events)
    result["order_id"] = order_id
    result["event_count"] = len(events)
    return result


async def holiday_dates(db) -> List[str]:
    rows = await db[HOLIDAY_COLLECTION].find({}, {"_id": 0, "date": 1}).to_list(None)
    return sorted({row.get("date") for row in rows if row.get("date")})


async def list_holidays(db) -> List[dict]:
    rows = await db[HOLIDAY_COLLECTION].find({}, {"_id": 0}).sort("date", 1).to_list(None)
    return rows


async def mutate_holiday(db, *, action: str, action_id: str, day: str, note: str, user: Optional[dict], now: Optional[str] = None) -> dict:
    key = parse_calendar_date(day)
    action_id = _text(action_id)
    if not action_id:
        raise ScheduleError(400, "ACTION_REQUIRED", "action_id wajib.")
    if not key:
        raise ScheduleError(400, "DATE_INVALID", "Tanggal libur tidak valid.")
    await ensure_wave2_indexes(db)
    existing_action = await db[HOLIDAY_HISTORY].find_one({"action_id": action_id}, {"_id": 0})
    if existing_action:
        current = await db[HOLIDAY_COLLECTION].find_one({"date": existing_action.get("date")}, {"_id": 0})
        return {"replay": True, "holiday": current, "event": existing_action}
    current = await db[HOLIDAY_COLLECTION].find_one({"date": key}, {"_id": 0})
    stamp = _now(now)
    if action == "add":
        if current:
            raise ScheduleError(409, "HOLIDAY_EXISTS", "Tanggal libur sudah ada.")
        doc = {"date": key, "note": note, "updated_at": stamp, "updated_by": _text((user or {}).get("email"))}
        await db[HOLIDAY_COLLECTION].insert_one(dict(doc))
        after = doc
        before = None
    elif action == "update":
        if not current:
            raise ScheduleError(404, "HOLIDAY_MISSING", "Tanggal libur tidak ditemukan.")
        before = {"date": key, "note": current.get("note") or ""}
        after = {"date": key, "note": note, "updated_at": stamp, "updated_by": _text((user or {}).get("email"))}
        await db[HOLIDAY_COLLECTION].update_one({"date": key}, {"$set": after})
    elif action == "delete":
        if not current:
            raise ScheduleError(404, "HOLIDAY_MISSING", "Tanggal libur tidak ditemukan.")
        before = {"date": key, "note": current.get("note") or ""}
        after = None
        await db[HOLIDAY_COLLECTION].delete_one({"date": key})
    else:
        raise ScheduleError(400, "HOLIDAY_ACTION", "Aksi libur tidak dikenal.")
    event = {
        "action_id": action_id,
        "action": action,
        "date": key,
        "before": before,
        "after": after,
        "at": stamp,
        **_actor(user),
    }
    await db[HOLIDAY_HISTORY].insert_one(dict(event))
    event.pop("_id", None)
    return {"replay": False, "holiday": after, "event": event}
