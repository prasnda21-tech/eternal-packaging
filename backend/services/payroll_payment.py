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
from pymongo import ReturnDocument
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
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_POSTED,
    find_primary_journal_for_source,
    get_journal,
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
SOURCE_REVERSAL = "payroll_payment_reversal"
NUMBER_PREFIX = "PYB"

PERM_VIEW = "payroll_payment.view"
PERM_CREATE = "payroll_payment.create"
PERM_APPROVE = "payroll_payment.approve"
PERM_POST = "payroll_payment.post"
PERM_REVERSE = "payroll_payment.reverse"
PERM_CANCEL = "payroll_payment.cancel"

STATUS_DRAFT = "DRAFT"
STATUS_APPROVED = "APPROVED"
STATUS_PAID = "PAID"
STATUS_REVERSED = "REVERSED"
STATUS_CANCELLED = "CANCELLED"
ACTIVE_STATUSES = (STATUS_DRAFT, STATUS_APPROVED, STATUS_PAID)
ACTIVE_PERIOD_INDEX = "uniq_active_payroll_period_payment"

# Normalized payment visibility for UI (separate from claim payroll_status).
VIS_BELUM_DIBAYAR = "BELUM_DIBAYAR"
VIS_MENUNGGU_PERSETUJUAN = "MENUNGGU_PERSETUJUAN"
VIS_SIAP_DIPOSTING = "SIAP_DIPOSTING"
VIS_SUDAH_DIBAYAR = "SUDAH_DIBAYAR"
PAYMENT_VISIBILITY_STATUSES = (
    VIS_BELUM_DIBAYAR,
    VIS_MENUNGGU_PERSETUJUAN,
    VIS_SIAP_DIPOSTING,
    VIS_SUDAH_DIBAYAR,
)
PAYMENT_VISIBILITY_LABELS = {
    VIS_BELUM_DIBAYAR: "Belum Dibayar",
    VIS_MENUNGGU_PERSETUJUAN: "Menunggu Persetujuan",
    VIS_SIAP_DIPOSTING: "Siap Diposting",
    VIS_SUDAH_DIBAYAR: "Sudah Dibayar",
}
# Unpaid-first precedence when one operator spans multiple periods.
PAYMENT_VISIBILITY_RANK = {
    VIS_BELUM_DIBAYAR: 0,
    VIS_MENUNGGU_PERSETUJUAN: 1,
    VIS_SIAP_DIPOSTING: 2,
    VIS_SUDAH_DIBAYAR: 3,
}

PAYABLE_MARKER = "Utang Gaji"

NOT_FINAL_MESSAGE = "Pembayaran hanya dapat dibuat untuk periode payroll FINAL."
NO_ACCRUAL_MESSAGE = "Jurnal akrual payroll belum diposting."
PAYABLE_MISSING_MESSAGE = "Jurnal akrual tidak memiliki satu kredit Utang Gaji."
BANK_MESSAGE = "Kas/Bank wajib dipilih dan harus aktif dengan akun bertipe BANK."
DATE_MESSAGE = "Tanggal pembayaran tidak valid."
EXISTS_MESSAGE = "Pembayaran untuk periode payroll ini sudah ada."
NOT_DRAFT_MESSAGE = "Hanya pembayaran DRAFT yang dapat disetujui."
NOT_APPROVED_MESSAGE = "Pembayaran harus disetujui sebelum diposting ke accounting."
NOT_PAID_MESSAGE = "Hanya pembayaran PAID yang dapat di-reverse."
REASON_MESSAGE = "Alasan reversal wajib diisi minimal 3 karakter."
CANCEL_MESSAGE = "Hanya pembayaran DRAFT atau APPROVED tanpa jurnal yang dapat dibatalkan."
CANCEL_REASON_MESSAGE = "Alasan pembatalan wajib diisi minimal 3 karakter."
INCOMPLETE_MESSAGE = "Pembayaran tidak lengkap. Batalkan pembayaran ini, lalu buat pembayaran baru."
CREATE_FAILED_MESSAGE = "Detail pembayaran gagal disimpan. Pembayaran ditandai batal dan dapat dibuat ulang."
CREATE_FAILED_REASON = "Detail pembayaran gagal disimpan."
INDEX_CONFLICT_MESSAGE = "Migrasi index dihentikan karena ada lebih dari satu pembayaran aktif untuk periode yang sama."
INDEX_MISSING_MESSAGE = "Index pembayaran aktif belum siap. Pembayaran tidak disimpan."


