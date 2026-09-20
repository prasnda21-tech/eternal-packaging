"""Purchase Goods Receipt → journal_core posting (Phase 4B.2).

Explicit POST only. Does not auto-post on create, write COGS, or mutate
physical stock. Journal is Dr Inventory / Cr Purchase Clearing (GRNI).
Canonical source is purchase_goods_receipts.id.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.accounting_periods import PeriodLockedError
from services.inventory_accounting import (
    CANONICAL_GR,
    CANONICAL_GR_REVERSAL,
    REASON_CANCELLED,
    REASON_INVALID_UNIT_CONVERSION,
    REASON_LEGACY_BEFORE_EFFECTIVE_DATE,
    REASON_MISSING_MASTER_ITEM,
    REASON_MISSING_PO_LINE,
    REASON_NON_INVENTORY_ITEM,
    REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED,
    REASON_UNKNOWN_ITEM_TYPE,
    WARNING_PARTIAL_INVENTORY_COVERAGE,
    get_goods_receipt_accounting_readiness,
    readiness_for_receipts,
)
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_POSTED,
    STATUS_REVERSED,
    JournalConflictError,
    JournalValidationError,
    find_primary_journal_for_source,
    post_balanced_journal,
    reverse_journal,
)
from services.purchase_accounting_settings import (
    _load_account,
    _validate_inventory,
    _validate_purchase_clearing,
    get_purchase_accounting_settings,
)
from services.purchase_common import cancellation_fields, is_cancelled, money_round, now_iso

SOURCE_TYPE = CANONICAL_GR
SOURCE_REVERSAL = CANONICAL_GR_REVERSAL
PERM_POST = "purchase.receipt.accounting.post"
PERM_REVERSE = "purchase.receipt.accounting.reverse"

CLAIM_FIELD = "accounting_posting_claim"
CLAIM_AT_FIELD = "accounting_posting_claim_at"
REVERSE_CLAIM_FIELD = "accounting_reverse_claim"
REVERSE_CLAIM_AT_FIELD = "accounting_reverse_claim_at"
CANCEL_CLAIM_FIELD = "receipt_cancel_claim"
CANCEL_CLAIM_AT_FIELD = "receipt_cancel_claim_at"
POSTING_BASIS_FIELD = "accounting_posting_basis"

ACCOUNTING_POST_CLAIM_TTL_SECONDS = 600
ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS = 600
RECEIPT_CANCEL_CLAIM_TTL_SECONDS = 600
REVERSAL_REASON_MIN_LEN = 3

ACCOUNTING_NOT_POSTED = "not_posted"
ACCOUNTING_POSTED = "posted"
ACCOUNTING_REVERSED = "reversed"

RECOVERY_REASON = "goods_receipt_reversal_pending_operational_cancel"
RECOVERY_REQUIRED_CODE = "ACCOUNTING_RECOVERY_REQUIRED"
STOCK_OPNAME_CODE = "STOCK_OPNAME_ACCOUNTING_REQUIRED"

CANCEL_POSTED_MESSAGE = (
    "Penerimaan sudah diposting ke accounting. Gunakan Reverse Accounting."
)
CANCEL_CLAIM_MESSAGE = (
    "Penerimaan sedang diposting ke accounting. Gunakan Reverse Accounting."
)
CANCEL_IN_PROGRESS_MESSAGE = "Penerimaan sedang dibatalkan."
REVERSE_CLAIM_MESSAGE = "Penerimaan sedang di-reverse dari accounting."
POSTING_CLAIM_REVERSE_MESSAGE = (
    "Penerimaan sedang diposting ke accounting dan tidak dapat di-reverse."
)
UNPOSTED_REVERSE_MESSAGE = (
    "Hanya penerimaan yang sudah diposting ke accounting yang dapat di-reverse."
)
CANCELLED_POST_MESSAGE = "Penerimaan yang dibatalkan tidak dapat diposting ke accounting."
REASON_REQUIRED_MESSAGE = "Alasan reversal wajib diisi minimal 3 karakter."
SETTINGS_NOT_READY_MESSAGE = "Pengaturan Akuntansi Persediaan belum dikonfigurasi."
NO_ELIGIBLE_LINES_MESSAGE = (
    "Tidak ada baris Persediaan yang memenuhi syarat untuk diposting."
)
ACTIVE_INVOICE_REVERSE_MESSAGE = (
    "Penerimaan tidak dapat di-reverse karena sudah memiliki faktur. "
    "Batalkan faktur terkait terlebih dahulu."
)
STOCK_OPNAME_MESSAGE = (
    "STOCK_OPNAME_ACCOUNTING_REQUIRED: pembatalan fisik receipt ini "
    "sudah digantikan Stock Opname / koreksi absolut. "
    "Reversal accounting otomatis ditunda."
)
RECOVERY_REQUIRED_MESSAGE = (
    "Reversal Accounting belum selesai. Diperlukan pemulihan."
)
LEGACY_DATE_MESSAGE = (
    "Tanggal penerimaan sebelum Tanggal Mulai Accounting Persediaan."
)
INVALID_UOM_MESSAGE = "Konversi satuan tidak valid untuk posting Persediaan."
UNKNOWN_ITEM_TYPE_MESSAGE = "Tipe item tidak dikenali. Posting accounting diblokir."
FATAL_LINE_DATA_MESSAGE = "Data baris penerimaan tidak lengkap. Posting accounting diblokir."

COLLECTION = "purchase_goods_receipts"


def _money(raw: Any, field: str) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HTTPException(400, f"{field} tidak valid.") from exc


def journal_date_from_receipt(receipt: dict) -> str:
    raw = str((receipt or {}).get("receipt_date") or "").strip()
    if not raw:
        raise HTTPException(400, "Tanggal penerimaan tidak valid.")
    return raw[:10]


def accounting_status_from_journal(journal: Optional[dict]) -> str:
    if not journal:
        return ACCOUNTING_NOT_POSTED
    status = str(journal.get("status") or "").strip().lower()
    if status == STATUS_REVERSED:
        return ACCOUNTING_REVERSED
    if status == STATUS_POSTED:
        return ACCOUNTING_POSTED
    return ACCOUNTING_NOT_POSTED


def _journal_amount(journal: Optional[dict], line_totals: Optional[Dict[str, Decimal]] = None) -> Optional[float]:
    if not journal:
        return None
    jid = str(journal.get("id") or "")
    if line_totals and jid in line_totals:
        return money_round(line_totals[jid])
    total = Decimal("0")
    for ln in journal.get("lines") or []:
        total += _safe_dec(ln.get("debit"))
    if total > 0:
        return money_round(total)
    return None


def _safe_dec(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


async def find_goods_receipt_source_journal(db, receipt_id: str) -> Optional[dict]:
    return await find_primary_journal_for_source(db, SOURCE_TYPE, str(receipt_id))


async def find_goods_receipt_reversal_journal(db, original_journal_id: str) -> Optional[dict]:
    oid = str(original_journal_id or "").strip()
    if not oid:
        return None
    return await db[JOURNAL_ENTRIES].find_one({"reversal_of": oid}, {"_id": 0})


async def load_source_journals_by_receipt_ids(db, receipt_ids: List[str]) -> Dict[str, dict]:
    ids = [str(i) for i in receipt_ids if i]
    if not ids:
        return {}
    out: Dict[str, dict] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        rows = await db[JOURNAL_ENTRIES].find(
            {
                "source_type": SOURCE_TYPE,
                "source_id": {"$in": chunk},
                "reversal_of": None,
            },
            {"_id": 0, "id": 1, "source_id": 1, "status": 1},
        ).to_list(len(chunk) + 10)
        for doc in rows or []:
            out[str(doc.get("source_id"))] = doc
    return out


async def load_journal_debit_totals(db, journal_ids: List[str]) -> Dict[str, Decimal]:
    ids = [str(i) for i in journal_ids if i]
    totals: Dict[str, Decimal] = {}
    if not ids:
        return totals
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        lines = await db[JOURNAL_LINES].find(
            {"journal_id": {"$in": chunk}},
            {"_id": 0, "journal_id": 1, "debit": 1},
        ).to_list(len(chunk) * 20 + 20)
        for row in lines or []:
            jid = str(row.get("journal_id") or "")
            if not jid:
                continue
            totals[jid] = totals.get(jid, Decimal("0")) + _safe_dec(row.get("debit"))
    return totals


def raise_if_journal_blocks_cancel(journal: Optional[dict]) -> None:
    if accounting_status_from_journal(journal) == ACCOUNTING_POSTED:
        raise HTTPException(409, CANCEL_POSTED_MESSAGE)


def _parse_claim_at(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _claim_absent_clause(field: str) -> Dict[str, Any]:
    return {"$or": [{field: {"$exists": False}}, {field: None}, {field: ""}]}


def _claim_lease_is_active(
    doc: Optional[dict],
    token_field: str,
    at_field: str,
    ttl_seconds: int,
    *,
    now: Optional[datetime] = None,
) -> bool:
    token = str((doc or {}).get(token_field) or "").strip()
    if not token:
        return False
    claimed_at = _parse_claim_at((doc or {}).get(at_field))
    if claimed_at is None:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - claimed_at).total_seconds() < ttl_seconds


def _claim_absent_or_stale_clause(
    token_field: str,
    at_field: str,
    ttl_seconds: int,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=ttl_seconds)
    return {
        "$or": [
            {token_field: {"$exists": False}},
            {token_field: None},
            {token_field: ""},
            {at_field: {"$exists": False}},
            {at_field: None},
            {at_field: ""},
            {at_field: {"$lte": cutoff.isoformat()}},
        ]
    }


def posting_claim_is_active(receipt: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        receipt, CLAIM_FIELD, CLAIM_AT_FIELD, ACCOUNTING_POST_CLAIM_TTL_SECONDS, now=now
    )


def reverse_claim_is_active(receipt: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        receipt, REVERSE_CLAIM_FIELD, REVERSE_CLAIM_AT_FIELD, ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS, now=now
    )


def cancel_claim_is_active(receipt: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    return _claim_lease_is_active(
        receipt, CANCEL_CLAIM_FIELD, CANCEL_CLAIM_AT_FIELD, RECEIPT_CANCEL_CLAIM_TTL_SECONDS, now=now
    )


def posting_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        CLAIM_FIELD, CLAIM_AT_FIELD, ACCOUNTING_POST_CLAIM_TTL_SECONDS, now=now
    )


def reverse_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        REVERSE_CLAIM_FIELD, REVERSE_CLAIM_AT_FIELD, ACCOUNTING_REVERSE_CLAIM_TTL_SECONDS, now=now
    )


def cancel_claim_absent_or_stale_clause(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    return _claim_absent_or_stale_clause(
        CANCEL_CLAIM_FIELD, CANCEL_CLAIM_AT_FIELD, RECEIPT_CANCEL_CLAIM_TTL_SECONDS, now=now
    )


def normalize_reversal_reason(raw: Any) -> str:
    reason = str(raw or "").strip()
    if len(reason) < REVERSAL_REASON_MIN_LEN:
        raise HTTPException(400, REASON_REQUIRED_MESSAGE)
    return reason


def recovery_required(doc: Optional[dict]) -> bool:
    return bool((doc or {}).get("accounting_recovery_required"))


def _strip_claim_fields(doc: dict) -> dict:
    for field in (
        CLAIM_FIELD,
        CLAIM_AT_FIELD,
        REVERSE_CLAIM_FIELD,
        REVERSE_CLAIM_AT_FIELD,
        CANCEL_CLAIM_FIELD,
        CANCEL_CLAIM_AT_FIELD,
    ):
        doc.pop(field, None)
    return doc


async def clear_posting_claim(db, receipt_id: str, *, token: Optional[str] = None) -> None:
    filt: Dict[str, Any] = {"id": str(receipt_id)}
    if token:
        filt[CLAIM_FIELD] = str(token)
    await db[COLLECTION].update_one(
        filt,
        {
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_posting_claim(db, receipt_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    return await db[COLLECTION].find_one_and_update(
        {
            "id": str(receipt_id),
            "cancelled": {"$ne": True},
            "$and": [
                reverse_claim_absent_or_stale_clause(),
                cancel_claim_absent_or_stale_clause(),
            ],
        },
        {
            "$set": {CLAIM_FIELD: claim_token, CLAIM_AT_FIELD: now, "updated_at": now},
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def clear_reverse_claim(db, receipt_id: str, *, token: Optional[str] = None) -> None:
    filt: Dict[str, Any] = {"id": str(receipt_id)}
    if token:
        filt[REVERSE_CLAIM_FIELD] = str(token)
    await db[COLLECTION].update_one(
        filt,
        {
            "$unset": {REVERSE_CLAIM_FIELD: "", REVERSE_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_reverse_claim(db, receipt_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    reverse_ok = reverse_claim_absent_or_stale_clause()
    reverse_ok.setdefault("$or", []).append({REVERSE_CLAIM_FIELD: claim_token})
    return await db[COLLECTION].find_one_and_update(
        {
            "id": str(receipt_id),
            "cancelled": {"$ne": True},
            "$and": [
                posting_claim_absent_or_stale_clause(),
                cancel_claim_absent_or_stale_clause(),
                reverse_ok,
            ],
        },
        {
            "$set": {
                REVERSE_CLAIM_FIELD: claim_token,
                REVERSE_CLAIM_AT_FIELD: now,
                "updated_at": now,
            },
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
        },
        return_document=ReturnDocument.AFTER,
    )


async def clear_receipt_cancel_claim(db, receipt_id: str, *, token: Optional[str] = None) -> None:
    filt: Dict[str, Any] = {"id": str(receipt_id)}
    if token:
        filt[CANCEL_CLAIM_FIELD] = str(token)
    await db[COLLECTION].update_one(
        filt,
        {
            "$unset": {CANCEL_CLAIM_FIELD: "", CANCEL_CLAIM_AT_FIELD: ""},
            "$set": {"updated_at": now_iso()},
        },
    )


async def acquire_receipt_cancel_claim(db, receipt_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    claim_token = str(token or uuid.uuid4())
    now = now_iso()
    cancel_ok = cancel_claim_absent_or_stale_clause()
    cancel_ok.setdefault("$or", []).append({CANCEL_CLAIM_FIELD: claim_token})
    return await db[COLLECTION].find_one_and_update(
        {
            "id": str(receipt_id),
            "cancelled": {"$ne": True},
            "$and": [
                posting_claim_absent_or_stale_clause(),
                reverse_claim_absent_or_stale_clause(),
                cancel_ok,
            ],
        },
        {
            "$set": {
                CANCEL_CLAIM_FIELD: claim_token,
                CANCEL_CLAIM_AT_FIELD: now,
                "updated_at": now,
            },
        },
        return_document=ReturnDocument.AFTER,
    )


async def _load_receipt(db, receipt_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(receipt_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"receipt_number": str(receipt_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Penerimaan Barang '{receipt_id}' tidak ditemukan.")
    return doc


async def _public_receipt_detail(db, receipt_id: str) -> Dict[str, Any]:
    from services.purchase_goods_receipts import get_purchase_goods_receipt

    return await get_purchase_goods_receipt(db, receipt_id)


def _fatal_block_message(reasons: List[str]) -> str:
    if REASON_CANCELLED in reasons:
        return "Penerimaan sudah dibatalkan."
    if REASON_INVALID_UNIT_CONVERSION in reasons:
        return INVALID_UOM_MESSAGE
    if REASON_PURCHASE_ACCOUNTING_NOT_CONFIGURED in reasons:
        return SETTINGS_NOT_READY_MESSAGE
    if REASON_LEGACY_BEFORE_EFFECTIVE_DATE in reasons:
        return LEGACY_DATE_MESSAGE
    if REASON_UNKNOWN_ITEM_TYPE in reasons:
        return UNKNOWN_ITEM_TYPE_MESSAGE
    if REASON_MISSING_MASTER_ITEM in reasons or REASON_MISSING_PO_LINE in reasons:
        return FATAL_LINE_DATA_MESSAGE
    return FATAL_LINE_DATA_MESSAGE


def _readiness_fatal_reasons(readiness: dict) -> List[str]:
    reasons: List[str] = []
    for reason in readiness.get("fatal_accounting_reasons") or []:
        if reason and reason not in reasons:
            reasons.append(str(reason))
    for row in readiness.get("fatal_ineligible_lines") or []:
        for reason in row.get("financial_ineligibility_reasons") or []:
            if reason and reason not in reasons:
                reasons.append(str(reason))
        primary = row.get("financial_ineligibility_reason") or row.get("reason")
        if primary and primary not in reasons:
            reasons.append(str(primary))
    return reasons


def _post_block_reason(receipt: dict, readiness: dict, status: str) -> str:
    if is_cancelled(receipt):
        return "Penerimaan sudah dibatalkan."
    if status != ACCOUNTING_NOT_POSTED:
        return ""
    if cancel_claim_is_active(receipt):
        return CANCEL_IN_PROGRESS_MESSAGE
    if reverse_claim_is_active(receipt):
        return REVERSE_CLAIM_MESSAGE
    if posting_claim_is_active(receipt):
        return CANCEL_CLAIM_MESSAGE
    if not readiness.get("settings_ready"):
        return SETTINGS_NOT_READY_MESSAGE
    if not readiness.get("in_scope_by_date"):
        return LEGACY_DATE_MESSAGE
    fatal_reasons = _readiness_fatal_reasons(readiness)
    if readiness.get("has_fatal_accounting_blocker") or fatal_reasons:
        return _fatal_block_message(fatal_reasons)
    if not (readiness.get("eligible_inventory_lines") or []):
        return NO_ELIGIBLE_LINES_MESSAGE
    return ""


async def attach_goods_receipt_accounting_fields(db, receipts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Derived journal status + posting readiness. Never writes receipts or journals."""
    if not receipts:
        return receipts
    settings = await get_purchase_accounting_settings(db)
    journals = await load_source_journals_by_receipt_ids(
        db, [row.get("id") for row in receipts if row.get("id")]
    )
    totals = await load_journal_debit_totals(
        db, [j.get("id") for j in journals.values() if j.get("id")]
    )
    readiness_map = await readiness_for_receipts(db, receipts)
    clearing_ready = bool(str(settings.get("purchase_clearing_account_id") or "").strip())
    inventory_ready = bool(settings.get("inventory_configured")) and clearing_ready

    for row in receipts:
        journal = journals.get(str(row.get("id")))
        status = accounting_status_from_journal(journal)
        cancelled = is_cancelled(row)
        readiness = readiness_map.get(str(row.get("id")) or "", {})
        basis = row.get(POSTING_BASIS_FIELD) if isinstance(row.get(POSTING_BASIS_FIELD), dict) else {}
        posted_or_reversed = status in (ACCOUNTING_POSTED, ACCOUNTING_REVERSED)
        eligible = list(readiness.get("eligible_inventory_lines") or [])
        has_fatal = bool(readiness.get("has_fatal_accounting_blocker"))
        amount = _journal_amount(journal, totals)
        if posted_or_reversed and basis:
            warnings = list(basis.get("accounting_warnings") or _warnings_from_posting_basis(basis))
            if amount is None:
                amount = basis.get("eligible_inventory_amount") or 0.0
            partial = bool(basis.get("partial_coverage") or basis.get("partial_inventory_coverage"))
        elif posted_or_reversed:
            warnings = list(basis.get("accounting_warnings") or [])
            if amount is None:
                amount = 0.0
            partial = False
        else:
            warnings = list(readiness.get("accounting_warnings") or [])
            if amount is None:
                amount = readiness.get("financial_inventory_amount") or 0.0
            partial = bool(readiness.get("partial_inventory_coverage"))
        recovering = recovery_required(row)
        post_reason = _post_block_reason(row, readiness, status)
        invoice_blocked = int(row.get("active_invoice_count") or 0) > 0
        stock = row.get("stock_dependency") or {}
        stock_blocked = bool(stock.get("blocked"))
        row["accounting_status"] = status
        row["accounting_journal_id"] = journal.get("id") if journal else None
        row["accounting_amount"] = amount
        row["accounting_ready"] = (
            (not cancelled)
            and inventory_ready
            and bool(readiness.get("in_scope_by_date"))
            and bool(eligible)
            and not has_fatal
        )
        row["accounting_warnings"] = warnings
        row["partial_inventory_coverage"] = partial
        row["has_fatal_accounting_blocker"] = False if posted_or_reversed else has_fatal
        row["fatal_accounting_reasons"] = (
            [] if posted_or_reversed else list(readiness.get("fatal_accounting_reasons") or [])
        )
        if basis:
            row[POSTING_BASIS_FIELD] = basis
        row["accounting_recovery_required"] = recovering
        row["accounting_recovery_journal_id"] = row.get("accounting_recovery_journal_id")
        row["accounting_recovery_reason"] = row.get("accounting_recovery_reason")
        row["accounting_post_block_reason"] = post_reason
        row["can_post_accounting"] = (
            not cancelled
            and status == ACCOUNTING_NOT_POSTED
            and inventory_ready
            and bool(readiness.get("in_scope_by_date"))
            and bool(eligible)
            and not has_fatal
            and not posting_claim_is_active(row)
            and not reverse_claim_is_active(row)
            and not cancel_claim_is_active(row)
            and not recovering
        )
        row["can_reverse_accounting"] = (
            recovering and status in (ACCOUNTING_POSTED, ACCOUNTING_REVERSED)
        ) or (
            (not cancelled)
            and status == ACCOUNTING_POSTED
            and not invoice_blocked
            and not stock_blocked
            and not posting_claim_is_active(row)
            and not cancel_claim_is_active(row)
        )
        if status == ACCOUNTING_POSTED or recovering:
            row["can_cancel"] = False
            row["cancel_block_reason"] = (
                RECOVERY_REQUIRED_MESSAGE if recovering else CANCEL_POSTED_MESSAGE
            )
        _strip_claim_fields(row)
    return receipts


