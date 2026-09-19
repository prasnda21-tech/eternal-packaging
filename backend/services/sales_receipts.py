"""Sales Receipt (Penerimaan Penjualan) Service & Workflow Payment Integration.

Manages backend-authoritative sales receipts linked to exact Sales Invoices (Faktur Penjualan),
enforces overpayment protection, computes cumulative invoice payment totals, derives
DP / LUNAS status, and performs automated Payment Stage & Approval Payment resolution
in the production workflow.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from workflow_gates import sync_workflow_stage, design_ok, payment_ok
from services.sales_receipt_payment_proof import (
    parse_proof_payload,
    proof_from_receipt,
    public_proof,
    soft_delete_proof_for_receipt,
    store_payment_proof,
)
from services.sales_receipt_posting import (
    CLAIM_FIELD,
    DELETE_CLAIM_FIELD,
    POSTED_DELETE_MESSAGE,
    POSTING_IN_PROGRESS_DELETE_MESSAGE,
    REVERSED_DELETE_MESSAGE,
    acquire_delete_claim,
    attach_receipt_accounting_fields,
    find_sales_receipt_source_journal,
    posting_claim_is_active,
    receipt_counts_toward_outstanding,
    receipt_is_cancelled,
    release_delete_claims,
    resolve_cash_bank_for_create,
)

_receipt_locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _get_receipt_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _receipt_locks:
        _receipt_locks[loop] = asyncio.Lock()
    return _receipt_locks[loop]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_base_receipt_number(po_number: str) -> str:
    """Derives base receipt number from PO number while preserving main sequence."""
    pn = (po_number or "").strip()
    if not pn:
        return "PAY/" + datetime.now(timezone.utc).strftime("%d/%m/%y/001")

    # 1. Match PO/04/09/26/001 or SO/04/09/26/001 -> PAY/04/09/26/001
    # 2. Match PO/2026/08/30/000123 or SO/2026/08/30/000123 -> PAY/2026/08/30/000123
    # 3. Match PO-20260830-001 or SO-20260830-001 -> PAY-20260830-001
    # 4. Match PO.2026.08.001 -> PAY.2026.08.001
    # 5. Match PO 12345 -> PAY 12345
    # 6. Match PO12345 -> PAY12345
    m = re.match(r'^(?:PO|SO)([\/\.\-\s_]?)(.*)$', pn, re.IGNORECASE)
    if m:
        sep = m.group(1)
        rest = m.group(2)
        return f"PAY{sep}{rest}"

    # Other alphabetic prefixes with separator
    m2 = re.match(r'^[A-Za-z]+([\/\.\-\s_])(.*)$', pn)
    if m2:
        sep = m2.group(1)
        rest = m2.group(2)
        return f"PAY{sep}{rest}"

    return f"PAY-{pn}"


def derive_receipt_number(po_number: str, receipt_index: int) -> str:
    """Computes backend-authoritative receipt number for a specific receipt index.

    Index 1: base PAY (e.g. PAY/04/09/26/001 or PAY-20260830-001)
    Index 2: base PAY + "_2" (e.g. PAY/04/09/26/001_2)
    Index 3: base PAY + "_3" (e.g. PAY/04/09/26/001_3)
    """
    base = derive_base_receipt_number(po_number)
    if receipt_index <= 1:
        return base
    return f"{base}_{receipt_index}"


def _parse_non_negative_amount(raw: Any, field_name: str = "Jumlah pembayaran") -> float:
    if raw is None or raw == "":
        raise HTTPException(400, f"{field_name} wajib diisi.")
    try:
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field_name} '{raw}' tidak valid. Format harus angka.")

    if d <= Decimal("0"):
        raise HTTPException(400, f"{field_name} harus lebih besar dari 0.")

    return float(d)


async def list_sales_receipts(
    db,
    q: Optional[str] = None,
    invoice_id: Optional[str] = None,
    po_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List sales receipts with search and relation filters."""
    filters: Dict[str, Any] = {}
    if invoice_id:
        filters["invoice_id"] = str(invoice_id)
    if po_id:
        filters["po_id"] = str(po_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"id": {"$regex": needle, "$options": "i"}},
            {"receipt_number": {"$regex": needle, "$options": "i"}},
            {"invoice_id": {"$regex": needle, "$options": "i"}},
            {"invoice_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"customer": {"$regex": needle, "$options": "i"}},
        ]

    cursor = db.sales_receipts.find(filters, {"_id": 0}).sort("created_at", -1)
    receipts = await cursor.to_list(1000)
    return await attach_receipt_accounting_fields(db, [_public_receipt(r) for r in receipts])


