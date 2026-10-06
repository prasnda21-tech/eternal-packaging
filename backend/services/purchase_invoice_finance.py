"""Canonical purchase-invoice settlement.

Supplier advance is optional. Active purchase-return credit, active
advance allocations, and confirmed payments reduce outstanding. Return
credit is not a payment. Excess credit is supplier credit, never a
negative outstanding.
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
    purchase_return_credit: Any = 0,
    cancelled: bool = False,
) -> Dict[str, Any]:
    """invoice - return credit - advance - confirmed payments, floored at zero."""
    amount = money_round(invoice_amount)
    credit = money_round(purchase_return_credit)
    advance = money_round(advance_applied)
    payments = money_round(payments_applied)
    settled = money_round(advance + payments)
    raw_outstanding = money_round(amount - credit - settled)
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
        "purchase_return_credit": credit,
        "advance_applied": advance,
        "payments_applied": payments,
        "settled_amount": settled,
        "outstanding_amount": outstanding,
        "payment_status": status,
        "payment_status_label": PAYMENT_STATUS_LABELS[status],
    }
