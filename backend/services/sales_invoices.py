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
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Dict, List, Optional, Tuple
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

FINANCIAL_DRAFT = "draft"
FINANCIAL_FINALIZED = "finalized"
FINANCIAL_CANCELLED = "cancelled"
FINANCIAL_STATUSES = frozenset({FINANCIAL_DRAFT, FINANCIAL_FINALIZED, FINANCIAL_CANCELLED})

REASON_INVOICE_QTY_OVER = "FAKTUR_QTY_MELEBIHI_SISA_PO"
MSG_INVOICE_QTY_OVER = "Qty Faktur melebihi sisa Qty Pesanan Penjualan."
MSG_INVOICE_QTY_OVER_FINALIZE = (
    "Qty Faktur melebihi sisa Qty Pesanan Penjualan. Sesuaikan Qty Faktur sebelum Finalisasi."
)
MSG_INVOICE_QTY_INVALID = "Qty Faktur harus berupa bilangan bulat lebih dari 0."
MSG_INVOICE_LINE_DUPLICATE = "Baris faktur tidak boleh dikirim lebih dari sekali."
MSG_INVOICE_LINE_UNKNOWN = "Baris faktur tidak ditemukan pada faktur ini."

DOCUMENT_INVOICE = "invoice"
DOCUMENT_PROFORMA = "proforma"
DOCUMENT_MARKUP = "markup"
PROFORMA_WARNING = "Dokumen Proforma — bukan Faktur Penjualan resmi"
MARKUP_WARNING = "Dokumen cetakan reseller — tidak mengubah Faktur Penjualan asli."

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
        # Surfaced for five-tab Pending before claim tokens are stripped.
        inv["accounting_claim_active"] = bool(
            posting_claim_is_active(inv)
            or reverse_claim_is_active(inv)
            or receipt_create_claim_is_active(inv)
        )
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


def accumulate_invoiced_qty_by_line_idx(invoices: Optional[List[dict]]) -> Dict[int, int]:
    """Sum invoice line qty by line_idx. Cancelled invoices do not consume qty."""
    cumulative_by_line_idx: Dict[int, int] = {}
    for inv in invoices or []:
        if not isinstance(inv, dict):
            continue
        if effective_financial_status(inv) == FINANCIAL_CANCELLED:
            continue
        for line in inv.get("lines") or []:
            if not isinstance(line, dict):
                continue
            try:
                l_idx = int(line.get("line_idx", 0))
                q = int(line.get("quantity", 0))
            except (ValueError, TypeError):
                continue
            cumulative_by_line_idx[l_idx] = cumulative_by_line_idx.get(l_idx, 0) + q
    return cumulative_by_line_idx


def summarize_po_invoiceable_quantities(
    raw_lines: Optional[list],
    cumulative_by_line_idx: Optional[Dict[int, int]] = None,
) -> Dict[str, Any]:
    """Invoiceable remaining qty — same rules as get_po_invoice_preview line math.

    Returns totals plus remaining_by_unit for safe multi-unit UI labels.
    Does not decide PO eligibility or touch delivery/shipping quantities.
    """
    cumulative = cumulative_by_line_idx or {}
    lines_out: List[dict] = []
    total_ordered_qty = 0
    total_remaining_invoice_qty = 0
    remaining_by_unit: Dict[str, int] = {}
    unit_order: List[str] = []

    for idx, li in enumerate(raw_lines or []):
        if not isinstance(li, dict):
            continue
        try:
            line_idx = int(li.get("line_idx", idx) if li.get("line_idx") is not None else idx)
        except (ValueError, TypeError):
            line_idx = idx
        unit = str(li.get("unit") or "pcs").strip() or "pcs"
        try:
            ordered_qty = int(li.get("quantity") or 0)
        except (ValueError, TypeError):
            ordered_qty = 0
        invoiced_cumulative = int(cumulative.get(line_idx, 0) or 0)
        remaining_invoice_qty = max(ordered_qty - invoiced_cumulative, 0)
        total_ordered_qty += ordered_qty
        total_remaining_invoice_qty += remaining_invoice_qty
        if unit not in remaining_by_unit:
            unit_order.append(unit)
            remaining_by_unit[unit] = 0
        remaining_by_unit[unit] += remaining_invoice_qty
        lines_out.append({
            "line_idx": line_idx,
            "unit": unit,
            "ordered_qty": ordered_qty,
            "invoiced_cumulative": invoiced_cumulative,
            "remaining_invoice_qty": remaining_invoice_qty,
        })

    return {
        "available": True,
        "total_ordered_qty": total_ordered_qty,
        "total_remaining_invoice_qty": total_remaining_invoice_qty,
        "remaining_by_unit": [
            {"unit": unit, "qty": remaining_by_unit[unit]} for unit in unit_order
        ],
        "unit_count": len(unit_order),
        "lines": lines_out,
    }


_INVOICEABLE_LIST_INVOICE_HARD_MAX = 20000


