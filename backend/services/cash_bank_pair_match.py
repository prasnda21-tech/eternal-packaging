"""One bank statement line matched to one legitimate journal line.

The claim document lives in cash_bank_reconciliation_matches, the same
collection legacy RK rows already use. Active claims keep journal_line_id
set. Cancelled pair claims unset that field and keep history_journal_line_id.
There is no multi-document transaction and no process-local lock.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date as date_cls
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from services.cash_bank_master import get_cash_bank_raw
from services.cash_bank_reconciliation import (
    RECON_MATCHES,
    RECONCILIATIONS,
    ClaimFeatureNotReady,
    claim_feature_status,
    require_claim_feature,
)
from services.cash_bank_statement import (
    IMPORTS,
    LINES,
    line_matchable,
    list_ready_lines,
    statement_closing,
)
from services.general_ledger import _journal_is_legitimate, get_general_ledger
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_REVERSED,
    reversal_is_pending,
)
from services.purchase_common import actor_snapshot, now_iso

MONEY = Decimal("0.01")
MATCH_DATE_TOLERANCE_DAYS = 7


class PairValidationError(ValueError):
    pass


class PairConflictError(Exception):
    pass


async def _before_pair_claim() -> None:
    """Test seam immediately before the statement-line claim compare-and-swap."""
    return None


async def _after_statement_claim() -> None:
    """Test seam after active_claim is stored and before the pair document insert."""
    return None


async def _after_pair_insert() -> None:
    """Test seam after the pair document is stored and before the response."""
    return None


async def _after_pair_cancel_stored() -> None:
    """Test seam after the pair is cancelled and before active_claim is cleared."""
    return None


async def _before_failed_claim_release() -> None:
    """Test seam after a lost insert has seen no pair and before claim release."""
    return None


def _money(raw: Any) -> Decimal:
    return Decimal(str(raw or 0)).quantize(MONEY)


def _hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _calendar_day(value: Any) -> Optional[date_cls]:
    try:
        return date_cls.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _calendar_delta_days(bank_date: Any, journal_date: Any) -> Optional[int]:
    bank_day = _calendar_day(bank_date)
    journal_day = _calendar_day(journal_date)
    if bank_day is None or journal_day is None:
        return None
    return abs((bank_day - journal_day).days)


def _reference_relevance(reasons: List[str]) -> int:
    text = " ".join(reasons).lower()
    if "referensi muncul" in text:
        return 0
    if "referensi tidak sama" in text:
        return 1
    return 2


def book_direction(debit: Any, credit: Any) -> Tuple[str, Decimal]:
    net = _money(debit) - _money(credit)
    if net > 0:
        return "in", net
    if net < 0:
        return "out", abs(net)
    raise PairValidationError("Baris buku tidak memiliki arah")


def review_state(journal: Optional[dict]) -> str:
    if not journal or not _journal_is_legitimate(journal):
        return "perlu_tinjauan"
    if reversal_is_pending(journal) or str(journal.get("status") or "") == STATUS_REVERSED:
        return "perlu_tinjauan"
    return "sumber_sah"


def _stored_pair_line(doc: dict) -> Optional[str]:
    return doc.get("statement_line_id") or doc.get("history_statement_line_id")


async def _pair_for_key(db, cash_bank_id: str, key: str) -> Optional[dict]:
    return await db[RECON_MATCHES].find_one(
        {"cash_bank_id": str(cash_bank_id), "idempotency_key": str(key)},
        {"_id": 0},
    )


async def _adopt_active_claim(db, line_id: str, key: str, pair_id: str) -> None:
    """Point the line at the stored pair only while it still holds this key.

    A cancelled pair is not adopted. A newer claim token does not match the filter.
    """
    if not line_id or not pair_id:
        return
    await db[LINES].update_one(
        {"id": str(line_id), "active_claim": str(key)},
        {"$set": {"active_claim": str(pair_id)}, "$unset": {"claim_slot": ""}},
    )


async def _public_existing_pair(db, existing: dict) -> dict:
    journal = None
    if existing.get("journal_id"):
        journal = await db[JOURNAL_ENTRIES].find_one({"id": existing.get("journal_id")}, {"_id": 0})
    return _public_pair(existing, journal)


async def _reply_stored_pair(
    db,
    existing: dict,
    *,
    key: str,
    payload_hash: str,
    claimed_line_id: Optional[str] = None,
    release_other_line: bool = False,
) -> dict:
    """Return the stored pair. Release a claim only when it is not that pair's line."""
    if existing.get("payload_hash") != payload_hash:
        winner_line = _stored_pair_line(existing)
        if release_other_line and claimed_line_id and winner_line != str(claimed_line_id):
            await _release_statement_claim(db, claimed_line_id, key)
        raise PairConflictError("Idempotency key yang sama dipakai untuk payload berbeda")
    if existing.get("status") == "active" and existing.get("statement_line_id"):
        await _adopt_active_claim(db, existing["statement_line_id"], key, existing["id"])
    return await _public_existing_pair(db, existing)


