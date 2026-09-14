"""Purchase Payment (Pembayaran Pembelian) against exact Faktur Pembelian.

Supports DP, partial, multiple payments, and final settlement. Overpayment rejected.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.purchase_common import (
    PREFIX_PURCHASE_PAYMENT,
    actor_snapshot,
    derive_child_number,
    get_named_lock,
    insert_with_unique_number,
    money_round,
    now_iso,
    parse_positive_money,
)
from services.purchase_invoices import get_purchase_invoice, payment_status

COLLECTION = "purchase_payments"


async def create_purchase_payment(db, payload: Dict[str, Any], user: dict) -> dict:
    invoice_id = str(payload.get("purchase_invoice_id") or payload.get("invoice_id") or "").strip()
    if not invoice_id:
        raise HTTPException(400, "ID Faktur Pembelian (purchase_invoice_id) wajib diisi.")

    async with get_named_lock("purchase_payment"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                return existing

        invoice = await get_purchase_invoice(db, invoice_id)
        outstanding = float(invoice.get("outstanding_amount") or 0)
        amount = parse_positive_money(payload.get("amount"), "Jumlah pembayaran")
        if money_round(amount) > money_round(outstanding):
            raise HTTPException(
                400,
                f"Jumlah pembayaran (Rp {amount:,.2f}) melebihi sisa tagihan (Rp {outstanding:,.2f}).",
            )

        new_paid = money_round(float(invoice.get("paid_total") or 0) + amount)
        new_out = max(money_round(float(invoice.get("grand_total") or 0) - new_paid), 0.0)
        state = payment_status(float(invoice.get("grand_total") or 0), new_paid)

        ts = now_iso()
        existing_count = await db[COLLECTION].count_documents({"purchase_order_id": invoice["purchase_order_id"]})
        doc = {
            "id": str(uuid.uuid4()),
            "purchase_invoice_id": invoice["id"],
            "invoice_number": invoice.get("invoice_number"),
            "purchase_order_id": invoice["purchase_order_id"],
            "po_number": invoice.get("po_number"),
            "supplier": invoice.get("supplier"),
            "invoice_grand_total": invoice.get("grand_total"),
            "paid_total_before": invoice.get("paid_total"),
            "outstanding_before": outstanding,
            "amount": money_round(amount),
            "paid_total_after": new_paid,
            "outstanding_after": new_out,
            "payment_state": state,
            "payment_date": str(payload.get("payment_date") or payload.get("receipt_date") or ts[:10]).strip()[:10],
            "notes": str(payload.get("notes") or "").strip(),
            "created_at": ts,
            "updated_at": ts,
            "created_by": actor_snapshot(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        async def _alloc():
            idx = existing_count + 1
            candidate = derive_child_number(invoice["po_number"], PREFIX_PURCHASE_PAYMENT, idx)
            while await db[COLLECTION].find_one({"payment_number": candidate}, {"_id": 1}):
                idx += 1
                candidate = derive_child_number(invoice["po_number"], PREFIX_PURCHASE_PAYMENT, idx)
            return candidate

        return await insert_with_unique_number(db, COLLECTION, "payment_number", doc, _alloc)


async def list_purchase_payments(
    db,
    q: Optional[str] = None,
    purchase_invoice_id: Optional[str] = None,
    purchase_order_id: Optional[str] = None,
) -> List[dict]:
    filters: Dict[str, Any] = {}
    if purchase_invoice_id:
        filters["purchase_invoice_id"] = str(purchase_invoice_id)
    if purchase_order_id:
        filters["purchase_order_id"] = str(purchase_order_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"payment_number": {"$regex": needle, "$options": "i"}},
            {"invoice_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"supplier": {"$regex": needle, "$options": "i"}},
        ]
    return await db[COLLECTION].find(filters, {"_id": 0}).sort("created_at", -1).to_list(1000)


async def get_purchase_payment(db, payment_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(payment_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"payment_number": str(payment_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Pembayaran Pembelian '{payment_id}' tidak ditemukan.")
    return doc
