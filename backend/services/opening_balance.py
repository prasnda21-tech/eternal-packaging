"""Opening Balance (Saldo Awal) vouchers — thin draft/post/reverse over journal_core.

Does not modify journal_core. Draft lives here; posting writes journal_entries/lines.
At most one active POSTED voucher per cutoff_date (reversed does not block replacement).
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pymongo.errors import DuplicateKeyError

from services.accounting_periods import (
    PeriodLockedError,
    acquire_period_writer_claim,
    release_period_writer_claim,
)
from services.chart_of_accounts import get_account
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

COLLECTION = "opening_balance_vouchers"
PREFIX_VOUCHER = "SA"

STATUS_DRAFT = "draft"
STATUS_POSTED = "posted"
STATUS_REVERSED = "reversed"

SOURCE_OPENING_BALANCE = "opening_balance"
SOURCE_OPENING_BALANCE_REVERSAL = "opening_balance_reversal"

RECOVERY_REASON_CUTOFF = "opening_balance_cutoff_conflict"

PERM_VIEW = "opening_balance.view"
PERM_MANAGE = "opening_balance.manage"
PERM_POST = "opening_balance.post"
PERM_REVERSE = "opening_balance.reverse"


class OpeningBalanceValidationError(ValueError):
    pass


class OpeningBalanceConflictError(Exception):
    pass


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise OpeningBalanceValidationError("Nominal tidak valid") from exc


def _money_float(d: Decimal) -> float:
    return float(d)


def _norm_date(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        raise OpeningBalanceValidationError("Tanggal cut-off wajib diisi")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    raise OpeningBalanceValidationError("Format tanggal harus YYYY-MM-DD")


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("number", unique=True)
    await col.create_index("status")
    await col.create_index("cutoff_date")
    await col.create_index("created_at")
    await col.create_index(
        "posted_journal_id",
        unique=True,
        partialFilterExpression={"posted_journal_id": {"$type": "string"}},
    )
    # At most one active POSTED opening-balance voucher per cutoff_date.
    await col.create_index(
        "cutoff_date",
        unique=True,
        name="uniq_posted_cutoff_date",
        partialFilterExpression={"status": STATUS_POSTED},
    )
    await col.create_index(
        [("accounting_recovery_required", 1), ("cutoff_date", 1)],
        name="ob_accounting_recovery",
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
        "cutoff_date": doc.get("cutoff_date"),
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
        "accounting_recovery_required": bool(doc.get("accounting_recovery_required")),
        "accounting_recovery_journal_id": doc.get("accounting_recovery_journal_id") or "",
        "accounting_recovery_reason": doc.get("accounting_recovery_reason") or "",
        "accounting_recovery_at": doc.get("accounting_recovery_at"),
        "accounting_recovery_last_error": doc.get("accounting_recovery_last_error") or "",
    }
    if journal is not None:
        out["journal"] = journal
    if reversal_journal is not None:
        out["reversal_journal"] = reversal_journal
    return out


async def normalize_lines(db, raw_lines: Any, *, require_balanced: bool = True) -> List[dict]:
    if not isinstance(raw_lines, list):
        raise OpeningBalanceValidationError("Baris jurnal wajib berupa daftar")
    if len(raw_lines) < 2:
        raise OpeningBalanceValidationError("Saldo Awal minimal 2 baris")

    normalized: List[dict] = []
    for raw in raw_lines:
        account_id = str((raw or {}).get("account_id") or "").strip()
        if not account_id:
            raise OpeningBalanceValidationError("Akun Perkiraan wajib diisi pada setiap baris")
        coa = await get_account(db, account_id)
        if not coa:
            raise OpeningBalanceValidationError("Akun Perkiraan tidak ditemukan")

        debit = _money((raw or {}).get("debit") or 0)
        credit = _money((raw or {}).get("credit") or 0)
        if debit < 0 or credit < 0:
            raise OpeningBalanceValidationError("Debit/kredit tidak boleh negatif")
        if debit > 0 and credit > 0:
            raise OpeningBalanceValidationError("Satu baris tidak boleh debit dan kredit sekaligus")
        if debit == 0 and credit == 0:
            raise OpeningBalanceValidationError("Nominal baris harus lebih besar dari 0")

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
            raise OpeningBalanceValidationError("Total debit harus lebih besar dari 0")
        if total_dr != total_cr:
            raise OpeningBalanceValidationError(
                f"Saldo Awal tidak seimbang: debit {total_dr} ≠ kredit {total_cr}"
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
            {"cutoff_date": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[COLLECTION].find(query, {"_id": 0}).sort([("cutoff_date", -1), ("created_at", -1)]).to_list(2000)
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


async def _find_active_posted_for_cutoff(db, cutoff_date: str, *, exclude_id: Optional[str] = None) -> Optional[dict]:
    query: Dict[str, Any] = {"cutoff_date": cutoff_date, "status": STATUS_POSTED}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    return await db[COLLECTION].find_one(query, {"_id": 0})


async def create_draft(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    cutoff_date = _norm_date((raw or {}).get("cutoff_date") or (raw or {}).get("date"))
    description = str((raw or {}).get("description") or (raw or {}).get("notes") or "").strip()
    lines = await normalize_lines(db, (raw or {}).get("lines"), require_balanced=False)
    now = now_iso()
    actor = actor_snapshot(user)
    doc = {
        "id": str(uuid.uuid4()),
        "number": "",
        "cutoff_date": cutoff_date,
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
    async with get_named_lock("opening_balance_create"):
        async def _alloc():
            return await next_dated_seq(db, COLLECTION, "number", PREFIX_VOUCHER)
        saved = await insert_with_unique_number(db, COLLECTION, "number", doc, _alloc)
    return public_voucher(saved)


async def update_draft(db, voucher_id: str, raw: dict, *, user: Optional[dict] = None) -> dict:
    doc = await get_voucher_raw(db, voucher_id)
    if not doc:
        raise KeyError("missing")
    if doc.get("status") != STATUS_DRAFT:
        raise OpeningBalanceConflictError("Hanya draft yang dapat diubah")

    if "cutoff_date" in (raw or {}) or "date" in (raw or {}):
        cutoff_raw = (raw or {}).get("cutoff_date", (raw or {}).get("date"))
        cutoff_date = _norm_date(cutoff_raw)
    else:
        cutoff_date = _norm_date(doc.get("cutoff_date"))
    description = str(
        (raw or {}).get("description")
        if "description" in (raw or {})
        else ((raw or {}).get("notes") if "notes" in (raw or {}) else doc.get("description") or "")
    ).strip()
    raw_lines = (raw or {}).get("lines") if "lines" in (raw or {}) else doc.get("lines")
    lines = await normalize_lines(db, raw_lines, require_balanced=False)

    await db[COLLECTION].update_one(
        {"id": doc["id"], "status": STATUS_DRAFT},
        {
            "$set": {
                "cutoff_date": cutoff_date,
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
        raise OpeningBalanceConflictError("Hanya draft yang dapat dihapus")
    await db[COLLECTION].delete_one({"id": doc["id"], "status": STATUS_DRAFT})
    return public_voucher(doc)


async def _persist_accounting_recovery(
    db,
    voucher: dict,
    journal_id: str,
    *,
    last_error: str = "",
) -> None:
    await db[COLLECTION].update_one(
        {"id": voucher["id"]},
        {
            "$set": {
                "accounting_recovery_required": True,
                "accounting_recovery_journal_id": str(journal_id),
                "accounting_recovery_reason": RECOVERY_REASON_CUTOFF,
                "accounting_recovery_at": now_iso(),
                "accounting_recovery_last_error": str(last_error or ""),
                "updated_at": now_iso(),
            }
        },
    )


async def _clear_accounting_recovery(db, voucher_id: str) -> None:
    await db[COLLECTION].update_one(
        {"id": voucher_id},
        {
            "$unset": {
                "accounting_recovery_required": "",
                "accounting_recovery_journal_id": "",
                "accounting_recovery_reason": "",
                "accounting_recovery_at": "",
                "accounting_recovery_last_error": "",
            },
            "$set": {"updated_at": now_iso()},
        },
    )


async def _recover_opening_balance_orphan(db, doc: dict, *, user: Optional[dict] = None) -> dict:
    """Compensate an orphan OB journal using a new legitimate writer lease."""
    journal_id = str(doc.get("accounting_recovery_journal_id") or "").strip()
    if not journal_id:
        await _clear_accounting_recovery(db, doc["id"])
        return await get_voucher(db, doc["id"])
    cutoff_date = _norm_date(doc.get("cutoff_date"))
    claim = await acquire_period_writer_claim(db, cutoff_date)
    try:
        try:
            await reverse_journal(
                db,
                journal_id,
                source_type=SOURCE_OPENING_BALANCE_REVERSAL,
                source_id=f"{doc['id']}:orphan-cutoff",
                posted_by=user,
                description=f"Compensating reverse {doc.get('number')}",
                writer_claim_token=claim["token"],
            )
        except PeriodLockedError:
            raise
        except JournalConflictError:
            existing = await db.journal_entries.find_one({"reversal_of": journal_id}, {"_id": 1})
            if not existing:
                raise
        except KeyError:
            pass
        await _clear_accounting_recovery(db, doc["id"])
        return await get_voucher(db, doc["id"])
    except PeriodLockedError:
        raise
    except Exception as exc:
        await _persist_accounting_recovery(db, doc, journal_id, last_error=str(exc))
        raise OpeningBalanceConflictError(
            f"Kompensasi jurnal Saldo Awal gagal untuk cut-off {cutoff_date}. "
            f"Jurnal {journal_id} masih perlu recovery."
        ) from exc
    finally:
        await release_period_writer_claim(db, claim["period_key"], claim["token"])


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
    lock_key = f"opening_balance_post:{voucher_id}"
    async with get_named_lock(lock_key):
        doc = await get_voucher_raw(db, voucher_id)
        if not doc:
            raise KeyError("missing")
        if doc.get("status") == STATUS_POSTED:
            return await get_voucher(db, doc["id"])
        if doc.get("status") == STATUS_REVERSED:
            raise OpeningBalanceConflictError("Saldo Awal yang sudah dibatalkan tidak dapat diposting ulang")
        if doc.get("status") != STATUS_DRAFT:
            raise OpeningBalanceConflictError("Status tidak valid untuk posting")

        if doc.get("accounting_recovery_required"):
            try:
                return await _recover_opening_balance_orphan(db, doc, user=user)
            except PeriodLockedError as exc:
                raise OpeningBalanceConflictError(str(exc)) from exc

        cutoff_date = _norm_date(doc.get("cutoff_date"))
        async with get_named_lock(f"opening_balance_cutoff:{cutoff_date}"):
            claim = None
            try:
                claim = await acquire_period_writer_claim(db, cutoff_date)
                claim_token = claim["token"]

                competing = await _find_active_posted_for_cutoff(db, cutoff_date, exclude_id=doc["id"])
                if competing:
                    raise OpeningBalanceConflictError(
                        f"Sudah ada Saldo Awal posted untuk tanggal cut-off {cutoff_date}"
                    )

                # Re-validate lines / COA / balance at post time
                lines = await normalize_lines(db, doc.get("lines"), require_balanced=True)
                await db[COLLECTION].update_one(
                    {"id": doc["id"], "status": STATUS_DRAFT},
                    {"$set": {"lines": lines, "cutoff_date": cutoff_date, "updated_at": now_iso()}},
                )
                doc["lines"] = lines
                doc["cutoff_date"] = cutoff_date

                existing_j = await find_primary_journal_for_source(db, SOURCE_OPENING_BALANCE, doc["id"])
                if existing_j and existing_j.get("status") == "reversed":
                    return await get_voucher(db, doc["id"])
                if existing_j:
                    try:
                        result = await db[COLLECTION].update_one(
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
                    except DuplicateKeyError as exc:
                        if existing_j.get("status") == "posted" and existing_j.get("id"):
                            await _persist_accounting_recovery(db, doc, existing_j["id"])
                            try:
                                await reverse_journal(
                                    db,
                                    existing_j["id"],
                                    source_type=SOURCE_OPENING_BALANCE_REVERSAL,
                                    source_id=f"{doc['id']}:orphan-cutoff",
                                    posted_by=user,
                                    description=f"Compensating reverse {doc.get('number')}",
                                    writer_claim_token=claim_token,
                                )
                            except PeriodLockedError:
                                raise
                            except Exception as rev_exc:
                                await _persist_accounting_recovery(
                                    db, doc, existing_j["id"], last_error=str(rev_exc)
                                )
                                raise OpeningBalanceConflictError(
                                    f"Kompensasi jurnal Saldo Awal gagal untuk cut-off {cutoff_date}. "
                                    f"Jurnal {existing_j.get('id')} masih perlu recovery."
                                ) from rev_exc
                            await _clear_accounting_recovery(db, doc["id"])
                        raise OpeningBalanceConflictError(
                            f"Sudah ada Saldo Awal posted untuk tanggal cut-off {cutoff_date}"
                        ) from exc
                    if result.matched_count == 0:
                        return await get_voucher(db, doc["id"])
                    return await get_voucher(db, doc["id"])

                try:
                    journal = await post_balanced_journal(
                        db,
                        date=cutoff_date,
                        source_type=SOURCE_OPENING_BALANCE,
                        source_id=doc["id"],
                        description=doc.get("description") or f"Saldo Awal {doc.get('number')}",
                        lines=_journal_lines_from_voucher(doc),
                        posted_by=user,
                        writer_claim_token=claim_token,
                    )
                except PeriodLockedError as exc:
                    raise OpeningBalanceConflictError(str(exc)) from exc
                except JournalValidationError as exc:
                    raise OpeningBalanceValidationError(str(exc)) from exc
                except JournalConflictError as exc:
                    existing_j = await find_primary_journal_for_source(db, SOURCE_OPENING_BALANCE, doc["id"])
                    if existing_j:
                        journal = existing_j
                    else:
                        raise OpeningBalanceConflictError(str(exc)) from exc

                now = now_iso()
                try:
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
                except DuplicateKeyError as exc:
                    # Persist durable recovery BEFORE releasing writer protection.
                    await _persist_accounting_recovery(db, doc, journal["id"])
                    try:
                        await reverse_journal(
                            db,
                            journal["id"],
                            source_type=SOURCE_OPENING_BALANCE_REVERSAL,
                            source_id=f"{doc['id']}:orphan-cutoff",
                            posted_by=user,
                            description=f"Compensating reverse {doc.get('number')}",
                            writer_claim_token=claim_token,
                        )
                    except PeriodLockedError:
                        raise
                    except Exception as rev_exc:
                        await _persist_accounting_recovery(
                            db, doc, journal["id"], last_error=str(rev_exc)
                        )
                        raise OpeningBalanceConflictError(
                            f"Kompensasi jurnal Saldo Awal gagal untuk cut-off {cutoff_date}. "
                            f"Jurnal {journal.get('id')} masih perlu recovery."
                        ) from rev_exc
                    await _clear_accounting_recovery(db, doc["id"])
                    raise OpeningBalanceConflictError(
                        f"Sudah ada Saldo Awal posted untuk tanggal cut-off {cutoff_date}"
                    ) from exc
                if result.matched_count == 0:
                    return await get_voucher(db, doc["id"])
                return await get_voucher(db, doc["id"])
            except PeriodLockedError as exc:
                raise OpeningBalanceConflictError(str(exc)) from exc
            finally:
                if claim:
                    await release_period_writer_claim(db, claim["period_key"], claim["token"])


async def reverse_voucher(db, voucher_id: str, *, user: Optional[dict] = None) -> dict:
    lock_key = f"opening_balance_reverse:{voucher_id}"
    async with get_named_lock(lock_key):
        doc = await get_voucher_raw(db, voucher_id)
        if not doc:
            raise KeyError("missing")
        if doc.get("status") == STATUS_DRAFT:
            raise OpeningBalanceValidationError("Draft tidak dapat dibatalkan; hapus saja")
        if doc.get("status") == STATUS_REVERSED:
            raise OpeningBalanceConflictError("Saldo Awal sudah dibatalkan")
        if doc.get("status") != STATUS_POSTED:
            raise OpeningBalanceConflictError("Hanya Saldo Awal posted yang dapat dibatalkan")
        journal_id = doc.get("posted_journal_id")
        if not journal_id:
            raise OpeningBalanceValidationError("Saldo Awal posted tanpa journal_core")

        try:
            rev = await reverse_journal(
                db,
                journal_id,
                source_type=SOURCE_OPENING_BALANCE_REVERSAL,
                source_id=doc["id"],
                posted_by=user,
                description=f"Reversal {doc.get('number')}",
            )
        except KeyError:
            raise KeyError("missing") from None
        except PeriodLockedError as exc:
            raise OpeningBalanceConflictError(str(exc)) from exc
        except JournalValidationError as exc:
            raise OpeningBalanceValidationError(str(exc)) from exc
        except JournalConflictError as exc:
            existing = await get_voucher_raw(db, voucher_id)
            if existing and existing.get("status") == STATUS_REVERSED:
                raise OpeningBalanceConflictError("Saldo Awal sudah dibatalkan") from exc
            raise OpeningBalanceConflictError(str(exc)) from exc

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
            raise OpeningBalanceConflictError("Saldo Awal sudah dibatalkan")
        return await get_voucher(db, doc["id"])