async def get_goods_receipt_accounting_view(db, receipt_id: str) -> Dict[str, Any]:
    receipt = await _load_receipt(db, receipt_id)
    readiness = await get_goods_receipt_accounting_readiness(db, receipt["id"])
    await attach_goods_receipt_accounting_fields(db, [receipt])
    out = dict(readiness)
    out.update({
        "accounting_status": receipt.get("accounting_status"),
        "accounting_journal_id": receipt.get("accounting_journal_id"),
        "accounting_amount": receipt.get("accounting_amount"),
        "can_post_accounting": receipt.get("can_post_accounting"),
        "can_reverse_accounting": receipt.get("can_reverse_accounting"),
        "accounting_ready": receipt.get("accounting_ready"),
        "accounting_warnings": receipt.get("accounting_warnings") or readiness.get("accounting_warnings") or [],
        "accounting_recovery_required": receipt.get("accounting_recovery_required"),
        "accounting_post_block_reason": receipt.get("accounting_post_block_reason"),
        "partial_inventory_coverage": receipt.get("partial_inventory_coverage"),
        "has_fatal_accounting_blocker": receipt.get("has_fatal_accounting_blocker"),
        "fatal_accounting_reasons": receipt.get("fatal_accounting_reasons") or [],
        POSTING_BASIS_FIELD: receipt.get(POSTING_BASIS_FIELD),
    })
    basis = receipt.get(POSTING_BASIS_FIELD) if isinstance(receipt.get(POSTING_BASIS_FIELD), dict) else {}
    if receipt.get("accounting_status") in (ACCOUNTING_POSTED, ACCOUNTING_REVERSED) and basis:
        out["accounting_warnings"] = list(basis.get("accounting_warnings") or _warnings_from_posting_basis(basis))
        out["partial_inventory_coverage"] = bool(basis.get("partial_coverage") or basis.get("partial_inventory_coverage"))
        out["financial_inventory_amount"] = basis.get("eligible_inventory_amount")
        out["eligible_inventory_amount"] = basis.get("eligible_inventory_amount")
        out["eligible_inventory_lines"] = list(basis.get("eligible_inventory_lines") or [])
        out["excluded_service_lines"] = list(basis.get("excluded_service_lines") or [])
        out["has_fatal_accounting_blocker"] = False
        out["fatal_accounting_reasons"] = []
    return out