async def _active_pair_for_line(db, statement_line_id: str) -> Optional[dict]:
    return await db[RECON_MATCHES].find_one(
        {"statement_line_id": str(statement_line_id), "status": "active", "match_kind": "pair"},
        {"_id": 0},
    )


def _business_pair(doc: Optional[dict]) -> Optional[dict]:
    if doc and doc.get("match_kind") == "pair":
        return doc
    return None


async def _recover_release_slot(db, doc: dict) -> None:
    """Drop a release slot left by a dead cleaner.

    The line update matches only this slot. A claim already moved to a pair id,
    or a newer claim that cleared the slot, is left in place.
    """
    line_id = doc.get("release_line_id")
    key = doc.get("idempotency_key")
    slot = doc.get("id")
    if line_id and key and slot:
        await db[LINES].update_one(
            {"id": str(line_id), "active_claim": str(key), "claim_slot": str(slot)},
            {"$unset": {"active_claim": "", "claim_slot": ""}},
        )
    await db[RECON_MATCHES].delete_one({"id": slot, "match_kind": "pair_release"})


async def _release_or_adopt_failed_key(
    db,
    *,
    cash_bank_id: str,
    statement_line_id: str,
    key: str,
):
    """Return the business pair that owns this key, or None after releasing it.

    The authority is the unique idempotency index, not the earlier read. This
    attempt inserts a slot with no journal or statement line. While that slot
    exists, no pair for the key can commit, so clearing this line's key cannot
    hit a pair that lands in between. If the insert loses, a pair already
    committed and this attempt must not clear it.
    """
    slot = str(uuid.uuid4())
    try:
        await db[RECON_MATCHES].insert_one({
            "id": slot,
            "match_kind": "pair_release",
            "status": "releasing",
            "cash_bank_id": str(cash_bank_id),
            "idempotency_key": str(key),
            "release_line_id": str(statement_line_id),
        })
    except DuplicateKeyError:
        return _business_pair(await _pair_for_key(db, cash_bank_id, key))
    held = False
    try:
        slotted = await db[LINES].update_one(
            {"id": str(statement_line_id), "active_claim": str(key)},
            {"$set": {"claim_slot": slot}},
        )
        held = slotted.matched_count == 1
        if held:
            await db[LINES].update_one(
                {"id": str(statement_line_id), "active_claim": str(key), "claim_slot": slot},
                {"$unset": {"active_claim": "", "claim_slot": ""}},
            )
    finally:
        await db[RECON_MATCHES].delete_one({"id": slot, "match_kind": "pair_release"})
    if held:
        return None
    return _business_pair(await _pair_for_key(db, cash_bank_id, key))


