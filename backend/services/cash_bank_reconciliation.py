"""Kas & Bank bank reconciliation — matching metadata only (Phase 2).

Does NOT create journals, alter debit/credit, vouchers, or cash balances.
Finalizing only freezes matched movements.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Set

from pymongo.errors import DuplicateKeyError

from services.cash_bank_master import get_cash_bank_raw, require_active_cash_bank
from services.cash_bank_mutasi import (
    SOURCE_LABELS,
    _resolve_voucher_meta,
    account_balance_as_of,
)
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES
from services.purchase_common import (
    actor_snapshot,
    get_named_lock,
    next_dated_seq,
    now_iso,
)

RECONCILIATIONS = "cash_bank_reconciliations"
RECON_MATCHES = "cash_bank_reconciliation_matches"
PREFIX_RECON = "RK"

STATUS_DRAFT = "draft"
STATUS_FINALIZED = "finalized"

PERM_RECONCILE = "cash_bank.reconcile"


class ReconValidationError(ValueError):
    pass


class ReconConflictError(Exception):
    pass


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ReconValidationError("Nominal tidak valid") from exc


def _money_float(d: Decimal) -> float:
    return float(d)


def _parse_statement_balance(raw: Any) -> float:
    """Allow zero / negative statement balances (bank can be overdrawn)."""
    if raw is None or raw == "":
        raise ReconValidationError("Saldo rekening koran wajib diisi")
    try:
        d = Decimal(str(raw).strip().replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ReconValidationError("Saldo rekening koran tidak valid") from exc
    return float(d)


async def ensure_indexes(db) -> None:
    col = db[RECONCILIATIONS]
    matches = db[RECON_MATCHES]
    await col.create_index("id", unique=True)
    await col.create_index("number", unique=True)
    await col.create_index("cash_bank_id")
    await col.create_index("status")
    await col.create_index([("cash_bank_id", 1), ("reconciliation_date", -1)])
    await matches.create_index("id", unique=True)
    await matches.create_index("reconciliation_id")
    await matches.create_index("journal_id")
    # One movement actively matched in at most one reconciliation.
    await matches.create_index("journal_line_id", unique=True)


def public_recon(doc: Optional[dict], *, matches: Optional[List[dict]] = None) -> Optional[dict]:
    if not doc:
        return None
    book = _money(doc.get("book_balance"))
    stmt = _money(doc.get("statement_balance"))
    out = {
        "id": doc.get("id"),
        "number": doc.get("number"),
        "cash_bank_id": doc.get("cash_bank_id"),
        "cash_bank_name": doc.get("cash_bank_name"),
        "account_id": doc.get("account_id"),
        "reconciliation_date": doc.get("reconciliation_date"),
        "statement_balance": _money_float(stmt),
        "book_balance": _money_float(book),
        "difference": _money_float(stmt - book),
        "notes": doc.get("notes") or "",
        "status": doc.get("status"),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "finalized_at": doc.get("finalized_at"),
        "finalized_by": doc.get("finalized_by"),
    }
    if matches is not None:
        out["matches"] = matches
        out["matched_count"] = len(matches)
    return out


async def _load_matches(db, reconciliation_id: str) -> List[dict]:
    docs = await db[RECON_MATCHES].find(
        {"reconciliation_id": str(reconciliation_id)}, {"_id": 0}
    ).to_list(100000)
    docs.sort(key=lambda m: (
        str(m.get("date") or ""),
        str(m.get("created_at") or ""),
        str(m.get("journal_line_id") or ""),
    ))
    return [
        {
            "id": m.get("id"),
            "reconciliation_id": m.get("reconciliation_id"),
            "journal_id": m.get("journal_id"),
            "journal_line_id": m.get("journal_line_id"),
            "journal_number": m.get("journal_number"),
            "transaction_number": m.get("transaction_number"),
            "date": m.get("date"),
            "description": m.get("description") or "",
            "debit": m.get("debit") or 0,
            "credit": m.get("credit") or 0,
            "amount": m.get("amount") or 0,
            "source_type": m.get("source_type"),
            "source_id": m.get("source_id"),
            "source_label": m.get("source_label"),
            "status": m.get("line_status") or m.get("status"),
        }
        for m in docs
    ]


async def list_reconciliations(
    db,
    *,
    cash_bank_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[dict]:
    query: Dict[str, Any] = {}
    if cash_bank_id:
        query["cash_bank_id"] = str(cash_bank_id)
    if status:
        query["status"] = str(status)
    docs = await db[RECONCILIATIONS].find(query, {"_id": 0}).sort(
        [("reconciliation_date", -1), ("created_at", -1)]
    ).to_list(2000)
    return [public_recon(d) for d in docs]


async def get_reconciliation(db, recon_id: str, *, with_matches: bool = True) -> Optional[dict]:
    doc = await db[RECONCILIATIONS].find_one({"id": str(recon_id)}, {"_id": 0})
    if not doc:
        return None
    matches = None
    if with_matches:
        matches = await _load_matches(db, doc["id"])
    return public_recon(doc, matches=matches)


async def _matched_line_ids(
    db,
    *,
    account_id: str,
    exclude_recon_id: Optional[str] = None,
) -> Set[str]:
    """All journal_line_ids currently claimed by any reconciliation for this account."""
    query: Dict[str, Any] = {"account_id": str(account_id)}
    if exclude_recon_id:
        query["reconciliation_id"] = {"$ne": str(exclude_recon_id)}
    docs = await db[RECON_MATCHES].find(query, {"_id": 0, "journal_line_id": 1}).to_list(100000)
    return {str(d["journal_line_id"]) for d in docs if d.get("journal_line_id")}


async def list_unmatched_movements(
    db,
    *,
    cash_bank_id: str,
    through_date: str,
    reconciliation_id: Optional[str] = None,
) -> List[dict]:
    """Journal movements for cash/bank COA up to through_date not matched elsewhere.

    Includes matches already on this draft reconciliation (marked matched=True).
    GET-safe.
    """
    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise ReconValidationError("Kas/Bank tidak ditemukan")
    account_id = str(cb.get("account_id") or "").strip()
    if not account_id:
        raise ReconValidationError("Kas/Bank tidak memiliki Akun Perkiraan")
    end = str(through_date or "").strip()
    if not end:
        raise ReconValidationError("Tanggal rekonsiliasi wajib diisi")

    own_matches: Dict[str, dict] = {}
    if reconciliation_id:
        for m in await _load_matches(db, reconciliation_id):
            if m.get("journal_line_id"):
                own_matches[str(m["journal_line_id"])] = m

    claimed_elsewhere = await _matched_line_ids(
        db, account_id=account_id, exclude_recon_id=reconciliation_id
    )

    journals = await db[JOURNAL_ENTRIES].find(
        {"date": {"$lte": end}}, {"_id": 0}
    ).to_list(100000)
    by_jid = {j["id"]: j for j in journals if j.get("id")}
    if not by_jid:
        return []

    lines = await db[JOURNAL_LINES].find(
        {"account_id": account_id, "journal_id": {"$in": list(by_jid.keys())}},
        {"_id": 0},
    ).to_list(100000)

    voucher_cache: Dict[tuple, dict] = {}
    rows: List[dict] = []
    for ln in lines:
        lid = str(ln.get("id") or "")
        if not lid:
            continue
        if lid in claimed_elsewhere:
            continue
        j = by_jid.get(ln.get("journal_id") or "")
        if not j:
            continue
        st = str(j.get("source_type") or "")
        sid = str(j.get("source_id") or "")
        cache_key = (st, sid)
        if cache_key not in voucher_cache:
            voucher_cache[cache_key] = await _resolve_voucher_meta(db, st, sid)
        meta = voucher_cache[cache_key]
        debit = float(ln.get("debit") or 0)
        credit = float(ln.get("credit") or 0)
        amount = debit - credit
        matched = lid in own_matches
        rows.append({
            "journal_line_id": lid,
            "journal_id": j.get("id"),
            "journal_number": j.get("number"),
            "transaction_number": meta.get("voucher_number") or j.get("number"),
            "date": j.get("date"),
            "description": ln.get("description") or j.get("description") or "",
            "debit": debit,
            "credit": credit,
            "amount": amount,
            "source_type": st,
            "source_id": sid,
            "source_label": SOURCE_LABELS.get(st, st or "—"),
            "status": j.get("status"),
            "matched": matched,
            "created_at": j.get("created_at") or ln.get("created_at"),
        })

    rows.sort(key=lambda r: (
        str(r.get("date") or ""),
        str(r.get("created_at") or ""),
        str(r.get("journal_line_id") or ""),
    ))
    return rows


async def _refresh_book_balance(db, doc: dict) -> dict:
    account_id = doc.get("account_id")
    date = doc.get("reconciliation_date")
    book = await account_balance_as_of(db, account_id, through_date=date)
    stmt = _money(doc.get("statement_balance"))
    await db[RECONCILIATIONS].update_one(
        {"id": doc["id"]},
        {
            "$set": {
                "book_balance": _money_float(book),
                "difference": _money_float(stmt - book),
            }
        },
    )
    doc["book_balance"] = _money_float(book)
    doc["difference"] = _money_float(stmt - book)
    return doc


async def create_reconciliation(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    cash_bank_id = str((raw or {}).get("cash_bank_id") or "").strip()
    recon_date = str((raw or {}).get("reconciliation_date") or "").strip()
    notes = str((raw or {}).get("notes") or "").strip()
    if not cash_bank_id:
        raise ReconValidationError("Kas/Bank wajib dipilih")
    if not recon_date:
        raise ReconValidationError("Tanggal rekonsiliasi wajib diisi")

    cb = await require_active_cash_bank(db, cash_bank_id)
    account_id = str(cb.get("account_id") or "").strip()
    statement = _parse_statement_balance((raw or {}).get("statement_balance"))
    book = await account_balance_as_of(db, account_id, through_date=recon_date)

    now = now_iso()
    actor = actor_snapshot(user)
    recon_id = str(uuid.uuid4())
    header = {
        "id": recon_id,
        "number": "",
        "cash_bank_id": cash_bank_id,
        "cash_bank_name": cb.get("name"),
        "account_id": account_id,
        "reconciliation_date": recon_date,
        "statement_balance": statement,
        "book_balance": _money_float(book),
        "difference": float(Decimal(str(statement)) - book),
        "notes": notes,
        "status": STATUS_DRAFT,
        "created_at": now,
        "created_by": actor,
        "updated_at": now,
        "updated_by": actor,
        "finalized_at": None,
        "finalized_by": None,
    }

    saved = None
    last_err = None
    for _ in range(25):
        header["number"] = await next_dated_seq(db, RECONCILIATIONS, "number", PREFIX_RECON)
        try:
            await db[RECONCILIATIONS].insert_one(dict(header))
            header.pop("_id", None)
            saved = header
            break
        except DuplicateKeyError as exc:
            last_err = exc
            if "number" in str(exc) or "E11000" in str(exc):
                continue
            raise ReconConflictError("Rekonsiliasi duplikat") from exc
    if not saved:
        raise ReconConflictError(f"Gagal mengalokasikan nomor rekonsiliasi. {last_err}")

    match_ids = (raw or {}).get("journal_line_ids") or (raw or {}).get("matched_line_ids") or []
    if match_ids:
        await set_matches(db, saved["id"], match_ids, user=user)
        return await get_reconciliation(db, saved["id"])
    return public_recon(saved, matches=[])


async def _require_draft(db, recon_id: str) -> dict:
    doc = await db[RECONCILIATIONS].find_one({"id": str(recon_id)}, {"_id": 0})
    if not doc:
        raise KeyError("missing")
    if doc.get("status") == STATUS_FINALIZED:
        raise ReconConflictError(
            "Rekonsiliasi finalized tidak dapat diubah. "
            "Correction workflow belum tersedia di Phase 2."
        )
    if doc.get("status") != STATUS_DRAFT:
        raise ReconConflictError("Hanya rekonsiliasi draft yang dapat diubah")
    return doc


async def update_draft_reconciliation(
    db, recon_id: str, raw: dict, *, user: Optional[dict] = None
) -> dict:
    doc = await _require_draft(db, recon_id)
    updates: Dict[str, Any] = {}

    if "reconciliation_date" in (raw or {}):
        d = str((raw or {}).get("reconciliation_date") or "").strip()
        if not d:
            raise ReconValidationError("Tanggal rekonsiliasi wajib diisi")
        updates["reconciliation_date"] = d

    if "statement_balance" in (raw or {}):
        updates["statement_balance"] = _parse_statement_balance(
            (raw or {}).get("statement_balance")
        )

    if "notes" in (raw or {}):
        updates["notes"] = str((raw or {}).get("notes") or "").strip()

    if "cash_bank_id" in (raw or {}):
        new_cb = str((raw or {}).get("cash_bank_id") or "").strip()
        if new_cb and new_cb != doc.get("cash_bank_id"):
            # Changing bank clears matches (different COA movements).
            cb = await require_active_cash_bank(db, new_cb)
            updates["cash_bank_id"] = new_cb
            updates["cash_bank_name"] = cb.get("name")
            updates["account_id"] = cb.get("account_id")
            await db[RECON_MATCHES].delete_many({"reconciliation_id": doc["id"]})

    if updates:
        updates["updated_at"] = now_iso()
        updates["updated_by"] = actor_snapshot(user)
        await db[RECONCILIATIONS].update_one({"id": doc["id"]}, {"$set": updates})

    refreshed = await db[RECONCILIATIONS].find_one({"id": doc["id"]}, {"_id": 0})
    await _refresh_book_balance(db, refreshed)

    if "journal_line_ids" in (raw or {}) or "matched_line_ids" in (raw or {}):
        ids = (raw or {}).get("journal_line_ids")
        if ids is None:
            ids = (raw or {}).get("matched_line_ids") or []
        await set_matches(db, doc["id"], ids, user=user)

    return await get_reconciliation(db, doc["id"])


async def set_matches(
    db,
    recon_id: str,
    journal_line_ids: Sequence[str],
    *,
    user: Optional[dict] = None,
) -> dict:
    """Replace matches for a draft reconciliation with exact journal_line_ids."""
    doc = await _require_draft(db, recon_id)
    account_id = str(doc.get("account_id") or "")
    through = str(doc.get("reconciliation_date") or "")
    wanted = [str(x).strip() for x in (journal_line_ids or []) if str(x).strip()]
    wanted_set = set(wanted)

    # Validate lines exist for this account / date
    if wanted:
        lines = await db[JOURNAL_LINES].find(
            {"id": {"$in": wanted}, "account_id": account_id},
            {"_id": 0},
        ).to_list(100000)
        found = {ln["id"]: ln for ln in lines if ln.get("id")}
        if len(found) != len(wanted_set):
            raise ReconValidationError(
                "Satu atau lebih movement tidak valid untuk Kas/Bank ini"
            )
        journals = await db[JOURNAL_ENTRIES].find(
            {"id": {"$in": [ln["journal_id"] for ln in found.values()]}},
            {"_id": 0},
        ).to_list(100000)
        by_jid = {j["id"]: j for j in journals}
        for lid, ln in found.items():
            j = by_jid.get(ln.get("journal_id"))
            if not j:
                raise ReconValidationError("Jurnal untuk movement tidak ditemukan")
            if str(j.get("date") or "") > through:
                raise ReconValidationError(
                    "Movement setelah tanggal rekonsiliasi tidak dapat dicocokkan"
                )
    else:
        found = {}
        by_jid = {}

    async with get_named_lock(f"cash_bank_recon_match:{doc['id']}"):
        # Re-check draft under lock
        await _require_draft(db, doc["id"])
        # Remove current matches for this recon
        await db[RECON_MATCHES].delete_many({"reconciliation_id": doc["id"]})

        now = now_iso()
        actor = actor_snapshot(user)
        voucher_cache: Dict[tuple, dict] = {}
        for lid in wanted:
            ln = found[lid]
            j = by_jid[ln["journal_id"]]
            st = str(j.get("source_type") or "")
            sid = str(j.get("source_id") or "")
            cache_key = (st, sid)
            if cache_key not in voucher_cache:
                voucher_cache[cache_key] = await _resolve_voucher_meta(db, st, sid)
            meta = voucher_cache[cache_key]
            debit = float(ln.get("debit") or 0)
            credit = float(ln.get("credit") or 0)
            match_doc = {
                "id": str(uuid.uuid4()),
                "reconciliation_id": doc["id"],
                "account_id": account_id,
                "journal_id": j["id"],
                "journal_line_id": lid,
                "journal_number": j.get("number"),
                "transaction_number": meta.get("voucher_number") or j.get("number"),
                "date": j.get("date"),
                "description": ln.get("description") or j.get("description") or "",
                "debit": debit,
                "credit": credit,
                "amount": debit - credit,
                "source_type": st,
                "source_id": sid,
                "source_label": SOURCE_LABELS.get(st, st or "—"),
                "line_status": j.get("status"),
                "created_at": now,
                "created_by": actor,
            }
            try:
                await db[RECON_MATCHES].insert_one(match_doc)
            except DuplicateKeyError as exc:
                # Cleanup partial inserts for this recon then fail
                await db[RECON_MATCHES].delete_many({"reconciliation_id": doc["id"]})
                raise ReconConflictError(
                    "Movement sudah dicocokkan pada rekonsiliasi lain"
                ) from exc

        await db[RECONCILIATIONS].update_one(
            {"id": doc["id"]},
            {"$set": {"updated_at": now, "updated_by": actor}},
        )

    return await get_reconciliation(db, doc["id"])


async def delete_draft_reconciliation(db, recon_id: str) -> bool:
    doc = await _require_draft(db, recon_id)
    await db[RECON_MATCHES].delete_many({"reconciliation_id": doc["id"]})
    result = await db[RECONCILIATIONS].delete_one({
        "id": doc["id"],
        "status": STATUS_DRAFT,
    })
    if result.deleted_count == 0:
        raise ReconConflictError(
            "Rekonsiliasi finalized tidak dapat dihapus. "
            "Correction workflow belum tersedia di Phase 2."
        )
    return True


async def finalize_reconciliation(
    db, recon_id: str, *, user: Optional[dict] = None
) -> dict:
    """Freeze matching. Idempotent if already finalized. Creates NO journal."""
    rid = str(recon_id or "").strip()
    async with get_named_lock(f"cash_bank_recon_finalize:{rid}"):
        doc = await db[RECONCILIATIONS].find_one({"id": rid}, {"_id": 0})
        if not doc:
            raise KeyError("missing")
        if doc.get("status") == STATUS_FINALIZED:
            return await get_reconciliation(db, rid)

        if doc.get("status") != STATUS_DRAFT:
            raise ReconConflictError("Hanya rekonsiliasi draft yang dapat difinalisasi")

        # Refresh book balance at finalize time (still from journals only)
        await _refresh_book_balance(db, doc)

        # Ensure all matches still unique / present
        matches = await db[RECON_MATCHES].find(
            {"reconciliation_id": rid}, {"_id": 0, "journal_line_id": 1}
        ).to_list(100000)
        line_ids = [m["journal_line_id"] for m in matches if m.get("journal_line_id")]
        if line_ids:
            # Detect any conflict: another recon claiming same line
            conflicts = await db[RECON_MATCHES].find(
                {
                    "journal_line_id": {"$in": line_ids},
                    "reconciliation_id": {"$ne": rid},
                },
                {"_id": 0, "journal_line_id": 1},
            ).to_list(10)
            if conflicts:
                raise ReconConflictError(
                    "Movement sudah dicocokkan pada rekonsiliasi lain"
                )

        now = now_iso()
        actor = actor_snapshot(user)
        result = await db[RECONCILIATIONS].update_one(
            {"id": rid, "status": STATUS_DRAFT},
            {
                "$set": {
                    "status": STATUS_FINALIZED,
                    "finalized_at": now,
                    "finalized_by": actor,
                    "updated_at": now,
                    "updated_by": actor,
                    "book_balance": doc.get("book_balance"),
                    "difference": doc.get("difference"),
                }
            },
        )
        if result.matched_count == 0:
            # Concurrent finalize won
            current = await get_reconciliation(db, rid)
            if current and current.get("status") == STATUS_FINALIZED:
                return current
            raise ReconConflictError("Gagal memfinalisasi rekonsiliasi")
        return await get_reconciliation(db, rid)


async def get_reconciliation_workspace(
    db,
    *,
    cash_bank_id: str,
    reconciliation_date: str,
    reconciliation_id: Optional[str] = None,
    statement_balance: Optional[Any] = None,
) -> dict:
    """Preview workspace for UI — GET zero-write, no auto-create."""
    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise ReconValidationError("Kas/Bank tidak ditemukan")
    account_id = str(cb.get("account_id") or "")
    date = str(reconciliation_date or "").strip()
    if not date:
        raise ReconValidationError("Tanggal rekonsiliasi wajib diisi")

    book = await account_balance_as_of(db, account_id, through_date=date)
    stmt_val = None
    if statement_balance is not None and statement_balance != "":
        stmt_val = _parse_statement_balance(statement_balance)
    elif reconciliation_id:
        existing = await db[RECONCILIATIONS].find_one(
            {"id": str(reconciliation_id)}, {"_id": 0, "statement_balance": 1}
        )
        if existing is not None:
            stmt_val = float(existing.get("statement_balance") or 0)

    movements = await list_unmatched_movements(
        db,
        cash_bank_id=cash_bank_id,
        through_date=date,
        reconciliation_id=reconciliation_id,
    )
    diff = None
    if stmt_val is not None:
        diff = float(Decimal(str(stmt_val)) - book)

    return {
        "cash_bank_id": cash_bank_id,
        "cash_bank_name": cb.get("name"),
        "account_id": account_id,
        "reconciliation_date": date,
        "book_balance": _money_float(book),
        "statement_balance": stmt_val,
        "difference": diff,
        "movements": movements,
        "total_movements": len(movements),
        "matched_count": sum(1 for m in movements if m.get("matched")),
    }
