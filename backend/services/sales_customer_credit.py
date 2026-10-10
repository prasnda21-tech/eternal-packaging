"""Customer credit from sales-receipt overpayments.

Credit is cash already received and not yet applied to an invoice.
It is not a payment term, a discount, extra revenue, or a credit note.
Available balance is derived from the source receipt and its applications.
Draft receipts do not make credit usable and do not post a journal.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.accounting_periods import PeriodLockedError
from services.customer_identity import extract_stable_customer_id
from services.financial_statement_common import MONEY_Q, money_float
from services.journal_core import (
    JournalConflictError,
    JournalValidationError,
    find_primary_journal_for_source,
    post_balanced_journal,
    reverse_journal,
)
from services.sales_receipt_posting import (
    ACCOUNTING_POSTED,
    accounting_status_from_journal,
    assert_invoice_allows_receipt_journal,
    find_sales_receipt_source_journal,
    receipt_allocated_amount,
    receipt_customer_credit_amount,
    receipt_is_cancelled,
    resolve_invoice_receivable_account,
)

COLLECTION = "sales_customer_credit_applications"
SOURCE_TYPE = "sales_customer_credit_application"
SOURCE_REVERSAL = "sales_customer_credit_application_reversal"
CLAIM_FIELD = "customer_credit_claim"
CLAIM_AT_FIELD = "customer_credit_claim_at"
CLAIM_TTL_SECONDS = 120

# Sales documents are Rupiah ledgers. An empty currency is this canonical
# code, not a match for every other currency.
CANONICAL_CURRENCY = "IDR"
IDENTITY_BLOCK_REASON = (
    "Identitas pelanggan tidak dapat dibuktikan. "
    "Saldo tidak dapat dipakai lintas faktur."
)
STALE_CLAIM_MESSAGE = (
    "Klaim saldo tidak lagi berlaku. Hasil pemakaian yang lebih baru tetap dipertahankan."
)
UNMAPPED_DEPOSIT_MESSAGE = (
    "Akun Titipan/Uang Muka Pelanggan belum dipetakan pada Pengaturan Akuntansi Penjualan."
)
SOURCE_IN_USE_MESSAGE = (
    "Penerimaan ini tidak dapat dibatalkan karena saldonya sudah dipakai. "
    "Balikkan pemakaian saldo terlebih dahulu."
)
DRAFT_CREDIT_REASON = "Saldo belum tersedia sebelum penerimaan diposting."


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_money(raw: Any, field_name: str) -> Decimal:
    if raw is None or raw == "":
        raise HTTPException(400, f"{field_name} wajib diisi.")
    try:
        amount = Decimal(str(raw).strip().replace(",", ".")).quantize(MONEY_Q)
    except Exception as exc:
        raise HTTPException(400, f"{field_name} tidak valid.") from exc
    if amount != amount.quantize(MONEY_Q):
        raise HTTPException(400, f"{field_name} tidak valid.")
    return amount


def parse_positive_money(raw: Any, field_name: str) -> Decimal:
    amount = parse_money(raw, field_name)
    if amount <= 0:
        raise HTTPException(400, f"{field_name} harus lebih besar dari 0.")
    return amount


def _scope_value(doc: Optional[dict], keys: tuple) -> str:
    for key in keys:
        value = str((doc or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def branch_scope(doc: Optional[dict]) -> str:
    return _scope_value(doc, ("branch_id", "cabang_id", "_branch_id"))


def currency_scope(doc: Optional[dict]) -> str:
    return _scope_value(doc, ("currency", "currency_code"))


def effective_currency(value: Any) -> str:
    """Resolve one document or raw code. Blank becomes the canonical Rupiah code."""
    if isinstance(value, dict):
        raw = currency_scope(value)
    else:
        raw = str(value or "").strip()
    return raw or CANONICAL_CURRENCY


def _scopes_match(left: str, right: str, label: str) -> None:
    if not left and not right:
        return
    if left and right and left == right:
        return
    raise HTTPException(409, f"{label} sumber saldo dan faktur tujuan tidak sama.")


def assert_same_currency(source: Any, target: Any) -> str:
    left = effective_currency(source)
    right = effective_currency(target)
    if left != right:
        raise HTTPException(409, "Mata uang sumber saldo dan faktur tujuan tidak sama.")
    return left


def _actor(user: Optional[dict]) -> Dict[str, str]:
    user = user or {}
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": str(user.get("name") or user.get("email") or ""),
        "role": str(user.get("role") or ""),
    }


def credit_summary(receipt: Optional[dict], *, accounting_posted: bool) -> Dict[str, Any]:
    """Distinguish a real zero balance from draft, cancelled, or unproven credit."""
    credit = receipt_customer_credit_amount(receipt)
    applied = parse_money((receipt or {}).get("customer_credit_applied") or 0, "Pemakaian saldo")
    if applied < 0:
        applied = Decimal("0.00")
    customer_id = str((receipt or {}).get("customer_id") or "").strip() or None
    cancelled = receipt_is_cancelled(receipt)
    reason = ""
    state = "none"
    available = Decimal("0.00")
    if credit > 0 and not customer_id:
        state = "unproven"
        reason = str((receipt or {}).get("customer_credit_block_reason") or IDENTITY_BLOCK_REASON)
    elif credit > 0 and cancelled:
        state = "cancelled"
        reason = "Penerimaan dibatalkan. Saldo tidak tersedia."
    elif credit > 0 and not accounting_posted:
        state = "draft"
        reason = DRAFT_CREDIT_REASON
    elif credit > 0:
        available = credit - applied
        if available < 0:
            available = Decimal("0.00")
        state = "available" if available > 0 else "used"
    return {
        "amount": money_float(credit),
        "applied": money_float(applied if accounting_posted and not cancelled else Decimal("0.00")),
        "available": money_float(available),
        "state": state,
        "customer_id": customer_id,
        "reason": reason or None,
    }


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index("invoice_id")
    await db[COLLECTION].create_index("source_receipt_id")
    await db[COLLECTION].create_index(
        "idempotency_key",
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="credit_application_idempotency",
    )


async def stable_customer_for_invoice(db, invoice: dict) -> Optional[str]:
    order = None
    po_id = str((invoice or {}).get("po_id") or "").strip()
    if po_id:
        order = await db.orders.find_one({"id": po_id}, {"_id": 0})
    return extract_stable_customer_id(order) or extract_stable_customer_id(invoice)


async def scope_from_invoice(db, invoice: dict) -> Dict[str, str]:
    order = None
    po_id = str((invoice or {}).get("po_id") or "").strip()
    if po_id:
        order = await db.orders.find_one({"id": po_id}, {"_id": 0})
    return {
        "branch_id": branch_scope(order) or branch_scope(invoice),
        "currency": currency_scope(order) or currency_scope(invoice),
    }


def split_receipt_amounts(amount: Decimal, outstanding: Decimal, allocated_raw: Any) -> tuple:
    if outstanding < 0:
        outstanding = Decimal("0.00")
    if allocated_raw is None or allocated_raw == "":
        allocated = amount if amount <= outstanding else outstanding
    else:
        allocated = parse_money(allocated_raw, "Alokasi faktur")
    if allocated <= 0:
        raise HTTPException(400, "Alokasi ke faktur harus lebih besar dari 0.")
    if allocated > amount:
        raise HTTPException(400, "Total alokasi tidak boleh melebihi jumlah diterima.")
    if allocated > outstanding:
        raise HTTPException(
            400,
            f"Alokasi (Rp {money_float(allocated):,.2f}) melebihi sisa tagihan faktur "
            f"(Rp {money_float(outstanding):,.2f}).",
        )
    credit = (amount - allocated).quantize(MONEY_Q)
    return allocated, credit


async def active_applied_total(db, source_receipt_id: str) -> Decimal:
    docs = await db[COLLECTION].find(
        {
            "source_receipt_id": str(source_receipt_id),
            "financial_status": "active",
            "status": "posted",
        },
        {"_id": 0, "amount": 1},
    ).to_list(500)
    total = Decimal("0.00")
    for doc in docs:
        total += parse_money(doc.get("amount") or 0, "Pemakaian saldo")
    return total.quantize(MONEY_Q)


async def invoice_credit_applied_total(db, invoice_id: str) -> Decimal:
    docs = await db[COLLECTION].find(
        {"invoice_id": str(invoice_id), "financial_status": "active", "status": "posted"},
        {"_id": 0, "amount": 1},
    ).to_list(500)
    total = Decimal("0.00")
    for doc in docs:
        total += parse_money(doc.get("amount") or 0, "Pemakaian saldo")
    return total.quantize(MONEY_Q)


def _claim_cutoff(now: Optional[datetime] = None) -> str:
    current = now or datetime.now(timezone.utc)
    return (current - timedelta(seconds=CLAIM_TTL_SECONDS)).isoformat()


def claim_is_active(receipt: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    token = str((receipt or {}).get(CLAIM_FIELD) or "").strip()
    if not token:
        return False
    raw = str((receipt or {}).get(CLAIM_AT_FIELD) or "").strip()
    if not raw:
        return False
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        claimed_at = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - claimed_at).total_seconds() < CLAIM_TTL_SECONDS


async def _acquire_claim(
    db, receipt_id: str, token: str, *, owned_cancel_fence: Optional[str] = None
) -> Optional[dict]:
    """CAS credit claim. Active receipt cancel fence rejects atomically.

    Own-token renew is allowed. A foreign expired credit claim may be taken over
    only when no foreign cancellation fence is present. Cancel-owned reverse may
    pass ``owned_cancel_fence`` so credit fencing for that Hapus can proceed.
    """
    from services.sales_business_cancel import RECEIPT_FENCE_FIELD, receipt_cancel_fence_absent_clause

    cutoff = _claim_cutoff()
    fence_ok: Dict[str, Any] = receipt_cancel_fence_absent_clause()
    owned = str(owned_cancel_fence or "").strip()
    if owned:
        fence_ok = {
            "$or": [
                receipt_cancel_fence_absent_clause(),
                {RECEIPT_FENCE_FIELD: owned},
            ]
        }
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            "$and": [
                fence_ok,
                {
                    "$or": [
                        {CLAIM_FIELD: {"$exists": False}},
                        {CLAIM_FIELD: None},
                        {CLAIM_FIELD: ""},
                        {CLAIM_AT_FIELD: {"$exists": False}},
                        {CLAIM_AT_FIELD: None},
                        {CLAIM_AT_FIELD: ""},
                        {CLAIM_AT_FIELD: {"$lte": cutoff}},
                        {CLAIM_FIELD: token},
                    ]
                },
            ],
        },
        {"$set": {CLAIM_FIELD: token, CLAIM_AT_FIELD: now_iso(), "updated_at": now_iso()}},
        return_document=ReturnDocument.AFTER,
    )


async def _release_claim(db, receipt_id: str, token: str) -> None:
    await db.sales_receipts.update_one(
        {"id": str(receipt_id), CLAIM_FIELD: token},
        {"$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""}, "$set": {"updated_at": now_iso()}},
    )


def _committed_credit_clause() -> Dict[str, Any]:
    """Amounts that can still become, or already are, a credit journal."""
    return {
        "$or": [
            {"status": "posted", "financial_status": "active"},
            {"status": "posting"},
            {"status": "reserving", "reserved": True},
        ]
    }


async def _committed_credit_total(db, receipt_id: str) -> Decimal:
    docs = await db[COLLECTION].find(
        {"source_receipt_id": str(receipt_id), **_committed_credit_clause()},
        {"_id": 0, "amount": 1},
    ).to_list(500)
    total = Decimal("0.00")
    for doc in docs:
        total += parse_money(doc.get("amount") or 0, "Pemakaian saldo")
    return total.quantize(MONEY_Q)


async def _sync_applied_counter(db, receipt_id: str, token: str) -> Decimal:
    """While this request holds the claim, align the CAS counter with commitments.

    Posted history alone is not enough: a fenced reservation can still journal
    after its lease timestamp expires, so the counter keeps that amount too.
    """
    applied = await _committed_credit_total(db, receipt_id)
    await db.sales_receipts.update_one(
        {"id": str(receipt_id), CLAIM_FIELD: token},
        {"$set": {"customer_credit_applied": money_float(applied), "updated_at": now_iso()}},
    )
    return applied


async def _reserve_credit(db, receipt_id: str, token: str, amount: Decimal) -> Optional[dict]:
    return await db.sales_receipts.find_one_and_update(
        {
            "id": str(receipt_id),
            CLAIM_FIELD: token,
            "$expr": {
                "$gte": [
                    {
                        "$subtract": [
                            {"$ifNull": ["$customer_credit_amount", 0]},
                            {"$ifNull": ["$customer_credit_applied", 0]},
                        ]
                    },
                    money_float(amount),
                ]
            },
        },
        {"$inc": {"customer_credit_applied": money_float(amount)}, "$set": {"updated_at": now_iso()}},
        return_document=ReturnDocument.AFTER,
    )


async def _release_reservation(db, receipt_id: str, amount: Decimal) -> None:
    await db.sales_receipts.update_one(
        {"id": str(receipt_id), "customer_credit_applied": {"$gte": money_float(amount)}},
        {"$inc": {"customer_credit_applied": -money_float(amount)}, "$set": {"updated_at": now_iso()}},
    )


async def hold_source_credit_claim(
    db, receipt_id: str, token: str, *, owned_cancel_fence: Optional[str] = None
) -> Optional[dict]:
    return await _acquire_claim(
        db, receipt_id, token, owned_cancel_fence=owned_cancel_fence
    )


async def release_source_credit_claim(db, receipt_id: str, token: str) -> None:
    await _release_claim(db, receipt_id, token)


async def _claim_is_live(db, receipt_id: str, token: str) -> bool:
    rec = await db.sales_receipts.find_one(
        {"id": str(receipt_id), CLAIM_FIELD: token},
        {"_id": 0, CLAIM_FIELD: 1, CLAIM_AT_FIELD: 1},
    )
    return bool(rec) and claim_is_active(rec)


async def _begin_journal_fence(db, application_id: str, token: str) -> Optional[dict]:
    """Atomically grant this reservation the sole right to insert its journal.

    The transition is one document update. A later worker can still drop a
    plain ``reserving`` row, but it cannot drop ``posting`` and then spend or
    reverse the same balance out from under the insert.
    """
    return await db[COLLECTION].find_one_and_update(
        {
            "id": str(application_id),
            "status": "reserving",
            "reserved": True,
            "claim_token": token,
        },
        {"$set": {
            "status": "posting",
            "journal_fence": token,
            "updated_at": now_iso(),
        }},
        return_document=ReturnDocument.AFTER,
    )


async def _abort_foreign_reservations(db, receipt_id: str, token: str) -> None:
    """Drop another worker's unfenced reservation. A journal fence stays put."""
    docs = await db[COLLECTION].find(
        {
            "source_receipt_id": str(receipt_id),
            "status": "reserving",
            "claim_token": {"$ne": token},
        },
        {"_id": 0},
    ).to_list(50)
    for doc in docs:
        journal = await find_primary_journal_for_source(db, SOURCE_TYPE, str(doc.get("id")))
        if journal and accounting_status_from_journal(journal) == ACCOUNTING_POSTED:
            await db[COLLECTION].update_one(
                {"id": doc["id"], "status": "reserving"},
                {"$set": {"status": "posted", "financial_status": "active", "updated_at": now_iso()}},
            )
            continue
        if not journal:
            await db[COLLECTION].delete_one({"id": doc["id"], "status": "reserving"})
    fenced = await db[COLLECTION].find(
        {
            "source_receipt_id": str(receipt_id),
            "status": "posting",
            "claim_token": {"$ne": token},
        },
        {"_id": 0},
    ).to_list(50)
    for doc in fenced:
        journal = await find_primary_journal_for_source(db, SOURCE_TYPE, str(doc.get("id")))
        if journal and accounting_status_from_journal(journal) == ACCOUNTING_POSTED:
            await db[COLLECTION].update_one(
                {"id": doc["id"], "status": "posting"},
                {"$set": {"status": "posted", "financial_status": "active", "updated_at": now_iso()}},
            )