def _public_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    """Strip any non-public proof fields from a receipt document."""
    if not isinstance(receipt, dict):
        return receipt
    out = dict(receipt)
    proof = proof_from_receipt(out)
    if proof:
        out["payment_proof"] = proof
        out["has_payment_proof"] = True
    else:
        out.pop("payment_proof", None)
        out["has_payment_proof"] = False
    return out


async def get_sales_receipt_detail(db, receipt_id: str) -> Dict[str, Any]:
    """Fetch detail of a single sales receipt."""
    r = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not r:
        r = await db.sales_receipts.find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not r:
        raise HTTPException(404, f"Penerimaan Penjualan '{receipt_id}' tidak ditemukan.")
    attached = await attach_receipt_accounting_fields(db, [_public_receipt(r)])
    return attached[0]


async def create_sales_receipt(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Creates a new Sales Receipt (Penerimaan Penjualan) backend-authoritatively.

    Guarantees:
    - Linked to exact invoice_id (preserving receipt -> invoice_id -> po_id relation)
    - Validates amount > 0 and amount <= outstanding_amount (overpayment protection)
    - Computes cumulative paid amount and derives payment state (DP / LUNAS)
    - Idempotency & double-submit protection
    - Automated workflow resolution for Payment & Approval Payment stage on PO:
      - If PO prerequisite stages (Design) are resolved, advances to Penjadwalan
      - If PO prerequisite stages (Design) are not yet resolved, records payment fact safely without skipping stages
    """
    invoice_id = str(payload.get("invoice_id") or "").strip()
    if not invoice_id:
        raise HTTPException(400, "ID Faktur Penjualan (invoice_id) wajib diisi.")

    # Validate proof payload early so invalid files never create a receipt / payment effect.
    proof_input = parse_proof_payload(payload.get("payment_proof"))

    async with _get_receipt_lock():
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if idempotency_key:
            existing_by_key = await db.sales_receipts.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
            if existing_by_key:
                # Idempotent replay: if original request included proof and receipt still lacks it, attach once.
                if proof_input and not proof_from_receipt(existing_by_key):
                    try:
                        proof_meta = await store_payment_proof(
                            db,
                            receipt=existing_by_key,
                            user=user,
                            filename=proof_input["name"],
                            data_url=proof_input["data_url"],
                            uploaded_at=now_iso(),
                        )
                        await db.sales_receipts.update_one(
                            {"id": existing_by_key["id"]},
                            {"$set": {"payment_proof": proof_meta}},
                        )
                        existing_by_key["payment_proof"] = proof_meta
                        await _patch_order_payment_receipt_proof(db, existing_by_key, proof_meta)
                    except HTTPException:
                        raise
                attached = await attach_receipt_accounting_fields(db, [_public_receipt(existing_by_key)])
                return attached[0]

        # 1. Fetch exact invoice
        invoice = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not invoice:
            invoice = await db.sales_invoices.find_one({"invoice_number": invoice_id}, {"_id": 0})
        if not invoice:
            raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

        grand_total = float(invoice.get("grand_total", 0))

        cash_bank_id = str(payload.get("cash_bank_id") or "").strip()
        cash_snap = await resolve_cash_bank_for_create(db, cash_bank_id) if cash_bank_id else {}

        # 2. Fetch existing receipts for this invoice to calculate paid_total & outstanding
        existing_receipts = await db.sales_receipts.find({"invoice_id": invoice["id"]}, {"_id": 0}).to_list(500)
        paid_total_before = round(
            sum(float(r.get("amount", 0)) for r in existing_receipts if receipt_counts_toward_outstanding(r)),
            2,
        )
        outstanding_before = max(round(grand_total - paid_total_before, 2), 0.0)

        # 3. Validate receipt amount
        amount = _parse_non_negative_amount(payload.get("amount"), "Jumlah penerimaan")

        if amount > outstanding_before:
            raise HTTPException(
                400,
                f"Jumlah penerimaan (Rp {amount:,.2f}) melebihi sisa tagihan faktur (Rp {outstanding_before:,.2f})."
            )

        new_paid_total = round(paid_total_before + amount, 2)
        new_outstanding = max(round(grand_total - new_paid_total, 2), 0.0)
        payment_state = "LUNAS" if new_paid_total >= grand_total else "DP"

        receipt_id = str(uuid.uuid4())
        receipt_date = str(payload.get("receipt_date") or now_iso()[:10]).strip()

        # Derive receipt number based on existing count for exact po_id
        existing_receipts_count = await db.sales_receipts.count_documents({"po_id": invoice["po_id"]})
        receipt_index = existing_receipts_count + 1
        candidate_receipt_number = derive_receipt_number(invoice["po_number"], receipt_index)
        while await db.sales_receipts.find_one({"receipt_number": candidate_receipt_number}, {"_id": 1}):
            receipt_index += 1
            candidate_receipt_number = derive_receipt_number(invoice["po_number"], receipt_index)
        receipt_number = candidate_receipt_number

        receipt_doc = {
            "id": receipt_id,
            "receipt_number": receipt_number,
            "receipt_index": receipt_index,
            "idempotency_key": idempotency_key or None,
            "invoice_id": invoice["id"],
            "invoice_number": invoice.get("invoice_number", invoice["id"]),
            "po_id": invoice["po_id"],
            "po_number": invoice["po_number"],
            "customer": invoice["customer"],
            "receipt_date": receipt_date,
            "amount": amount,
            "payment_state": payment_state,
            "notes": str(payload.get("notes") or "").strip(),
            "financial_status": "active",
            **cash_snap,
            "created_at": now_iso(),
            "created_by": {
                "id": str(user.get("id") or user.get("_id") or ""),
                "name": str(user.get("name") or user.get("email") or ""),
                "role": str(user.get("role") or ""),
            },
        }

        try:
            await db.sales_receipts.insert_one(receipt_doc)
        except DuplicateKeyError:
            if idempotency_key:
                existing = await db.sales_receipts.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
                if existing:
                    attached = await attach_receipt_accounting_fields(db, [_public_receipt(existing)])
                    return attached[0]
            raise HTTPException(409, "Penerimaan Penjualan duplikat terdeteksi.")

        receipt_doc.pop("_id", None)

        # 4. Update Invoice payment fields
        await db.sales_invoices.update_one(
            {"id": invoice["id"]},
            {
                "$set": {
                    "paid_total": new_paid_total,
                    "outstanding_amount": new_outstanding,
                    "payment_status": payment_state,
                    "updated_at": now_iso(),
                }
            }
        )

        # 5. WORKFLOW PAYMENT INTEGRATION on PO
        po_id = invoice.get("po_id")
        if po_id:
            order = await db.orders.find_one({"id": str(po_id)})
            if not order:
                order = await db.orders.find_one({"po_number": str(po_id)})

            if order and not order.get("cancelled"):
                order.setdefault("payment_receipts", []).append({
                    "receipt_id": receipt_id,
                    "invoice_id": invoice["id"],
                    "amount": amount,
                    "payment_state": payment_state,
                    "receipt_number": receipt_number,
                    "receipt_date": receipt_date,
                    "at": now_iso(),
                })
                order["payment_fact"] = {
                    "receipt_id": receipt_id,
                    "invoice_id": invoice["id"],
                    "amount": amount,
                    "payment_state": payment_state,
                    "updated_at": now_iso(),
                }

                # Auto-approve payment stage if not already approved
                if order.get("payment_status") != "Approved":
                    order["payment_status"] = "Approved"
                    order["payment_approved_by"] = "system"
                    order["payment_approved_by_name"] = "Penerimaan Penjualan"
                    order["payment_approved_at"] = now_iso()
                    order.setdefault("payment_approval_history", []).append({
                        "action": "approve",
                        "by_email": "system",
                        "by_name": "Penerimaan Penjualan",
                        "at": now_iso(),
                        "note": f"Otomatis disetujui dari Penerimaan Penjualan #{receipt_id} ({payment_state})",
                        "receipt_id": receipt_id,
                        "payment_state": payment_state,
                        "amount_received": amount,
                    })

                    # Add timeline entry for automated payment approval
                    timeline_entry = {
                        "stage": 4,
                        "stage_label": "Approval Payment",
                        "at": now_iso(),
                        "by_email": "system",
                        "by_role": "system",
                        "by_name": "Penerimaan Penjualan",
                        "note": f"Approval Payment otomatis disetujui ({payment_state}) via Penerimaan #{receipt_id}",
                    }
                    order.setdefault("timeline", []).append(timeline_entry)

                    # Update current_stage safely without skipping prerequisite stages (Design)
                    sync_workflow_stage(order)

                await db.orders.replace_one({"id": order["id"]}, order)

        # 6. Optional Bukti Payment — after receipt is legitimate; rollback receipt if storage fails.
        if proof_input:
            try:
                proof_meta = await store_payment_proof(
                    db,
                    receipt=receipt_doc,
                    user=user,
                    filename=proof_input["name"],
                    data_url=proof_input["data_url"],
                    uploaded_at=now_iso(),
                )
            except HTTPException:
                # Roll back receipt + invoice/order payment side-effects so failed proof
                # does not leave an authoritative payment without required evidence.
                await db.sales_receipts.delete_one({"id": receipt_id})
                await _recalc_invoice_payment_from_receipts(db, invoice["id"])
                await _recalc_order_payment_facts_after_receipt_delete(db, invoice.get("po_id"), [receipt_id])
                raise
            await db.sales_receipts.update_one(
                {"id": receipt_id},
                {"$set": {"payment_proof": proof_meta}},
            )
            receipt_doc["payment_proof"] = proof_meta
            await _patch_order_payment_receipt_proof(db, receipt_doc, proof_meta)

        attached = await attach_receipt_accounting_fields(db, [_public_receipt(receipt_doc)])
        return attached[0]


async def _patch_order_payment_receipt_proof(db, receipt: dict, proof_meta: dict) -> None:
    """Attach public proof meta onto the matching payment_receipts entry. No payment_files mutation."""
    po_id = receipt.get("po_id")
    receipt_id = receipt.get("id")
    if not po_id or not receipt_id or not proof_meta:
        return
    order = await db.orders.find_one({"id": str(po_id)}, {"_id": 0, "payment_receipts": 1})
    if not order:
        return
    changed = False
    entries = list(order.get("payment_receipts") or [])
    for entry in entries:
        if entry.get("receipt_id") == receipt_id:
            entry["payment_proof"] = public_proof(proof_meta) or proof_meta
            entry["has_payment_proof"] = True
            changed = True
            break
    if changed:
        await db.orders.update_one({"id": str(po_id)}, {"$set": {"payment_receipts": entries}})


async def attach_payment_proof_to_receipt(db, receipt_id: str, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Attach proof to a receipt that has none. Idempotent if proof already exists. Immutable replace denied."""
    receipt = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not receipt:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")
    existing = proof_from_receipt(receipt)
    if existing and existing.get("id"):
        attached = await attach_receipt_accounting_fields(db, [_public_receipt(receipt)])
        return attached[0]
    proof_input = parse_proof_payload(payload.get("payment_proof") or payload)
    if not proof_input:
        raise HTTPException(400, "Bukti Payment wajib diunggah.")
    proof_meta = await store_payment_proof(
        db,
        receipt=receipt,
        user=user,
        filename=proof_input["name"],
        data_url=proof_input["data_url"],
        uploaded_at=now_iso(),
    )
    await db.sales_receipts.update_one({"id": receipt["id"]}, {"$set": {"payment_proof": proof_meta}})
    receipt["payment_proof"] = proof_meta
    await _patch_order_payment_receipt_proof(db, receipt, proof_meta)
    attached = await attach_receipt_accounting_fields(db, [_public_receipt(receipt)])
    return attached[0]


_PAY_DOWNSTREAM_MSG = (
    "Penerimaan tidak dapat dihapus karena alur produksi sudah melanjutkan tahap setelah Approval Payment."
)


async def _receipt_delete_blocked_by_workflow(db, rec: dict, remaining_after_delete: int) -> bool:
    """Last PAY on a PO cannot be removed once Penjadwalan (stage 5+) has started."""
    if remaining_after_delete > 0:
        return False
    po_id = rec.get("po_id")
    if not po_id and rec.get("invoice_id"):
        invoice = await db.sales_invoices.find_one({"id": rec.get("invoice_id")}, {"po_id": 1, "_id": 0})
        po_id = (invoice or {}).get("po_id")
    if not po_id:
        return False
    order = await db.orders.find_one({"id": po_id}, {"current_stage": 1, "_id": 0})
    if not order:
        return False
    return int(order.get("current_stage") or 0) >= 5


async def _recalc_invoice_payment_from_receipts(db, invoice_id: str) -> None:
    if not invoice_id:
        return
    invoice = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        return
    remaining = await db.sales_receipts.find({"invoice_id": invoice_id}, {"_id": 0}).to_list(500)
    paid = round(
        sum(float(r.get("amount", 0)) for r in remaining if receipt_counts_toward_outstanding(r)),
        2,
    )
    grand = float(invoice.get("grand_total", 0))
    outstanding = max(round(grand - paid, 2), 0.0)
    if paid <= 0:
        p_status = "BELUM DIBAYAR"
    elif paid < grand:
        p_status = "DP"
    else:
        p_status = "LUNAS"
    await db.sales_invoices.update_one(
        {"id": invoice_id},
        {"$set": {"paid_total": paid, "outstanding_amount": outstanding, "payment_status": p_status}},
    )


async def _recalc_order_payment_facts_after_receipt_delete(db, po_id: str, deleted_receipt_ids: list) -> None:
    """Recalculate payment_fact from remaining receipts. Never reverse stage history."""
    if not po_id:
        return
    order = await db.orders.find_one({"id": po_id})
    if not order:
        return
    deleted = set(deleted_receipt_ids or [])
    remaining_docs = [
        r
        for r in await db.sales_receipts.find({"po_id": po_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
        if receipt_counts_toward_outstanding(r)
    ]
    kept_facts = [
        r for r in (order.get("payment_receipts") or [])
        if r.get("receipt_id") not in deleted
    ]
    patch: Dict[str, Any] = {"payment_receipts": kept_facts}
    if remaining_docs:
        last = remaining_docs[-1]
        patch["payment_fact"] = {
            "receipt_id": last.get("id"),
            "invoice_id": last.get("invoice_id"),
            "amount": last.get("amount"),
            "payment_state": last.get("payment_state"),
            "updated_at": now_iso(),
        }
    else:
        patch["payment_fact"] = None
        cs = int(order.get("current_stage") or 0)
        if cs < 5 and order.get("payment_approved_by") == "system":
            patch["payment_status"] = "Waiting"
            patch["payment_approved_by"] = None
            patch["payment_approved_by_name"] = None
            patch["payment_approved_at"] = None
    await db.orders.update_one({"id": po_id}, {"$set": patch})


async def delete_sales_receipt(db, receipt_id: str, user: dict) -> dict:
    """Delete a single sales receipt if safe (workflow not past Approval Payment)."""
    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")

    invoice_id = rec.get("invoice_id")
    po_id = rec.get("po_id")
    if not po_id and invoice_id:
        invoice = await db.sales_invoices.find_one({"id": invoice_id}, {"po_id": 1, "_id": 0})
        po_id = (invoice or {}).get("po_id")

    remaining_after = 0
    if po_id:
        remaining_after = max(await db.sales_receipts.count_documents({"po_id": po_id}) - 1, 0)
    journal = await find_sales_receipt_source_journal(db, rec["id"])
    if journal:
        if str(journal.get("status") or "").strip().lower() == "reversed" or receipt_is_cancelled(rec):
            raise HTTPException(409, REVERSED_DELETE_MESSAGE)
        raise HTTPException(409, POSTED_DELETE_MESSAGE)
    if receipt_is_cancelled(rec):
        raise HTTPException(409, REVERSED_DELETE_MESSAGE)
    if posting_claim_is_active(rec):
        raise HTTPException(409, POSTING_IN_PROGRESS_DELETE_MESSAGE)

    if await _receipt_delete_blocked_by_workflow(db, rec, remaining_after):
        raise HTTPException(400, _PAY_DOWNSTREAM_MSG)

    receipt_number = rec.get("receipt_number", rec["id"])
    deleted = await db.sales_receipts.find_one_and_delete(
        {
            "id": rec["id"],
            "$or": [
                {CLAIM_FIELD: {"$exists": False}},
                {CLAIM_FIELD: None},
                {CLAIM_FIELD: ""},
            ],
        }
    )
    if not deleted:
        fresh = await db.sales_receipts.find_one({"id": rec["id"]}, {"_id": 0})
        if fresh and posting_claim_is_active(fresh):
            raise HTTPException(409, POSTING_IN_PROGRESS_DELETE_MESSAGE)
        journal = await find_sales_receipt_source_journal(db, rec["id"])
        if journal:
            raise HTTPException(409, POSTED_DELETE_MESSAGE)
        raise HTTPException(409, POSTING_IN_PROGRESS_DELETE_MESSAGE)
    await soft_delete_proof_for_receipt(
        db,
        rec["id"],
        deleted_by=str(user.get("email") or ""),
        reason="sales_receipt_deleted",
    )
    await _recalc_invoice_payment_from_receipts(db, invoice_id)
    await _recalc_order_payment_facts_after_receipt_delete(db, po_id, [rec["id"]])
    return {"deleted": True, "receipt_number": receipt_number, "id": rec["id"]}


async def bulk_delete_sales_receipts(
    db,
    ids: list,
    user: dict,
    *,
    after_eligibility=None,
    after_reserve=None,
) -> dict:
    """Bulk delete receipts — ALL OR NOTHING, reserved before any physical delete."""
    unique_ids = list(dict.fromkeys(ids))
    receipts = await db.sales_receipts.find({"id": {"$in": unique_ids}}, {"_id": 0}).to_list(len(unique_ids) or 1)
    by_id = {r["id"]: r for r in receipts}

    blockers = []
    to_delete = []

    for rid in unique_ids:
        rec = by_id.get(rid)
        if not rec:
            blockers.append({"id": rid, "number": rid, "reason": "Penerimaan tidak ditemukan."})
            continue
        po_id = rec.get("po_id")
        if not po_id and rec.get("invoice_id"):
            invoice = await db.sales_invoices.find_one({"id": rec.get("invoice_id")}, {"po_id": 1, "_id": 0})
            po_id = (invoice or {}).get("po_id")
            rec["po_id"] = po_id
        remaining_after = 0
        if po_id:
            all_po_receipts = await db.sales_receipts.count_documents({"po_id": po_id})
            po_receipts_in_batch = sum(1 for rid2 in unique_ids if by_id.get(rid2, {}).get("po_id") == po_id)
            remaining_after = max(all_po_receipts - po_receipts_in_batch, 0)
        journal = await find_sales_receipt_source_journal(db, rec["id"])
        if journal:
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": (
                    REVERSED_DELETE_MESSAGE
                    if str(journal.get("status") or "").strip().lower() == "reversed" or receipt_is_cancelled(rec)
                    else POSTED_DELETE_MESSAGE
                ),
            })
            continue
        if receipt_is_cancelled(rec):
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": REVERSED_DELETE_MESSAGE,
            })
            continue
        if posting_claim_is_active(rec):
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": POSTING_IN_PROGRESS_DELETE_MESSAGE,
            })
            continue
        if await _receipt_delete_blocked_by_workflow(db, rec, remaining_after):
            blockers.append({
                "id": rec["id"],
                "number": rec.get("receipt_number", rec["id"]),
                "reason": _PAY_DOWNSTREAM_MSG,
            })
            continue
        to_delete.append(rec)

    if blockers:
        raise HTTPException(400, {"message": "Tidak dapat menghapus. Beberapa penerimaan memiliki dependensi.", "blockers": blockers})

    if after_eligibility is not None:
        await after_eligibility(list(to_delete))

    if not to_delete:
        return {"deleted_count": 0, "deleted": []}

    token = str(uuid.uuid4())
    reserved: List[dict] = []
    try:
        for rec in to_delete:
            claimed = await acquire_delete_claim(db, rec["id"], token)
            if claimed is None:
                raise HTTPException(409, {
                    "message": "Tidak dapat menghapus. Beberapa penerimaan memiliki dependensi.",
                    "blockers": [{
                        "id": rec["id"],
                        "number": rec.get("receipt_number", rec["id"]),
                        "reason": POSTING_IN_PROGRESS_DELETE_MESSAGE,
                    }],
                })
            journal = await find_sales_receipt_source_journal(db, rec["id"])
            if journal:
                raise HTTPException(409, {
                    "message": "Tidak dapat menghapus. Beberapa penerimaan memiliki dependensi.",
                    "blockers": [{
                        "id": rec["id"],
                        "number": rec.get("receipt_number", rec["id"]),
                        "reason": POSTED_DELETE_MESSAGE,
                    }],
                })
            reserved.append(rec)

        if after_reserve is not None:
            await after_reserve(list(reserved), token)

        result = await db.sales_receipts.delete_many({DELETE_CLAIM_FIELD: token})
        if int(result.deleted_count or 0) != len(reserved):
            raise HTTPException(409, {
                "message": "Tidak dapat menghapus. Beberapa penerimaan memiliki dependensi.",
                "blockers": [{
                    "id": reserved[0]["id"] if reserved else "",
                    "number": reserved[0].get("receipt_number", "") if reserved else "",
                    "reason": POSTING_IN_PROGRESS_DELETE_MESSAGE,
                }],
            })
    except Exception:
        await release_delete_claims(db, token)
        raise

    for r in reserved:
        await soft_delete_proof_for_receipt(
            db,
            r["id"],
            deleted_by=str(user.get("email") or ""),
            reason="sales_receipt_bulk_deleted",
        )

    affected_invoice_ids = list(set(r.get("invoice_id") for r in reserved if r.get("invoice_id")))
    for inv_id in affected_invoice_ids:
        await _recalc_invoice_payment_from_receipts(db, inv_id)

    affected_po_ids = list(set(r.get("po_id") for r in reserved if r.get("po_id")))
    for po_id in affected_po_ids:
        po_deleted = [r["id"] for r in reserved if r.get("po_id") == po_id]
        await _recalc_order_payment_facts_after_receipt_delete(db, po_id, po_deleted)

    return {
        "deleted_count": len(reserved),
        "deleted": [{"id": r["id"], "receipt_number": r.get("receipt_number", "")} for r in reserved],
    }