def _index_keys(spec: dict) -> list:
    return [(str(field), int(direction)) for field, direction in (spec.get("key") or [])]


def _active_status_filter(spec: Optional[dict]) -> set:
    partial = (spec or {}).get("partialFilterExpression") or {}
    values = ((partial.get("status") or {}).get("$in")) or []
    return {str(value) for value in values}


def _is_active_period_index(spec: Optional[dict]) -> bool:
    return bool(
        spec
        and spec.get("unique")
        and _active_status_filter(spec) == set(ACTIVE_STATUSES)
    )


def _is_legacy_period_unique(name: str, spec: dict) -> bool:
    if name == ACTIVE_PERIOD_INDEX:
        return False
    return (
        _index_keys(spec) == [("payroll_period_id", 1)]
        and bool(spec.get("unique"))
        and not spec.get("partialFilterExpression")
    )


async def _active_period_conflicts(payments) -> List[dict]:
    return await payments.aggregate([
        {"$match": {"status": {"$in": list(ACTIVE_STATUSES)}}},
        {"$group": {"_id": "$payroll_period_id", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}},
    ]).to_list(20)


async def _create_active_period_index(payments) -> None:
    await payments.create_index(
        [("payroll_period_id", 1)],
        unique=True,
        name=ACTIVE_PERIOD_INDEX,
        partialFilterExpression={"status": {"$in": list(ACTIVE_STATUSES)}},
    )


async def _migrate_active_payment_index(payments) -> None:
    """Install the active-payment index before removing the old unique index.

    Documents are never deleted. A failed new index leaves the old unique
    index in place, so inserts stay constrained.
    """
    conflicts = await _active_period_conflicts(payments)
    if conflicts:
        raise HTTPException(409, INDEX_CONFLICT_MESSAGE)
    info = await payments.index_information()
    if not _is_active_period_index(info.get(ACTIVE_PERIOD_INDEX)):
        await _create_active_period_index(payments)
        info = await payments.index_information()
    if not _is_active_period_index(info.get(ACTIVE_PERIOD_INDEX)):
        raise HTTPException(503, INDEX_MISSING_MESSAGE)
    if not any(
        name == "payroll_period_id_lookup" or (
            _index_keys(spec) == [("payroll_period_id", 1)] and not spec.get("unique")
        )
        for name, spec in info.items()
    ):
        await payments.create_index([("payroll_period_id", 1)], name="payroll_period_id_lookup")
        info = await payments.index_information()
    if not _is_active_period_index(info.get(ACTIVE_PERIOD_INDEX)):
        raise HTTPException(503, INDEX_MISSING_MESSAGE)
    for name, spec in list(info.items()):
        if _is_legacy_period_unique(name, spec):
            await payments.drop_index(name)


async def require_active_payment_index(db) -> None:
    info = await db[PAYMENTS].index_information()
    if not _is_active_period_index(info.get(ACTIVE_PERIOD_INDEX)):
        raise HTTPException(503, INDEX_MISSING_MESSAGE)


async def ensure_indexes(db) -> None:
    payments = db[PAYMENTS]
    await payments.create_index("id", unique=True)
    await payments.create_index("payment_number", unique=True)
    await payments.create_index("status")
    await _migrate_active_payment_index(payments)
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
    """Utang Gaji from the accrual snapshot, else one unambiguous legacy line.

    A stored payroll_payable snapshot wins even when another credit uses the
    same account or a component name ends with Utang Gaji. This function does
    not write the journal.
    """
    if not journal or journal.get("status") != STATUS_POSTED:
        raise HTTPException(409, NO_ACCRUAL_MESSAGE)
    snap = journal.get("payroll_payable")
    if isinstance(snap, dict):
        account_id = str(snap.get("account_id") or "").strip()
        amount = to_money(snap.get("amount"))
        if account_id and amount > 0:
            return {"account_id": account_id, "amount": amount}
        raise HTTPException(409, PAYABLE_MISSING_MESSAGE)
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


def map_payment_visibility(payment_status: Optional[str]) -> Optional[str]:
    """Map active payment document status → UI visibility code. None = no payment."""
    status = str(payment_status or "").strip().upper()
    if not status:
        return VIS_BELUM_DIBAYAR
    if status == STATUS_DRAFT:
        return VIS_MENUNGGU_PERSETUJUAN
    if status == STATUS_APPROVED:
        return VIS_SIAP_DIPOSTING
    if status == STATUS_PAID:
        return VIS_SUDAH_DIBAYAR
    # Cancelled / reversed are not active; treat as no active payment.
    return VIS_BELUM_DIBAYAR


