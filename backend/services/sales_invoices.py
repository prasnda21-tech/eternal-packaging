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
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

FINANCIAL_DRAFT = "draft"
FINANCIAL_FINALIZED = "finalized"
FINANCIAL_CANCELLED = "cancelled"
FINANCIAL_STATUSES = frozenset({FINANCIAL_DRAFT, FINANCIAL_FINALIZED, FINANCIAL_CANCELLED})

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
        return "INV/" + datetime.now(timezone.utc).strftime("%d/%m/%y/001")

    # 1. Match PO/04/09/26/001 or SO/04/09/26/001 -> INV/04/09/26/001
    # 2. Match PO/2026/08/30/000123 or SO/2026/08/30/000123 -> INV/2026/08/30/000123
    # 3. Match PO-20260830-001 or SO-20260830-001 -> INV-20260830-001
    # 4. Match PO.2026.08.001 -> INV.2026.08.001
    # 5. Match PO 12345 -> INV 12345
    # 6. Match PO12345 -> INV12345
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

    Index 1: base INV (e.g. INV/04/09/26/001 or INV-20260830-001)
    Index 2: base INV + "_2" (e.g. INV/04/09/26/001_2)
    Index 3: base INV + "_3" (e.g. INV/04/09/26/001_3)
    """
    base = derive_base_invoice_number(po_number)
    if invoice_index <= 1:
        return base
    return f"{base}_{invoice_index}"


def stored_order_unit_price(line: Optional[dict]) -> Optional[float]:
    """A missing order price is None, never an implied zero."""
    if not isinstance(line, dict) or "unit_price" not in line:
        return None
    raw = line.get("unit_price")
    if raw is None or raw == "":
        return None
    return _parse_non_negative_price(raw, "Harga jual pesanan")


def _require_explicit_price(raw: Any, field_name: str) -> float:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        raise HTTPException(
            400,
            f"{field_name} wajib diisi. Harga kosong tidak dijadikan Rp0.",
        )
    return _parse_non_negative_price(raw, field_name)


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


def _money2(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


# Biaya Lainnya is billed to the customer and credited to the selected account.
# Existing discount and PPN rules stay on goods only: discount cannot exceed the
# goods subtotal, and PPN is after_discount × percent. No rule applies either
# one to these charges, so they are added after that goods total.
OTHER_CHARGE_ACCOUNT_TYPES = frozenset({"EXPS", "OEXP", "OINC", "REVE"})
_INACTIVE_ACCOUNT_STATUSES = frozenset({"inactive", "nonaktif", "disabled", "non-aktif"})


def other_charges_total(invoice: Optional[dict]) -> Decimal:
    rows = (invoice or {}).get("other_charges") or []
    if not isinstance(rows, list):
        return Decimal("0.00")
    total = Decimal("0.00")
    for row in rows:
        if isinstance(row, dict):
            total += _money2(row.get("amount"))
    return total.quantize(Decimal("0.01"))


def invoice_sales_label(invoice: Optional[dict]) -> str:
    """Sales name snapshotted from the order. Never guessed from the actor."""
    for key in ("sales", "sales_name"):
        value = str((invoice or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def _account_status_text(account: dict) -> str:
    return str(account.get("status") or account.get("account_status") or "").strip().lower()


def _charge_account_blocked_reason(account: Optional[dict]) -> Optional[str]:
    if not account:
        return "tidak ditemukan"
    if _account_status_text(account) in _INACTIVE_ACCOUNT_STATUSES:
        return "tidak aktif"
    if account.get("is_active") is False or account.get("active") is False:
        return "tidak aktif"
    if account.get("is_header") is True or account.get("header") is True:
        return "tidak dapat diposting"
    if account.get("postable") is False or account.get("can_post") is False:
        return "tidak dapat diposting"
    account_type = str(account.get("account_type") or "").strip().upper()
    if account_type not in OTHER_CHARGE_ACCOUNT_TYPES:
        return "tidak diizinkan"
    return None


def _charge_account_message(row_no: int, reason: str, account: Optional[dict]) -> str:
    code = str((account or {}).get("code") or "").strip()
    name = str((account or {}).get("name") or "").strip()
    label = " ".join(part for part in (code, name) if part).strip()
    prefix = f"Baris biaya {row_no}"
    if reason == "tidak ditemukan":
        return f"{prefix}: akun perkiraan tidak ditemukan."
    if reason == "tidak aktif":
        shown = label or "yang dipilih"
        return f"{prefix}: akun {shown} tidak aktif."
    if reason == "tidak dapat diposting":
        shown = label or "yang dipilih"
        return f"{prefix}: akun {shown} tidak dapat diposting."
    shown = label or "yang dipilih"
    return (
        f"{prefix}: akun {shown} tidak diizinkan untuk Biaya Lainnya. "
        "Pilih akun beban, beban lain-lain, pendapatan, atau pendapatan lain-lain yang aktif."
    )


async def _load_ledger_account(db, account_id: str) -> Optional[dict]:
    from services.chart_of_accounts import COLLECTION

    aid = str(account_id or "").strip()
    if not aid:
        return None
    return await db[COLLECTION].find_one({"id": aid}, {"_id": 0})


def _parse_charge_amount(raw: Any, row_no: int) -> float:
    label = f"Baris biaya {row_no}: nominal"
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        raise HTTPException(400, f"{label} wajib diisi.")
    if isinstance(raw, bool):
        raise HTTPException(400, f"{label} tidak valid. Format harus angka.")
    try:
        amount = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"{label} tidak valid. Format harus angka.")
    if amount < 0:
        raise HTTPException(400, f"{label} tidak boleh negatif.")
    if amount == 0:
        raise HTTPException(400, f"{label} harus lebih besar dari 0.")
    return float(amount.quantize(Decimal("0.01")))


async def normalize_other_charges(db, raw: Any) -> List[dict]:
    """Validate every submitted charge row. Incomplete rows are rejected, not dropped."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(400, "Biaya lainnya harus berupa daftar baris.")
    normalized: List[dict] = []
    for index, row in enumerate(raw):
        row_no = index + 1
        if not isinstance(row, dict):
            raise HTTPException(400, f"Baris biaya {row_no} belum lengkap: akun, keterangan, dan nominal wajib diisi.")
        account_id = str(row.get("account_id") or "").strip()
        description = str(row.get("description") if row.get("description") is not None else row.get("keterangan") or "").strip()
        amount_raw = row.get("amount")
        missing = []
        if not account_id:
            missing.append("akun perkiraan")
        if not description:
            missing.append("keterangan")
        amount_blank = amount_raw is None or (isinstance(amount_raw, str) and not str(amount_raw).strip())
        if amount_blank:
            missing.append("nominal")
        if missing:
            raise HTTPException(
                400,
                f"Baris biaya {row_no} belum lengkap: {', '.join(missing)} wajib diisi.",
            )
        account = await _load_ledger_account(db, account_id)
        reason = _charge_account_blocked_reason(account)
        if reason:
            raise HTTPException(400, _charge_account_message(row_no, reason, account))
        amount = _parse_charge_amount(amount_raw, row_no)
        line_id = str(row.get("id") or "").strip() or str(uuid.uuid4())
        normalized.append({
            "id": line_id,
            "account_id": str(account["id"]),
            "account_code": str(account.get("code") or ""),
            "account_name": str(account.get("name") or ""),
            "account_type": str(account.get("account_type") or "").strip().upper(),
            "description": description,
            "amount": amount,
        })
    return normalized