def generate_sales_receipt_pdf_bytes(receipt: dict) -> bytes:
    """Generate official A4 PDF for Sales Receipt (Penerimaan Penjualan)."""
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from services.pdf_date import format_pdf_date

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    styles = getSampleStyleSheet()

    company_title_style = ParagraphStyle(
        'CompanyTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=colors.HexColor('#0f172a'),
    )

    doc_title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        alignment=2,
        textColor=colors.HexColor('#059669'), # Green for receipt
    )

    meta_label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#334155'),
    )

    meta_val_style = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#0f172a'),
    )

    tbl_cell_bold = ParagraphStyle(
        'TblCellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        alignment=2, # Right align for numbers
        textColor=colors.HexColor('#0f172a'),
    )

    from html import escape

    elements = []

    header_data = [
        [
            Paragraph("<b>ETERNAL PACKAGING</b>", company_title_style),
            Paragraph("<b>PENERIMAAN PENJUALAN</b><br/><font size='9' color='#10b981'><b>" + escape(str(receipt.get('receipt_number') or receipt.get('id') or '')) + "</b></font>", doc_title_style),
        ]
    ]
    header_tbl = Table(header_data, colWidths=[9.5 * cm, 8.5 * cm])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 0.2 * cm))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#34d399'), spaceAfter=8, spaceBefore=0))

    meta_data = [
        [
            Paragraph("<b>Nomor Faktur</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('invoice_number') or '-'))}", meta_val_style),
            Paragraph("<b>Customer</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('customer') or '-'))}", meta_val_style),
        ],
        [
            Paragraph("<b>Nomor PO</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('po_number') or '-'))}", meta_val_style),
            Paragraph("<b>Tgl Penerimaan</b>", meta_label_style),
            Paragraph(f": {format_pdf_date(receipt.get('receipt_date'))}", meta_val_style),
        ],
        [
            Paragraph("<b>Tipe Bayar</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('payment_state') or '-'))}", meta_val_style),
            Paragraph("<b>Catatan</b>", meta_label_style),
            Paragraph(f": {escape(str(receipt.get('notes') or '-'))}", meta_val_style),
        ],
    ]
    meta_tbl = Table(meta_data, colWidths=[3.0 * cm, 6.0 * cm, 3.0 * cm, 6.0 * cm])
    meta_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(meta_tbl)
    elements.append(Spacer(1, 0.8 * cm))

    def format_money(val):
        try:
            f_val = float(val)
            if f_val.is_integer():
                return "Rp " + f"{int(f_val):,}".replace(",", ".")
            else:
                s = f"{f_val:,.2f}"
                return "Rp " + s.replace(",", "X").replace(".", ",").replace("X", ".")
        except:
            return "Rp 0"

    amount = receipt.get("amount", 0)

    amount_data = [
        [
            Paragraph("<font size='10'><b>TOTAL DITERIMA</b></font>", meta_label_style),
            Paragraph(f"<font size='12'>{format_money(amount)}</font>", tbl_cell_bold),
        ]
    ]

    amount_tbl = Table(amount_data, colWidths=[9.0 * cm, 9.0 * cm])
    amount_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ecfdf5')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#6ee7b7')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    elements.append(amount_tbl)

    doc.build(elements)
    return buffer.getvalue()