async def _finish_lost_insert(
    db,
    *,
    cash_bank_id: str,
    statement_line_id: str,
    key: str,
    payload_hash: str,
    created_claim: bool,
    exc: DuplicateKeyError,
):
    """Settle a lost insert without clearing the winner's claim.

    The pair document is read first. Release runs only when that read shows
    this key has no pair and this statement line has no active pair. The
    release filter is still ``active_claim == key``, so a token the winner
    already replaced is left alone. A claim this request merely continued is
    released only in that same no-pair case; otherwise the line would stay
    claimed after a failed insert that stored nothing.
    """
    stored = _business_pair(await _pair_for_key(db, cash_bank_id, key))
    if stored is None:
        line_pair = await _active_pair_for_line(db, statement_line_id)
        if line_pair is not None and line_pair.get("idempotency_key") == key:
            stored = line_pair
        elif line_pair is not None:
            raise PairConflictError("Baris bank atau baris buku sudah diklaim") from exc
    if stored is None:
        # The read above is not ownership. Another same-key pair can commit
        # before a bare unset. Hold the idempotency slot first, or adopt the
        # pair that won it.
        await _before_failed_claim_release()
        stored = await _release_or_adopt_failed_key(
            db,
            cash_bank_id=cash_bank_id,
            statement_line_id=statement_line_id,
            key=key,
        )
    if stored is not None:
        if stored.get("status") != "active" and created_claim:
            await _release_statement_claim(db, statement_line_id, key)
            return await _reply_stored_pair(
                db, stored, key=key, payload_hash=payload_hash,
            )
        winner_line = _stored_pair_line(stored)
        # Created and continued attempts both keep the winner line. Only a
        # claim this request created on a different line is rolled back.
        release_other = bool(created_claim and winner_line and winner_line != statement_line_id)
        return await _reply_stored_pair(
            db,
            stored,
            key=key,
            payload_hash=payload_hash,
            claimed_line_id=statement_line_id,
            release_other_line=release_other,
        )
    raise PairConflictError("Baris bank atau baris buku sudah diklaim") from exc


async def _journal_for_line(db, journal_line_id: str) -> Tuple[dict, dict]:
    line = await db[JOURNAL_LINES].find_one({"id": str(journal_line_id)}, {"_id": 0})
    if not line:
        raise PairValidationError("Baris buku tidak ditemukan")
    journal = await db[JOURNAL_ENTRIES].find_one({"id": line.get("journal_id")}, {"_id": 0})
    if not journal:
        raise PairValidationError("Jurnal baris buku tidak ditemukan")
    return line, journal


def _assert_matchable_journal(journal: dict, line: dict, *, account_id: str, start: str, end: str) -> Tuple[str, Decimal]:
    if str(line.get("account_id") or "") != account_id:
        raise PairConflictError("Baris buku bukan akun rekening yang dipilih")
    if not _journal_is_legitimate(journal):
        raise PairConflictError("Baris buku tidak masuk Buku Besar")
    if reversal_is_pending(journal) or str(journal.get("status") or "") != "posted":
        raise PairConflictError("Jurnal sumber berubah atau belum selesai. Baris tidak dicocokkan.")
    date = str(journal.get("date") or "")
    if date < start or date > end:
        raise PairValidationError("Baris buku di luar periode yang dipilih")
    return book_direction(line.get("debit"), line.get("credit"))