async def _release_own_reservation(
    db, receipt_id: str, application_id: str, token: str, amount: Decimal,
) -> None:
    """Return this worker's reservation only while it still owns the live claim."""
    if not await _claim_is_live(db, receipt_id, token):
        await db[COLLECTION].update_one(
            {
                "id": application_id,
                "status": {"$in": ["reserving", "posting"]},
                "claim_token": token,
            },
            {"$set": {"reserved": False, "status": "aborted", "updated_at": now_iso()}},
        )
        return
    changed = await db[COLLECTION].update_one(
        {
            "id": application_id,
            "status": {"$in": ["reserving", "posting"]},
            "reserved": True,
            "claim_token": token,
        },
        {"$set": {"reserved": False, "status": "aborted", "updated_at": now_iso()}},
    )
    if not changed.modified_count:
        return
    await db.sales_receipts.update_one(
        {
            "id": str(receipt_id),
            CLAIM_FIELD: token,
            "customer_credit_applied": {"$gte": money_float(amount)},
        },
        {"$inc": {"customer_credit_applied": -money_float(amount)}, "$set": {"updated_at": now_iso()}},
    )


async def _sync_posted_counter(db, receipt_id: str) -> None:
    token = str(uuid.uuid4())
    claimed = await _acquire_claim(db, receipt_id, token)
    if not claimed:
        return
    try:
        await _abort_foreign_reservations(db, receipt_id, token)
        await _sync_applied_counter(db, receipt_id, token)
    finally:
        await _release_claim(db, receipt_id, token)


