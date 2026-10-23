"""Read-only production analytics for Dashboard Produksi Wave 1.

Counts confirmed print increments on operator_splits.progress_entries.
Does not read work_today, timeline notes, or qty_completed as an extra total.
Does not write orders, splits, workflow, stock, journals, payroll, or surat jalan.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from quantity_rules import is_print_line, print_completed_for_line, qty_packed, requires_printing
from services.product_unit_conversion import normalize_uom
from workflow_gates import design_ok, group2_complete, helper_done, payment_ok, penjadwalan_done, sample_ok, screener_done

WIB = ZoneInfo("Asia/Jakarta")
PCS = "pcs"
TARGET_NOTE = "Target Awal dan Target Revisi belum tersedia. Persentase tidak ditampilkan."
LEGACY_NOTE = "Histori konfirmasi tidak tersedia."
LEGACY_EXCLUDED_NOTE = "Entri legacy tanpa tanggal konfirmasi asli tidak masuk histori harian."
NO_UNIT_NOTE = "Qty tanpa satuan tidak dijumlahkan dan tidak dianggap pcs."
PER_LINE_NOTE = "Belum tersedia — menunggu sumber status per line yang valid."
UNVERIFIED_CONVERSION_NOTE = "Konversi historis belum terverifikasi. Qty ditampilkan dalam satuan asli."

_STAGE_ORDER = (
    "Penjadwalan",
    "Screener",
    "Persiapan Helper",
    "Sample",
    "Cetak",
    "Packing",
    "Produksi selesai",
)


def wib_today(now: Optional[datetime] = None) -> date:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(WIB).date()


def parse_confirmed_at(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(WIB)


def _whole_number(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    if abs(number - round(number)) > 1e-6:
        return None
    return int(round(number))


def _add(bucket: Dict[str, Any], unit: str, qty: Any) -> None:
    current = bucket.get(unit, 0)
    total = current + qty
    if isinstance(current, int) and isinstance(qty, int):
        bucket[unit] = total
        return
    bucket[unit] = int(total) if abs(total - round(total)) < 1e-6 else round(total, 6)


def _unit_key(unit: str) -> str:
    return normalize_uom(unit).lower()


def _line_for(order: dict, line_idx: Optional[int]) -> Optional[dict]:
    lines = order.get("line_items") or []
    if line_idx is None or line_idx < 0 or line_idx >= len(lines):
        return None
    line = lines[line_idx]
    return line if isinstance(line, dict) else None


def _resolved_line_idx(split: dict, entry: dict) -> Optional[int]:
    raw = entry.get("line_idx")
    if raw is None:
        raw = split.get("line_idx")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _snapshot_pcs(qty: int, unit: str, line: dict) -> tuple:
    """Convert only from a factor stored on the line. Current master settings are ignored."""
    if _unit_key(unit) == PCS:
        return PCS, qty, "native"
    factor = line.get("conversion_factor")
    if factor in (None, ""):
        return _unit_key(unit), qty, "original"
    base = normalize_uom(line.get("base_uom"))
    transaction = normalize_uom(line.get("transaction_uom") or "")
    try:
        factor_n = float(factor)
    except (TypeError, ValueError):
        factor_n = 0
    if (
        base == "PCS"
        and math.isfinite(factor_n)
        and factor_n > 0
        and (not transaction or transaction == normalize_uom(unit))
    ):
        scaled = qty * factor_n
        if abs(scaled - round(scaled)) < 1e-6:
            scaled = int(round(scaled))
        else:
            scaled = round(scaled, 6)
        return PCS, scaled, "snapshot"
    return _unit_key(unit), qty, "original"


def _conversion_report(sources: Iterable[str]) -> dict:
    found = set(sources)
    if "original" in found:
        return {"status": "unverified", "note": UNVERIFIED_CONVERSION_NOTE}
    if "snapshot" in found:
        return {"status": "snapshot", "note": ""}
    if "native" in found:
        return {"status": "native", "note": ""}
    return {"status": "none", "note": ""}


def _with_conversion_note(note: str, report: dict) -> str:
    extra = report.get("note") or ""
    if not extra or extra in (note or ""):
        return note or ""
    return f"{note} {extra}".strip() if note else extra


def _operator_identity(split: dict) -> Optional[dict]:
    user_id = str(split.get("operator_user_id") or "").strip()
    email = str(split.get("operator_email") or "").strip().lower()
    name = str(split.get("operator_name") or "").strip()
    if user_id:
        return {"operator_id": user_id, "name": name or email or user_id}
    if email:
        return {"operator_id": email, "name": name or email}
    return None


def _coverage() -> Dict[str, int]:
    return {
        "counted": 0,
        "legacy": 0,
        "cancelled": 0,
        "duplicate": 0,
        "no_unit": 0,
        "no_date": 0,
        "bad_qty": 0,
        "no_operator": 0,
    }


def _walk_entries(orders: Iterable[dict]) -> dict:
    coverage = _coverage()
    events = []
    seen_actions = set()
    for order in orders:
        if not isinstance(order, dict) or order.get("cancelled"):
            continue
        order_id = str(order.get("id") or "")
        for split in order.get("operator_splits") or []:
            if not isinstance(split, dict):
                continue
            operator = _operator_identity(split)
            for entry in split.get("progress_entries") or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("cancelled") or entry.get("replaced"):
                    coverage["cancelled"] += 1
                    continue
                if entry.get("legacy") is True:
                    coverage["legacy"] += 1
                    continue
                action_id = str(entry.get("action_id") or "").strip()
                if action_id:
                    if action_id in seen_actions:
                        coverage["duplicate"] += 1
                        continue
                    seen_actions.add(action_id)
                qty = _whole_number(entry.get("qty"))
                if qty is None or qty <= 0:
                    coverage["bad_qty"] += 1
                    continue
                line_idx = _resolved_line_idx(split, entry)
                line = _line_for(order, line_idx)
                unit = str((line or {}).get("unit") or "").strip()
                if line is None or not unit:
                    coverage["no_unit"] += 1
                    continue
                confirmed = parse_confirmed_at(entry.get("confirmed_at"))
                if confirmed is None:
                    coverage["no_date"] += 1
                    continue
                display_unit, display_qty, conversion = _snapshot_pcs(qty, unit, line)
                if operator is None:
                    coverage["no_operator"] += 1
                coverage["counted"] += 1
                events.append({
                    "day": confirmed.date(),
                    "confirmed_at": confirmed.isoformat(),
                    "unit": display_unit,
                    "qty": display_qty,
                    "conversion": conversion,
                    "operator": operator,
                    "order_id": order_id,
                })
    return {"events": events, "coverage": coverage}


def _history_note(coverage: dict, *, placed: bool) -> str:
    notes = []
    if coverage["legacy"] and not placed and coverage["counted"] == 0:
        return LEGACY_NOTE
    if coverage["legacy"]:
        notes.append(LEGACY_EXCLUDED_NOTE)
    if coverage["no_unit"]:
        notes.append(NO_UNIT_NOTE)
    if coverage["no_date"] and not placed:
        notes.append("Sebagian entri tidak punya tanggal konfirmasi.")
    return " ".join(notes)


def _output_status(coverage: dict, bucket: Dict[str, Any], *, window_events: int) -> str:
    if bucket:
        if coverage["no_unit"] or (coverage["legacy"] and coverage["counted"] == 0):
            return "incomplete"
        return "complete"
    if coverage["counted"] == 0 and (coverage["legacy"] or coverage["no_unit"] or coverage["no_date"]):
        return "incomplete"
    if window_events == 0:
        return "zero"
    return "zero"


def _series(events: List[dict], today: date, days: int) -> dict:
    start = today - timedelta(days=days - 1)
    window = [event for event in events if start <= event["day"] <= today]
    units = []
    for event in window:
        if event["unit"] not in units:
            units.append(event["unit"])
    series = {}
    for unit in units:
        points = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            qty = 0
            for event in window:
                if event["unit"] == unit and event["day"] == day:
                    qty = _sum(qty, event["qty"])
            points.append({"day": day.isoformat(), "qty": qty})
        series[unit] = points
    return {"units": units, "series": series, "window_count": len(window)}


def _sum(current: Any, extra: Any) -> Any:
    total = current + extra
    if isinstance(current, int) and isinstance(extra, int):
        return total
    if isinstance(total, float) and abs(total - round(total)) < 1e-6:
        return int(round(total))
    return round(total, 6) if isinstance(total, float) else total


def _bucket_for(events: List[dict]) -> Dict[str, Any]:
    bucket: Dict[str, Any] = {}
    for event in events:
        _add(bucket, event["unit"], event["qty"])
    return bucket


def _latest(events: List[dict]) -> Optional[str]:
    if not events:
        return None
    return max(event["confirmed_at"] for event in events)


def _is_hold(order: dict) -> bool:
    try:
        stage = int(order.get("current_stage") or 0)
    except (TypeError, ValueError):
        stage = 0
    if stage >= 5:
        return False
    if not order.get("design_files"):
        return True
    if not design_ok(order):
        return True
    if not order.get("payment_files"):
        return True
    if order.get("payment_status") != "Approved" and not order.get("payment_bypassed"):
        return True
    return False


def _line_qty(line: dict) -> Optional[int]:
    return _whole_number(line.get("quantity"))


def _print_done(order: dict) -> bool:
    if not requires_printing(order):
        return True
    saw = False
    for index, line in enumerate(order.get("line_items") or []):
        if not isinstance(line, dict) or not is_print_line(line):
            continue
        saw = True
        needed = _line_qty(line)
        if needed is None or print_completed_for_line(order, index) < needed:
            return False
    return saw


def _packing_done(order: dict) -> bool:
    if not requires_printing(order):
        return True
    needed = 0
    saw = False
    for line in order.get("line_items") or []:
        if not isinstance(line, dict) or not is_print_line(line):
            continue
        saw = True
        qty = _line_qty(line)
        if qty is None:
            return False
        needed += qty
    if not saw or needed <= 0:
        return False
    return qty_packed(order) >= needed


def _lagging_stage(order: dict) -> str:
    checks = (
        ("Penjadwalan", penjadwalan_done),
        ("Screener", screener_done),
        ("Persiapan Helper", helper_done),
        ("Sample", sample_ok),
        ("Cetak", _print_done),
        ("Packing", _packing_done),
    )
    for label, check in checks:
        if not check(order):
            return label
    return "Produksi selesai"


def _bars(counts: Dict[str, int], order: Iterable[str], total: int, scope_label: str) -> dict:
    bars = [{"stage": label, "count": counts.get(label, 0)} for label in order if counts.get(label, 0)]
    return {
        "status": "complete" if total else "zero",
        "scope_label": scope_label,
        "bars": bars,
        "total": total,
        "note": "Tidak ada PO pada cakupan ini." if total == 0 else "",
    }


def _workflow(orders: Iterable[dict]) -> dict:
    full_counts = {label: 0 for label in ("HOLD", "Menunggu Approval", *_STAGE_ORDER)}
    production_counts = {label: 0 for label in _STAGE_ORDER}
    design_waiting = 0
    payment_waiting = 0
    approval_unique = 0
    full_total = 0
    production_total = 0
    for order in orders:
        if not isinstance(order, dict) or order.get("cancelled"):
            continue
        full_total += 1
        if _is_hold(order):
            full_counts["HOLD"] += 1
            continue
        if not group2_complete(order):
            full_counts["Menunggu Approval"] += 1
            approval_unique += 1
            if not design_ok(order):
                design_waiting += 1
            if not payment_ok(order):
                payment_waiting += 1
            continue
        stage = _lagging_stage(order)
        full_counts[stage] += 1
        production_counts[stage] += 1
        production_total += 1
    full = _bars(full_counts, ("HOLD", "Menunggu Approval", *_STAGE_ORDER), full_total, "Seluruh workflow")
    full["approval_detail"] = {
        "design_waiting": design_waiting,
        "payment_waiting": payment_waiting,
        "either_unique": approval_unique,
    }
    production = _bars(production_counts, _STAGE_ORDER, production_total, "Produksi saja")
    return {
        "per_po": {"full": full, "production": production},
        "per_line": {"status": "unavailable", "note": PER_LINE_NOTE},
    }


def _performance(events: List[dict], coverage: dict, today: date, days: int) -> dict:
    start = today - timedelta(days=days - 1)
    window = [event for event in events if start <= event["day"] <= today and event.get("operator")]
    ranking: Dict[str, List[dict]] = {}
    names: Dict[str, Dict[str, Any]] = {}
    for event in window:
        operator = event["operator"]
        slot = names.setdefault(operator["operator_id"], {"name": operator["name"], "by_unit": {}})
        _add(slot["by_unit"], event["unit"], event["qty"])
    for operator_id, slot in names.items():
        for unit, qty in slot["by_unit"].items():
            ranking.setdefault(unit, []).append({"operator_id": operator_id, "name": slot["name"], "qty": qty})
    for rows in ranking.values():
        rows.sort(key=lambda row: (-_sort_qty(row["qty"]), row["name"].casefold()))
    units = list(ranking.keys())
    placed = bool(window)
    status = _output_status(coverage, {unit: 1 for unit in units}, window_events=len(window))
    if coverage["counted"] and not window and coverage["no_operator"] == coverage["counted"]:
        status = "incomplete"
    return {
        "status": status,
        "note": TARGET_NOTE if status != "incomplete" or placed else _history_note(coverage, placed=placed) or TARGET_NOTE,
        "target_available": False,
        "units": units,
        "ranking": ranking,
    }


def _sort_qty(qty: Any) -> float:
    try:
        return float(qty)
    except (TypeError, ValueError):
        return 0


def _workload(orders: Iterable[dict]) -> dict:
    grouped: Dict[str, dict] = {}
    missing_unit = 0
    for order in orders:
        if not isinstance(order, dict) or order.get("cancelled"):
            continue
        order_id = str(order.get("id") or "")
        for split in order.get("operator_splits") or []:
            if not isinstance(split, dict):
                continue
            operator = _operator_identity(split)
            if operator is None:
                continue
            quantity = _whole_number(split.get("quantity"))
            if quantity is None:
                continue
            completed = _whole_number(split.get("qty_completed"))
            if completed is None:
                completed = 0
            remaining = max(quantity - completed, 0)
            if remaining <= 0:
                continue
            line_idx = split.get("line_idx")
            if line_idx is None or isinstance(line_idx, bool):
                resolved = None
            else:
                try:
                    resolved = int(line_idx)
                except (TypeError, ValueError):
                    resolved = None
            line = _line_for(order, resolved)
            unit = str((line or {}).get("unit") or "").strip()
            slot = grouped.setdefault(operator["operator_id"], {
                "operator_id": operator["operator_id"],
                "name": operator["name"],
                "remaining_by_unit": {},
                "orders": set(),
                "lines": set(),
                "lines_without_index": 0,
                "conversion_sources": [],
            })
            slot["orders"].add(order_id)
            if resolved is None or not unit:
                slot["lines_without_index"] += 1
                if not unit:
                    missing_unit += 1
                continue
            display_unit, display_qty, source = _snapshot_pcs(remaining, unit, line)
            slot["conversion_sources"].append(source)
            _add(slot["remaining_by_unit"], display_unit, display_qty)
            slot["lines"].add((order_id, resolved))
    operators = []
    sources = []
    for slot in grouped.values():
        sources.extend(slot["conversion_sources"])
        operators.append({
            "operator_id": slot["operator_id"],
            "name": slot["name"],
            "remaining_by_unit": slot["remaining_by_unit"],
            "active_po": len(slot["orders"]),
            "active_lines": len(slot["lines"]),
            "lines_without_index": slot["lines_without_index"],
        })
    operators.sort(key=lambda row: row["name"].casefold())
    note = ""
    if missing_unit:
        note = NO_UNIT_NOTE
    if any(row["lines_without_index"] for row in operators):
        extra = "Alokasi tanpa identitas line tidak dihitung sebagai line."
        note = f"{note} {extra}".strip()
    return {
        "status": "complete" if operators else "zero",
        "note": note,
        "operators": operators,
        "conversion_sources": sources,
    }


def build_plant_analytics(
    orders: Iterable[dict],
    *,
    days: int = 7,
    today: Optional[date] = None,
    generated_at: Optional[str] = None,
) -> dict:
    today = today or wib_today()
    walked = _walk_entries(orders)
    events = walked["events"]
    coverage = walked["coverage"]
    report = _conversion_report(event["conversion"] for event in events)
    today_events = [event for event in events if event["day"] == today]
    today_bucket = _bucket_for(today_events)
    today_status = _output_status(coverage, today_bucket, window_events=len(today_events))
    if today_status == "zero":
        today_note = "Tidak ada output terkonfirmasi hari ini."
        if coverage["legacy"]:
            today_note = f"{today_note} {LEGACY_EXCLUDED_NOTE}"
    else:
        today_note = _history_note(coverage, placed=bool(today_bucket))
    trend = _series(events, today, days)
    trend_bucket = {unit: 1 for unit in trend["units"]}
    trend_status = _output_status(coverage, trend_bucket, window_events=trend["window_count"])
    if trend_status == "zero":
        trend_note = "Tidak ada output terkonfirmasi pada periode ini."
        if coverage["legacy"]:
            trend_note = f"{trend_note} {LEGACY_EXCLUDED_NOTE}"
    else:
        trend_note = _history_note(coverage, placed=bool(trend["units"]))
    return {
        "timezone": "Asia/Jakarta",
        "today_wib": today.isoformat(),
        "generated_at": generated_at,
        "permission": "dashboard.production.view",
        "days": days,
        "conversion": report,
        "today": {
            "status": today_status,
            "by_unit": today_bucket,
            "updated_at": _latest(today_events),
            "note": _with_conversion_note(today_note, report),
        },
        "trend": {
            "status": trend_status,
            "units": trend["units"],
            "series": trend["series"],
            "note": _with_conversion_note(trend_note, report),
        },
        "workflow": _workflow(orders),
        "coverage": coverage,
        "history_complete": coverage["legacy"] == 0 and coverage["no_date"] == 0 and coverage["no_unit"] == 0,
    }


def build_operator_analytics(
    orders: Iterable[dict],
    *,
    days: int = 7,
    today: Optional[date] = None,
    generated_at: Optional[str] = None,
) -> dict:
    today = today or wib_today()
    walked = _walk_entries(orders)
    performance = _performance(walked["events"], walked["coverage"], today, days)
    workload = _workload(orders)
    sources = [event["conversion"] for event in walked["events"]]
    sources.extend(workload.pop("conversion_sources", []))
    report = _conversion_report(sources)
    if performance["status"] == "incomplete" and not performance["ranking"]:
        history = _history_note(walked["coverage"], placed=False) or LEGACY_NOTE
        performance["note"] = f"{history} {TARGET_NOTE}"
    else:
        extras = []
        if walked["coverage"]["legacy"]:
            extras.append(LEGACY_EXCLUDED_NOTE)
        if walked["coverage"]["no_unit"]:
            extras.append(NO_UNIT_NOTE)
        performance["note"] = TARGET_NOTE if not extras else f"{TARGET_NOTE} {' '.join(extras)}"
    from services.production_wave2 import operator_targets

    targets = operator_targets(orders)
    if targets["available"]:
        for rows in performance.get("ranking", {}).values():
            for row in rows:
                key = row.get("operator_id")
                unit = None
                for candidate, bucket in (performance.get("ranking") or {}).items():
                    if row in bucket:
                        unit = candidate
                        break
                awal = (targets["awal"].get(key) or {}).get(unit) if unit else None
                revisi = (targets["revisi"].get(key) or {}).get(unit) if unit else None
                realisasi = (targets["realisasi"].get(key) or {}).get(unit) if unit else None
                row["target_awal"] = awal
                row["target_revisi"] = revisi
                row["realisasi"] = realisasi
                row["pencapaian_awal"] = None if awal is None else round((realisasi or 0) / awal * 100, 1) if awal else None
                row["pencapaian_revisi"] = None if revisi is None else round((realisasi or 0) / revisi * 100, 1) if revisi else None
        performance["target_available"] = True
        revision = "Target Revisi mengikuti perubahan sah." if targets["has_revision"] else "Belum ada revisi."
        performance["note"] = f"Target Awal dari finalisasi jadwal. {revision} Pencapaian memakai qty selesai pada alokasi operator."
    performance["note"] = _with_conversion_note(performance["note"], report)
    workload["note"] = _with_conversion_note(workload.get("note") or "", report)
    return {
        "timezone": "Asia/Jakarta",
        "today_wib": today.isoformat(),
        "generated_at": generated_at,
        "permission": "dashboard.production.operator_analytics",
        "days": days,
        "conversion": report,
        "performance": performance,
        "workload": workload,
        "coverage": walked["coverage"],
        "history_complete": walked["coverage"]["legacy"] == 0,
    }