async def pair_workspace(
    db,
    *,
    cash_bank_id: str,
    start: str,
    end: str,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    if not start or not end or start > end:
        raise PairValidationError("Periode tidak valid")
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 50)))
    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise PairValidationError("Kas/Bank tidak ditemukan")
    account_id = str(cb.get("account_id") or "")
    gl = await get_general_ledger(
        db, account_id=account_id, start_date=start, end_date=end
    )
    bank_rows = await list_ready_lines(db, cash_bank_id=cash_bank_id, start=start, end=end)
    line_ids = [str(row.get("journal_line_id") or "") for row in gl["items"] if row.get("journal_line_id")]
    bank_ids = [str(row.get("id") or "") for row in bank_rows]
    claim_query: Dict[str, Any] = {"$or": []}
    if line_ids:
        claim_query["$or"].append({"journal_line_id": {"$in": line_ids}})
    if bank_ids:
        claim_query["$or"].append({"statement_line_id": {"$in": bank_ids}})
    claims = []
    if claim_query["$or"]:
        claims = await db[RECON_MATCHES].find(claim_query, {"_id": 0}).to_list(100000)
    by_journal = {c.get("journal_line_id"): c for c in claims if c.get("journal_line_id")}
    by_statement = {c.get("statement_line_id"): c for c in claims if c.get("statement_line_id")}
    journals = {}
    if by_journal:
        ids = list({
            c.get("journal_id") for c in by_journal.values() if c.get("journal_id")
        })
        if ids:
            found = await db[JOURNAL_ENTRIES].find({"id": {"$in": ids}}, {"_id": 0}).to_list(len(ids))
            journals = {j["id"]: j for j in found if j.get("id")}

    book = []
    for row in gl["items"]:
        lid = str(row.get("journal_line_id") or "")
        claim = by_journal.get(lid)
        try:
            direction, amount = book_direction(row.get("debit"), row.get("credit"))
        except PairValidationError:
            direction, amount = "none", Decimal("0.00")
        matchable = (
            str(row.get("status") or "") == "posted"
            and not row.get("blocker_code")
            and claim is None
            and direction in ("in", "out")
        )
        reason = ""
        if claim is not None:
            matchable = False
            reason = (
                "Diklaim pencocokan baris"
                if claim.get("match_kind") == "pair"
                else "Diklaim dokumen rekonsiliasi saldo lama"
            )
        elif row.get("blocker_code") or str(row.get("status") or "") != "posted":
            matchable = False
            reason = "Jurnal tidak eligible untuk pencocokan baru"
        book.append({
            **row,
            "direction": direction,
            "absolute_amount": float(amount),
            "matchable": matchable,
            "block_reason": reason,
            "claim_id": (claim or {}).get("id"),
            "claim_kind": (claim or {}).get("match_kind") or ("legacy" if claim else None),
        })

    account_currency = cb.get("currency_code") or None
    bank = []
    for row in bank_rows:
        claim = by_statement.get(row.get("id"))
        ok, reason = line_matchable(row, account_currency)
        if claim is not None:
            ok = False
            reason = "Sudah dicocokkan"
        bank.append({
            "id": row.get("id"),
            "import_id": row.get("import_id"),
            "date": row.get("date"),
            "description": row.get("description"),
            "reference": row.get("reference"),
            "bank_txn_id": row.get("bank_txn_id"),
            "direction": row.get("direction"),
            "amount": row.get("amount"),
            "currency_code": row.get("currency_code"),
            "duplicate_status": row.get("duplicate_status"),
            "duplicate_reason": row.get("duplicate_reason"),
            "identity_guarantee": row.get("identity_guarantee") or "tidak_dijamin",
            "running_balance": row.get("running_balance"),
            "original_date": row.get("original_date"),
            "date_source": row.get("date_source"),
            "date_status": row.get("date_status"),
            "matchable": ok,
            "block_reason": reason,
            "claim_id": (claim or {}).get("id"),
        })

    def hits(row: dict, needle: str) -> bool:
        blob = " ".join(str(row.get(k) or "") for k in (
            "description", "reference", "bank_txn_id", "journal_number",
            "transaction_number", "source_label", "date", "amount", "absolute_amount",
        )).lower()
        return needle in blob

    needle = str(q or "").strip().lower()
    visible_bank = [row for row in bank if not needle or hits(row, needle)]
    visible_book = [row for row in book if not needle or hits(row, needle)]
    unmatched_bank = [row for row in bank if not row.get("claim_id")]
    unmatched_book = [row for row in book if not row.get("claim_id")]
    start_i = (page - 1) * page_size
    closing = await statement_closing(db, cash_bank_id=cash_bank_id, end=end)
    book_ending = gl["ending_balance"]
    difference = None
    if closing.get("amount") is not None:
        difference = float(_money(closing["amount"]) - _money(book_ending))
    active_pairs = [
        c for c in claims
        if c.get("match_kind") == "pair" and c.get("status") == "active"
    ]
    feature = await claim_feature_status(db)
    return {
        "cash_bank_id": cash_bank_id,
        "cash_bank_name": cb.get("name"),
        "account_id": account_id,
        "currency_code": account_currency,
        "currency_contract": "explicit" if account_currency else "unknown",
        "start_date": start,
        "end_date": end,
        "book_opening_balance": gl["opening_balance"],
        "book_ending_balance": book_ending,
        "book_source": "general_ledger",
        "completeness_status": gl.get("completeness_status"),
        "blocker_code": gl.get("blocker_code"),
        "statement_closing": closing,
        "difference": difference,
        "balance_match_declared": False,
        "completion_status": "tidak_dinilai",
        "pair_saved_note": "Pasangan tersimpan bukan rekonsiliasi saldo selesai.",
        "period_close_note": "Penutupan periode rekonsiliasi belum disediakan.",
        "currency_note": "Mata uang rekening adalah konfigurasi operator, bukan bukti independen bank.",
        "feature_ready": feature["ready"],
        "feature_message": feature["message"],
        "unmatched_bank_count": len(unmatched_bank),
        "unmatched_book_count": len(unmatched_book),
        "bank_total": len(bank),
        "book_total": len(book),
        "bank_lines": visible_bank[start_i:start_i + page_size],
        "book_lines": visible_book[start_i:start_i + page_size],
        "visible_bank_count": len(visible_bank),
        "visible_book_count": len(visible_book),
        "page": page,
        "page_size": page_size,
        "matched_pairs": [_public_pair(c, journals.get(c.get("journal_id"))) for c in active_pairs],
        "search": needle,
    }