def payment_visibility_label(code: Optional[str]) -> str:
    if not code:
        return ""
    return PAYMENT_VISIBILITY_LABELS.get(str(code), str(code))


def summarize_payment_visibility(codes: List[Optional[str]]) -> Optional[str]:
    """Unpaid-first among relevant visibility codes. Ignores empty/None."""
    present = [str(code) for code in codes if code in PAYMENT_VISIBILITY_RANK]
    if not present:
        return None
    return min(present, key=lambda code: PAYMENT_VISIBILITY_RANK[code])


def payment_visibility_fields(
    payment: Optional[dict],
    *,
    period_final: bool = False,
) -> Dict[str, Any]:
    """Safe read-only visibility fields for period/detail responses."""
    empty = {
        "payment_status": None,
        "payment_status_label": "",
        "payment_id": None,
        "payment_bank_account": None,
        "payment_date": None,
        "paid_at": None,
        "payment_journal_id": None,
        "payment_number": None,
        "payment_amount": None,
        "payment_raw_status": None,
    }
    if not period_final:
        return empty
    if not payment or str(payment.get("status") or "") not in ACTIVE_STATUSES:
        return {
            **empty,
            "payment_status": VIS_BELUM_DIBAYAR,
            "payment_status_label": payment_visibility_label(VIS_BELUM_DIBAYAR),
        }
    code = map_payment_visibility(payment.get("status"))
    return {
        "payment_status": code,
        "payment_status_label": payment_visibility_label(code),
        "payment_id": payment.get("id"),
        "payment_bank_account": payment.get("cash_bank_account_id"),
        "payment_date": payment.get("payment_date"),
        "paid_at": payment.get("paid_at"),
        "payment_journal_id": payment.get("accounting_journal_id"),
        "payment_number": payment.get("payment_number"),
        "payment_amount": payment.get("total_amount"),
        "payment_raw_status": payment.get("status"),
    }


def enrich_period_with_payment_visibility(period: Optional[dict], payment: Optional[dict]) -> Optional[dict]:
    """Attach payment visibility onto a period dict. Does not write."""
    if not period:
        return period
    out = dict(period)
    out.update(payment_visibility_fields(
        payment,
        period_final=str(period.get("status") or "") == STATUS_FINAL,
    ))
    return out


async def active_payments_by_period(db, period_ids: List[str]) -> Dict[str, dict]:
    """Latest active payment per period. Read-only; no journal enrichment."""
    ids = sorted({str(pid).strip() for pid in period_ids if str(pid or "").strip()})
    if not ids:
        return {}
    docs = await db[PAYMENTS].find(
        {"payroll_period_id": {"$in": ids}, "status": {"$in": list(ACTIVE_STATUSES)}},
        {
            "_id": 0,
            "id": 1,
            "payroll_period_id": 1,
            "status": 1,
            "payment_date": 1,
            "cash_bank_account_id": 1,
            "paid_at": 1,
            "accounting_journal_id": 1,
            "payment_number": 1,
            "total_amount": 1,
            "created_at": 1,
        },
    ).to_list(len(ids) + 50)
    out: Dict[str, dict] = {}
    for doc in docs:
        pid = str(doc.get("payroll_period_id") or "")
        if not pid:
            continue
        previous = out.get(pid)
        if previous is None or str(doc.get("created_at") or "") >= str(previous.get("created_at") or ""):
            out[pid] = doc
    return out


def public_payment(doc: Optional[dict], lines: Optional[List[dict]] = None) -> Optional[dict]:
    if not doc:
        return None
    active = str(doc.get("status") or "") in ACTIVE_STATUSES
    visibility = payment_visibility_fields(doc, period_final=True) if active else {
        "payment_status": None,
        "payment_status_label": "",
    }
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
        "payment_status": visibility.get("payment_status"),
        "payment_status_label": visibility.get("payment_status_label"),
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at"),
        "approved_by": doc.get("approved_by"),
        "approved_at": doc.get("approved_at"),
        "paid_by": doc.get("paid_by"),
        "paid_at": doc.get("paid_at"),
        "accounting_journal_id": doc.get("accounting_journal_id"),
        "reversal_reason": doc.get("reversal_reason"),
        "reversed_by": doc.get("reversed_by"),
        "reversed_at": doc.get("reversed_at"),
        "reversal_journal_id": doc.get("reversal_journal_id"),
        "cancellation_reason": doc.get("cancellation_reason"),
        "cancelled_by": doc.get("cancelled_by"),
        "cancelled_at": doc.get("cancelled_at"),
        "journal_number": doc.get("journal_number"),
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
    lines = await payment_lines(db, doc["id"])
    return await _present(db, doc, lines)