async def _require_posting_settings(db) -> dict:
    settings = await get_purchase_accounting_settings(db)
    inventory_id = str(settings.get("inventory_account_id") or "").strip()
    clearing_id = str(settings.get("purchase_clearing_account_id") or "").strip()
    if not settings.get("inventory_configured") or not inventory_id or not clearing_id:
        raise HTTPException(400, SETTINGS_NOT_READY_MESSAGE)
    inventory = _validate_inventory(await _load_account(db, inventory_id))
    clearing = _validate_purchase_clearing(await _load_account(db, clearing_id))
    return {
        "inventory_account_id": str(inventory["id"]),
        "purchase_clearing_account_id": str(clearing["id"]),
    }


def _require_eligible_amount(readiness: dict) -> Decimal:
    eligible = readiness.get("eligible_inventory_lines") or []
    if not eligible:
        raise HTTPException(409, NO_ELIGIBLE_LINES_MESSAGE)
    amount = _money(
        readiness.get("eligible_inventory_amount", readiness.get("financial_inventory_amount")),
        "financial_inventory_amount",
    )
    if amount <= 0:
        raise HTTPException(409, NO_ELIGIBLE_LINES_MESSAGE)
    return amount


def _require_postable_readiness(readiness: dict) -> Decimal:
    if readiness.get("cancelled"):
        raise HTTPException(409, CANCELLED_POST_MESSAGE)
    fatal_reasons = _readiness_fatal_reasons(readiness)
    if readiness.get("has_fatal_accounting_blocker") or fatal_reasons:
        message = _fatal_block_message(fatal_reasons)
        status = 400 if message == SETTINGS_NOT_READY_MESSAGE else 409
        raise HTTPException(status, message)
    return _require_eligible_amount(readiness)