def _public_pair(doc: dict, journal: Optional[dict] = None) -> dict:
    claim_active = (
        doc.get("status") == "active"
        and bool(doc.get("journal_line_id"))
        and bool(doc.get("statement_line_id"))
    )
    return {
        "id": doc.get("id"),
        "match_kind": "pair",
        "status": doc.get("status"),
        "cash_bank_id": doc.get("cash_bank_id"),
        "statement_line_id": doc.get("statement_line_id"),
        "journal_line_id": doc.get("journal_line_id"),
        "history_journal_line_id": doc.get("history_journal_line_id"),
        "history_statement_line_id": doc.get("history_statement_line_id"),
        "claim_active": claim_active,
        "source_history": {
            "journal_line_id": doc.get("history_journal_line_id") or doc.get("journal_line_id"),
            "statement_line_id": doc.get("history_statement_line_id") or doc.get("statement_line_id"),
            "journal_number": doc.get("journal_number"),
            "currency_code": doc.get("currency_code"),
            "currency_note": "Snapshot mata uang saat pencocokan, bukan perubahan rekening kemudian.",
        },
        "amount": doc.get("amount"),
        "direction": doc.get("direction"),
        "currency_code": doc.get("currency_code"),
        "idempotency_key": doc.get("idempotency_key"),
        "review_state": review_state(journal) if doc.get("status") == "active" else doc.get("review_state") or "riwayat",
        "book_snapshot": {
            "label": "Snapshot saat pencocokan, bukan saldo terkini",
            "journal_number": doc.get("journal_number"),
            "date": doc.get("date"),
            "description": doc.get("description"),
            "debit": doc.get("debit"),
            "credit": doc.get("credit"),
            "line_status": doc.get("line_status"),
        },
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "cancelled_at": doc.get("cancelled_at"),
        "cancelled_by": doc.get("cancelled_by"),
        "cancel_reason": doc.get("cancel_reason"),
        "cancel_idempotency_key": doc.get("cancel_idempotency_key"),
    }


async def suggest_pairs(db, *, statement_line_id: str, start: str, end: str) -> dict:
    line = await db[LINES].find_one({"id": str(statement_line_id)}, {"_id": 0})
    if not line:
        raise PairValidationError("Baris bank tidak ditemukan")
    cb = await get_cash_bank_raw(db, line.get("cash_bank_id"))
    if not cb:
        raise PairValidationError("Kas/Bank tidak ditemukan")
    gl = await get_general_ledger(
        db, account_id=str(cb.get("account_id")), start_date=start, end_date=end
    )
    ids = [str(row.get("journal_line_id") or "") for row in gl["items"] if row.get("journal_line_id")]
    claimed = set()
    if ids:
        rows = await db[RECON_MATCHES].find(
            {"journal_line_id": {"$in": ids}},
            {"_id": 0, "journal_line_id": 1},
        ).to_list(len(ids))
        claimed = {row.get("journal_line_id") for row in rows}
    suggestions = []
    bank_amount = _money(line.get("amount"))
    bank_direction = line.get("direction")
    ref = str(line.get("reference") or line.get("bank_txn_id") or "").strip().lower()
    for row in gl["items"]:
        if str(row.get("status") or "") != "posted" or row.get("blocker_code"):
            continue
        if row.get("journal_line_id") in claimed:
            continue
        try:
            direction, amount = book_direction(row.get("debit"), row.get("credit"))
        except PairValidationError:
            continue
        if direction != bank_direction or amount != bank_amount:
            continue
        delta = _calendar_delta_days(line.get("date"), row.get("date"))
        if delta is None or delta > MATCH_DATE_TOLERANCE_DAYS:
            continue
        blob = " ".join([
            str(row.get("description") or ""),
            str(row.get("line_description") or ""),
            str(row.get("journal_number") or ""),
        ]).lower()
        reasons = ["Nominal sama", "Arah sama"]
        if ref and ref in blob:
            reasons.append("Referensi muncul di buku")
        elif ref:
            reasons.append("Referensi tidak sama")
        reasons.append(f"Beda tanggal {delta} hari")
        suggestions.append({
            "journal_line_id": row.get("journal_line_id"),
            "journal_number": row.get("journal_number"),
            "date": row.get("date"),
            "description": row.get("description"),
            "amount": float(amount),
            "direction": direction,
            "reasons": reasons,
        })
    suggestions.sort(key=lambda item: (
        _calendar_delta_days(line.get("date"), item.get("date")),
        _reference_relevance(item.get("reasons") or []),
        abs(Decimal(str(item.get("amount") or 0)) - bank_amount),
    ))
    return {
        "statement_line_id": line.get("id"),
        "ambiguous": len(suggestions) > 1,
        "applied": False,
        "suggestions": suggestions,
    }