def payment_is_complete(doc: dict, lines: List[dict]) -> bool:
    """Detail rows must be complete and their nets must equal the header total."""
    if not lines:
        return False
    total = to_money(doc.get("total_amount"))
    if total <= 0:
        return False
    if not str(doc.get("payable_account_id") or "").strip():
        return False
    if not str(doc.get("cash_bank_account_id") or "").strip():
        return False
    if not str(doc.get("payment_date") or "").strip():
        return False
    seen = set()
    summed = to_money(0)
    for line in lines:
        if not isinstance(line, dict):
            return False
        employee_id = str(line.get("employee_id") or "").strip()
        if not employee_id or employee_id in seen:
            return False
        if "net_salary" not in line or line.get("net_salary") is None or str(line.get("net_salary")).strip() == "":
            return False
        seen.add(employee_id)
        summed += to_money(line.get("net_salary"))
    return summed == total


async def payment_lines(db, payment_id: str) -> List[dict]:
    return await db[PAYMENT_LINES].find({"payment_id": str(payment_id)}, {"_id": 0}).sort("employee_name", 1).to_list(5000)


async def require_complete_payment(db, payment: dict) -> List[dict]:
    lines = await payment_lines(db, payment["id"])
    if not payment_is_complete(payment, lines):
        raise HTTPException(409, INCOMPLETE_MESSAGE)
    return lines


async def read_payment_journal_reference(db, payment_id: str) -> Dict[str, Any]:
    """Read the payment journal number from accounting_journal_id. Does not post."""
    payment = await get_payment(db, payment_id)
    journal_id = str(payment.get("accounting_journal_id") or "").strip()
    if not journal_id:
        return {"journal_id": None, "journal_number": None}
    journal = await get_journal(db, journal_id, with_lines=False)
    same_source = (
        journal
        and str(journal.get("source_type") or "") == SOURCE_TYPE
        and str(journal.get("source_id") or "") == str(payment["id"])
    )
    if not same_source:
        return {"journal_id": journal_id, "journal_number": None}
    return {"journal_id": str(journal.get("id") or journal_id), "journal_number": journal.get("number")}


async def _present(db, doc: Optional[dict], lines: Optional[List[dict]] = None) -> Optional[dict]:
    public = public_payment(doc, lines)
    if not public:
        return None
    ref = await read_payment_journal_reference(db, public["id"])
    public["journal_number"] = ref.get("journal_number")
    return public


async def get_payment_for_period(db, period_id: str) -> Optional[dict]:
    """Read the active payment for a period. Does not create or post anything."""
    active, _history = await list_payments_for_period(db, period_id)
    return active


async def list_payments_for_period(db, period_id: str):
    """Active payment plus every stored payment for the period, oldest first."""
    period = await get_period(db, period_id)
    docs = await db[PAYMENTS].find(
        {"payroll_period_id": period["id"]},
        {"_id": 0},
    ).sort("created_at", 1).to_list(50)
    history = []
    active = None
    for doc in docs:
        lines = await payment_lines(db, doc["id"])
        presented = await _present(db, doc, lines)
        history.append(presented)
        if presented.get("status") in ACTIVE_STATUSES:
            active = presented
    return active, history


async def _insert_payment_lines(db, detail: List[dict]) -> None:
    if detail:
        await db[PAYMENT_LINES].insert_many(detail)


async def _mark_create_failed(db, payment_id: str, user: dict) -> None:
    """Leave the header as cancelled history. Does not delete it or post a journal."""
    await db[PAYMENTS].update_one(
        {"id": payment_id, "status": STATUS_DRAFT},
        {"$set": {
            "status": STATUS_CANCELLED,
            "cancelled_at": now_iso(),
            "cancelled_by": actor_snapshot(user),
            "cancellation_reason": CREATE_FAILED_REASON,
        }},
    )


