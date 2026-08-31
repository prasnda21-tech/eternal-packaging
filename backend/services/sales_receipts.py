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
        return "PAY-" + datetime.now(timezone.utc).strftime("%Y%m%d-001")
    
    # 1. Match PO/2026/08/30/000123 or SO/2026/08/30/000123 -> PAY/2026/08/30/000123
    # 2. Match PO-20260830-001 or SO-20260830-001 -> PAY-20260830-001
    # 3. Match PO.2026.08.001 -> PAY.2026.08.001
    # 4. Match PO 12345 -> PAY 12345
    # 5. Match PO12345 -> PAY12345
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
    
    Index 1: base PAY (e.g. PAY/2026/08/31/000125)
    Index 2: base PAY + "-2" (e.g. PAY/2026/08/31/000125-2)
    Index 3: base PAY + "-3" (e.g. PAY/2026/08/31/000125-3)
    """
    base = derive_base_receipt_number(po_number)
    if receipt_index <= 1:
        return base
    return f"{base}-{receipt_index}"


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
    return receipts


async def get_sales_receipt_detail(db, receipt_id: str) -> Dict[str, Any]:
    """Fetch detail of a single sales receipt."""
    r = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not r:
        r = await db.sales_receipts.find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not r:
        raise HTTPException(404, f"Penerimaan Penjualan '{receipt_id}' tidak ditemukan.")
    return r


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

    async with _get_receipt_lock():
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if idempotency_key:
            existing_by_key = await db.sales_receipts.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
            if existing_by_key:
                return existing_by_key

        # 1. Fetch exact invoice
        invoice = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
        if not invoice:
            invoice = await db.sales_invoices.find_one({"invoice_number": invoice_id}, {"_id": 0})
        if not invoice:
            raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

        grand_total = float(invoice.get("grand_total", 0))

        # 2. Fetch existing receipts for this invoice to calculate paid_total & outstanding
        existing_receipts = await db.sales_receipts.find({"invoice_id": invoice["id"]}, {"_id": 0}).to_list(500)
        paid_total_before = round(sum(float(r.get("amount", 0)) for r in existing_receipts), 2)
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
                    return existing
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

        return receipt_doc


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
    paid = round(sum(float(r.get("amount", 0)) for r in remaining), 2)
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
    remaining_docs = await db.sales_receipts.find({"po_id": po_id}, {"_id": 0}).sort("created_at", 1).to_list(500)
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
    if await _receipt_delete_blocked_by_workflow(db, rec, remaining_after):
        raise HTTPException(400, _PAY_DOWNSTREAM_MSG)

    receipt_number = rec.get("receipt_number", rec["id"])
    await db.sales_receipts.delete_one({"id": rec["id"]})
    await _recalc_invoice_payment_from_receipts(db, invoice_id)
    await _recalc_order_payment_facts_after_receipt_delete(db, po_id, [rec["id"]])
    return {"deleted": True, "receipt_number": receipt_number, "id": rec["id"]}


async def bulk_delete_sales_receipts(db, ids: list, user: dict) -> dict:
    """Bulk delete receipts — ALL OR NOTHING."""
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

    if to_delete:
        delete_ids = [r["id"] for r in to_delete]
        await db.sales_receipts.delete_many({"id": {"$in": delete_ids}})

        affected_invoice_ids = list(set(r.get("invoice_id") for r in to_delete if r.get("invoice_id")))
        for inv_id in affected_invoice_ids:
            await _recalc_invoice_payment_from_receipts(db, inv_id)

        affected_po_ids = list(set(r.get("po_id") for r in to_delete if r.get("po_id")))
        for po_id in affected_po_ids:
            po_deleted = [r["id"] for r in to_delete if r.get("po_id") == po_id]
            await _recalc_order_payment_facts_after_receipt_delete(db, po_id, po_deleted)

    return {
        "deleted_count": len(to_delete),
        "deleted": [{"id": r["id"], "receipt_number": r.get("receipt_number", "")} for r in to_delete],
    }

