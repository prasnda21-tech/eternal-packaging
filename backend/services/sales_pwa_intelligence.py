"""Read-only sales priority feed. Reuses Phase 4 prediction and visit slots.

Does not write orders, outlets, or activities, and does not change the
prediction formula. One row per customer. The highest category wins.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from services.customer_identity import build_identity_context
from services.order_prediction import (
    ORDER_PREDICTION_PROJECTION,
    PATTERN_2_BULANAN,
    PATTERN_2_MINGGUAN,
    PATTERN_3_BULANAN,
    PATTERN_BULANAN,
    PATTERN_MINGGUAN,
    STABILITY_CUKUP,
    STABILITY_SANGAT,
    STATUS_BELUM_WAKTUNYA,
    STATUS_DATA_BELUM_CUKUP,
    STATUS_JATUH_TEMPO,
    STATUS_RISIKO,
    STATUS_TERLAMBAT,
    STATUS_TIDAK_AKTIF,
    STATUS_WAKTU_FOLLOW_UP,
    build_order_prediction,
    customer_group_key,
    is_valid_business_order,
    order_date_of,
    wib_today,
)
from services.sales_pwa import (
    _live_prediction_order,
    _non_negative_decimal,
    _norm,
    _order_line_value,
    _owned_visit_context,
    _reminder_for_outlet,
    _shape_prediction_row,
    _stable_customer_link,
    require_sales_pwa,
)
from services.sales_user_mapping import sales_owner_match, session_user_id

INTELLIGENCE_CAPS = {
    "orders": 5000,
    "customers": 1000,
    "import_history": 10000,
    "prediction_rows": 2000,
}

CONTACT_STATUSES = (
    STATUS_RISIKO,
    STATUS_TERLAMBAT,
    STATUS_JATUH_TEMPO,
    STATUS_WAKTU_FOLLOW_UP,
)
REGULAR_PATTERNS = {
    PATTERN_MINGGUAN,
    PATTERN_2_MINGGUAN,
    PATTERN_BULANAN,
    PATTERN_2_BULANAN,
    PATTERN_3_BULANAN,
}
REGULAR_STABILITY = {STABILITY_SANGAT, STABILITY_CUKUP}

TYPE_OVERDUE = "overdue_follow_up"
TYPE_RISK = "risiko"
TYPE_LATE = "terlambat"
TYPE_DUE = "jatuh_tempo"
TYPE_FOLLOW = "waktu_follow_up"
TYPE_DECLINE = "declining"
TYPE_INACTIVE = "inactive"

TYPE_RANK = {
    TYPE_OVERDUE: 0,
    TYPE_RISK: 1,
    TYPE_LATE: 2,
    TYPE_DUE: 3,
    TYPE_FOLLOW: 4,
    TYPE_DECLINE: 5,
    TYPE_INACTIVE: 6,
}
STATUS_TYPE = {
    STATUS_RISIKO: TYPE_RISK,
    STATUS_TERLAMBAT: TYPE_LATE,
    STATUS_JATUH_TEMPO: TYPE_DUE,
    STATUS_WAKTU_FOLLOW_UP: TYPE_FOLLOW,
    STATUS_TIDAK_AKTIF: TYPE_INACTIVE,
}
GROUP_LABEL = {
    TYPE_OVERDUE: "Follow-up terlambat",
    TYPE_RISK: "Perlu dihubungi",
    TYPE_LATE: "Perlu dihubungi",
    TYPE_DUE: "Perlu dihubungi",
    TYPE_FOLLOW: "Perlu dihubungi",
    TYPE_DECLINE: "Order menurun",
    TYPE_INACTIVE: "Mulai tidak aktif",
}


def _short_date(value: Any) -> str:
    text = str(value or "")[:10]
    try:
        day = date.fromisoformat(text)
    except ValueError:
        return text
    return f"{day.day:02d}/{day.month:02d}/{str(day.year)[2:]}"


def _whole_text(value: Any) -> str:
    number = Decimal(str(value))
    if number == number.to_integral():
        return f"{int(number):,}".replace(",", ".")
    rendered = format(number.normalize(), "f")
    whole, _, fraction = rendered.partition(".")
    return f"{int(whole):,}".replace(",", ".") + "," + fraction


def _rupiah(value: Any) -> str:
    return f"Rp{_whole_text(value)}"


def _prediction_href(customer_key: str) -> str:
    return f"/sales/prediksi/{quote(customer_key or '', safe='')}"


def _identity(customer_key: str) -> str:
    key = str(customer_key or "")
    if key.startswith("id:"):
        return "stable"
    if key.startswith("ambiguous:"):
        return "ambiguous"
    if key.startswith("name:"):
        return "name"
    return "outlet"


def _is_regular(row: dict) -> bool:
    if int(row.get("total_orders") or 0) < 3:
        return False
    if row.get("pattern") not in REGULAR_PATTERNS:
        return False
    return row.get("stability") in REGULAR_STABILITY


def _dated(orders: Sequence[dict]) -> List[Tuple[date, dict]]:
    rows = []
    for order in orders:
        day = order_date_of(order)
        if day:
            rows.append((day, order))
    rows.sort(key=lambda item: (item[0], str(item[1].get("po_number") or ""), str(item[1].get("id") or "")))
    return rows


def _halves(orders: Sequence[dict]) -> Optional[Tuple[List[Tuple[date, dict]], List[Tuple[date, dict]]]]:
    dated = _dated(orders)
    if len(dated) < 4:
        return None
    size = min(len(dated) // 2, 3)
    if size < 2:
        return None
    recent = dated[-size:]
    old = dated[-2 * size:-size]
    if len(old) < 2 or len(recent) < 2:
        return None
    return old, recent


def _median_gap(half: Sequence[Tuple[date, dict]]) -> Optional[float]:
    dates = [day for day, _ in half]
    if len(dates) < 2:
        return None
    gaps = [(dates[index] - dates[index - 1]).days for index in range(1, len(dates))]
    if not gaps:
        return None
    return float(median(gaps))


def _order_quantity(order: dict) -> Optional[Decimal]:
    """None means the quantity signal is unavailable. Missing is not zero."""
    if not _live_prediction_order(order):
        return None
    lines = [line for line in (order.get("line_items") or []) if isinstance(line, dict)]
    if not lines:
        return None
    total = Decimal("0")
    for line in lines:
        qty = _non_negative_decimal(line.get("quantity"))
        if qty is None:
            return None
        total += qty
    return total


def _half_quantity(half: Sequence[Tuple[date, dict]]) -> Optional[Decimal]:
    total = Decimal("0")
    for _, order in half:
        qty = _order_quantity(order)
        if qty is None:
            return None
        total += qty
    return total


def _half_value(half: Sequence[Tuple[date, dict]]) -> Optional[Decimal]:
    total = Decimal("0")
    for _, order in half:
        valued = _order_line_value(order)
        if valued.get("value_status") != "complete" or valued.get("order_value") is None:
            return None
        total += Decimal(str(valued["order_value"]))
    return total


def decline_signals(orders: Sequence[dict]) -> List[dict]:
    """Strict two-half decline. One real signal is enough. No single-order drop."""
    halves = _halves(orders)
    if not halves:
        return []
    old, recent = halves
    signals = []
    old_gap = _median_gap(old)
    recent_gap = _median_gap(recent)
    if old_gap not in (None, 0) and recent_gap is not None:
        if recent_gap >= old_gap * 1.5 and (recent_gap - old_gap) >= 7:
            signals.append({
                "kind": "frequency",
                "days": int(recent_gap - old_gap),
                "reason": (
                    f"Jarak order memanjang dari {_whole_text(old_gap)} hari "
                    f"menjadi {_whole_text(recent_gap)} hari"
                ),
            })
    old_qty = _half_quantity(old)
    recent_qty = _half_quantity(recent)
    if old_qty is not None and recent_qty is not None and old_qty > 0 and recent_qty <= old_qty * Decimal("0.6"):
        signals.append({
            "kind": "quantity",
            "days": 0,
            "reason": f"Jumlah barang turun dari {_whole_text(old_qty)} menjadi {_whole_text(recent_qty)}",
        })
    old_value = _half_value(old)
    recent_value = _half_value(recent)
    if (
        old_value is not None
        and recent_value is not None
        and old_value > 0
        and recent_value <= old_value * Decimal("0.6")
    ):
        signals.append({
            "kind": "value",
            "days": 0,
            "reason": f"Nilai order turun dari {_rupiah(old_value)} menjadi {_rupiah(recent_value)}",
        })
    return signals


def _measured_reason(row: dict, today: date, kind: str) -> Tuple[str, int]:
    status = str(row.get("status") or row.get("followup_status") or "")
    if kind == TYPE_RISK:
        days = int(row.get("days_late") or 0)
        return f"{days} hari melewati perkiraan order", days
    if kind == TYPE_LATE:
        days = int(row.get("days_late") or 0)
        return f"{days} hari lewat akhir perkiraan", days
    if kind == TYPE_DUE:
        mid = str(row.get("prediction_center") or row.get("prediction_mid") or "")[:10]
        try:
            days = (today - date.fromisoformat(mid)).days if mid else 0
        except ValueError:
            days = 0
        start = _short_date(row.get("prediction_start"))
        end = _short_date(row.get("prediction_end"))
        return f"Sudah masuk periode order {start}–{end}", max(days, 0)
    if kind == TYPE_FOLLOW:
        mid = str(row.get("prediction_center") or row.get("prediction_mid") or "")[:10]
        try:
            until = (date.fromisoformat(mid) - today).days if mid else 0
        except ValueError:
            until = 0
        start = str(row.get("followup_start") or "")[:10]
        try:
            waiting = (today - date.fromisoformat(start)).days if start else 0
        except ValueError:
            waiting = 0
        return f"{max(until, 0)} hari menuju perkiraan order", max(waiting, 0)
    if kind == TYPE_INACTIVE:
        days = int(row.get("days_since_order") or 0)
        return f"{days} hari sejak order terakhir, melewati batas tidak aktif", days
    return "", 0


def _blank_item() -> dict:
    return {
        "type": "",
        "group": "",
        "customer_key": "",
        "customer_name": "",
        "identity": "outlet",
        "reason": "",
        "secondary_reasons": [],
        "days": 0,
        "prediction_status": "",
        "last_order_date": None,
        "next_follow_up_at": None,
        "action": {"kind": "visit", "href": "/sales/visit"},
    }


def _prediction_item(row: dict, today: date, kind: str, reason: str, days: int, secondary: List[str]) -> dict:
    key = str(row.get("customer_key") or "")
    item = _blank_item()
    item.update({
        "type": kind,
        "group": GROUP_LABEL[kind],
        "customer_key": key,
        "customer_name": row.get("customer_name") or row.get("customer") or "",
        "identity": _identity(key),
        "reason": reason,
        "secondary_reasons": secondary,
        "days": int(days),
        "prediction_status": str(row.get("status") or ""),
        "last_order_date": row.get("last_order_date"),
        "action": {"kind": "prediction", "href": _prediction_href(key)},
    })
    return item


def _sort_tuple(item: dict) -> tuple:
    return (
        TYPE_RANK.get(item.get("type"), 99),
        -int(item.get("days") or 0),
        _norm(item.get("customer_name")),
        str(item.get("customer_key") or ""),
    )


def _keep_better(current: Optional[dict], incoming: dict) -> dict:
    if current is None or _sort_tuple(incoming) < _sort_tuple(current):
        winner, loser = incoming, current
    else:
        winner, loser = current, incoming
    if loser and loser.get("reason") and loser["reason"] not in winner["secondary_reasons"] and loser["reason"] != winner["reason"]:
        winner["secondary_reasons"] = [*winner["secondary_reasons"], loser["reason"]]
    if loser:
        for extra in loser.get("secondary_reasons") or []:
            if extra and extra not in winner["secondary_reasons"] and extra != winner["reason"]:
                winner["secondary_reasons"].append(extra)
        if not winner.get("last_order_date"):
            winner["last_order_date"] = loser.get("last_order_date")
        if not winner.get("prediction_status"):
            winner["prediction_status"] = loser.get("prediction_status") or ""
        if not winner.get("next_follow_up_at"):
            winner["next_follow_up_at"] = loser.get("next_follow_up_at")
    return winner


def _completeness(*, orders: int, customers: int, history: int, prediction_total: int) -> Tuple[bool, List[str]]:
    reasons = []
    if orders > INTELLIGENCE_CAPS["orders"]:
        reasons.append("orders")
    if customers > INTELLIGENCE_CAPS["customers"]:
        reasons.append("customers")
    if history > INTELLIGENCE_CAPS["import_history"]:
        reasons.append("import_history")
    if prediction_total > INTELLIGENCE_CAPS["prediction_rows"]:
        reasons.append("prediction_rows")
    return bool(reasons), reasons


async def build_sales_pwa_intelligence(db, user: dict, *, now: Optional[datetime] = None) -> dict:
    """Session-scoped priority feed. `now` is for tests; HTTP uses the server clock."""
    uid = require_sales_pwa(user)
    today = wib_today(now)
    match = sales_owner_match(user)
    order_count = await db.orders.count_documents(match)
    customer_count = await db.sales_customers.count_documents({})
    history_query = {"cancelled": {"$ne": True}, "sales_user_id": uid}
    history_count = await db.order_prediction_history.count_documents(history_query)

    projection = dict(ORDER_PREDICTION_PROJECTION)
    projection["sales_user_id"] = 1
    projection["line_items"] = 1
    orders = await db.orders.find(match, projection).to_list(INTELLIGENCE_CAPS["orders"])
    masters = await db.sales_customers.find(
        {},
        {"_id": 0, "id": 1, "name": 1},
    ).to_list(INTELLIGENCE_CAPS["customers"])
    supplemental = await db.order_prediction_history.find(
        history_query,
        {"_id": 0},
    ).to_list(INTELLIGENCE_CAPS["import_history"])

    predicted = build_order_prediction(
        orders,
        supplemental=supplemental,
        masters=masters,
        sales="",
        skip=0,
        limit=INTELLIGENCE_CAPS["prediction_rows"],
        allow_name_fallback=False,
        now=now,
    )
    shaped = [_shape_prediction_row(row, today) for row in (predicted.get("rows") or [])]
    incomplete, incomplete_reasons = _completeness(
        orders=order_count,
        customers=customer_count,
        history=history_count,
        prediction_total=int(predicted.get("total") or 0),
    )

    from services.order_prediction_import import merge_prediction_orders

    valid = [order for order in merge_prediction_orders(orders, supplemental) if is_valid_business_order(order)]
    context = build_identity_context(masters, valid, [])
    grouped: Dict[str, List[dict]] = {}
    for order in valid:
        key = customer_group_key(order, context, allow_name_fallback=False)
        if key:
            grouped.setdefault(key, []).append(order)

    chosen: Dict[str, dict] = {}
    declining_keys = set()
    for row in shaped:
        status = str(row.get("status") or "")
        key = str(row.get("customer_key") or "")
        if not key:
            continue
        kind = STATUS_TYPE.get(status)
        if kind:
            reason, days = _measured_reason(row, today, kind)
            secondary = ["Customer rutin"] if _is_regular(row) and kind in {TYPE_RISK, TYPE_LATE, TYPE_DUE, TYPE_FOLLOW} else []
            if secondary:
                reason = f"{reason} · Customer rutin"
            chosen[key] = _keep_better(chosen.get(key), _prediction_item(row, today, kind, reason, days, []))
        signals = decline_signals(grouped.get(key) or [])
        if signals:
            declining_keys.add(key)
            primary = signals[0]
            extra = [signal["reason"] for signal in signals[1:]]
            if _is_regular(row):
                extra.append("Customer rutin")
            decline_item = _prediction_item(
                row,
                today,
                TYPE_DECLINE,
                primary["reason"],
                int(primary.get("days") or 0),
                extra,
            )
            chosen[key] = _keep_better(chosen.get(key), decline_item)

    ownerships, outlets = await _owned_visit_context(db, user)
    outlet_by_id = {str(row.get("id") or ""): row for row in outlets}
    own_by_outlet = {str(row.get("outlet_id") or ""): row for row in ownerships if row.get("outlet_id")}
    activities = await db.sales_activities.find(
        {"sales_user_id": session_user_id(user), "activity_type": "visit"},
        {"_id": 0, "outlet_id": 1, "activity_at": 1, "next_action": 1, "next_follow_up_at": 1},
    ).sort("activity_at", -1).to_list(1000)
    latest_activity: Dict[str, dict] = {}
    for activity in activities:
        outlet_id = str(activity.get("outlet_id") or "")
        if outlet_id and outlet_id not in latest_activity:
            latest_activity[outlet_id] = activity

    overdue_count = 0
    today_key = today.isoformat()
    for outlet_id, ownership in own_by_outlet.items():
        reminder = _reminder_for_outlet(
            outlet_id,
            ownership,
            outlet_by_id.get(outlet_id) or {},
            latest_activity.get(outlet_id) or {},
            today_key,
        )
        if not reminder or not reminder.get("overdue"):
            continue
        overdue_count += 1
        try:
            follow_day = date.fromisoformat(str(reminder.get("date") or ""))
        except ValueError:
            continue
        days = (today - follow_day).days
        if days <= 0:
            continue
        outlet = outlet_by_id.get(outlet_id) or {}
        link = _stable_customer_link(outlet.get("linked_customer_id"))
        customer_key = f"id:{link}" if link else ""
        dedupe_key = customer_key or f"outlet:{outlet_id}"
        name = ""
        if customer_key and customer_key in chosen:
            name = chosen[customer_key].get("customer_name") or ""
        if not name:
            name = str(outlet.get("name") or "Outlet")
        item = _blank_item()
        item.update({
            "type": TYPE_OVERDUE,
            "group": GROUP_LABEL[TYPE_OVERDUE],
            "customer_key": customer_key,
            "customer_name": name,
            "identity": _identity(customer_key) if customer_key else "outlet",
            "reason": f"Follow-up terlambat {days} hari",
            "days": days,
            "next_follow_up_at": reminder.get("at"),
            "action": {"kind": "visit", "href": "/sales/visit"},
        })
        if customer_key and customer_key in chosen and chosen[customer_key].get("prediction_status"):
            item["prediction_status"] = chosen[customer_key]["prediction_status"]
            item["last_order_date"] = chosen[customer_key].get("last_order_date")
        chosen[dedupe_key] = _keep_better(chosen.get(dedupe_key), item)

    items = sorted(chosen.values(), key=_sort_tuple)
    counts = {
        "priority_total": len(items),
        "need_contact": sum(1 for row in shaped if row.get("status") in CONTACT_STATUSES),
        "inactive": sum(1 for row in shaped if row.get("status") == STATUS_TIDAK_AKTIF),
        "overdue_follow_up": overdue_count,
        "declining": len(declining_keys),
        "insufficient_data": sum(1 for row in shaped if row.get("status") == STATUS_DATA_BELUM_CUKUP),
        "not_due_yet": sum(1 for row in shaped if row.get("status") == STATUS_BELUM_WAKTUNYA),
    }
    return {
        "as_of": today.isoformat(),
        "counts": counts,
        "items": items,
        "incomplete": incomplete,
        "incomplete_reasons": incomplete_reasons,
    }