async def create_pair(
    db,
    *,
    cash_bank_id: str,
    statement_line_id: str,
    journal_line_id: str,
    idempotency_key: str,
    start: str,
    end: str,
    user: Optional[dict],
) -> dict:
    try:
        await require_claim_feature(db)
    except ClaimFeatureNotReady as exc:
        raise PairConflictError(str(exc)) from exc
    key = str(idempotency_key or "").strip()
    if not key:
        raise PairValidationError("Idempotency key wajib")
    payload_hash = _hash(str(cash_bank_id), str(statement_line_id), str(journal_line_id))
    existing = await _pair_for_key(db, cash_bank_id, key)
    if existing and existing.get("match_kind") == "pair_release":
        await _recover_release_slot(db, existing)
        existing = await _pair_for_key(db, cash_bank_id, key)
    if existing:
        return await _reply_stored_pair(
            db, existing, key=key, payload_hash=payload_hash,
        )

    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise PairValidationError("Kas/Bank tidak ditemukan")
    account_currency = cb.get("currency_code") or None
    if not account_currency:
        raise PairConflictError(
            "Mata uang rekening belum ditetapkan. Pencocokan tidak dinyatakan valid."
        )
    statement = await db[LINES].find_one(
        {"id": str(statement_line_id)}, {"_id": 0}
    )
    if not statement or statement.get("cash_bank_id") != str(cash_bank_id):
        raise PairValidationError("Baris bank tidak ditemukan pada rekening ini")
    batch = await db[IMPORTS].find_one(
        {"id": statement.get("import_id")}, {"_id": 0, "status": 1}
    )
    if not batch or batch.get("status") != "complete":
        raise PairConflictError("Batch impor belum selesai dan tidak dapat dicocokkan")
    visible = dict(statement)
    if visible.get("active_claim") == key:
        visible["active_claim"] = None
    elif visible.get("active_claim"):
        raise PairConflictError("Baris sedang diklaim pencocokan")
    ok, reason = line_matchable(visible, account_currency)
    if not ok:
        raise PairConflictError(reason)
    if str(statement.get("date") or "") < start or str(statement.get("date") or "") > end:
        raise PairValidationError("Baris bank di luar periode yang dipilih")
    line, journal = await _journal_for_line(db, journal_line_id)
    direction, amount = _assert_matchable_journal(
        journal, line, account_id=str(cb.get("account_id")), start=start, end=end
    )
    if statement.get("direction") != direction:
        raise PairConflictError("Arah uang bank dan buku berbeda")
    if _money(statement.get("amount")) != amount:
        raise PairConflictError("Nominal bank dan buku berbeda")
    if (statement.get("currency_code") or None) != account_currency:
        raise PairConflictError("Mata uang baris bank berbeda dari rekening")

    await _before_pair_claim()
    claim = await _claim_statement_line(db, str(statement_line_id), key)
    if claim == "busy":
        raise PairConflictError("Baris bank sudah diklaim atau tidak siap dicocokkan")
    created_claim = claim == "created"
    fresh = await db[LINES].find_one(
        {"id": str(statement_line_id), "active_claim": key},
        {"_id": 0},
    )
    visible = dict(fresh or {})
    visible["active_claim"] = None
    fresh_ok, fresh_reason = line_matchable(visible, account_currency) if fresh else (False, "")
    fresh_direction_ok = bool(fresh) and fresh.get("direction") == direction
    fresh_amount_ok = bool(fresh) and _money(fresh.get("amount")) == amount
    fresh_currency_ok = bool(fresh) and (fresh.get("currency_code") or None) == account_currency
    fresh_in_period = bool(fresh) and start <= str(fresh.get("date") or "") <= end
    if not fresh or fresh.get("cash_bank_id") != str(cash_bank_id) or not (
        fresh_ok and fresh_direction_ok and fresh_amount_ok and fresh_currency_ok and fresh_in_period
    ):
        await _release_statement_claim(db, str(statement_line_id), key)
        if fresh and not fresh_in_period:
            raise PairValidationError("Baris bank di luar periode yang dipilih")
        if fresh and not fresh_direction_ok:
            raise PairConflictError("Arah uang bank dan buku berbeda")
        if fresh and not fresh_amount_ok:
            raise PairConflictError("Nominal bank dan buku berbeda")
        if fresh and not fresh_currency_ok:
            raise PairConflictError("Mata uang baris bank berbeda dari rekening")
        raise PairConflictError(fresh_reason or "Baris bank sudah diklaim atau tidak siap dicocokkan")
    statement = fresh
    await _after_statement_claim()

    doc = {
        "id": str(uuid.uuid4()),
        "match_kind": "pair",
        "status": "active",
        "cash_bank_id": str(cash_bank_id),
        "account_id": str(cb.get("account_id")),
        "statement_line_id": str(statement_line_id),
        "journal_line_id": str(journal_line_id),
        "journal_id": journal.get("id"),
        "journal_number": journal.get("number"),
        "date": journal.get("date"),
        "description": line.get("description") or journal.get("description") or "",
        "debit": line.get("debit") or 0,
        "credit": line.get("credit") or 0,
        "amount": float(amount),
        "direction": direction,
        "currency_code": account_currency,
        "currency_note": "Snapshot mata uang saat pencocokan, bukan perubahan rekening kemudian.",
        "line_status": journal.get("status"),
        "idempotency_key": key,
        "payload_hash": payload_hash,
        "created_at": now_iso(),
        "created_by": actor_snapshot(user),
    }
    try:
        await db[RECON_MATCHES].insert_one(dict(doc))
    except DuplicateKeyError as exc:
        # The reported index may be journal_line_id even when the stored
        # document also owns this idempotency key. Read the winner before any
        # release. A created claim and a continued claim follow that same gate:
        # the loser must not clear a line the stored pair still uses.
        return await _finish_lost_insert(
            db,
            cash_bank_id=str(cash_bank_id),
            statement_line_id=str(statement_line_id),
            key=key,
            payload_hash=payload_hash,
            created_claim=created_claim,
            exc=exc,
        )
    await _after_pair_insert()
    await db[LINES].update_one(
        {"id": str(statement_line_id), "active_claim": key},
        {"$set": {"active_claim": doc["id"]}, "$unset": {"claim_slot": ""}},
    )
    doc.pop("_id", None)
    fresh_journal = await db[JOURNAL_ENTRIES].find_one({"id": journal.get("id")}, {"_id": 0})
    return _public_pair(doc, fresh_journal)