def _eligible_line_snapshot(row: dict) -> Dict[str, Any]:
    return {
        "line_id": row.get("line_id"),
        "purchase_order_line_id": row.get("purchase_order_line_id"),
        "master_item_id": row.get("master_item_id"),
        "accurate_item_id": row.get("accurate_item_id"),
        "financial_inventory_item_key": row.get("financial_inventory_item_key"),
        "transaction_qty": row.get("transaction_qty"),
        "transaction_uom": row.get("transaction_uom"),
        "conversion_factor": row.get("conversion_factor"),
        "base_qty": row.get("base_qty"),
        "base_uom": row.get("base_uom"),
        "transaction_unit_cost": row.get("transaction_unit_cost"),
        "base_unit_cost": row.get("base_unit_cost"),
        "total_cost": row.get("total_cost", row.get("line_financial_amount")),
    }


def _excluded_service_snapshot(row: dict) -> Dict[str, Any]:
    return {
        "line_id": row.get("line_id"),
        "purchase_order_line_id": row.get("purchase_order_line_id"),
        "master_item_id": row.get("master_item_id"),
        "reason": row.get("financial_ineligibility_reason") or REASON_NON_INVENTORY_ITEM,
        "line_amount": row.get("line_financial_amount"),
    }


def _warnings_from_posting_basis(basis: Optional[dict]) -> List[dict]:
    row = basis or {}
    excluded = list(row.get("excluded_service_lines") or [])
    if not row.get("partial_coverage") and not row.get("partial_inventory_coverage") and not excluded:
        return []
    if row.get("accounting_warnings"):
        return list(row.get("accounting_warnings") or [])
    amount = row.get("eligible_inventory_amount") or 0.0
    return [{
        "code": WARNING_PARTIAL_INVENTORY_COVERAGE,
        "eligible_amount": amount,
        "excluded_line_count": len(excluded),
        "excluded_reasons": [REASON_NON_INVENTORY_ITEM],
        "message": (
            "Sebagian baris tidak dikapitalisasi ke Persediaan. "
            "Sisa nilai dapat tertinggal di Purchase Clearing / GRNI."
        ),
    }]


