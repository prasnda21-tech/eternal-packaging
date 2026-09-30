"""Payroll payment documents.

One payment per finalized payroll period. The amount is the Utang Gaji
credit on the posted accrual journal, not a fresh slip calculation.
Posting the bank journal is a separate user action.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.cash_bank_master import (
    CashBankValidationError,
    get_cash_bank_raw,
    require_active_cash_bank,
    require_bank_coa,
)
from services.chart_of_accounts import get_account
from services.hr_payroll import LINES, STATUS_FINAL, get_period, money_float, to_money
from services.journal_core import (
    STATUS_POSTED,
    find_primary_journal_for_source,
)
from services.payroll_journal_posting import SOURCE_TYPE as ACCRUAL_SOURCE_TYPE
from services.purchase_common import (
    actor_snapshot,
    get_named_lock,
    next_dated_seq,
    now_iso,
)

PAYMENTS = "hr_payroll_payments"
PAYMENT_LINES = "hr_payroll_payment_lines"
SOURCE_TYPE = "payroll_payment"
NUMBER_PREFIX = "PYB"

PERM_VIEW = "payroll_payment.view"
PERM_CREATE = "payroll_payment.create"
PERM_APPROVE = "payroll_payment.approve"
PERM_POST = "payroll_payment.post"
PERM_REVERSE = "payroll_payment.reverse"

STATUS_DRAFT = "DRAFT"
STATUS_APPROVED = "APPROVED"
STATUS_PAID = "PAID"

PAYABLE_MARKER = "Utang Gaji"

NOT_FINAL_MESSAGE = "Pembayaran hanya dapat dibuat untuk periode payroll FINAL."
NO_ACCRUAL_MESSAGE = "Jurnal akrual payroll belum diposting."
PAYABLE_MISSING_MESSAGE = "Jurnal akrual tidak memiliki satu kredit Utang Gaji."
BANK_MESSAGE = "Kas/Bank wajib dipilih dan harus aktif dengan akun bertipe BANK."
DATE_MESSAGE = "Tanggal pembayaran tidak valid."
EXISTS_MESSAGE = "Pembayaran untuk periode payroll ini sudah ada."
NOT_DRAFT_MESSAGE = "Hanya pembayaran DRAFT yang dapat disetujui."
NOT_APPROVED_MESSAGE = "Pembayaran harus disetujui sebelum diposting ke accounting."


async def ensure_indexes(db) -> None:
    payments = db[PAYMENTS]
    await payments.create_index("id", unique=True)
    await payments.create_index("payment_number", unique=True)
    await payments.create_index("payroll_period_id", unique=True)
    await payments.create_index("status")
    lines = db[PAYMENT_LINES]
    await lines.create_index("id", unique=True)
    await lines.create_index("payment_id")
    await lines.create_index([("payment_id", 1), ("employee_id", 1)], unique=True)


def _journal_date(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) < 10 or text[4] != "-" or not text[:4].isdigit() or not text[5:7].isdigit():
        raise HTTPException(400, DATE_MESSAGE)
    return text[:10]


def payable_credit_from_accrual(journal: Optional[dict]) -> Dict[str, Any]:
    """The single posted credit line named Utang Gaji. Not recomputed from slips."""
    if not journal or journal.get("status") != STATUS_POSTED:
        raise HTTPException(409, NO_ACCRUAL_MESSAGE)
    matches = []
    for line in journal.get("lines") or []:
        if not isinstance(line, dict):
            continue
        credit = to_money(line.get("credit"))
        debit = to_money(line.get("debit"))
        description = str(line.get("description") or "")
        if credit > 0 and debit == 0 and description.endswith(PAYABLE_MARKER):
            account_id = str(line.get("account_id") or "").strip()
            if account_id:
                matches.append({"account_id": account_id, "amount": credit})
    if len(matches) != 1:
        raise HTTPException(409, PAYABLE_MISSING_MESSAGE)
    return matches[0]


async def resolve_cash_bank(db, cash_bank_account_id: Any) -> dict:
    """Active cash/bank master whose linked COA account_type is BANK."""
    raw_id = str(cash_bank_account_id or "").strip()
    if not raw_id:
        raise HTTPException(400, BANK_MESSAGE)
    master = await get_cash_bank_raw(db, raw_id)
    if not master:
        master = await db["cash_bank_accounts"].find_one({"account_id": raw_id}, {"_id": 0})
    if not master:
        raise HTTPException(400, BANK_MESSAGE)
    try:
        active = await require_active_cash_bank(db, master["id"])
        coa = require_bank_coa(await get_account(db, active.get("account_id") or ""))
    except CashBankValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "cash_bank_account_id": str(active["id"]),
        "bank_coa_id": str(coa["id"]),
        "bank_name": str(active.get("name") or ""),
    }


async def _employee_lines(db, period_id: str, payment_id: str) -> List[dict]:
    rows = await db[LINES].find({"period_id": period_id}, {"_id": 0}).sort(
        "employee_snapshot.employee_number", 1
    ).to_list(5000)
    if not rows:
        raise HTTPException(409, "Periode payroll tidak memiliki baris karyawan.")
    now = now_iso()
    lines = []
    for row in rows:
        snap = row.get("employee_snapshot") or {}
        lines.append({
            "id": str(uuid.uuid4()),
            "payment_id": payment_id,
            "employee_id": str(row.get("employee_id") or ""),
            "employee_name": str(snap.get("name") or ""),
            "net_salary": money_float(to_money(row.get("net"))),
            "created_at": now,
        })
    return lines


def public_payment(doc: Optional[dict], lines: Optional[List[dict]] = None) -> Optional[dict]:
    if not doc:
        return None
    out = {
        "id": doc.get("id"),
        "payment_number": doc.get("payment_number"),
        "payroll_period_id": doc.get("payroll_period_id"),
        "payment_date": doc.get("payment_date"),
        "cash_bank_account_id": doc.get("cash_bank_account_id"),
        "bank_coa_id": doc.get("bank_coa_id"),
        "payable_account_id": doc.get("payable_account_id"),
        "total_amount": doc.get("total_amount"),
        "status": doc.get("status"),
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at"),
        "approved_by": doc.get("approved_by"),
        "approved_at": doc.get("approved_at"),
        "paid_by": doc.get("paid_by"),
        "paid_at": doc.get("paid_at"),
        "accounting_journal_id": doc.get("accounting_journal_id"),
    }
    if lines is not None:
        out["lines"] = [
            {
                "id": line.get("id"),
                "payment_id": line.get("payment_id"),
                "employee_id": line.get("employee_id"),
                "employee_name": line.get("employee_name"),
                "net_salary": line.get("net_salary"),
            }
            for line in lines
        ]
    return out


async def get_payment(db, payment_id: str, *, with_lines: bool = False) -> dict:
    doc = await db[PAYMENTS].find_one({"id": str(payment_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Pembayaran payroll tidak ditemukan.")
    if not with_lines:
        return doc
    lines = await db[PAYMENT_LINES].find({"payment_id": doc["id"]}, {"_id": 0}).sort("employee_name", 1).to_list(5000)
    return public_payment(doc, lines)


async def create_payroll_payment(db, period_id: str, payload: dict, user: dict) -> dict:
    await ensure_indexes(db)
    period = await get_period(db, period_id)
    if str(period.get("status") or "") != STATUS_FINAL:
        raise HTTPException(409, NOT_FINAL_MESSAGE)
    accrual = await find_primary_journal_for_source(db, ACCRUAL_SOURCE_TYPE, str(period["id"]))
    payable = payable_credit_from_accrual(accrual)
    bank = await resolve_cash_bank(db, (payload or {}).get("cash_bank_account_id"))
    payment_date = _journal_date((payload or {}).get("payment_date") or period.get("end_date"))
    amount = payable["amount"]
    if amount <= 0:
        raise HTTPException(409, PAYABLE_MISSING_MESSAGE)

    async with get_named_lock(f"payroll_payment_create:{period['id']}"):
        existing = await db[PAYMENTS].find_one({"payroll_period_id": period["id"]}, {"_id": 0, "id": 1})
        if existing:
            raise HTTPException(409, EXISTS_MESSAGE)
        payment_id = str(uuid.uuid4())
        detail = await _employee_lines(db, period["id"], payment_id)
        now = now_iso()
        doc = {
            "id": payment_id,
            "payment_number": "",
            "payroll_period_id": period["id"],
            "payment_date": payment_date,
            "cash_bank_account_id": bank["cash_bank_account_id"],
            "bank_coa_id": bank["bank_coa_id"],
            "payable_account_id": payable["account_id"],
            "accrual_journal_id": accrual.get("id"),
            "total_amount": money_float(amount),
            "status": STATUS_DRAFT,
            "created_by": actor_snapshot(user),
            "created_at": now,
            "approved_by": None,
            "approved_at": None,
            "paid_by": None,
            "paid_at": None,
            "accounting_journal_id": None,
        }
        saved = None
        last_err = None
        for _ in range(25):
            doc["payment_number"] = await next_dated_seq(db, PAYMENTS, "payment_number", NUMBER_PREFIX)
            try:
                await db[PAYMENTS].insert_one(dict(doc))
                doc.pop("_id", None)
                saved = doc
                break
            except DuplicateKeyError as exc:
                last_err = exc
                message = str(exc)
                if "payroll_period_id" in message:
                    raise HTTPException(409, EXISTS_MESSAGE) from exc
                if "payment_number" in message or "E11000" in message:
                    continue
                raise HTTPException(409, EXISTS_MESSAGE) from exc
        if not saved:
            raise HTTPException(409, f"Gagal mengalokasikan nomor pembayaran. {last_err}")
        try:
            if detail:
                await db[PAYMENT_LINES].insert_many(detail)
        except Exception:
            await db[PAYMENT_LINES].delete_many({"payment_id": payment_id})
            await db[PAYMENTS].delete_one({"id": payment_id})
            raise
        return public_payment(saved, detail)


async def approve_payroll_payment(db, payment_id: str, user: dict) -> dict:
    async with get_named_lock(f"payroll_payment_approve:{payment_id}"):
        doc = await db[PAYMENTS].find_one({"id": str(payment_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Pembayaran payroll tidak ditemukan.")
        if doc.get("status") == STATUS_APPROVED:
            lines = await db[PAYMENT_LINES].find({"payment_id": doc["id"]}, {"_id": 0}).to_list(5000)
            return public_payment(doc, lines)
        if doc.get("status") != STATUS_DRAFT:
            raise HTTPException(409, NOT_DRAFT_MESSAGE)
        now = now_iso()
        await db[PAYMENTS].update_one(
            {"id": doc["id"], "status": STATUS_DRAFT},
            {"$set": {
                "status": STATUS_APPROVED,
                "approved_by": actor_snapshot(user),
                "approved_at": now,
            }},
        )
        fresh = await db[PAYMENTS].find_one({"id": doc["id"]}, {"_id": 0})
        lines = await db[PAYMENT_LINES].find({"payment_id": doc["id"]}, {"_id": 0}).to_list(5000)
        return public_payment(fresh, lines)


async def mark_payment_paid(db, payment: dict, journal: dict, user: dict) -> dict:
    """Stamp PAID metadata. Does not change the payroll period status."""
    now = now_iso()
    patch: Dict[str, Any] = {}
    if payment.get("status") != STATUS_PAID:
        patch["status"] = STATUS_PAID
    if not payment.get("paid_at"):
        patch["paid_at"] = journal.get("posted_at") or now
    if not payment.get("paid_by"):
        patch["paid_by"] = actor_snapshot(user)
    journal_id = str(journal.get("id") or "")
    if journal_id and payment.get("accounting_journal_id") != journal_id:
        patch["accounting_journal_id"] = journal_id
    if patch:
        await db[PAYMENTS].update_one({"id": payment["id"]}, {"$set": patch})
    fresh = await db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0})
    lines = await db[PAYMENT_LINES].find({"payment_id": payment["id"]}, {"_id": 0}).to_list(5000)
    return public_payment(fresh, lines)


async def period_has_posted_payroll_payment(db, period_id: str) -> bool:
    rows = await db[PAYMENTS].find(
        {"payroll_period_id": str(period_id)},
        {"_id": 0, "id": 1},
    ).to_list(20)
    for row in rows:
        journal = await find_primary_journal_for_source(db, SOURCE_TYPE, str(row.get("id") or ""))
        if journal and journal.get("status") == STATUS_POSTED:
            return True
    return False
