"""Supplier advance (Uang Muka Pembelian) against a Purchase Order.

This is not a purchase_payments row and it does not mean invoice status DP.
Only a confirmed PAID advance contributes to available/unapplied supplier advance.
Invoice allocation is a later phase: allocated_amount stays 0 and is not editable here.
No journal is posted. Purchase-payment accounting debits invoice AP, which does not exist yet.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.accounts_payable import supplier_identity_key
from services.purchase_common import (
    PREFIX_SUPPLIER_ADVANCE,
    cancellation_fields,
    get_named_lock,
    insert_with_unique_number,
    is_cancelled,
    money_round,
    next_dated_seq,
    now_iso,
    parse_positive_money,
    stamp_create,
    stamp_update,
    actor_snapshot,
)
from services.purchase_orders import get_purchase_order

COLLECTION = "purchase_supplier_advances"

SUBMISSION_NOT_SUBMITTED = "NOT_SUBMITTED"
SUBMISSION_SUBMITTED = "SUBMITTED"
SETTLEMENT_UNPAID = "UNPAID"
SETTLEMENT_PAID = "PAID"

STATUS_BELUM_DIAJUKAN = "BELUM DIAJUKAN"
STATUS_DIAJUKAN = "DIAJUKAN"
STATUS_DIBAYAR = "DIBAYAR"

PAID_CANCEL_BLOCK = (
    "Uang muka yang sudah dibayar tidak dapat dibatalkan. "
    "Reversal keuangan untuk uang muka belum tersedia."
)


def _workflow(doc: dict) -> str:
    if str(doc.get("settlement_status") or "").upper() == SETTLEMENT_PAID:
        return STATUS_DIBAYAR
    if str(doc.get("submission_status") or "").upper() == SUBMISSION_SUBMITTED:
        return STATUS_DIAJUKAN
    return STATUS_BELUM_DIAJUKAN


def _status_label(doc: dict) -> str:
    if is_cancelled(doc):
        return "Dibatalkan"
    workflow = _workflow(doc)
    if workflow == STATUS_DIBAYAR:
        return "Dibayar"
    if workflow == STATUS_DIAJUKAN:
        return "Diajukan"
    return "Draft"


def _amounts(doc: dict) -> dict:
    original = money_round(doc.get("original_amount"))
    allocated = money_round(doc.get("allocated_amount") or 0)
    paid = str(doc.get("settlement_status") or "").upper() == SETTLEMENT_PAID and not is_cancelled(doc)
    available = money_round(original - allocated) if paid else 0.0
    if available < 0:
        available = 0.0
    return {
        "original_amount": original,
        "allocated_amount": allocated,
        "available_amount": available,
        "unapplied_amount": available,
    }


def enrich_supplier_advance(doc: dict) -> dict:
    out = dict(doc)
    out.pop("_id", None)
    cancelled = is_cancelled(out)
    out["cancelled"] = cancelled
    workflow = _workflow(out)
    out["status"] = workflow
    out["workflow_status"] = workflow
    out["status_label"] = _status_label(out)
    out.update(_amounts(out))
    paid = workflow == STATUS_DIBAYAR
    submitted = str(out.get("submission_status") or "").upper() == SUBMISSION_SUBMITTED
    out["can_submit"] = (not cancelled) and (not paid) and (not submitted)
    out["can_confirm_paid"] = (not cancelled) and (not paid) and submitted
    if cancelled:
        out["can_cancel"] = False
        out["cancel_block_reason"] = "Uang muka sudah dibatalkan."
    elif paid:
        out["can_cancel"] = False
        out["cancel_block_reason"] = PAID_CANCEL_BLOCK
    else:
        out["can_cancel"] = True
        out["cancel_block_reason"] = None
    return out


def _create_fingerprint(doc: dict) -> dict:
    return {
        "purchase_order_id": str(doc.get("purchase_order_id") or ""),
        "original_amount": money_round(doc.get("original_amount")),
        "payment_date": str(doc.get("payment_date") or ""),
        "cash_bank_id": str(doc.get("cash_bank_id") or ""),
        "notes": str(doc.get("notes") or ""),
    }


def _assert_same_create(existing: dict, fingerprint: dict) -> None:
    if _create_fingerprint(existing) != fingerprint:
        raise HTTPException(409, "Permintaan uang muka dengan kunci yang sama memiliki data berbeda.")


async def _load(db, advance_id: str) -> dict:
    key = str(advance_id or "").strip()
    doc = await db[COLLECTION].find_one({"id": key}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"advance_number": key}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Uang muka '{advance_id}' tidak ditemukan.")
    return doc


async def _require_open_po(db, po_id: str) -> dict:
    try:
        po = await get_purchase_order(db, po_id)
    except HTTPException:
        raise
    if is_cancelled(po):
        raise HTTPException(400, "Pesanan sudah dibatalkan; uang muka tidak dapat dibuat.")
    supplier_name = str(po.get("supplier") or "").strip()
    if not supplier_name:
        raise HTTPException(400, "Pesanan tidak memiliki supplier.")
    return po


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index("advance_number", unique=True)
    await db[COLLECTION].create_index("purchase_order_id")
    await db[COLLECTION].create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="idempotency_key_partial_unique",
    )
    await db[COLLECTION].create_index(
        "confirmation_key",
        unique=True,
        partialFilterExpression={"confirmation_key": {"$type": "string"}},
        name="confirmation_key_partial_unique",
    )


async def create_purchase_supplier_advance(db, payload: Dict[str, Any], user: dict) -> dict:
    po_id = str(payload.get("purchase_order_id") or "").strip()
    if not po_id:
        raise HTTPException(400, "ID Pesanan Pembelian (purchase_order_id) wajib diisi.")
    amount = parse_positive_money(payload.get("amount") or payload.get("original_amount"), "Nilai uang muka")
    # No cap against PO total: a later invoice may be smaller and the remainder stays unapplied.

    async with get_named_lock("purchase_supplier_advance"):
        idem = str(payload.get("idempotency_key") or "").strip()
        po = await _require_open_po(db, po_id)
        incoming_supplier = str(payload.get("supplier") or payload.get("supplier_name") or "").strip()
        if incoming_supplier and supplier_identity_key(incoming_supplier) != supplier_identity_key(po.get("supplier")):
            raise HTTPException(400, "Supplier uang muka harus sama dengan supplier pesanan.")
        ts = now_iso()
        payment_date = str(payload.get("payment_date") or ts[:10]).strip()[:10]
        cash_bank_id = str(payload.get("cash_bank_id") or "").strip()
        notes = str(payload.get("notes") or "").strip()
        fingerprint = {
            "purchase_order_id": po["id"],
            "original_amount": money_round(amount),
            "payment_date": payment_date,
            "cash_bank_id": cash_bank_id,
            "notes": notes,
        }
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                _assert_same_create(existing, fingerprint)
                return enrich_supplier_advance(existing)

        doc = {
            "id": str(uuid.uuid4()),
            "purchase_order_id": po["id"],
            "po_number": po.get("po_number") or "",
            "supplier": str(po.get("supplier") or "").strip(),
            "supplier_name": str(po.get("supplier") or "").strip(),
            "supplier_key": supplier_identity_key(po.get("supplier")),
            "original_amount": money_round(amount),
            "allocated_amount": 0.0,
            "payment_date": payment_date,
            "cash_bank_id": cash_bank_id,
            "notes": notes,
            "submission_status": SUBMISSION_NOT_SUBMITTED,
            "settlement_status": SETTLEMENT_UNPAID,
            "status": STATUS_BELUM_DIAJUKAN,
            **stamp_create(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        async def _alloc():
            return await next_dated_seq(db, COLLECTION, "advance_number", PREFIX_SUPPLIER_ADVANCE)

        try:
            saved = await insert_with_unique_number(db, COLLECTION, "advance_number", doc, _alloc)
        except HTTPException as exc:
            if idem and exc.status_code == 409:
                existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
                if existing:
                    _assert_same_create(existing, fingerprint)
                    return enrich_supplier_advance(existing)
            raise
        if idem and saved.get("id") != doc["id"]:
            _assert_same_create(saved, fingerprint)
        return enrich_supplier_advance(saved)


async def _with_allocation_truth(db, rows: List[dict]) -> List[dict]:
    from services.purchase_supplier_advance_allocations import overlay_advance_allocations

    return await overlay_advance_allocations(db, rows)


async def list_purchase_supplier_advances(db, purchase_order_id: Optional[str] = None) -> List[dict]:
    query: Dict[str, Any] = {}
    if purchase_order_id:
        query["purchase_order_id"] = str(purchase_order_id)
    rows = await db[COLLECTION].find(query, {"_id": 0}).sort("created_at", 1).to_list(1000)
    return await _with_allocation_truth(db, [enrich_supplier_advance(row) for row in rows])


async def get_purchase_supplier_advance(db, advance_id: str) -> dict:
    return (await _with_allocation_truth(db, [enrich_supplier_advance(await _load(db, advance_id))]))[0]


async def supplier_advance_availability(db, purchase_order_id: str) -> dict:
    po_id = str(purchase_order_id or "").strip()
    if not po_id:
        raise HTTPException(400, "purchase_order_id wajib diisi.")
    rows = await db[COLLECTION].find(
        {
            "purchase_order_id": po_id,
            "settlement_status": SETTLEMENT_PAID,
            "cancelled": {"$ne": True},
        },
        {"_id": 0},
    ).to_list(1000)
    enriched = await _with_allocation_truth(db, [enrich_supplier_advance(row) for row in rows])
    paid = sum(money_round(row.get("original_amount")) for row in enriched)
    allocated = sum(money_round(row.get("allocated_amount") or 0) for row in enriched)
    available = money_round(paid - allocated)
    return {
        "purchase_order_id": po_id,
        "supplier_key": (rows[0].get("supplier_key") if rows else "") or "",
        "supplier_name": (rows[0].get("supplier_name") if rows else "") or "",
        "advance_count": len(rows),
        "total_paid_amount": money_round(paid),
        "allocated_amount": money_round(allocated),
        "available_amount": available,
    }


async def submit_purchase_supplier_advance(db, advance_id: str, user: dict) -> dict:
    async with get_named_lock("purchase_supplier_advance"):
        doc = await _load(db, advance_id)
        if is_cancelled(doc):
            raise HTTPException(400, "Uang muka sudah dibatalkan.")
        if str(doc.get("settlement_status") or "").upper() == SETTLEMENT_PAID:
            raise HTTPException(400, "Uang muka sudah dibayar.")
        if str(doc.get("submission_status") or "").upper() == SUBMISSION_SUBMITTED:
            return enrich_supplier_advance(doc)
        await _require_open_po(db, doc["purchase_order_id"])
        await db[COLLECTION].update_one(
            {"id": doc["id"], "settlement_status": SETTLEMENT_UNPAID, "cancelled": {"$ne": True}},
            {"$set": {
                "submission_status": SUBMISSION_SUBMITTED,
                "status": STATUS_DIAJUKAN,
                **stamp_update(user),
            }},
        )
        return enrich_supplier_advance(await _load(db, doc["id"]))


def _confirmation_view(doc: dict) -> dict:
    return {
        "confirmation_key": str(doc.get("confirmation_key") or ""),
        "payment_date": str(doc.get("confirmation_payment_date") or doc.get("payment_date") or ""),
        "cash_bank_id": str(doc.get("confirmation_cash_bank_id") or doc.get("cash_bank_id") or ""),
    }


async def confirm_purchase_supplier_advance(
    db,
    advance_id: str,
    user: dict,
    payload: Optional[Dict[str, Any]] = None,
) -> dict:
    """Mark one advance PAID. Available balance is derived from PAID rows, not a second counter."""
    payload = payload or {}
    doc = await _load(db, advance_id)
    confirmation_key = str(payload.get("confirmation_key") or payload.get("idempotency_key") or "").strip()
    if not confirmation_key:
        confirmation_key = f"confirm:{doc['id']}"
    payment_date = str(payload.get("payment_date") or doc.get("payment_date") or now_iso()[:10]).strip()[:10]
    cash_bank_id = str(payload.get("cash_bank_id") if payload.get("cash_bank_id") is not None else doc.get("cash_bank_id") or "").strip()
    requested = {
        "confirmation_key": confirmation_key,
        "payment_date": payment_date,
        "cash_bank_id": cash_bank_id,
    }

    if str(doc.get("settlement_status") or "").upper() == SETTLEMENT_PAID:
        if _confirmation_view(doc) != requested:
            raise HTTPException(409, "Konfirmasi uang muka dengan identitas yang sama memiliki data berbeda.")
        return enrich_supplier_advance(doc)
    if is_cancelled(doc):
        raise HTTPException(400, "Uang muka sudah dibatalkan.")
    if str(doc.get("submission_status") or "").upper() != SUBMISSION_SUBMITTED:
        raise HTTPException(400, "Uang muka harus diajukan sebelum dikonfirmasi dibayar.")
    await _require_open_po(db, doc["purchase_order_id"])

    actor = actor_snapshot(user)
    ts = now_iso()
    fields = {
        "settlement_status": SETTLEMENT_PAID,
        "status": STATUS_DIBAYAR,
        "paid_at": ts,
        "paid_by": actor,
        "payment_date": payment_date,
        "cash_bank_id": cash_bank_id,
        "confirmation_key": confirmation_key,
        "confirmation_payment_date": payment_date,
        "confirmation_cash_bank_id": cash_bank_id,
        **stamp_update(user),
    }
    try:
        result = await db[COLLECTION].update_one(
            {
                "id": doc["id"],
                "settlement_status": SETTLEMENT_UNPAID,
                "submission_status": SUBMISSION_SUBMITTED,
                "cancelled": {"$ne": True},
            },
            {"$set": fields},
        )
    except DuplicateKeyError:
        current = await db[COLLECTION].find_one({"confirmation_key": confirmation_key}, {"_id": 0})
        if current and current.get("id") == doc["id"] and _confirmation_view(current) == requested:
            return enrich_supplier_advance(current)
        raise HTTPException(409, "Konfirmasi uang muka bentrok dengan dokumen lain.")

    current = await _load(db, doc["id"])
    if result.modified_count == 0:
        if str(current.get("settlement_status") or "").upper() == SETTLEMENT_PAID and _confirmation_view(current) == requested:
            return enrich_supplier_advance(current)
        if str(current.get("settlement_status") or "").upper() == SETTLEMENT_PAID:
            raise HTTPException(409, "Konfirmasi uang muka dengan identitas yang sama memiliki data berbeda.")
        raise HTTPException(409, "Uang muka tidak dapat dikonfirmasi.")
    return enrich_supplier_advance(current)


async def cancel_purchase_supplier_advance(db, advance_id: str, user: dict, reason: str = "") -> dict:
    doc = await _load(db, advance_id)
    if is_cancelled(doc):
        return enrich_supplier_advance(doc)
    if str(doc.get("settlement_status") or "").upper() == SETTLEMENT_PAID:
        raise HTTPException(409, PAID_CANCEL_BLOCK)
    await db[COLLECTION].update_one(
        {"id": doc["id"], "settlement_status": {"$ne": SETTLEMENT_PAID}, "cancelled": {"$ne": True}},
        {"$set": {
            **cancellation_fields(user, reason),
            "status": "DIBATALKAN",
        }},
    )
    return enrich_supplier_advance(await _load(db, doc["id"]))
