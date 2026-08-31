"""Sales Invoice (Faktur Penjualan) Service.

Manages backend-authoritative sales invoices created from exact Sales Orders (PO).
Preserves exact PO snapshot values, enforces exact line identities (never merging
distinct lines with identical item names), supports partial invoicing (with cumulative
and remaining quantity checks), unit prices, nominal discounts, optional PPN (%),
payment terms (termin) with due date calculation, and derived payment status from
Penerimaan Penjualan (Sales Receipts).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

_invoice_locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _get_invoice_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _invoice_locks:
        _invoice_locks[loop] = asyncio.Lock()
    return _invoice_locks[loop]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_base_invoice_number(po_number: str) -> str:
    """Derives base invoice number from PO number while preserving main sequence."""
    pn = (po_number or "").strip()
    if not pn:
        return "INV-" + datetime.now(timezone.utc).strftime("%Y%m%d-001")
    
    # 1. Match PO/2026/08/30/000123 or SO/2026/08/30/000123 -> INV/2026/08/30/000123
    # 2. Match PO-20260830-001 or SO-20260830-001 -> INV-20260830-001
    # 3. Match PO.2026.08.001 -> INV.2026.08.001
    # 4. Match PO 12345 -> INV 12345
    # 5. Match PO12345 -> INV12345
    m = re.match(r'^(?:PO|SO)([\/\.\-\s_]?)(.*)$', pn, re.IGNORECASE)
    if m:
        sep = m.group(1)
        rest = m.group(2)
        return f"INV{sep}{rest}"
    
    # Other alphabetic prefixes with separator
    m2 = re.match(r'^[A-Za-z]+([\/\.\-\s_])(.*)$', pn)
    if m2:
        sep = m2.group(1)
        rest = m2.group(2)
        return f"INV{sep}{rest}"
    
    return f"INV-{pn}"


def derive_invoice_number(po_number: str, invoice_index: int) -> str:
    """Computes backend-authoritative invoice number for a specific invoice index.
    
    Index 1: base INV (e.g. INV/2026/08/31/000125)
    Index 2: base INV + "-2" (e.g. INV/2026/08/31/000125-2)
    Index 3: base INV + "-3" (e.g. INV/2026/08/31/000125-3)
    """
    base = derive_base_invoice_number(po_number)
    if invoice_index <= 1:
        return base
    return f"{base}-{invoice_index}"


def _parse_non_negative_price(raw: Any, field_name: str = "Harga satuan") -> float:
    """Strictly validates and converts numeric input to a non-negative float.
    Rejects malformed strings, None, negative numbers, etc.
    """
    if raw is None or raw == "":
        return 0.0
    try:
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{field_name} '{raw}' tidak valid. Format harus angka.")
    
    if d < Decimal("0"):
        raise HTTPException(400, f"{field_name} tidak boleh negatif.")
    
    return float(d)


async def get_po_invoice_preview(db, po_id_or_doc: Any, exclude_invoice_id: Optional[str] = None) -> Dict[str, Any]:
    """Generates an invoice snapshot preview from exact PO data.
    
    Computes cumulative invoiced quantity across all existing invoices for this PO,
    and determines the remaining quantity that can be invoiced per exact line item.
    """
    if isinstance(po_id_or_doc, dict):
        order = po_id_or_doc
    else:
        order = await db.orders.find_one({"id": str(po_id_or_doc)}, {"_id": 0})
        if not order:
            order = await db.orders.find_one({"po_number": str(po_id_or_doc)}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"Pesanan Penjualan (PO) '{po_id_or_doc}' tidak ditemukan.")

    po_id = order.get("id") or str(order.get("_id", ""))
    po_number = (order.get("po_number") or "").strip()
    order_date = order.get("order_date") or order.get("created_at") or ""
    customer = (order.get("customer") or order.get("customer_name") or "").strip()
    outlet = (order.get("outlet") or order.get("outlet_name") or "").strip()
    recipient = (order.get("recipient") or order.get("pic") or order.get("recipient_name") or "").strip()
    address = (order.get("address") or order.get("shipping_address") or order.get("delivery_address") or "").strip()
    delivery_zone = (order.get("delivery_zone") or order.get("zona_kirim") or order.get("zona") or order.get("shipping_zone") or "").strip()
    sales = (order.get("sales") or order.get("seller") or order.get("sales_rep") or order.get("sales_name") or "").strip()

    # Query all existing invoices for this PO to compute cumulative invoiced quantity per exact line
    invoice_filter: Dict[str, Any] = {"po_id": po_id}
    if exclude_invoice_id:
        invoice_filter["id"] = {"$ne": str(exclude_invoice_id)}
    
    existing_invoices = await db.sales_invoices.find(invoice_filter, {"_id": 0, "lines": 1}).to_list(500)
    
    # Cumulative invoiced per exact line_idx
    cumulative_by_line_idx: Dict[int, int] = {}
    for inv in existing_invoices:
        for l in inv.get("lines", []):
            try:
                l_idx = int(l.get("line_idx", 0))
                q = int(l.get("quantity", 0))
                cumulative_by_line_idx[l_idx] = cumulative_by_line_idx.get(l_idx, 0) + q
            except (ValueError, TypeError):
                continue

    raw_lines = order.get("line_items") or order.get("items") or []
    preview_lines = []
    total_ordered_qty = 0
    total_remaining_invoice_qty = 0

    for idx, li in enumerate(raw_lines):
        line_idx = int(li.get("line_idx", idx) if li.get("line_idx") is not None else idx)
        line_id = str(li.get("line_id") or li.get("id") or f"line_{idx}")
        item_name = str(li.get("name") or li.get("item_name") or "").strip()
        item_code = str(li.get("item_code") or "").strip()
        unit = str(li.get("unit") or "pcs").strip()
        color = str(li.get("color") or "").strip()
        
        try:
            ordered_qty = int(li.get("quantity") or 0)
        except (ValueError, TypeError):
            ordered_qty = 0

        invoiced_cumulative = cumulative_by_line_idx.get(line_idx, 0)
        remaining_invoice_qty = max(ordered_qty - invoiced_cumulative, 0)

        total_ordered_qty += ordered_qty
        total_remaining_invoice_qty += remaining_invoice_qty

        preview_lines.append({
            "line_idx": line_idx,
            "line_id": line_id,
            "item_name": item_name,
            "item_code": item_code,
            "color": color,
            "ordered_qty": ordered_qty,
            "invoiced_cumulative": invoiced_cumulative,
            "remaining_invoice_qty": remaining_invoice_qty,
            "quantity": remaining_invoice_qty,  # Default suggested qty to invoice
            "unit": unit,
            "unit_price": 0.0,
            "line_subtotal": 0.0,
            "line_total": 0.0,
        })

    return {
        "po_id": po_id,
        "po_number": po_number,
        "order_date": order_date,
        "customer": customer,
        "outlet": outlet,
        "recipient": recipient,
        "address": address,
        "delivery_zone": delivery_zone,
        "sales": sales,
        "total_ordered_qty": total_ordered_qty,
        "total_remaining_invoice_qty": total_remaining_invoice_qty,
        "total_qty": total_remaining_invoice_qty,
        "lines": preview_lines,
        "subtotal": 0.0,
        "discount_amount": 0.0,
        "after_discount": 0.0,
        "ppn_percent": 0.0,
        "ppn_amount": 0.0,
        "grand_total": 0.0,
        "payment_term_days": 0,
    }


async def create_sales_invoice(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Creates a new Sales Invoice (Faktur Penjualan) backend-authoritatively.
    
    Guarantees:
    - Exact PO snapshot
    - Exact line identity preserved (distinct lines with same item name stay distinct)
    - Partial invoicing: validate 0 < invoice_qty <= remaining_invoice_qty per exact line
    - Authoritative backend recalculation of subtotal, discount, after_discount, PPN, and grand_total
    - Payment term (termin) and due_date calculation
    - Rejection of negative / malformed prices or discounts or PPN
    - Duplicate submission protection via idempotency_key or internal locks
    - Zero mutation on PO or master collections
    """
    po_id = str(payload.get("po_id") or "").strip()
    if not po_id:
        raise HTTPException(400, "ID Pesanan Penjualan (po_id) wajib diisi.")

    async with _get_invoice_lock():
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if idempotency_key:
            existing_by_key = await db.sales_invoices.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
            if existing_by_key:
                return existing_by_key

        order = await db.orders.find_one({"id": po_id}, {"_id": 0})
        if not order:
            order = await db.orders.find_one({"po_number": po_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"Pesanan Penjualan (PO) '{po_id}' tidak ditemukan.")

        if order.get("cancelled"):
            raise HTTPException(400, "Pesanan Penjualan ini sudah dibatalkan.")

        # Compute preview and remaining allowable invoice quantities
        preview = await get_po_invoice_preview(db, order)
        preview_by_line_idx = {l["line_idx"]: l for l in preview["lines"]}

        input_lines = payload.get("lines") or payload.get("line_items") or []
        input_by_idx: Dict[int, Any] = {}
        for inp in input_lines:
            try:
                l_idx = int(inp.get("line_idx", 0))
            except (ValueError, TypeError):
                continue
            input_by_idx[l_idx] = inp

        calculated_lines = []
        authoritative_subtotal = 0.0
        total_invoice_qty = 0

        for l_idx, p_line in preview_by_line_idx.items():
            matched_input = input_by_idx.get(l_idx)
            if matched_input is None:
                if input_lines:
                    continue
                invoice_qty = p_line["remaining_invoice_qty"]
                raw_unit_price = 0
            else:
                raw_qty = matched_input.get("quantity")
                if raw_qty is None or raw_qty == "":
                    raw_qty = matched_input.get("invoice_qty")
                if raw_qty is None or raw_qty == "":
                    invoice_qty = p_line["remaining_invoice_qty"]
                else:
                    try:
                        invoice_qty = int(raw_qty)
                    except (ValueError, TypeError):
                        raise HTTPException(400, f"Kuantitas faktur untuk '{p_line['item_name']}' harus berupa bilangan bulat.")
                raw_unit_price = matched_input.get("unit_price", 0)

            if invoice_qty < 0:
                raise HTTPException(400, f"Kuantitas faktur untuk '{p_line['item_name']}' tidak boleh negatif.")

            # Skip lines with 0 quantity
            if invoice_qty == 0:
                continue

            remaining = p_line["remaining_invoice_qty"]
            if invoice_qty > remaining:
                raise HTTPException(
                    400,
                    f"Kuantitas faktur ({invoice_qty}) untuk '{p_line['item_name']}' melebihi sisa PO ({remaining} {p_line['unit']})."
                )

            unit_price = _parse_non_negative_price(raw_unit_price, f"Harga satuan '{p_line['item_name']}'")

            line_subtotal = round(float(Decimal(str(invoice_qty)) * Decimal(str(unit_price))), 2)
            authoritative_subtotal += line_subtotal
            total_invoice_qty += invoice_qty

            calculated_lines.append({
                "line_idx": l_idx,
                "line_id": p_line["line_id"],
                "item_name": p_line["item_name"],
                "item_code": p_line["item_code"],
                "color": p_line["color"],
                "ordered_qty": p_line["ordered_qty"],
                "invoiced_cumulative_before": p_line["invoiced_cumulative"],
                "remaining_invoice_qty_before": remaining,
                "quantity": invoice_qty,
                "unit": p_line["unit"],
                "unit_price": unit_price,
                "line_subtotal": line_subtotal,
                "line_total": line_subtotal,
            })

        if total_invoice_qty <= 0 or len(calculated_lines) == 0:
            raise HTTPException(400, "Total kuantitas faktur harus lebih besar dari 0.")

        authoritative_subtotal = round(authoritative_subtotal, 2)

        # Discount validation (nominal)
        discount_amount = _parse_non_negative_price(payload.get("discount_amount", 0), "Diskon")
        if discount_amount > authoritative_subtotal:
            raise HTTPException(400, f"Diskon (Rp {discount_amount:,.2f}) tidak boleh melebihi subtotal (Rp {authoritative_subtotal:,.2f}).")

        after_discount = round(float(Decimal(str(authoritative_subtotal)) - Decimal(str(discount_amount))), 2)

        # PPN validation (%)
        ppn_percent = _parse_non_negative_price(payload.get("ppn_percent", 0), "Persentase PPN")
        ppn_amount = round(float(Decimal(str(after_discount)) * (Decimal(str(ppn_percent)) / Decimal("100"))), 2)

        authoritative_grand_total = round(float(Decimal(str(after_discount)) + Decimal(str(ppn_amount))), 2)

        # Termin & Due Date
        raw_term = payload.get("payment_term_days", 0)
        try:
            payment_term_days = int(raw_term if raw_term is not None and str(raw_term).strip() != "" else 0)
        except (ValueError, TypeError):
            raise HTTPException(400, "Termin pembayaran harus berupa angka hari.")
        if payment_term_days < 0:
            raise HTTPException(400, "Termin pembayaran tidak boleh negatif.")

        invoice_date_str = str(payload.get("invoice_date") or now_iso()[:10]).strip()
        try:
            inv_dt = datetime.strptime(invoice_date_str[:10], "%Y-%m-%d")
        except ValueError:
            inv_dt = datetime.now(timezone.utc)
            invoice_date_str = inv_dt.strftime("%Y-%m-%d")

        due_date_str = (inv_dt + timedelta(days=payment_term_days)).strftime("%Y-%m-%d")

        # Derive invoice number based on existing count for exact po_id
        existing_invoices_count = await db.sales_invoices.count_documents({"po_id": preview["po_id"]})
        invoice_index = existing_invoices_count + 1
        candidate_number = derive_invoice_number(preview["po_number"], invoice_index)
        while await db.sales_invoices.find_one({"invoice_number": candidate_number}, {"_id": 1}):
            invoice_index += 1
            candidate_number = derive_invoice_number(preview["po_number"], invoice_index)
        invoice_number = candidate_number

        invoice_id = str(uuid.uuid4())
        invoice_doc = {
            "id": invoice_id,
            "invoice_number": invoice_number,
            "invoice_index": invoice_index,
            "idempotency_key": idempotency_key or None,
            "po_id": preview["po_id"],
            "po_number": preview["po_number"],
            "order_date": preview["order_date"],
            "invoice_date": invoice_date_str,
            "payment_term_days": payment_term_days,
            "due_date": due_date_str,
            "customer": preview["customer"],
            "outlet": preview["outlet"],
            "recipient": preview["recipient"],
            "address": preview["address"],
            "delivery_zone": preview["delivery_zone"],
            "sales": preview["sales"],
            "notes": str(payload.get("notes") or "").strip(),
            "lines": calculated_lines,
            "total_qty": total_invoice_qty,
            "subtotal": authoritative_subtotal,
            "discount_amount": discount_amount,
            "after_discount": after_discount,
            "ppn_percent": ppn_percent,
            "ppn_amount": ppn_amount,
            "grand_total": authoritative_grand_total,
            "paid_total": 0.0,
            "outstanding_amount": authoritative_grand_total,
            "payment_status": "BELUM DIBAYAR",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "created_by": {
                "id": str(user.get("id") or user.get("_id") or ""),
                "name": str(user.get("name") or user.get("email") or ""),
                "role": str(user.get("role") or ""),
            },
        }

        try:
            await db.sales_invoices.insert_one(invoice_doc)
        except DuplicateKeyError:
            if idempotency_key:
                existing = await db.sales_invoices.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
                if existing:
                    return existing
            raise HTTPException(409, "Faktur Penjualan duplikat terdeteksi.")

        invoice_doc.pop("_id", None)
        return invoice_doc


async def list_sales_invoices(db, q: Optional[str] = None, po_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all sales invoices with payment derived fields."""
    filters: Dict[str, Any] = {}
    if po_id:
        filters["po_id"] = str(po_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"id": {"$regex": needle, "$options": "i"}},
            {"invoice_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"customer": {"$regex": needle, "$options": "i"}},
            {"outlet": {"$regex": needle, "$options": "i"}},
            {"sales": {"$regex": needle, "$options": "i"}},
        ]

    invoices = await db.sales_invoices.find(filters, {"_id": 0}).sort("created_at", -1).to_list(1000)
    
    # Attach derived payment totals dynamically from db.sales_receipts
    inv_ids = [inv["id"] for inv in invoices if "id" in inv]
    if inv_ids:
        receipts = await db.sales_receipts.find({"invoice_id": {"$in": inv_ids}}, {"_id": 0}).to_list(5000)
        receipts_by_inv: Dict[str, List[dict]] = {}
        for r in receipts:
            receipts_by_inv.setdefault(r.get("invoice_id"), []).append(r)

        for inv in invoices:
            inv_receipts = receipts_by_inv.get(inv.get("id"), [])
            paid = round(sum(float(r.get("amount", 0)) for r in inv_receipts), 2)
            grand = float(inv.get("grand_total", 0))
            outstanding = max(round(grand - paid, 2), 0.0)
            
            if paid <= 0:
                p_status = "BELUM DIBAYAR"
            elif paid < grand:
                p_status = "DP"
            else:
                p_status = "LUNAS"

            inv["paid_total"] = paid
            inv["outstanding_amount"] = outstanding
            inv["payment_status"] = p_status

    return invoices


async def get_sales_invoice_detail(db, invoice_id: str) -> Dict[str, Any]:
    """Fetch detail of a single sales invoice with live payment derived fields and receipt list."""
    inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not inv:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

    # Fetch live receipts for exact invoice
    receipts = await db.sales_receipts.find({"invoice_id": inv["id"]}, {"_id": 0}).sort("created_at", 1).to_list(500)
    paid = round(sum(float(r.get("amount", 0)) for r in receipts), 2)
    grand = float(inv.get("grand_total", 0))
    outstanding = max(round(grand - paid, 2), 0.0)

    if paid <= 0:
        p_status = "BELUM DIBAYAR"
    elif paid < grand:
        p_status = "DP"
    else:
        p_status = "LUNAS"

    inv["paid_total"] = paid
    inv["outstanding_amount"] = outstanding
    inv["payment_status"] = p_status
    inv["receipts"] = receipts
    return inv


async def update_sales_invoice(db, invoice_id: str, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Updates an existing sales invoice (unit prices, discount, PPN, termin, notes, invoice_date).
    
    Recalculates line totals, discount, PPN, and grand total authoritatively on backend.
    Preserves exact PO lines and never mutates PO or Master collections.
    """
    async with _get_invoice_lock():
        inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
        if not inv:
            inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
        if not inv:
            raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

        existing_lines = inv.get("lines") or []
        input_lines = payload.get("lines") or payload.get("line_items") or []
        input_by_idx = {int(inp.get("line_idx", idx)): inp for idx, inp in enumerate(input_lines)}

        updated_lines = []
        authoritative_subtotal = 0.0
        total_invoice_qty = 0

        for idx, el in enumerate(existing_lines):
            line_idx = int(el.get("line_idx", idx))
            qty = int(el.get("quantity") or 0)
            
            if line_idx in input_by_idx:
                raw_price = input_by_idx[line_idx].get("unit_price")
                unit_price = _parse_non_negative_price(raw_price, f"Harga satuan '{el.get('item_name')}'")
            else:
                unit_price = _parse_non_negative_price(el.get("unit_price", 0), f"Harga satuan '{el.get('item_name')}'")

            line_subtotal = round(float(Decimal(str(qty)) * Decimal(str(unit_price))), 2)
            authoritative_subtotal += line_subtotal
            total_invoice_qty += qty

            updated_el = dict(el)
            updated_el["unit_price"] = unit_price
            updated_el["line_subtotal"] = line_subtotal
            updated_el["line_total"] = line_subtotal
            updated_lines.append(updated_el)

        authoritative_subtotal = round(authoritative_subtotal, 2)

        # Discount
        raw_disc = payload.get("discount_amount") if "discount_amount" in payload else inv.get("discount_amount", 0)
        discount_amount = _parse_non_negative_price(raw_disc, "Diskon")
        if discount_amount > authoritative_subtotal:
            raise HTTPException(400, f"Diskon (Rp {discount_amount:,.2f}) tidak boleh melebihi subtotal (Rp {authoritative_subtotal:,.2f}).")

        after_discount = round(float(Decimal(str(authoritative_subtotal)) - Decimal(str(discount_amount))), 2)

        # PPN
        raw_ppn = payload.get("ppn_percent") if "ppn_percent" in payload else inv.get("ppn_percent", 0)
        ppn_percent = _parse_non_negative_price(raw_ppn, "Persentase PPN")
        ppn_amount = round(float(Decimal(str(after_discount)) * (Decimal(str(ppn_percent)) / Decimal("100"))), 2)

        authoritative_grand_total = round(float(Decimal(str(after_discount)) + Decimal(str(ppn_amount))), 2)

        # Termin & Due Date
        raw_term = payload.get("payment_term_days") if "payment_term_days" in payload else inv.get("payment_term_days", 0)
        try:
            payment_term_days = int(raw_term if raw_term is not None and str(raw_term).strip() != "" else 0)
        except (ValueError, TypeError):
            raise HTTPException(400, "Termin pembayaran harus berupa angka hari.")
        if payment_term_days < 0:
            raise HTTPException(400, "Termin pembayaran tidak boleh negatif.")

        invoice_date_str = str(payload.get("invoice_date") or inv.get("invoice_date") or now_iso()[:10]).strip()
        try:
            inv_dt = datetime.strptime(invoice_date_str[:10], "%Y-%m-%d")
        except ValueError:
            inv_dt = datetime.now(timezone.utc)
            invoice_date_str = inv_dt.strftime("%Y-%m-%d")

        due_date_str = (inv_dt + timedelta(days=payment_term_days)).strftime("%Y-%m-%d")

        update_fields = {
            "lines": updated_lines,
            "total_qty": total_invoice_qty,
            "subtotal": authoritative_subtotal,
            "discount_amount": discount_amount,
            "after_discount": after_discount,
            "ppn_percent": ppn_percent,
            "ppn_amount": ppn_amount,
            "grand_total": authoritative_grand_total,
            "payment_term_days": payment_term_days,
            "invoice_date": invoice_date_str,
            "due_date": due_date_str,
            "updated_at": now_iso(),
            "updated_by": {
                "id": str(user.get("id") or user.get("_id") or ""),
                "name": str(user.get("name") or user.get("email") or ""),
                "role": str(user.get("role") or ""),
            },
        }

        if "notes" in payload:
            update_fields["notes"] = str(payload["notes"] or "").strip()

        await db.sales_invoices.update_one({"id": inv["id"]}, {"$set": update_fields})

        return await get_sales_invoice_detail(db, inv["id"])


async def delete_sales_invoice(db, invoice_id: str, user: dict) -> dict:
    """Delete a single sales invoice if safe (no PAY children)."""
    inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        raise HTTPException(404, "Faktur Penjualan tidak ditemukan.")

    # Check PAY children
    pay_count = await db.sales_receipts.count_documents({"invoice_id": inv["id"]})
    if pay_count > 0:
        raise HTTPException(400, "Hapus Penerimaan Penjualan terlebih dahulu.")

    inv_number = inv.get("invoice_number", inv["id"])
    await db.sales_invoices.delete_one({"id": inv["id"]})
    return {"deleted": True, "invoice_number": inv_number, "id": inv["id"]}


async def bulk_delete_sales_invoices(db, ids: list, user: dict) -> dict:
    """Bulk delete invoices — ALL OR NOTHING. Pre-validate all before deleting any."""
    unique_ids = list(dict.fromkeys(ids))
    invoices = await db.sales_invoices.find({"id": {"$in": unique_ids}}, {"_id": 0}).to_list(len(unique_ids) or 1)
    by_id = {inv["id"]: inv for inv in invoices}

    blockers = []
    to_delete = []

    for iid in unique_ids:
        inv = by_id.get(iid)
        if not inv:
            blockers.append({"id": iid, "number": iid, "reason": "Faktur tidak ditemukan."})
            continue
        pay_count = await db.sales_receipts.count_documents({"invoice_id": inv["id"]})
        if pay_count > 0:
            blockers.append({"id": inv["id"], "number": inv.get("invoice_number", inv["id"]), "reason": "Hapus Penerimaan Penjualan terlebih dahulu."})
            continue
        to_delete.append(inv)

    if blockers:
        raise HTTPException(400, {"message": "Tidak dapat menghapus. Beberapa faktur memiliki dependensi.", "blockers": blockers})

    if to_delete:
        delete_ids = [inv["id"] for inv in to_delete]
        await db.sales_invoices.delete_many({"id": {"$in": delete_ids}})

    return {
        "deleted_count": len(to_delete),
        "deleted": [{"id": inv["id"], "invoice_number": inv.get("invoice_number", "")} for inv in to_delete],
    }