async def list_charge_account_options(db, q: Optional[str] = None) -> List[dict]:
    """Active, postable COA accounts allowed on a sales-invoice other charge. Zero-write."""
    from services.chart_of_accounts import COLLECTION

    query: Dict[str, Any] = {}
    needle = (q or "").strip()
    if needle:
        safe = re.escape(needle)
        query["$or"] = [
            {"code": {"$regex": safe, "$options": "i"}},
            {"name": {"$regex": safe, "$options": "i"}},
        ]
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort("code", 1).to_list(5000)
    items = []
    for doc in docs:
        if _charge_account_blocked_reason(doc):
            continue
        items.append({
            "id": doc.get("id"),
            "code": str(doc.get("code") or ""),
            "name": str(doc.get("name") or ""),
            "account_type": str(doc.get("account_type") or "").strip().upper(),
        })
    return items


def ensure_other_charge_fields(invoice: Dict[str, Any]) -> Dict[str, Any]:
    rows = invoice.get("other_charges")
    if not isinstance(rows, list):
        invoice["other_charges"] = []
    invoice["other_charges_total"] = float(other_charges_total(invoice))
    return invoice


def _actor_public(user: Optional[dict]) -> Dict[str, str]:
    user = user or {}
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": str(user.get("name") or user.get("email") or ""),
        "role": str(user.get("role") or ""),
    }


def effective_financial_status(invoice: Optional[dict]) -> str:
    """Legacy documents without the field are treated as draft. GET must not persist this."""
    if not invoice:
        return FINANCIAL_DRAFT
    raw = invoice.get("financial_status")
    if raw is None or str(raw).strip() == "":
        return FINANCIAL_DRAFT
    status = str(raw).strip().lower()
    if status in FINANCIAL_STATUSES:
        return status
    return FINANCIAL_DRAFT


def invoice_delete_block(invoice: Optional[dict], receipt_document_count: int):
    """Data gates shared by ``can_delete`` and the delete endpoints.

    A row may be deleted only when the invoice exists, its effective financial
    status is draft, and ``sales_receipts`` has no document for that invoice.
    Cancelled or reversed receipts still count. The delete endpoints also
    require permission and repeat this check; they stay the last safeguard.
    """
    if not invoice:
        return 404, "Faktur Penjualan tidak ditemukan."
    if effective_financial_status(invoice) != FINANCIAL_DRAFT:
        return 409, "Faktur yang sudah difinalisasi atau dibatalkan tidak dapat dihapus."
    if int(receipt_document_count or 0) > 0:
        return 400, "Hapus Penerimaan Penjualan terlebih dahulu."
    return None


async def sales_receipt_document_counts(db, invoice_ids: List[str]) -> Dict[str, int]:
    """Uncapped receipt-document counts. Matches ``count_documents`` used on delete."""
    ids = [str(invoice_id) for invoice_id in invoice_ids if invoice_id]
    if not ids:
        return {}
    grouped = await db.sales_receipts.aggregate([
        {"$match": {"invoice_id": {"$in": ids}}},
        {"$group": {"_id": "$invoice_id", "n": {"$sum": 1}}},
    ]).to_list(len(ids))
    return {str(row["_id"]): int(row["n"]) for row in grouped}


def monetary_invariant_valid(invoice: Optional[dict]) -> bool:
    invoice = invoice or {}
    grand = _money2(invoice.get("grand_total"))
    after = _money2(invoice.get("after_discount"))
    ppn = _money2(invoice.get("ppn_amount"))
    charges = other_charges_total(invoice)
    return grand == (after + ppn + charges).quantize(Decimal("0.01"))


TOTAL_INVARIANT_MESSAGE = (
    "Total faktur tidak valid: grand_total harus sama dengan after_discount + ppn_amount + total biaya lainnya."
)


