"""Purchase Invoice (Faktur Pembelian).

Sources:
- receipt (default UI): invoice against goods already received
- purchase_order: may precede goods receipt when acknowledgement is set

Invoice/payment amounts do not rewrite PO HPP unit prices.
Invoice never mutates received_qty or stock.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.purchase_common import (
    PREFIX_PURCHASE_INVOICE,
    actor_snapshot,
    actor_user_id,
    add_days_ymd,
    stamp_create,
    stamp_update,
    cancellation_fields,
    derive_child_number,
    get_named_lock,
    insert_with_unique_number,
    invoice_due_display_status,
    is_cancelled,
    line_subtotal,
    money_round,
    now_iso,
    parse_optional_ymd,
    parse_payment_term_days,
    parse_qty,
    parse_required_ymd,
    qty_le,
    with_active_filter,
)
from services.purchase_branch import (
    invoice_location_public,
    resolve_invoice_location_from_gr,
    resolve_invoice_location_from_po,
    strip_client_invoice_location,
)
from services.purchase_orders import get_purchase_order

COLLECTION = "purchase_invoices"
PAY_COLLECTION = "purchase_payments"
GR_COLLECTION = "purchase_goods_receipts"
AUDIT_COLLECTION = "audit_logs"
AUDIT_ACTION_TERMS = "purchase_invoice.payment_terms.update"
HISTORY_FIELD = "payment_terms_history"
HISTORY_CAP = 40


def payment_status(grand_total: float, paid_total: float) -> str:
    """Compatibility wrapper. Canonical fields come from invoice_financial_summary."""
    from services.purchase_invoice_finance import invoice_financial_summary

    return invoice_financial_summary(
        invoice_amount=grand_total,
        payments_applied=paid_total,
    )["payment_status"]


CONFIRMED_PAYMENT_FILTER = {
    "cancelled": {"$ne": True},
    "settlement_status": "PAID",
}


async def _paid_total(db, invoice_id: str) -> float:
    """Sum amounts from active CONFIRMED (PAID) payments only — drafts/submitted unpaid excluded."""
    rows = await db[PAY_COLLECTION].find(
        {
            "purchase_invoice_id": invoice_id,
            **CONFIRMED_PAYMENT_FILTER,
        },
        {"_id": 0, "amount": 1},
    ).to_list(2000)
    return money_round(sum(float(r.get("amount") or 0) for r in rows))


async def _active_payment_count(db, invoice_id: str) -> int:
    return int(await db[PAY_COLLECTION].count_documents(
        with_active_filter({"purchase_invoice_id": invoice_id})
    ))


async def _advance_applied_total(db, invoice_id: str) -> float:
    """Active supplier-advance allocations only. Future return credit is a separate subtraction."""
    from services.purchase_supplier_advance_allocations import active_applied_total

    return await active_applied_total(db, invoice_id)


async def attach_payment_fields(db, inv: dict) -> dict:
    from services.purchase_invoice_finance import invoice_financial_summary
    from services.purchase_return_finance import active_return_credit_breakdown

    out = dict(inv)
    # LOC-B: normalize location snapshot for read model (legacy nulls stay null).
    out.update(invoice_location_public(out))
    paid = await _paid_total(db, out["id"])
    applied = await _advance_applied_total(db, out["id"])
    credit = await active_return_credit_breakdown(db, out["id"])
    summary = invoice_financial_summary(
        invoice_amount=out.get("grand_total"),
        advance_applied=applied,
        payments_applied=paid,
        purchase_return_credit=credit["purchase_return_credit"],
        cancelled=is_cancelled(out),
    )
    summary["purchase_return_credit_applied_to_outstanding"] = credit["purchase_return_credit_applied_to_outstanding"]
    summary["supplier_credit_generated"] = credit["supplier_credit_generated"]
    raw_status = str(out.get("payment_status") or "")
    out.update(summary)
    out["paid_total"] = paid
    out["normal_payment_total"] = paid
    out["advance_applied_amount"] = applied
    if raw_status.upper() == "DP":
        out["legacy_payment_status"] = "DP"
    active_pays = await _active_payment_count(db, out["id"])
    if is_cancelled(out):
        out["can_cancel"] = False
        out["cancel_block_reason"] = ""
    else:
        out["can_cancel"] = active_pays <= 0 and applied <= 0
        if active_pays > 0:
            out["cancel_block_reason"] = (
                "Faktur tidak dapat dihapus karena sudah memiliki pembayaran. Batalkan pembayaran terlebih dahulu."
            )
        elif applied > 0:
            out["cancel_block_reason"] = "Faktur tidak dapat dihapus karena masih memiliki alokasi uang muka."
        else:
            out["cancel_block_reason"] = ""
    # Legacy invoices may omit due_date / payment_term_days — keep nullable contract.
    if "due_date" not in out or out.get("due_date") in ("",):
        out["due_date"] = out.get("due_date") or None
    if out.get("payment_term_days") in ("", None):
        out["payment_term_days"] = out.get("payment_term_days")
    else:
        try:
            out["payment_term_days"] = int(out["payment_term_days"])
        except (TypeError, ValueError):
            out["payment_term_days"] = out.get("payment_term_days")
    pay_status = str(out.get("payment_status") or "")
    out["due_status"] = invoice_due_display_status(
        due_date=out.get("due_date"),
        payment_status=pay_status,
    )
    out["can_edit_payment_terms"] = (not is_cancelled(out)) and pay_status != "LUNAS"
    if is_cancelled(out):
        out["edit_payment_terms_block_reason"] = "Faktur yang dibatalkan tidak dapat diubah termin/jatuh temponya."
    elif pay_status == "LUNAS":
        out["edit_payment_terms_block_reason"] = "Faktur yang sudah lunas tidak dapat diubah termin/jatuh temponya."
    else:
        out["edit_payment_terms_block_reason"] = ""
    return out


def _resolve_create_terms(payload: dict, invoice_date: str) -> tuple:
    """Return (payment_term_days, due_date). New invoices always persist a concrete due_date.

    Missing term defaults to COD (0) so legacy API callers still create a valid due_date
    (= invoice_date). UI always sends an explicit term preset.
    """
    term_days = parse_payment_term_days(payload.get("payment_term_days"), required=False)
    raw_due = payload.get("due_date")
    if raw_due is None or str(raw_due).strip() == "":
        due_date = add_days_ymd(invoice_date, term_days)
    else:
        due_date = parse_required_ymd(raw_due, "Tanggal jatuh tempo")
    return term_days, due_date


async def _write_terms_audit(
    db,
    *,
    user: dict,
    invoice_id: str,
    invoice_number: str,
    before: dict,
    after: dict,
    reason: str,
) -> str:
    """Insert audit_logs row. Caller must not mutate invoice if this raises."""
    audit_id = str(uuid.uuid4())
    actor = actor_snapshot(user)
    reason_text = str(reason or "").strip()
    await db[AUDIT_COLLECTION].insert_one({
        "id": audit_id,
        "action": AUDIT_ACTION_TERMS,
        "entity_type": "purchase_invoice",
        "entity_id": invoice_id,
        "invoice_number": invoice_number,
        "actor_email": actor.get("email"),
        "actor_role": actor.get("role"),
        "actor_name": actor.get("name"),
        "actor_id": actor_user_id(user),
        "before": before,
        "after": after,
        "reason": reason_text,
        "note": reason_text,
        "at": now_iso(),
    })
    return audit_id


async def _delete_terms_audit(db, audit_id: str) -> None:
    if not audit_id:
        return
    await db[AUDIT_COLLECTION].delete_one({"id": audit_id, "action": AUDIT_ACTION_TERMS})


def _invoice_source_of(payload: dict) -> str:
    raw = str(payload.get("invoice_source") or "").strip().lower()
    if raw in {"receipt", "penerimaan", "goods_receipt"}:
        return "receipt"
    if raw in {"purchase_order", "po", "pesanan"}:
        return "purchase_order"
    if str(payload.get("receipt_id") or payload.get("goods_receipt_id") or "").strip():
        return "receipt"
    return "purchase_order"


async def get_po_invoice_preview(db, purchase_order_id: str, exclude_invoice_id: Optional[str] = None) -> dict:
    po = await get_purchase_order(db, purchase_order_id)
    if exclude_invoice_id:
        po = await get_purchase_order(db, purchase_order_id)
    lines = []
    for li in po.get("lines") or []:
        remain = float(li.get("remaining_invoice_qty") or 0)
        received = float(li.get("cumulative_received_qty") or 0)
        invoiced = float(li.get("cumulative_invoiced_qty") or 0)
        unit_price = float(li.get("unit_price") or 0)
        qty = remain
        lines.append({
            "purchase_order_line_id": li.get("line_id"),
            "line_idx": li.get("line_idx"),
            "line_id": li.get("line_id"),
            "request_line_id": li.get("request_line_id"),
            "master_item_id": li.get("master_item_id") or "",
            "accurate_item_id": li.get("accurate_item_id") or "",
            "item_code": li.get("item_code") or "",
            "item_name": li.get("item_name") or "",
            "unit": li.get("unit") or "pcs",
            "ordered_qty": li.get("ordered_qty") or 0,
            "cumulative_received_qty": received,
            "confirmed_return_qty": float(li.get("confirmed_return_qty") or 0),
            "net_received_qty": float(li.get("net_received_qty") if li.get("net_received_qty") is not None else received),
            "cumulative_invoiced_qty": invoiced,
            "remaining_invoice_qty": remain,
            "receipt_invoice_eligible_qty": float(li.get("receipt_invoice_eligible_qty") or 0),
            "invoice_qty": qty,
            "unit_price": unit_price,
            "line_subtotal": line_subtotal(qty, unit_price),
        })
    location = await resolve_invoice_location_from_po(db, po)
    return {
        "purchase_order_id": po["id"],
        "po_number": po.get("po_number"),
        "request_id": po.get("request_id"),
        "request_number": po.get("request_number"),
        "supplier": po.get("supplier"),
        "order_date": po.get("order_date"),
        "lines": lines,
        "invoice_progress": po.get("invoice_progress") or {},
        "receiving_progress": po.get("receiving_progress") or {},
        "receiving_status": po.get("receiving_status"),
        "invoice_status": po.get("invoice_status"),
        **location,
    }


async def get_receipt_invoice_preview(db, receipt_id: str) -> dict:
    """Prefill invoice lines from an official PO-linked goods receipt."""
    receipt = await db[GR_COLLECTION].find_one({"id": str(receipt_id)}, {"_id": 0})
    if not receipt:
        receipt = await db[GR_COLLECTION].find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not receipt:
        raise HTTPException(404, f"Penerimaan '{receipt_id}' tidak ditemukan.")
    if is_cancelled(receipt):
        raise HTTPException(400, "Penerimaan sudah dibatalkan; faktur tidak dapat dibuat.")
    po_id = str(receipt.get("purchase_order_id") or "").strip()
    if not po_id:
        raise HTTPException(
            400,
            "Faktur dari penerimaan hanya didukung untuk penerimaan yang terhubung ke Pesanan Pembelian.",
        )
    po_preview = await get_po_invoice_preview(db, po_id)
    by_line = {str(l["purchase_order_line_id"]): l for l in po_preview["lines"]}
    from services.purchase_returns import confirmed_return_qty_by_receipt_line
    returned_on_receipt = await confirmed_return_qty_by_receipt_line(db, receipt["id"])
    lines = []
    for li in receipt.get("lines") or []:
        po_line_id = str(li.get("purchase_order_line_id") or "").strip()
        src = by_line.get(po_line_id)
        if src is None:
            continue
        receipt_qty = float(li.get("received_qty") or 0)
        receipt_qty = max(0.0, receipt_qty - float(returned_on_receipt.get(str(li.get("line_id") or ""), 0)))
        eligible = min(
            float(src.get("remaining_invoice_qty") or 0),
            float(src.get("receipt_invoice_eligible_qty") or 0),
            receipt_qty,
        )
        if eligible <= 1e-9:
            continue
        unit_price = float(src.get("unit_price") or 0)
        lines.append({
            **src,
            "receipt_line_id": li.get("line_id"),
            "receipt_received_qty": receipt_qty,
            "remaining_invoice_qty": eligible,
            "invoice_qty": eligible,
            "line_subtotal": line_subtotal(eligible, unit_price),
        })
    po_doc = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0})
    location = await resolve_invoice_location_from_gr(db, receipt, po_doc)
    return {
        "invoice_source": "receipt",
        "receipt_id": receipt["id"],
        "receipt_number": receipt.get("receipt_number"),
        "purchase_order_id": po_preview["purchase_order_id"],
        "po_number": po_preview.get("po_number"),
        "request_id": po_preview.get("request_id"),
        "request_number": po_preview.get("request_number"),
        "supplier": po_preview.get("supplier") or receipt.get("supplier"),
        "order_date": po_preview.get("order_date"),
        "receipt_date": receipt.get("receipt_date"),
        "lines": lines,
        "invoice_progress": po_preview.get("invoice_progress") or {},
        "receiving_progress": po_preview.get("receiving_progress") or {},
        **location,
    }


async def create_purchase_invoice(db, payload: Dict[str, Any], user: dict) -> dict:
    payload = strip_client_invoice_location(payload)
    invoice_source = _invoice_source_of(payload)
    precedes_goods_receipt = bool(payload.get("precedes_goods_receipt"))
    receipt_id = str(payload.get("receipt_id") or payload.get("goods_receipt_id") or "").strip() or None
    po_id = str(payload.get("purchase_order_id") or "").strip()
    receipt_doc = None
    receipt_qty_by_line: Dict[str, float] = {}

    if invoice_source == "receipt":
        if not receipt_id:
            raise HTTPException(400, "receipt_id wajib diisi untuk faktur dari penerimaan.")
        receipt_doc = await db[GR_COLLECTION].find_one({"id": receipt_id}, {"_id": 0})
        if not receipt_doc:
            raise HTTPException(404, f"Penerimaan '{receipt_id}' tidak ditemukan.")
        po_id = str(receipt_doc.get("purchase_order_id") or po_id or "").strip()
        if not po_id:
            raise HTTPException(
                400,
                "Faktur dari penerimaan hanya didukung untuk penerimaan yang terhubung ke Pesanan Pembelian.",
            )
        precedes_goods_receipt = False
        from services.purchase_returns import confirmed_return_qty_by_receipt_line
        returned_on_receipt = await confirmed_return_qty_by_receipt_line(db, receipt_id)
        for li in receipt_doc.get("lines") or []:
            key = str(li.get("purchase_order_line_id") or "").strip()
            if key:
                gross = float(li.get("received_qty") or 0)
                returned = float(returned_on_receipt.get(str(li.get("line_id") or ""), 0))
                receipt_qty_by_line[key] = receipt_qty_by_line.get(key, 0.0) + max(0.0, gross - returned)

    if not po_id:
        raise HTTPException(400, "ID Pesanan Pembelian (purchase_order_id) wajib diisi.")

    async with get_named_lock("purchase_invoice"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                return await attach_payment_fields(db, existing)

        po_check = await get_purchase_order(db, po_id)
        if is_cancelled(po_check):
            raise HTTPException(400, "Pesanan sudah dibatalkan; faktur tidak dapat dibuat.")
        if receipt_doc and is_cancelled(receipt_doc):
            raise HTTPException(400, "Penerimaan sudah dibatalkan; faktur tidak dapat dibuat.")

        preview = await get_po_invoice_preview(db, po_id)
        by_line = {str(l["purchase_order_line_id"]): l for l in preview["lines"]}
        input_lines = payload.get("lines") or payload.get("line_items") or []
        calculated = []
        total = 0.0
        seen = set()
        early_needed = False

        sources = input_lines if input_lines else preview["lines"]
        if invoice_source == "receipt" and not input_lines and receipt_doc:
            # Prefer receipt-scoped eligible defaults
            receipt_preview = await get_receipt_invoice_preview(db, receipt_id)
            sources = receipt_preview["lines"]

        for inp in sources:
            po_line_id = str(inp.get("purchase_order_line_id") or inp.get("line_id") or "").strip()
            src = by_line.get(po_line_id)
            if src is None:
                continue
            if po_line_id in seen:
                raise HTTPException(400, "Baris PO tidak boleh digabung pada satu faktur.")
            seen.add(po_line_id)
            raw_qty = inp.get("invoice_qty", inp.get("quantity"))
            if raw_qty in (None, "") and not input_lines:
                if invoice_source == "receipt":
                    raw_qty = min(
                        float(src["remaining_invoice_qty"] or 0),
                        float(src["receipt_invoice_eligible_qty"] or 0),
                        float(receipt_qty_by_line.get(po_line_id) or 0) or float(src["receipt_invoice_eligible_qty"] or 0),
                    )
                else:
                    raw_qty = src["remaining_invoice_qty"]
            if raw_qty in (None, "") or float(raw_qty or 0) == 0:
                continue
            qty = parse_qty(raw_qty, f"Qty faktur '{src['item_name']}'")
            remain = float(src["remaining_invoice_qty"] or 0)
            returned = float(src.get("confirmed_return_qty") or 0)
            invoice_cap = max(0.0, remain - returned)
            if not qty_le(qty, invoice_cap):
                if returned > 0:
                    raise HTTPException(
                        400,
                        f"Qty faktur ({qty}) untuk '{src['item_name']}' melebihi qty yang masih dapat difaktur setelah retur ({invoice_cap}).",
                    )
                raise HTTPException(
                    400,
                    f"Qty faktur ({qty}) untuk '{src['item_name']}' melebihi sisa PO ({remain}).",
                )

            received = float(src.get("cumulative_received_qty") or 0)
            net_received = float(src.get("net_received_qty") if src.get("net_received_qty") is not None else max(0.0, received - returned))
            already = float(src.get("cumulative_invoiced_qty") or 0)
            uninvoiced_received = max(0.0, net_received - already)

            if invoice_source == "receipt":
                receipt_cap = float(receipt_qty_by_line.get(po_line_id) or 0)
                if not qty_le(qty, receipt_cap):
                    raise HTTPException(
                        400,
                        f"Qty faktur ({qty}) untuk '{src['item_name']}' melebihi qty penerimaan terpilih ({receipt_cap}).",
                    )
                if not qty_le(qty, uninvoiced_received):
                    raise HTTPException(
                        400,
                        f"Qty faktur ({qty}) untuk '{src['item_name']}' melebihi sisa penerimaan yang belum difaktur ({uninvoiced_received}).",
                    )
            else:
                if already + qty > net_received + 1e-9:
                    early_needed = True

            unit_price = float(src["unit_price"] or 0)
            sub = line_subtotal(qty, unit_price)
            total += sub
            calculated.append({
                "line_idx": src["line_idx"],
                "line_id": str(uuid.uuid4()),
                "purchase_order_line_id": src["purchase_order_line_id"],
                "request_line_id": src.get("request_line_id") or "",
                "master_item_id": src["master_item_id"],
                "accurate_item_id": src["accurate_item_id"],
                "item_code": src["item_code"],
                "item_name": src["item_name"],
                "unit": src["unit"],
                "invoice_qty": qty,
                "quantity": qty,
                "unit_price": unit_price,
                "line_subtotal": sub,
            })

        if not calculated:
            raise HTTPException(400, "Total kuantitas faktur harus lebih besar dari 0.")

        if invoice_source == "purchase_order" and early_needed and not precedes_goods_receipt:
            raise HTTPException(
                400,
                "Faktur mendahului penerimaan barang. Centang acknowledgement "
                "'Faktur mendahului penerimaan barang' untuk menyimpan.",
            )
        if invoice_source == "purchase_order" and not early_needed:
            precedes_goods_receipt = False

        ts = now_iso()
        total = money_round(total)
        invoice_date = parse_required_ymd(
            payload.get("invoice_date") or ts[:10],
            "Tanggal faktur",
        )
        term_days, due_date = _resolve_create_terms(payload, invoice_date)
        if invoice_source == "receipt":
            location = await resolve_invoice_location_from_gr(db, receipt_doc or {}, po_check)
        else:
            location = await resolve_invoice_location_from_po(db, po_check)
        existing_count = await db[COLLECTION].count_documents({"purchase_order_id": preview["purchase_order_id"]})
        doc = {
            "id": str(uuid.uuid4()),
            "purchase_order_id": preview["purchase_order_id"],
            "po_number": preview["po_number"],
            "request_id": preview.get("request_id"),
            "request_number": preview.get("request_number"),
            "supplier": preview.get("supplier"),
            "invoice_source": invoice_source,
            "receipt_id": receipt_id if invoice_source == "receipt" else None,
            "receipt_number": (receipt_doc or {}).get("receipt_number") if invoice_source == "receipt" else None,
            "precedes_goods_receipt": bool(precedes_goods_receipt),
            "invoice_date": invoice_date,
            "payment_term_days": term_days,
            "due_date": due_date,
            HISTORY_FIELD: [],
            "notes": str(payload.get("notes") or "").strip(),
            "lines": calculated,
            "subtotal": total,
            "grand_total": total,
            **location,
            **stamp_create(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        async def _alloc():
            idx = existing_count + 1
            candidate = derive_child_number(preview["po_number"], PREFIX_PURCHASE_INVOICE, idx)
            while await db[COLLECTION].find_one({"invoice_number": candidate}, {"_id": 1}):
                idx += 1
                candidate = derive_child_number(preview["po_number"], PREFIX_PURCHASE_INVOICE, idx)
            return candidate

        saved = await insert_with_unique_number(db, COLLECTION, "invoice_number", doc, _alloc)
        # Invoice must never mutate PO received_qty / stock.
        return await attach_payment_fields(db, saved)


async def list_purchase_invoices(
    db,
    q: Optional[str] = None,
    purchase_order_id: Optional[str] = None,
    with_outstanding: bool = False,
    include_cancelled: bool = False,
) -> List[dict]:
    filters: Dict[str, Any] = {}
    if purchase_order_id:
        filters["purchase_order_id"] = str(purchase_order_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"invoice_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"supplier": {"$regex": needle, "$options": "i"}},
        ]
    if not include_cancelled:
        filters = with_active_filter(filters)
    rows = await db[COLLECTION].find(filters, {"_id": 0}).sort("created_at", -1).to_list(1000)
    out = []
    for row in rows:
        enriched = await attach_payment_fields(db, row)
        if with_outstanding and float(enriched.get("outstanding_amount") or 0) <= 0:
            continue
        out.append(enriched)
    from services.purchase_invoice_posting import attach_invoice_accounting_fields

    return await attach_invoice_accounting_fields(db, out)


async def get_purchase_invoice(db, invoice_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(invoice_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Faktur Pembelian '{invoice_id}' tidak ditemukan.")
    inv = await attach_payment_fields(db, doc)
    from services.purchase_payments import _enrich_payment

    payments = await db[PAY_COLLECTION].find(
        with_active_filter({"purchase_invoice_id": inv["id"]}),
        {"_id": 0},
    ).sort([("submitted_at", 1), ("created_at", 1)]).to_list(500)
    inv["payments"] = [_enrich_payment(p) for p in payments]
    from services.purchase_invoice_posting import attach_invoice_accounting_fields
    from services.purchase_payment_posting import attach_payment_accounting_fields

    await attach_invoice_accounting_fields(db, [inv])
    await attach_payment_accounting_fields(db, inv["payments"])
    return inv


async def update_purchase_invoice_payment_terms(
    db, invoice_id: str, payload: Optional[dict], user: dict
) -> dict:
    """Update only payment_term_days and/or due_date (+ stamp + audit).

    Does not touch amounts, lines, PO/GR links, HPP, stock, payments, or accounting.
    Rejects cancelled and LUNAS invoices. Re-checks payment status under lock.
    Audit is written before the document mutation; on mutation failure the audit is removed.
    """
    data = dict(payload or {})
    reason = str(data.get("reason") or data.get("note") or "").strip()
    if len(reason) < 3:
        raise HTTPException(400, "Alasan perubahan wajib diisi (minimal 3 karakter).")

    async with get_named_lock("purchase_invoice"):
        doc = await db[COLLECTION].find_one({"id": str(invoice_id)}, {"_id": 0})
        if not doc:
            doc = await db[COLLECTION].find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, f"Faktur Pembelian '{invoice_id}' tidak ditemukan.")
        if is_cancelled(doc):
            raise HTTPException(409, "Faktur yang dibatalkan tidak dapat diubah termin/jatuh temponya.")

        live = await attach_payment_fields(db, doc)
        if str(live.get("payment_status") or "") == "LUNAS":
            raise HTTPException(409, "Faktur yang sudah lunas tidak dapat diubah termin/jatuh temponya.")

        invoice_date = parse_required_ymd(
            live.get("invoice_date") or now_iso()[:10],
            "Tanggal faktur",
        )
        has_term = "payment_term_days" in data
        has_due = "due_date" in data
        if not has_term and not has_due:
            raise HTTPException(400, "Sertakan payment_term_days dan/atau due_date.")

        if has_term:
            term_days = parse_payment_term_days(data.get("payment_term_days"), required=True)
        else:
            raw_term = live.get("payment_term_days")
            term_days = parse_payment_term_days(raw_term if raw_term is not None else 0, required=False)

        if has_due:
            due_raw = data.get("due_date")
            if due_raw is None or str(due_raw).strip() == "":
                # Explicit clear not allowed for post-create edits under PUR-INV-01
                # (new invoices require due_date; legacy null stays until first edit sets it).
                raise HTTPException(400, "Tanggal jatuh tempo wajib diisi.")
            due_date = parse_required_ymd(due_raw, "Tanggal jatuh tempo")
        else:
            due_date = add_days_ymd(invoice_date, term_days)

        before = {
            "payment_term_days": live.get("payment_term_days"),
            "due_date": live.get("due_date"),
        }
        after = {
            "payment_term_days": term_days,
            "due_date": due_date,
        }
        if before.get("payment_term_days") == after["payment_term_days"] and str(before.get("due_date") or "") == after["due_date"]:
            return live

        history_entry = {
            "at": now_iso(),
            "by": actor_snapshot(user),
            "before": before,
            "after": after,
            "reason": reason,
        }

        audit_id = await _write_terms_audit(
            db,
            user=user,
            invoice_id=live["id"],
            invoice_number=str(live.get("invoice_number") or ""),
            before=before,
            after=after,
            reason=reason,
        )
        try:
            # Re-check LUNAS under the same lock immediately before write (race with settlement).
            paid_now = await _paid_total(db, live["id"])
            applied_now = await _advance_applied_total(db, live["id"])
            from services.purchase_return_finance import active_return_credit_breakdown
            from services.purchase_invoice_finance import invoice_financial_summary

            credit_now = await active_return_credit_breakdown(db, live["id"])
            status_now = invoice_financial_summary(
                invoice_amount=live.get("grand_total"),
                advance_applied=applied_now,
                payments_applied=paid_now,
                purchase_return_credit=credit_now["purchase_return_credit"],
                cancelled=False,
            )["payment_status"]
            if status_now == "LUNAS":
                await _delete_terms_audit(db, audit_id)
                raise HTTPException(409, "Faktur yang sudah lunas tidak dapat diubah termin/jatuh temponya.")

            updated = await db[COLLECTION].find_one_and_update(
                {"id": live["id"], "cancelled": {"$ne": True}},
                {
                    "$set": {
                        "payment_term_days": term_days,
                        "due_date": due_date,
                        **stamp_update(user),
                    },
                    "$push": {
                        HISTORY_FIELD: {
                            "$each": [history_entry],
                            "$slice": -HISTORY_CAP,
                        }
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated is None:
                await _delete_terms_audit(db, audit_id)
                current = await db[COLLECTION].find_one({"id": live["id"]}, {"_id": 0})
                if current and is_cancelled(current):
                    raise HTTPException(409, "Faktur yang dibatalkan tidak dapat diubah termin/jatuh temponya.")
                raise HTTPException(409, "Gagal mengubah termin/jatuh tempo karena perubahan bersamaan.")
        except HTTPException:
            raise
        except Exception:
            await _delete_terms_audit(db, audit_id)
            raise

        return await get_purchase_invoice(db, updated["id"])


async def cancel_purchase_invoice(
    db, invoice_id: str, user: dict, reason: str = "", *, claim_token: Optional[str] = None
) -> dict:
    """Soft-cancel unpaid invoice. PO invoice qty recalculates via active invoices only."""
    reason = str(reason or "").strip()

    async with get_named_lock("purchase_invoice"):
        doc = await db[COLLECTION].find_one({"id": str(invoice_id)}, {"_id": 0})
        if not doc:
            doc = await db[COLLECTION].find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, f"Faktur Pembelian '{invoice_id}' tidak ditemukan.")

        if is_cancelled(doc):
            return await get_purchase_invoice(db, doc["id"])

        from services.purchase_invoice_posting import (
            ACTIVE_PAYMENT_CANCEL_MESSAGE,
            CANCEL_CLAIM_MESSAGE,
            INVOICE_CANCEL_CLAIM_AT_FIELD,
            INVOICE_CANCEL_CLAIM_CONFLICT_MESSAGE,
            INVOICE_CANCEL_CLAIM_FIELD,
            PAYMENT_CREATE_CLAIM_CANCEL_MESSAGE,
            REVERSE_CLAIM_MESSAGE,
            acquire_invoice_cancel_claim,
            clear_invoice_cancel_claim,
            find_purchase_invoice_source_journal,
            invoice_cancel_claim_is_active,
            payment_create_claim_is_active,
            posting_claim_absent_or_stale_clause,
            posting_claim_is_active,
            raise_if_journal_blocks_cancel,
            restore_active_after_cancel_race,
            reverse_claim_is_active,
        )

        claimed = await acquire_invoice_cancel_claim(db, doc["id"], token=claim_token)
        if claimed is None:
            current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
            if current and is_cancelled(current):
                return await get_purchase_invoice(db, doc["id"])
            if current and payment_create_claim_is_active(current):
                raise HTTPException(409, PAYMENT_CREATE_CLAIM_CANCEL_MESSAGE)
            if current and posting_claim_is_active(current):
                raise HTTPException(409, CANCEL_CLAIM_MESSAGE)
            if current and reverse_claim_is_active(current):
                raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
            if current and invoice_cancel_claim_is_active(current):
                raise HTTPException(409, INVOICE_CANCEL_CLAIM_CONFLICT_MESSAGE)
            raced = await find_purchase_invoice_source_journal(db, doc["id"])
            raise_if_journal_blocks_cancel(raced)
            raise HTTPException(409, "Pembatalan faktur gagal karena perubahan bersamaan.")

        token = str(claimed.get(INVOICE_CANCEL_CLAIM_FIELD) or "")
        try:
            paid = await _paid_total(db, doc["id"])
            active_pays = await _active_payment_count(db, doc["id"])
            applied = await _advance_applied_total(db, doc["id"])
            if paid > 0 or active_pays > 0:
                raise HTTPException(409, ACTIVE_PAYMENT_CANCEL_MESSAGE)
            if applied > 0:
                raise HTTPException(409, "Faktur tidak dapat dihapus karena masih memiliki alokasi uang muka.")

            journal = await find_purchase_invoice_source_journal(db, doc["id"])
            raise_if_journal_blocks_cancel(journal)
            current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}) or claimed
            if posting_claim_is_active(current):
                raise HTTPException(409, CANCEL_CLAIM_MESSAGE)

            fields = cancellation_fields(user, reason)
            updated = await db[COLLECTION].find_one_and_update(
                {
                    "id": doc["id"],
                    "cancelled": {"$ne": True},
                    INVOICE_CANCEL_CLAIM_FIELD: token,
                    **posting_claim_absent_or_stale_clause(),
                },
                {
                    "$set": fields,
                    "$unset": {INVOICE_CANCEL_CLAIM_FIELD: "", INVOICE_CANCEL_CLAIM_AT_FIELD: ""},
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated is None:
                current = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
                if current and is_cancelled(current):
                    return await get_purchase_invoice(db, doc["id"])
                if current and posting_claim_is_active(current):
                    raise HTTPException(409, CANCEL_CLAIM_MESSAGE)
                raced = await find_purchase_invoice_source_journal(db, doc["id"])
                raise_if_journal_blocks_cancel(raced)
                raise HTTPException(409, "Pembatalan faktur gagal karena perubahan bersamaan.")

            raced_journal = await find_purchase_invoice_source_journal(db, doc["id"])
            if raced_journal:
                await restore_active_after_cancel_race(db, doc["id"])
                raise_if_journal_blocks_cancel(raced_journal)
            return await get_purchase_invoice(db, doc["id"])
        finally:
            if token:
                await clear_invoice_cancel_claim(db, doc["id"], token=token)