def build_accounting_posting_basis(
    *,
    receipt: dict,
    readiness: dict,
    inventory_account_id: str,
    purchase_clearing_account_id: str,
    effective_date: Any,
    user: Optional[dict],
    claim_token: str,
) -> Dict[str, Any]:
    eligible = [ _eligible_line_snapshot(row) for row in (readiness.get("eligible_inventory_lines") or []) ]
    excluded = [ _excluded_service_snapshot(row) for row in (readiness.get("excluded_service_lines") or []) ]
    amount = readiness.get("eligible_inventory_amount", readiness.get("financial_inventory_amount")) or 0.0
    partial = bool(readiness.get("partial_inventory_coverage"))
    return {
        "receipt_id": receipt.get("id"),
        "receipt_number": receipt.get("receipt_number") or "",
        "receipt_date": journal_date_from_receipt(receipt),
        "inventory_account_id": str(inventory_account_id),
        "purchase_clearing_account_id": str(purchase_clearing_account_id),
        "inventory_accounting_effective_date": effective_date,
        "eligible_inventory_amount": amount,
        "eligible_inventory_lines": eligible,
        "excluded_service_lines": excluded,
        "partial_coverage": partial,
        "partial_inventory_coverage": partial,
        "accounting_warnings": list(readiness.get("accounting_warnings") or []),
        "posting_claim_token": str(claim_token),
        "created_at": now_iso(),
        "created_by": {
            "id": str((user or {}).get("id") or (user or {}).get("_id") or ""),
            "name": str((user or {}).get("name") or (user or {}).get("email") or ""),
            "role": str((user or {}).get("role") or ""),
        },
    }


async def persist_accounting_posting_basis(
    db, receipt_id: str, token: str, basis: dict
) -> bool:
    result = await db[COLLECTION].update_one(
        {"id": str(receipt_id), CLAIM_FIELD: str(token)},
        {"$set": {POSTING_BASIS_FIELD: basis, "updated_at": now_iso()}},
    )
    return int(getattr(result, "matched_count", 0) or 0) > 0


def _build_journal_lines(
    *,
    inventory_account_id: str,
    clearing_account_id: str,
    amount: Decimal,
    receipt_number: str,
) -> List[dict]:
    ref = receipt_number or "penerimaan"
    return [
        {
            "account_id": inventory_account_id,
            "debit": amount,
            "credit": Decimal("0.00"),
            "description": f"Persediaan {ref}",
        },
        {
            "account_id": clearing_account_id,
            "debit": Decimal("0.00"),
            "credit": amount,
            "description": f"Purchase Clearing {ref}",
        },
    ]


async def _heal_posted_metadata(db, receipt: dict, journal: dict, user: Optional[dict] = None) -> None:
    now = now_iso()
    patch: Dict[str, Any] = {
        "updated_at": now,
        "accounting_journal_id": journal.get("id"),
    }
    stored = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0}) or {}
    if not stored.get("accounting_posted_at"):
        patch["accounting_posted_at"] = now
    if user and not stored.get("accounting_posted_by"):
        patch["accounting_posted_by"] = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": str(user.get("name") or user.get("email") or ""),
            "role": str(user.get("role") or ""),
        }
    await db[COLLECTION].update_one(
        {"id": receipt["id"]},
        {
            "$set": patch,
            "$unset": {CLAIM_FIELD: "", CLAIM_AT_FIELD: ""},
        },
    )


