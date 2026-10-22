"""Kepala Produksi Priority Board — read-model KPI + priority (no writes).

Shared classification for dashboard KPI cards and detail pages.
KPI A–E count unique PO; KPI F sums production remaining by unit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from quantity_rules import is_fully_delivered, is_print_line, print_completed_for_line, requires_printing
from services.dashboard_today import wib_today
from services.division_tasks import (
    _base_row,
    _line_item_ids,
    _safe_int,
    active_scheduled_qty_for_line,
)
from workflow_gates import group2_complete

KPI_KEYS = (
    "kirim-hari-ini",
    "terlambat",
    "urgent",
    "hold",
    "perlu-produksi",
    "sisa-produksi",
)

KPI_META = {
    "kirim-hari-ini": {
        "title": "Kirim Hari Ini",
        "description": "PO dengan tanggal kirim efektif hari ini (WIB) yang belum fully delivered.",
        "metric": "po_count",
    },
    "terlambat": {
        "title": "Terlambat",
        "description": "PO dengan tanggal kirim efektif sebelum hari ini (WIB), belum fully delivered. Mutually exclusive dari Kirim Hari Ini.",
        "metric": "po_count",
    },
    "urgent": {
        "title": "Urgent",
        "description": "PO dengan prioritas eksplisit URGENT (terpisah dari overdue / due today).",
        "metric": "po_count",
    },
    "hold": {
        "title": "HOLD",
        "description": "PO dengan HOLD aktif (approval gate). Jadwalkan diblokir bila belum eligible.",
        "metric": "po_count",
    },
    "perlu-produksi": {
        "title": "Perlu Produksi",
        "description": "PO cetak dengan sisa qty produksi valid > 0 (bukan sisa penjadwalan).",
        "metric": "po_count",
    },
    "sisa-produksi": {
        "title": "Total Sisa Produksi",
        "description": "Jumlah sisa qty produksi per satuan. Satuan berbeda tidak digabung.",
        "metric": "qty_by_unit",
    },
}


def line_unit(li: Optional[dict]) -> str:
    if not isinstance(li, dict):
        return "pcs"
    u = str(li.get("unit") or li.get("uom") or "").strip()
    return u or "pcs"


def effective_delivery_date_key(order: dict) -> Optional[str]:
    raw = order.get("effective_delivery_date") or order.get("tanggal_pengiriman")
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def apply_priority_fields(row: dict, order: dict, li: dict, *, produced: int, printable: int, scheduled: int) -> dict:
    """Mutate KP task/hold row with board read-model fields."""
    prod_remaining = printable - produced
    unscheduled = max(0, printable - scheduled)
    unit = line_unit(li)
    hold = bool(order.get("hold"))
    hold_reasons = list(order.get("hold_reasons") or [])
    urgent = (order.get("priority") or "normal") == "urgent" or bool(order.get("urgent"))
    fully = is_fully_delivered(order)
    date_key = effective_delivery_date_key(order)
    dd_status = order.get("delivery_deadline_status") or "unknown"
    schedule_eligible = (not hold) and group2_complete(order) and not fully
    buffer_stock = bool(order.get("buffer_stock"))

    row["qty_order_current"] = printable
    row["qty_produced"] = max(0, produced)
    row["qty_production_remaining"] = max(0, prod_remaining)
    row["qty_production_over"] = max(0, -prod_remaining)
    row["qty_scheduled"] = scheduled
    row["qty_unscheduled"] = unscheduled
    row["unit"] = unit
    row["effective_delivery_date"] = date_key
    row["delivery_deadline"] = order.get("delivery_deadline_effective")
    row["delivery_deadline_source"] = order.get("delivery_deadline_source")
    row["delivery_deadline_status"] = dd_status
    row["authoritative_hold"] = hold
    row["hold"] = hold
    row["hold_reasons"] = hold_reasons
    row["explicit_priority"] = "urgent" if urgent else "normal"
    row["explicit_urgent"] = urgent
    row["urgent"] = urgent
    row["priority"] = "urgent" if urgent else (order.get("priority") or "normal")
    row["buffer_stock"] = buffer_stock
    row["fully_delivered"] = fully
    row["schedule_eligible"] = schedule_eligible
    row["actionable"] = bool(schedule_eligible and unscheduled > 0)
    if hold or not schedule_eligible:
        row["available_actions"] = []
        if hold:
            row["action_label"] = "HOLD"
    row["kpi_flags"] = classify_row_kpi_flags(row, order)
    row["priority_rank"] = priority_rank(row)
    row["priority_display"] = priority_display_labels(row)
    return row


def classify_row_kpi_flags(row: dict, order: Optional[dict] = None) -> Dict[str, bool]:
    """KP-board-only delivery due flags (calendar WIB). Mutually exclusive.

    OVERDUE  ↔ effective_delivery_date < today_WIB
    DUE TODAY ↔ effective_delivery_date == today_WIB

    Buffer Stock is not a customer SLA — never `terlambat` / OVERDUE customer.
    Row stays on the board for production progress and scheduling.

    Does NOT use the global deadline-status timestamp helper (WIB-midnight overdue).
    Explicit URGENT remains independent and may combine with either flag.
    """
    today = wib_today().isoformat()
    date_key = row.get("effective_delivery_date")
    if date_key is not None:
        date_key = str(date_key).strip()[:10] or None
        if date_key and not (len(date_key) == 10 and date_key[4] == "-" and date_key[7] == "-"):
            date_key = None
    fully = bool(row.get("fully_delivered"))
    hold = bool(row.get("authoritative_hold") or row.get("hold"))
    urgent = bool(row.get("explicit_urgent") or row.get("urgent") or row.get("priority") == "urgent")
    buffer_stock = bool(
        row.get("buffer_stock")
        if "buffer_stock" in (row or {})
        else (order or {}).get("buffer_stock")
    )
    prod_rem = max(0, _safe_int(row.get("qty_production_remaining")))
    due_today = (not fully) and bool(date_key) and date_key == today
    overdue = (not fully) and bool(date_key) and date_key < today and not buffer_stock
    return {
        "kirim-hari-ini": due_today,
        "terlambat": overdue,
        "urgent": urgent and not fully,
        "hold": hold,
        "perlu-produksi": (not fully) and prod_rem > 0,
        "sisa-produksi": (not fully) and prod_rem > 0,
    }


def priority_rank(row: dict) -> int:
    """Lower = higher priority. HOLD/blocked last among presentation groups."""
    flags = row.get("kpi_flags") or classify_row_kpi_flags(row)
    hold = bool(flags.get("hold") or row.get("authoritative_hold"))
    if hold:
        return 50
    actionable = bool(row.get("schedule_eligible")) or max(0, _safe_int(row.get("qty_production_remaining"))) > 0
    if flags.get("terlambat") and actionable:
        return 0
    if flags.get("urgent") and actionable:
        return 1
    if flags.get("kirim-hari-ini") and actionable:
        return 2
    if (row.get("delivery_deadline_status") or "") == "h1" and actionable:
        return 3
    if (row.get("delivery_deadline_status") or "") == "h6" and actionable:
        return 4
    return 10


def priority_display_labels(row: dict) -> List[str]:
    labels = []
    flags = row.get("kpi_flags") or {}
    if flags.get("hold") or row.get("authoritative_hold"):
        labels.append("HOLD")
    if flags.get("terlambat"):
        labels.append("OVERDUE")
    if flags.get("urgent") or row.get("explicit_urgent"):
        labels.append("URGENT")
    if flags.get("kirim-hari-ini"):
        labels.append("DUE TODAY")
    elif (row.get("delivery_deadline_status") or "") == "h1":
        labels.append("H-1")
    if not labels:
        labels.append("NORMAL")
    return labels


def enrich_kepala_task_rows(tasks: List[dict], orders_by_id: Dict[str, dict]) -> List[dict]:
    for row in tasks or []:
        order = orders_by_id.get(row.get("order_id")) or {}
        try:
            idx = int(row.get("line_idx"))
        except (TypeError, ValueError):
            continue
        lis = order.get("line_items") or []
        li = lis[idx] if 0 <= idx < len(lis) else {}
        printable = max(0, _safe_int(row.get("target_qty"), _safe_int(li.get("quantity"))))
        scheduled = max(0, _safe_int(row.get("completed_qty"), active_scheduled_qty_for_line(order, idx)))
        produced = print_completed_for_line(order, idx)
        apply_priority_fields(row, order, li, produced=produced, printable=printable, scheduled=scheduled)
        # Preserve scheduling semantics on legacy fields
        row["target_qty"] = printable
        row["completed_qty"] = scheduled
        row["remaining_qty"] = max(0, printable - scheduled)
    return tasks


def build_kepala_hold_readonly_rows(order: dict) -> List[dict]:
    """HOLD POs that are not already in the actionable KP task queue."""
    if not requires_printing(order):
        return []
    if not order.get("hold"):
        return []
    if is_fully_delivered(order):
        return []
    # If group2 complete, normal builder already emits rows — avoid duplicates.
    if group2_complete(order):
        return []
    out = []
    for idx, li in enumerate(order.get("line_items") or []):
        if not is_print_line(li):
            continue
        printable = max(0, _safe_int(li.get("quantity")))
        if printable <= 0:
            continue
        scheduled = max(0, active_scheduled_qty_for_line(order, idx))
        produced = print_completed_for_line(order, idx)
        row = _base_row(order, "kepala_produksi", line_idx=idx)
        ids = _line_item_ids(li)
        row["accurate_item_id"] = ids["accurate_item_id"]
        row["master_item_id"] = ids["master_item_id"]
        row["current_stock"] = None
        row["target_qty"] = printable
        row["completed_qty"] = scheduled
        row["remaining_qty"] = max(0, printable - scheduled)
        row["required_input"] = "HOLD — menunggu approval"
        row["required_input_type"] = "hold_blocked"
        row["action_label"] = "HOLD"
        row["status"] = "belum"
        row["status_label"] = "HOLD"
        row["readonly_hold"] = True
        apply_priority_fields(row, order, li, produced=produced, printable=printable, scheduled=scheduled)
        row["schedule_eligible"] = False
        row["actionable"] = False
        row["available_actions"] = []
        out.append(row)
    return out


def sort_kepala_priority_rows(rows: Sequence[dict]) -> List[dict]:
    def key(r: dict):
        date_key = r.get("effective_delivery_date") or "9999-99-99"
        rem = max(0, _safe_int(r.get("qty_production_remaining")))
        po = str(r.get("order_number") or r.get("order_id") or "")
        line = _safe_int(r.get("line_idx"), 0)
        return (priority_rank(r), date_key, -rem, po, line)

    return sorted(rows, key=key)


def _unique_po_ids(rows: Iterable[dict], predicate) -> Set[str]:
    ids: Set[str] = set()
    for r in rows:
        if not predicate(r):
            continue
        oid = r.get("order_id")
        if oid:
            ids.add(str(oid))
    return ids


def aggregate_kepala_kpis(rows: Sequence[dict]) -> Dict[str, Any]:
    """Shared KPI aggregation. A–E = unique PO; F = qty by unit."""
    flags_for = lambda key: (lambda r: bool((r.get("kpi_flags") or {}).get(key)))

    sisa_by_unit: Dict[str, int] = {}
    for r in rows:
        if not (r.get("kpi_flags") or {}).get("sisa-produksi"):
            continue
        unit = str(r.get("unit") or "pcs")
        sisa_by_unit[unit] = sisa_by_unit.get(unit, 0) + max(0, _safe_int(r.get("qty_production_remaining")))

    def po_card(key: str) -> dict:
        ids = _unique_po_ids(rows, flags_for(key))
        return {
            "key": key,
            "title": KPI_META[key]["title"],
            "description": KPI_META[key]["description"],
            "metric": "po_count",
            "count": len(ids),
            "value": len(ids),
            "href": f"/tasks/kepala-produksi/prioritas/{key}",
        }

    sisa_display = ", ".join(
        f"{v:,}".replace(",", ".") + f" {u}" for u, v in sorted(sisa_by_unit.items())
    ) if sisa_by_unit else "0"

    return {
        "kirim-hari-ini": po_card("kirim-hari-ini"),
        "terlambat": po_card("terlambat"),
        "urgent": po_card("urgent"),
        "hold": po_card("hold"),
        "perlu-produksi": po_card("perlu-produksi"),
        "sisa-produksi": {
            "key": "sisa-produksi",
            "title": KPI_META["sisa-produksi"]["title"],
            "description": KPI_META["sisa-produksi"]["description"],
            "metric": "qty_by_unit",
            "count": sum(sisa_by_unit.values()),
            "value": sum(sisa_by_unit.values()) if len(sisa_by_unit) <= 1 else None,
            "by_unit": sisa_by_unit,
            "display": sisa_display,
            "href": "/tasks/kepala-produksi/prioritas/sisa-produksi",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today_wib": wib_today().isoformat(),
    }


def filter_rows_for_kpi(rows: Sequence[dict], kpi: str) -> List[dict]:
    key = (kpi or "").strip().lower()
    if key not in KPI_META:
        return []
    return [r for r in rows if (r.get("kpi_flags") or {}).get(key)]


def build_kepala_produksi_board(
    orders: Sequence[dict],
    tasks: List[dict],
) -> Dict[str, Any]:
    """Compose actionable tasks + HOLD readonly union + KPIs (one pass)."""
    orders_by_id = {str(o.get("id")): o for o in orders if o.get("id")}
    enrich_kepala_task_rows(tasks, orders_by_id)
    task_oids = {str(t.get("order_id")) for t in tasks if t.get("order_id")}
    hold_extra: List[dict] = []
    for o in orders:
        oid = str(o.get("id") or "")
        if oid in task_oids:
            continue
        hold_extra.extend(build_kepala_hold_readonly_rows(o))
    board_rows = sort_kepala_priority_rows(list(tasks) + hold_extra)
    # Main queue: actionable scheduling tasks only (HOLD readonly excluded)
    main_tasks = sort_kepala_priority_rows(tasks)
    kpis = aggregate_kepala_kpis(board_rows)
    return {
        "tasks": main_tasks,
        "board_rows": board_rows,
        "kpis": kpis,
        "hold_readonly_count": len(hold_extra),
    }


def resolve_kpi_key(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower().replace("_", "-")
    return key if key in KPI_META else None