def apply_invoice_payment_fields(
    invoice: Dict[str, Any],
    receipts: Optional[List[dict]],
    extra_paid: float = 0,
) -> Dict[str, Any]:
    """Authoritative paid/outstanding from allocated receipts plus posted credit use."""
    from services.sales_receipt_posting import receipt_allocated_amount, receipt_counts_toward_outstanding

    rows = receipts or []
    active = [r for r in rows if receipt_counts_toward_outstanding(r)]
    invoice["receipt_count"] = len(rows)
    invoice["active_receipt_count"] = len(active)
    if effective_financial_status(invoice) == FINANCIAL_CANCELLED:
        invoice["paid_total"] = 0.0
        invoice["outstanding_amount"] = 0.0
        invoice["payment_status"] = "BELUM DIBAYAR"
        return invoice

    paid = round(
        sum(float(receipt_allocated_amount(r)) for r in active) + float(extra_paid or 0),
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
    invoice["paid_total"] = paid
    invoice["outstanding_amount"] = outstanding
    invoice["payment_status"] = p_status
    return invoice


def _draft_filter(invoice_id: str) -> Dict[str, Any]:
    return {
        "id": invoice_id,
        "$or": [
            {"financial_status": FINANCIAL_DRAFT},
            {"financial_status": {"$exists": False}},
            {"financial_status": None},
            {"financial_status": ""},
        ],
    }


async def attach_invoice_accounting_fields(db, invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derived readiness + journal source status. Never writes invoices or journals."""
    if not invoices:
        return invoices
    from services.sales_accounting_settings import mappings_configured
    from services.sales_invoice_posting import (
        ACCOUNTING_NOT_POSTED,
        ACCOUNTING_POSTED,
        CLAIM_AT_FIELD,
        CLAIM_FIELD,
        RECEIPT_CREATE_CLAIM_AT_FIELD,
        RECEIPT_CREATE_CLAIM_FIELD,
        REVERSE_CLAIM_AT_FIELD,
        REVERSE_CLAIM_FIELD,
        accounting_status_from_journal,
        collect_invoice_reverse_receipt_blockers,
        load_source_journals_by_invoice_ids,
        posting_claim_is_active,
        receipt_create_claim_is_active,
        reverse_claim_is_active,
    )
    from services.sales_receipt_posting import receipt_counts_toward_outstanding

    configured = await mappings_configured(db)
    invoice_ids = [inv.get("id") for inv in invoices if inv.get("id")]
    journals = await load_source_journals_by_invoice_ids(db, invoice_ids)
    receipt_document_counts = await sales_receipt_document_counts(db, invoice_ids)
    for inv in invoices:
        status = effective_financial_status(inv)
        receipts = inv.get("receipts") if isinstance(inv.get("receipts"), list) else None
        receipt_count = int(inv.get("receipt_count") or 0)
        active_receipt_count = int(inv.get("active_receipt_count") or 0)
        if receipts is not None:
            receipt_count = len(receipts)
            active_receipt_count = len([r for r in receipts if receipt_counts_toward_outstanding(r)])
        journal = journals.get(str(inv.get("id")))
        accounting_status = accounting_status_from_journal(journal)
        inv["financial_status"] = status
        inv["accounting_status"] = accounting_status
        inv["accounting_posted"] = accounting_status == ACCOUNTING_POSTED
        inv["accounting_journal_id"] = journal.get("id") if journal else None
        inv["accounting_reversal_journal_id"] = inv.get("accounting_reversal_journal_id")
        inv["accounting_ready"] = (
            status == FINANCIAL_FINALIZED and configured and monetary_invariant_valid(inv)
        )
        inv["receipt_count"] = receipt_count
        inv["active_receipt_count"] = active_receipt_count
        inv["can_cancel"] = (
            status == FINANCIAL_FINALIZED
            and receipt_count == 0
            and accounting_status == ACCOUNTING_NOT_POSTED
            and not posting_claim_is_active(inv)
        )
        inv["can_reverse_accounting"] = (
            status == FINANCIAL_FINALIZED
            and accounting_status == ACCOUNTING_POSTED
            and active_receipt_count == 0
            and not posting_claim_is_active(inv)
            and not reverse_claim_is_active(inv)
            and not receipt_create_claim_is_active(inv)
        )
        if receipts is not None and status == FINANCIAL_FINALIZED and accounting_status == ACCOUNTING_POSTED:
            if not inv["can_reverse_accounting"] and active_receipt_count > 0:
                inv["reverse_blockers"] = await collect_invoice_reverse_receipt_blockers(db, inv["id"])
            else:
                inv["reverse_blockers"] = []
        inv.pop(CLAIM_FIELD, None)
        inv.pop(CLAIM_AT_FIELD, None)
        inv.pop(REVERSE_CLAIM_FIELD, None)
        inv.pop(REVERSE_CLAIM_AT_FIELD, None)
        inv.pop(RECEIPT_CREATE_CLAIM_FIELD, None)
        inv.pop(RECEIPT_CREATE_CLAIM_AT_FIELD, None)
        inv["can_post_accounting"] = (
            status == FINANCIAL_FINALIZED
            and accounting_status == ACCOUNTING_NOT_POSTED
            and configured
            and monetary_invariant_valid(inv)
            and _money2(inv.get("grand_total")) > 0
            and _money2(inv.get("after_discount")) >= 0
            and _money2(inv.get("ppn_amount")) >= 0
        )
        inv["can_delete"] = invoice_delete_block(
            inv, receipt_document_counts.get(str(inv.get("id")), 0)
        ) is None
        ensure_other_charge_fields(inv)
    return invoices


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

    existing_invoices = await db.sales_invoices.find(
        invoice_filter, {"_id": 0, "lines": 1, "financial_status": 1}
    ).to_list(500)

    # Cumulative invoiced per exact line_idx (cancelled invoices do not consume qty)
    cumulative_by_line_idx: Dict[int, int] = {}
    for inv in existing_invoices:
        if effective_financial_status(inv) == FINANCIAL_CANCELLED:
            continue
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
        stored_price = stored_order_unit_price(li)
        stored_subtotal = None
        if stored_price is not None:
            stored_subtotal = round(float(Decimal(str(remaining_invoice_qty)) * Decimal(str(stored_price))), 2)

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
            "unit_price": stored_price,
            "price_from_order": stored_price is not None,
            "price_source": li.get("price_source") if stored_price is not None else None,
            "line_subtotal": stored_subtotal,
            "line_total": stored_subtotal,
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
            else:
                sent_line_id = str(matched_input.get("line_id") or "").strip()
                if sent_line_id and sent_line_id != str(p_line.get("line_id") or ""):
                    raise HTTPException(400, f"Identitas baris faktur tidak cocok dengan pesanan untuk '{p_line['item_name']}'.")
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

            if p_line.get("price_from_order"):
                unit_price = float(p_line["unit_price"])
            elif matched_input is None or "unit_price" not in matched_input:
                raise HTTPException(
                    400,
                    f"Harga jual untuk '{p_line['item_name']}' belum ada di pesanan. Lengkapi harga sebelum membuat faktur.",
                )
            else:
                unit_price = _require_explicit_price(
                    matched_input.get("unit_price"),
                    f"Harga jual '{p_line['item_name']}'",
                )

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

        # Discount and PPN stay on goods. Charges are added after that total.
        # Owner decision for ongkir only: shipping is not reduced by the invoice discount
        # and is not part of the goods PPN base. The same arithmetic is still used for
        # other charge rows; that is not a tax decision for those charge types.
        discount_amount = _parse_non_negative_price(payload.get("discount_amount", 0), "Diskon")
        if discount_amount > authoritative_subtotal:
            raise HTTPException(400, f"Diskon (Rp {discount_amount:,.2f}) tidak boleh melebihi subtotal (Rp {authoritative_subtotal:,.2f}).")

        after_discount = round(float(Decimal(str(authoritative_subtotal)) - Decimal(str(discount_amount))), 2)

        ppn_percent = _parse_non_negative_price(payload.get("ppn_percent", 0), "Persentase PPN")
        ppn_amount = round(float(Decimal(str(after_discount)) * (Decimal(str(ppn_percent)) / Decimal("100"))), 2)

        other_charges = await normalize_other_charges(db, payload.get("other_charges"))
        charges_total = float(other_charges_total({"other_charges": other_charges}))
        authoritative_grand_total = round(
            float(Decimal(str(after_discount)) + Decimal(str(ppn_amount)) + Decimal(str(charges_total))),
            2,
        )

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
            "other_charges": other_charges,
            "other_charges_total": charges_total,
            "grand_total": authoritative_grand_total,
            "paid_total": 0.0,
            "outstanding_amount": authoritative_grand_total,
            "payment_status": "BELUM DIBAYAR",
            "financial_status": FINANCIAL_DRAFT,
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
        await attach_invoice_accounting_fields(db, [invoice_doc])
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

        from services.sales_customer_credit import invoice_credit_applied_total

        credit_by_invoice = {}
        for inv_id in inv_ids:
            credit_by_invoice[inv_id] = float(await invoice_credit_applied_total(db, inv_id))
        for inv in invoices:
            apply_invoice_payment_fields(
                inv,
                receipts_by_inv.get(inv.get("id"), []),
                credit_by_invoice.get(inv.get("id"), 0),
            )

    await attach_invoice_accounting_fields(db, invoices)
    return invoices


_CANCELLED_STATUS_VALUES = ["cancelled", "Cancelled", "CANCELLED"]
_INVOICE_SCOPES = frozenset({"active", "cancelled"})
_INVOICE_FINANCIAL_FILTERS = frozenset({FINANCIAL_DRAFT, FINANCIAL_FINALIZED, FINANCIAL_CANCELLED})
_INVOICE_ACCOUNTING_FILTERS = frozenset({"not_posted", "posted", "reversed"})
_INVOICE_PAYMENT_FILTERS = frozenset({"BELUM DIBAYAR", "DP", "LUNAS"})
_INVOICE_PAGE_SIZE_MAX = 100


def _optional_text(raw: Optional[Any]) -> str:
    return str(raw or "").strip()


def _parse_optional_amount(raw: Optional[Any], label: str) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{label} tidak valid.")
    if value != value or value in (float("inf"), float("-inf")):
        raise HTTPException(400, f"{label} tidak valid.")
    return round(value, 2)


def _parse_optional_day(raw: Optional[Any], label: str) -> Optional[str]:
    text = _optional_text(raw)
    if not text:
        return None
    day = text[:10]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, f"{label} tidak valid. Gunakan format YYYY-MM-DD.")
    return day


def _reject_inverted_range(low: Optional[float], high: Optional[float], label: str) -> None:
    if low is not None and high is not None and low > high:
        raise HTTPException(
            400,
            f"Rentang {label} tidak valid: nilai minimum tidak boleh lebih besar dari maksimum.",
        )


def _amount_in_range(value: Any, low: Optional[float], high: Optional[float]) -> bool:
    try:
        amount = round(float(value if value is not None else 0), 2)
    except (TypeError, ValueError):
        amount = 0.0
    if low is not None and amount < low:
        return False
    if high is not None and amount > high:
        return False
    return True


def _invoice_day(invoice: Dict[str, Any]) -> str:
    raw = str(invoice.get("invoice_date") or invoice.get("created_at") or "").strip()
    return raw[:10]


def _scope_clause(scope: str) -> Dict[str, Any]:
    cancelled = {"financial_status": {"$in": _CANCELLED_STATUS_VALUES}}
    if scope == "cancelled":
        return cancelled
    return {"financial_status": {"$nin": _CANCELLED_STATUS_VALUES}}


def _regex_or(fields: List[str], needle: str) -> Dict[str, Any]:
    pattern = re.escape(needle)
    return {"$or": [{field: {"$regex": pattern, "$options": "i"}} for field in fields]}


async def _attach_live_payment_fields(db, invoices: List[Dict[str, Any]]) -> None:
    """Same derived payment and posting fields as the unpaged list. Does not write invoices."""
    inv_ids = [inv["id"] for inv in invoices if inv.get("id")]
    if inv_ids:
        receipt_cap = max(5000, len(inv_ids) * 50)
        receipts = await db.sales_receipts.find(
            {"invoice_id": {"$in": inv_ids}}, {"_id": 0}
        ).to_list(receipt_cap)
        receipts_by_inv: Dict[str, List[dict]] = {}
        for receipt in receipts:
            receipts_by_inv.setdefault(receipt.get("invoice_id"), []).append(receipt)

        from services.sales_customer_credit import invoice_credit_applied_total

        credit_by_invoice = {}
        for inv_id in inv_ids:
            credit_by_invoice[inv_id] = float(await invoice_credit_applied_total(db, inv_id))
        for inv in invoices:
            apply_invoice_payment_fields(
                inv,
                receipts_by_inv.get(inv.get("id"), []),
                credit_by_invoice.get(inv.get("id"), 0),
            )
    await attach_invoice_accounting_fields(db, invoices)


async def query_sales_invoices(
    db,
    *,
    q: Optional[str] = None,
    po_id: Optional[str] = None,
    scope: str = "active",
    party: Optional[str] = None,
    po_number: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    total_min: Optional[Any] = None,
    total_max: Optional[Any] = None,
    outstanding_min: Optional[Any] = None,
    outstanding_max: Optional[Any] = None,
    financial_status: Optional[str] = None,
    accounting_status: Optional[str] = None,
    payment_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """Paged invoice list. Cancellation is financial_status, never posting/reversed.

    Returned rows are the current page only. ``total`` is the filtered count inside
    the tab. ``active_total`` and ``cancelled_total`` ignore search and column filters.
    """
    scope_key = str(scope or "active").strip().lower()
    if scope_key not in _INVOICE_SCOPES:
        raise HTTPException(400, "Tab faktur tidak valid. Gunakan active atau cancelled.")

    financial_key = str(financial_status or "").strip().lower()
    if financial_key and financial_key not in _INVOICE_FINANCIAL_FILTERS:
        raise HTTPException(400, "Status faktur tidak valid.")
    accounting_key = str(accounting_status or "").strip().lower()
    if accounting_key and accounting_key not in _INVOICE_ACCOUNTING_FILTERS:
        raise HTTPException(400, "Status posting tidak valid.")
    payment_key = str(payment_status or "").strip().upper()
    if payment_key and payment_key not in _INVOICE_PAYMENT_FILTERS:
        raise HTTPException(400, "Status pembayaran tidak valid.")

    day_from = _parse_optional_day(date_from, "Tanggal awal")
    day_to = _parse_optional_day(date_to, "Tanggal akhir")
    if day_from and day_to and day_from > day_to:
        raise HTTPException(
            400,
            "Rentang tanggal tidak valid: tanggal awal tidak boleh setelah tanggal akhir.",
        )
    parsed_total_min = _parse_optional_amount(total_min, "Total faktur minimum")
    parsed_total_max = _parse_optional_amount(total_max, "Total faktur maksimum")
    _reject_inverted_range(parsed_total_min, parsed_total_max, "Total Faktur")
    parsed_outstanding_min = _parse_optional_amount(outstanding_min, "Sisa tagihan minimum")
    parsed_outstanding_max = _parse_optional_amount(outstanding_max, "Sisa tagihan maksimum")
    _reject_inverted_range(parsed_outstanding_min, parsed_outstanding_max, "Sisa Tagihan")

    try:
        page_num = int(page or 1)
        size = int(page_size or 20)
    except (TypeError, ValueError):
        raise HTTPException(400, "Halaman atau ukuran halaman tidak valid.")
    if page_num < 1 or size < 1 or size > _INVOICE_PAGE_SIZE_MAX:
        raise HTTPException(400, "Halaman atau ukuran halaman tidak valid.")

    active_clause = _scope_clause("active")
    cancelled_clause = _scope_clause("cancelled")
    if po_id:
        owned = {"po_id": str(po_id)}
        active_total = await db.sales_invoices.count_documents({"$and": [active_clause, owned]})
        cancelled_total = await db.sales_invoices.count_documents({"$and": [cancelled_clause, owned]})
    else:
        active_total = await db.sales_invoices.count_documents(active_clause)
        cancelled_total = await db.sales_invoices.count_documents(cancelled_clause)

    clauses: List[Dict[str, Any]] = [_scope_clause(scope_key)]
    if po_id:
        clauses.append({"po_id": str(po_id)})
    needle = _optional_text(q)
    if needle:
        clauses.append(_regex_or(
            ["id", "invoice_number", "po_number", "customer", "outlet", "sales"],
            needle,
        ))
    party_needle = _optional_text(party)
    if party_needle:
        clauses.append(_regex_or(["customer", "invoice_number"], party_needle))
    po_needle = _optional_text(po_number)
    if po_needle:
        clauses.append(_regex_or(["po_number"], po_needle))

    invoices = await db.sales_invoices.find(
        {"$and": clauses}, {"_id": 0}
    ).sort("created_at", -1).to_list(None)
    await _attach_live_payment_fields(db, invoices)

    filtered: List[Dict[str, Any]] = []
    for inv in invoices:
        status = effective_financial_status(inv)
        if scope_key == "cancelled" and status != FINANCIAL_CANCELLED:
            continue
        if scope_key == "active" and status == FINANCIAL_CANCELLED:
            continue
        if financial_key and status != financial_key:
            continue
        if accounting_key and str(inv.get("accounting_status") or "not_posted").lower() != accounting_key:
            continue
        if payment_key and str(inv.get("payment_status") or "").upper() != payment_key:
            continue
        day = _invoice_day(inv)
        if day_from and (not day or day < day_from):
            continue
        if day_to and (not day or day > day_to):
            continue
        if not _amount_in_range(inv.get("grand_total"), parsed_total_min, parsed_total_max):
            continue
        if not _amount_in_range(inv.get("outstanding_amount"), parsed_outstanding_min, parsed_outstanding_max):
            continue
        filtered.append(inv)

    start = (page_num - 1) * size
    page_rows = filtered[start:start + size]
    tab_total = cancelled_total if scope_key == "cancelled" else active_total
    return {
        "invoices": page_rows,
        "count": len(page_rows),
        "total": len(filtered),
        "tab_total": tab_total,
        "active_total": active_total,
        "cancelled_total": cancelled_total,
        "page": page_num,
        "page_size": size,
    }


async def get_sales_invoice_detail(db, invoice_id: str) -> Dict[str, Any]:
    """Fetch detail of a single sales invoice with live payment derived fields and receipt list."""
    inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not inv:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

    # Fetch live receipts for exact invoice
    receipts = await db.sales_receipts.find({"invoice_id": inv["id"]}, {"_id": 0}).sort("created_at", 1).to_list(500)
    from services.sales_customer_credit import invoice_credit_applied_total

    apply_invoice_payment_fields(inv, receipts, float(await invoice_credit_applied_total(db, inv["id"])))
    inv["receipts"] = receipts
    await attach_invoice_accounting_fields(db, [inv])
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

        status = effective_financial_status(inv)
        if status != FINANCIAL_DRAFT:
            raise HTTPException(
                409,
                "Faktur yang sudah difinalisasi atau dibatalkan tidak dapat diubah field keuangannya.",
            )

        existing_lines = inv.get("lines") or []
        input_lines = payload.get("lines") or payload.get("line_items") or []
        input_by_idx = {int(inp.get("line_idx", idx)): inp for idx, inp in enumerate(input_lines)}

        updated_lines = []
        authoritative_subtotal = 0.0
        total_invoice_qty = 0

        for idx, el in enumerate(existing_lines):
            line_idx = int(el.get("line_idx", idx))
            qty = int(el.get("quantity") or 0)

            if line_idx in input_by_idx and input_by_idx[line_idx].get("unit_price") not in (None, ""):
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

        raw_disc = payload.get("discount_amount") if "discount_amount" in payload else inv.get("discount_amount", 0)
        discount_amount = _parse_non_negative_price(raw_disc, "Diskon")
        if discount_amount > authoritative_subtotal:
            raise HTTPException(400, f"Diskon (Rp {discount_amount:,.2f}) tidak boleh melebihi subtotal (Rp {authoritative_subtotal:,.2f}).")

        after_discount = round(float(Decimal(str(authoritative_subtotal)) - Decimal(str(discount_amount))), 2)

        raw_ppn = payload.get("ppn_percent") if "ppn_percent" in payload else inv.get("ppn_percent", 0)
        ppn_percent = _parse_non_negative_price(raw_ppn, "Persentase PPN")
        ppn_amount = round(float(Decimal(str(after_discount)) * (Decimal(str(ppn_percent)) / Decimal("100"))), 2)

        if payload.get("other_charges") is None:
            other_charges = list(inv.get("other_charges") or [])
        else:
            other_charges = await normalize_other_charges(db, payload.get("other_charges"))
        charges_total = float(other_charges_total({"other_charges": other_charges}))
        authoritative_grand_total = round(
            float(Decimal(str(after_discount)) + Decimal(str(ppn_amount)) + Decimal(str(charges_total))),
            2,
        )

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
            "other_charges": other_charges,
            "other_charges_total": charges_total,
            "grand_total": authoritative_grand_total,
            "outstanding_amount": authoritative_grand_total,
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

    pay_count = await db.sales_receipts.count_documents({"invoice_id": inv["id"]})
    block = invoice_delete_block(inv, pay_count)
    if block:
        raise HTTPException(block[0], block[1])

    inv_number = inv.get("invoice_number", inv["id"])
    await db.sales_invoices.delete_one({"id": inv["id"]})
    return {"deleted": True, "invoice_number": inv_number, "id": inv["id"]}


async def bulk_delete_sales_invoices(db, ids: list, user: dict) -> dict:
    """Bulk delete invoices â€” ALL OR NOTHING. Pre-validate all before deleting any."""
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
        block = invoice_delete_block(inv, pay_count)
        if block:
            blockers.append({
                "id": inv["id"],
                "number": inv.get("invoice_number", inv["id"]),
                "reason": block[1],
            })
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


async def finalize_sales_invoice(db, invoice_id: str, user: dict) -> Dict[str, Any]:
    """draft → finalized. Retry-safe. Does not post a journal or change order workflow."""
    async with _get_invoice_lock():
        inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
        if not inv:
            inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
        if not inv:
            raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

        status = effective_financial_status(inv)
        if status == FINANCIAL_CANCELLED:
            raise HTTPException(409, "Faktur yang dibatalkan tidak dapat difinalisasi.")
        if status == FINANCIAL_FINALIZED:
            return await get_sales_invoice_detail(db, inv["id"])

        if not monetary_invariant_valid(inv):
            raise HTTPException(400, TOTAL_INVARIANT_MESSAGE)

        now = now_iso()
        claimed = await db.sales_invoices.find_one_and_update(
            _draft_filter(inv["id"]),
            {
                "$set": {
                    "financial_status": FINANCIAL_FINALIZED,
                    "finalized_at": now,
                    "finalized_by": _actor_public(user),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if claimed is None:
            current = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            if current and effective_financial_status(current) == FINANCIAL_FINALIZED:
                return await get_sales_invoice_detail(db, inv["id"])
            if current and effective_financial_status(current) == FINANCIAL_CANCELLED:
                raise HTTPException(409, "Faktur yang dibatalkan tidak dapat difinalisasi.")
            raise HTTPException(409, "Finalisasi faktur gagal karena perubahan bersamaan.")
        return await get_sales_invoice_detail(db, inv["id"])


async def cancel_sales_invoice(
    db, invoice_id: str, user: dict, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """finalized → cancelled only when no receipts, no posting claim, and no journal."""
    from services.sales_invoice_posting import (
        CANCEL_CLAIM_MESSAGE,
        cancel_unclaimed_filter,
        find_sales_invoice_source_journal,
        posting_claim_is_active,
        raise_if_journal_blocks_cancel,
        restore_finalized_after_cancel_race,
        _optional_invoice_lock,
    )

    async with _optional_invoice_lock(serialize_in_process):
        inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
        if not inv:
            inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
        if not inv:
            raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

        status = effective_financial_status(inv)
        if status == FINANCIAL_DRAFT:
            raise HTTPException(409, "Hanya faktur yang sudah difinalisasi yang dapat dibatalkan.")
        if status == FINANCIAL_CANCELLED:
            return await get_sales_invoice_detail(db, inv["id"])

        pay_count = await db.sales_receipts.count_documents({"invoice_id": inv["id"]})
        if pay_count > 0:
            raise HTTPException(
                409,
                "Faktur tidak dapat dibatalkan karena sudah memiliki Penerimaan Penjualan.",
            )

        raise_if_journal_blocks_cancel(await find_sales_invoice_source_journal(db, inv["id"]))
        if posting_claim_is_active(inv):
            raise HTTPException(409, CANCEL_CLAIM_MESSAGE)

        now = now_iso()
        claimed = await db.sales_invoices.find_one_and_update(
            cancel_unclaimed_filter(inv["id"]),
            {
                "$set": {
                    "financial_status": FINANCIAL_CANCELLED,
                    "cancelled_at": now,
                    "cancelled_by": _actor_public(user),
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if claimed is None:
            current = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            if current and effective_financial_status(current) == FINANCIAL_CANCELLED:
                return await get_sales_invoice_detail(db, inv["id"])
            if current and posting_claim_is_active(current):
                raise HTTPException(409, CANCEL_CLAIM_MESSAGE)
            if current and await find_sales_invoice_source_journal(db, inv["id"]):
                raise_if_journal_blocks_cancel(await find_sales_invoice_source_journal(db, inv["id"]))
            raise HTTPException(409, "Pembatalan faktur gagal karena perubahan bersamaan.")

        raced_journal = await find_sales_invoice_source_journal(db, inv["id"])
        if raced_journal:
            await restore_finalized_after_cancel_race(db, inv["id"])
            raise_if_journal_blocks_cancel(raced_journal)

        raced = await db.sales_receipts.count_documents({"invoice_id": inv["id"]})
        if raced > 0:
            await db.sales_invoices.update_one(
                {"id": inv["id"], "financial_status": FINANCIAL_CANCELLED},
                {
                    "$set": {"financial_status": FINANCIAL_FINALIZED, "updated_at": now_iso()},
                    "$unset": {"cancelled_at": "", "cancelled_by": ""},
                },
            )
            raise HTTPException(
                409,
                "Faktur tidak dapat dibatalkan karena sudah memiliki Penerimaan Penjualan.",
            )
        return await get_sales_invoice_detail(db, inv["id"])


def generate_sales_invoice_pdf_bytes(invoice: dict) -> bytes:
    """Generate official A4 PDF for Sales Invoice (Faktur Penjualan)."""
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
        textColor=colors.HexColor('#9333ea'), # Purple for invoice
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

    tbl_header_style = ParagraphStyle(
        'TblHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#1e293b'),
    )

    tbl_cell_style = ParagraphStyle(
        'TblCell',
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
            Paragraph("<b>FAKTUR PENJUALAN</b><br/><font size='9' color='#7e22ce'><b>" + escape(str(invoice.get('invoice_number') or invoice.get('id') or '')) + "</b></font>", doc_title_style),
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
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#a855f7'), spaceAfter=8, spaceBefore=0))

    meta_data = [
        [
            Paragraph("<b>Nomor PO</b>", meta_label_style),
            Paragraph(f": {escape(str(invoice.get('po_number') or '-'))}", meta_val_style),
            Paragraph("<b>Customer</b>", meta_label_style),
            Paragraph(f": {escape(str(invoice.get('customer') or '-'))}", meta_val_style),
        ],
        [
            Paragraph("<b>Tanggal Faktur</b>", meta_label_style),
            Paragraph(f": {format_pdf_date(invoice.get('invoice_date'))}", meta_val_style),
            Paragraph("<b>Alamat</b>", meta_label_style),
            Paragraph(f": {escape(str(invoice.get('address') or '-'))}", meta_val_style),
        ],
        [
            Paragraph("<b>Termin (Hari)</b>", meta_label_style),
            Paragraph(f": {invoice.get('payment_term_days') or '0'}", meta_val_style),
            Paragraph("<b>Jatuh Tempo</b>", meta_label_style),
            Paragraph(f": {format_pdf_date(invoice.get('due_date'))}", meta_val_style),
        ],
        [
            Paragraph("<b>Sales</b>", meta_label_style),
            Paragraph(f": {escape(invoice_sales_label(invoice) or '-')}", meta_val_style),
            Paragraph("<b>Status</b>", meta_label_style),
            Paragraph(f": {escape(str(invoice.get('payment_status') or '-'))}", meta_val_style),
        ],
        [
            # Same `notes` field the official invoice PDF already printed as Catatan.
            # The form label is Catatan Faktur. Account, journal, HPP, and margin stay off the PDF.
            Paragraph("<b>Catatan</b>", meta_label_style),
            Paragraph(f": {escape(str(invoice.get('notes') or '-'))}", meta_val_style),
            Paragraph("", meta_label_style),
            Paragraph("", meta_val_style),
        ],
    ]
    meta_tbl = Table(meta_data, colWidths=[2.5 * cm, 6.5 * cm, 2.5 * cm, 6.5 * cm])
    meta_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(meta_tbl)
    elements.append(Spacer(1, 0.4 * cm))

    # Line Items
    table_rows = [
        [
            Paragraph("<b>No</b>", tbl_header_style),
            Paragraph("<b>Produk / Jasa</b>", tbl_header_style),
            Paragraph("<b>Qty</b>", tbl_header_style),
            Paragraph("<b>Harga Satuan</b>", tbl_header_style),
            Paragraph("<b>Subtotal</b>", tbl_header_style),
        ]
    ]

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

    total_qty = 0
    lines = invoice.get("lines") or []
    for idx, li in enumerate(lines):
        q = li.get("quantity", 0)
        total_qty += q
        price = li.get("unit_price", 0)
        subt = li.get("line_subtotal", 0)
        table_rows.append([
            Paragraph(str(idx + 1), tbl_cell_style),
            Paragraph(f"<b>{escape(str(li.get('item_name') or '-'))}</b>", tbl_cell_style),
            Paragraph(f"{q} {escape(str(li.get('unit') or ''))}".strip(), tbl_cell_style),
            Paragraph(format_money(price), tbl_cell_bold),
            Paragraph(format_money(subt), tbl_cell_bold),
        ])

    items_tbl = Table(
        table_rows,
        colWidths=[1.0 * cm, 7.0 * cm, 2.5 * cm, 3.5 * cm, 4.0 * cm]
    )
    items_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#cbd5e1')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
    ]))
    elements.append(items_tbl)
    elements.append(Spacer(1, 0.4 * cm))

    # Financial Summary
    subtotal = invoice.get("subtotal", 0)
    discount = invoice.get("discount_amount", 0)
    ppn_pct = invoice.get("ppn_percent", 0)
    ppn_amt = invoice.get("ppn_amount", 0)
    grand = invoice.get("grand_total", 0)
    paid = invoice.get("paid_total", 0)
    outstanding = invoice.get("outstanding_amount", 0)

    summary_data = [
        [Paragraph("<b>Subtotal</b>", tbl_cell_style), Paragraph(format_money(subtotal), tbl_cell_bold)],
    ]
    if float(discount) > 0:
        summary_data.append([Paragraph("<b>Diskon</b>", tbl_cell_style), Paragraph(f"- {format_money(discount)}", tbl_cell_bold)])
    if float(ppn_pct) > 0:
        summary_data.append([Paragraph(f"<b>PPN ({ppn_pct}%)</b>", tbl_cell_style), Paragraph(format_money(ppn_amt), tbl_cell_bold)])
    for charge in invoice.get("other_charges") or []:
        if not isinstance(charge, dict):
            continue
        # Customer sees the charge description, not the internal ledger account.
        label = str(charge.get("description") or "").strip() or "Biaya lainnya"
        summary_data.append([
            Paragraph(f"<b>{escape(label)}</b>", tbl_cell_style),
            Paragraph(format_money(charge.get("amount")), tbl_cell_bold),
        ])

    summary_data.append([Paragraph("<b>GRAND TOTAL</b>", tbl_cell_style), Paragraph(f"<b>{format_money(grand)}</b>", tbl_cell_bold)])
    summary_data.append([Paragraph("<b>Telah Dibayar</b>", tbl_cell_style), Paragraph(format_money(paid), tbl_cell_bold)])
    summary_data.append([Paragraph("<b>Sisa Tagihan</b>", tbl_cell_style), Paragraph(format_money(outstanding), tbl_cell_bold)])

    sum_tbl = Table(summary_data, colWidths=[4.0 * cm, 4.0 * cm])
    sum_tbl.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'RIGHT'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
    ]))

    # Align summary table to the right
    wrapper_data = [[Paragraph(""), sum_tbl]]
    wrapper_tbl = Table(wrapper_data, colWidths=[10.0 * cm, 8.0 * cm])
    wrapper_tbl.setStyle(TableStyle([
        ('ALIGN', (1,0), (1,0), 'RIGHT'),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ]))
    elements.append(wrapper_tbl)

    doc.build(elements)
    return buffer.getvalue()