async def _claim_statement_line(db, line_id: str, token: str) -> str:
    """Return created, continued, or busy.

    created: this request won None → token.
    continued: the line already holds this same token, so a crashed or
    concurrent attempt with this key may proceed.
    busy: a different token already owns the line.
    """
    from services.cash_bank_statement import DUP_CLEAR, DUP_KEPT
    result = await db[LINES].update_one(
        {
            "id": line_id,
            "duplicate_status": {"$in": [DUP_CLEAR, DUP_KEPT]},
            "active_claim": None,
        },
        {"$set": {"active_claim": token}, "$unset": {"claim_slot": ""}},
    )
    if result.matched_count == 1:
        return "created"
    current = await db[LINES].find_one({"id": line_id}, {"_id": 0, "active_claim": 1})
    if current and current.get("active_claim") == token:
        return "continued"
    return "busy"


async def _release_statement_claim(db, line_id: str, token: str) -> None:
    await db[LINES].update_one(
        {"id": line_id, "active_claim": token},
        {"$unset": {"active_claim": ""}},
    )


async def cancel_pair(
    db,
    match_id: str,
    *,
    idempotency_key: str,
    reason: str,
    user: Optional[dict],
) -> dict:
    try:
        await require_claim_feature(db)
    except ClaimFeatureNotReady as exc:
        raise PairConflictError(str(exc)) from exc
    key = str(idempotency_key or "").strip()
    why = str(reason or "").strip()
    if not key:
        raise PairValidationError("Idempotency key pembatalan wajib")
    if len(why) < 3:
        raise PairValidationError("Alasan pembatalan wajib diisi")
    payload_hash = _hash(str(match_id), why)
    doc = await db[RECON_MATCHES].find_one({"id": str(match_id), "match_kind": "pair"}, {"_id": 0})
    if not doc:
        raise KeyError("missing")
    by_key = await db[RECON_MATCHES].find_one(
        {"cash_bank_id": doc.get("cash_bank_id"), "cancel_idempotency_key": key},
        {"_id": 0},
    )
    if by_key:
        if by_key.get("cancel_payload_hash") != payload_hash or by_key.get("id") != str(match_id):
            raise PairConflictError("Kunci pembatalan yang sama dipakai untuk payload berbeda")
        await _clear_line_claim(db, by_key)
        return _public_pair(by_key)
    if doc.get("status") != "active":
        raise PairConflictError("Pencocokan sudah dibatalkan dan tidak diaktifkan kembali")
    journal_line_id = doc.get("journal_line_id")
    statement_line_id = doc.get("statement_line_id")
    result = await db[RECON_MATCHES].update_one(
        {
            "id": doc["id"],
            "match_kind": "pair",
            "status": "active",
            "journal_line_id": journal_line_id,
            "statement_line_id": statement_line_id,
        },
        {
            "$set": {
                "status": "cancelled",
                "cancel_idempotency_key": key,
                "cancel_payload_hash": payload_hash,
                "cancelled_at": now_iso(),
                "cancelled_by": actor_snapshot(user),
                "cancel_reason": why,
                "history_journal_line_id": journal_line_id,
                "history_statement_line_id": statement_line_id,
                "review_state": "riwayat",
            },
            "$unset": {"journal_line_id": "", "statement_line_id": ""},
        },
    )
    current = await db[RECON_MATCHES].find_one({"id": doc["id"]}, {"_id": 0})
    if result.matched_count != 1:
        if current and current.get("cancel_idempotency_key") == key:
            await _clear_line_claim(db, current)
            return _public_pair(current)
        raise PairConflictError("Pembatalan bentrok. Klaim pemenang tidak dilepas.")
    await _after_pair_cancel_stored()
    await _clear_line_claim(db, current)
    return _public_pair(current)


