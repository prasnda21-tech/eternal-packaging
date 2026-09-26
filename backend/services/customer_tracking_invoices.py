"""Customer-visible sales invoices for a live-tracking token.

Publication rule (read-only):
- A tracking token may see an invoice only when it is exclusively tied to that
  order's id (po_id), the stored PO number matches, and the customer name does
  not point at someone else.
- Draft and unknown financial_status stay internal. Missing status is draft.
- Issued documents are financial_status finalized. Cancelled issued documents
  stay visible as Batal so the lifecycle is not silently dropped.
- Journal posting is ignored. accounting_posted never publishes a draft and is
  not required for a finalized invoice.
- Totals and outstanding come from the stored invoice plus active receipts
  (apply_invoice_payment_fields). This module never writes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.sales_invoices import (
    FINANCIAL_CANCELLED,
    FINANCIAL_FINALIZED,
    apply_invoice_payment_fields,
    effective_financial_status,
    generate_sales_invoice_pdf_bytes,
    invoice_sales_label,
)

PUBLIC_INVOICE_STATUSES = frozenset({FINANCIAL_FINALIZED, FINANCIAL_CANCELLED})
_FOREIGN_LIST_KEYS = ("po_ids", "order_ids", "source_po_ids", "linked_po_ids")
_RECEIPT_FIELDS = {"_id": 0, "invoice_id": 1, "amount": 1, "financial_status": 1, "cancelled": 1}


def _money(raw: Any) -> float:
    try:
        return float(round(float(raw if raw is not None else 0), 2))
    except (TypeError, ValueError):
        return 0.0


def _norm(raw: Any) -> str:
    return str(raw or "").strip()


def invoice_id_usable(invoice_id: Optional[str]) -> bool:
    iid = _norm(invoice_id)
    if not iid or len(iid) > 80:
        return False
    if any(ch.isspace() for ch in iid):
        return False
    if "/" in iid or "\\" in iid or ".." in iid:
        return False
    return True


def customer_may_view_invoice(invoice: Optional[dict]) -> bool:
    """Issued financial documents only. Posting a journal is not publication."""
    return effective_financial_status(invoice) in PUBLIC_INVOICE_STATUSES


def customer_payment_label(invoice: Optional[dict]) -> str:
    """Customer copy. Cancelled lifecycle wins over the internal AR label."""
    if effective_financial_status(invoice) == FINANCIAL_CANCELLED:
        return "Batal"
    raw = _norm((invoice or {}).get("payment_status")).upper()
    if raw == "LUNAS":
        return "Lunas"
    if raw == "DP":
        return "Sebagian"
    return "Belum lunas"


def invoice_exclusive_to_order(invoice: Optional[dict], order: Optional[dict]) -> bool:
    """True only when the invoice cannot describe another order or customer."""
    invoice = invoice or {}
    order = order or {}
    order_id = _norm(order.get("id"))
    po_number = _norm(order.get("po_number"))
    if not order_id:
        return False
    if _norm(invoice.get("po_id")) != order_id:
        return False
    inv_po = _norm(invoice.get("po_number"))
    if po_number and inv_po and inv_po != po_number:
        return False
    order_customer = _norm(order.get("customer") or order.get("customer_name")).casefold()
    inv_customer = _norm(invoice.get("customer")).casefold()
    if order_customer and inv_customer and order_customer != inv_customer:
        return False
    allowed_refs = {order_id}
    if po_number:
        allowed_refs.add(po_number)
    for key in _FOREIGN_LIST_KEYS:
        raw = invoice.get(key)
        if raw is None:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for item in values:
            ref = item.get("id") if isinstance(item, dict) else item
            text = _norm(ref)
            if text and text not in allowed_refs:
                return False
    for line in invoice.get("lines") or []:
        if not isinstance(line, dict):
            continue
        for key in ("po_id", "order_id"):
            text = _norm(line.get(key))
            if text and text != order_id:
                return False
        line_po = _norm(line.get("po_number"))
        if po_number and line_po and line_po != po_number:
            return False
    return True


def _pdf_path(token: str, invoice_id: str) -> str:
    return f"/api/public/tracking/{token}/invoices/{invoice_id}/pdf?disposition=attachment"


def public_invoice_summary(invoice: dict, token: str) -> Dict[str, Any]:
    iid = _norm(invoice.get("id"))
    row: Dict[str, Any] = {
        "id": iid,
        "invoice_number": _norm(invoice.get("invoice_number")),
        "invoice_date": _norm(invoice.get("invoice_date"))[:10],
        "grand_total": _money(invoice.get("grand_total")),
        "outstanding_amount": _money(invoice.get("outstanding_amount")),
        "paid_total": _money(invoice.get("paid_total")),
        "payment_label": customer_payment_label(invoice),
        "financial_status": effective_financial_status(invoice),
        "pdf_url": _pdf_path(token, iid),
    }
    due = _norm(invoice.get("due_date"))
    if due:
        row["due_date"] = due[:10]
    return row


def public_invoice_detail(invoice: dict, token: str, order: dict) -> Dict[str, Any]:
    row = public_invoice_summary(invoice, token)
    row["order_number"] = _norm(order.get("po_number"))
    row["subtotal"] = _money(invoice.get("subtotal"))
    row["discount_amount"] = _money(invoice.get("discount_amount"))
    row["ppn_percent"] = _money(invoice.get("ppn_percent"))
    row["ppn_amount"] = _money(invoice.get("ppn_amount"))
    lines: List[Dict[str, Any]] = []
    for li in invoice.get("lines") or []:
        if not isinstance(li, dict):
            continue
        name = _norm(li.get("item_name") or li.get("name"))
        if not name:
            continue
        item: Dict[str, Any] = {
            "item_name": name,
            "quantity": li.get("quantity"),
            "unit_price": _money(li.get("unit_price")),
            "line_subtotal": _money(li.get("line_subtotal")),
        }
        unit = _norm(li.get("unit"))
        if unit:
            item["unit"] = unit
        lines.append(item)
    row["lines"] = lines
    return row


def public_charge_lines(invoice: Optional[dict]) -> List[dict]:
    """Billing rows the customer may see. Account id, code, and name stay behind."""
    rows: List[dict] = []
    for raw in (invoice or {}).get("other_charges") or []:
        if not isinstance(raw, dict):
            continue
        description = _norm(raw.get("description"))
        amount = _money(raw.get("amount"))
        if not description or amount <= 0:
            continue
        rows.append({"description": description, "amount": amount})
    return rows


def customer_invoice_pdf_source(invoice: dict, order: dict) -> Dict[str, Any]:
    """Fields the existing PDF renderer is allowed to see.

    `notes` stays empty. The internal invoice PDF already prints that field,
    which does not show that every historical note is safe to publish.
    Sales is the invoice snapshot only. Charge rows are an explicit allowlist.
    """
    lines = []
    for li in invoice.get("lines") or []:
        if not isinstance(li, dict):
            continue
        lines.append({
            "item_name": _norm(li.get("item_name") or li.get("name")) or "-",
            "quantity": li.get("quantity") or 0,
            "unit": _norm(li.get("unit")),
            "unit_price": _money(li.get("unit_price")),
            "line_subtotal": _money(li.get("line_subtotal")),
        })
    return {
        "invoice_number": _norm(invoice.get("invoice_number")),
        "po_number": _norm(order.get("po_number")),
        "customer": _norm(order.get("customer") or order.get("customer_name")),
        "address": "",
        "sales": invoice_sales_label(invoice),
        "invoice_date": _norm(invoice.get("invoice_date"))[:10],
        "payment_term_days": invoice.get("payment_term_days") or 0,
        "due_date": _norm(invoice.get("due_date"))[:10],
        "notes": "",
        "payment_status": customer_payment_label(invoice),
        "lines": lines,
        "other_charges": public_charge_lines(invoice),
        "subtotal": _money(invoice.get("subtotal")),
        "discount_amount": _money(invoice.get("discount_amount")),
        "ppn_percent": invoice.get("ppn_percent") or 0,
        "ppn_amount": _money(invoice.get("ppn_amount")),
        "grand_total": _money(invoice.get("grand_total")),
        "paid_total": _money(invoice.get("paid_total")),
        "outstanding_amount": _money(invoice.get("outstanding_amount")),
    }


def render_customer_invoice_pdf(invoice: dict, order: dict) -> bytes:
    return generate_sales_invoice_pdf_bytes(customer_invoice_pdf_source(invoice, order))


async def _attach_receipts(db, invoices: List[dict]) -> None:
    ids = [inv.get("id") for inv in invoices if inv.get("id")]
    if not ids:
        return
    receipts = await db.sales_receipts.find(
        {"invoice_id": {"$in": ids}},
        _RECEIPT_FIELDS,
    ).to_list(2000)
    grouped: Dict[str, List[dict]] = {}
    for rec in receipts:
        grouped.setdefault(rec.get("invoice_id"), []).append(rec)
    for inv in invoices:
        apply_invoice_payment_fields(inv, grouped.get(inv.get("id"), []))


def _sort_key(invoice: dict):
    try:
        index = int(invoice.get("invoice_index") or 0)
    except (TypeError, ValueError):
        index = 0
    return (index, _norm(invoice.get("invoice_date")), _norm(invoice.get("invoice_number")))


async def visible_invoices_for_order(db, order: dict) -> List[dict]:
    order_id = _norm(order.get("id"))
    if not order_id:
        return []
    found = await db.sales_invoices.find({"po_id": order_id}, {"_id": 0}).to_list(200)
    visible = [
        inv for inv in found
        if customer_may_view_invoice(inv) and invoice_exclusive_to_order(inv, order)
    ]
    visible.sort(key=_sort_key)
    await _attach_receipts(db, visible)
    return visible


async def visible_invoice_for_order(db, order: dict, invoice_id: str) -> Optional[dict]:
    if not invoice_id_usable(invoice_id):
        return None
    order_id = _norm(order.get("id"))
    if not order_id:
        return None
    inv = await db.sales_invoices.find_one(
        {"id": _norm(invoice_id), "po_id": order_id},
        {"_id": 0},
    )
    if not inv:
        return None
    if not customer_may_view_invoice(inv) or not invoice_exclusive_to_order(inv, order):
        return None
    await _attach_receipts(db, [inv])
    return inv


async def public_invoice_list(db, order: dict, token: str) -> Dict[str, Any]:
    rows = await visible_invoices_for_order(db, order)
    return {"invoices": [public_invoice_summary(inv, token) for inv in rows]}


async def public_invoice_detail_payload(db, order: dict, token: str, invoice_id: str) -> Optional[dict]:
    inv = await visible_invoice_for_order(db, order, invoice_id)
    if not inv:
        return None
    return public_invoice_detail(inv, token, order)


async def public_invoice_pdf_bytes(db, order: dict, invoice_id: str) -> Optional[bytes]:
    inv = await visible_invoice_for_order(db, order, invoice_id)
    if not inv:
        return None
    return render_customer_invoice_pdf(inv, order)