async def batch_po_invoiceable_summaries(
    db,
    orders: List[dict],
    *,
    hard_max_invoices: int = _INVOICEABLE_LIST_INVOICE_HARD_MAX,
) -> Dict[str, Dict[str, Any]]:
    """One bounded invoice scan for many POs. Keys are po_id.

    Shares accumulate + summarize rules with get_po_invoice_preview.
    """
    po_ids = [str(o.get("id")) for o in orders if o.get("id")]
    if not po_ids:
        return {}
    cumulative_by_po: Dict[str, Dict[int, int]] = {pid: {} for pid in po_ids}
    scanned = 0
    cursor = db.sales_invoices.find(
        {"po_id": {"$in": po_ids}},
        {"_id": 0, "po_id": 1, "lines": 1, "financial_status": 1},
    )
    async for inv in cursor:
        scanned += 1
        if scanned > hard_max_invoices:
            break
        po_id = str(inv.get("po_id") or "")
        if po_id not in cumulative_by_po:
            continue
        if effective_financial_status(inv) == FINANCIAL_CANCELLED:
            continue
        bucket = cumulative_by_po[po_id]
        for line in inv.get("lines") or []:
            if not isinstance(line, dict):
                continue
            try:
                l_idx = int(line.get("line_idx", 0))
                q = int(line.get("quantity", 0))
            except (ValueError, TypeError):
                continue
            bucket[l_idx] = bucket.get(l_idx, 0) + q

    truncated = scanned > hard_max_invoices
    out: Dict[str, Dict[str, Any]] = {}
    for order in orders:
        po_id = str(order.get("id") or "")
        if not po_id:
            continue
        raw_lines = order.get("line_items") or order.get("items") or []
        summary = summarize_po_invoiceable_quantities(
            raw_lines,
            cumulative_by_po.get(po_id) or {},
        )
        if truncated:
            # Fail closed for UI: do not invent totals when scan was capped.
            summary = {
                "available": False,
                "total_ordered_qty": None,
                "total_remaining_invoice_qty": None,
                "remaining_by_unit": [],
                "unit_count": 0,
                "lines": [],
            }
        out[po_id] = summary
    return out


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
    # Header Keterangan PO (orders.notes) for create-form prefill only; invoice stores its own snapshot.
    notes = (order.get("notes") or "").strip()

    # Include multi-source invoices referencing this PO; attribute each billed
    # line to its exact PO source (never count another PO's same line_idx).
    invoice_filter: Dict[str, Any] = {
        "$or": [
            {"po_id": po_id},
            {"source_po_ids": po_id},
            {"lines.source_po_id": po_id},
        ],
        "billing_commit_pending": {"$ne": True},
    }
    if exclude_invoice_id:
        invoice_filter["id"] = {"$ne": str(exclude_invoice_id)}

    existing_invoices = await db.sales_invoices.find(
        invoice_filter, {"_id": 0, "po_id": 1, "lines": 1, "financial_status": 1}
    ).to_list(501)
    if len(existing_invoices) > 500:
        raise HTTPException(409, "Terlalu banyak Faktur sumber untuk preview PO; gunakan audit penagihan.")

    raw_lines = order.get("line_items") or order.get("items") or []
    current_line_ids = {
        str(li.get("line_id") or li.get("id") or f"line_{idx}"): idx
        for idx, li in enumerate(raw_lines) if isinstance(li, dict)
    }
    cumulative_by_line_idx: Dict[int, int] = {}
    for invoice in existing_invoices:
        if str(invoice.get("financial_status") or "").strip().lower() == "cancelled":
            continue
        for row in invoice.get("lines") or []:
            if not isinstance(row, dict):
                continue
            row_po = str(row.get("source_po_id") or invoice.get("po_id") or "").strip()
            if row_po != str(po_id):
                continue
            row_id = str(row.get("source_po_line_id") or row.get("line_id") or "").strip()
            if row_id in current_line_ids:
                idx = current_line_ids[row_id]
            else:
                try:
                    idx = int(row.get("source_po_line_idx", row.get("line_idx", -1)))
                except (ValueError, TypeError):
                    continue
                if idx < 0 or idx >= len(raw_lines):
                    continue
                current_id = str((raw_lines[idx] or {}).get("line_id") or (raw_lines[idx] or {}).get("id") or "").strip()
                if row_id and current_id and row_id != current_id:
                    continue
            cumulative_by_line_idx[idx] = (
                cumulative_by_line_idx.get(idx, 0) + int(row.get("quantity") or 0)
            )
    qty_summary = summarize_po_invoiceable_quantities(raw_lines, cumulative_by_line_idx)
    qty_by_line = {
        int(row["line_idx"]): row for row in qty_summary.get("lines") or []
    }
    preview_lines = []
    # Read-only lookup: current atomic billing counter may disagree with
    # historical invoice documents after a PO revision/cancellation.
    counter_by_line_id = {}
    if not exclude_invoice_id and raw_lines:
        from services.sales_invoice_allocations import BILLING_COUNTERS, po_billing_counter_id
        ids = [
            po_billing_counter_id(po_id, str(li.get("line_id") or li.get("id") or f"line_{idx}"))
            for idx, li in enumerate(raw_lines) if isinstance(li, dict)
        ]
        if ids:
            counter_docs = await db[BILLING_COUNTERS].find(
                {"id": {"$in": ids}}, {"_id": 0, "id": 1, "invoiced_qty": 1},
            ).to_list(len(ids))
            counter_by_line_id = {
                str(row["id"]): int(row.get("invoiced_qty") or 0)
                for row in counter_docs
            }

    for idx, li in enumerate(raw_lines):
        if not isinstance(li, dict):
            continue
        try:
            line_idx = int(li.get("line_idx", idx) if li.get("line_idx") is not None else idx)
        except (ValueError, TypeError):
            line_idx = idx
        line_id = str(li.get("line_id") or li.get("id") or f"line_{idx}")
        item_name = str(li.get("name") or li.get("item_name") or "").strip()
        item_code = str(li.get("item_code") or "").strip()
        unit = str(li.get("unit") or "pcs").strip()
        color = str(li.get("color") or "").strip()
        qty_row = qty_by_line.get(line_idx) or {}
        ordered_qty = int(qty_row.get("ordered_qty") or 0)
        invoice_document_qty = int(qty_row.get("invoiced_cumulative") or 0)
        counter_qty = counter_by_line_id.get(f"{po_id}::{line_id}")
        # Never offer more than either source permits. No silent reconciliation.
        invoiced_cumulative = max(invoice_document_qty, counter_qty or 0)
        remaining_invoice_qty = max(0, ordered_qty - invoiced_cumulative)
        billing_counter_discrepancy = (
            counter_qty is not None and counter_qty != invoice_document_qty
        )
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
            "invoice_document_qty": invoice_document_qty,
            "billing_counter_qty": counter_qty,
            "billing_counter_discrepancy": billing_counter_discrepancy,
            "remaining_invoice_qty": remaining_invoice_qty,
            "quantity": remaining_invoice_qty,  # Default suggested qty to invoice
            "unit": unit,
            "unit_price": stored_price,
            "price_from_order": stored_price is not None,
            "price_source": li.get("price_source") if stored_price is not None else None,
            "line_subtotal": stored_subtotal,
            "line_total": stored_subtotal,
        })

    total_ordered_qty = int(qty_summary.get("total_ordered_qty") or 0)
    total_remaining_invoice_qty = sum(int(line["remaining_invoice_qty"]) for line in preview_lines)

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
        "notes": notes,
        "total_ordered_qty": total_ordered_qty,
        "total_remaining_invoice_qty": total_remaining_invoice_qty,
        "billing_reconciliation_required": any(
            line["billing_counter_discrepancy"] for line in preview_lines
        ),
        "total_qty": total_remaining_invoice_qty,
        "remaining_by_unit": [
            {"unit": unit, "qty": sum(
                int(line["remaining_invoice_qty"])
                for line in preview_lines if line["unit"] == unit
            )}
            for unit in dict.fromkeys(line["unit"] for line in preview_lines)
        ],
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

    Model C: optional ``source_mode`` ``po``|``sj`` with multi document ids.
    One mode per request. Legacy single ``po_id`` remains the default path.
    """
    source_mode = str(payload.get("source_mode") or "").strip().lower()
    source_po_ids = [
        str(x).strip() for x in (payload.get("source_po_ids") or []) if str(x).strip()
    ]
    source_sj_ids = [
        str(x).strip() for x in (payload.get("source_sj_ids") or []) if str(x).strip()
    ]
    if source_mode in ("po", "sj") or len(source_po_ids) > 1 or source_sj_ids:
        return await create_flexible_sales_invoice(db, payload, user)

    po_id = str(payload.get("po_id") or "").strip()
    if not po_id:
        raise HTTPException(400, "ID Pesanan Penjualan (po_id) wajib diisi.")

    from services.operational_branch import require_document_branch
    order = await db.orders.find_one({"id": po_id}, {"_id": 0})
    if not order:
        order = await db.orders.find_one({"po_number": po_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, f"Pesanan Penjualan (PO) '{po_id}' tidak ditemukan.")
    await require_document_branch(db, user, order)
    if order.get("cancelled"):
        raise HTTPException(400, "Pesanan Penjualan ini sudah dibatalkan.")

    from services.sales_invoice_create_idempotency import (
        begin_idempotent_create,
        bind_idempotent_create_invoice,
        resolve_duplicate_key_invoice,
        stamp_invoice_fingerprint,
    )

    existing_idem, idempotency_key, create_fingerprint = await begin_idempotent_create(
        db, payload, user
    )
    if existing_idem:
        await require_document_branch(db, user, existing_idem)
        return existing_idem
    idempotency_key = str(idempotency_key or payload.get("idempotency_key") or "").strip()

    async with _get_invoice_lock():
        if idempotency_key and not create_fingerprint:
            existing_by_key = await db.sales_invoices.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
            if existing_by_key:
                await require_document_branch(db, user, existing_by_key)
                return existing_by_key

        # Re-read order inside lock for remaining qty.
        order = await db.orders.find_one({"id": order["id"]}, {"_id": 0}) or order
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

            from services.sales_invoice_allocations import new_invoice_line_id
            calculated_lines.append({
                "invoice_line_id": new_invoice_line_id(),
                "line_idx": l_idx,
                "line_id": p_line["line_id"],
                "source_po_id": preview["po_id"],
                "source_po_line_id": p_line["line_id"],
                "source_po_line_idx": l_idx,
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

        from services.sales_invoice_dates import SalesInvoiceDateError, parse_sales_invoice_date

        raw_invoice_date = payload.get("invoice_date")
        if raw_invoice_date is None:
            inv_dt = datetime.now(timezone.utc).date()
        elif str(raw_invoice_date).strip() == "":
            raise HTTPException(400, "Tanggal faktur wajib diisi.")
        else:
            try:
                inv_dt = parse_sales_invoice_date(raw_invoice_date)
            except SalesInvoiceDateError as exc:
                raise HTTPException(400, "Tanggal faktur tidak valid.") from exc
        invoice_date_str = inv_dt.isoformat()

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
            "source_mode": "po",
            "source_po_ids": [preview["po_id"]],
            "is_multi_source": False,
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
        from services.operational_branch import document_branch
        invoice_doc["operational_branch_id"] = document_branch(order)
        order_branch_id = str(order.get("branch_id") or "").strip()
        if order_branch_id.isdigit():
            invoice_doc["branch_id"] = order_branch_id

        stamp_invoice_fingerprint(invoice_doc, create_fingerprint)
        from services.sales_invoice_allocations import persist_invoice_with_billing_claims
        try:
            await persist_invoice_with_billing_claims(db, invoice_doc)
        except DuplicateKeyError:
            if idempotency_key:
                existing = await resolve_duplicate_key_invoice(
                    db, idempotency_key=idempotency_key, fingerprint=create_fingerprint
                )
                if existing:
                    return existing
            raise HTTPException(409, "Faktur Penjualan duplikat terdeteksi.")

        if idempotency_key and create_fingerprint:
            await bind_idempotent_create_invoice(
                db,
                idempotency_key=idempotency_key,
                fingerprint=create_fingerprint,
                invoice_id=invoice_id,
            )

        invoice_doc.pop("_id", None)
        await attach_invoice_accounting_fields(db, [invoice_doc])
        try:
            from services.notifications import emit_sales_invoice_created_to_creator
            await emit_sales_invoice_created_to_creator(db, order=order, invoice=invoice_doc)
        except Exception:
            logging.getLogger(__name__).exception("sales invoice created notification failed")
        return invoice_doc


async def create_flexible_sales_invoice(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Model C create: one source_mode per request (po | sj), possibly multi documents."""
    from services.customer_identity import extract_stable_customer_id
    from services.operational_branch import document_branch, require_document_branch
    from services.sales_delivery_allocations import (
        normalize_delivery_source_allocations,
        require_operational_sales_delivery,
    )
    from services.sales_invoice_allocations import (
        derive_combined_invoice_number,
        get_po_line_invoiceable_remaining,
        new_invoice_line_id,
        validate_orders_invoice_merge_compatible,
    )
    from services.sales_invoice_dates import SalesInvoiceDateError, parse_sales_invoice_date

    source_mode = str(payload.get("source_mode") or "").strip().lower()
    source_po_ids = [str(x).strip() for x in (payload.get("source_po_ids") or []) if str(x).strip()]
    source_sj_ids = [str(x).strip() for x in (payload.get("source_sj_ids") or []) if str(x).strip()]
    if source_mode not in ("po", "sj"):
        if source_sj_ids:
            source_mode = "sj"
        else:
            source_mode = "po"
    if source_mode == "po" and source_sj_ids:
        raise HTTPException(400, "Mode PO tidak boleh mencampur source_sj_ids dalam satu request.")
    if source_mode == "sj" and not source_sj_ids:
        raise HTTPException(400, "Mode SJ memerlukan source_sj_ids.")

    input_lines = payload.get("lines") or payload.get("line_items") or []
    if not input_lines:
        raise HTTPException(400, "Baris Faktur wajib diisi untuk mode multi-sumber.")

    orders_by_id: Dict[str, dict] = {}
    deliveries_by_id: Dict[str, dict] = {}

    if source_mode == "sj":
        for sj_id in source_sj_ids:
            delivery = await require_operational_sales_delivery(db, sj_id, attempt_reconcile=True)
            deliveries_by_id[str(delivery.get("id"))] = delivery
            for alloc in normalize_delivery_source_allocations(delivery):
                pid = str(alloc.get("source_po_id") or "").strip()
                if pid and pid not in orders_by_id:
                    order = await db.orders.find_one({"id": pid}, {"_id": 0})
                    if not order:
                        raise HTTPException(404, f"PO sumber SJ '{pid}' tidak ditemukan.")
                    orders_by_id[pid] = order
        source_po_ids = list(orders_by_id.keys())
    else:
        if not source_po_ids:
            po_id = str(payload.get("po_id") or "").strip()
            if po_id:
                source_po_ids = [po_id]
        if not source_po_ids:
            raise HTTPException(400, "Mode PO memerlukan source_po_ids atau po_id.")
        for pid in source_po_ids:
            order = await db.orders.find_one({"id": pid}, {"_id": 0})
            if not order:
                order = await db.orders.find_one({"po_number": pid}, {"_id": 0})
            if not order:
                raise HTTPException(404, f"Pesanan Penjualan '{pid}' tidak ditemukan.")
            orders_by_id[str(order.get("id"))] = order
        source_po_ids = list(orders_by_id.keys())

    orders = [orders_by_id[pid] for pid in source_po_ids]
    validate_orders_invoice_merge_compatible(orders)
    for order in orders:
        await require_document_branch(db, user, order)

    # Durable multi-worker ownership AFTER validation, BEFORE billing claims.
    from services.sales_invoice_create_idempotency import (
        begin_idempotent_create,
        bind_idempotent_create_invoice,
        resolve_duplicate_key_invoice,
        stamp_invoice_fingerprint,
    )

    existing_idem, idempotency_key, create_fingerprint = await begin_idempotent_create(
        db, payload, user
    )
    if existing_idem:
        await require_document_branch(db, user, existing_idem)
        return existing_idem
    idempotency_key = str(idempotency_key or payload.get("idempotency_key") or "").strip()

    primary = orders[0]
    calculated_lines: List[dict] = []
    authoritative_subtotal = 0.0
    total_invoice_qty = 0

    for inp in input_lines:
        if not isinstance(inp, dict):
            continue
        try:
            invoice_qty = int(inp.get("quantity") if inp.get("quantity") is not None else inp.get("invoice_qty") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "Kuantitas faktur harus bilangan bulat.")
        if invoice_qty <= 0:
            continue
        source_po_id = str(inp.get("source_po_id") or "").strip()
        if source_mode == "po" and not source_po_id:
            source_po_id = source_po_ids[0]
        if source_po_id not in orders_by_id:
            raise HTTPException(400, f"source_po_id '{source_po_id}' tidak termasuk dokumen sumber.")
        order = orders_by_id[source_po_id]
        try:
            line_idx = int(inp.get("source_po_line_idx", inp.get("line_idx", 0)))
        except (TypeError, ValueError):
            raise HTTPException(400, "source_po_line_idx tidak valid.")
        order_lines = order.get("line_items") or []
        if line_idx < 0 or line_idx >= len(order_lines) or not isinstance(order_lines[line_idx], dict):
            raise HTTPException(400, f"Baris PO {line_idx} tidak ditemukan.")
        ol = order_lines[line_idx]
        line_id = str(inp.get("source_po_line_id") or ol.get("line_id") or f"{source_po_id}_line_{line_idx}").strip()
        ordered_qty = int(ol.get("quantity") or 0)
        remaining = await get_po_line_invoiceable_remaining(
            db,
            source_po_id=source_po_id,
            source_line_id=line_id,
            source_line_idx=line_idx,
            ordered_qty=ordered_qty,
        )
        if invoice_qty > remaining:
            raise HTTPException(
                409,
                f"Qty faktur ({invoice_qty}) melebihi sisa invoiceable PO ({remaining}).",
            )

        source_sj_id = str(inp.get("source_sj_id") or "").strip()
        sj_line_qty = None
        if source_mode == "sj":
            if not source_sj_id or source_sj_id not in deliveries_by_id:
                raise HTTPException(400, "Setiap baris mode SJ wajib source_sj_id yang valid.")
            delivery = deliveries_by_id[source_sj_id]
            matched_alloc = None
            for alloc in normalize_delivery_source_allocations(delivery):
                if str(alloc.get("source_po_id")) != source_po_id:
                    continue
                if str(alloc.get("source_line_id") or "") == line_id or int(alloc.get("source_line_idx") or -1) == line_idx:
                    matched_alloc = alloc
                    break
            if not matched_alloc:
                raise HTTPException(400, "Baris tidak ditemukan pada alokasi SJ sumber.")
            sj_line_qty = int(matched_alloc.get("allocated_qty") or 0)
            if invoice_qty > sj_line_qty:
                raise HTTPException(
                    409,
                    f"Qty faktur ({invoice_qty}) melebihi qty SJ sumber ({sj_line_qty}).",
                )

        if ol.get("unit_price") is not None or ol.get("price") is not None:
            unit_price = float(ol.get("unit_price") if ol.get("unit_price") is not None else ol.get("price"))
        elif "unit_price" in inp:
            unit_price = _require_explicit_price(inp.get("unit_price"), f"Harga jual baris {line_idx}")
        else:
            raise HTTPException(400, f"Harga jual baris {line_idx} belum tersedia.")

        line_subtotal = round(float(Decimal(str(invoice_qty)) * Decimal(str(unit_price))), 2)
        authoritative_subtotal += line_subtotal
        total_invoice_qty += invoice_qty
        row = {
            "invoice_line_id": new_invoice_line_id(),
            "line_idx": line_idx,
            "line_id": line_id,
            "source_po_id": source_po_id,
            "source_po_number": order.get("po_number"),
            "source_po_line_id": line_id,
            "source_po_line_idx": line_idx,
            "item_name": ol.get("name") or ol.get("item_name") or f"Item #{line_idx + 1}",
            "item_code": ol.get("item_code"),
            "color": ol.get("color"),
            "ordered_qty": ordered_qty,
            "remaining_invoice_qty_before": remaining,
            "quantity": invoice_qty,
            "unit": ol.get("unit") or "pcs",
            "unit_price": unit_price,
            "line_subtotal": line_subtotal,
            "line_total": line_subtotal,
        }
        if source_sj_id:
            row["source_sj_id"] = source_sj_id
            row["source_sj_line_id"] = str(inp.get("source_sj_line_id") or line_id)
            row["sj_line_qty"] = int(sj_line_qty or invoice_qty)
        calculated_lines.append(row)

    if total_invoice_qty <= 0 or not calculated_lines:
        raise HTTPException(400, "Total kuantitas faktur harus lebih besar dari 0.")

    authoritative_subtotal = round(authoritative_subtotal, 2)
    discount_amount = _parse_non_negative_price(payload.get("discount_amount", 0), "Diskon")
    if discount_amount > authoritative_subtotal:
        raise HTTPException(400, "Diskon tidak boleh melebihi subtotal.")
    after_discount = round(float(Decimal(str(authoritative_subtotal)) - Decimal(str(discount_amount))), 2)
    ppn_percent = _parse_non_negative_price(payload.get("ppn_percent", 0), "Persentase PPN")
    ppn_amount = round(float(Decimal(str(after_discount)) * (Decimal(str(ppn_percent)) / Decimal("100"))), 2)
    other_charges = await normalize_other_charges(db, payload.get("other_charges"))
    charges_total = float(other_charges_total({"other_charges": other_charges}))
    grand_total = round(
        float(Decimal(str(after_discount)) + Decimal(str(ppn_amount)) + Decimal(str(charges_total))),
        2,
    )

    raw_term = payload.get("payment_term_days", 0)
    try:
        payment_term_days = int(raw_term if raw_term is not None and str(raw_term).strip() != "" else 0)
    except (ValueError, TypeError):
        raise HTTPException(400, "Termin pembayaran harus berupa angka hari.")
    if payment_term_days < 0:
        raise HTTPException(400, "Termin pembayaran tidak boleh negatif.")

    raw_invoice_date = payload.get("invoice_date")
    if raw_invoice_date is None:
        inv_dt = datetime.now(timezone.utc).date()
    elif str(raw_invoice_date).strip() == "":
        raise HTTPException(400, "Tanggal faktur wajib diisi.")
    else:
        try:
            inv_dt = parse_sales_invoice_date(raw_invoice_date)
        except SalesInvoiceDateError as exc:
            raise HTTPException(400, "Tanggal faktur tidak valid.") from exc
    invoice_date_str = inv_dt.isoformat()
    due_date_str = (inv_dt + timedelta(days=payment_term_days)).strftime("%Y-%m-%d")

    multi = len(source_po_ids) > 1 or (source_mode == "sj" and len(source_sj_ids) > 1)
    if multi:
        invoice_number = derive_combined_invoice_number()
        while await db.sales_invoices.find_one({"invoice_number": invoice_number}, {"_id": 1}):
            invoice_number = derive_combined_invoice_number()
        invoice_index = 1
    else:
        existing_invoices_count = await db.sales_invoices.count_documents({"po_id": primary["id"]})
        invoice_index = existing_invoices_count + 1
        invoice_number = derive_invoice_number(primary.get("po_number") or "", invoice_index)
        while await db.sales_invoices.find_one({"invoice_number": invoice_number}, {"_id": 1}):
            invoice_index += 1
            invoice_number = derive_invoice_number(primary.get("po_number") or "", invoice_index)

    invoice_id = str(uuid.uuid4())
    customer = (primary.get("customer") or primary.get("customer_name") or "").strip()
    invoice_doc = {
        "id": invoice_id,
        "invoice_number": invoice_number,
        "invoice_index": invoice_index,
        "idempotency_key": idempotency_key or None,
        "source_mode": source_mode,
        "source_po_ids": source_po_ids,
        "source_sj_ids": source_sj_ids if source_mode == "sj" else [],
        "is_multi_source": multi,
        "po_id": primary["id"],
        "po_number": primary.get("po_number"),
        "customer_id": extract_stable_customer_id(primary),
        "order_date": primary.get("order_date"),
        "invoice_date": invoice_date_str,
        "payment_term_days": payment_term_days,
        "due_date": due_date_str,
        "customer": customer,
        "outlet": (primary.get("outlet") or "").strip(),
        "recipient": (primary.get("recipient") or "").strip(),
        "address": (primary.get("address") or "").strip(),
        "delivery_zone": (primary.get("delivery_zone") or "").strip(),
        "sales": (primary.get("sales") or "").strip(),
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
        "grand_total": grand_total,
        "paid_total": 0.0,
        "outstanding_amount": grand_total,
        "payment_status": "BELUM DIBAYAR",
        "financial_status": FINANCIAL_DRAFT,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "created_by": {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": str(user.get("name") or user.get("email") or ""),
            "role": str(user.get("role") or ""),
        },
        "operational_branch_id": document_branch(primary),
    }
    order_branch_id = str(primary.get("branch_id") or "").strip()
    if order_branch_id.isdigit():
        invoice_doc["branch_id"] = order_branch_id

    stamp_invoice_fingerprint(invoice_doc, create_fingerprint)
    from services.sales_invoice_allocations import persist_invoice_with_billing_claims
    try:
        await persist_invoice_with_billing_claims(db, invoice_doc)
    except DuplicateKeyError:
        if idempotency_key:
            existing = await resolve_duplicate_key_invoice(
                db, idempotency_key=idempotency_key, fingerprint=create_fingerprint
            )
            if existing:
                return existing
        raise HTTPException(409, "Faktur Penjualan duplikat terdeteksi.")

    if idempotency_key and create_fingerprint:
        await bind_idempotent_create_invoice(
            db,
            idempotency_key=idempotency_key,
            fingerprint=create_fingerprint,
            invoice_id=invoice_id,
        )

    invoice_doc.pop("_id", None)
    await attach_invoice_accounting_fields(db, [invoice_doc])
    return invoice_doc