async def create_payroll_payment(db, period_id: str, payload: dict, user: dict) -> dict:
    await ensure_indexes(db)
    period = await get_period(db, period_id)
    if str(period.get("status") or "") != STATUS_FINAL:
        raise HTTPException(409, NOT_FINAL_MESSAGE)
    accrual = await db[JOURNAL_ENTRIES].find_one(
        {
            "source_type": ACCRUAL_SOURCE_TYPE,
            "source_id": str(period["id"]),
            "reversal_of": None,
        },
        {"_id": 0},
    )
    if accrual:
        accrual["lines"] = await db[JOURNAL_LINES].find(
            {"journal_id": accrual["id"]}, {"_id": 0}
        ).to_list(100)
    payable = payable_credit_from_accrual(accrual)
    bank = await resolve_cash_bank(db, (payload or {}).get("cash_bank_account_id"))
    payment_date = _journal_date((payload or {}).get("payment_date") or period.get("end_date"))
    amount = payable["amount"]
    if amount <= 0:
        raise HTTPException(409, PAYABLE_MISSING_MESSAGE)

    await require_active_payment_index(db)
    async with get_named_lock(f"payroll_payment_create:{period['id']}"):
        existing = await db[PAYMENTS].find_one(
            {"payroll_period_id": period["id"], "status": {"$in": list(ACTIVE_STATUSES)}},
            {"_id": 0, "id": 1},
        )
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
                if "payroll_period_id" in message or ACTIVE_PERIOD_INDEX in message:
                    raise HTTPException(409, EXISTS_MESSAGE) from exc
                if "payment_number" in message or "E11000" in message:
                    continue
                raise HTTPException(409, EXISTS_MESSAGE) from exc
        if not saved:
            raise HTTPException(409, f"Gagal mengalokasikan nomor pembayaran. {last_err}")
        try:
            await _insert_payment_lines(db, detail)
        except Exception as exc:
            await _mark_create_failed(db, payment_id, user)
            raise HTTPException(409, CREATE_FAILED_MESSAGE) from exc
        fresh = await db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0})
        return await _present(db, fresh, detail)


async def approve_payroll_payment(db, payment_id: str, user: dict) -> dict:
    async with get_named_lock(f"payroll_payment_approve:{payment_id}"):
        doc = await db[PAYMENTS].find_one({"id": str(payment_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, "Pembayaran payroll tidak ditemukan.")
        if doc.get("status") == STATUS_APPROVED:
            lines = await require_complete_payment(db, doc)
            return await _present(db, doc, lines)
        if doc.get("status") != STATUS_DRAFT:
            raise HTTPException(409, NOT_DRAFT_MESSAGE)
        lines = await require_complete_payment(db, doc)
        now = now_iso()
        result = await db[PAYMENTS].update_one(
            {"id": doc["id"], "status": STATUS_DRAFT},
            {"$set": {
                "status": STATUS_APPROVED,
                "approved_by": actor_snapshot(user),
                "approved_at": now,
            }},
        )
        fresh = await db[PAYMENTS].find_one({"id": doc["id"]}, {"_id": 0})
        if result.modified_count != 1 or fresh.get("status") != STATUS_APPROVED:
            if fresh.get("status") == STATUS_APPROVED:
                return await _present(db, fresh, lines)
            raise HTTPException(409, NOT_DRAFT_MESSAGE)
        return await _present(db, fresh, lines)


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
    statuses = [STATUS_APPROVED, STATUS_PAID]
    unset = {"post_claim": ""}
    if payment.get("status") == STATUS_CANCELLED and str((journal or {}).get("id") or "").strip():
        statuses.append(STATUS_CANCELLED)
        patch["status"] = STATUS_PAID
        unset.update({
            "cancelled_at": "",
            "cancelled_by": "",
            "cancellation_reason": "",
        })
    update: Dict[str, Any] = {"$unset": unset}
    if patch:
        update["$set"] = patch
    if patch or payment.get("post_claim") or payment.get("status") == STATUS_CANCELLED:
        await db[PAYMENTS].update_one(
            {"id": payment["id"], "status": {"$in": statuses}},
            update,
        )
    fresh = await db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0})
    lines = await payment_lines(db, payment["id"])
    return await _present(db, fresh, lines)