async def _clear_line_claim(db, match: dict) -> None:
    line_id = match.get("history_statement_line_id") or match.get("statement_line_id")
    if not line_id:
        return
    await db[LINES].update_one(
        {"id": line_id, "active_claim": {"$in": [match.get("id"), match.get("idempotency_key")]}},
        {"$unset": {"active_claim": "", "claim_slot": ""}},
    )


async def get_pair(db, match_id: str) -> dict:
    doc = await db[RECON_MATCHES].find_one(
        {"id": str(match_id), "match_kind": "pair"},
        {"_id": 0},
    )
    if not doc:
        raise KeyError("missing")
    journal = None
    if doc.get("journal_id"):
        journal = await db[JOURNAL_ENTRIES].find_one({"id": doc.get("journal_id")}, {"_id": 0})
    return _public_pair(doc, journal)


async def list_legacy_reconciliations(db, cash_bank_id: str) -> List[dict]:
    docs = await db[RECONCILIATIONS].find(
        {"cash_bank_id": str(cash_bank_id)},
        {"_id": 0, "id": 1, "number": 1, "status": 1, "reconciliation_date": 1, "statement_balance": 1, "book_balance": 1, "difference": 1},
    ).sort("reconciliation_date", -1).to_list(200)
    for doc in docs:
        doc["kind"] = "legacy_balance"
        doc["statement_balance_label"] = "Input manual pada dokumen saldo lama"
    return docs
