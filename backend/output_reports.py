"""Screener / design / export helpers for Analitik Output.

Reuses operator_analytics deltas and filters. Does not write orders.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from operator_analytics import (
    DATA_SOURCES,
    INDEX_RECOMMENDATIONS,
    SCREEN_NOTE_RE,
    UNATTRIBUTED_ID,
    UNATTRIBUTED_NAME,
    _empty_activity_user,
    _match_events_for_split,
    _order_po_number,
    _split_fallback_day,
    _timeline_qty_events,
    bucket_key,
    completed_in_period_for_split,
    design_line_identity,
    in_period,
    iso_week_key,
    is_print_line_item,
    line_item_label,
    line_item_name,
    month_key,
    order_matches_item,
    order_matches_po,
    packing_activity_events,
    parse_iso_datetime_date,
    prep_activity_events,
    safe_int,
    wib_today,
    weeks_covering,
    wow_pct,
)


def periods_covering(start: date, end: date, grouping: str) -> List[dict]:
    g = (grouping or "week").strip().lower()
    if g == "day":
        out = []
        d = start
        while d <= end:
            out.append({
                "key": d.isoformat(),
                "from": d.isoformat(),
                "to": d.isoformat(),
                "label": d.strftime("%d %b"),
            })
            d += timedelta(days=1)
        return out
    if g == "month":
        out = []
        cur = start.replace(day=1)
        last = end.replace(day=1)
        while cur <= last:
            if cur.month == 12:
                nxt = date(cur.year + 1, 1, 1)
            else:
                nxt = date(cur.year, cur.month + 1, 1)
            bucket_end = min(end, nxt - timedelta(days=1))
            out.append({
                "key": month_key(cur),
                "from": max(start, cur).isoformat(),
                "to": bucket_end.isoformat(),
                "label": cur.strftime("%b %Y"),
            })
            cur = nxt
        return out
    weeks = weeks_covering(start, end)
    return [{"key": w["week"], "from": w["from"], "to": w["to"], "label": w["label"]} for w in weeks]


def completed_by_bucket_for_split(
    split: dict,
    events: Sequence[dict],
    start: date,
    end: date,
    grouping: str = "week",
) -> Dict[str, int]:
    snapshot = max(0, safe_int(split.get("qty_completed")))
    assigned = max(0, safe_int(split.get("quantity")))
    cap = min(snapshot, assigned) if assigned > 0 else snapshot
    matched = _match_events_for_split(split, events)
    buckets: Dict[str, int] = defaultdict(int)
    if matched:
        prev = 0
        running = 0
        for ev in matched:
            delta = ev["qty"] - prev
            prev = ev["qty"]
            if delta <= 0 or not ev["day"] or not in_period(ev["day"], start, end):
                continue
            take = min(delta, max(0, cap - running))
            if take <= 0:
                continue
            buckets[bucket_key(ev["day"], grouping)] += take
            running += take
        return dict(buckets)
    day = _split_fallback_day(split)
    qty = max(0, min(snapshot, assigned if assigned > 0 else snapshot))
    if qty > 0 and in_period(day, start, end) and day:
        return {bucket_key(day, grouping): qty}
    return {}


def compute_output_series(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    grouping: str,
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> dict:
    meta = periods_covering(date_from, date_to, grouping)
    keys = [m["key"] for m in meta]
    totals = {k: 0 for k in keys}
    per_op: Dict[str, dict] = {}
    op_filter = (operator_id or "").strip()
    if op_filter in ("all", ""):
        op_filter = ""
    cust_filter = (customer or "").strip()
    if cust_filter in ("all", ""):
        cust_filter = ""
    for order in orders:
        if order.get("cancelled"):
            continue
        if cust_filter and (order.get("customer") or "") != cust_filter:
            continue
        if not order_matches_item(order, item):
            continue
        if not order_matches_po(order, po):
            continue
        events = _timeline_qty_events(order)
        for split in order.get("operator_splits") or []:
            email = (split.get("operator_email") or "").strip()
            if not email:
                continue
            if op_filter and email != op_filter:
                continue
            rec = per_op.setdefault(email, {
                "operator_id": email, "email": email,
                "name": split.get("operator_name") or email,
                "buckets": {k: 0 for k in keys},
            })
            if split.get("operator_name"):
                rec["name"] = split["operator_name"]
            for bk, qty in completed_by_bucket_for_split(split, events, date_from, date_to, grouping).items():
                if bk in rec["buckets"]:
                    rec["buckets"][bk] += qty
                    totals[bk] += qty
    series = []
    prev = None
    for m in meta:
        qty = totals.get(m["key"], 0)
        series.append({**m, "qty": qty, "wow_pct": wow_pct(qty, prev)})
        prev = qty
    return {
        "grouping": grouping,
        "buckets": series,
        "users": sorted(per_op.values(), key=lambda r: r["name"] or r["email"]),
        "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
    }


def normalize_daily_targets(doc: Optional[dict]) -> Dict[str, int]:
    """email / operator_id → qty_per_day. Current target only (no effective_from)."""
    out: Dict[str, int] = {}
    if not doc:
        return out
    raw = doc.get("targets")
    items: List[dict] = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                items.append({**v, "operator_id": v.get("operator_id") or k, "email": v.get("email") or k})
            else:
                items.append({"operator_id": k, "email": k, "qty_per_day": v})
    elif isinstance(raw, list):
        items = [x for x in raw if isinstance(x, dict)]
    for it in items:
        qty = max(0, safe_int(it.get("qty_per_day")))
        email = (it.get("email") or "").strip()
        oid = str(it.get("operator_id") or "").strip()
        if email:
            out[email] = qty
        if oid:
            out[oid] = qty
    return out


def parse_screen_note(note: str) -> Optional[int]:
    m = SCREEN_NOTE_RE.match((note or "").strip())
    if not m:
        return None
    return int(m.group(1))


def screener_activity_events(order: dict) -> List[dict]:
    """Positive deltas from cumulative 'Screen qty: N' timeline notes (PO-level)."""
    events: List[dict] = []
    prev = 0
    found = False
    for t in order.get("timeline") or []:
        parsed = parse_screen_note(t.get("note") or "")
        if parsed is None:
            continue
        found = True
        email = (t.get("by_email") or "").strip() or UNATTRIBUTED_ID
        delta = parsed - prev
        prev = parsed
        if delta <= 0:
            continue
        events.append({
            "email": email,
            "name": (t.get("by_name") or "").strip() or (UNATTRIBUTED_NAME if email == UNATTRIBUTED_ID else email),
            "role": (t.get("by_role") or "").strip(),
            "day": parse_iso_datetime_date(t.get("at")),
            "delta": delta,
            "attribution": "unattributed" if email == UNATTRIBUTED_ID else "timeline_delta",
        })
    if found:
        return events
    si = order.get("screener_info") or {}
    qty = max(0, safe_int(si.get("screen_qty")))
    email = (si.get("updated_by") or "").strip()
    if qty <= 0:
        return []
    if not email:
        email = UNATTRIBUTED_ID
    return [{
        "email": email,
        "name": (si.get("updated_by_name") or "").strip() or (UNATTRIBUTED_NAME if email == UNATTRIBUTED_ID else email),
        "role": "",
        "day": parse_iso_datetime_date(si.get("updated_at")),
        "delta": qty,
        "attribution": "unattributed" if email == UNATTRIBUTED_ID else "last_saver_snapshot",
    }]


def _print_item_rows(order: dict) -> List[dict]:
    lis = order.get("line_items") or []
    rows = []
    for i, li in enumerate(lis):
        if not is_print_line_item(li):
            continue
        rows.append({
            "line_idx": i,
            "item": line_item_name(order, i),
            "item_label": line_item_label(order, i),
        })
    if not rows and lis:
        rows.append({"line_idx": 0, "item": line_item_name(order, 0), "item_label": line_item_label(order, 0)})
    if not rows:
        rows.append({"line_idx": 0, "item": (order.get("product_name") or "Item"), "item_label": order.get("product_name") or "Item"})
    return rows


def compute_screener_report(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> dict:
    """Read-only screener qty. Deltas from cumulative Screen qty notes — never sum snapshots."""
    users: Dict[str, dict] = {}
    pos: Dict[str, set] = defaultdict(set)
    lines_seen: Dict[str, set] = defaultdict(set)
    days: Dict[str, set] = defaultdict(set)
    last_day: Dict[str, Optional[date]] = {}
    week_meta = weeks_covering(date_from, date_to)
    week_keys = [w["week"] for w in week_meta]
    week_totals = {k: 0 for k in week_keys}
    per_user_weeks: Dict[str, Dict[str, int]] = defaultdict(lambda: {k: 0 for k in week_keys})
    cup_acc: Dict[str, Dict[str, dict]] = defaultdict(dict)
    cup_lines: List[dict] = []
    used_fallback = False
    user_filter = (user_id or "").strip()
    if user_filter in ("all", ""):
        user_filter = ""
    role_filter = (role or "").strip()
    if role_filter in ("all", ""):
        role_filter = ""
    cust_filter = (customer or "").strip()
    if cust_filter in ("all", ""):
        cust_filter = ""
    roles: set = set()
    user_opts: Dict[str, str] = {}

    for order in orders:
        if order.get("cancelled"):
            continue
        if cust_filter and (order.get("customer") or "") != cust_filter:
            continue
        if not order_matches_item(order, item):
            continue
        if not order_matches_po(order, po):
            continue
        po_id = order.get("id") or order.get("po_number")
        po_num = _order_po_number(order) or str(po_id)
        cust = (order.get("customer") or "").strip()
        print_rows = _print_item_rows(order)
        events = screener_activity_events(order)
        for ev in events:
            if ev.get("attribution") == "last_saver_snapshot":
                used_fallback = True
            email = ev["email"]
            user_opts[email] = ev.get("name") or email
            if ev.get("role"):
                roles.add(ev["role"])
            if user_filter and email != user_filter:
                continue
            if role_filter and (ev.get("role") or "") != role_filter:
                continue
            if not in_period(ev.get("day"), date_from, date_to):
                continue
            rec = users.setdefault(email, _empty_activity_user(email, ev.get("name") or email, ev.get("role") or ""))
            if ev.get("name"):
                rec["name"] = ev["name"]
            if ev.get("role"):
                rec["role"] = ev["role"]
            if ev.get("attribution") == "last_saver_snapshot":
                rec["attribution"] = "last_saver_snapshot"
            rec["total_qty"] += ev["delta"]
            pos[email].add(po_id)
            if ev.get("day"):
                days[email].add(ev["day"])
                prevd = last_day.get(email)
                if prevd is None or ev["day"] > prevd:
                    last_day[email] = ev["day"]
                wk = iso_week_key(ev["day"])
                if wk in week_totals:
                    week_totals[wk] += ev["delta"]
                    per_user_weeks[email][wk] += ev["delta"]
            single_line = len(print_rows) == 1
            for pr in print_rows:
                lines_seen[email].add((po_id, pr["line_idx"]))
                item_name = pr["item"]
                cup = cup_acc[email].setdefault(item_name, {
                    "item": item_name, "qty": 0, "pos": set(), "lines": set(), "last_day": None,
                })
                if single_line:
                    cup["qty"] += ev["delta"]
                cup["pos"].add(po_id)
                cup["lines"].add((po_id, pr["line_idx"]))
                if ev.get("day") and (cup["last_day"] is None or ev["day"] > cup["last_day"]):
                    cup["last_day"] = ev["day"]
                cup_lines.append({
                    "user_id": email,
                    "user_name": rec["name"],
                    "role": rec["role"],
                    "po_id": po_id,
                    "po_number": po_num,
                    "customer": cust,
                    "line_idx": pr["line_idx"],
                    "item": item_name,
                    "qty_screener": ev["delta"] if single_line else None,
                    "po_qty_screener": ev["delta"],
                    "qty_split": single_line,
                    "day": ev["day"].isoformat() if ev.get("day") else None,
                })

    rows = []
    this_wk = iso_week_key(date_to)
    today = wib_today()
    if date_from <= today <= date_to:
        this_wk = iso_week_key(today)
    for email, rec in users.items():
        rec["po_count"] = len(pos.get(email) or [])
        rec["line_count"] = len(lines_seen.get(email) or [])
        adays = days.get(email) or set()
        rec["active_days"] = len(adays)
        rec["avg_daily_qty"] = round(rec["total_qty"] / len(adays), 2) if adays else 0.0
        rec["last_activity_at"] = last_day[email].isoformat() if last_day.get(email) else None
        rec["weekly"] = per_user_weeks.get(email) or {k: 0 for k in week_keys}
        rec["this_week_qty"] = int((rec["weekly"] or {}).get(this_wk, 0) or 0)
        cups = []
        for cup in (cup_acc.get(email) or {}).values():
            cups.append({
                "item": cup["item"],
                "qty": cup["qty"],
                "po_count": len(cup["pos"]),
                "line_count": len(cup["lines"]),
                "last_activity_at": cup["last_day"].isoformat() if cup.get("last_day") else None,
            })
        cups.sort(key=lambda c: (-c["qty"], c["item"] or ""))
        rec["cups"] = cups
        rows.append(rec)
    rows.sort(key=lambda r: (-r["total_qty"], r["name"] or r["email"]))

    series = []
    prev_qty = None
    for meta in week_meta:
        qty = week_totals.get(meta["week"], 0)
        series.append({**meta, "qty": qty, "wow_pct": wow_pct(qty, prev_qty)})
        prev_qty = qty

    return {
        "kpis": {
            "total_qty": sum(r["total_qty"] for r in rows),
            "user_count": len(rows),
            "po_count": len(set().union(*pos.values()) if pos else set()),
            "line_count": sum(r["line_count"] for r in rows),
            "avg_daily_qty": round(
                sum(r["total_qty"] for r in rows) / max(1, len(set().union(*days.values()) if days else set())),
                2,
            ) if days else 0.0,
            "this_week_qty": sum(r.get("this_week_qty") or 0 for r in rows),
        },
        "users": rows,
        "cup_lines": cup_lines,
        "weekly_trend": {
            "weeks": series,
            "wow_pct": wow_pct(series[-1]["qty"], series[-2]["qty"]) if len(series) >= 2 else None,
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "filters": {
            "users": [{"id": e, "name": n} for e, n in sorted(user_opts.items(), key=lambda x: x[1] or x[0])],
            "roles": sorted(roles),
        },
        "attribution": {
            "model": "timeline_delta" if not used_fallback else "mixed",
            "cumulative_field": "screener_info.screen_qty",
            "actor": "timeline.by_email pada note 'Screen qty: N'",
            "timestamp": "timeline.at",
            "per_line": False,
            "fallback_used": used_fallback,
            "note": DATA_SOURCES["screener"],
            "line_note": DATA_SOURCES["screener_line"],
        },
        "period": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "read_only": True,
        "sources": DATA_SOURCES,
        "index_recommendations": INDEX_RECOMMENDATIONS,
    }


def compute_design_report(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> dict:
    """Distinct printable lines with actual operator completed qty in the period."""
    rows: List[dict] = []
    seen: set = set()
    per_op: Dict[str, dict] = {}
    per_week: Dict[str, set] = defaultdict(set)
    week_meta = weeks_covering(date_from, date_to)
    op_filter = (operator_id or "").strip()
    if op_filter in ("all", ""):
        op_filter = ""
    cust_filter = (customer or "").strip()
    if cust_filter in ("all", ""):
        cust_filter = ""

    for order in orders:
        if order.get("cancelled"):
            continue
        if cust_filter and (order.get("customer") or "") != cust_filter:
            continue
        if not order_matches_item(order, item):
            continue
        if not order_matches_po(order, po):
            continue
        po_id = order.get("id") or order.get("po_number")
        po_num = _order_po_number(order) or str(po_id)
        cust = (order.get("customer") or "").strip()
        events = _timeline_qty_events(order)
        for split in order.get("operator_splits") or []:
            email = (split.get("operator_email") or "").strip()
            if not email:
                continue
            if op_filter and email != op_filter:
                continue
            assigned = max(0, safe_int(split.get("quantity")))
            completed, days_list = completed_in_period_for_split(split, events, date_from, date_to)
            completed = max(0, min(completed, assigned if assigned > 0 else completed))
            if completed <= 0:
                continue
            dkey, dlabel = design_line_identity(order, split.get("line_idx"), split)
            rec_key = (email, dkey)
            last = max(days_list) if days_list else None
            rec = per_op.setdefault(email, {
                "operator_id": email,
                "email": email,
                "name": split.get("operator_name") or email,
                "design_count": 0,
                "qty": 0,
                "keys": set(),
            })
            if split.get("operator_name"):
                rec["name"] = split["operator_name"]
            rec["qty"] += completed
            if dkey not in rec["keys"]:
                rec["keys"].add(dkey)
                rec["design_count"] += 1
            if last:
                per_week[iso_week_key(last)].add(dkey)
            if rec_key in seen:
                for row in rows:
                    if row["operator_id"] == email and row["design_id"] == dkey:
                        row["qty_completed"] += completed
                        if last and (not row.get("last_activity_at") or last.isoformat() > row["last_activity_at"]):
                            row["last_activity_at"] = last.isoformat()
                        break
                continue
            seen.add(rec_key)
            rows.append({
                "operator_id": email,
                "operator": rec["name"],
                "email": email,
                "po_id": po_id,
                "po_number": po_num,
                "customer": cust,
                "item": line_item_name(order, split.get("line_idx")),
                "item_label": line_item_label(order, split.get("line_idx")),
                "line_idx": split.get("line_idx"),
                "design_id": dkey,
                "design_label": dlabel,
                "qty_completed": completed,
                "last_activity_at": last.isoformat() if last else None,
            })

    operators = []
    for email, rec in per_op.items():
        operators.append({
            "operator_id": email,
            "email": email,
            "name": rec["name"],
            "design_count": rec["design_count"],
            "qty_completed": rec["qty"],
        })
    operators.sort(key=lambda r: (-r["design_count"], r["name"] or r["email"]))
    weekly = []
    for meta in week_meta:
        weekly.append({**meta, "design_count": len(per_week.get(meta["week"]) or set()), "qty": 0})
    total_distinct = len({r["design_id"] for r in rows})
    return {
        "kpis": {
            "total_designs": total_distinct,
            "operator_count": len(operators),
            "label": "Design / Line Cetak",
        },
        "operators": operators,
        "lines": rows,
        "weekly": weekly,
        "identity": DATA_SOURCES["design"],
        "period": {
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "week_definition": "ISO-8601 Senin–Minggu, zona WIB (UTC+7)",
        },
        "read_only": True,
        "sources": DATA_SOURCES,
    }


def iter_operator_export_rows(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
    daily_targets: Optional[Dict[str, int]] = None,
) -> List[dict]:
    op_filter = (operator_id or "").strip()
    if op_filter in ("all", ""):
        op_filter = ""
    cust_filter = (customer or "").strip()
    if cust_filter in ("all", ""):
        cust_filter = ""
    targets = daily_targets or {}
    out: List[dict] = []
    for order in orders:
        if order.get("cancelled"):
            continue
        if cust_filter and (order.get("customer") or "") != cust_filter:
            continue
        if not order_matches_item(order, item):
            continue
        if not order_matches_po(order, po):
            continue
        po_num = _order_po_number(order) or str(order.get("id") or "")
        cust = (order.get("customer") or "").strip()
        events = _timeline_qty_events(order)
        for split in order.get("operator_splits") or []:
            email = (split.get("operator_email") or "").strip()
            if not email or (op_filter and email != op_filter):
                continue
            name = split.get("operator_name") or email
            target = targets.get(email)
            assigned = max(0, safe_int(split.get("quantity")))
            snapshot = max(0, safe_int(split.get("qty_completed")))
            cap = min(snapshot, assigned) if assigned > 0 else snapshot
            matched = _match_events_for_split(split, events)
            dkey, dlabel = design_line_identity(order, split.get("line_idx"), split)
            cup = line_item_name(order, split.get("line_idx"))
            line_idx = split.get("line_idx")
            day_qty: Dict[date, int] = defaultdict(int)
            if matched:
                prev = 0
                running = 0
                for ev in matched:
                    delta = ev["qty"] - prev
                    prev = ev["qty"]
                    if delta <= 0 or not ev["day"] or not in_period(ev["day"], date_from, date_to):
                        continue
                    take = min(delta, max(0, cap - running))
                    if take <= 0:
                        continue
                    day_qty[ev["day"]] += take
                    running += take
            else:
                day = _split_fallback_day(split)
                qty = cap
                if qty > 0 and day and in_period(day, date_from, date_to):
                    day_qty[day] += qty
            try:
                line_no = int(line_idx) + 1
            except (TypeError, ValueError):
                line_no = line_idx
            for day, qty in sorted(day_qty.items()):
                out.append({
                    "tanggal": day.isoformat(),
                    "minggu": iso_week_key(day),
                    "operator": name,
                    "operator_id": email,
                    "no_po": po_num,
                    "customer": cust,
                    "cup_item": cup,
                    "line": line_no,
                    "design_line": dlabel,
                    "qty_completed": qty,
                    "target_per_hari": target if target is not None else "",
                })
    return out


def iter_helper_export_rows(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> List[dict]:
    user_filter = (user_id or "").strip()
    if user_filter in ("all", ""):
        user_filter = ""
    packed_by: Dict[Tuple[str, Any, Optional[date]], int] = defaultdict(int)
    for order in orders:
        if order.get("cancelled") or not order_matches_item(order, item) or not order_matches_po(order, po):
            continue
        po_id = order.get("id") or order.get("po_number")
        for ev in packing_activity_events(order):
            if not in_period(ev.get("day"), date_from, date_to):
                continue
            if ev["email"] == UNATTRIBUTED_ID:
                continue
            packed_by[(ev["email"], po_id, ev.get("day"))] += ev["delta"]
    out: List[dict] = []
    for order in orders:
        if order.get("cancelled"):
            continue
        if customer and customer not in ("all", "") and (order.get("customer") or "") != customer:
            continue
        if not order_matches_item(order, item) or not order_matches_po(order, po):
            continue
        po_id = order.get("id") or order.get("po_number")
        po_num = _order_po_number(order) or str(po_id)
        cust = (order.get("customer") or "").strip()
        print_rows = _print_item_rows(order)
        item_label = print_rows[0]["item"] if len(print_rows) == 1 else "; ".join(p["item"] for p in print_rows)
        line_val = print_rows[0]["line_idx"] if len(print_rows) == 1 else ""
        try:
            line_no = int(line_val) + 1 if line_val != "" else ""
        except (TypeError, ValueError):
            line_no = line_val
        for ev in prep_activity_events(order):
            email = ev["email"]
            if user_filter and email != user_filter:
                continue
            if role and role not in ("all", "") and (ev.get("role") or "") != role:
                continue
            if not in_period(ev.get("day"), date_from, date_to):
                continue
            packed = packed_by.get((email, po_id, ev.get("day")))
            out.append({
                "tanggal": ev["day"].isoformat() if ev.get("day") else "",
                "minggu": iso_week_key(ev["day"]) if ev.get("day") else "",
                "helper": ev.get("name") or email,
                "helper_id": email,
                "no_po": po_num,
                "customer": cust,
                "cup_item": item_label,
                "line": line_no,
                "qty_prepared": ev["delta"],
                "qty_packed": packed if packed is not None else "",
            })
    return out


def iter_screener_export_rows(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> List[dict]:
    user_filter = (user_id or "").strip()
    if user_filter in ("all", ""):
        user_filter = ""
    out: List[dict] = []
    for order in orders:
        if order.get("cancelled"):
            continue
        if customer and customer not in ("all", "") and (order.get("customer") or "") != customer:
            continue
        if not order_matches_item(order, item) or not order_matches_po(order, po):
            continue
        po_num = _order_po_number(order) or str(order.get("id") or "")
        cust = (order.get("customer") or "").strip()
        print_rows = _print_item_rows(order)
        item_label = print_rows[0]["item"] if len(print_rows) == 1 else "; ".join(p["item"] for p in print_rows)
        line_val = print_rows[0]["line_idx"] if len(print_rows) == 1 else ""
        try:
            line_no = int(line_val) + 1 if line_val != "" else ""
        except (TypeError, ValueError):
            line_no = line_val
        for ev in screener_activity_events(order):
            email = ev["email"]
            if user_filter and email != user_filter:
                continue
            if role and role not in ("all", "") and (ev.get("role") or "") != role:
                continue
            if not in_period(ev.get("day"), date_from, date_to):
                continue
            out.append({
                "tanggal": ev["day"].isoformat() if ev.get("day") else "",
                "minggu": iso_week_key(ev["day"]) if ev.get("day") else "",
                "screener": ev.get("name") or email,
                "screener_id": email,
                "no_po": po_num,
                "customer": cust,
                "cup_item": item_label,
                "line": line_no,
                "qty_screener": ev["delta"],
            })
    return out


def iter_design_export_rows(
    orders: Iterable[dict],
    *,
    date_from: date,
    date_to: date,
    operator_id: Optional[str] = None,
    customer: Optional[str] = None,
    item: Optional[str] = None,
    po: Optional[str] = None,
) -> List[dict]:
    report = compute_design_report(
        orders, date_from=date_from, date_to=date_to,
        operator_id=operator_id, customer=customer, item=item, po=po,
    )
    out = []
    for r in report.get("lines") or []:
        try:
            line_no = int(r["line_idx"]) + 1
        except (TypeError, ValueError):
            line_no = r.get("line_idx")
        out.append({
            "operator": r.get("operator"),
            "operator_id": r.get("operator_id"),
            "no_po": r.get("po_number"),
            "customer": r.get("customer"),
            "cup_item": r.get("item"),
            "line": line_no,
            "design_identity": r.get("design_label"),
            "qty_completed": r.get("qty_completed"),
            "tanggal": r.get("last_activity_at") or "",
        })
    return out


EXPORT_COLUMNS = {
    "operator": [
        ("tanggal", "Tanggal"),
        ("minggu", "Minggu"),
        ("operator", "Operator"),
        ("no_po", "No PO"),
        ("customer", "Customer"),
        ("cup_item", "Cup / Item"),
        ("line", "Line"),
        ("design_line", "Design / Line"),
        ("qty_completed", "Qty Completed"),
        ("target_per_hari", "Target / Hari"),
    ],
    "helper": [
        ("tanggal", "Tanggal"),
        ("minggu", "Minggu"),
        ("helper", "Helper"),
        ("no_po", "No PO"),
        ("customer", "Customer"),
        ("cup_item", "Cup / Item"),
        ("line", "Line"),
        ("qty_prepared", "Qty Prepared"),
        ("qty_packed", "Qty Packed"),
    ],
    "screener": [
        ("tanggal", "Tanggal"),
        ("minggu", "Minggu"),
        ("screener", "Screener"),
        ("no_po", "No PO"),
        ("customer", "Customer"),
        ("cup_item", "Cup / Item"),
        ("line", "Line"),
        ("qty_screener", "Qty Screener"),
    ],
    "design": [
        ("operator", "Operator"),
        ("no_po", "No PO"),
        ("customer", "Customer"),
        ("cup_item", "Cup / Item"),
        ("line", "Line"),
        ("design_identity", "Design Identity"),
        ("qty_completed", "Qty Completed"),
        ("tanggal", "Tanggal"),
    ],
}