async def mark_payment_reversed(db, payment: dict, journal: dict, user: dict, reason: str) -> dict:
    """Stamp reversal metadata on the same payment. Does not insert a new payment."""
    now = now_iso()
    patch: Dict[str, Any] = {}
    if payment.get("status") != STATUS_REVERSED:
        patch["status"] = STATUS_REVERSED
    if not payment.get("reversed_at"):
        patch["reversed_at"] = (journal or {}).get("posted_at") or now
    if not payment.get("reversed_by"):
        patch["reversed_by"] = actor_snapshot(user)
    if reason and not str(payment.get("reversal_reason") or "").strip():
        patch["reversal_reason"] = reason
    journal_id = str((journal or {}).get("id") or "")
    if journal_id and payment.get("reversal_journal_id") != journal_id:
        patch["reversal_journal_id"] = journal_id
    if patch:
        await db[PAYMENTS].update_one({"id": payment["id"]}, {"$set": patch})
    fresh = await db[PAYMENTS].find_one({"id": payment["id"]}, {"_id": 0})
    lines = await payment_lines(db, payment["id"])
    return await _present(db, fresh, lines)


def _cancel_reason(raw: Any) -> str:
    reason = str(raw or "").strip()
    if len(reason) < 3:
        raise HTTPException(400, CANCEL_REASON_MESSAGE)
    return reason


def _blank_field_clause(field: str) -> Dict[str, Any]:
    return {"$or": [
        {field: None},
        {field: ""},
        {field: {"$exists": False}},
    ]}


async def posted_payment_journal(db, payment_id: str) -> Optional[dict]:
    return await db[JOURNAL_ENTRIES].find_one(
        {
            "source_type": SOURCE_TYPE,
            "source_id": str(payment_id),
            "reversal_of": None,
            "status": STATUS_POSTED,
        },
        {"_id": 0, "id": 1, "status": 1},
    )


async def _payment_journal_reference(db, payment: dict) -> Optional[dict]:
    referenced = str(payment.get("accounting_journal_id") or "").strip()
    if referenced:
        return {"id": referenced}
    return await db[JOURNAL_ENTRIES].find_one(
        {"source_type": SOURCE_TYPE, "source_id": str(payment["id"]), "reversal_of": None},
        {"_id": 0, "id": 1},
    )


async def claim_payment_for_post(db, payment_id: str) -> dict:
    """Atomically reserve an APPROVED payment. Works across processes."""
    token = str(uuid.uuid4())
    claimed = await db[PAYMENTS].find_one_and_update(
        {
            "id": str(payment_id),
            "status": STATUS_APPROVED,
            **_blank_field_clause("post_claim"),
        },
        {"$set": {"post_claim": token}},
        return_document=ReturnDocument.AFTER,
    )
    if claimed:
        return claimed
    current = await get_payment(db, payment_id)
    if await posted_payment_journal(db, payment_id):
        return current
    existing_claim = str(current.get("post_claim") or "")
    if current.get("status") == STATUS_APPROVED and existing_claim and not await posted_payment_journal(db, payment_id):
        stolen = await db[PAYMENTS].find_one_and_update(
            {"id": current["id"], "status": STATUS_APPROVED, "post_claim": existing_claim},
            {"$set": {"post_claim": token}},
            return_document=ReturnDocument.AFTER,
        )
        if stolen:
            return stolen
    raise HTTPException(409, NOT_APPROVED_MESSAGE)


async def release_post_claim(db, payment_id: str, token: str) -> None:
    if not token:
        return
    await db[PAYMENTS].update_one(
        {"id": str(payment_id), "status": STATUS_APPROVED, "post_claim": token},
        {"$unset": {"post_claim": ""}},
    )


