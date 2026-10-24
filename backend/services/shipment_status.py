"""Status Pengiriman Barang — read-only monitoring rows.

Terkirim  = persisted delivery evidence (qty_delivered), not stage index.
Dapat Dikirim = actual Operator print (print_completed_qty), not Penjadwalan.
"""
from __future__ import annotations

from typing import Optional

from quantity_rules import (
    packing_required_total,
    print_completed_qty,
    print_qty_total,
    qty_delivered,
    qty_shipped,
)

# Single-find projection — no N+1. Enough for qty rules + dates.
SHIPMENT_STATUS_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "customer": 1,
    "order_date": 1,
    "deadline": 1,
    "delivery_deadline_manual": 1,
    "design_approved_at": 1,
    "design_bypassed_at": 1,
    "payment_approved_at": 1,
    "payment_bypassed_at": 1,
    "delivery_sla_v2": 1,
    "delivered_at": 1,
    "delivery_proofs.uploaded_at": 1,
    "delivery_proofs.created_at": 1,
    "qty_delivered": 1,
    "delivery_status": 1,
    "current_stage": 1,
    "total_quantity": 1,
    "line_items.quantity": 1,
    "line_items.production_process": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.line_idx": 1,
    "shipments.id": 1,
    "shipments.lines": 1,
    "cancelled": 1,
}


def status_percent(numerator: int, denominator: int) -> int:
    """Uncapped percent (may exceed 100). 0 if no relevant total."""
    n = max(0, int(numerator or 0))
    d = max(0, int(denominator or 0))
    if d <= 0:
        return 0
    return int(round(100.0 * n / d))


def status_percent_display(numerator: int, denominator: int) -> int:
    """Visual cap at 100; does not change persisted qty."""
    return min(100, status_percent(numerator, denominator))


def qty_terkirim(order: dict) -> int:
    """Actual shipped qty from delivery evidence, never inferred from stage alone."""
    return qty_delivered(order)


def qty_dapat_dikirim(order: dict) -> int:
    """Actual Operator qty_completed — not assigned/scheduled qty."""
    return print_completed_qty(order)


def _date_key(raw) -> Optional[str]:
    if not raw:
        return None
    text = str(raw).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text or None


def delivery_date_value(order: dict) -> Optional[str]:
    """Actual delivery date (delivered_at / bukti kirim), not deadline."""
    key = _date_key(order.get("delivered_at"))
    if key:
        return key
    times = []
    for p in order.get("delivery_proofs") or []:
        key = _date_key(p.get("uploaded_at") or p.get("created_at"))
        if key:
            times.append(key)
    return min(times) if times else None


def shipment_status_row(order: dict) -> dict:
    qty_total = packing_required_total(order)
    print_target = print_qty_total(order)
    delivered = qty_terkirim(order)
    printed = qty_dapat_dikirim(order)
    return {
        "id": order.get("id"),
        "customer_name": order.get("customer") or "",
        "order_number": order.get("po_number") or "",
        "order_date": order.get("order_date"),
        "delivery_date": delivery_date_value(order),
        "qty_total": qty_total,
        "qty_delivered": delivered,
        "qty_printed_operator": printed,
        "print_qty_total": print_target,
        "percent_delivered": status_percent(delivered, qty_total),
        "percent_delivered_display": status_percent_display(delivered, qty_total),
        "percent_ready_to_ship": status_percent(printed, print_target),
        "percent_ready_to_ship_display": status_percent_display(printed, print_target),
        # Diagnostic only — current SJ line totals, not the Terkirim numerator.
        "qty_shipped_lines": qty_shipped(order),
    }
