"""Purchase Invoice (Faktur Pembelian) from exact Pesanan Pembelian lines.

Invoice/payment amounts do not rewrite PO HPP unit prices.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.purchase_common import (
    PREFIX_PURCHASE_INVOICE,
    actor_snapshot,
    derive_child_number,
    get_named_lock,
    insert_with_unique_number,
    line_subtotal,
    money_round,
    now_iso,
    parse_qty,
    qty_le,
)
from services.purchase_orders import get_purchase_order

COLLECTION = "purchase_invoices"
PAY_COLLECTION = "purchase_payments"


def payment_status(grand_total: float, paid_total: float) -> str:
    paid = money_round(paid_total)
    grand = money_round(grand_total)
    if paid <= 0:
        return "BELUM DIBAYAR"
    if paid < grand:
        return "DP"
    return "LUNAS"


async def _paid_total(db, invoice_id: str) -> float:
    rows = await db[PAY_COLLECTION].find({"purchase_invoice_id": invoice_id}, {"_id": 0, "amount": 1}).to_list(2000)
    return money_round(sum(float(r.get("amount") or 0) for r in rows))


async def attach_payment_fields(db, inv: dict) -> dict:
    out = dict(inv)
    paid = await _paid_total(db, out["id"])
    grand = float(out.get("grand_total") or 0)
    out["paid_total"] = paid
    out["outstanding_amount"] = max(money_round(grand - paid), 0.0)
    out["payment_status"] = payment_status(grand, paid)
    return out


async def get_po_invoice_preview(db, purchase_order_id: str, exclude_invoice_id: Optional[str] = None) -> dict:
    po = await get_purchase_order(db, purchase_order_id)
    if exclude_invoice_id:
        # recompute remaining excluding one invoice — used only if we later support edit
        po = await get_purchase_order(db, purchase_order_id)
    lines = []
    for li in po.get("lines") or []:
        remain = float(li.get("remaining_invoice_qty") or 0)
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
            "cumulative_invoiced_qty": li.get("cumulative_invoiced_qty") or 0,
            "remaining_invoice_qty": remain,
            "invoice_qty": qty,
            "unit_price": unit_price,
            "line_subtotal": line_subtotal(qty, unit_price),
        })
    return {
        "purchase_order_id": po["id"],
        "po_number": po.get("po_number"),
        "request_id": po.get("request_id"),
        "request_number": po.get("request_number"),
        "supplier": po.get("supplier"),
        "order_date": po.get("order_date"),
        "lines": lines,
        "invoice_progress": po.get("invoice_progress") or {},
    }


async def create_purchase_invoice(db, payload: Dict[str, Any], user: dict) -> dict:
    po_id = str(payload.get("purchase_order_id") or "").strip()
    if not po_id:
        raise HTTPException(400, "ID Pesanan Pembelian (purchase_order_id) wajib diisi.")

    async with get_named_lock("purchase_invoice"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                return await attach_payment_fields(db, existing)

        preview = await get_po_invoice_preview(db, po_id)
        by_line = {str(l["purchase_order_line_id"]): l for l in preview["lines"]}
        input_lines = payload.get("lines") or payload.get("line_items") or []
        calculated = []
        total = 0.0
        seen = set()

        sources = input_lines if input_lines else preview["lines"]
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
                raw_qty = src["remaining_invoice_qty"]
            if raw_qty in (None, "") or float(raw_qty or 0) == 0:
                continue
            qty = parse_qty(raw_qty, f"Qty faktur '{src['item_name']}'")
            remain = float(src["remaining_invoice_qty"] or 0)
            if not qty_le(qty, remain):
                raise HTTPException(
                    400,
                    f"Qty faktur ({qty}) untuk '{src['item_name']}' melebihi sisa PO ({remain}).",
                )
            # Prefill from PO; ignore client rewrite of HPP source
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

        ts = now_iso()
        total = money_round(total)
        existing_count = await db[COLLECTION].count_documents({"purchase_order_id": preview["purchase_order_id"]})
        doc = {
            "id": str(uuid.uuid4()),
            "purchase_order_id": preview["purchase_order_id"],
            "po_number": preview["po_number"],
            "request_id": preview.get("request_id"),
            "request_number": preview.get("request_number"),
            "supplier": preview.get("supplier"),
            "invoice_date": str(payload.get("invoice_date") or ts[:10]).strip()[:10],
            "notes": str(payload.get("notes") or "").strip(),
            "lines": calculated,
            "subtotal": total,
            "grand_total": total,
            "created_at": ts,
            "updated_at": ts,
            "created_by": actor_snapshot(user),
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
        return await attach_payment_fields(db, saved)


async def list_purchase_invoices(
    db,
    q: Optional[str] = None,
    purchase_order_id: Optional[str] = None,
    with_outstanding: bool = False,
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
    rows = await db[COLLECTION].find(filters, {"_id": 0}).sort("created_at", -1).to_list(1000)
    out = []
    for row in rows:
        enriched = await attach_payment_fields(db, row)
        if with_outstanding and float(enriched.get("outstanding_amount") or 0) <= 0:
            continue
        out.append(enriched)
    return out


async def get_purchase_invoice(db, invoice_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(invoice_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Faktur Pembelian '{invoice_id}' tidak ditemukan.")
    inv = await attach_payment_fields(db, doc)
    payments = await db[PAY_COLLECTION].find(
        {"purchase_invoice_id": inv["id"]}, {"_id": 0}
    ).sort("created_at", 1).to_list(500)
    inv["payments"] = payments
    return inv