async def cancel_payroll_payment(db, payment_id: str, user: dict, reason: Any) -> dict:
    """Cancel a draft or approved payment that has no journal. Keeps the document."""
    text = _cancel_reason(reason)
    doc = await db[PAYMENTS].find_one({"id": str(payment_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Pembayaran payroll tidak ditemukan.")
    lines = await payment_lines(db, doc["id"])
    if doc.get("status") == STATUS_CANCELLED:
        if await posted_payment_journal(db, doc["id"]):
            raise HTTPException(409, CANCEL_MESSAGE)
        return await _present(db, doc, lines)
    if doc.get("status") not in (STATUS_DRAFT, STATUS_APPROVED):
        raise HTTPException(409, CANCEL_MESSAGE)
    if await _payment_journal_reference(db, doc):
        raise HTTPException(409, CANCEL_MESSAGE)
    fresh = await db[PAYMENTS].find_one_and_update(
        {
            "id": doc["id"],
            "status": {"$in": [STATUS_DRAFT, STATUS_APPROVED]},
            "$and": [
                _blank_field_clause("accounting_journal_id"),
                _blank_field_clause("post_claim"),
            ],
        },
        {"$set": {
            "status": STATUS_CANCELLED,
            "cancelled_at": now_iso(),
            "cancelled_by": actor_snapshot(user),
            "cancellation_reason": text,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not fresh:
        current = await db[PAYMENTS].find_one({"id": doc["id"]}, {"_id": 0})
        if current and current.get("status") == STATUS_CANCELLED and not await posted_payment_journal(db, doc["id"]):
            return await _present(db, current, lines)
        raise HTTPException(409, CANCEL_MESSAGE)
    if await posted_payment_journal(db, fresh["id"]) or str(fresh.get("accounting_journal_id") or "").strip():
        restored = STATUS_APPROVED if fresh.get("approved_at") else STATUS_DRAFT
        await db[PAYMENTS].find_one_and_update(
            {"id": fresh["id"], "status": STATUS_CANCELLED},
            {"$set": {"status": restored}, "$unset": {
                "cancelled_at": "",
                "cancelled_by": "",
                "cancellation_reason": "",
            }},
        )
        raise HTTPException(409, CANCEL_MESSAGE)
    return await _present(db, fresh, lines)


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


async def pay_payroll_period(db, period_id: str, payload: dict, user: dict) -> dict:
    """Trusted-role Bayar: create → approve → post with resume semantics.

    Does not invent NONE→PAID. Reuses create/approve/post services so journals,
    stamps, and one-active-payment guards stay authoritative. Retry-safe:
    DRAFT resumes approve+post; APPROVED resumes post; PAID returns existing.
    HTTP must require create + approve + post permissions before calling.
    """
    from services.payroll_payment_posting import post_payroll_payment

    period = await get_period(db, period_id)
    if str(period.get("status") or "") != STATUS_FINAL:
        raise HTTPException(409, NOT_FINAL_MESSAGE)

    async with get_named_lock(f"payroll_payment_pay:{period['id']}"):
        active = await get_payment_for_period(db, period["id"])
        if active and str(active.get("status") or "") == STATUS_PAID:
            journal_ref = await read_payment_journal_reference(db, active["id"])
            return {
                "payment": active,
                "journal_id": journal_ref.get("journal_id") or active.get("accounting_journal_id"),
                "journal_number": journal_ref.get("journal_number") or active.get("journal_number"),
                "payment_status": STATUS_PAID,
                "already": True,
                "message": "Pembayaran payroll sudah diposting.",
            }

        if not active:
            created = await create_payroll_payment(db, period["id"], payload or {}, user)
            payment_id = str(created["id"])
        else:
            payment_id = str(active["id"])

        fresh = await db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0})
        if not fresh:
            raise HTTPException(404, "Pembayaran payroll tidak ditemukan.")

        if str(fresh.get("status") or "") == STATUS_DRAFT:
            approved = await approve_payroll_payment(db, payment_id, user)
            fresh = await db[PAYMENTS].find_one({"id": payment_id}, {"_id": 0}) or approved

        status = str(fresh.get("status") or "")
        if status == STATUS_APPROVED:
            posted = await post_payroll_payment(db, payment_id, user)
            payment = await get_payment(db, payment_id, with_lines=True)
            return {
                "payment": payment,
                "journal_id": posted.get("journal_id"),
                "journal_number": posted.get("journal_number"),
                "payment_status": STATUS_PAID,
                "already": bool(posted.get("message") and "sudah" in str(posted.get("message") or "").lower()),
                "message": posted.get("message") or "Pembayaran payroll diposting ke accounting.",
            }

        if status == STATUS_PAID:
            journal_ref = await read_payment_journal_reference(db, payment_id)
            payment = await get_payment(db, payment_id, with_lines=True)
            return {
                "payment": payment,
                "journal_id": journal_ref.get("journal_id") or payment.get("accounting_journal_id"),
                "journal_number": journal_ref.get("journal_number") or payment.get("journal_number"),
                "payment_status": STATUS_PAID,
                "already": True,
                "message": "Pembayaran payroll sudah diposting.",
            }

        raise HTTPException(409, f"Status pembayaran {status} tidak dapat dilanjutkan ke Bayar.")
