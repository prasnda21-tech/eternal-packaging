"""Allocate paid supplier advances to purchase invoices.

Allocation records are the source of truth. Cached allocated_amount and
advance_applied_amount are updated in the same Mongo transaction and must
match the active allocation sum. Allocation is not a cash payment.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError, PyMongoError

from services.accounts_payable import supplier_identity_key
from services.physical_stock import require_transaction_support
from services.purchase_common import (
    actor_snapshot,
    is_cancelled,
    money_round,
    now_iso,
    parse_positive_money,
    stamp_create,
)
from services.purchase_invoices import get_purchase_invoice

COLLECTION = "purchase_supplier_advance_allocations"
ADVANCE_COLLECTION = "purchase_supplier_advances"
INVOICE_COLLECTION = "purchase_invoices"
PAY_COLLECTION = "purchase_payments"

STATUS_ACTIVE = "ACTIVE"
STATUS_REVERSED = "REVERSED"


def _client(db):
    client = getattr(db, "client", None)
    if client is None:
        raise HTTPException(503, "MongoDB client is not available.")
    return client


def _retryable(exc: PyMongoError) -> bool:
    labels = getattr(exc, "has_error_label", None)
    if not labels:
        return False
    return labels("TransientTransactionError") or labels("UnknownTransactionCommitResult")


def _fingerprint(doc: dict) -> dict:
    return {
        "supplier_advance_id": str(doc.get("supplier_advance_id") or ""),
        "purchase_invoice_id": str(doc.get("purchase_invoice_id") or ""),
        "amount": money_round(doc.get("amount")),
        "notes": str(doc.get("notes") or ""),
    }


def enrich_allocation(doc: dict) -> dict:
    out = dict(doc)
    out.pop("_id", None)
    out["amount"] = money_round(out.get("amount"))
    out["status"] = str(out.get("status") or STATUS_ACTIVE)
    out["active"] = out["status"] == STATUS_ACTIVE
    return out


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index("supplier_advance_id")
    await db[COLLECTION].create_index("purchase_invoice_id")
    await db[COLLECTION].create_index("purchase_order_id")
    await db[COLLECTION].create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )


async def active_applied_total(db, invoice_id: str, session=None) -> float:
    rows = await db[COLLECTION].find(
        {"purchase_invoice_id": str(invoice_id), "status": STATUS_ACTIVE},
        {"_id": 0, "amount": 1},
        session=session,
    ).to_list(2000)
    return money_round(sum(float(row.get("amount") or 0) for row in rows))


async def _active_for_advance(db, advance_id: str, session=None) -> float:
    rows = await db[COLLECTION].find(
        {"supplier_advance_id": str(advance_id), "status": STATUS_ACTIVE},
        {"_id": 0, "amount": 1},
        session=session,
    ).to_list(2000)
    return money_round(sum(float(row.get("amount") or 0) for row in rows))


async def overlay_advance_allocations(db, docs: List[dict]) -> List[dict]:
    ids = [str(doc.get("id") or "") for doc in docs if doc.get("id")]
    sums: Dict[str, float] = {}
    if ids:
        rows = await db[COLLECTION].find(
            {"supplier_advance_id": {"$in": ids}, "status": STATUS_ACTIVE},
            {"_id": 0, "supplier_advance_id": 1, "amount": 1},
        ).to_list(5000)
        for row in rows:
            key = str(row.get("supplier_advance_id") or "")
            sums[key] = money_round(sums.get(key, 0.0) + float(row.get("amount") or 0))
    out = []
    for doc in docs:
        item = dict(doc)
        allocated = sums.get(str(item.get("id") or ""), 0.0)
        original = money_round(item.get("original_amount"))
        paid = str(item.get("settlement_status") or "").upper() == "PAID" and not item.get("cancelled")
        available = money_round(original - allocated) if paid else 0.0
        if available < 0:
            available = 0.0
        item["allocated_amount"] = allocated
        item["available_amount"] = available
        item["unapplied_amount"] = available
        out.append(item)
    return out


async def _paid_payments(db, invoice_id: str, session=None) -> float:
    rows = await db[PAY_COLLECTION].find(
        {
            "purchase_invoice_id": str(invoice_id),
            "cancelled": {"$ne": True},
            "settlement_status": "PAID",
        },
        {"_id": 0, "amount": 1},
        session=session,
    ).to_list(2000)
    return money_round(sum(float(row.get("amount") or 0) for row in rows))


def _same_or_conflict(existing: dict, fingerprint: dict) -> dict:
    if _fingerprint(existing) != fingerprint:
        raise HTTPException(409, "Alokasi dengan kunci yang sama memiliki data berbeda.")
    return enrich_allocation(existing)


async def _load(db, allocation_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(allocation_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Alokasi uang muka '{allocation_id}' tidak ditemukan.")
    return doc


async def list_allocations(
    db,
    *,
    purchase_invoice_id: Optional[str] = None,
    supplier_advance_id: Optional[str] = None,
) -> List[dict]:
    query: Dict[str, Any] = {}
    if purchase_invoice_id:
        query["purchase_invoice_id"] = str(purchase_invoice_id)
    if supplier_advance_id:
        query["supplier_advance_id"] = str(supplier_advance_id)
    rows = await db[COLLECTION].find(query, {"_id": 0}).sort("created_at", 1).to_list(2000)
    return [enrich_allocation(row) for row in rows]


async def _write_allocation(db, session, payload: dict, user: dict) -> dict:
    amount = parse_positive_money(payload.get("amount"), "Nilai alokasi")
    advance_id = str(payload.get("supplier_advance_id") or "").strip()
    invoice_id = str(payload.get("purchase_invoice_id") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    idem = str(payload.get("idempotency_key") or "").strip()
    if not advance_id or not invoice_id:
        raise HTTPException(400, "supplier_advance_id dan purchase_invoice_id wajib diisi.")
    fingerprint = {
        "supplier_advance_id": advance_id,
        "purchase_invoice_id": invoice_id,
        "amount": money_round(amount),
        "notes": notes,
    }
    if idem:
        existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0}, session=session)
        if existing:
            return _same_or_conflict(existing, fingerprint)

    advance = await db[ADVANCE_COLLECTION].find_one({"id": advance_id}, {"_id": 0}, session=session)
    if not advance:
        raise HTTPException(404, "Uang muka tidak ditemukan.")
    if is_cancelled(advance):
        raise HTTPException(400, "Uang muka yang dibatalkan tidak dapat dialokasikan.")
    if str(advance.get("settlement_status") or "").upper() != "PAID":
        raise HTTPException(400, "Hanya uang muka yang sudah dibayar yang dapat dialokasikan.")

    invoice = await db[INVOICE_COLLECTION].find_one({"id": invoice_id}, {"_id": 0}, session=session)
    if not invoice:
        raise HTTPException(404, "Faktur Pembelian tidak ditemukan.")
    if is_cancelled(invoice):
        raise HTTPException(400, "Faktur yang dibatalkan tidak dapat menerima alokasi uang muka.")
    if supplier_identity_key(advance.get("supplier") or advance.get("supplier_name")) != supplier_identity_key(invoice.get("supplier")):
        raise HTTPException(400, "Uang muka tidak dapat dialokasikan ke faktur supplier lain.")
    if str(advance.get("purchase_order_id") or "") != str(invoice.get("purchase_order_id") or ""):
        raise HTTPException(400, "Uang muka hanya dapat dialokasikan ke faktur pada pesanan yang sama.")

    used = await _active_for_advance(db, advance_id, session=session)
    cached_used = money_round(advance.get("allocated_amount") or 0)
    if cached_used != used:
        raise HTTPException(409, "Saldo alokasi uang muka tidak rekonsiliasi.")
    available = money_round(money_round(advance.get("original_amount")) - used)
    if money_round(amount) > available:
        raise HTTPException(409, "Nilai alokasi melebihi uang muka yang tersedia.")

    paid = await _paid_payments(db, invoice_id, session=session)
    applied = await active_applied_total(db, invoice_id, session=session)
    from services.purchase_return_finance import active_return_credit_total
    from services.purchase_invoice_finance import invoice_financial_summary
    credited = await active_return_credit_total(db, invoice_id, session=session)
    cached_applied = money_round(invoice.get("advance_applied_amount") or 0)
    if cached_applied != applied:
        raise HTTPException(409, "Alokasi faktur tidak rekonsiliasi.")
    outstanding = invoice_financial_summary(
        invoice_amount=invoice.get("grand_total"),
        advance_applied=applied,
        payments_applied=paid,
        purchase_return_credit=credited,
    )["outstanding_amount"]
    if money_round(amount) > outstanding:
        raise HTTPException(409, "Nilai alokasi melebihi sisa tagihan.")

    new_used = money_round(used + amount)
    new_applied = money_round(applied + amount)
    adv_filter: Dict[str, Any] = {"id": advance_id, "settlement_status": "PAID", "allocated_amount": advance.get("allocated_amount", 0)}
    adv_result = await db[ADVANCE_COLLECTION].update_one(
        adv_filter,
        {"$set": {"allocated_amount": new_used, "updated_at": now_iso()}},
        session=session,
    )
    if adv_result.modified_count != 1:
        raise HTTPException(409, "Alokasi bentrok dengan perubahan uang muka lain.")
    inv_filter: Dict[str, Any] = {"id": invoice_id, "cancelled": {"$ne": True}}
    if "advance_applied_amount" in invoice:
        inv_filter["advance_applied_amount"] = invoice.get("advance_applied_amount")
    else:
        inv_filter["advance_applied_amount"] = {"$exists": False}
    inv_result = await db[INVOICE_COLLECTION].update_one(
        inv_filter,
        {"$set": {"advance_applied_amount": new_applied, "updated_at": now_iso()}},
        session=session,
    )
    if inv_result.modified_count != 1:
        raise HTTPException(409, "Alokasi bentrok dengan perubahan faktur lain.")

    doc = {
        "id": str(uuid.uuid4()),
        "supplier_advance_id": advance_id,
        "advance_number": advance.get("advance_number") or "",
        "purchase_invoice_id": invoice_id,
        "invoice_number": invoice.get("invoice_number") or "",
        "purchase_order_id": invoice.get("purchase_order_id") or "",
        "po_number": invoice.get("po_number") or "",
        "supplier_key": advance.get("supplier_key") or supplier_identity_key(advance.get("supplier")),
        "supplier_name": advance.get("supplier_name") or advance.get("supplier") or "",
        "amount": money_round(amount),
        "status": STATUS_ACTIVE,
        "notes": notes,
        **stamp_create(user),
    }
    if idem:
        doc["idempotency_key"] = idem
    await db[COLLECTION].insert_one(doc, session=session)
    doc.pop("_id", None)
    return enrich_allocation(doc)


async def create_allocation(db, payload: Dict[str, Any], user: dict) -> dict:
    await require_transaction_support(_client(db))
    last_error: Optional[BaseException] = None
    for _attempt in range(5):
        try:
            async with await _client(db).start_session() as session:
                async with session.start_transaction():
                    return await _write_allocation(db, session, payload, user)
        except HTTPException:
            raise
        except DuplicateKeyError:
            idem = str((payload or {}).get("idempotency_key") or "").strip()
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0}) if idem else None
            if existing:
                return _same_or_conflict(existing, {
                    "supplier_advance_id": str(payload.get("supplier_advance_id") or ""),
                    "purchase_invoice_id": str(payload.get("purchase_invoice_id") or ""),
                    "amount": money_round(parse_positive_money(payload.get("amount"), "Nilai alokasi")),
                    "notes": str(payload.get("notes") or ""),
                })
            raise HTTPException(409, "Alokasi duplikat terdeteksi.")
        except PyMongoError as exc:
            last_error = exc
            if not _retryable(exc):
                raise
    raise HTTPException(409, "Alokasi gagal karena perubahan bersamaan.") from last_error


async def reverse_allocation(db, allocation_id: str, user: dict) -> dict:
    """Mark one active allocation reversed. The original row stays."""
    await require_transaction_support(_client(db))
    last_error: Optional[BaseException] = None
    for _attempt in range(5):
        try:
            async with await _client(db).start_session() as session:
                async with session.start_transaction():
                    current = await db[COLLECTION].find_one({"id": str(allocation_id)}, {"_id": 0}, session=session)
                    if not current:
                        raise HTTPException(404, f"Alokasi uang muka '{allocation_id}' tidak ditemukan.")
                    if current.get("status") == STATUS_REVERSED:
                        return enrich_allocation(current)
                    amount = money_round(current.get("amount"))
                    advance = await db[ADVANCE_COLLECTION].find_one(
                        {"id": current["supplier_advance_id"]}, {"_id": 0}, session=session,
                    )
                    invoice = await db[INVOICE_COLLECTION].find_one(
                        {"id": current["purchase_invoice_id"]}, {"_id": 0}, session=session,
                    )
                    if not advance or not invoice:
                        raise HTTPException(409, "Alokasi tidak dapat dibatalkan karena dokumen sumber berubah.")
                    used = await _active_for_advance(db, advance["id"], session=session)
                    applied = await active_applied_total(db, invoice["id"], session=session)
                    if money_round(advance.get("allocated_amount") or 0) != used or money_round(invoice.get("advance_applied_amount") or 0) != applied:
                        raise HTTPException(409, "Saldo alokasi tidak rekonsiliasi.")
                    actor = actor_snapshot(user)
                    updated = await db[COLLECTION].update_one(
                        {"id": current["id"], "status": STATUS_ACTIVE},
                        {"$set": {
                            "status": STATUS_REVERSED,
                            "reversed_at": now_iso(),
                            "reversed_by": actor,
                        }},
                        session=session,
                    )
                    if updated.modified_count != 1:
                        raced = await db[COLLECTION].find_one({"id": current["id"]}, {"_id": 0}, session=session)
                        if raced and raced.get("status") == STATUS_REVERSED:
                            return enrich_allocation(raced)
                        raise HTTPException(409, "Pembatalan alokasi bentrok.")
                    adv_result = await db[ADVANCE_COLLECTION].update_one(
                        {"id": advance["id"], "allocated_amount": advance.get("allocated_amount", 0)},
                        {"$set": {"allocated_amount": money_round(used - amount), "updated_at": now_iso()}},
                        session=session,
                    )
                    inv_result = await db[INVOICE_COLLECTION].update_one(
                        {"id": invoice["id"], "advance_applied_amount": invoice.get("advance_applied_amount", 0)},
                        {"$set": {"advance_applied_amount": money_round(applied - amount), "updated_at": now_iso()}},
                        session=session,
                    )
                    if adv_result.modified_count != 1 or inv_result.modified_count != 1:
                        raise HTTPException(409, "Pembatalan alokasi bentrok dengan saldo.")
                    refreshed = await db[COLLECTION].find_one({"id": current["id"]}, {"_id": 0}, session=session)
                    return enrich_allocation(refreshed)
        except HTTPException:
            raise
        except PyMongoError as exc:
            last_error = exc
            if not _retryable(exc):
                raise
    raise HTTPException(409, "Pembatalan alokasi gagal karena perubahan bersamaan.") from last_error


async def allocation_invoice_view(db, invoice_id: str) -> dict:
    """Read model used by tests. Financial fields come from the invoice helper."""
    invoice = await get_purchase_invoice(db, invoice_id)
    rows = await list_allocations(db, purchase_invoice_id=invoice["id"])
    return {"invoice": invoice, "allocations": rows}
