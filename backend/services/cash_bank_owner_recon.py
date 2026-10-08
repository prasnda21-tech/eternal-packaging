"""Kas & Bank reconciliation owner view — Phase 1 read-only aggregation.

Derives compact statuses from existing pair workspace / statement / book data.
Zero-write. Does not call suggest_pairs in bulk. Does not change match rules.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from services.cash_bank_master import get_cash_bank_raw
from services.cash_bank_mutasi import SOURCE_LABELS, _resolve_voucher_meta
from services.cash_bank_pair_match import (
    PairValidationError,
    _public_pair,
    book_direction,
    review_state,
)
from services.cash_bank_reconciliation import RECON_MATCHES
from services.cash_bank_statement import (
    DUP_DEFINITE,
    DUP_PENDING,
    list_ready_lines,
    statement_closing,
)
from services.general_ledger import GeneralLedgerValidationError, get_general_ledger
from services.journal_core import JOURNAL_ENTRIES, STATUS_REVERSED as JOURNAL_STATUS_REVERSED

MONEY = Decimal("0.01")

STATUS_SESUAI = "SESUAI"
STATUS_BELUM = "BELUM_DICOCOKKAN"
STATUS_STMT_ONLY = "STATEMENT_TANPA_JURNAL"
STATUS_BOOK_ONLY = "JURNAL_TANPA_STATEMENT"
STATUS_REVIEW_CANON = "PERLU_REVIEW"
STATUS_DUPLIKAT = "DUPLIKAT"
STATUS_REVERSED = "REVERSED"

STATUS_LABELS = {
    STATUS_SESUAI: "Sesuai",
    STATUS_BELUM: "Belum Dicocokkan",
    STATUS_STMT_ONLY: "Statement Tanpa Jurnal",
    STATUS_BOOK_ONLY: "Jurnal Tanpa Statement",
    STATUS_REVIEW_CANON: "Perlu Review",
    STATUS_DUPLIKAT: "Duplikat",
    STATUS_REVERSED: "Reversed",
}

FILTER_ALIASES = {
    "semua": None,
    "sesuai": STATUS_SESUAI,
    "belum_cocok": STATUS_BELUM,
    "belum_dicocokkan": STATUS_BELUM,
    "statement_tanpa_jurnal": STATUS_STMT_ONLY,
    "jurnal_tanpa_statement": STATUS_BOOK_ONLY,
    "perlu_review": STATUS_REVIEW_CANON,
    "duplikat": STATUS_DUPLIKAT,
    "reversed": STATUS_REVERSED,
}


def _money(raw: Any) -> Decimal:
    return Decimal(str(raw or 0)).quantize(MONEY)


def _money_float(d: Decimal) -> float:
    return float(d)


def _source_label(source_type: Optional[str]) -> str:
    st = str(source_type or "").strip()
    if not st:
        return "—"
    return SOURCE_LABELS.get(st, st)


def _is_duplicate(row: Optional[dict]) -> bool:
    if not row:
        return False
    return str(row.get("duplicate_status") or "") in (DUP_DEFINITE, DUP_PENDING)


def _pair_amounts_match(pair: dict, statement: Optional[dict], book_amount: Decimal, book_direction_val: str) -> bool:
    if not statement:
        return False
    if str(pair.get("cash_bank_id") or "") != str(statement.get("cash_bank_id") or ""):
        return False
    if str(pair.get("direction") or "") != str(statement.get("direction") or ""):
        return False
    if str(pair.get("direction") or "") != book_direction_val:
        return False
    return _money(pair.get("amount")) == _money(statement.get("amount")) == book_amount


def _derive_status(
    *,
    kind: str,
    pair: Optional[dict],
    statement: Optional[dict],
    journal: Optional[dict],
    book_amount: Optional[Decimal],
    book_dir: Optional[str],
    journal_status: Optional[str],
) -> str:
    """Derive one owner status when multiple conditions could apply.

    Precedence (highest first), aligned with source semantics:
    1. REVERSED — journal status is reversed
    2. DUPLIKAT — unmatched statement with duplicate_status definite/pending
    3. PERLU_REVIEW — review_state perlu_tinjauan (pair or unmatched book)
    4. SESUAI — active pair with amount/direction/COA validity
    5. STATEMENT_TANPA_JURNAL — unmatched statement
    6. JURNAL_TANPA_STATEMENT — unmatched book
    """
    if journal_status == JOURNAL_STATUS_REVERSED:
        return STATUS_REVERSED
    if kind == "statement" and _is_duplicate(statement) and not pair:
        return STATUS_DUPLIKAT
    if pair and pair.get("status") == "active" and pair.get("statement_line_id") and pair.get("journal_line_id"):
        if review_state(journal) == "perlu_tinjauan":
            return STATUS_REVIEW_CANON
        if book_amount is not None and book_dir and _pair_amounts_match(pair, statement, book_amount, book_dir):
            return STATUS_SESUAI
        return STATUS_REVIEW_CANON
    if journal is not None and review_state(journal) == "perlu_tinjauan":
        return STATUS_REVIEW_CANON
    if kind == "statement":
        return STATUS_STMT_ONLY
    return STATUS_BOOK_ONLY


async def get_owner_recon_view(
    db,
    *,
    cash_bank_id: str,
    start_date: str,
    end_date: str,
    status: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    """Batch read-only owner reconciliation view. No writes. No suggest_pairs."""
    cb_id = str(cash_bank_id or "").strip()
    start = str(start_date or "").strip()
    end = str(end_date or "").strip()
    if not cb_id:
        raise PairValidationError("Kas/Bank wajib dipilih")
    if not start or not end or start > end:
        raise PairValidationError("Periode tidak valid")

    cb = await get_cash_bank_raw(db, cb_id)
    if not cb:
        raise PairValidationError("Kas/Bank tidak ditemukan")
    account_id = str(cb.get("account_id") or "").strip()
    if not account_id:
        raise PairValidationError("Kas/Bank tidak memiliki Akun Perkiraan")

    try:
        gl = await get_general_ledger(db, account_id=account_id, start_date=start, end_date=end)
    except GeneralLedgerValidationError as exc:
        raise PairValidationError(str(exc)) from exc

    bank_rows = await list_ready_lines(db, cash_bank_id=cb_id, start=start, end=end)
    line_ids = [str(r.get("journal_line_id") or "") for r in gl["items"] if r.get("journal_line_id")]
    bank_ids = [str(r.get("id") or "") for r in bank_rows if r.get("id")]

    claim_query: Dict[str, Any] = {"$or": []}
    if line_ids:
        claim_query["$or"].append({"journal_line_id": {"$in": line_ids}})
    if bank_ids:
        claim_query["$or"].append({"statement_line_id": {"$in": bank_ids}})
    claims: List[dict] = []
    if claim_query["$or"]:
        claims = await db[RECON_MATCHES].find(claim_query, {"_id": 0}).to_list(100000)

    active_pairs = [
        c for c in claims
        if c.get("match_kind") == "pair" and c.get("status") == "active"
        and c.get("statement_line_id") and c.get("journal_line_id")
    ]
    by_statement = {c["statement_line_id"]: c for c in active_pairs}
    by_journal = {c["journal_line_id"]: c for c in active_pairs}

    journal_ids = list({
        *(c.get("journal_id") for c in active_pairs if c.get("journal_id")),
        *(r.get("journal_id") for r in gl["items"] if r.get("journal_id")),
    })
    journals: Dict[str, dict] = {}
    if journal_ids:
        found = await db[JOURNAL_ENTRIES].find(
            {"id": {"$in": list(journal_ids)}}, {"_id": 0}
        ).to_list(len(journal_ids))
        journals = {j["id"]: j for j in found if j.get("id")}

    # Batch source resolve (mutasi resolver — vouchers + payroll only)
    source_cache: Dict[Tuple[str, str], dict] = {}
    source_keys = set()
    for row in gl["items"]:
        st = str(row.get("source_type") or "")
        sid = str(row.get("source_id") or "")
        if st or sid:
            source_keys.add((st, sid))
    for st, sid in source_keys:
        source_cache[(st, sid)] = await _resolve_voucher_meta(db, st, sid)

    bank_by_id = {str(r.get("id")): r for r in bank_rows}
    book_by_line = {str(r.get("journal_line_id")): r for r in gl["items"] if r.get("journal_line_id")}

    rows: List[dict] = []
    emitted_book: set = set()
    emitted_stmt: set = set()

    # Matched pairs → one row each
    for pair in active_pairs:
        stmt = bank_by_id.get(str(pair.get("statement_line_id")))
        book = book_by_line.get(str(pair.get("journal_line_id")))
        journal = journals.get(pair.get("journal_id") or "") or (
            journals.get((book or {}).get("journal_id") or "")
        )
        book_dir, book_amt = "none", Decimal("0.00")
        if book:
            try:
                book_dir, book_amt = book_direction(book.get("debit"), book.get("credit"))
            except Exception:
                book_dir, book_amt = "none", Decimal("0.00")
        j_status = str((journal or {}).get("status") or (book or {}).get("status") or "")
        status_code = _derive_status(
            kind="pair",
            pair=pair,
            statement=stmt,
            journal=journal,
            book_amount=book_amt if book else None,
            book_dir=book_dir if book else None,
            journal_status=j_status,
        )
        st = str((book or {}).get("source_type") or "")
        sid = str((book or {}).get("source_id") or "")
        meta = source_cache.get((st, sid)) or {}
        public = _public_pair(pair, journal)
        rows.append(_row(
            row_id=f"pair:{pair.get('id')}",
            date=str((stmt or {}).get("date") or (book or {}).get("date") or pair.get("date") or ""),
            cash_bank_id=cb_id,
            cash_bank_name=cb.get("name"),
            statement=stmt,
            book=book,
            book_direction=book_dir if book else None,
            book_amount=float(book_amt) if book else None,
            source_type=st or None,
            source_id=sid or None,
            source_label=_source_label(st) if st else ((book or {}).get("source_label") or "—"),
            voucher_number=meta.get("voucher_number"),
            source_path=meta.get("source_path"),
            status=status_code,
            pair=public,
            journal_status=j_status or None,
        ))
        if pair.get("statement_line_id"):
            emitted_stmt.add(str(pair["statement_line_id"]))
        if pair.get("journal_line_id"):
            emitted_book.add(str(pair["journal_line_id"]))

    # Unmatched statement lines
    for stmt in bank_rows:
        sid = str(stmt.get("id") or "")
        if not sid or sid in emitted_stmt:
            continue
        if by_statement.get(sid):
            continue
        status_code = _derive_status(
            kind="statement",
            pair=None,
            statement=stmt,
            journal=None,
            book_amount=None,
            book_dir=None,
            journal_status=None,
        )
        rows.append(_row(
            row_id=f"stmt:{sid}",
            date=str(stmt.get("date") or ""),
            cash_bank_id=cb_id,
            cash_bank_name=cb.get("name"),
            statement=stmt,
            book=None,
            book_direction=None,
            book_amount=None,
            source_type=None,
            source_id=None,
            source_label="—",
            voucher_number=None,
            source_path=None,
            status=status_code,
            pair=None,
            journal_status=None,
        ))

    # Unmatched book lines
    for book in gl["items"]:
        lid = str(book.get("journal_line_id") or "")
        if not lid or lid in emitted_book:
            continue
        if by_journal.get(lid):
            continue
        journal = journals.get(book.get("journal_id") or "")
        try:
            book_dir, book_amt = book_direction(book.get("debit"), book.get("credit"))
        except Exception:
            book_dir, book_amt = "none", Decimal("0.00")
        j_status = str((journal or {}).get("status") or book.get("status") or "")
        status_code = _derive_status(
            kind="book",
            pair=None,
            statement=None,
            journal=journal,
            book_amount=book_amt,
            book_dir=book_dir,
            journal_status=j_status,
        )
        st = str(book.get("source_type") or "")
        sid = str(book.get("source_id") or "")
        meta = source_cache.get((st, sid)) or {}
        rows.append(_row(
            row_id=f"book:{lid}",
            date=str(book.get("date") or ""),
            cash_bank_id=cb_id,
            cash_bank_name=cb.get("name"),
            statement=None,
            book=book,
            book_direction=book_dir,
            book_amount=float(book_amt),
            source_type=st or None,
            source_id=sid or None,
            source_label=_source_label(st) if st else (book.get("source_label") or "—"),
            voucher_number=meta.get("voucher_number"),
            source_path=meta.get("source_path"),
            status=status_code,
            pair=None,
            journal_status=j_status or None,
        ))

    rows.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("id") or "")))

    # Filters
    status_filter = _normalize_status_filter(status)
    needle = str(q or "").strip().lower()
    visible = rows
    if status_filter == STATUS_BELUM:
        visible = [
            r for r in visible
            if r.get("status") in (STATUS_STMT_ONLY, STATUS_BOOK_ONLY, STATUS_BELUM)
        ]
    elif status_filter:
        visible = [r for r in visible if r.get("status") == status_filter]
    if needle:
        visible = [r for r in visible if _hits(r, needle)]

    closing = await statement_closing(db, cash_bank_id=cb_id, end=end)
    book_ending = gl["ending_balance"]
    difference = None
    closing_complete = False
    closing_amount = closing.get("amount")
    if closing.get("status") == "dari_kolom_saldo_impor" and closing_amount is not None:
        closing_complete = True
        difference = float(_money(closing_amount) - _money(book_ending))
    elif closing.get("manual") and closing["manual"].get("amount") is not None:
        # Manual is detail-only context; not treated as complete bank closing.
        difference = float(_money(closing["manual"]["amount"]) - _money(book_ending))

    counts = _count_statuses(rows)
    return {
        "cash_bank_id": cb_id,
        "cash_bank_name": cb.get("name"),
        "account_id": account_id,
        "start_date": start,
        "end_date": end,
        "book_ending_balance": book_ending,
        "book_opening_balance": gl.get("opening_balance"),
        "statement_closing": closing,
        "statement_closing_complete": closing_complete,
        "statement_closing_note": (
            None if closing_complete
            else "Statement closing belum lengkap"
        ),
        "difference": difference,
        "balance_match_declared": False,
        "legacy_rk_note": (
            "Dokumen rekonsiliasi saldo lama (RK) terpisah. "
            "Finalized RK bukan berarti saldo cocok."
        ),
        "auto_match": False,
        "read_only": True,
        "counts": counts,
        "summary": {
            "saldo_buku": book_ending,
            "saldo_statement": closing_amount if closing_complete else None,
            "selisih": difference if closing_complete else None,
            "cocok": counts.get(STATUS_SESUAI, 0),
            "belum_cocok": (
                counts.get(STATUS_STMT_ONLY, 0)
                + counts.get(STATUS_BOOK_ONLY, 0)
                + counts.get(STATUS_BELUM, 0)
            ),
            "perlu_review": counts.get(STATUS_REVIEW_CANON, 0),
        },
        "items": visible,
        "total": len(visible),
        "total_unfiltered": len(rows),
        "status_labels": STATUS_LABELS,
    }


def _normalize_status_filter(raw: Optional[str]) -> Optional[str]:
    if raw is None or str(raw).strip() == "":
        return None
    key = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
    if key in STATUS_LABELS:
        return key
    alias = FILTER_ALIASES.get(str(raw).strip().lower().replace(" ", "_").replace("-", "_"))
    return alias


def _count_statuses(rows: List[dict]) -> Dict[str, int]:
    out = {k: 0 for k in STATUS_LABELS}
    for r in rows:
        st = r.get("status")
        if st in out:
            out[st] += 1
    return out


def _hits(row: dict, needle: str) -> bool:
    stmt = row.get("statement") or {}
    book = row.get("book") or {}
    blob = " ".join([
        str(row.get("date") or ""),
        str(row.get("status") or ""),
        str(row.get("source_label") or ""),
        str(row.get("source_type") or ""),
        str(row.get("voucher_number") or ""),
        str(stmt.get("description") or ""),
        str(stmt.get("reference") or ""),
        str(stmt.get("bank_txn_id") or ""),
        str(book.get("journal_number") or ""),
        str(book.get("description") or ""),
        str(book.get("line_description") or ""),
        str(row.get("nominal") or ""),
    ]).lower()
    return needle in blob


def _row(
    *,
    row_id: str,
    date: str,
    cash_bank_id: str,
    cash_bank_name: Any,
    statement: Optional[dict],
    book: Optional[dict],
    book_direction: Optional[str],
    book_amount: Optional[float],
    source_type: Optional[str],
    source_id: Optional[str],
    source_label: str,
    voucher_number: Optional[str],
    source_path: Optional[str],
    status: str,
    pair: Optional[dict],
    journal_status: Optional[str],
) -> dict:
    stmt_public = None
    if statement:
        stmt_public = {
            "id": statement.get("id"),
            "date": statement.get("date"),
            "amount": statement.get("amount"),
            "direction": statement.get("direction"),
            "description": statement.get("description"),
            "reference": statement.get("reference"),
            "bank_txn_id": statement.get("bank_txn_id"),
            "duplicate_status": statement.get("duplicate_status"),
            "duplicate_reason": statement.get("duplicate_reason"),
            "running_balance": statement.get("running_balance"),
            "import_id": statement.get("import_id"),
        }
    book_public = None
    nominal = None
    if statement and statement.get("amount") is not None:
        nominal = statement.get("amount")
    elif book_amount is not None:
        nominal = book_amount
    if book:
        book_public = {
            "journal_line_id": book.get("journal_line_id"),
            "journal_id": book.get("journal_id"),
            "journal_number": book.get("journal_number"),
            "date": book.get("date"),
            "debit": book.get("debit"),
            "credit": book.get("credit"),
            "direction": book_direction,
            "absolute_amount": book_amount,
            "source_type": book.get("source_type"),
            "source_id": book.get("source_id"),
            "description": book.get("description") or book.get("line_description"),
            "status": book.get("status") or journal_status,
        }
    return {
        "id": row_id,
        "date": date,
        "cash_bank_id": cash_bank_id,
        "bank": cash_bank_name,
        "statement_text": (statement or {}).get("description") if statement else None,
        "book_text": (book_public or {}).get("journal_number") if book_public else None,
        "nominal": nominal,
        "direction": (statement or {}).get("direction") if statement else book_direction,
        "source_type": source_type,
        "source_id": source_id,
        "source_label": source_label,
        "voucher_number": voucher_number,
        "source_path": source_path,
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "statement": stmt_public,
        "book": book_public,
        "match": pair,
        "journal_status": journal_status,
    }