async def _write_source_journal_from_basis(db, receipt: dict, user: dict, basis: dict) -> None:
    amount = _money(basis.get("eligible_inventory_amount"), "eligible_inventory_amount")
    if amount <= 0:
        raise HTTPException(409, NO_ELIGIBLE_LINES_MESSAGE)
    inventory_account_id = str(basis.get("inventory_account_id") or "").strip()
    clearing_account_id = str(basis.get("purchase_clearing_account_id") or "").strip()
    if not inventory_account_id or not clearing_account_id:
        raise HTTPException(400, SETTINGS_NOT_READY_MESSAGE)
    lines = _build_journal_lines(
        inventory_account_id=inventory_account_id,
        clearing_account_id=clearing_account_id,
        amount=amount,
        receipt_number=str(basis.get("receipt_number") or receipt.get("receipt_number") or ""),
    )
    journal_date = str(basis.get("receipt_date") or journal_date_from_receipt(receipt))
    try:
        await post_balanced_journal(
            db,
            date=journal_date,
            source_type=SOURCE_TYPE,
            source_id=str(receipt["id"]),
            description=f"Penerimaan Barang {receipt.get('receipt_number') or receipt['id']}",
            lines=lines,
            posted_by=user,
        )
    except PeriodLockedError as exc:
        raise HTTPException(409, str(exc)) from exc
    except JournalValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JournalConflictError:
        existing = await find_goods_receipt_source_journal(db, receipt["id"])
        if existing:
            return
        raise HTTPException(409, "Jurnal untuk penerimaan ini sudah ada.")


async def restore_active_after_cancel_race(db, receipt_id: str) -> None:
    journal = await find_goods_receipt_source_journal(db, receipt_id)
    if accounting_status_from_journal(journal) == ACCOUNTING_REVERSED:
        return
    stored = await db[COLLECTION].find_one({"id": receipt_id}, {"_id": 0}) or {}
    if stored.get("accounting_reversed_at") or stored.get("accounting_reversal_journal_id"):
        return
    if recovery_required(stored):
        return
    await db[COLLECTION].update_one(
        {
            "id": receipt_id,
            "cancelled": True,
            "$and": [
                _claim_absent_clause("accounting_reversed_at"),
                _claim_absent_clause("accounting_reversal_journal_id"),
                _claim_absent_clause("accounting_recovery_required"),
            ],
        },
        {
            "$set": {"updated_at": now_iso(), "status": "ACTIVE"},
            "$unset": {
                "cancelled": "",
                "cancelled_at": "",
                "cancelled_by": "",
                "cancel_reason": "",
            },
        },
    )


async def _persist_recovery_marker(db, receipt_id: str, reversal_journal: Optional[dict]) -> None:
    now = now_iso()
    await db[COLLECTION].update_one(
        {"id": str(receipt_id)},
        {
            "$set": {
                "accounting_recovery_required": True,
                "accounting_recovery_journal_id": (reversal_journal or {}).get("id"),
                "accounting_recovery_reason": RECOVERY_REASON,
                "accounting_recovery_at": now,
                "updated_at": now,
            }
        },
    )


async def _clear_recovery_and_finalize(
    db,
    receipt: dict,
    user: dict,
    reason: str,
    reversal_journal: Optional[dict] = None,
) -> None:
    now = now_iso()
    stored = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0}) or {}
    patch: Dict[str, Any] = {"updated_at": now}
    if not is_cancelled(stored):
        patch.update(cancellation_fields(user, reason))
        patch["status"] = "CANCELLED"
    if not stored.get("accounting_reversed_at"):
        patch["accounting_reversed_at"] = now
        patch["accounting_reversed_by"] = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": str(user.get("name") or user.get("email") or ""),
            "role": str(user.get("role") or ""),
        }
    if reason and not str(stored.get("accounting_reversal_reason") or "").strip():
        patch["accounting_reversal_reason"] = reason
    rev_id = None
    if reversal_journal and reversal_journal.get("id"):
        rev_id = reversal_journal.get("id")
    elif stored.get("accounting_reversal_journal_id"):
        rev_id = stored.get("accounting_reversal_journal_id")
    elif stored.get("accounting_recovery_journal_id"):
        rev_id = stored.get("accounting_recovery_journal_id")
    if rev_id:
        patch["accounting_reversal_journal_id"] = rev_id
    await db[COLLECTION].update_one(
        {"id": receipt["id"]},
        {
            "$set": patch,
            "$unset": {
                "accounting_recovery_required": "",
                "accounting_recovery_journal_id": "",
                "accounting_recovery_reason": "",
                "accounting_recovery_at": "",
                REVERSE_CLAIM_FIELD: "",
                REVERSE_CLAIM_AT_FIELD: "",
                CLAIM_FIELD: "",
                CLAIM_AT_FIELD: "",
                CANCEL_CLAIM_FIELD: "",
                CANCEL_CLAIM_AT_FIELD: "",
            },
        },
    )


def _raise_recovery_required(message: Optional[str] = None) -> None:
    raise HTTPException(409, {
        "code": RECOVERY_REQUIRED_CODE,
        "message": message or RECOVERY_REQUIRED_MESSAGE,
    })


async def _raise_active_invoice_blockers(db, receipt_id: str) -> None:
    from services.purchase_goods_receipts import _active_invoice_count_for_receipt

    if await _active_invoice_count_for_receipt(db, receipt_id) > 0:
        raise HTTPException(409, ACTIVE_INVOICE_REVERSE_MESSAGE)


async def _stock_plan_or_raise_for_new_reversal(db, receipt_id: str) -> dict:
    from services.purchase_goods_receipts import _stock_reversal_plan

    plan = await _stock_reversal_plan(db, receipt_id)
    if plan.get("superseded_originals"):
        raise HTTPException(409, {
            "code": STOCK_OPNAME_CODE,
            "message": STOCK_OPNAME_MESSAGE,
        })
    if plan.get("blocked"):
        raise HTTPException(
            409,
            plan.get("block_reason") or "Reversal stock Penerimaan Barang tidak aman.",
        )
    return plan


async def _resume_operational_cancel(
    db,
    receipt: dict,
    user: dict,
    reason: str,
    reversal_journal: Optional[dict],
) -> Dict[str, Any]:
    from services.purchase_goods_receipts import cancel_purchase_goods_receipt

    try:
        await cancel_purchase_goods_receipt(
            db,
            receipt["id"],
            user,
            reason=reason,
            accounting_reversal_owned=True,
        )
    except HTTPException:
        await _persist_recovery_marker(db, receipt["id"], reversal_journal)
        raise
    except Exception:
        await _persist_recovery_marker(db, receipt["id"], reversal_journal)
        _raise_recovery_required()

    stored = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0}) or {}
    if not is_cancelled(stored):
        await _persist_recovery_marker(db, receipt["id"], reversal_journal)
        _raise_recovery_required()
    await _clear_recovery_and_finalize(db, receipt, user, reason, reversal_journal)
    return await _public_receipt_detail(db, receipt["id"])


