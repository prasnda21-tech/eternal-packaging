"""General Journal vouchers — thin draft/post/reverse layer over journal_core.

Does not modify journal_core. Draft lives here; posting writes journal_entries/lines.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pymongo.errors import DuplicateKeyError

from services.chart_of_accounts import get_account
from services.accounting_periods import PeriodLockedError
from services.journal_core import (
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
)

COLLECTION = "general_journal_vouchers"
PREFIX_VOUCHER = "JU"

STATUS_DRAFT = "draft"
STATUS_POSTED = "posted"
STATUS_REVERSED = "reversed"

SOURCE_GENERAL_JOURNAL = "general_journal"
SOURCE_GENERAL_JOURNAL_REVERSAL = "general_journal_reversal"

PERM_VIEW = "general_journal.view"
PERM_MANAGE = "general_journal.manage"
PERM_POST = "general_journal.post"
PERM_REVERSE = "general_journal.reverse"


class GeneralJournalValidationError(ValueError):
    pass


class GeneralJournalConflictError(Exception):
    pass


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise GeneralJournalValidationError("Nominal tidak valid") from exc


def _money_float(d: Decimal) -> float:
    return float(d)


def _norm_date(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        raise GeneralJournalValidationError("Tanggal wajib diisi")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    raise GeneralJournalValidationError("Format tanggal harus YYYY-MM-DD")


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("number", unique=True)
    await col.create_index("status")
    await col.create_index("date")
    await col.create_index("created_at")
    await col.create_index(
        "posted_journal_id",
        unique=True,
        partialFilterExpression={"posted_journal_id": {"$type": "string"}},
    )


def _line_totals(lines: Sequence[dict]) -> Tuple[Decimal, Decimal]:
    total_dr = Decimal("0.00")
    total_cr = Decimal("0.00")
    for ln in lines or []:
        total_dr += _money(ln.get("debit"))
        total_cr += _money(ln.get("credit"))
    return total_dr, total_cr


def public_voucher(doc: Optional[dict], *, journal: Optional[dict] = None, reversal_journal: Optional[dict] = None) -> Optional[dict]:
    if not doc:
        return None
    lines = []
    for ln in doc.get("lines") or []:
        lines.append({
            "id": ln.get("id"),
            "account_id": ln.get("account_id"),
            "account_code": ln.get("account_code"),
            "account_name": ln.get("account_name"),
            "account_type": ln.get("account_type"),
            "description": ln.get("description") or "",
            "debit": ln.get("debit") or 0,
            "credit": ln.get("credit") or 0,
        })
    total_dr, total_cr = _line_totals(lines)
    out = {
        "id": doc.get("id"),
        "number": doc.get("number"),
        "date": doc.get("date"),
        "description": doc.get("description") or "",
        "status": doc.get("status"),
        "lines": lines,
        "total_debit": _money_float(total_dr),
        "total_credit": _money_float(total_cr),
        "posted_journal_id": doc.get("posted_journal_id"),
        "reversal_journal_id": doc.get("reversal_journal_id"),
        "journal_number": (journal or {}).get("number") if journal else doc.get("journal_number"),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "posted_at": doc.get("posted_at"),
        "posted_by": doc.get("posted_by"),
        "reversed_at": doc.get("reversed_at"),
        "reversed_by": doc.get("reversed_by"),
    }
    if journal is not None:
        out["journal"] = journal
    if reversal_journal is not None:
        out["reversal_journal"] = reversal_journal
    return out


async def normalize_lines(db, raw_lines: Any, *, require_balanced: bool = True) -> List[dict]:
    if not isinstance(raw_lines, list):
        raise GeneralJournalValidationError("Baris jurnal wajib berupa daftar")
    if len(raw_lines) < 2:
        raise GeneralJournalValidationError("Jurnal minimal 2 baris")

    normalized: List[dict] = []
    for raw in raw_lines:
        account_id = str((raw or {}).get("account_id") or "").strip()
        if not account_id:
            raise GeneralJournalValidationError("Akun Perkiraan wajib diisi pada setiap baris")
        coa = await get_account(db, account_id)
        if not coa:
            raise GeneralJournalValidationError("Akun Perkiraan tidak ditemukan")

        debit = _money((raw or {}).get("debit") or 0)
        credit = _money((raw or {}).get("credit") or 0)
        if debit < 0 or credit < 0:
            raise GeneralJournalValidationError("Debit/kredit tidak boleh negatif")
        if debit > 0 and credit > 0:
            raise GeneralJournalValidationError("Satu baris tidak boleh debit dan kredit sekaligus")
        if debit == 0 and credit == 0:
            raise GeneralJournalValidationError("Nominal baris harus lebih besar dari 0")

        line_id = str((raw or {}).get("id") or "").strip() or str(uuid.uuid4())
        normalized.append({
            "id": line_id,
            "account_id": account_id,
            "account_code": coa.get("code"),
            "account_name": coa.get("name"),
            "account_type": coa.get("account_type"),
            "description": str((raw or {}).get("description") or "").strip(),
            "debit": _money_float(debit),
            "credit": _money_float(credit),
        })

    if require_balanced:
        total_dr, total_cr = _line_totals(normalized)
        if total_dr <= 0:
            raise GeneralJournalValidationError("Total debit harus lebih besar dari 0")
        if total_dr != total_cr:
            raise GeneralJournalValidationError(
                f"Jurnal tidak seimbang: debit {total_dr} ≠ kredit {total_cr}"
            )
    return normalized


async def list_vouchers(
    db,
    *,
    q: Optional[str] = None,
    status: Optional[str] = None,
) -> List[dict]:
    query: Dict[str, Any] = {}
    st = str(status or "").strip().lower()
    if st in (STATUS_DRAFT, STATUS_POSTED, STATUS_REVERSED):
        query["status"] = st
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"number": {"$regex": needle, "$options": "i"}},
            {"description": {"$regex": needle, "$options": "i"}},
            {"journal_number": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort([("date", -1), ("created_at", -1)]).to_list(2000)
    return [public_voucher(d) for d in docs]


async def get_voucher_raw(db, voucher_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one({"id": str(voucher_id)}, {"_id": 0})


async def get_voucher(db, voucher_id: str) -> Optional[dict]:
    doc = await get_voucher_raw(db, voucher_id)
    if not doc:
        return None
    journal = None
    reversal = None
    if doc.get("posted_journal_id"):
        journal = await get_journal(db, doc["posted_journal_id"], with_lines=True)
    if doc.get("reversal_journal_id"):
        reversal = await get_journal(db, doc["reversal_journal_id"], with_lines=True)
    return public_voucher(doc, journal=journal, reversal_journal=reversal)


async def create_draft(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    date = _norm_date((raw or {}).get("date"))
    description = str((raw or {}).get("description") or "").strip()
    lines = await normalize_lines(db, (raw or {}).get("lines"), require_balanced=False)
    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": str(uuid.uuid4()),
        "number": "",
        "date": date,
        "description": description,
        "status": STATUS_DRAFT,
        "lines": lines,
        "posted_journal_id": None,
        "reversal_journal_id": None,
        "journal_number": None,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
        "posted_at": None,
        "posted_by": None,
        "reversed_at": None,
        "reversed_by": None,
    }
    async with get_named_lock("general_journal_create"):
        async def _alloc():
            return await next_dated_seq(db, COLLECTION, "number", PREFIX_VOUCHER)
        saved = await insert_with_unique_number(db, COLLECTION, "number", doc, _alloc)
    return public_voucher(saved)


async def update_draft(db, voucher_id: str, raw: dict, *, user: Optional[dict] = None) -> dict:
    doc = await get_voucher_raw(db, voucher_id)
    if not doc:
        raise KeyError("missing")
    if doc.get("status") != STATUS_DRAFT:
        raise GeneralJournalConflictError("Hanya draft yang dapat diubah")

    date = _norm_date((raw or {}).get("date") if "date" in (raw or {}) else doc.get("date"))
    description = str(
        (raw or {}).get("description") if "description" in (raw or {}) else doc.get("description") or ""
    ).strip()
    raw_lines = (raw or {}).get("lines") if "lines" in (raw or {}) else doc.get("lines")
    lines = await normalize_lines(db, raw_lines, require_balanced=False)

    await db[COLLECTION].update_one(
        {"id": doc["id"], "status": STATUS_DRAFT},
        {
            "$set": {
                "date": date,
                "description": description,
                "lines": lines,
                "updated_at": now_iso(),
                "updated_by": actor_snapshot(user),
            }
        },
    )
    return await get_voucher(db, doc["id"])


async def delete_draft(db, voucher_id: str) -> dict:
    doc = await get_voucher_raw(db, voucher_id)
    if not doc:
        raise KeyError("missing")
    if doc.get("status") != STATUS_DRAFT:
        raise GeneralJournalConflictError("Hanya draft yang dapat dihapus")
    await db[COLLECTION].delete_one({"id": doc["id"], "status": STATUS_DRAFT})
    return public_voucher(doc)


def _journal_lines_from_voucher(doc: dict) -> List[dict]:
    return [
        {
            "account_id": ln["account_id"],
            "debit": ln.get("debit") or 0,
            "credit": ln.get("credit") or 0,
            "description": ln.get("description") or None,
        }
        for ln in (doc.get("lines") or [])
    ]


async def post_voucher(db, voucher_id: str, *, user: Optional[dict] = None) -> dict:
    lock_key = f"general_journal_post:{voucher_id}"
    async with get_named_lock(lock_key):
        doc = await get_voucher_raw(db, voucher_id)
        if not doc:
            raise KeyError("missing")
        if doc.get("status") == STATUS_POSTED:
            return await get_voucher(db, doc["id"])
        if doc.get("status") == STATUS_REVERSED:
            raise GeneralJournalConflictError("Jurnal yang sudah dibatalkan tidak dapat diposting ulang")
        if doc.get("status") != STATUS_DRAFT:
            raise GeneralJournalConflictError("Status tidak valid untuk posting")

        # Re-validate lines / COA / balance at post time
        lines = await normalize_lines(db, doc.get("lines"), require_balanced=True)
        await db[COLLECTION].update_one(
            {"id": doc["id"], "status": STATUS_DRAFT},
            {"$set": {"lines": lines, "updated_at": now_iso()}},
        )
        doc["lines"] = lines

        existing_j = await find_primary_journal_for_source(db, SOURCE_GENERAL_JOURNAL, doc["id"])
        if existing_j:
            await db[COLLECTION].update_one(
                {"id": doc["id"], "status": STATUS_DRAFT},
                {
                    "$set": {
                        "status": STATUS_POSTED,
                        "posted_journal_id": existing_j["id"],
                        "journal_number": existing_j.get("number"),
                        "posted_at": existing_j.get("posted_at") or now_iso(),
                        "posted_by": actor_snapshot(user),
                        "updated_at": now_iso(),
                        "updated_by": actor_snapshot(user),
                    }
                },
            )
            return await get_voucher(db, doc["id"])

        try:
            journal = await post_balanced_journal(
                db,
                date=doc.get("date") or "",
                source_type=SOURCE_GENERAL_JOURNAL,
                source_id=doc["id"],
                description=doc.get("description") or f"Jurnal Umum {doc.get('number')}",
                lines=_journal_lines_from_voucher(doc),
                posted_by=user,
            )
        except PeriodLockedError as exc:
            raise GeneralJournalConflictError(str(exc)) from exc
        except JournalValidationError as exc:
            raise GeneralJournalValidationError(str(exc)) from exc
        except JournalConflictError as exc:
            existing_j = await find_primary_journal_for_source(db, SOURCE_GENERAL_JOURNAL, doc["id"])
            if existing_j:
                journal = existing_j
            else:
                raise GeneralJournalConflictError(str(exc)) from exc

        now = now_iso()
        result = await db[COLLECTION].update_one(
            {"id": doc["id"], "status": STATUS_DRAFT},
            {
                "$set": {
                    "status": STATUS_POSTED,
                    "posted_journal_id": journal["id"],
                    "journal_number": journal.get("number"),
                    "posted_at": now,
                    "posted_by": actor_snapshot(user),
                    "updated_at": now,
                    "updated_by": actor_snapshot(user),
                }
            },
        )
        if result.matched_count == 0:
            return await get_voucher(db, doc["id"])
        return await get_voucher(db, doc["id"])


async def reverse_voucher(db, voucher_id: str, *, user: Optional[dict] = None) -> dict:
    lock_key = f"general_journal_reverse:{voucher_id}"
    async with get_named_lock(lock_key):
        doc = await get_voucher_raw(db, voucher_id)
        if not doc:
            raise KeyError("missing")
        if doc.get("status") == STATUS_DRAFT:
            raise GeneralJournalValidationError("Draft tidak dapat dibatalkan; hapus saja")
        if doc.get("status") == STATUS_REVERSED:
            raise GeneralJournalConflictError("Jurnal sudah dibatalkan")
        if doc.get("status") != STATUS_POSTED:
            raise GeneralJournalConflictError("Hanya jurnal posted yang dapat dibatalkan")
        journal_id = doc.get("posted_journal_id")
        if not journal_id:
            raise GeneralJournalValidationError("Jurnal posted tanpa journal_core")

        try:
            rev = await reverse_journal(
                db,
                journal_id,
                source_type=SOURCE_GENERAL_JOURNAL_REVERSAL,
                source_id=doc["id"],
                posted_by=user,
                description=f"Reversal {doc.get('number')}",
            )
        except KeyError:
            raise KeyError("missing") from None
        except PeriodLockedError as exc:
            raise GeneralJournalConflictError(str(exc)) from exc
        except JournalValidationError as exc:
            raise GeneralJournalValidationError(str(exc)) from exc
        except JournalConflictError as exc:
            existing = await get_voucher_raw(db, voucher_id)
            if existing and existing.get("status") == STATUS_REVERSED:
                raise GeneralJournalConflictError("Jurnal sudah dibatalkan") from exc
            raise GeneralJournalConflictError(str(exc)) from exc

        now = now_iso()
        result = await db[COLLECTION].update_one(
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
            raise GeneralJournalConflictError("Jurnal sudah dibatalkan")
        return await get_voucher(db, doc["id"])