async def list_sales_invoices(db, q: Optional[str] = None, po_id: Optional[str] = None, branch_query: Optional[dict] = None) -> List[Dict[str, Any]]:
    """List all sales invoices with payment derived fields."""
    from services.sales_invoice_allocations import BILLING_COMMIT_PENDING_FIELD
    filters: Dict[str, Any] = {"$and": [branch_query]} if branch_query else {}
    # Operational list — provisional billing_commit_pending excluded.
    filters[BILLING_COMMIT_PENDING_FIELD] = {"$ne": True}
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

    from services.sales_list_tabs import LIST_SORT

    invoices = await db.sales_invoices.find(filters, {"_id": 0}).sort(LIST_SORT).to_list(1000)

    # Attach derived payment totals dynamically from db.sales_receipts
    inv_ids = [inv["id"] for inv in invoices if "id" in inv]
    if inv_ids:
        receipts = await db.sales_receipts.find({"invoice_id": {"$in": inv_ids}}, {"_id": 0}).to_list(5000)
        receipts_by_inv: Dict[str, List[dict]] = {}
        for r in receipts:
            receipts_by_inv.setdefault(r.get("invoice_id"), []).append(r)

        from services.sales_customer_credit import invoice_credit_applied_totals_by_invoice_ids

        credit_totals = await invoice_credit_applied_totals_by_invoice_ids(db, inv_ids)
        credit_by_invoice = {inv_id: float(val) for inv_id, val in credit_totals.items()}
        for inv in invoices:
            apply_invoice_payment_fields(
                inv,
                receipts_by_inv.get(inv.get("id"), []),
                credit_by_invoice.get(inv.get("id"), 0),
            )

    await attach_invoice_accounting_fields(db, invoices)
    return invoices


