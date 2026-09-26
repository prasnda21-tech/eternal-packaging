"""Customer-visible payment proofs for one live-tracking token.

Read-only. A proof is published only through the receipt → invoice → order
chain already used for public invoices. Customer name is not a lookup key.
Bytes stay behind the token route. Responses never include storage locations,
accounting, journals, or internal notes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services.customer_tracking_invoices import (
    customer_may_view_invoice,
    invoice_exclusive_to_order,
    invoice_id_usable,
)
from services.sales_receipt_payment_proof import (
    ALLOWED_MIME,
    find_proof_file,
    load_proof_bytes,
    proof_from_receipt,
    sniff_content_type,
)
from services.sales_receipt_posting import receipt_allocated_amount, receipt_is_cancelled

_PUBLIC_LIST_KEYS = (
    "receipt_id",
    "receipt_number",
    "invoice_number",
    "receipt_date",
    "amount",
    "status_label",
    "active",
    "file_name",
    "content_type",
)


def _norm(raw: Any) -> str:
    return str(raw or "").strip()


def _money(raw: Any) -> float:
    try:
        return float(round(float(raw if raw is not None else 0), 2))
    except (TypeError, ValueError):
        return 0.0


def receipt_id_usable(receipt_id: Optional[str]) -> bool:
    return invoice_id_usable(receipt_id)


def public_proof_status(receipt: Optional[dict]) -> Tuple[str, bool]:
    """Customer label. Reversed receipts stay visible and are not active payments."""
    if receipt_is_cancelled(receipt):
        return "Dibatalkan", False
    state = _norm((receipt or {}).get("payment_state")).upper()
    if state == "LUNAS":
        return "Lunas", True
    return "Sebagian", True


def public_proof_row(receipt: dict, file_doc: dict) -> Dict[str, Any]:
    label, active = public_proof_status(receipt)
    meta = proof_from_receipt(receipt) or {}
    amount = receipt_allocated_amount(receipt)
    row = {
        "receipt_id": _norm(receipt.get("id")),
        "receipt_number": _norm(receipt.get("receipt_number")),
        "invoice_number": _norm(receipt.get("invoice_number")),
        "receipt_date": _norm(receipt.get("receipt_date"))[:10],
        "amount": _money(amount),
        "status_label": label,
        "active": active,
        "file_name": _norm(file_doc.get("original_filename") or meta.get("name") or "bukti-pembayaran"),
        "content_type": _norm(file_doc.get("content_type") or meta.get("content_type")),
    }
    return {key: row[key] for key in _PUBLIC_LIST_KEYS}


async def _invoice_for_receipt(db, order: dict, receipt: dict) -> Optional[dict]:
    invoice_id = _norm(receipt.get("invoice_id"))
    order_id = _norm(order.get("id"))
    if not invoice_id or not order_id:
        return None
    if _norm(receipt.get("po_id")) != order_id:
        return None
    invoice = await db.sales_invoices.find_one(
        {"id": invoice_id, "po_id": order_id},
        {"_id": 0},
    )
    if not invoice:
        return None
    if not customer_may_view_invoice(invoice) or not invoice_exclusive_to_order(invoice, order):
        return None
    return invoice


async def _proof_file_for_receipt(db, order: dict, receipt: dict) -> Optional[dict]:
    meta = proof_from_receipt(receipt)
    if not meta or not _norm(meta.get("id")):
        return None
    file_doc = await find_proof_file(db, receipt_id=_norm(receipt.get("id")), file_id=_norm(meta.get("id")))
    if not file_doc or file_doc.get("is_deleted") is True:
        return None
    if _norm(file_doc.get("receipt_id")) != _norm(receipt.get("id")):
        return None
    if _norm(file_doc.get("invoice_id")) != _norm(receipt.get("invoice_id")):
        return None
    if _norm(file_doc.get("po_id")) != _norm(order.get("id")):
        return None
    if _norm(file_doc.get("id")) != _norm(meta.get("id")):
        return None
    return file_doc


async def visible_proofs_for_order(db, order: dict) -> List[dict]:
    order_id = _norm(order.get("id"))
    if not order_id:
        return []
    receipts = await db.sales_receipts.find(
        {"po_id": order_id},
        {"_id": 0},
    ).to_list(500)
    rows = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        invoice = await _invoice_for_receipt(db, order, receipt)
        if not invoice:
            continue
        file_doc = await _proof_file_for_receipt(db, order, receipt)
        if not file_doc:
            continue
        rows.append((receipt, file_doc))
    rows.sort(key=lambda pair: (
        _norm(pair[0].get("receipt_date")),
        _norm(pair[0].get("created_at")),
        _norm(pair[0].get("receipt_number")),
    ))
    return [public_proof_row(receipt, file_doc) for receipt, file_doc in rows]


async def public_payment_proof_list(db, order: dict) -> Dict[str, Any]:
    return {"proofs": await visible_proofs_for_order(db, order)}


async def public_payment_proof_bytes(db, order: dict, receipt_id: str) -> Optional[Tuple[bytes, str, str]]:
    """Return file bytes for one proof linked to this order, or None when refused."""
    if not receipt_id_usable(receipt_id):
        return None
    order_id = _norm(order.get("id"))
    receipt = await db.sales_receipts.find_one(
        {"id": _norm(receipt_id), "po_id": order_id},
        {"_id": 0},
    )
    if not receipt:
        return None
    if not await _invoice_for_receipt(db, order, receipt):
        return None
    if not await _proof_file_for_receipt(db, order, receipt):
        return None
    try:
        data, content_type, filename = await load_proof_bytes(db, receipt["id"])
    except HTTPException as exc:
        if exc.status_code == 415:
            raise
        return None
    sniffed = sniff_content_type(data, filename, content_type)
    if sniffed not in ALLOWED_MIME:
        raise HTTPException(415, "Jenis file tidak dapat ditampilkan.")
    return data, sniffed, filename
