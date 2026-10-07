"""Financial effect of a confirmed purchase return.

Credit follows the invoice line's own qty and line_subtotal. It is not a
payment and not uang muka. Excess above outstanding becomes supplier credit.
Ambiguous invoice ownership is never guessed.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.accounts_payable import supplier_identity_key
from services.purchase_common import money_round, now_iso, qty_le, stamp_create, stamp_update
from services.purchase_invoice_finance import invoice_financial_summary

CREDIT_COLLECTION = "purchase_return_credits"
SUPPLIER_CREDIT_COLLECTION = "purchase_supplier_credits"
INVOICE_COLLECTION = "purchase_invoices"

STATUS_ACTIVE = "ACTIVE"
STATUS_REVERSED = "REVERSED"

FINANCIAL_NONE = "NO_FINANCIAL_EFFECT"
FINANCIAL_PENDING = "PENDING_FINANCIAL_ALLOCATION"
FINANCIAL_DONE = "FINANCIALIZED"
FINANCIAL_UNAVAILABLE = "FINANCIAL_VALUE_UNAVAILABLE"


def return_credit_amount(
    *,
    line_value: Any,
    invoice_qty: Any,
    qty: Any,
    already_qty: Any = 0,
    already_amount: Any = 0,
) -> float:
    """Credit from the invoice line. The last remaining qty takes the remaining value."""
    total_value = Decimal(str(money_round(line_value)))
    total_qty = Decimal(str(invoice_qty or 0))
    credit_qty = Decimal(str(qty or 0))
    used_qty = Decimal(str(already_qty or 0))
    used_amount = Decimal(str(money_round(already_amount)))
    if total_qty <= 0 or credit_qty <= 0:
        raise HTTPException(400, "Qty kredit retur tidak valid.")
    remaining_qty = total_qty - used_qty
    remaining_value = total_value - used_amount
    if credit_qty > remaining_qty + Decimal("0.0000001"):
        raise HTTPException(400, "Qty kredit melebihi qty faktur yang masih dapat dikreditkan.")
    if remaining_value < 0:
        remaining_value = Decimal("0")
    if credit_qty == remaining_qty or abs(credit_qty - remaining_qty) <= Decimal("0.0000001"):
        return money_round(remaining_value)
    portion = money_round(total_value * credit_qty / total_qty)
    if Decimal(str(portion)) > remaining_value:
        return money_round(remaining_value)
    return portion


async def active_return_credit_total(db, invoice_id: str, session=None) -> float:
    rows = await db[CREDIT_COLLECTION].find(
        {"purchase_invoice_id": str(invoice_id), "status": STATUS_ACTIVE},
        {"_id": 0, "credit_amount": 1},
        session=session,
    ).to_list(2000)
    return money_round(sum(float(row.get("credit_amount") or 0) for row in rows))


async def active_return_credit_breakdown(db, invoice_id: str, session=None) -> dict:
    rows = await db[CREDIT_COLLECTION].find(
        {"purchase_invoice_id": str(invoice_id), "status": STATUS_ACTIVE},
        {"_id": 0, "credit_amount": 1, "applied_to_invoice_amount": 1, "supplier_credit_amount": 1},
        session=session,
    ).to_list(2000)
    return {
        "purchase_return_credit": money_round(sum(float(row.get("credit_amount") or 0) for row in rows)),
        "purchase_return_credit_applied_to_outstanding": money_round(
            sum(float(row.get("applied_to_invoice_amount") or 0) for row in rows)
        ),
        "supplier_credit_generated": money_round(
            sum(float(row.get("supplier_credit_amount") or 0) for row in rows)
        ),
    }


async def _credited_on_invoice_line(db, session, invoice_line_id: str) -> dict:
    rows = await db[CREDIT_COLLECTION].find(
        {"purchase_invoice_line_id": str(invoice_line_id), "status": STATUS_ACTIVE},
        {"_id": 0, "returned_qty": 1, "credit_amount": 1},
        session=session,
    ).to_list(2000)
    return {
        "qty": sum(float(row.get("returned_qty") or 0) for row in rows),
        "amount": money_round(sum(float(row.get("credit_amount") or 0) for row in rows)),
    }


async def _credited_on_return_line(db, session, return_line_id: str) -> float:
    rows = await db[CREDIT_COLLECTION].find(
        {"purchase_return_line_id": str(return_line_id), "status": STATUS_ACTIVE},
        {"_id": 0, "returned_qty": 1},
        session=session,
    ).to_list(200)
    return sum(float(row.get("returned_qty") or 0) for row in rows)


def _line_value(invoice_line: dict) -> Optional[float]:
    if invoice_line.get("line_subtotal") not in (None, ""):
        return money_round(invoice_line.get("line_subtotal"))
    if invoice_line.get("unit_price") not in (None, "") and invoice_line.get("invoice_qty") not in (None, ""):
        from services.purchase_common import line_subtotal
        return line_subtotal(invoice_line.get("invoice_qty"), invoice_line.get("unit_price"))
    return None


async def _invoice_outstanding(db, session, invoice: dict) -> float:
    from services.purchase_supplier_advance_allocations import active_applied_total

    paid_rows = await db.purchase_payments.find(
        {
            "purchase_invoice_id": invoice["id"],
            "cancelled": {"$ne": True},
            "settlement_status": "PAID",
        },
        {"_id": 0, "amount": 1},
        session=session,
    ).to_list(2000)
    paid = money_round(sum(float(row.get("amount") or 0) for row in paid_rows))
    applied = await active_applied_total(db, invoice["id"], session=session)
    credited = await active_return_credit_total(db, invoice["id"], session=session)
    return invoice_financial_summary(
        invoice_amount=invoice.get("grand_total"),
        advance_applied=applied,
        payments_applied=paid,
        purchase_return_credit=credited,
    )["outstanding_amount"]


async def _touch_invoice_credit_cache(db, session, invoice_id: str, delta: float) -> None:
    result = await db[INVOICE_COLLECTION].update_one(
        {"id": str(invoice_id), "cancelled": {"$ne": True}},
        {"$inc": {"purchase_return_credit_amount": money_round(delta)}, "$set": {"updated_at": now_iso()}},
        session=session,
    )
    if result.matched_count != 1:
        raise HTTPException(409, "Perubahan kredit retur bentrok dengan perubahan faktur lain.")


async def _load_invoice_line(db, session, invoice_line_id: str) -> tuple:
    invoices = await db[INVOICE_COLLECTION].find(
        {"lines.line_id": str(invoice_line_id), "cancelled": {"$ne": True}},
        {"_id": 0},
        session=session,
    ).to_list(5)
    if len(invoices) != 1:
        raise HTTPException(400, "Baris faktur kredit retur tidak dapat diidentifikasi.")
    invoice = invoices[0]
    match = None
    for line in invoice.get("lines") or []:
        if str(line.get("line_id") or "") == str(invoice_line_id):
            match = line
            break
    if match is None:
        raise HTTPException(400, "Baris faktur kredit retur tidak dapat diidentifikasi.")
    return invoice, match


async def post_return_credit(
    db,
    session,
    *,
    return_doc: dict,
    return_line: dict,
    invoice: dict,
    invoice_line: dict,
    qty: float,
    user: dict,
    idempotency_key: str,
) -> dict:
    if idempotency_key:
        existing = await db[CREDIT_COLLECTION].find_one(
            {"idempotency_key": idempotency_key},
            {"_id": 0},
            session=session,
        )
        if existing:
            if (
                str(existing.get("purchase_return_line_id") or "") != str(return_line.get("line_id") or "")
                or str(existing.get("purchase_invoice_line_id") or "") != str(invoice_line.get("line_id") or "")
                or float(existing.get("returned_qty") or 0) != float(qty)
            ):
                raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
            return existing
    if supplier_identity_key(return_doc.get("supplier")) != supplier_identity_key(invoice.get("supplier")):
        raise HTTPException(400, "Faktur bukan milik supplier retur yang sama.")
    if str(invoice.get("purchase_order_id") or "") != str(return_line.get("purchase_order_id") or return_doc.get("purchase_order_id") or ""):
        raise HTTPException(400, "Faktur tidak berasal dari pesanan retur yang sama.")
    if str(invoice_line.get("purchase_order_line_id") or "") != str(return_line.get("purchase_order_line_id") or ""):
        raise HTTPException(400, "Baris faktur tidak menelusuri baris pesanan retur yang sama.")
    value = _line_value(invoice_line)
    if value is None:
        raise HTTPException(400, "Nilai baris faktur tidak dapat dihitung secara pasti.")
    invoice_qty = float(invoice_line.get("invoice_qty") or invoice_line.get("quantity") or 0)
    already = await _credited_on_invoice_line(db, session, invoice_line.get("line_id"))
    allocated = await _credited_on_return_line(db, session, return_line.get("line_id"))
    if not qty_le(allocated + float(qty), float(return_line.get("return_qty") or 0)):
        raise HTTPException(400, "Qty kredit melebihi qty retur fisik.")
    amount = return_credit_amount(
        line_value=value,
        invoice_qty=invoice_qty,
        qty=qty,
        already_qty=already["qty"],
        already_amount=already["amount"],
    )
    outstanding = await _invoice_outstanding(db, session, invoice)
    applied = money_round(min(amount, outstanding))
    excess = money_round(amount - applied)
    credit = {
        "id": str(uuid.uuid4()),
        "purchase_return_id": return_doc["id"],
        "purchase_return_line_id": return_line["line_id"],
        "supplier": invoice.get("supplier") or return_doc.get("supplier") or "",
        "supplier_key": supplier_identity_key(invoice.get("supplier") or return_doc.get("supplier")),
        "purchase_order_id": return_doc.get("purchase_order_id") or "",
        "purchase_invoice_id": invoice["id"],
        "purchase_invoice_line_id": invoice_line.get("line_id"),
        "returned_qty": float(qty),
        "credit_amount": amount,
        "applied_to_invoice_amount": applied,
        "supplier_credit_amount": excess,
        "status": STATUS_ACTIVE,
        "idempotency_key": idempotency_key,
        **stamp_create(user),
    }
    await db[CREDIT_COLLECTION].insert_one(dict(credit), session=session)
    credit.pop("_id", None)
    supplier_credit = None
    if excess > 0:
        supplier_credit = {
            "id": str(uuid.uuid4()),
            "supplier": credit["supplier"],
            "supplier_key": credit["supplier_key"],
            "supplier_name": credit["supplier"],
            "source_purchase_return_id": return_doc["id"],
            "source_purchase_return_credit_id": credit["id"],
            "source_invoice_id": invoice["id"],
            "original_amount": excess,
            "available_amount": excess,
            "status": STATUS_ACTIVE,
            **stamp_create(user),
        }
        await db[SUPPLIER_CREDIT_COLLECTION].insert_one(dict(supplier_credit), session=session)
        supplier_credit.pop("_id", None)
        credit["supplier_credit_id"] = supplier_credit["id"]
        await db[CREDIT_COLLECTION].update_one(
            {"id": credit["id"]},
            {"$set": {"supplier_credit_id": supplier_credit["id"]}},
            session=session,
        )
    await _touch_invoice_credit_cache(db, session, invoice["id"], amount)
    credit["supplier_credit"] = supplier_credit
    return credit


async def financialize_unambiguous_lines(db, session, return_doc: dict, lines: List[dict], user: dict) -> List[dict]:
    """Auto-credit only a single related invoice line. Multiple invoices stay pending."""
    prepared = []
    for line in lines:
        row = dict(line)
        trace = row.get("invoice_trace") or {}
        related = list(trace.get("related_invoice_lines") or [])
        if trace.get("invoice_ownership_ambiguous") or len(related) > 1:
            row["financial_status"] = FINANCIAL_PENDING
            row["financial_allocated_qty"] = 0
            prepared.append(row)
            continue
        if len(related) != 1:
            row["financial_status"] = FINANCIAL_NONE
            row["financial_allocated_qty"] = 0
            prepared.append(row)
            continue
        ref = related[0]
        invoice, invoice_line = await _load_invoice_line(db, session, ref.get("purchase_invoice_line_id"))
        if _line_value(invoice_line) is None:
            row["financial_status"] = FINANCIAL_UNAVAILABLE
            row["financial_allocated_qty"] = 0
            prepared.append(row)
            continue
        already = await _credited_on_invoice_line(db, session, invoice_line.get("line_id"))
        invoice_qty = float(invoice_line.get("invoice_qty") or invoice_line.get("quantity") or 0)
        remaining = max(0.0, invoice_qty - float(already["qty"]))
        qty = min(float(row.get("return_qty") or 0), remaining)
        if qty <= 0:
            row["financial_status"] = FINANCIAL_NONE
            row["financial_allocated_qty"] = 0
            prepared.append(row)
            continue
        credit = await post_return_credit(
            db,
            session,
            return_doc=return_doc,
            return_line=row,
            invoice=invoice,
            invoice_line=invoice_line,
            qty=qty,
            user=user,
            idempotency_key=f"purchase-return-credit:{return_doc['id']}:{row['line_id']}",
        )
        row["financial_status"] = FINANCIAL_DONE
        row["financial_allocated_qty"] = qty
        row["purchase_return_credit_id"] = credit["id"]
        row["uninvoiced_return_qty"] = max(0.0, float(row.get("return_qty") or 0) - qty)
        prepared.append(row)
    return prepared


def _header_financial_status(lines: List[dict]) -> str:
    states = {str(line.get("financial_status") or "") for line in lines}
    if FINANCIAL_PENDING in states or FINANCIAL_UNAVAILABLE in states:
        return FINANCIAL_PENDING
    if states <= {FINANCIAL_NONE, ""}:
        return FINANCIAL_NONE
    return FINANCIAL_DONE


async def read_return_financial_state(db, return_id: str) -> dict:
    from services.purchase_returns import get_purchase_return

    document = await get_purchase_return(db, return_id)
    credits = await db[CREDIT_COLLECTION].find(
        {"purchase_return_id": document["id"]},
        {"_id": 0},
    ).sort("created_at", 1).to_list(200)
    supplier_credits = await db[SUPPLIER_CREDIT_COLLECTION].find(
        {"source_purchase_return_id": document["id"]},
        {"_id": 0},
    ).sort("created_at", 1).to_list(200)
    return {
        "return": document,
        "financial_status": document.get("financial_status") or _header_financial_status(document.get("lines") or []),
        "credits": credits,
        "supplier_credits": supplier_credits,
    }


async def allocate_ambiguous_return(db, return_id: str, payload: Dict[str, Any], user: dict) -> dict:
    from services.physical_stock import _client, require_transaction_support
    from services.purchase_returns import STATUS_CONFIRMED, get_purchase_return, is_cancelled

    document = await get_purchase_return(db, return_id)
    if document.get("status") != STATUS_CONFIRMED or is_cancelled(document):
        raise HTTPException(400, "Hanya retur terkonfirmasi yang dapat dialokasikan secara finansial.")
    allocations = payload.get("allocations") or payload.get("lines") or []
    if not allocations:
        raise HTTPException(400, "Minimal satu alokasi faktur wajib diisi.")
    idem = str(payload.get("idempotency_key") or "").strip()
    fingerprint = tuple(sorted(
        (
            str(row.get("purchase_return_line_id") or ""),
            str(row.get("purchase_invoice_line_id") or ""),
            float(row.get("qty") or row.get("qty_to_credit") or 0),
        )
        for row in allocations
    ))
    await require_transaction_support(_client(db))
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            if idem:
                existing = await db[CREDIT_COLLECTION].find(
                    {"allocation_group_key": idem},
                    {"_id": 0},
                    session=session,
                ).to_list(50)
                if existing:
                    stored = tuple(sorted(
                        (
                            str(row.get("purchase_return_line_id") or ""),
                            str(row.get("purchase_invoice_line_id") or ""),
                            float(row.get("returned_qty") or 0),
                        )
                        for row in existing
                    ))
                    if stored != fingerprint:
                        raise HTTPException(409, "Idempotency key sudah digunakan dengan payload yang berbeda")
                    return await read_return_financial_state(db, document["id"])
            current = await db.purchase_returns.find_one({"id": document["id"]}, {"_id": 0}, session=session)
            by_line = {str(line.get("line_id")): line for line in current.get("lines") or []}
            created = []
            for raw in allocations:
                line = by_line.get(str(raw.get("purchase_return_line_id") or ""))
                if not line:
                    raise HTTPException(400, "Baris retur alokasi tidak ditemukan.")
                if line.get("financial_status") != FINANCIAL_PENDING and not (line.get("invoice_trace") or {}).get("invoice_ownership_ambiguous"):
                    raise HTTPException(400, "Baris retur ini tidak menunggu alokasi faktur.")
                invoice, invoice_line = await _load_invoice_line(db, session, raw.get("purchase_invoice_line_id"))
                allowed = {
                    str(ref.get("purchase_invoice_line_id") or "")
                    for ref in (line.get("invoice_trace") or {}).get("related_invoice_lines") or []
                }
                if str(invoice_line.get("line_id") or "") not in allowed:
                    raise HTTPException(400, "Baris faktur tidak termasuk faktur yang terkait dengan retur ini.")
                qty = float(raw.get("qty") or raw.get("qty_to_credit") or 0)
                if qty <= 0:
                    raise HTTPException(400, "Qty alokasi harus lebih besar dari 0.")
                credit = await post_return_credit(
                    db,
                    session,
                    return_doc=current,
                    return_line=line,
                    invoice=invoice,
                    invoice_line=invoice_line,
                    qty=qty,
                    user=user,
                    idempotency_key=f"{idem}:{line['line_id']}:{invoice_line.get('line_id')}" if idem else f"purchase-return-credit:{uuid.uuid4()}",
                )
                if idem:
                    await db[CREDIT_COLLECTION].update_one(
                        {"id": credit["id"]},
                        {"$set": {"allocation_group_key": idem}},
                        session=session,
                    )
                created.append(credit)
                allocated = await _credited_on_return_line(db, session, line["line_id"])
                line["financial_allocated_qty"] = allocated
                line["financial_status"] = FINANCIAL_DONE if qty_le(float(line.get("return_qty") or 0), allocated) else FINANCIAL_PENDING
            lines = []
            for line in current.get("lines") or []:
                fresh = by_line.get(str(line.get("line_id")))
                lines.append(fresh or line)
            await db.purchase_returns.update_one(
                {"id": current["id"], "status": STATUS_CONFIRMED},
                {"$set": {
                    "lines": lines,
                    "financial_status": _header_financial_status(lines),
                    **stamp_update(user),
                }},
                session=session,
            )
    return await read_return_financial_state(db, document["id"])


async def reverse_return_financials(db, session, return_doc: dict, user: dict) -> None:
    credits = await db[CREDIT_COLLECTION].find(
        {"purchase_return_id": return_doc["id"], "status": STATUS_ACTIVE},
        {"_id": 0},
        session=session,
    ).to_list(200)
    for credit in credits:
        supplier = None
        if credit.get("supplier_credit_id") or float(credit.get("supplier_credit_amount") or 0) > 0:
            supplier = await db[SUPPLIER_CREDIT_COLLECTION].find_one(
                {"source_purchase_return_credit_id": credit["id"], "status": STATUS_ACTIVE},
                {"_id": 0},
                session=session,
            )
            if supplier and money_round(supplier.get("available_amount")) != money_round(supplier.get("original_amount")):
                raise HTTPException(409, "Kredit supplier sudah terpakai dan belum dapat dibatalkan.")
        await db[CREDIT_COLLECTION].update_one(
            {"id": credit["id"], "status": STATUS_ACTIVE},
            {"$set": {"status": STATUS_REVERSED, **stamp_update(user)}},
            session=session,
        )
        if supplier:
            await db[SUPPLIER_CREDIT_COLLECTION].update_one(
                {"id": supplier["id"], "status": STATUS_ACTIVE},
                {"$set": {"status": STATUS_REVERSED, "available_amount": 0, **stamp_update(user)}},
                session=session,
            )
        await _touch_invoice_credit_cache(db, session, credit["purchase_invoice_id"], -float(credit.get("credit_amount") or 0))


async def list_supplier_credits(db, supplier: Optional[str] = None) -> List[dict]:
    query: Dict[str, Any] = {}
    if supplier:
        query["supplier_key"] = supplier_identity_key(supplier)
    return await db[SUPPLIER_CREDIT_COLLECTION].find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)