_CANCELLED_STATUS_VALUES = ["cancelled", "Cancelled", "CANCELLED"]
# Legacy: active = non-Batal (incl. Pending/Selesai); cancelled = Batal; all = union.
# Additive exclusive five-tab scopes: completed, pending.
_INVOICE_SCOPES = frozenset({"active", "cancelled", "all", "completed", "pending"})
_INVOICE_FINANCIAL_FILTERS = frozenset({FINANCIAL_DRAFT, FINANCIAL_FINALIZED, FINANCIAL_CANCELLED})
_INVOICE_ACCOUNTING_FILTERS = frozenset({"not_posted", "posted", "reversed"})
_INVOICE_PAYMENT_FILTERS = frozenset({"BELUM DIBAYAR", "DP", "LUNAS"})
_INVOICE_PAGE_SIZE_MAX = 100
_INVOICE_LIST_BATCH = 250


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
    """Canonical YYYY-MM-DD for range checks. Mixed historical formats are not compared raw."""
    from services.sales_invoice_dates import try_canonical_sales_invoice_date

    raw = invoice.get("invoice_date") or invoice.get("created_at") or ""
    return try_canonical_sales_invoice_date(raw)


def _scope_clause(
    scope: str, *, incomplete_cancel_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Tab Mongo prefilter.

    Incomplete/failed cancel intents stay on Aktif even when financial_status is
    already cancelled (post-reverse crash window). Historic cancelled rows with
    no incomplete intent remain on Batal.
    """
    hold_ids = [str(i) for i in (incomplete_cancel_ids or []) if i]
    cancelled = {"financial_status": {"$in": _CANCELLED_STATUS_VALUES}}
    if scope == "cancelled":
        if hold_ids:
            return {"$and": [cancelled, {"id": {"$nin": hold_ids}}]}
        return cancelled
    if scope == "all":
        return {}
    active = {"financial_status": {"$nin": _CANCELLED_STATUS_VALUES}}
    if hold_ids:
        return {"$or": [active, {"id": {"$in": hold_ids}}]}
    return active


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

        from services.sales_customer_credit import invoice_credit_applied_totals_by_invoice_ids

        credit_totals = await invoice_credit_applied_totals_by_invoice_ids(db, inv_ids)
        credit_by_invoice = {inv_id: float(val) for inv_id, val in credit_totals.items()}
        for inv in invoices:
            apply_invoice_payment_fields(
                inv,
                receipts_by_inv.get(inv.get("id"), []),
                credit_by_invoice.get(inv.get("id"), 0),
            )
    await attach_invoice_accounting_fields(db, invoices)


async def _incomplete_revision_invoice_ids(db, invoice_ids: List[str]) -> set:
    if not invoice_ids:
        return set()
    from services.sales_invoice_edit_revision import (
        COLLECTION as REV_COLL,
        PHASE_COMPLETE,
        PHASE_FAILED,
    )

    rows = await db[REV_COLL].find(
        {
            "invoice_id": {"$in": [str(i) for i in invoice_ids]},
            "financial_complete": {"$ne": True},
            "phase": {"$nin": [PHASE_COMPLETE, PHASE_FAILED]},
        },
        {"_id": 0, "invoice_id": 1},
    ).to_list(max(len(invoice_ids) * 4, 20))
    return {str(r.get("invoice_id") or "") for r in rows if r.get("invoice_id")}


async def query_sales_invoices(
    db,
    *,
    q: Optional[str] = None,
    po_id: Optional[str] = None,
    scope: str = "active",
    list_tab: Optional[str] = None,
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
    page_size: int = 50,
    branch_query: Optional[dict] = None,
) -> Dict[str, Any]:
    """Paged invoice list with five-tab classification.

    ``scope`` remains backward compatible:
    - active = non-Batal (Aktif ∪ Selesai ∪ Pending)
    - cancelled / all = legacy
    - completed / pending = exclusive five-tab filters

    ``list_tab`` when set selects an exclusive primary tab (including active unpaid-only).
    Rows always include computed ``list_tab``. Tab count fields use the same classifier
    under the same search/filters (not Mongo financial_status alone).
    """
    from services.sales_list_tabs import (
        DEFAULT_PAGE_SIZE,
        LIST_HARD_MAX,
        LIST_SORT,
        TAB_ACTIVE,
        TAB_ALL,
        TAB_CANCELLED,
        TAB_COMPLETED,
        TAB_PENDING,
        bump_tab_count,
        classify_sales_invoice_list_tab,
        empty_tab_counts,
        legacy_invoice_scope_includes,
        normalize_list_tab,
        parse_page_params,
    )

    scope_key = str(scope or "active").strip().lower()
    if scope_key not in _INVOICE_SCOPES:
        raise HTTPException(
            400,
            "Tab faktur tidak valid. Gunakan active, completed, pending, cancelled, atau all.",
        )
    exclusive_tab = None
    if list_tab is not None and str(list_tab).strip() != "":
        try:
            exclusive_tab = normalize_list_tab(list_tab, default=TAB_ACTIVE)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    elif scope_key in {TAB_COMPLETED, TAB_PENDING}:
        exclusive_tab = scope_key

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
        page_num, size = parse_page_params(page, page_size if page_size is not None else DEFAULT_PAGE_SIZE)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if size > _INVOICE_PAGE_SIZE_MAX:
        raise HTTPException(400, "Halaman atau ukuran halaman tidak valid.")

    from services.sales_business_cancel import (
        DOC_INVOICE,
        attach_invoice_business_cancel_fields,
        incomplete_or_failed_cancel_document_ids,
    )

    hold_ids = sorted(await incomplete_or_failed_cancel_document_ids(db, DOC_INVOICE))

    # Mongo prefilter: never narrow exclusive completed/pending in Mongo — classify in app.
    mongo_scope = "all" if exclusive_tab or scope_key in {TAB_COMPLETED, TAB_PENDING, TAB_ALL} else (
        "cancelled" if scope_key == TAB_CANCELLED else "active"
    )
    scope_clause = _scope_clause(mongo_scope, incomplete_cancel_ids=hold_ids)
    clauses: List[Dict[str, Any]] = [branch_query] if branch_query else []
    from services.sales_invoice_allocations import BILLING_COMMIT_PENDING_FIELD
    clauses.append({BILLING_COMMIT_PENDING_FIELD: {"$ne": True}})
    if scope_clause:
        clauses.append(scope_clause)
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

    find_filter: Dict[str, Any] = {"$and": clauses} if clauses else {}
    cursor = db.sales_invoices.find(find_filter, {"_id": 0}).sort(LIST_SORT)

    tab_counts = empty_tab_counts()
    filtered: List[Dict[str, Any]] = []
    scanned = 0
    batch: List[Dict[str, Any]] = []

    async def _flush(batch_rows: List[Dict[str, Any]]) -> None:
        nonlocal scanned
        if not batch_rows:
            return
        scanned += len(batch_rows)
        if scanned > LIST_HARD_MAX:
            raise HTTPException(
                409,
                f"Hasil daftar faktur melebihi batas aman ({LIST_HARD_MAX}). Perhalus pencarian/filter.",
            )
        await _attach_live_payment_fields(db, batch_rows)
        await attach_invoice_business_cancel_fields(db, batch_rows)
        rev_ids = await _incomplete_revision_invoice_ids(
            db, [str(r.get("id") or "") for r in batch_rows if r.get("id")]
        )
        for inv in batch_rows:
            status = effective_financial_status(inv)
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
            inv_id = str(inv.get("id") or "")
            primary = classify_sales_invoice_list_tab(
                inv, revision_incomplete=inv_id in rev_ids
            )
            inv["list_tab"] = primary
            inv["revision_incomplete"] = inv_id in rev_ids
            bump_tab_count(tab_counts, primary)
            if exclusive_tab is not None:
                if exclusive_tab != TAB_ALL and primary != exclusive_tab:
                    continue
            elif not legacy_invoice_scope_includes(primary, scope_key):
                continue
            filtered.append(inv)

    async for doc in cursor:
        batch.append(doc)
        if len(batch) >= _INVOICE_LIST_BATCH:
            await _flush(batch)
            batch = []
    await _flush(batch)

    start = (page_num - 1) * size
    page_rows = filtered[start:start + size]
    from services.po_identity_fields import attach_po_identity_fields
    await attach_po_identity_fields(db, page_rows, po_id_key="po_id")
    if exclusive_tab == TAB_CANCELLED or (exclusive_tab is None and scope_key == TAB_CANCELLED):
        tab_total = tab_counts[TAB_CANCELLED]
    elif exclusive_tab == TAB_COMPLETED:
        tab_total = tab_counts[TAB_COMPLETED]
    elif exclusive_tab == TAB_PENDING:
        tab_total = tab_counts[TAB_PENDING]
    elif exclusive_tab == TAB_ACTIVE:
        tab_total = tab_counts[TAB_ACTIVE]
    elif exclusive_tab == TAB_ALL or scope_key == TAB_ALL:
        tab_total = tab_counts[TAB_ALL]
    else:
        # Legacy scope=active → non-batal
        tab_total = tab_counts[TAB_ALL] - tab_counts[TAB_CANCELLED]
    legacy_active = tab_counts[TAB_ALL] - tab_counts[TAB_CANCELLED]
    return {
        "invoices": page_rows,
        "count": len(page_rows),
        "total": len(filtered),
        "tab_total": tab_total,
        # Legacy badge: non-Batal (Aktif ∪ Selesai ∪ Pending) under same filters.
        "active_total": legacy_active,
        "completed_total": tab_counts[TAB_COMPLETED],
        "pending_total": tab_counts[TAB_PENDING],
        "cancelled_total": tab_counts[TAB_CANCELLED],
        "all_total": tab_counts[TAB_ALL],
        # Exclusive five-tab unpaid Aktif count.
        "five_tab_active_total": tab_counts[TAB_ACTIVE],
        "page": page_num,
        "page_size": size,
        "list_tab": exclusive_tab,
        "scope": scope_key,
    }


def _active_proforma_invoices(rows: List[dict]) -> List[dict]:
    """Non-cancelled invoices that can supply a proforma.

    Draft and finalized both qualify. Cancelled rows are ignored and do not
    block the remaining invoice. Accounting status and payment status are not
    consulted. A blank legacy financial_status counts as draft, matching
    ``effective_financial_status``.
    """
    active: List[dict] = []
    for row in rows or []:
        if effective_financial_status(row) == FINANCIAL_CANCELLED:
            continue
        active.append(row)
    return active


async def _invoices_for_po(db, po_id: str) -> List[dict]:
    return await db.sales_invoices.find({"po_id": str(po_id)}, {"_id": 0}).to_list(100)


async def proforma_availability(db, po_id: str) -> Dict[str, Any]:
    """Lightweight read: one non-cancelled invoice id, or unavailable. Does not write."""
    order = await db.orders.find_one({"id": str(po_id)}, {"_id": 0, "id": 1, "cancelled": 1})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    empty = {"available": False, "invoice_id": None, "invoice_number": None}
    if order.get("cancelled"):
        return empty
    active = _active_proforma_invoices(await _invoices_for_po(db, order["id"]))
    if len(active) != 1:
        return empty
    chosen = active[0]
    return {
        "available": True,
        "invoice_id": chosen.get("id"),
        "invoice_number": chosen.get("invoice_number") or "",
    }


async def require_proforma_invoice(db, invoice_id: str) -> Dict[str, Any]:
    """Load the single non-cancelled saved invoice that supplies this PO's proforma.

    Reads ``sales_invoices`` and ``orders`` only. Draft and finalized invoices
    both qualify. Rejects a cancelled invoice, a cancelled PO, a missing
    invoice, or more than one non-cancelled invoice for the same PO.
    """
    key = str(invoice_id or "").strip()
    inv = await db.sales_invoices.find_one({"id": key}, {"_id": 0})
    if not inv:
        inv = await db.sales_invoices.find_one({"invoice_number": key}, {"_id": 0})
    if not inv:
        raise HTTPException(404, "Faktur Penjualan tidak ditemukan.")

    po_id = str(inv.get("po_id") or "").strip()
    order = await db.orders.find_one({"id": po_id}, {"_id": 0, "id": 1, "cancelled": 1})
    if not order:
        raise HTTPException(404, "PO tidak ditemukan")
    if order.get("cancelled"):
        raise HTTPException(409, "PO dibatalkan. Proforma tidak tersedia.")

    if effective_financial_status(inv) == FINANCIAL_CANCELLED:
        raise HTTPException(409, "Faktur dibatalkan. Proforma tidak tersedia.")

    active = _active_proforma_invoices(await _invoices_for_po(db, po_id))
    if len(active) != 1 or active[0].get("id") != inv.get("id"):
        raise HTTPException(409, "Proforma tidak tersedia untuk faktur ini.")
    return inv


async def get_sales_invoice_detail(db, invoice_id: str) -> Dict[str, Any]:
    """Fetch detail of a single sales invoice with live payment derived fields and receipt list."""
    from services.sales_invoice_allocations import (
        invoice_is_billing_pending,
        reconcile_pending_orphan_invoice,
        require_operational_sales_invoice,
    )

    inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not inv:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

    if invoice_is_billing_pending(inv) or inv.get("billing_recovery_fence") is True:
        # Attempt safe recovery; provisional invoices are not operational detail.
        await reconcile_pending_orphan_invoice(db, str(inv.get("id") or invoice_id))
        inv = await require_operational_sales_invoice(db, str(inv.get("id") or invoice_id), attempt_reconcile=False)

    # Fetch live receipts for exact invoice
    receipts = await db.sales_receipts.find({"invoice_id": inv["id"]}, {"_id": 0}).sort("created_at", 1).to_list(500)
    from services.sales_customer_credit import invoice_credit_applied_total

    apply_invoice_payment_fields(inv, receipts, float(await invoice_credit_applied_total(db, inv["id"])))
    inv["receipts"] = receipts
    await attach_invoice_accounting_fields(db, [inv])
    await _attach_invoice_qty_limits(db, inv)
    # Stage 4: read-only source_mode + relation history (no mutation of legacy docs).
    mode = str(inv.get("source_mode") or "").strip().lower()
    if mode not in ("po", "sj"):
        mode = "sj" if (inv.get("source_sj_ids") or []) else "po"
    inv["source_mode"] = mode
    inv["is_multi_source"] = bool(inv.get("is_multi_source")) or (
        len(inv.get("source_po_ids") or []) > 1 or len(inv.get("source_sj_ids") or []) > 1
    )
    try:
        from services.sales_document_relations import build_relation_history_events
        inv["relation_history"] = build_relation_history_events(invoice=inv)
    except Exception:
        inv["relation_history"] = []
    return inv


async def get_sales_invoice_detail_for_user(db, invoice_id: str, user: Optional[dict]) -> Dict[str, Any]:
    """Detail plus read-only lifecycle capability for the authenticated user."""
    inv = await get_sales_invoice_detail(db, invoice_id)
    from services.sales_lifecycle_capability import build_invoice_lifecycle_capability

    inv["lifecycle_capability"] = await build_invoice_lifecycle_capability(db, user, inv)
    return inv


def _parse_draft_invoice_qty(raw: Any) -> int:
    """Whole number greater than zero. Floats that are not whole numbers are rejected."""
    if isinstance(raw, bool) or raw is None:
        raise HTTPException(400, {"code": "FAKTUR_QTY_TIDAK_VALID", "message": MSG_INVOICE_QTY_INVALID})
    if isinstance(raw, int):
        qty = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise HTTPException(400, {"code": "FAKTUR_QTY_TIDAK_VALID", "message": MSG_INVOICE_QTY_INVALID})
        qty = int(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        if not text.isdigit():
            raise HTTPException(400, {"code": "FAKTUR_QTY_TIDAK_VALID", "message": MSG_INVOICE_QTY_INVALID})
        qty = int(text)
    else:
        raise HTTPException(400, {"code": "FAKTUR_QTY_TIDAK_VALID", "message": MSG_INVOICE_QTY_INVALID})
    if qty <= 0:
        raise HTTPException(400, {"code": "FAKTUR_QTY_TIDAK_VALID", "message": MSG_INVOICE_QTY_INVALID})
    return qty


def _invoice_line_match_key(line: dict, *, invoice: Optional[dict] = None, fallback_idx: int = 0) -> Tuple[str, ...]:
    """Stable match key for draft edit. Prefer invoice_line_id, then source identity, else line_idx.

    Source identity requires explicit source_po_line_id (Model C). Do not treat legacy
    line_id as source identity — that would break line_idx-only revision payloads on
    single-PO invoices that still store PO line_id without source_* fields.
    """
    if not isinstance(line, dict):
        return ("idx", str(fallback_idx))
    iid = str(line.get("invoice_line_id") or "").strip()
    if iid:
        return ("iid", iid)
    po = str(line.get("source_po_id") or (invoice or {}).get("po_id") or "").strip()
    lid = str(line.get("source_po_line_id") or "").strip()
    sj = str(line.get("source_sj_id") or "").strip()
    if po and lid:
        return ("src", po, lid, sj)
    try:
        line_idx = int(line.get("line_idx", fallback_idx))
    except (TypeError, ValueError):
        line_idx = fallback_idx
    return ("idx", str(line_idx))


def _explicit_draft_qty_by_line(
    existing_lines: List[dict],
    input_lines: List[dict],
    *,
    invoice: Optional[dict] = None,
) -> Dict[Tuple[str, ...], int]:
    """Qty explicitly sent for an existing invoice line. Missing qty keeps the stored value.

    Matches by invoice_line_id / source identity when present so multi-SJ rows that share
    the same source line_idx do not collide. Duplicate keys and unknown lines are rejected.
    """
    existing_keys = {
        _invoice_line_match_key(el, invoice=invoice, fallback_idx=idx)
        for idx, el in enumerate(existing_lines or [])
        if isinstance(el, dict)
    }
    seen = set()
    requested: Dict[Tuple[str, ...], int] = {}
    for fallback, inp in enumerate(input_lines or []):
        if not isinstance(inp, dict):
            raise HTTPException(400, {"code": "FAKTUR_BARIS_TIDAK_DITEMUKAN", "message": MSG_INVOICE_LINE_UNKNOWN})
        key = _invoice_line_match_key(inp, invoice=invoice, fallback_idx=fallback)
        if key in seen:
            raise HTTPException(400, {"code": "FAKTUR_BARIS_DUPLIKAT", "message": MSG_INVOICE_LINE_DUPLICATE})
        seen.add(key)
        if key not in existing_keys:
            raise HTTPException(400, {"code": "FAKTUR_BARIS_TIDAK_DITEMUKAN", "message": MSG_INVOICE_LINE_UNKNOWN})
        if "quantity" not in inp or inp.get("quantity") in (None, ""):
            continue
        requested[key] = _parse_draft_invoice_qty(inp.get("quantity"))
    return requested


async def invoice_line_qty_limits(
    db,
    po_id: str,
    exclude_invoice_id: Optional[str] = None,
) -> Dict[int, Dict[str, int]]:
    """Latest PO qty minus other non-cancelled invoices.

    The excluded invoice is the draft being edited or finalized, so it is not
    counted against itself. Cancelled invoices are ignored by the preview helper.
    """
    key = str(po_id or "").strip()
    if not key:
        return {}
    order = await db.orders.find_one({"id": key}, {"_id": 0})
    if not order:
        order = await db.orders.find_one({"po_number": key}, {"_id": 0})
    if not order:
        return {}
    preview = await get_po_invoice_preview(db, order, exclude_invoice_id=exclude_invoice_id)
    limits: Dict[int, Dict[str, int]] = {}
    for line in preview.get("lines") or []:
        limits[int(line["line_idx"])] = {
            "po_qty": int(line.get("ordered_qty") or 0),
            "other_active_invoiced_qty": int(line.get("invoiced_cumulative") or 0),
            "max_qty": int(line.get("remaining_invoice_qty") or 0),
        }
    return limits


def _qty_over_detail(line_idx: int, invoice_qty: int, cap: Dict[str, int], *, finalize: bool) -> Dict[str, Any]:
    return {
        "code": REASON_INVOICE_QTY_OVER,
        "message": MSG_INVOICE_QTY_OVER_FINALIZE if finalize else MSG_INVOICE_QTY_OVER,
        "line_idx": line_idx,
        "po_qty": int(cap.get("po_qty") or 0),
        "other_invoiced_qty": int(cap.get("other_active_invoiced_qty") or 0),
        "invoice_qty": int(invoice_qty),
        "max_qty": int(cap.get("max_qty") or 0),
    }


async def assert_invoice_qty_within_po(db, invoice: dict, *, finalize: bool = False) -> None:
    """Reject when a saved line qty is above the latest remaining capacity.

    Reads the order and other active invoices at call time. Create, draft update,
    and finalize already share the in-process invoice lock, so one worker cannot
    interleave those writes. Separate processes do not share that lock.

    Multi-source invoices resolve capacity per source_po_id so colliding source
    line_idx values do not borrow another PO's remaining qty.
    """
    empty = {"po_qty": 0, "other_active_invoiced_qty": 0, "max_qty": 0}
    limits_by_po: Dict[str, Dict[int, Dict[str, int]]] = {}
    for idx, line in enumerate(invoice.get("lines") or []):
        if not isinstance(line, dict):
            continue
        po_id = str(line.get("source_po_id") or invoice.get("po_id") or "").strip()
        try:
            line_idx = int(line.get("source_po_line_idx", line.get("line_idx", idx)))
        except (TypeError, ValueError):
            line_idx = idx
        try:
            invoice_qty = int(line.get("quantity") or 0)
        except (TypeError, ValueError):
            invoice_qty = 0
        if po_id not in limits_by_po:
            limits_by_po[po_id] = await invoice_line_qty_limits(db, po_id, invoice.get("id"))
        cap = dict(limits_by_po.get(po_id, {}).get(line_idx, empty))
        if line.get("sj_line_qty") is not None:
            try:
                sj_cap = int(line.get("sj_line_qty") or 0)
            except (TypeError, ValueError):
                sj_cap = 0
            cap["max_qty"] = min(int(cap.get("max_qty") or 0), sj_cap) if sj_cap >= 0 else int(cap.get("max_qty") or 0)
        if invoice_qty > int(cap["max_qty"]):
            raise HTTPException(409, _qty_over_detail(line_idx, invoice_qty, cap, finalize=finalize))


async def _attach_invoice_qty_limits(db, invoice: Dict[str, Any]) -> None:
    """Read-only guidance for the draft editor. Not written back to the invoice."""
    empty = {"po_qty": 0, "other_active_invoiced_qty": 0, "max_qty": 0}
    limits_by_po: Dict[str, Dict[int, Dict[str, int]]] = {}
    for idx, line in enumerate(invoice.get("lines") or []):
        if not isinstance(line, dict):
            continue
        po_id = str(line.get("source_po_id") or invoice.get("po_id") or "").strip()
        try:
            line_idx = int(line.get("source_po_line_idx", line.get("line_idx", idx)))
        except (TypeError, ValueError):
            line_idx = idx
        if po_id and po_id not in limits_by_po:
            limits_by_po[po_id] = await invoice_line_qty_limits(db, po_id, invoice.get("id"))
        cap = dict(limits_by_po.get(po_id, {}).get(line_idx, empty))
        try:
            current_qty = int(line.get("quantity") or 0)
        except (TypeError, ValueError):
            current_qty = 0
        max_qty = int(cap.get("max_qty") or 0)
        if line.get("sj_line_qty") is not None:
            try:
                sj_cap = int(line.get("sj_line_qty") or 0)
            except (TypeError, ValueError):
                sj_cap = 0
            if sj_cap >= 0:
                max_qty = min(max_qty, sj_cap) if max_qty else sj_cap
        line["current_qty"] = current_qty
        line["po_qty"] = int(cap.get("po_qty") or line.get("ordered_qty") or 0)
        line["other_active_invoiced_qty"] = int(cap.get("other_active_invoiced_qty") or 0)
        line["max_qty"] = max_qty


async def update_sales_invoice(db, invoice_id: str, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Updates a draft sales invoice, including line quantity.

    Recalculates line totals, discount, PPN, and grand total from the invoice
    unit price. Does not read the price master and never mutates the PO.
    """
    async with _get_invoice_lock():
        inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
        if not inv:
            inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
        if not inv:
            raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

        status = effective_financial_status(inv)
        if status != FINANCIAL_DRAFT:
            from services.sales_invoice_edit_revision import (
                is_sales_invoice_edit_revision_enabled,
                revise_posted_sales_invoice,
            )

            if status == FINANCIAL_FINALIZED and await is_sales_invoice_edit_revision_enabled(db):
                return await revise_posted_sales_invoice(db, inv["id"], payload, user)
            raise HTTPException(
                409,
                "Faktur yang sudah difinalisasi atau dibatalkan tidak dapat diubah field keuangannya.",
            )

        existing_lines = inv.get("lines") or []
        input_lines = payload.get("lines") or payload.get("line_items") or []
        explicit_qty = _explicit_draft_qty_by_line(existing_lines, input_lines, invoice=inv)
        input_by_key = {}
        seen_input = set()
        for fallback, inp in enumerate(input_lines):
            if not isinstance(inp, dict):
                raise HTTPException(400, {"code": "FAKTUR_BARIS_TIDAK_DITEMUKAN", "message": MSG_INVOICE_LINE_UNKNOWN})
            key = _invoice_line_match_key(inp, invoice=inv, fallback_idx=fallback)
            if key in seen_input:
                raise HTTPException(400, {"code": "FAKTUR_BARIS_DUPLIKAT", "message": MSG_INVOICE_LINE_DUPLICATE})
            seen_input.add(key)
            input_by_key[key] = inp

        updated_lines = []
        authoritative_subtotal = 0.0
        total_invoice_qty = 0

        for idx, el in enumerate(existing_lines):
            key = _invoice_line_match_key(el, invoice=inv, fallback_idx=idx)
            if key in explicit_qty:
                qty = explicit_qty[key]
            else:
                try:
                    qty = int(el.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0
            if qty <= 0:
                raise HTTPException(400, {"code": "FAKTUR_QTY_TIDAK_VALID", "message": MSG_INVOICE_QTY_INVALID})

            if key in input_by_key and input_by_key[key].get("unit_price") not in (None, ""):
                raw_price = input_by_key[key].get("unit_price")
                unit_price = _parse_non_negative_price(raw_price, f"Harga satuan '{el.get('item_name')}'")
            else:
                unit_price = _parse_non_negative_price(el.get("unit_price", 0), f"Harga satuan '{el.get('item_name')}'")

            line_subtotal = round(float(Decimal(str(qty)) * Decimal(str(unit_price))), 2)
            authoritative_subtotal += line_subtotal
            total_invoice_qty += qty

            updated_el = dict(el)
            updated_el["quantity"] = qty
            updated_el["unit_price"] = unit_price
            updated_el["line_subtotal"] = line_subtotal
            updated_el["line_total"] = line_subtotal
            for derived in ("current_qty", "po_qty", "other_active_invoiced_qty", "max_qty"):
                updated_el.pop(derived, None)
            updated_lines.append(updated_el)

        authoritative_subtotal = round(authoritative_subtotal, 2)
        await assert_invoice_qty_within_po(db, {**inv, "lines": updated_lines}, finalize=False)

        from services.sales_invoice_allocations import (
            assert_invoice_not_provisional,
            rebalance_invoice_billing_allocations,
        )
        assert_invoice_not_provisional(inv)
        # Rebalance billing counters BEFORE persisting new qty (all-or-nothing).
        await rebalance_invoice_billing_allocations(
            db, inv, existing_lines, updated_lines
        )

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

        from services.sales_invoice_dates import SalesInvoiceDateError, persist_sales_invoice_date

        try:
            inv_dt, invoice_date_str = persist_sales_invoice_date(
                inv.get("invoice_date"),
                payload.get("invoice_date") if "invoice_date" in payload else None,
            )
        except SalesInvoiceDateError as exc:
            raise HTTPException(400, "Tanggal faktur tidak valid.") from exc

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

    from services.sales_invoice_allocations import release_invoice_billing_allocations
    await release_invoice_billing_allocations(db, inv)
    inv_number = inv.get("invoice_number", inv["id"])
    await db.sales_invoices.delete_one({"id": inv["id"]})
    return {"deleted": True, "invoice_number": inv_number, "id": inv["id"]}


async def bulk_delete_sales_invoices(db, ids: list, user: dict) -> dict:
    """Bulk delete invoices — ALL OR NOTHING. Pre-validate all before deleting any."""
    unique_ids = list(dict.fromkeys(ids))
    from services.operational_branch import id_scope_query
    invoices = await db.sales_invoices.find(await id_scope_query(db, user, unique_ids), {"_id": 0}).to_list(len(unique_ids) or 1)
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
        from services.sales_invoice_allocations import release_invoice_billing_allocations
        for inv in to_delete:
            await release_invoice_billing_allocations(db, inv)
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

        from services.sales_invoice_allocations import assert_invoice_not_provisional
        assert_invoice_not_provisional(inv)

        status = effective_financial_status(inv)
        if status == FINANCIAL_CANCELLED:
            raise HTTPException(409, "Faktur yang dibatalkan tidak dapat difinalisasi.")
        if status == FINANCIAL_FINALIZED:
            return await get_sales_invoice_detail(db, inv["id"])

        if not monetary_invariant_valid(inv):
            raise HTTPException(400, TOTAL_INVARIANT_MESSAGE)

        await assert_invoice_qty_within_po(db, inv, finalize=True)

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


async def _raise_finalize_after_create_failure(db, invoice: Dict[str, Any], exc: HTTPException) -> None:
    """Fail-closed: keep draft identity; never advertise financial success."""
    invoice_id = str((invoice or {}).get("id") or "").strip()
    detail_doc = invoice
    if invoice_id:
        try:
            detail_doc = await get_sales_invoice_detail(db, invoice_id)
        except Exception:
            detail_doc = invoice
    raw = exc.detail
    if isinstance(raw, dict):
        body = dict(raw)
        body.setdefault("message", raw.get("message") or "Finalisasi faktur gagal.")
    else:
        body = {"message": str(raw or "Finalisasi faktur gagal.")}
    body["invoice"] = detail_doc
    body["code"] = body.get("code") or "FINALIZE_AFTER_CREATE_FAILED"
    body["finalize_after_create"] = False
    raise HTTPException(int(exc.status_code or 409), body) from exc


async def create_and_finalize_sales_invoice(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Create via existing lifecycle, then finalize. Idempotent retry-safe.

    Does NOT post a journal. Does NOT set payment_status to LUNAS.
    Uses create_sales_invoice (legacy + Model C) then finalize_sales_invoice.
    On finalize failure after create, preserves draft identity for recovery.
    """
    created = await create_sales_invoice(db, payload, user)
    status = effective_financial_status(created)
    if status == FINANCIAL_FINALIZED:
        return await get_sales_invoice_detail(db, created["id"])
    if status == FINANCIAL_CANCELLED:
        raise HTTPException(409, "Faktur yang dibatalkan tidak dapat difinalisasi.")
    try:
        return await finalize_sales_invoice(db, created["id"], user)
    except HTTPException as exc:
        await _raise_finalize_after_create_failure(db, created, exc)
        raise  # pragma: no cover


async def update_draft_and_finalize_sales_invoice(
    db, invoice_id: str, payload: Dict[str, Any], user: dict
) -> Dict[str, Any]:
    """Update draft then finalize in one orchestration. Finalized revision stays revision-only.

    Legacy drafts: Simpan Perubahan also finalizes.
    Already-finalized invoices: update_sales_invoice routes to edit revision when enabled;
    this wrapper does not force a second finalize.
    """
    existing = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not existing:
        existing = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not existing:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

    was_draft = effective_financial_status(existing) == FINANCIAL_DRAFT
    updated = await update_sales_invoice(db, invoice_id, payload, user)
    if not was_draft:
        return updated
    if effective_financial_status(updated) == FINANCIAL_FINALIZED:
        return updated
    try:
        return await finalize_sales_invoice(db, updated["id"], user)
    except HTTPException as exc:
        await _raise_finalize_after_create_failure(db, updated, exc)
        raise  # pragma: no cover


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
        from services.sales_invoice_edit_revision import raise_if_revision_in_progress

        await raise_if_revision_in_progress(db, inv["id"])
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
            await raise_if_revision_in_progress(db, inv["id"])
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
        # Free shared billing capacity exactly once (idempotent).
        try:
            from services.sales_invoice_allocations import release_invoice_billing_allocations
            cancelled_doc = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
            await release_invoice_billing_allocations(db, cancelled_doc or claimed or inv)
        except Exception:
            logging.getLogger(__name__).exception("release invoice billing on cancel failed")
            raise
        return await get_sales_invoice_detail(db, inv["id"])




async def invoice_pdf_presentation(db, invoice: dict) -> dict:
    """Read-only shipping label for the PDF. Does not write the invoice."""
    shown = dict(invoice or {})
    po_id = str(shown.get("po_id") or "").strip()
    if not po_id or db is None:
        return shown
    order = await db.orders.find_one({"id": po_id}, {"_id": 0, "expedition": 1})
    if not order:
        return shown
    shown["presentation_expedition"] = str(order.get("expedition") or "").strip()
    return shown


def _sales_invoice_pdf_date(raw) -> str:
    """Sales Invoice PDF only. Four-digit DD/MM/YYYY. Does not change the shared PDF helper."""
    from services.pdf_date import format_pdf_date
    from services.sales_invoice_dates import format_sales_invoice_user_date

    shown = format_sales_invoice_user_date(raw)
    if shown:
        return shown
    return format_pdf_date(raw)


def generate_sales_invoice_pdf_bytes(
    invoice: dict,
    document_type: str = DOCUMENT_INVOICE,
    design: dict | None = None,
) -> bytes:
    """A4 portrait Faktur Penjualan. Proforma/Markup reuse this layout with another title.

    Proforma and Markup omit payment-settlement rows (uang dimuka). They do not
    reprice, post, or change the saved invoice.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    from services.pdf_layout import (
        build_pdf,
        content_width,
        esc,
        format_money,
        format_qty,
        logo_flowable,
        scale_widths,
        signature_image_flowable,
        styles,
        terbilang,
    )
    from services.print_design_settings import bank_display_lines, resolve_print_design

    invoice = invoice or {}
    design = resolve_print_design(design)
    inv_design = design.get("invoice") or {}
    pdf_assets = design.get("_pdf_assets") if isinstance(design.get("_pdf_assets"), dict) else {}
    proforma = document_type == DOCUMENT_PROFORMA
    markup = document_type == DOCUMENT_MARKUP
    omit_payment_rows = proforma or markup
    sty = styles(design.get("accent_color"))
    accent = sty["accent"]
    width = content_width(A4)
    elements = []

    from services.print_context_resolver import build_invoice_print_context
    from services.print_footer_render import (
        build_generic_invoice_footer_flowables,
        footer_elements_active,
    )
    from services.print_header_render import (
        build_generic_invoice_header_flowables,
        header_elements_active,
    )

    header_elements = inv_design.get("header_elements") or []
    footer_elements = inv_design.get("footer_elements") or []
    use_generic_header = header_elements_active(header_elements)
    use_generic_footer = footer_elements_active(footer_elements)
    if markup:
        title = "Faktur Markup"
        doc_warning = MARKUP_WARNING
        context_document_type = "markup"
    elif proforma:
        title = "PROFORMA INVOICE"
        doc_warning = PROFORMA_WARNING
        context_document_type = "proforma"
    else:
        title = "Faktur Penjualan"
        doc_warning = None
        context_document_type = "invoice"
    print_context = None
    if use_generic_header or use_generic_footer:
        print_context = build_invoice_print_context(
            invoice,
            design,
            document_type=context_document_type,
        )

    if use_generic_header:
        # Phase 1 V2: controlled multi-source header. Body/totals unchanged.
        elements.extend(
            build_generic_invoice_header_flowables(
                elements=header_elements,
                context=print_context,
                design=design,
                sty=sty,
                width=width,
                document_title=title,
                proforma_warning=doc_warning,
            )
        )
    else:
        # Exact V1 legacy header path.
        logo = logo_flowable(design, width=36 * mm, height=12.2 * mm, company_style=sty["company"])
        address_html = "<br/>".join(esc(line) for line in (design.get("address_lines") or []))
        identity = Paragraph(
            f"<font size='12'><b>{esc(design.get('company_name') or '')}</b></font><br/>{address_html}",
            sty["address"],
        )
        brand = Table([[logo, identity]], colWidths=[40 * mm, width - 40 * mm])
        brand.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(brand)
        elements.append(Spacer(1, 2 * mm))
        elements.append(Paragraph(esc(title), sty["title"]))
        if doc_warning:
            warn_style = sty["markup_warn"] if markup and sty.get("markup_warn") else sty["warn"]
            elements.append(Paragraph(esc(doc_warning), warn_style))
            if markup:
                elements.append(Spacer(1, 1 * mm))
        elements.append(Spacer(1, 2 * mm))

        address = str(invoice.get("address") or "").strip()
        customer = Paragraph(
            f"Kepada :<br/><b>{esc(invoice.get('customer') or '-')}</b><br/>{esc(address)}",
            sty["body"],
        )
        due = _sales_invoice_pdf_date(invoice.get("due_date"))
        jasa = str(invoice.get("presentation_expedition") or invoice.get("expedition") or "").strip()
        tipe = str(invoice.get("presentation_shipping_type") or invoice.get("shipping_type") or "").strip()
        bank_lines = bank_display_lines(inv_design.get("bank_accounts"))
        bank_left = "<br/>".join(esc(line) for line in bank_lines[:2])
        bank_right = "<br/>".join(esc(line) for line in bank_lines[2:])

        def cell(label: str, value_html: str) -> Paragraph:
            return Paragraph(f"{esc(label)}<br/><b>{value_html or '&nbsp;'}</b>", sty["small"])

        if markup:
            # Keep shipping and manual bank visually separate (markup-only layout).
            meta = [
                [
                    cell("Tanggal", esc(_sales_invoice_pdf_date(invoice.get("invoice_date")))),
                    cell("Nomor", esc(invoice.get("invoice_number") or invoice.get("id") or "")),
                ],
                [
                    cell("Sales", esc(invoice_sales_label(invoice))),
                    cell("PO No", esc(invoice.get("po_number") or "")),
                ],
                [
                    cell("Tipe Pengiriman", esc(tipe) or "&nbsp;"),
                    cell("Jasa Pengiriman", esc(jasa) or "&nbsp;"),
                ],
            ]
            bank_html = bank_left or "&nbsp;"
            if bank_right:
                bank_html = f"{bank_left}<br/>{bank_right}" if bank_left else bank_right
            bank_block = Paragraph(
                f"<b>Rekening Markup (manual)</b><br/>{bank_html}",
                sty["small"],
            )
        else:
            meta = [
                [
                    cell("Tanggal", esc(_sales_invoice_pdf_date(invoice.get("invoice_date")))),
                    cell("Nomor", esc(invoice.get("invoice_number") or invoice.get("id") or "")),
                ],
                [
                    cell("Sales", esc(invoice_sales_label(invoice))),
                    cell("Tipe Pengiriman", esc(tipe)),
                ],
                [
                    cell("Jasa Pengiriman", esc(jasa)),
                    Paragraph(bank_left, sty["small"]),
                ],
                [
                    cell("PO No", esc(invoice.get("po_number") or "")),
                    Paragraph(bank_right, sty["small"]),
                ],
            ]
            bank_block = None
        grid_width = width * 0.58
        meta_table = Table(meta, colWidths=[grid_width / 2, grid_width / 2])
        meta_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.7, colors.black),
            ("LINEAFTER", (0, 0), (0, -1), 0.6, colors.black),
            ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.black, None, (2, 2)),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        left_width = width - grid_width
        party = Table([[customer, meta_table]], colWidths=[left_width, grid_width])
        party.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 3),
            ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("LINEBELOW", (0, 0), (0, 0), 0.6, colors.black),
        ]))
        elements.append(party)
        elements.append(Spacer(1, 1.5 * mm))
        elements.append(Paragraph(f"Jatuh Tempo : {esc(due) or '-'}", sty["red"]))
        if bank_block is not None:
            elements.append(Spacer(1, 1.5 * mm))
            bank_table = Table([[bank_block]], colWidths=[width])
            bank_table.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.7, colors.black),
                ("BACKGROUND", (0, 0), (-1, -1), colors.Color(0.97, 0.97, 0.97)),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            elements.append(bank_table)
        elements.append(Spacer(1, 2 * mm))

    header = [
        Paragraph("<b>Kode Barang</b>", sty["header"]),
        Paragraph("<b>Nama Barang</b>", sty["header"]),
        Paragraph("<b>Kts.</b>", sty["header"]),
        Paragraph("<b>@Harga</b>", sty["header"]),
        Paragraph("<b>Diskon</b>", sty["header"]),
        Paragraph("<b>Total Harga</b>", sty["header"]),
    ]
    rows = [header]
    for line in invoice.get("lines") or []:
        if not isinstance(line, dict):
            continue
        rows.append([
            Paragraph(esc(line.get("item_code") or ""), sty["body"]),
            Paragraph(esc(line.get("item_name") or line.get("name") or "-"), sty["body"]),
            Paragraph(esc(format_qty(line.get("quantity"))), sty["right"]),
            Paragraph(esc(format_money(line.get("unit_price"))), sty["right"]),
            Paragraph("0", sty["right"]),
            Paragraph(esc(format_money(line.get("line_subtotal"))), sty["right"]),
        ])
    if len(rows) == 1:
        rows.append([Paragraph("", sty["body"]) for _ in range(6)])
    items = Table(rows, colWidths=scale_widths([70, 190, 42, 68, 48, 78], width), repeatRows=1)
    items.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(items)
    elements.append(Spacer(1, 1.5 * mm))
    words = terbilang(invoice.get("grand_total"))
    elements.append(Paragraph(f"Terbilang : <b>{esc(words)}</b>", sty["body"]))
    rule = Table([[""]], colWidths=[width])
    rule.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, -1), 0.6, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(rule)
    elements.append(Spacer(1, 2 * mm))

    notes = str(invoice.get("notes") or "").strip()
    note = Paragraph(f"<b>Keterangan:</b><br/>{esc(notes) or '&nbsp;'}", sty["body"])
    summary = [
        [Paragraph("Sub Total", sty["body"]), Paragraph(esc(format_money(invoice.get("subtotal"))), sty["right"])],
        [Paragraph("Diskon", sty["body"]), Paragraph(esc(format_money(invoice.get("discount_amount"))), sty["right"])],
        [
            Paragraph(f"PPN ({invoice.get('ppn_percent') if invoice.get('ppn_percent') not in (None, '') else 0}%)", sty["body"]),
            Paragraph(esc(format_money(invoice.get("ppn_amount"))), sty["right"]),
        ],
    ]
    for charge in invoice.get("other_charges") or []:
        if not isinstance(charge, dict):
            continue
        label = str(charge.get("description") or "").strip() or "Biaya lainnya"
        summary.append([
            Paragraph(esc(label), sty["body"]),
            Paragraph(esc(format_money(charge.get("amount"))), sty["right"]),
        ])
    if not omit_payment_rows:
        summary.append([
            Paragraph("Uang dimuka", sty["body"]),
            Paragraph(esc(format_money(invoice.get("paid_total"))), sty["right"]),
        ])
    summary.append([
        Paragraph("<b>Total</b>", sty["bold"]),
        Paragraph(f"<b>{esc(format_money(invoice.get('grand_total')))}</b>", sty["right_bold"]),
    ])
    summary_table = Table(summary, colWidths=[32 * mm, 28 * mm])
    summary_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f4f4f4")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    note_width = width - 64 * mm
    lower = Table(
        [[note, summary_table]],
        colWidths=[note_width, 64 * mm],
    )
    lower.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (0, 0), 0.7, colors.black),
        ("LEFTPADDING", (0, 0), (0, 0), 4),
        ("RIGHTPADDING", (0, 0), (0, 0), 4),
        ("TOPPADDING", (0, 0), (0, 0), 3),
        ("BOTTOMPADDING", (0, 0), (0, 0), 3),
        ("LEFTPADDING", (1, 0), (1, 0), 4),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    elements.append(lower)
    elements.append(Spacer(1, 4 * mm))

    if use_generic_footer:
        # Phase 2: controlled multi-source footer. Body/totals/header path unchanged.
        elements.extend(
            build_generic_invoice_footer_flowables(
                elements=footer_elements,
                context=print_context,
                design=design,
                sty=sty,
                width=width,
            )
        )
    else:
        # Exact V1 legacy footer path.
        sales_name = invoice_sales_label(invoice)

        gap = 4 * mm
        sig_w = (width - 68 * mm - (2 * gap)) / 2

        def signature_column(title: str, footer: str, middle: str = "", image_bytes: Optional[bytes] = None) -> Table:
            sig_img = signature_image_flowable(image_bytes, max_width=sig_w - 4 * mm, max_height=14 * mm)
            middle_cell = sig_img if sig_img is not None else Paragraph(
                f"<b>{esc(middle)}</b>" if middle else "&nbsp;",
                sty["center"],
            )
            # Keep row height bounded: image replaces blank pad; text-only keeps existing pad.
            mid_top = 2 * mm if sig_img is not None else 10 * mm
            column = Table(
                [
                    [Paragraph(esc(title), sty["center"])],
                    [middle_cell],
                    [Paragraph(esc(footer), sty["center"])],
                ],
                colWidths=[sig_w],
            )
            column.setStyle(TableStyle([
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 1), (-1, 1), "BOTTOM"),
                ("TOPPADDING", (0, 1), (-1, 1), mid_top),
                ("BOTTOMPADDING", (0, 1), (-1, 1), 3),
                ("LINEABOVE", (0, 2), (-1, 2), 0.6, colors.black),
                ("TOPPADDING", (0, 2), (-1, 2), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]))
            return column

        prepared = signature_column(
            str(inv_design.get("prepared_label") or "Disiapkan Oleh"),
            str(inv_design.get("prepared_footer") or "Admin"),
            image_bytes=pdf_assets.get("prepared_signature_image"),
        )
        approved = signature_column(
            str(inv_design.get("approved_label") or "Disetujui Oleh"),
            str(inv_design.get("approved_footer") or "Pengelola"),
            sales_name,
            image_bytes=pdf_assets.get("approved_signature_image"),
        )
        estimasi_title = str(inv_design.get("estimasi_title") or "")
        estimasi_lines = inv_design.get("estimasi_lines") or []
        estimasi_html = "<b>" + esc(estimasi_title) + "</b><br/>" + "<br/>".join(esc(line) for line in estimasi_lines)
        estimasi = Paragraph(estimasi_html, sty["red_small"])
        estimasi_table = Table([[estimasi]], colWidths=[68 * mm])
        estimasi_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1.2, accent),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        blank = Paragraph("&nbsp;", sty["small"])
        sign = Table(
            [[prepared, blank, approved, blank, estimasi_table]],
            colWidths=[sig_w, gap, sig_w, gap, 68 * mm],
        )
        sign.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (1, 0), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 1),
            ("RIGHTPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        elements.append(sign)
    return build_pdf(elements, A4)
