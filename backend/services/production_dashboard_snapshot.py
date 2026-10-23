"""Read-only Production Dashboard snapshot.

Reuses Kepala Produksi aggregation and the Dashboard Produksi summary
predicates. Does not write orders and does not invent a second qty formula.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from quantity_rules import is_fully_delivered, is_print_line, print_completed_for_line
from workflow_gates import group2_complete
from services.dashboard_today import partition_orders_by_delivery, wib_today
from services.division_tasks import build_kepala_produksi_tasks
from services.kepala_produksi_board import build_kepala_produksi_board
from services.production_wave2 import build_wave2

PRIORITY_LIMIT = 8

DRILL_STATUS_PO = {
    "path": "/laporan/status-po?view=active",
    "any_of": ["nav.reports.status_po"],
    "also_any_of": ["schedule.view"],
}
DRILL_KEPALA = {
    "perlu_produksi": "/tasks/kepala-produksi/prioritas/perlu-produksi",
    "sisa_qty": "/tasks/kepala-produksi/prioritas/sisa-produksi",
    "prioritas": "/tasks/kepala-produksi/prioritas/perlu-produksi",
}


def summary_flags(order: dict) -> Dict[str, bool]:
    """Same independent predicates as GET /dashboard/summary.

    Urgent is the workflow priority field only. The legacy ``urgent`` toggle
    is ignored. Deadline statuses stay mutually exclusive. HOLD is the
    snapshot already attached to the order.
    """
    status = order.get("delivery_deadline_status")
    return {
        "overdue": status == "overdue",
        "h6": status == "h6",
        "h1": status == "h1",
        "urgent": order.get("priority") == "urgent",
        "hold": bool(order.get("hold")),
        "design": order.get("design_status") != "Approved" and not order.get("design_bypassed"),
        "payment": order.get("payment_status") != "Approved" and not order.get("payment_bypassed"),
    }


def _ids(orders: Iterable[dict], predicate) -> set:
    found = set()
    for order in orders:
        if not predicate(order):
            continue
        oid = order.get("id")
        if oid:
            found.add(str(oid))
    return found


def _drill_kepala(key: str) -> dict:
    return {
        "path": DRILL_KEPALA[key],
        "any_of": ["nav.tasks.kepala_produksi", "schedule.edit"],
    }


def _priority_rows(rows: Sequence[dict]) -> List[dict]:
    out = []
    for row in rows[:PRIORITY_LIMIT]:
        labels = []
        for label in row.get("priority_display") or []:
            labels.append("≤ 24 jam" if label == "H-1" else label)
        out.append({
            "order_id": row.get("order_id"),
            "line_idx": row.get("line_idx"),
            "line_id": row.get("line_id"),
            "po_number": row.get("order_number") or "",
            "item": row.get("item") or "",
            "unit": row.get("unit") or "",
            "qty_production_remaining": max(0, int(row.get("qty_production_remaining") or 0)),
            "labels": labels,
        })
    return out


def _required_print_qty(raw: Any):
    """Positive line quantity, 0 when the line has no required qty, or None when unusable.

    Comparison stays in the line's own unit. Master conversion is not applied.
    """
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text or not text.lstrip("-").isdigit():
            return None
        number = int(text)
    elif isinstance(raw, int):
        number = raw
    elif isinstance(raw, float):
        if raw != raw or raw != int(raw):
            return None
        number = int(raw)
    else:
        return None
    if number < 0:
        return None
    return number


def _print_availability(order: dict) -> str:
    """complete, partial, none, or not_applicable. Uses confirmed print qty only."""
    rows = [
        (idx, line)
        for idx, line in enumerate(order.get("line_items") or [])
        if isinstance(line, dict) and is_print_line(line)
    ]
    if not rows:
        return "not_applicable"
    required = []
    for idx, line in rows:
        qty = _required_print_qty(line.get("quantity"))
        if qty is None:
            return "not_applicable"
        if qty == 0:
            continue
        required.append((idx, qty))
    if not required:
        return "not_applicable"
    reached = []
    any_completed = False
    for idx, qty in required:
        completed = print_completed_for_line(order, idx)
        if completed > 0:
            any_completed = True
        reached.append(completed >= qty)
    if all(reached):
        return "complete"
    if not any_completed:
        return "none"
    return "partial"


def _kesiapan(active: Sequence[dict]) -> Dict[str, Any]:
    """Unique active POs. HOLD with both approvals stays ready and is noted separately."""
    seen = set()
    ready = not_ready = hold_ready = 0
    complete = partial = none = not_applicable = 0
    for order in active:
        oid = str(order.get("id") or "").strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        if group2_complete(order):
            ready += 1
            if order.get("hold"):
                hold_ready += 1
        else:
            not_ready += 1
        state = _print_availability(order)
        if state == "complete":
            complete += 1
        elif state == "partial":
            partial += 1
        elif state == "none":
            none += 1
        else:
            not_applicable += 1
    hold_note = ""
    if hold_ready:
        hold_note = (
            f"{hold_ready} PO HOLD sudah lolos Design dan Payment. "
            "Ini bukan izin menjadwalkan."
        )
    cetak_note = ""
    if not_applicable:
        cetak_note = (
            f"{not_applicable} PO tidak masuk ketiga kategori karena tidak ada line cetak "
            "atau qty line tidak dapat ditentukan."
        )
    return {
        "status": "zero" if not seen else "complete",
        "population": "non_cancelled_active",
        "siap_produksi": {
            "ready": ready,
            "not_ready": not_ready,
            "hold_ready": hold_ready,
            "note": hold_note,
        },
        "hasil_cetak": {
            "complete": complete,
            "partial": partial,
            "none": none,
            "not_applicable": not_applicable,
            "note": cetak_note,
        },
    }


def build_production_snapshot(orders: Sequence[dict], *, generated_at: Optional[str] = None) -> Dict[str, Any]:
    """Snapshot widgets. Caller attaches HOLD and deadline, and excludes cancelled orders."""
    active, _completed = partition_orders_by_delivery(orders, is_fully_delivered)
    flags = {str(order.get("id")): summary_flags(order) for order in active if order.get("id")}

    def flagged(name: str) -> set:
        return {oid for oid, row in flags.items() if row.get(name)}

    within_6h = flagged("h6")
    within_24h = within_6h | flagged("h1")
    design = flagged("design")
    payment = flagged("payment")

    tasks: List[dict] = []
    for order in active:
        tasks.extend(build_kepala_produksi_tasks(order))
    board = build_kepala_produksi_board(active, tasks)
    kpis = board["kpis"]
    sisa = kpis["sisa-produksi"]
    by_unit = dict(sisa.get("by_unit") or {})
    single_unit = next(iter(by_unit)) if len(by_unit) == 1 else None
    priority_source = board["board_rows"]

    return {
        "timezone": "Asia/Jakarta",
        "today_wib": wib_today().isoformat(),
        "generated_at": generated_at,
        "scope": "non_cancelled_active",
        "permission": "dashboard.production.view",
        "po_aktif": {
            "count": len(active),
            "drill": dict(DRILL_STATUS_PO),
        },
        "perlu_produksi": {
            "count": kpis["perlu-produksi"]["count"],
            "drill": _drill_kepala("perlu_produksi"),
        },
        "sisa_qty": {
            "by_unit": by_unit,
            "value": sisa.get("value") if single_unit else None,
            "unit": single_unit,
            "drill": _drill_kepala("sisa_qty"),
        },
        "po_urgent": {
            "count": len(flagged("urgent")),
            "rule": "priority",
            "drill": None,
        },
        "po_hold": {
            "count": len(flagged("hold")),
            "drill": None,
        },
        "deadline": {
            "within_6h": len(within_6h),
            "within_24h": len(within_24h),
            "label_6h": "≤ 6 jam",
            "label_24h": "≤ 24 jam",
            "drill": None,
        },
        "approval": {
            "design": len(design),
            "payment": len(payment),
            "either_unique": len(design | payment),
            "drill": None,
        },
        "prioritas": {
            "rows": _priority_rows(priority_source),
            "total_rows": len(priority_source),
            "drill": _drill_kepala("prioritas"),
        },
        "kesiapan": _kesiapan(active),
        "wave2": build_wave2(active, today=wib_today()),
    }