async def post_purchase_goods_receipt_accounting(
    db, receipt_id: str, user: dict, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Post one active canonical GR. Idempotent. Mongo claim is the lock."""
    async with _optional_receipt_lock(serialize_in_process):
        receipt = await _load_receipt(db, receipt_id)
        existing = await find_goods_receipt_source_journal(db, receipt["id"])
        if existing:
            await _heal_posted_metadata(db, receipt, existing, user)
            if is_cancelled(receipt) and accounting_status_from_journal(existing) == ACCOUNTING_POSTED:
                await restore_active_after_cancel_race(db, receipt["id"])
            return await _public_receipt_detail(db, receipt["id"])

        if is_cancelled(receipt):
            raise HTTPException(409, CANCELLED_POST_MESSAGE)
        if recovery_required(receipt):
            _raise_recovery_required()
        if reverse_claim_is_active(receipt):
            raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
        if cancel_claim_is_active(receipt):
            raise HTTPException(409, CANCEL_IN_PROGRESS_MESSAGE)

        claimed = await acquire_posting_claim(db, receipt["id"])
        if claimed is None:
            current = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0})
            if current and is_cancelled(current):
                raise HTTPException(409, CANCELLED_POST_MESSAGE)
            if current and reverse_claim_is_active(current):
                raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
            if current and cancel_claim_is_active(current):
                raise HTTPException(409, CANCEL_IN_PROGRESS_MESSAGE)
            existing = await find_goods_receipt_source_journal(db, receipt["id"])
            if existing:
                await _heal_posted_metadata(db, receipt, existing, user)
                return await _public_receipt_detail(db, receipt["id"])
            raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

        post_token = str(claimed.get(CLAIM_FIELD) or "")
        existing = await find_goods_receipt_source_journal(db, receipt["id"])
        if existing:
            await _heal_posted_metadata(db, receipt, existing, user)
            return await _public_receipt_detail(db, receipt["id"])

        try:
            fresh = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0}) or claimed
            if (
                is_cancelled(fresh)
                or reverse_claim_is_active(fresh)
                or cancel_claim_is_active(fresh)
            ):
                await clear_posting_claim(db, receipt["id"], token=post_token)
                if reverse_claim_is_active(fresh):
                    raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
                if cancel_claim_is_active(fresh):
                    raise HTTPException(409, CANCEL_IN_PROGRESS_MESSAGE)
                raise HTTPException(409, CANCELLED_POST_MESSAGE)
            if str(fresh.get(CLAIM_FIELD) or "") != post_token:
                raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

            readiness = await get_goods_receipt_accounting_readiness(db, receipt["id"])
            _require_postable_readiness(readiness)
            settings = await get_purchase_accounting_settings(db)
            mapped = await _require_posting_settings(db)
            basis = build_accounting_posting_basis(
                receipt=fresh,
                readiness=readiness,
                inventory_account_id=mapped["inventory_account_id"],
                purchase_clearing_account_id=mapped["purchase_clearing_account_id"],
                effective_date=settings.get("inventory_accounting_effective_date"),
                user=user,
                claim_token=post_token,
            )
            persisted = await persist_accounting_posting_basis(
                db, receipt["id"], post_token, basis
            )
            if not persisted:
                raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")

            authoritative = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0}) or {}
            if (
                is_cancelled(authoritative)
                or reverse_claim_is_active(authoritative)
                or cancel_claim_is_active(authoritative)
            ):
                await clear_posting_claim(db, receipt["id"], token=post_token)
                if reverse_claim_is_active(authoritative):
                    raise HTTPException(409, REVERSE_CLAIM_MESSAGE)
                if cancel_claim_is_active(authoritative):
                    raise HTTPException(409, CANCEL_IN_PROGRESS_MESSAGE)
                raise HTTPException(409, CANCELLED_POST_MESSAGE)
            if str(authoritative.get(CLAIM_FIELD) or "") != post_token:
                raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")
            stored_basis = authoritative.get(POSTING_BASIS_FIELD) or basis
            if str((stored_basis or {}).get("posting_claim_token") or "") not in ("", post_token):
                raise HTTPException(409, "Posting accounting gagal karena perubahan bersamaan.")
            await _write_source_journal_from_basis(db, authoritative, user, stored_basis)
        except Exception:
            still = await find_goods_receipt_source_journal(db, receipt["id"])
            if not still:
                await clear_posting_claim(db, receipt["id"], token=post_token)
            else:
                await _heal_posted_metadata(db, receipt, still, user)
            raise

        written = await find_goods_receipt_source_journal(db, receipt["id"])
        if written:
            try:
                await _heal_posted_metadata(db, receipt, written, user)
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(500, "Posting accounting belum selesai disimpan.") from exc
        current = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0})
        if current and is_cancelled(current):
            await restore_active_after_cancel_race(db, receipt["id"])
        await clear_posting_claim(db, receipt["id"], token=post_token)
        return await _public_receipt_detail(db, receipt["id"])


async def reverse_purchase_goods_receipt_accounting(
    db, receipt_id: str, user: dict, reason: Any, *, serialize_in_process: bool = True
) -> Dict[str, Any]:
    """Reverse posted GR inventory journal, then resume operational cancel."""
    reason = normalize_reversal_reason(reason)

    async with _optional_receipt_lock(serialize_in_process):
        receipt = await _load_receipt(db, receipt_id)
        existing = await find_goods_receipt_source_journal(db, receipt["id"])
        status = accounting_status_from_journal(existing)
        rev = await find_goods_receipt_reversal_journal(db, existing.get("id") if existing else "")

        if status == ACCOUNTING_REVERSED or (recovery_required(receipt) and rev):
            if is_cancelled(receipt) and not recovery_required(receipt):
                await _clear_recovery_and_finalize(db, receipt, user, reason, rev)
                return await _public_receipt_detail(db, receipt["id"])
            return await _resume_operational_cancel(db, receipt, user, reason, rev)

        if is_cancelled(receipt) and not recovery_required(receipt):
            if reverse_claim_is_active(receipt):
                await clear_reverse_claim(db, receipt["id"])
            raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)
        if status != ACCOUNTING_POSTED or not existing:
            raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)
        if posting_claim_is_active(receipt):
            raise HTTPException(409, POSTING_CLAIM_REVERSE_MESSAGE)
        if cancel_claim_is_active(receipt):
            raise HTTPException(409, CANCEL_IN_PROGRESS_MESSAGE)

        await _raise_active_invoice_blockers(db, receipt["id"])
        await _stock_plan_or_raise_for_new_reversal(db, receipt["id"])

        claimed = await acquire_reverse_claim(db, receipt["id"])
        if claimed is None:
            current = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0})
            still = await find_goods_receipt_source_journal(db, receipt["id"])
            if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                rev = await find_goods_receipt_reversal_journal(db, still.get("id") if still else "")
                return await _resume_operational_cancel(db, receipt, user, reason, rev)
            if current and posting_claim_is_active(current):
                raise HTTPException(409, POSTING_CLAIM_REVERSE_MESSAGE)
            if current and cancel_claim_is_active(current):
                raise HTTPException(409, CANCEL_IN_PROGRESS_MESSAGE)
            raise HTTPException(409, "Reversal accounting gagal karena perubahan bersamaan.")

        reverse_token = str(claimed.get(REVERSE_CLAIM_FIELD) or "")
        try:
            await _raise_active_invoice_blockers(db, receipt["id"])
            await _stock_plan_or_raise_for_new_reversal(db, receipt["id"])

            current = await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0}) or claimed
            if posting_claim_is_active(current) or cancel_claim_is_active(current):
                await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                if posting_claim_is_active(current):
                    raise HTTPException(409, POSTING_CLAIM_REVERSE_MESSAGE)
                raise HTTPException(409, CANCEL_IN_PROGRESS_MESSAGE)

            existing = await find_goods_receipt_source_journal(db, receipt["id"])
            status = accounting_status_from_journal(existing)
            if status == ACCOUNTING_REVERSED:
                rev = await find_goods_receipt_reversal_journal(db, existing.get("id") if existing else "")
                return await _resume_operational_cancel(db, receipt, user, reason, rev)
            if status != ACCOUNTING_POSTED or not existing:
                await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                raise HTTPException(409, UNPOSTED_REVERSE_MESSAGE)

            from services.inventory_financial_locks import (
                LOCK_CONFLICT_CODE,
                LOCK_CONFLICT_MESSAGE,
                acquire_item_locks,
                release_item_locks,
                renew_item_locks,
            )
            from services.inventory_issue_accounting import (
                InventoryIssueError,
                assert_gr_unconsumed_for_reversal,
                gr_item_keys_from_basis,
            )

            item_locks = []
            try:
                item_keys = gr_item_keys_from_basis(
                    (current or {}).get(POSTING_BASIS_FIELD)
                    or (receipt or {}).get(POSTING_BASIS_FIELD)
                )
                item_locks = await acquire_item_locks(
                    db, item_keys, f"gr_reverse:{receipt['id']}"
                )
                if item_locks is None:
                    await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                    raise HTTPException(409, {
                        "code": LOCK_CONFLICT_CODE,
                        "message": LOCK_CONFLICT_MESSAGE,
                    })
                try:
                    await assert_gr_unconsumed_for_reversal(db, current or receipt)
                except InventoryIssueError as exc:
                    await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                    raise HTTPException(exc.status_code, exc.as_detail()) from exc

                still_held = await renew_item_locks(db, item_locks)
                if still_held is None:
                    await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                    raise HTTPException(409, {
                        "code": LOCK_CONFLICT_CODE,
                        "message": LOCK_CONFLICT_MESSAGE,
                    })
                item_locks = still_held

                try:
                    rev_doc = await reverse_journal(
                        db,
                        existing["id"],
                        source_type=SOURCE_REVERSAL,
                        source_id=str(receipt["id"]),
                        posted_by=user,
                        description=(
                            f"Reversal Penerimaan Barang {receipt.get('receipt_number') or receipt['id']} — {reason}"
                        ),
                    )
                except KeyError as exc:
                    await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                    raise HTTPException(404, "Jurnal penerimaan tidak ditemukan.") from exc
                except PeriodLockedError as exc:
                    await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                    raise HTTPException(409, str(exc)) from exc
                except JournalValidationError as exc:
                    await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                    raise HTTPException(400, str(exc)) from exc
                except JournalConflictError:
                    still = await find_goods_receipt_source_journal(db, receipt["id"])
                    if accounting_status_from_journal(still) != ACCOUNTING_REVERSED:
                        await clear_reverse_claim(db, receipt["id"], token=reverse_token)
                        raise HTTPException(409, "Reversal jurnal untuk penerimaan ini sudah ada.")
                    rev_doc = await find_goods_receipt_reversal_journal(db, still.get("id") if still else "")

                try:
                    await _persist_recovery_marker(db, receipt["id"], rev_doc)
                except HTTPException:
                    raise
                except Exception as exc:
                    raise HTTPException(409, {
                        "code": RECOVERY_REQUIRED_CODE,
                        "message": RECOVERY_REQUIRED_MESSAGE,
                    }) from exc
                return await _resume_operational_cancel(db, receipt, user, reason, rev_doc)
            finally:
                await release_item_locks(db, item_locks)
        except HTTPException as exc:
            still = await find_goods_receipt_source_journal(db, receipt["id"])
            if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                detail = exc.detail
                already_recovery = isinstance(detail, dict) and detail.get("code") == RECOVERY_REQUIRED_CODE
                if not already_recovery:
                    rev = await find_goods_receipt_reversal_journal(db, still.get("id") if still else "")
                    await _persist_recovery_marker(db, receipt["id"], rev)
                if already_recovery:
                    raise
                if exc.status_code >= 500:
                    _raise_recovery_required()
                if recovery_required(await db[COLLECTION].find_one({"id": receipt["id"]}, {"_id": 0}) or {}):
                    if exc.status_code in (409, 500):
                        raise
            raise
        except Exception:
            still = await find_goods_receipt_source_journal(db, receipt["id"])
            if accounting_status_from_journal(still) == ACCOUNTING_REVERSED:
                rev = await find_goods_receipt_reversal_journal(db, still.get("id") if still else "")
                try:
                    await _persist_recovery_marker(db, receipt["id"], rev)
                except Exception:
                    pass
                _raise_recovery_required()
            await clear_reverse_claim(db, receipt["id"], token=reverse_token)
            raise


@asynccontextmanager
async def _optional_receipt_lock(enabled: bool):
    from services.purchase_common import get_named_lock

    if not enabled:
        yield
        return
    async with get_named_lock("purchase_goods_receipt_accounting"):
        yield
