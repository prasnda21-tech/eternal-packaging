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


class ProcessInterrupt(BaseException):
    """Test-only abrupt stop. Not a subclass of Exception, so create/cancel
    Exception cleanup handlers do not run — approximates process death.
    """


async def _before_pair_claim() -> None:
    """Test seam immediately before the statement-line claim compare-and-swap."""
    return None


async def _after_statement_claim() -> None:
    """Test seam after active_claim is stored and before the pair document insert."""
    return None


async def _after_pair_insert() -> None:
    """Test seam after the pair/group document is stored and before members/adopt."""
    return None


async def _after_group_member_insert() -> None:
    """Test seam after one pair_group_member document is inserted."""
    return None


async def _after_group_member_cancel() -> None:
    """Test seam after one pair_group_member is cancelled during group cancel."""
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
    """Point the line at the stored pair only while it still holds this match's token.

    Accepts the create idempotency key or the pair id (already adopted). A
    cancelled pair is not adopted. Foreign tokens and import-hold are left alone.
    """
    if not line_id or not pair_id:
        return
    tokens = [t for t in (str(key or ""), str(pair_id)) if t]
    if not tokens:
        return
    await db[LINES].update_one(
        {"id": str(line_id), "active_claim": {"$in": tokens}},
        {"$set": {"active_claim": str(pair_id)}, "$unset": {"claim_slot": ""}},
    )