def _public_application(doc: dict) -> Dict[str, Any]:
    return {
        "id": doc.get("id"),
        "idempotency_key": doc.get("idempotency_key"),
        "source_receipt_id": doc.get("source_receipt_id"),
        "source_receipt_number": doc.get("source_receipt_number"),
        "customer_id": doc.get("customer_id"),
        "invoice_id": doc.get("invoice_id"),
        "invoice_number": doc.get("invoice_number"),
        "po_id": doc.get("po_id"),
        "amount": doc.get("amount"),
        "branch_id": doc.get("branch_id") or None,
        "currency": doc.get("currency") or None,
        "status": doc.get("status"),
        "financial_status": doc.get("financial_status"),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "reversed_at": doc.get("reversed_at"),
    }


async def list_applications_for_receipt(db, receipt_id: str) -> List[dict]:
    docs = await db[COLLECTION].find(
        {"source_receipt_id": str(receipt_id), "status": {"$in": ["posted", "reversed"]}},
        {"_id": 0},
    ).sort("created_at", 1).to_list(200)
    return [_public_application(doc) for doc in docs]


async def assert_source_receipt_reversible(db, receipt_id: str) -> None:
    count = await db[COLLECTION].count_documents(
        {
            "source_receipt_id": str(receipt_id),
            "$or": [
                {"financial_status": "active", "status": "posted"},
                {"status": "posting"},
            ],
        }
    )
    if count:
        raise HTTPException(409, SOURCE_IN_USE_MESSAGE)


