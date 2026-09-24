"""Read-only purchase and sales activity reports.

Document dates are business dates. The filter is inclusive on those dates in
the same calendar sense as the stored YYYY-MM-DD values. This is not a
historical stock as-of report, and GET does not post or repair anything.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from services.purchase_common import money_round


def _text(value: Any) -> str:
    return str(value or "").strip()


def _date(value: Any) -> str:
    text = _text(value)
    return text[:10] if len(text) >= 10 else ""


def _in_range(value: Any, start: str, end: str) -> bool:
    business = _date(value)
    if not business:
        return False
    if start and business < start:
        return False
    if end and business > end:
        return False
    return True


def _page(rows: List[dict], page: int, page_size: int) -> dict:
    size = min(max(int(page_size or 50), 1), 200)
    current = max(int(page or 1), 1)
    start = (current - 1) * size
    total = len(rows)
    return {
        "items": rows[start:start + size],
        "total": total,
        "page": current,
        "page_size": size,
        "page_count": max((total + size - 1) // size, 1),
    }


async def _journal_map(db, source_type: str, source_ids: List[str]) -> Dict[str, dict]:
    if not source_ids:
        return {}
    rows = await db.journal_entries.find(
        {"source_type": source_type, "source_id": {"$in": source_ids}},
        {"_id": 0, "id": 1, "source_id": 1, "status": 1, "number": 1, "date": 1, "reversal_of": 1},
    ).to_list(len(source_ids) + 20)
    found: Dict[str, dict] = {}
    for row in rows:
        if row.get("reversal_of"):
            continue
        found[_text(row.get("source_id"))] = row
    return found


def _posting_label(journal: Optional[dict]) -> str:
    if not journal:
        return "UNPOSTED"
    status = _text(journal.get("status")).lower()
    if status == "posted":
        return "POSTED"
    if status == "reversed":
        return "REVERSED"
    return status.upper() or "UNPOSTED"


async def purchase_activity_report(
    db,
    *,
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    before = await db.journal_entries.count_documents({})
    receipts = await db.purchase_goods_receipts.find(
        {"cancelled": {"$ne": True}},
        {"_id": 0},
    ).to_list(5000)
    invoices = await db.purchase_invoices.find({}, {"_id": 0}).to_list(5000)
    payments = await db.purchase_payments.find({}, {"_id": 0}).to_list(5000)
    orders = {
        _text(row.get("id")): row
        for row in await db.purchase_orders.find({}, {"_id": 0, "id": 1, "po_number": 1, "supplier": 1}).to_list(5000)
    }
    invoice_by_receipt: Dict[str, List[dict]] = {}
    invoice_by_po: Dict[str, List[dict]] = {}
    for invoice in invoices:
        receipt_id = _text(invoice.get("receipt_id"))
        if receipt_id:
            invoice_by_receipt.setdefault(receipt_id, []).append(invoice)
        invoice_by_po.setdefault(_text(invoice.get("purchase_order_id")), []).append(invoice)
    payment_by_invoice: Dict[str, List[dict]] = {}
    for payment in payments:
        payment_by_invoice.setdefault(_text(payment.get("purchase_invoice_id")), []).append(payment)
    receipt_ids = [_text(row.get("id")) for row in receipts]
    invoice_ids = [_text(row.get("id")) for row in invoices]
    payment_ids = [_text(row.get("id")) for row in payments]
    receipt_journals = await _journal_map(db, "purchase_goods_receipt", receipt_ids)
    invoice_journals = await _journal_map(db, "purchase_invoice", invoice_ids)
    payment_journals = await _journal_map(db, "purchase_payment", payment_ids)
    rows = []
    for receipt in receipts:
        business = _date(receipt.get("receipt_date"))
        if not _in_range(business, start_date, end_date):
            continue
        po = orders.get(_text(receipt.get("purchase_order_id"))) or {}
        linked = invoice_by_receipt.get(_text(receipt.get("id"))) or invoice_by_po.get(_text(receipt.get("purchase_order_id"))) or []
        pay_rows = []
        for invoice in linked:
            pay_rows.extend(payment_by_invoice.get(_text(invoice.get("id"))) or [])
        journal = receipt_journals.get(_text(receipt.get("id")))
        for line in receipt.get("lines") or []:
            if not isinstance(line, dict):
                continue
            qty = line.get("received_qty")
            price = line.get("unit_price")
            amount = None
            if qty is not None and price is not None:
                amount = money_round(Decimal(str(qty)) * Decimal(str(price)))
            rows.append({
                "supplier": receipt.get("supplier") or po.get("supplier"),
                "document_type": "goods_receipt",
                "document_number": receipt.get("receipt_number"),
                "document_id": receipt.get("id"),
                "po_number": receipt.get("po_number") or po.get("po_number"),
                "date": business,
                "timezone": "Asia/Jakarta",
                "master_item_id": line.get("master_item_id"),
                "item_name": line.get("item_name"),
                "qty": qty,
                "uom": line.get("unit"),
                "amount": amount,
                "receipt_status": "cancelled" if receipt.get("cancelled") else "received",
                "invoice_numbers": [row.get("invoice_number") for row in linked],
                "invoice_posting": [_posting_label(invoice_journals.get(_text(row.get("id")))) for row in linked],
                "payment_numbers": [row.get("payment_number") for row in pay_rows],
                "payment_posting": [_posting_label(payment_journals.get(_text(row.get("id")))) for row in pay_rows],
                "stock_posting_status": _posting_label(journal),
                "journal_id": (journal or {}).get("id"),
                "journal_number": (journal or {}).get("number"),
                "po_is_not_receipt": True,
            })
    rows.sort(key=lambda row: (row.get("date") or "", row.get("document_number") or ""))
    total_amount = money_round(sum((Decimal(str(row["amount"])) for row in rows if row.get("amount") is not None), Decimal("0")))
    paged = _page(rows, page, page_size)
    if await db.journal_entries.count_documents({}) != before:
        raise RuntimeError("purchase report wrote a journal")
    return {
        "writes": 0,
        "date_meaning": "Tanggal dokumen YYYY-MM-DD inklusif. Bukan saldo historis as-of.",
        "historical_as_of": False,
        "timezone": "Asia/Jakarta",
        "total_amount": total_amount,
        "filtered_count": len(rows),
        **paged,
    }


async def sales_activity_report(
    db,
    *,
    start_date: str = "",
    end_date: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    before = await db.journal_entries.count_documents({})
    invoices = await db.sales_invoices.find({}, {"_id": 0}).to_list(5000)
    receipts = await db.sales_receipts.find({}, {"_id": 0}).to_list(5000)
    orders = {
        _text(row.get("id")): row
        for row in await db.orders.find({}, {"_id": 0, "id": 1, "po_number": 1, "customer": 1}).to_list(5000)
    }
    receipt_by_invoice: Dict[str, List[dict]] = {}
    for receipt in receipts:
        receipt_by_invoice.setdefault(_text(receipt.get("invoice_id")), []).append(receipt)
    invoice_ids = [_text(row.get("id")) for row in invoices]
    receipt_ids = [_text(row.get("id")) for row in receipts]
    invoice_journals = await _journal_map(db, "sales_invoice", invoice_ids)
    receipt_journals = await _journal_map(db, "sales_receipt", receipt_ids)
    rows = []
    for invoice in invoices:
        business = _date(invoice.get("invoice_date"))
        if not _in_range(business, start_date, end_date):
            continue
        order = orders.get(_text(invoice.get("po_id"))) or {}
        pays = receipt_by_invoice.get(_text(invoice.get("id"))) or []
        journal = invoice_journals.get(_text(invoice.get("id")))
        lines = invoice.get("lines") or []
        if not lines:
            lines = [{}]
        for line in lines:
            if not isinstance(line, dict):
                continue
            rows.append({
                "customer": invoice.get("customer_name") or order.get("customer"),
                "document_type": "sales_invoice",
                "document_number": invoice.get("invoice_number"),
                "document_id": invoice.get("id"),
                "order_number": order.get("po_number"),
                "date": business,
                "timezone": "Asia/Jakarta",
                "item_name": line.get("name") or line.get("item_name"),
                "qty": line.get("quantity"),
                "amount": line.get("line_total") if line.get("line_total") is not None else line.get("amount"),
                "delivery_status": invoice.get("delivery_status") or order.get("status_label"),
                "invoice_status": invoice.get("financial_status") or invoice.get("status"),
                "receipt_numbers": [row.get("receipt_number") for row in pays],
                "receipt_posting": [_posting_label(receipt_journals.get(_text(row.get("id")))) for row in pays],
                "posting_status": _posting_label(journal),
                "journal_id": (journal or {}).get("id"),
                "journal_number": (journal or {}).get("number"),
                "order_does_not_reduce_stock": True,
            })
    rows.sort(key=lambda row: (row.get("date") or "", row.get("document_number") or ""))
    total_amount = money_round(sum((Decimal(str(row["amount"])) for row in rows if row.get("amount") is not None), Decimal("0")))
    paged = _page(rows, page, page_size)
    if await db.journal_entries.count_documents({}) != before:
        raise RuntimeError("sales report wrote a journal")
    return {
        "writes": 0,
        "date_meaning": "Tanggal faktur YYYY-MM-DD inklusif, dibaca sebagai tanggal bisnis WIB. Bukan saldo historis as-of.",
        "historical_as_of": False,
        "timezone": "Asia/Jakarta",
        "total_amount": total_amount,
        "filtered_count": len(rows),
        **paged,
    }