async def _active_match_by_payload(
    db,
    *,
    cash_bank_id: str,
    payload_hash: str,
    match_kind: str,
) -> Optional[dict]:
    """Return an active pair/group for this exact business payload, if any."""
    return await db[RECON_MATCHES].find_one(
        {
            "cash_bank_id": str(cash_bank_id),
            "payload_hash": str(payload_hash),
            "match_kind": str(match_kind),
            "status": "active",
        },
        {"_id": 0},
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
        adopt_key = str(existing.get("idempotency_key") or key)
        await _adopt_active_claim(db, existing["statement_line_id"], adopt_key, existing["id"])
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
    # Prefer active pair/group claims; legacy RK rows also set journal_line_id.
    by_journal = {}
    for c in claims:
        jid = c.get("journal_line_id")
        if not jid or c.get("match_kind") in ("pair_group_member", "pair_release"):
            continue
        prev = by_journal.get(jid)
        if prev is None or (c.get("status") == "active" and prev.get("status") != "active"):
            by_journal[jid] = c
    by_statement = {}
    for c in claims:
        sid = c.get("statement_line_id")
        if not sid or c.get("match_kind") == "pair_release":
            continue
        # pair, pair_group_member, or legacy (missing/other match_kind with statement_line_id)
        if c.get("match_kind") in ("pair_group",):
            continue
        prev = by_statement.get(sid)
        if prev is None or (c.get("status") == "active" and prev.get("status") != "active"):
            by_statement[sid] = c
    journals = {}
    journal_ids = list({
        c.get("journal_id") for c in claims
        if c.get("journal_id") and c.get("match_kind") in ("pair", "pair_group")
    })
    if journal_ids:
        found = await db[JOURNAL_ENTRIES].find({"id": {"$in": journal_ids}}, {"_id": 0}).to_list(len(journal_ids))
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
            kind = claim.get("match_kind")
            if kind == "pair":
                reason = "Diklaim pencocokan baris"
            elif kind == "pair_group":
                reason = "Diklaim pencocokan grup 1:N"
            else:
                reason = "Diklaim dokumen rekonsiliasi saldo lama"
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
        active_claim = row.get("active_claim") or None
        ok, reason = line_matchable(row, account_currency)
        if claim is not None:
            ok = False
            reason = "Sudah dicocokkan"
        elif active_claim:
            ok = False
            reason = reason or "Baris sedang diklaim pencocokan"
        claim_id = None
        if claim and claim.get("match_kind") != "pair_group_member":
            claim_id = (claim or {}).get("id")
        elif claim:
            claim_id = (claim or {}).get("group_id") or (claim or {}).get("id")
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
            "active_claim": active_claim,
            "claim_in_flight": bool(active_claim) and claim is None,
            "claim_id": claim_id,
            "claim_kind": (claim or {}).get("match_kind"),
            "group_id": (claim or {}).get("group_id") if (claim or {}).get("match_kind") == "pair_group_member" else (
                (claim or {}).get("id") if (claim or {}).get("match_kind") == "pair_group" else None
            ),
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
    # In-flight CAS holds (active_claim without match doc) are not unmatched-eligible.
    unmatched_bank = [
        row for row in bank
        if not row.get("claim_id") and not row.get("active_claim")
    ]
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
    active_groups = [
        c for c in claims
        if c.get("match_kind") == "pair_group" and c.get("status") == "active"
    ]
    # Load member snapshots for groups whose header was found via journal claim.
    group_ids = [str(g.get("id")) for g in active_groups if g.get("id")]
    members_by_group: Dict[str, List[dict]] = {gid: [] for gid in group_ids}
    if group_ids:
        member_rows = await db[RECON_MATCHES].find(
            {"match_kind": "pair_group_member", "group_id": {"$in": group_ids}},
            {"_id": 0},
        ).to_list(100000)
        for member in member_rows:
            members_by_group.setdefault(str(member.get("group_id")), []).append(member)
    feature = await claim_feature_status(db)
    matched = [_public_pair(c, journals.get(c.get("journal_id"))) for c in active_pairs]
    matched.extend(
        _public_group(g, journals.get(g.get("journal_id")), members_by_group.get(str(g.get("id")), []))
        for g in active_groups
    )
    matched.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
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
        "matched_pairs": matched,
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
    # Same business payload already durable (e.g. unadopted claim after crash):
    # repair via create mutation without requiring the original idempotency key.
    by_payload = await _active_match_by_payload(
        db,
        cash_bank_id=str(cash_bank_id),
        payload_hash=payload_hash,
        match_kind="pair",
    )
    if by_payload:
        return await _reply_stored_pair(
            db,
            by_payload,
            key=str(by_payload.get("idempotency_key") or key),
            payload_hash=payload_hash,
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
        # Crashed cancel can leave an owned claim; clear only matching tokens.
        await _clear_line_claim(db, doc)
        return _public_pair(doc)
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
        if current and current.get("status") != "active":
            await _clear_line_claim(db, current)
            return _public_pair(current)
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


def _public_group(
    doc: dict,
    journal: Optional[dict] = None,
    members: Optional[List[dict]] = None,
) -> dict:
    member_rows = members or []
    active_members = [
        m for m in member_rows
        if m.get("status") == "active" and m.get("statement_line_id")
    ]
    history_members = [
        {
            "statement_line_id": m.get("statement_line_id") or m.get("history_statement_line_id"),
            "amount": m.get("amount"),
            "direction": m.get("direction"),
            "date": m.get("date"),
            "description": m.get("description"),
            "reference": m.get("reference"),
            "status": m.get("status"),
        }
        for m in member_rows
    ]
    expected_ids = [
        str(sid) for sid in (doc.get("statement_line_ids") or []) if sid
    ]
    active_ids = {
        str(m.get("statement_line_id"))
        for m in active_members
        if m.get("statement_line_id")
    }
    members_complete = (
        doc.get("status") == "active"
        and bool(expected_ids)
        and len(active_members) >= 2
        and len(active_ids) == len(expected_ids)
        and active_ids == set(expected_ids)
    )
    claim_active = (
        doc.get("status") == "active"
        and bool(doc.get("journal_line_id"))
        and members_complete
    )
    return {
        "id": doc.get("id"),
        "match_kind": "pair_group",
        "status": doc.get("status"),
        "cash_bank_id": doc.get("cash_bank_id"),
        "journal_line_id": doc.get("journal_line_id"),
        "history_journal_line_id": doc.get("history_journal_line_id"),
        "statement_line_ids": list(doc.get("statement_line_ids") or [
            m.get("statement_line_id") or m.get("history_statement_line_id")
            for m in member_rows
            if m.get("statement_line_id") or m.get("history_statement_line_id")
        ]),
        "members": history_members,
        "member_count": len(history_members),
        "active_member_count": len(active_members),
        "members_complete": members_complete,
        "claim_active": claim_active,
        "source_history": {
            "journal_line_id": doc.get("history_journal_line_id") or doc.get("journal_line_id"),
            "statement_line_ids": list(doc.get("history_statement_line_ids") or doc.get("statement_line_ids") or []),
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
        "label": "Grup 1:N",
    }


def _business_group(doc: Optional[dict]) -> Optional[dict]:
    if doc and doc.get("match_kind") == "pair_group":
        return doc
    return None


async def _load_group_members(db, group_id: str) -> List[dict]:
    return await db[RECON_MATCHES].find(
        {"match_kind": "pair_group_member", "group_id": str(group_id)},
        {"_id": 0},
    ).to_list(1000)


async def _public_existing_group(db, existing: dict) -> dict:
    journal = None
    if existing.get("journal_id"):
        journal = await db[JOURNAL_ENTRIES].find_one({"id": existing.get("journal_id")}, {"_id": 0})
    members = await _load_group_members(db, existing["id"])
    return _public_group(existing, journal, members)


async def _adopt_group_claims(db, *, group_id: str, key: str, statement_line_ids: List[str]) -> None:
    """Adopt only lines still holding this group's create key or group id."""
    tokens = [t for t in (str(key or ""), str(group_id or "")) if t]
    if not tokens:
        return
    for line_id in statement_line_ids:
        await db[LINES].update_one(
            {"id": str(line_id), "active_claim": {"$in": tokens}},
            {"$set": {"active_claim": str(group_id)}, "$unset": {"claim_slot": ""}},
        )


async def _release_group_line_claims(db, *, statement_line_ids: List[str], token: str) -> None:
    for line_id in statement_line_ids:
        await _release_statement_claim(db, str(line_id), token)


async def _reply_stored_group(
    db,
    existing: dict,
    *,
    key: str,
    payload_hash: str,
    claimed_line_ids: Optional[List[str]] = None,
) -> dict:
    if existing.get("match_kind") != "pair_group":
        if claimed_line_ids:
            await _release_group_line_claims(db, statement_line_ids=claimed_line_ids, token=key)
        raise PairConflictError("Idempotency key yang sama dipakai untuk payload berbeda")
    if existing.get("payload_hash") != payload_hash:
        if claimed_line_ids:
            await _release_group_line_claims(db, statement_line_ids=claimed_line_ids, token=key)
        raise PairConflictError("Idempotency key yang sama dipakai untuk payload berbeda")
    if existing.get("status") == "active":
        ids = list(existing.get("statement_line_ids") or [])
        await _ensure_group_members(db, existing)
        adopt_key = str(existing.get("idempotency_key") or key)
        await _adopt_group_claims(db, group_id=existing["id"], key=adopt_key, statement_line_ids=ids)
    elif claimed_line_ids:
        await _release_group_line_claims(db, statement_line_ids=claimed_line_ids, token=key)
    return await _public_existing_group(db, existing)


async def _ensure_group_members(db, group: dict) -> None:
    """Repair members after crash between group insert and member insert.

    Re-reads the header before inserting. Stops if the group was cancelled.
    Never resurrects a cancelled member row for the same statement line.
    """
    if group.get("status") != "active":
        return
    group_id = str(group.get("id") or "")
    if not group_id:
        return
    fresh = await db[RECON_MATCHES].find_one(
        {"id": group_id, "match_kind": "pair_group", "status": "active"},
        {"_id": 0},
    )
    if not fresh:
        return
    existing = {
        str(m.get("statement_line_id") or m.get("history_statement_line_id")): m
        for m in await _load_group_members(db, group_id)
        if m.get("statement_line_id") or m.get("history_statement_line_id")
    }
    expected_ids = list(fresh.get("statement_line_ids") or [])
    snapshots = list(fresh.get("member_snapshots") or [])
    if len(snapshots) < len(expected_ids):
        # Incomplete durable header metadata — do not invent members.
        return
    for line_id, snap in zip(expected_ids, snapshots):
        sid = str(line_id)
        if sid in existing and existing[sid].get("status") == "active" and existing[sid].get("statement_line_id"):
            continue
        if sid in existing:
            # Cancelled (or other) member history for this line — do not resurrect.
            continue
        still_active = await db[RECON_MATCHES].find_one(
            {"id": group_id, "match_kind": "pair_group", "status": "active"},
            {"_id": 0, "id": 1},
        )
        if not still_active:
            return
        member = {
            "id": str(uuid.uuid4()),
            "match_kind": "pair_group_member",
            "status": "active",
            "group_id": group_id,
            "cash_bank_id": fresh.get("cash_bank_id"),
            "statement_line_id": sid,
            "amount": snap.get("amount"),
            "direction": snap.get("direction"),
            "date": snap.get("date"),
            "description": snap.get("description") or "",
            "reference": snap.get("reference") or "",
            "currency_code": fresh.get("currency_code"),
            "created_at": fresh.get("created_at") or now_iso(),
        }
        try:
            await db[RECON_MATCHES].insert_one(dict(member))
        except DuplicateKeyError:
            # Another writer owns this statement line; leave repair incomplete.
            # Caller revalidates via public read / conflict on next mutation.
            continue
        # Cancel can win between the active check and this insert — neutralize.
        header_ok = await db[RECON_MATCHES].find_one(
            {"id": group_id, "match_kind": "pair_group", "status": "active"},
            {"_id": 0, "id": 1},
        )
        if not header_ok:
            await db[RECON_MATCHES].update_one(
                {
                    "id": member["id"],
                    "match_kind": "pair_group_member",
                    "group_id": group_id,
                    "status": "active",
                },
                {
                    "$set": {
                        "status": "cancelled",
                        "history_statement_line_id": sid,
                        "cancel_reason": "repair aborted: group not active",
                        "cancelled_at": now_iso(),
                    },
                    "$unset": {"statement_line_id": ""},
                },
            )
            return


async def _validate_group_statement(
    db,
    *,
    cash_bank_id: str,
    statement_line_id: str,
    direction: str,
    account_currency: str,
    start: str,
    end: str,
    key: str,
) -> dict:
    statement = await db[LINES].find_one({"id": str(statement_line_id)}, {"_id": 0})
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
    if statement.get("direction") != direction:
        raise PairConflictError("Arah uang bank dan buku berbeda")
    if (statement.get("currency_code") or None) != account_currency:
        raise PairConflictError("Mata uang baris bank berbeda dari rekening")
    return statement


async def list_group_candidates(
    db,
    *,
    cash_bank_id: str,
    journal_line_id: str,
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
    account_currency = cb.get("currency_code") or None
    line, journal = await _journal_for_line(db, journal_line_id)
    direction, amount = _assert_matchable_journal(
        journal, line, account_id=str(cb.get("account_id")), start=start, end=end
    )
    claimed = await db[RECON_MATCHES].find_one(
        {"journal_line_id": str(journal_line_id)},
        {"_id": 0, "id": 1, "match_kind": 1},
    )
    bank_rows = await list_ready_lines(db, cash_bank_id=cash_bank_id, start=start, end=end)
    bank_ids = [str(row.get("id") or "") for row in bank_rows]
    stmt_claims = {}
    if bank_ids:
        for c in await db[RECON_MATCHES].find(
            {"statement_line_id": {"$in": bank_ids}},
            {"_id": 0},
        ).to_list(len(bank_ids)):
            stmt_claims[str(c.get("statement_line_id"))] = c

    needle = str(q or "").strip().lower()
    items = []
    for row in bank_rows:
        if stmt_claims.get(str(row.get("id"))):
            continue
        ok, reason = line_matchable(row, account_currency)
        if not ok:
            continue
        if row.get("direction") != direction:
            continue
        if account_currency and (row.get("currency_code") or None) != account_currency:
            continue
        blob = " ".join(str(row.get(k) or "") for k in (
            "description", "reference", "bank_txn_id", "date", "amount", "id",
        )).lower()
        if needle and needle not in blob:
            continue
        items.append({
            "id": row.get("id"),
            "date": row.get("date"),
            "description": row.get("description"),
            "reference": row.get("reference"),
            "bank_txn_id": row.get("bank_txn_id"),
            "direction": row.get("direction"),
            "amount": row.get("amount"),
            "currency_code": row.get("currency_code"),
            "running_balance": row.get("running_balance"),
            "matchable": True,
            "block_reason": reason if not ok else "",
        })
    start_i = (page - 1) * page_size
    return {
        "cash_bank_id": cash_bank_id,
        "journal_line_id": str(journal_line_id),
        "journal_number": journal.get("number"),
        "journal_date": journal.get("date"),
        "description": line.get("description") or journal.get("description") or "",
        "direction": direction,
        "target_amount": float(amount),
        "currency_code": account_currency,
        "journal_claimed": bool(claimed),
        "claim_kind": (claimed or {}).get("match_kind"),
        "items": items[start_i:start_i + page_size],
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "search": needle,
    }


async def create_pair_group(
    db,
    *,
    cash_bank_id: str,
    journal_line_id: str,
    statement_line_ids: List[str],
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
    unique_ids = []
    seen = set()
    for raw in statement_line_ids or []:
        sid = str(raw or "").strip()
        if not sid:
            continue
        if sid in seen:
            raise PairValidationError("Baris bank duplikat dalam grup")
        seen.add(sid)
        unique_ids.append(sid)
    if len(unique_ids) < 2:
        raise PairValidationError("Pencocokan grup membutuhkan minimal dua mutasi bank")
    ordered = sorted(unique_ids)
    payload_hash = _hash(str(cash_bank_id), str(journal_line_id), *ordered)
    existing = await _pair_for_key(db, cash_bank_id, key)
    if existing and existing.get("match_kind") == "pair_release":
        await _recover_release_slot(db, existing)
        existing = await _pair_for_key(db, cash_bank_id, key)
    if existing:
        stored = _business_group(existing) or existing
        return await _reply_stored_group(
            db, stored, key=key, payload_hash=payload_hash,
        )
    by_payload = await _active_match_by_payload(
        db,
        cash_bank_id=str(cash_bank_id),
        payload_hash=payload_hash,
        match_kind="pair_group",
    )
    if by_payload:
        return await _reply_stored_group(
            db,
            by_payload,
            key=str(by_payload.get("idempotency_key") or key),
            payload_hash=payload_hash,
        )

    cb = await get_cash_bank_raw(db, cash_bank_id)
    if not cb:
        raise PairValidationError("Kas/Bank tidak ditemukan")
    account_currency = cb.get("currency_code") or None
    if not account_currency:
        raise PairConflictError(
            "Mata uang rekening belum ditetapkan. Pencocokan tidak dinyatakan valid."
        )
    line, journal = await _journal_for_line(db, journal_line_id)
    direction, amount = _assert_matchable_journal(
        journal, line, account_id=str(cb.get("account_id")), start=start, end=end
    )

    statements = []
    total = Decimal("0.00")
    for sid in ordered:
        stmt = await _validate_group_statement(
            db,
            cash_bank_id=str(cash_bank_id),
            statement_line_id=sid,
            direction=direction,
            account_currency=account_currency,
            start=start,
            end=end,
            key=key,
        )
        total += _money(stmt.get("amount"))
        statements.append(stmt)
    if total != amount:
        raise PairConflictError(
            f"Total mutasi bank ({total}) tidak sama dengan nominal jurnal ({amount})"
        )

    await _before_pair_claim()
    claimed_ids: List[str] = []
    created_claims: List[str] = []
    try:
        for stmt in statements:
            sid = str(stmt["id"])
            claim = await _claim_statement_line(db, sid, key)
            if claim == "busy":
                raise PairConflictError("Baris bank sudah diklaim atau tidak siap dicocokkan")
            claimed_ids.append(sid)
            if claim == "created":
                created_claims.append(sid)
        await _after_statement_claim()

        # Revalidate under ownership before durable inserts.
        refreshed = []
        re_total = Decimal("0.00")
        for sid in claimed_ids:
            fresh = await db[LINES].find_one({"id": sid, "active_claim": key}, {"_id": 0})
            if not fresh:
                raise PairConflictError("Baris bank sudah diklaim atau tidak siap dicocokkan")
            visible = dict(fresh)
            visible["active_claim"] = None
            fresh_ok, fresh_reason = line_matchable(visible, account_currency)
            if not (
                fresh_ok
                and fresh.get("cash_bank_id") == str(cash_bank_id)
                and fresh.get("direction") == direction
                and (fresh.get("currency_code") or None) == account_currency
                and start <= str(fresh.get("date") or "") <= end
            ):
                raise PairConflictError(fresh_reason or "Baris bank sudah diklaim atau tidak siap dicocokkan")
            re_total += _money(fresh.get("amount"))
            refreshed.append(fresh)
        if re_total != amount:
            raise PairConflictError(
                f"Total mutasi bank ({re_total}) tidak sama dengan nominal jurnal ({amount})"
            )

        group_id = str(uuid.uuid4())
        member_snapshots = [
            {
                "statement_line_id": str(s.get("id")),
                "amount": float(_money(s.get("amount"))),
                "direction": s.get("direction"),
                "date": s.get("date"),
                "description": s.get("description") or "",
                "reference": s.get("reference") or "",
            }
            for s in refreshed
        ]
        doc = {
            "id": group_id,
            "match_kind": "pair_group",
            "status": "active",
            "cash_bank_id": str(cash_bank_id),
            "account_id": str(cb.get("account_id")),
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
            "statement_line_ids": [str(s.get("id")) for s in refreshed],
            "member_snapshots": member_snapshots,
            "idempotency_key": key,
            "payload_hash": payload_hash,
            "created_at": now_iso(),
            "created_by": actor_snapshot(user),
        }
        try:
            await db[RECON_MATCHES].insert_one(dict(doc))
        except DuplicateKeyError as exc:
            stored = _business_group(await _pair_for_key(db, cash_bank_id, key))
            if stored is None:
                # Journal uniqueness loss without our key winning.
                await _release_group_line_claims(db, statement_line_ids=claimed_ids, token=key)
                raise PairConflictError("Baris bank atau baris buku sudah diklaim") from exc
            return await _reply_stored_group(
                db,
                stored,
                key=key,
                payload_hash=payload_hash,
                claimed_line_ids=claimed_ids,
            )

        await _after_pair_insert()
        for snap in member_snapshots:
            member = {
                "id": str(uuid.uuid4()),
                "match_kind": "pair_group_member",
                "status": "active",
                "group_id": group_id,
                "cash_bank_id": str(cash_bank_id),
                "statement_line_id": snap["statement_line_id"],
                "amount": snap["amount"],
                "direction": snap["direction"],
                "date": snap["date"],
                "description": snap["description"],
                "reference": snap["reference"],
                "currency_code": account_currency,
                "created_at": doc["created_at"],
                "created_by": doc["created_by"],
            }
            try:
                await db[RECON_MATCHES].insert_one(dict(member))
            except DuplicateKeyError as exc:
                # Roll back owned group + claims; do not clear foreign claims.
                await _rollback_failed_group(
                    db,
                    group_id=group_id,
                    key=key,
                    claimed_line_ids=claimed_ids,
                )
                raise PairConflictError("Baris bank atau baris buku sudah diklaim") from exc
            await _after_group_member_insert()

        await _adopt_group_claims(
            db, group_id=group_id, key=key, statement_line_ids=claimed_ids,
        )
        doc.pop("_id", None)
        fresh_journal = await db[JOURNAL_ENTRIES].find_one({"id": journal.get("id")}, {"_id": 0})
        members = await _load_group_members(db, group_id)
        return _public_group(doc, fresh_journal, members)
    except Exception:
        if claimed_ids:
            # Only release if our group was not committed under this key.
            stored = _business_group(await _pair_for_key(db, cash_bank_id, key))
            if stored is None:
                await _release_group_line_claims(db, statement_line_ids=claimed_ids, token=key)
        raise


async def _rollback_failed_group(
    db,
    *,
    group_id: str,
    key: str,
    claimed_line_ids: List[str],
) -> None:
    """Best-effort rollback after partial member insert failure.

    Cancels the owned group header and owned members, then releases only claims
    still held by this attempt's token or group id.
    """
    members = await _load_group_members(db, group_id)
    for member in members:
        await db[RECON_MATCHES].update_one(
            {
                "id": member["id"],
                "match_kind": "pair_group_member",
                "group_id": group_id,
                "status": "active",
            },
            {
                "$set": {
                    "status": "cancelled",
                    "history_statement_line_id": member.get("statement_line_id"),
                    "cancel_reason": "rollback partial group",
                    "cancelled_at": now_iso(),
                },
                "$unset": {"statement_line_id": ""},
            },
        )
    current = await db[RECON_MATCHES].find_one({"id": group_id}, {"_id": 0})
    journal_line_id = (current or {}).get("journal_line_id")
    await db[RECON_MATCHES].update_one(
        {"id": group_id, "match_kind": "pair_group", "status": "active", "idempotency_key": key},
        {
            "$set": {
                "status": "cancelled",
                "history_journal_line_id": journal_line_id,
                "history_statement_line_ids": list(claimed_line_ids),
                "cancel_reason": "rollback partial group",
                "cancelled_at": now_iso(),
                "review_state": "riwayat",
            },
            "$unset": {"journal_line_id": ""},
        },
    )
    for line_id in claimed_line_ids:
        await db[LINES].update_one(
            {"id": str(line_id), "active_claim": {"$in": [key, group_id]}},
            {"$unset": {"active_claim": "", "claim_slot": ""}},
        )


async def cancel_pair_group(
    db,
    group_id: str,
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
    payload_hash = _hash(str(group_id), why)
    doc = await db[RECON_MATCHES].find_one(
        {"id": str(group_id), "match_kind": "pair_group"},
        {"_id": 0},
    )
    if not doc:
        raise KeyError("missing")
    by_key = await db[RECON_MATCHES].find_one(
        {"cash_bank_id": doc.get("cash_bank_id"), "cancel_idempotency_key": key},
        {"_id": 0},
    )
    if by_key:
        if by_key.get("cancel_payload_hash") != payload_hash or by_key.get("id") != str(group_id):
            raise PairConflictError("Kunci pembatalan yang sama dipakai untuk payload berbeda")
        await _clear_group_claims(db, by_key)
        members = await _load_group_members(db, by_key["id"])
        return _public_group(by_key, None, members)
    if doc.get("status") != "active":
        # Crashed cancel can leave owned claims; clear only matching tokens.
        await _clear_group_claims(db, doc)
        members = await _load_group_members(db, doc["id"])
        return _public_group(doc, None, members)

    journal_line_id = doc.get("journal_line_id")
    statement_line_ids = list(doc.get("statement_line_ids") or [])
    members = await _load_group_members(db, doc["id"])
    for member in members:
        if member.get("status") != "active":
            continue
        sid = member.get("statement_line_id")
        await db[RECON_MATCHES].update_one(
            {
                "id": member["id"],
                "match_kind": "pair_group_member",
                "group_id": doc["id"],
                "status": "active",
                "statement_line_id": sid,
            },
            {
                "$set": {
                    "status": "cancelled",
                    "history_statement_line_id": sid,
                    "cancelled_at": now_iso(),
                    "cancelled_by": actor_snapshot(user),
                    "cancel_reason": why,
                },
                "$unset": {"statement_line_id": ""},
            },
        )
        await _after_group_member_cancel()

    result = await db[RECON_MATCHES].update_one(
        {
            "id": doc["id"],
            "match_kind": "pair_group",
            "status": "active",
            "journal_line_id": journal_line_id,
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
                "history_statement_line_ids": statement_line_ids,
                "review_state": "riwayat",
            },
            "$unset": {"journal_line_id": ""},
        },
    )
    current = await db[RECON_MATCHES].find_one({"id": doc["id"]}, {"_id": 0})
    if result.matched_count != 1:
        if current and current.get("status") != "active":
            await _clear_group_claims(db, current)
            members = await _load_group_members(db, current["id"])
            return _public_group(current, None, members)
        if current and current.get("cancel_idempotency_key") == key:
            await _clear_group_claims(db, current)
            members = await _load_group_members(db, current["id"])
            return _public_group(current, None, members)
        raise PairConflictError("Pembatalan bentrok. Klaim pemenang tidak dilepas.")
    await _after_pair_cancel_stored()
    await _clear_group_claims(db, current)
    members = await _load_group_members(db, current["id"])
    return _public_group(current, None, members)


async def _clear_group_claims(db, match: dict) -> None:
    group_id = match.get("id")
    key = match.get("idempotency_key")
    line_ids = list(match.get("history_statement_line_ids") or match.get("statement_line_ids") or [])
    if not line_ids:
        members = await _load_group_members(db, group_id)
        line_ids = [
            m.get("history_statement_line_id") or m.get("statement_line_id")
            for m in members
            if m.get("history_statement_line_id") or m.get("statement_line_id")
        ]
    tokens = [t for t in (group_id, key) if t]
    for line_id in line_ids:
        if not line_id or not tokens:
            continue
        await db[LINES].update_one(
            {"id": str(line_id), "active_claim": {"$in": tokens}},
            {"$unset": {"active_claim": "", "claim_slot": ""}},
        )


async def get_pair_group(db, group_id: str) -> dict:
    doc = await db[RECON_MATCHES].find_one(
        {"id": str(group_id), "match_kind": "pair_group"},
        {"_id": 0},
    )
    if not doc:
        raise KeyError("missing")
    journal = None
    if doc.get("journal_id"):
        journal = await db[JOURNAL_ENTRIES].find_one({"id": doc.get("journal_id")}, {"_id": 0})
    members = await _load_group_members(db, doc["id"])
    return _public_group(doc, journal, members)


async def list_legacy_reconciliations(db, cash_bank_id: str) -> List[dict]:
    docs = await db[RECONCILIATIONS].find(
        {"cash_bank_id": str(cash_bank_id)},
        {"_id": 0, "id": 1, "number": 1, "status": 1, "reconciliation_date": 1, "statement_balance": 1, "book_balance": 1, "difference": 1},
    ).sort("reconciliation_date", -1).to_list(200)
    for doc in docs:
        doc["kind"] = "legacy_balance"
        doc["statement_balance_label"] = "Input manual pada dokumen saldo lama"
    return docs
