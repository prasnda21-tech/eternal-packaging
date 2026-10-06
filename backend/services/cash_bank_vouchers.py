"""Kas & Bank vouchers: Penerimaan, Pembayaran, Transfer + post/reverse."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from services.cash_bank_master import (
    CashBankValidationError,
    get_cash_bank,
    get_cash_bank_raw,
    require_active_cash_bank,
)
from services.chart_of_accounts import get_account
from services.accounting_periods import PeriodLockedError
from services.journal_core import (
    SOURCE_PAYMENT,
    SOURCE_RECEIPT,
    SOURCE_REVERSAL,
    SOURCE_TRANSFER,
    JournalConflictError,
    JournalValidationError,
    find_primary_journal_for_source,
    get_journal,
    post_balanced_journal,
    reverse_journal,
)
from services.purchase_common import (
    actor_snapshot,
    get_named_lock,
    insert_with_unique_number,
    next_dated_seq,
    now_iso,
    parse_positive_money,
    today_ymd,
)

RECEIPTS = "cash_bank_receipts"
PAYMENTS = "cash_bank_payments"
TRANSFERS = "cash_bank_transfers"

PREFIX_RECEIPT = "KM"
PREFIX_PAYMENT = "KK"
PREFIX_TRANSFER = "TR"

STATUS_DRAFT = "draft"
STATUS_POSTED = "posted"
STATUS_REVERSED = "reversed"

KIND_RECEIPT = "receipt"
KIND_PAYMENT = "payment"
KIND_TRANSFER = "transfer"


class VoucherValidationError(ValueError):
    pass


class VoucherConflictError(Exception):
    pass


PAYROLL_PAYABLE_BLOCK_CODE = "PAYROLL_PAYABLE_MANUAL_PAYMENT_BLOCKED"
PAYROLL_PAYABLE_BLOCK_MESSAGE = (
    "Utang Gaji harus dibayar melalui HR & Payroll → Payment Payroll, "
    "bukan melalui Pembayaran Kas & Bank manual."
)


async def _reject_payroll_payable_counter(db, counter_account_id: str) -> None:
    """Block only the active payroll payable account. Other COA stay open."""
    from services.payroll_accounting_settings import active_manual_payment_accounts

    guard = await active_manual_payment_accounts(db)
    payable_id = str(guard.get("payroll_payable_account_id") or "")
    if payable_id and str(counter_account_id or "") == payable_id:
        raise VoucherValidationError(f"{PAYROLL_PAYABLE_BLOCK_CODE}: {PAYROLL_PAYABLE_BLOCK_MESSAGE}")


async def ensure_indexes(db) -> None:
    for coll_name, number_field in (
        (RECEIPTS, "number"),
        (PAYMENTS, "number"),
        (TRANSFERS, "number"),
    ):
        col = db[coll_name]
        await col.create_index("id", unique=True)
        await col.create_index(number_field, unique=True)
        await col.create_index("status")
        await col.create_index("date")
        await col.create_index("created_at")
        await col.create_index(
            "journal_id",
            unique=True,
            partialFilterExpression={"journal_id": {"$type": "string"}},
        )


def _norm_date(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        raise VoucherValidationError("Tanggal wajib diisi")
    # Accept YYYY-MM-DD
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    raise VoucherValidationError("Format tanggal harus YYYY-MM-DD")


def _public_attachment(meta: Optional[dict]) -> Optional[dict]:
    if not isinstance(meta, dict) or not meta.get("id"):
        return None
    return {
        "id": meta.get("id"),
        "name": meta.get("name"),
        "content_type": meta.get("content_type"),
        "uploaded_at": meta.get("uploaded_at"),
        "uploaded_by": meta.get("uploaded_by"),
    }


def public_voucher(doc: Optional[dict], *, kind: str) -> Optional[dict]:
    if not doc:
        return None
    out = {
        "id": doc.get("id"),
        "number": doc.get("number"),
        "date": doc.get("date"),
        "kind": kind,
        "amount": doc.get("amount"),
        "description": doc.get("description") or "",
        "reference": doc.get("reference") or "",
        "status": doc.get("status"),
        "journal_id": doc.get("journal_id"),
        "reversal_journal_id": doc.get("reversal_journal_id"),
        "posted_at": doc.get("posted_at"),
        "posted_by": doc.get("posted_by"),
        "reversed_at": doc.get("reversed_at"),
        "reversed_by": doc.get("reversed_by"),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "attachment": _public_attachment(doc.get("attachment")),
    }
    if kind == KIND_RECEIPT:
        out["cash_bank_id"] = doc.get("cash_bank_id")
        out["cash_bank_name"] = doc.get("cash_bank_name")
        out["counter_account_id"] = doc.get("counter_account_id")
        out["counter_account_code"] = doc.get("counter_account_code")
        out["counter_account_name"] = doc.get("counter_account_name")
    elif kind == KIND_PAYMENT:
        out["cash_bank_id"] = doc.get("cash_bank_id")
        out["cash_bank_name"] = doc.get("cash_bank_name")
        out["counter_account_id"] = doc.get("counter_account_id")
        out["counter_account_code"] = doc.get("counter_account_code")
        out["counter_account_name"] = doc.get("counter_account_name")
    elif kind == KIND_TRANSFER:
        out["source_cash_bank_id"] = doc.get("source_cash_bank_id")
        out["source_cash_bank_name"] = doc.get("source_cash_bank_name")
        out["dest_cash_bank_id"] = doc.get("dest_cash_bank_id")
        out["dest_cash_bank_name"] = doc.get("dest_cash_bank_name")
    return out


def _collection_for(kind: str) -> str:
    if kind == KIND_RECEIPT:
        return RECEIPTS
    if kind == KIND_PAYMENT:
        return PAYMENTS
    if kind == KIND_TRANSFER:
        return TRANSFERS
    raise VoucherValidationError("Jenis voucher tidak dikenal")


def _prefix_for(kind: str) -> str:
    if kind == KIND_RECEIPT:
        return PREFIX_RECEIPT
    if kind == KIND_PAYMENT:
        return PREFIX_PAYMENT
    if kind == KIND_TRANSFER:
        return PREFIX_TRANSFER
    raise VoucherValidationError("Jenis voucher tidak dikenal")


def _source_type_for(kind: str) -> str:
    if kind == KIND_RECEIPT:
        return SOURCE_RECEIPT
    if kind == KIND_PAYMENT:
        return SOURCE_PAYMENT
    if kind == KIND_TRANSFER:
        return SOURCE_TRANSFER
    raise VoucherValidationError("Jenis voucher tidak dikenal")


async def list_vouchers(db, kind: str, *, q: Optional[str] = None) -> List[dict]:
    coll = _collection_for(kind)
    query: Dict[str, Any] = {}
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"number": {"$regex": needle, "$options": "i"}},
            {"description": {"$regex": needle, "$options": "i"}},
            {"reference": {"$regex": needle, "$options": "i"}},
            {"cash_bank_name": {"$regex": needle, "$options": "i"}},
            {"source_cash_bank_name": {"$regex": needle, "$options": "i"}},
            {"dest_cash_bank_name": {"$regex": needle, "$options": "i"}},
            {"counter_account_name": {"$regex": needle, "$options": "i"}},
            {"counter_account_code": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[coll].find(query, {"_id": 0}).sort([("date", -1), ("created_at", -1)]).to_list(2000)
    return [public_voucher(d, kind=kind) for d in docs]


async def get_voucher(db, kind: str, voucher_id: str) -> Optional[dict]:
    doc = await db[_collection_for(kind)].find_one({"id": str(voucher_id)}, {"_id": 0})
    return public_voucher(doc, kind=kind)


async def get_voucher_raw(db, kind: str, voucher_id: str) -> Optional[dict]:
    return await db[_collection_for(kind)].find_one({"id": str(voucher_id)}, {"_id": 0})


async def _resolve_counter(db, counter_account_id: str) -> dict:
    aid = str(counter_account_id or "").strip()
    if not aid:
        raise VoucherValidationError("Akun lawan wajib diisi")
    coa = await get_account(db, aid)
    if not coa:
        raise VoucherValidationError("Akun lawan tidak ditemukan")
    return coa


async def create_receipt(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    cash_bank = await require_active_cash_bank(db, (raw or {}).get("cash_bank_id"))
    counter = await _resolve_counter(db, (raw or {}).get("counter_account_id"))
    if counter["id"] == cash_bank.get("account_id"):
        raise VoucherValidationError("Akun lawan tidak boleh sama dengan akun Kas/Bank")
    amount = parse_positive_money((raw or {}).get("amount"), "Nominal")
    date = _norm_date((raw or {}).get("date"))
    description = str((raw or {}).get("description") or "").strip()
    reference = str((raw or {}).get("reference") or "").strip()
    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": str(uuid.uuid4()),
        "number": "",
        "date": date,
        "cash_bank_id": cash_bank["id"],
        "cash_bank_name": cash_bank.get("name"),
        "cash_bank_account_id": cash_bank.get("account_id"),
        "counter_account_id": counter["id"],
        "counter_account_code": counter.get("code"),
        "counter_account_name": counter.get("name"),
        "amount": amount,
        "description": description,
        "reference": reference,
        "status": STATUS_DRAFT,
        "journal_id": None,
        "reversal_journal_id": None,
        "attachment": None,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    async with get_named_lock("cash_bank_receipt_create"):
        async def _alloc():
            return await next_dated_seq(db, RECEIPTS, "number", PREFIX_RECEIPT)
        saved = await insert_with_unique_number(db, RECEIPTS, "number", doc, _alloc)
    return public_voucher(saved, kind=KIND_RECEIPT)


async def create_payment(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    cash_bank = await require_active_cash_bank(db, (raw or {}).get("cash_bank_id"))
    counter = await _resolve_counter(db, (raw or {}).get("counter_account_id"))
    if counter["id"] == cash_bank.get("account_id"):
        raise VoucherValidationError("Akun lawan tidak boleh sama dengan akun Kas/Bank")
    await _reject_payroll_payable_counter(db, counter["id"])
    amount = parse_positive_money((raw or {}).get("amount"), "Nominal")
    date = _norm_date((raw or {}).get("date"))
    description = str((raw or {}).get("description") or "").strip()
    reference = str((raw or {}).get("reference") or "").strip()
    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": str(uuid.uuid4()),
        "number": "",
        "date": date,
        "cash_bank_id": cash_bank["id"],
        "cash_bank_name": cash_bank.get("name"),
        "cash_bank_account_id": cash_bank.get("account_id"),
        "counter_account_id": counter["id"],
        "counter_account_code": counter.get("code"),
        "counter_account_name": counter.get("name"),
        "amount": amount,
        "description": description,
        "reference": reference,
        "status": STATUS_DRAFT,
        "journal_id": None,
        "reversal_journal_id": None,
        "attachment": None,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    async with get_named_lock("cash_bank_payment_create"):
        async def _alloc():
            return await next_dated_seq(db, PAYMENTS, "number", PREFIX_PAYMENT)
        saved = await insert_with_unique_number(db, PAYMENTS, "number", doc, _alloc)
    return public_voucher(saved, kind=KIND_PAYMENT)


async def create_transfer(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    source = await require_active_cash_bank(db, (raw or {}).get("source_cash_bank_id"))
    dest = await require_active_cash_bank(db, (raw or {}).get("dest_cash_bank_id"))
    if source["id"] == dest["id"]:
        raise VoucherValidationError("Kas/Bank sumber dan tujuan tidak boleh sama")
    if source.get("account_id") == dest.get("account_id"):
        raise VoucherValidationError("Akun sumber dan tujuan tidak boleh sama")
    amount = parse_positive_money((raw or {}).get("amount"), "Nominal")
    date = _norm_date((raw or {}).get("date"))
    description = str((raw or {}).get("description") or "").strip()
    reference = str((raw or {}).get("reference") or "").strip()
    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": str(uuid.uuid4()),
        "number": "",
        "date": date,
        "source_cash_bank_id": source["id"],
        "source_cash_bank_name": source.get("name"),
        "source_account_id": source.get("account_id"),
        "dest_cash_bank_id": dest["id"],
        "dest_cash_bank_name": dest.get("name"),
        "dest_account_id": dest.get("account_id"),
        "amount": amount,
        "description": description,
        "reference": reference,
        "status": STATUS_DRAFT,
        "journal_id": None,
        "reversal_journal_id": None,
        "attachment": None,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
    }
    async with get_named_lock("cash_bank_transfer_create"):
        async def _alloc():
            return await next_dated_seq(db, TRANSFERS, "number", PREFIX_TRANSFER)
        saved = await insert_with_unique_number(db, TRANSFERS, "number", doc, _alloc)
    return public_voucher(saved, kind=KIND_TRANSFER)


async def update_draft(db, kind: str, voucher_id: str, raw: dict, *, user: Optional[dict] = None) -> dict:
    coll = _collection_for(kind)
    doc = await get_voucher_raw(db, kind, voucher_id)
    if not doc:
        raise KeyError("missing")
    if doc.get("status") != STATUS_DRAFT:
        raise VoucherConflictError("Hanya draft yang dapat diubah")

    amount = parse_positive_money(
        (raw or {}).get("amount") if "amount" in (raw or {}) else doc.get("amount"),
        "Nominal",
    )
    date = _norm_date((raw or {}).get("date") if "date" in (raw or {}) else doc.get("date"))
    description = str(
        (raw or {}).get("description") if "description" in (raw or {}) else doc.get("description") or ""
    ).strip()
    reference = str(
        (raw or {}).get("reference") if "reference" in (raw or {}) else doc.get("reference") or ""
    ).strip()

    updates: Dict[str, Any] = {
        "amount": amount,
        "date": date,
        "description": description,
        "reference": reference,
        "updated_at": now_iso(),
        "updated_by": actor_snapshot(user),
    }

    if kind in (KIND_RECEIPT, KIND_PAYMENT):
        cb_id = (raw or {}).get("cash_bank_id") if "cash_bank_id" in (raw or {}) else doc.get("cash_bank_id")
        cash_bank = await require_active_cash_bank(db, cb_id)
        counter_id = (
            (raw or {}).get("counter_account_id")
            if "counter_account_id" in (raw or {})
            else doc.get("counter_account_id")
        )
        counter = await _resolve_counter(db, counter_id)
        if counter["id"] == cash_bank.get("account_id"):
            raise VoucherValidationError("Akun lawan tidak boleh sama dengan akun Kas/Bank")
        if kind == KIND_PAYMENT:
            await _reject_payroll_payable_counter(db, counter["id"])
        updates.update({
            "cash_bank_id": cash_bank["id"],
            "cash_bank_name": cash_bank.get("name"),
            "cash_bank_account_id": cash_bank.get("account_id"),
            "counter_account_id": counter["id"],
            "counter_account_code": counter.get("code"),
            "counter_account_name": counter.get("name"),
        })
    else:
        src_id = (
            (raw or {}).get("source_cash_bank_id")
            if "source_cash_bank_id" in (raw or {})
            else doc.get("source_cash_bank_id")
        )
        dst_id = (
            (raw or {}).get("dest_cash_bank_id")
            if "dest_cash_bank_id" in (raw or {})
            else doc.get("dest_cash_bank_id")
        )
        source = await require_active_cash_bank(db, src_id)
        dest = await require_active_cash_bank(db, dst_id)
        if source["id"] == dest["id"]:
            raise VoucherValidationError("Kas/Bank sumber dan tujuan tidak boleh sama")
        updates.update({
            "source_cash_bank_id": source["id"],
            "source_cash_bank_name": source.get("name"),
            "source_account_id": source.get("account_id"),
            "dest_cash_bank_id": dest["id"],
            "dest_cash_bank_name": dest.get("name"),
            "dest_account_id": dest.get("account_id"),
        })

    await db[coll].update_one({"id": doc["id"], "status": STATUS_DRAFT}, {"$set": updates})
    return await get_voucher(db, kind, doc["id"])


async def delete_draft(db, kind: str, voucher_id: str) -> dict:
    coll = _collection_for(kind)
    doc = await get_voucher_raw(db, kind, voucher_id)
    if not doc:
        raise KeyError("missing")
    if doc.get("status") != STATUS_DRAFT:
        raise VoucherConflictError("Hanya draft yang dapat dihapus")
    await db[coll].delete_one({"id": doc["id"], "status": STATUS_DRAFT})
    return public_voucher(doc, kind=kind)


def _build_post_lines(kind: str, doc: dict) -> List[dict]:
    amount = doc.get("amount")
    desc = doc.get("description") or doc.get("number") or ""
    if kind == KIND_RECEIPT:
        cash_aid = doc.get("cash_bank_account_id")
        counter_aid = doc.get("counter_account_id")
        return [
            {"account_id": cash_aid, "debit": amount, "credit": 0, "description": desc},
            {"account_id": counter_aid, "debit": 0, "credit": amount, "description": desc},
        ]
    if kind == KIND_PAYMENT:
        cash_aid = doc.get("cash_bank_account_id")
        counter_aid = doc.get("counter_account_id")
        return [
            {"account_id": counter_aid, "debit": amount, "credit": 0, "description": desc},
            {"account_id": cash_aid, "debit": 0, "credit": amount, "description": desc},
        ]
    if kind == KIND_TRANSFER:
        return [
            {
                "account_id": doc.get("dest_account_id"),
                "debit": amount,
                "credit": 0,
                "description": desc,
            },
            {
                "account_id": doc.get("source_account_id"),
                "debit": 0,
                "credit": amount,
                "description": desc,
            },
        ]
    raise VoucherValidationError("Jenis voucher tidak dikenal")


async def post_voucher(db, kind: str, voucher_id: str, *, user: Optional[dict] = None) -> dict:
    coll = _collection_for(kind)
    source_type = _source_type_for(kind)
    lock_key = f"cash_bank_post:{kind}:{voucher_id}"

    async with get_named_lock(lock_key):
        doc = await get_voucher_raw(db, kind, voucher_id)
        if not doc:
            raise KeyError("missing")
        if doc.get("status") == STATUS_POSTED:
            # Idempotent: already posted — return as-is (no duplicate journal)
            return public_voucher(doc, kind=kind)
        if doc.get("status") == STATUS_REVERSED:
            raise VoucherConflictError("Transaksi yang sudah dibatalkan tidak dapat diposting ulang")
        if doc.get("status") != STATUS_DRAFT:
            raise VoucherConflictError("Status transaksi tidak valid untuk posting")

        # Re-validate cash banks still active / COA present
        if kind in (KIND_RECEIPT, KIND_PAYMENT):
            await require_active_cash_bank(db, doc.get("cash_bank_id"))
            await _resolve_counter(db, doc.get("counter_account_id"))
            if kind == KIND_PAYMENT:
                await _reject_payroll_payable_counter(db, doc.get("counter_account_id"))
        else:
            await require_active_cash_bank(db, doc.get("source_cash_bank_id"))
            await require_active_cash_bank(db, doc.get("dest_cash_bank_id"))

        existing_j = await find_primary_journal_for_source(db, source_type, doc["id"])
        if existing_j:
            # Recover partial state: journal exists but voucher still draft
            await db[coll].update_one(
                {"id": doc["id"], "status": STATUS_DRAFT},
                {
                    "$set": {
                        "status": STATUS_POSTED,
                        "journal_id": existing_j["id"],
                        "posted_at": existing_j.get("posted_at") or now_iso(),
                        "posted_by": actor_snapshot(user),
                        "updated_at": now_iso(),
                        "updated_by": actor_snapshot(user),
                    }
                },
            )
            return await get_voucher(db, kind, doc["id"])

        lines = _build_post_lines(kind, doc)
        try:
            journal = await post_balanced_journal(
                db,
                date=doc.get("date") or today_ymd(),
                source_type=source_type,
                source_id=doc["id"],
                description=doc.get("description") or f"{kind} {doc.get('number')}",
                lines=lines,
                posted_by=user,
            )
        except PeriodLockedError as exc:
            raise VoucherConflictError(str(exc)) from exc
        except JournalValidationError as exc:
            raise VoucherValidationError(str(exc)) from exc
        except JournalConflictError as exc:
            # Race: another request posted — reload
            existing_j = await find_primary_journal_for_source(db, source_type, doc["id"])
            if existing_j:
                journal = existing_j
            else:
                raise VoucherConflictError(str(exc)) from exc

        now = now_iso()
        result = await db[coll].update_one(
            {"id": doc["id"], "status": STATUS_DRAFT},
            {
                "$set": {
                    "status": STATUS_POSTED,
                    "journal_id": journal["id"],
                    "posted_at": now,
                    "posted_by": actor_snapshot(user),
                    "updated_at": now,
                    "updated_by": actor_snapshot(user),
                }
            },
        )
        if result.matched_count == 0:
            # Another worker won — return current
            return await get_voucher(db, kind, doc["id"])
        return await get_voucher(db, kind, doc["id"])


async def reverse_voucher(db, kind: str, voucher_id: str, *, user: Optional[dict] = None) -> dict:
    coll = _collection_for(kind)
    source_type = _source_type_for(kind)
    lock_key = f"cash_bank_reverse:{kind}:{voucher_id}"

    async with get_named_lock(lock_key):
        doc = await get_voucher_raw(db, kind, voucher_id)
        if not doc:
            raise KeyError("missing")
        if doc.get("status") == STATUS_DRAFT:
            raise VoucherValidationError("Draft tidak dapat dibatalkan; hapus saja")
        if doc.get("status") == STATUS_REVERSED:
            raise VoucherConflictError("Transaksi sudah dibatalkan")
        if doc.get("status") != STATUS_POSTED:
            raise VoucherConflictError("Hanya transaksi posted yang dapat dibatalkan")
        journal_id = doc.get("journal_id")
        if not journal_id:
            raise VoucherValidationError("Transaksi posted tanpa jurnal")

        try:
            rev = await reverse_journal(
                db,
                journal_id,
                source_type=SOURCE_REVERSAL,
                source_id=doc["id"],
                posted_by=user,
                description=f"Reversal {doc.get('number')}",
            )
        except KeyError:
            raise KeyError("missing") from None
        except PeriodLockedError as exc:
            raise VoucherConflictError(str(exc)) from exc
        except JournalValidationError as exc:
            raise VoucherValidationError(str(exc)) from exc
        except JournalConflictError as exc:
            # Already reversed at journal layer
            existing = await get_voucher_raw(db, kind, voucher_id)
            if existing and existing.get("status") == STATUS_REVERSED:
                raise VoucherConflictError("Transaksi sudah dibatalkan") from exc
            raise VoucherConflictError(str(exc)) from exc

        now = now_iso()
        result = await db[coll].update_one(
            {"id": doc["id"], "status": STATUS_POSTED},
            {
                "$set": {
                    "status": STATUS_REVERSED,
                    "reversal_journal_id": rev["id"],
                    "reversed_at": now,
                    "reversed_by": actor_snapshot(user),
                    "updated_at": now,
                    "updated_by": actor_snapshot(user),
                }
            },
        )
        if result.matched_count == 0:
            raise VoucherConflictError("Transaksi sudah dibatalkan")
        return await get_voucher(db, kind, doc["id"])


async def get_voucher_with_journal(db, kind: str, voucher_id: str) -> Optional[dict]:
    voucher = await get_voucher(db, kind, voucher_id)
    if not voucher:
        return None
    journal = None
    if voucher.get("journal_id"):
        journal = await get_journal(db, voucher["journal_id"], with_lines=True)
    reversal = None
    if voucher.get("reversal_journal_id"):
        reversal = await get_journal(db, voucher["reversal_journal_id"], with_lines=True)
    voucher["journal"] = journal
    voucher["reversal_journal"] = reversal
    return voucher


async def set_voucher_attachment(db, kind: str, voucher_id: str, attachment: Optional[dict]) -> dict:
    """Attach public metadata only (no storage paths). Draft or posted OK; attachment is independent."""
    coll = _collection_for(kind)
    doc = await get_voucher_raw(db, kind, voucher_id)
    if not doc:
        raise KeyError("missing")
    await db[coll].update_one(
        {"id": doc["id"]},
        {"$set": {"attachment": _public_attachment(attachment), "updated_at": now_iso()}},
    )
    return await get_voucher(db, kind, doc["id"])
