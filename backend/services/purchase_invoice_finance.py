"""Canonical purchase-invoice settlement.

Supplier advance is optional. Only active allocations and confirmed
payments reduce outstanding. Purchase return credit is not included yet.
"""
from __future__ import annotations

from typing import Any, Dict

from services.purchase_common import money_round

STATUS_UNPAID = "BELUM_DIBAYAR"
STATUS_PARTIAL = "DIBAYAR_SEBAGIAN"
STATUS_PAID = "LUNAS"
STATUS_CANCELLED = "DIBATALKAN"

PAYMENT_STATUS_LABELS = {
    STATUS_UNPAID: "Belum Dibayar",
    STATUS_PARTIAL: "Dibayar Sebagian",
    STATUS_PAID: "Lunas",
    STATUS_CANCELLED: "Dibatalkan",
    "BELUM DIBAYAR": "Belum Dibayar",
    "DP": "Dibayar Sebagian",
}


def normalize_legacy_payment_status(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if text == "DP":
        return STATUS_PARTIAL
    if text in {"BELUM DIBAYAR", STATUS_UNPAID}:
        return STATUS_UNPAID
    if text in {STATUS_PAID, STATUS_PARTIAL, STATUS_CANCELLED}:
        return text
    return text


def invoice_financial_summary(
    *,
    invoice_amount: Any,
    advance_applied: Any = 0,
    payments_applied: Any = 0,
    cancelled: bool = False,
) -> Dict[str, Any]:
    """Single settlement formula for invoice, payment, and allocation reads."""
    amount = money_round(invoice_amount)
    advance = money_round(advance_applied)
    payments = money_round(payments_applied)
    settled = money_round(advance + payments)
    raw_outstanding = money_round(amount - settled)
    outstanding = raw_outstanding if raw_outstanding > 0 else 0.0
    if cancelled:
        status = STATUS_CANCELLED
    elif outstanding == 0:
        status = STATUS_PAID
    elif settled <= 0:
        status = STATUS_UNPAID
    else:
        status = STATUS_PARTIAL
    return {
        "invoice_amount": amount,
        "advance_applied": advance,
        "payments_applied": payments,
        "settled_amount": settled,
        "outstanding_amount": outstanding,
        "payment_status": status,
        "payment_status_label": PAYMENT_STATUS_LABELS[status],
    }