async def release_unfenced_credit_reservations(db, receipt_id: str, token: str) -> None:
    """While holding the source claim, drop reservations that have not fenced a journal."""
    await _abort_foreign_reservations(db, receipt_id, token)
    await _sync_applied_counter(db, receipt_id, token)


async def _load_receipt(db, receipt_id: str) -> dict:
    rec = await db.sales_receipts.find_one({"id": str(receipt_id)}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Penerimaan Penjualan tidak ditemukan.")
    return rec


async def _heal_application(db, doc: dict, user: dict) -> dict:
    journal = await find_primary_journal_for_source(db, SOURCE_TYPE, str(doc.get("id")))
    status = accounting_status_from_journal(journal) if journal else ""
    if status == ACCOUNTING_POSTED and doc.get("status") != "posted":
        await db[COLLECTION].update_one(
            {"id": doc["id"]},
            {"$set": {"status": "posted", "financial_status": "active", "updated_at": now_iso()}},
        )
        doc["status"] = "posted"
        doc["financial_status"] = "active"
    elif status == "reversed" and doc.get("financial_status") != "cancelled":
        await _mark_application_reversed(db, doc, user)
        doc["status"] = "reversed"
        doc["financial_status"] = "cancelled"
    return doc


async def list_available_credit(db, *, customer_id: str = "", invoice_id: str = "") -> Dict[str, Any]:
    target_customer = str(customer_id or "").strip()
    invoice = None
    if invoice_id:
        invoice = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
        if not invoice:
            raise HTTPException(404, "Faktur Penjualan tidak ditemukan.")
        stable = await stable_customer_for_invoice(db, invoice)
        if not stable:
            return {
                "loaded": True,
                "customer_id": None,
                "available": 0,
                "blocked_reason": IDENTITY_BLOCK_REASON,
                "sources": [],
            }
        if target_customer and target_customer != stable:
            raise HTTPException(409, "Pelanggan sumber saldo dan faktur tujuan tidak sama.")
        target_customer = stable
    if not target_customer:
        raise HTTPException(400, "Pelanggan wajib dipilih dari identitas yang dapat dibuktikan.")

    receipts = await db.sales_receipts.find(
        {"customer_id": target_customer, "customer_credit_amount": {"$gt": 0}},
        {"_id": 0},
    ).sort("created_at", 1).to_list(500)
    sources = []
    total = Decimal("0.00")
    for rec in receipts:
        journal = await find_sales_receipt_source_journal(db, rec["id"])
        posted = accounting_status_from_journal(journal) == ACCOUNTING_POSTED
        summary = credit_summary(rec, accounting_posted=posted)
        if summary["state"] != "available":
            continue
        row = {
            "receipt_id": rec.get("id"),
            "receipt_number": rec.get("receipt_number"),
            "customer_id": target_customer,
            "available": summary["available"],
            "amount": summary["amount"],
            "state": "available",
        }
        sources.append(row)
        total += Decimal(str(summary["available"]))
    return {
        "loaded": True,
        "customer_id": target_customer,
        "available": money_float(total.quantize(MONEY_Q)),
        "blocked_reason": None,
        "sources": sources,
        "invoice_id": (invoice or {}).get("id"),
        "invoice_number": (invoice or {}).get("invoice_number"),
        "outstanding_amount": (invoice or {}).get("outstanding_amount"),
    }


async def _outstanding_now(db, invoice: dict) -> Decimal:
    from services.sales_receipts import receipt_counts_toward_outstanding

    receipts = await db.sales_receipts.find({"invoice_id": invoice["id"]}, {"_id": 0}).to_list(500)
    paid = Decimal("0.00")
    for rec in receipts:
        if receipt_counts_toward_outstanding(rec):
            paid += receipt_allocated_amount(rec)
    paid += await invoice_credit_applied_total(db, invoice["id"])
    grand = parse_money(invoice.get("grand_total") or 0, "Total faktur")
    outstanding = (grand - paid).quantize(MONEY_Q)
    if outstanding < 0:
        outstanding = Decimal("0.00")
    return outstanding


async def _lease_invoice_for_credit_mutation(db, invoice_id: str) -> str:
    """Hold the invoice receipt lease across a credit journal or invoice recalc.

    The lease compare-and-swap requires the revision fence to be absent, so a
    recovery that already started cannot post or rewrite invoice balances
    after the fence is acquired.
    """
    from services.sales_invoice_posting import acquire_receipt_create_claim

    token = str(uuid.uuid4())
    leased = await acquire_receipt_create_claim(db, str(invoice_id), token=token)
    if not leased:
        raise HTTPException(409, "Revisi faktur belum selesai.")
    return token


async def _release_invoice_credit_lease(db, invoice_id: str, token: str) -> None:
    from services.sales_invoice_posting import clear_receipt_create_claim

    await clear_receipt_create_claim(db, str(invoice_id), token=token)


async def _complete_posting_application(
    db, doc: dict, user: dict, application_date: str,
) -> Dict[str, Any]:
    """Finish a fenced reservation that already owns its journal slot."""
    invoice_id = str(doc.get("invoice_id") or "")
    if not invoice_id:
        raise HTTPException(400, "Faktur tujuan wajib diisi.")
    lease = await _lease_invoice_for_credit_mutation(db, invoice_id)
    try:
        return await _complete_posting_application_leased(db, doc, user, application_date)
    finally:
        await _release_invoice_credit_lease(db, invoice_id, lease)


async def _complete_posting_application_leased(
    db, doc: dict, user: dict, application_date: str,
) -> Dict[str, Any]:
    from services.sales_accounting_settings import require_customer_deposit_account

    journal = await find_primary_journal_for_source(db, SOURCE_TYPE, str(doc.get("id")))
    if not (journal and accounting_status_from_journal(journal) == ACCOUNTING_POSTED):
        deposit = await require_customer_deposit_account(db)
        receivable = await resolve_invoice_receivable_account(db, doc.get("invoice_id"))
        amount = parse_positive_money(doc.get("amount"), "Nominal pemakaian saldo")
        date = str(application_date or doc.get("created_at") or now_iso())[:10]
        try:
            await post_balanced_journal(
                db,
                date=date,
                source_type=SOURCE_TYPE,
                source_id=str(doc["id"]),
                description=(
                    f"Pemakaian saldo {doc.get('source_receipt_number') or doc.get('source_receipt_id')} "
                    f"untuk {doc.get('invoice_number') or doc.get('invoice_id')}"
                ),
                lines=[
                    {
                        "account_id": str(deposit["id"]),
                        "debit": amount,
                        "credit": Decimal("0.00"),
                        "description": "Titipan pelanggan",
                    },
                    {
                        "account_id": str(receivable["id"]),
                        "debit": Decimal("0.00"),
                        "credit": amount,
                        "description": "Piutang",
                    },
                ],
                posted_by=user,
            )
        except PeriodLockedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except JournalValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except JournalConflictError:
            existing_journal = await find_primary_journal_for_source(db, SOURCE_TYPE, str(doc["id"]))
            if not existing_journal:
                raise HTTPException(409, "Jurnal pemakaian saldo bentrok.")
    await db[COLLECTION].update_one(
        {"id": doc["id"], "status": {"$in": ["posting", "posted"]}},
        {"$set": {"status": "posted", "financial_status": "active", "updated_at": now_iso()}},
    )
    await _sync_posted_counter(db, doc.get("source_receipt_id"))
    from services.sales_receipts import _recalc_invoice_payment_from_receipts

    await _recalc_invoice_payment_from_receipts(db, doc.get("invoice_id"))
    stored = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
    return _public_application(stored or doc)


async def apply_customer_credit(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    source_id = str(payload.get("source_receipt_id") or "").strip()
    invoice_id = str(payload.get("invoice_id") or "").strip()
    if not source_id or not invoice_id:
        raise HTTPException(400, "Sumber saldo dan faktur tujuan wajib diisi.")
    amount = parse_positive_money(payload.get("amount"), "Nominal pemakaian saldo")
    idem = str(payload.get("idempotency_key") or "").strip()

    if idem:
        existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
        if existing:
            healed = await _heal_application(db, existing, user)
            journal = await find_primary_journal_for_source(db, SOURCE_TYPE, healed["id"])
            if healed.get("status") == "posted" or (
                journal and accounting_status_from_journal(journal) == ACCOUNTING_POSTED
            ):
                lease = await _lease_invoice_for_credit_mutation(db, healed.get("invoice_id"))
                try:
                    if healed.get("status") != "posted":
                        await db[COLLECTION].update_one(
                            {"id": healed["id"]},
                            {"$set": {"status": "posted", "financial_status": "active", "updated_at": now_iso()}},
                        )
                        healed["status"] = "posted"
                        healed["financial_status"] = "active"
                    await _sync_posted_counter(db, healed["source_receipt_id"])
                    from services.sales_receipts import _recalc_invoice_payment_from_receipts

                    await _recalc_invoice_payment_from_receipts(db, healed.get("invoice_id"))
                    stored = await db[COLLECTION].find_one({"id": healed["id"]}, {"_id": 0})
                    return _public_application(stored or healed)
                finally:
                    await _release_invoice_credit_lease(db, healed.get("invoice_id"), lease)
            if not journal:
                if healed.get("status") == "posting":
                    return await _complete_posting_application(
                        db, healed, user, str(payload.get("application_date") or ""),
                    )
                await db[COLLECTION].delete_one({"id": healed["id"], "status": "reserving"})
                await _sync_posted_counter(db, healed["source_receipt_id"])

    from services.sales_accounting_settings import require_customer_deposit_account

    deposit = await require_customer_deposit_account(db)
    source = await _load_receipt(db, source_id)
    if receipt_is_cancelled(source):
        raise HTTPException(409, "Penerimaan yang dibatalkan tidak menyediakan saldo.")
    journal = await find_sales_receipt_source_journal(db, source["id"])
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED:
        raise HTTPException(409, DRAFT_CREDIT_REASON)
    summary = credit_summary(source, accounting_posted=True)
    if summary["state"] == "unproven" or not source.get("customer_id"):
        raise HTTPException(409, IDENTITY_BLOCK_REASON)
    if receipt_customer_credit_amount(source) <= 0:
        raise HTTPException(409, "Penerimaan ini tidak memiliki saldo pelanggan.")

    invoice = await db.sales_invoices.find_one({"id": invoice_id}, {"_id": 0})
    if not invoice:
        invoice = await db.sales_invoices.find_one({"invoice_number": invoice_id}, {"_id": 0})
    if not invoice:
        raise HTTPException(404, "Faktur Penjualan tidak ditemukan.")
    await assert_invoice_allows_receipt_journal(db, invoice["id"])
    target_customer = await stable_customer_for_invoice(db, invoice)
    if not target_customer:
        raise HTTPException(409, IDENTITY_BLOCK_REASON)
    if target_customer != str(source.get("customer_id")):
        raise HTTPException(409, "Pelanggan sumber saldo dan faktur tujuan tidak sama.")
    target_scope = await scope_from_invoice(db, invoice)
    _scopes_match(branch_scope(source), target_scope["branch_id"], "Cabang")
    assert_same_currency(source, target_scope["currency"])
    receivable = await resolve_invoice_receivable_account(db, invoice["id"])
    from services.sales_invoice_posting import (
        RECEIPT_CREATE_CLAIM_CONFLICT_MESSAGE,
        acquire_receipt_create_claim,
        clear_receipt_create_claim,
    )

    invoice_claim = str(uuid.uuid4())
    leased_invoice = await acquire_receipt_create_claim(db, invoice["id"], token=invoice_claim)
    if not leased_invoice:
        raise HTTPException(409, RECEIPT_CREATE_CLAIM_CONFLICT_MESSAGE)

    try:
        outstanding = await _outstanding_now(db, invoice)
        if amount > outstanding:
            raise HTTPException(400, "Pemakaian saldo melebihi sisa tagihan faktur.")
        app_id = str(uuid.uuid4())
        doc = {
            "id": app_id,
            "idempotency_key": idem or None,
            "source_receipt_id": source["id"],
            "source_receipt_number": source.get("receipt_number"),
            "customer_id": target_customer,
            "invoice_id": invoice["id"],
            "invoice_number": invoice.get("invoice_number"),
            "po_id": invoice.get("po_id"),
            "amount": money_float(amount),
            "branch_id": target_scope["branch_id"] or None,
            "currency": effective_currency(target_scope["currency"]),
            "status": "reserving",
            "financial_status": "active",
            "reserved": False,
            "claim_token": "",
            "created_at": now_iso(),
            "created_by": _actor(user),
        }
        try:
            await db[COLLECTION].insert_one(dict(doc))
        except DuplicateKeyError:
            if idem:
                existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
                if existing:
                    return _public_application(await _heal_application(db, existing, user))
            raise HTTPException(409, "Pemakaian saldo duplikat terdeteksi.")

        token = str(uuid.uuid4())
        try:
            claimed = await _acquire_claim(db, source["id"], token)
            if not claimed:
                raise HTTPException(409, "Saldo pelanggan sedang dipakai. Ulangi permintaan.")
            await db[COLLECTION].update_one({"id": app_id}, {"$set": {"claim_token": token}})
            await _abort_foreign_reservations(db, source["id"], token)
            await _sync_applied_counter(db, source["id"], token)
            fresh = await _load_receipt(db, source["id"])
            if receipt_is_cancelled(fresh):
                raise HTTPException(409, "Penerimaan yang dibatalkan tidak menyediakan saldo.")
            fresh_journal = await find_sales_receipt_source_journal(db, source["id"])
            if accounting_status_from_journal(fresh_journal) != ACCOUNTING_POSTED:
                raise HTTPException(409, DRAFT_CREDIT_REASON)
            available = receipt_customer_credit_amount(fresh) - parse_money(
                fresh.get("customer_credit_applied") or 0, "Pemakaian saldo"
            )
            if amount > available:
                raise HTTPException(400, "Pemakaian saldo melebihi saldo tersedia.")
            reserved_doc = await _reserve_credit(db, source["id"], token, amount)
            if not reserved_doc:
                raise HTTPException(409, "Pemakaian saldo melebihi saldo tersedia.")
            await db[COLLECTION].update_one(
                {"id": app_id, "claim_token": token},
                {"$set": {"reserved": True}},
            )
            if not await _claim_is_live(db, source["id"], token):
                raise HTTPException(409, STALE_CLAIM_MESSAGE)
            if not await _begin_journal_fence(db, app_id, token):
                raise HTTPException(409, STALE_CLAIM_MESSAGE)
            try:
                await post_balanced_journal(
                    db,
                    date=str(payload.get("application_date") or now_iso()[:10])[:10],
                    source_type=SOURCE_TYPE,
                    source_id=app_id,
                    description=(
                        f"Pemakaian saldo {source.get('receipt_number') or source['id']} "
                        f"untuk {invoice.get('invoice_number') or invoice['id']}"
                    ),
                    lines=[
                        {
                            "account_id": str(deposit["id"]),
                            "debit": amount,
                            "credit": Decimal("0.00"),
                            "description": "Titipan pelanggan",
                        },
                        {
                            "account_id": str(receivable["id"]),
                            "debit": Decimal("0.00"),
                            "credit": amount,
                            "description": "Piutang",
                        },
                    ],
                    posted_by=user,
                )
            except PeriodLockedError as exc:
                raise HTTPException(409, str(exc)) from exc
            except JournalValidationError as exc:
                raise HTTPException(400, str(exc)) from exc
            except JournalConflictError:
                existing_journal = await find_primary_journal_for_source(db, SOURCE_TYPE, app_id)
                if not existing_journal:
                    raise HTTPException(409, "Jurnal pemakaian saldo bentrok.")
            await db[COLLECTION].update_one(
                {"id": app_id},
                {"$set": {"status": "posted", "financial_status": "active", "updated_at": now_iso()}},
            )
        except Exception:
            journal = await find_primary_journal_for_source(db, SOURCE_TYPE, app_id)
            if journal and accounting_status_from_journal(journal) == ACCOUNTING_POSTED:
                await db[COLLECTION].update_one(
                    {"id": app_id},
                    {"$set": {"status": "posted", "financial_status": "active", "updated_at": now_iso()}},
                )
                if await _claim_is_live(db, source["id"], token):
                    await _sync_applied_counter(db, source["id"], token)
            else:
                await _release_own_reservation(db, source["id"], app_id, token, amount)
                await db[COLLECTION].delete_one(
                    {"id": app_id, "status": {"$in": ["reserving", "aborted"]}},
                )
                raise
        finally:
            await _release_claim(db, source["id"], token)

        from services.sales_receipts import _recalc_invoice_payment_from_receipts

        await _recalc_invoice_payment_from_receipts(db, invoice["id"])
        stored = await db[COLLECTION].find_one({"id": app_id}, {"_id": 0})
        return _public_application(stored or doc)
    finally:
        await clear_receipt_create_claim(db, invoice["id"], token=invoice_claim)


async def _mark_application_reversed(db, doc: dict, user: dict) -> None:
    if doc.get("credit_restored"):
        await db[COLLECTION].update_one(
            {"id": doc["id"]},
            {"$set": {
                "status": "reversed",
                "financial_status": "cancelled",
                "updated_at": now_iso(),
            }},
        )
        return
    amount = parse_positive_money(doc.get("amount"), "Nominal pemakaian saldo")
    await _release_reservation(db, doc["source_receipt_id"], amount)
    await db[COLLECTION].update_one(
        {"id": doc["id"], "credit_restored": {"$ne": True}},
        {"$set": {
            "status": "reversed",
            "financial_status": "cancelled",
            "credit_restored": True,
            "reversed_at": now_iso(),
            "reversed_by": _actor(user),
            "updated_at": now_iso(),
        }},
    )


async def reverse_customer_credit_application(db, application_id: str, user: dict) -> Dict[str, Any]:
    doc = await db[COLLECTION].find_one({"id": str(application_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Pemakaian saldo tidak ditemukan.")
    doc = await _heal_application(db, doc, user)
    if doc.get("financial_status") == "cancelled" or doc.get("status") == "reversed":
        return _public_application(doc)
    if doc.get("status") != "posted":
        raise HTTPException(409, "Hanya pemakaian saldo yang sudah diposting yang dapat dibalik.")
    journal = await find_primary_journal_for_source(db, SOURCE_TYPE, doc["id"])
    if accounting_status_from_journal(journal) != ACCOUNTING_POSTED or not journal:
        raise HTTPException(409, "Jurnal pemakaian saldo tidak ditemukan.")
    token = str(uuid.uuid4())
    claimed = await _acquire_claim(db, doc["source_receipt_id"], token)
    if not claimed:
        raise HTTPException(409, "Saldo pelanggan sedang dipakai. Ulangi permintaan.")
    try:
        fresh = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0}) or doc
        if fresh.get("status") == "reversed":
            return _public_application(fresh)
        try:
            await reverse_journal(
                db,
                journal["id"],
                source_type=SOURCE_REVERSAL,
                source_id=str(doc["id"]),
                posted_by=user,
                description=f"Reversal pemakaian saldo {doc.get('source_receipt_number') or doc['id']}",
            )
        except PeriodLockedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except JournalValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except JournalConflictError:
            still = await find_primary_journal_for_source(db, SOURCE_TYPE, doc["id"])
            if accounting_status_from_journal(still) != "reversed":
                raise HTTPException(409, "Reversal pemakaian saldo bentrok.")
        except KeyError as exc:
            raise HTTPException(404, "Jurnal pemakaian saldo tidak ditemukan.") from exc
        await _mark_application_reversed(db, fresh, user)
    finally:
        await _release_claim(db, doc["source_receipt_id"], token)

    from services.sales_receipts import _recalc_invoice_payment_from_receipts

    await _recalc_invoice_payment_from_receipts(db, doc.get("invoice_id"))
    stored = await db[COLLECTION].find_one({"id": doc["id"]}, {"_id": 0})
    return _public_application(stored or doc)
