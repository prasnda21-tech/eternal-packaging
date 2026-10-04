"""Period purchase totals. Read-only. Does not post, pay, or cancel documents.

Active purchase invoices in the invoice_date range. Paid means non-cancelled
PAID purchase payments cover grand_total. A down payment stays unpaid.
Bar amounts are invoice grand totals by that status, not cash paid.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from services.financial_statement_common import parse_financial_date_range
from services.purchase_common import money_round, with_active_filter
from services.purchase_invoices import COLLECTION, CONFIRMED_PAYMENT_FILTER, PAY_COLLECTION, payment_status

PERM_VIEW = "purchase_invoices.view"
MONEY_Q = Decimal("0.01")
IN_BATCH = 500


class PurchasePeriodError(Exception):
    pass


def _num(value: Decimal) -> float:
    return float(value.quantize(MONEY_Q))


def _decimal(raw: Any) -> Optional[Decimal]:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except Exception:
        return None


def _next_day(iso_day: str) -> str:
    return (date.fromisoformat(iso_day) + timedelta(days=1)).isoformat()


async def _cursor_all(cursor) -> List[dict]:
    rows: List[dict] = []
    async for doc in cursor:
        rows.append(doc)
    return rows


async def get_purchase_period_summary(db, *, start_date: str, end_date: str) -> dict:
    start, end = parse_financial_date_range(
        start_date,
        end_date,
        empty_message="Tanggal wajib diisi",
        start_invalid_message="Tanggal awal tidak valid",
        end_invalid_message="Tanggal akhir tidak valid",
        order_message="Tanggal akhir sebelum tanggal awal",
        error_cls=PurchasePeriodError,
    )
    query = with_active_filter({"invoice_date": {"$gte": start, "$lt": _next_day(end)}})
    invoices = await _cursor_all(db[COLLECTION].find(query, {
        "_id": 0,
        "id": 1,
        "invoice_date": 1,
        "grand_total": 1,
        "cancelled": 1,
    }))
    if not invoices:
        return {
            "status": "empty",
            "start_date": start,
            "end_date": end,
            "timezone": "Asia/Jakarta",
            "basis": "grand_total",
            "amount": None,
            "paid_amount": None,
            "unpaid_amount": None,
            "paid_count": 0,
            "unpaid_count": 0,
            "invoice_count": 0,
            "detail": "Belum ada faktur pembelian aktif pada periode ini",
        }
    ids = [str(row.get("id") or "") for row in invoices if row.get("id")]
    paid_by_invoice: Dict[str, Decimal] = {}
    for index in range(0, len(ids), IN_BATCH):
        chunk = ids[index:index + IN_BATCH]
        payments = await _cursor_all(db[PAY_COLLECTION].find(
            {"purchase_invoice_id": {"$in": chunk}, **CONFIRMED_PAYMENT_FILTER},
            {"_id": 0, "purchase_invoice_id": 1, "amount": 1},
        ))
        for payment in payments:
            amount = _decimal(payment.get("amount"))
            if amount is None:
                return _error(start, end, "Ada pembayaran PAID yang nominalnya tidak dapat dibaca.")
            key = str(payment.get("purchase_invoice_id") or "")
            paid_by_invoice[key] = paid_by_invoice.get(key, Decimal("0.00")) + amount
    total = Decimal("0.00")
    paid_amount = Decimal("0.00")
    unpaid_amount = Decimal("0.00")
    paid_count = 0
    unpaid_count = 0
    for invoice in invoices:
        grand = _decimal(invoice.get("grand_total"))
        if grand is None:
            return _error(start, end, "Ada faktur pembelian dengan grand_total yang tidak dapat dibaca.")
        paid = paid_by_invoice.get(str(invoice.get("id") or ""), Decimal("0.00"))
        status = payment_status(money_round(grand), money_round(paid))
        total += grand
        if status == "LUNAS":
            paid_count += 1
            paid_amount += grand
        else:
            unpaid_count += 1
            unpaid_amount += grand
    total_q = total.quantize(MONEY_Q)
    status = "zero" if total_q == 0 else "complete"
    return {
        "status": status,
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Jakarta",
        "basis": "grand_total",
        "amount": _num(total_q),
        "paid_amount": _num(paid_amount.quantize(MONEY_Q)),
        "unpaid_amount": _num(unpaid_amount.quantize(MONEY_Q)),
        "paid_count": paid_count,
        "unpaid_count": unpaid_count,
        "invoice_count": len(invoices),
        "detail": "Lunas hanya bila pembayaran PAID yang tidak dibatalkan menutup grand_total. DP tetap belum lunas. Bagian batang adalah nilai faktur, bukan kas pembayaran.",
    }


def _error(start: str, end: str, detail: str) -> dict:
    return {
        "status": "error",
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Jakarta",
        "basis": "grand_total",
        "amount": None,
        "paid_amount": None,
        "unpaid_amount": None,
        "paid_count": None,
        "unpaid_count": None,
        "invoice_count": None,
        "detail": detail,
    }
