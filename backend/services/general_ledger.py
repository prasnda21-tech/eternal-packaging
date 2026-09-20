"""Buku Besar per akun — read model from journal_entries + journal_lines.

Phase 1: zero-write. Saldo is derived ONLY from journal_lines of legitimate
journal_entries (posted / reversed). Draft vouchers never create journals.
Original + reversal journals both remain so nets stay correct.

Display filters (source_type, search) narrow returned rows only.
Opening / period totals / ending stay full-period for the selected account.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS
from services.chart_of_accounts import get_account
from services.general_journal import SOURCE_GENERAL_JOURNAL, SOURCE_GENERAL_JOURNAL_REVERSAL
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    SOURCE_PAYMENT,
    SOURCE_RECEIPT,
    SOURCE_REVERSAL,
    SOURCE_TRANSFER,
    STATUS_POSTED,
    STATUS_REVERSED,
)
from services.opening_balance import SOURCE_OPENING_BALANCE, SOURCE_OPENING_BALANCE_REVERSAL

PERM_VIEW = "general_ledger.view"

SOURCE_LABELS = {
    SOURCE_RECEIPT: "Penerimaan",
    SOURCE_PAYMENT: "Pembayaran",
    SOURCE_TRANSFER: "Transfer",
    SOURCE_REVERSAL: "Reversal Kas & Bank",
    SOURCE_GENERAL_JOURNAL: "Jurnal Umum",
    SOURCE_GENERAL_JOURNAL_REVERSAL: "Reversal Jurnal Umum",
    SOURCE_OPENING_BALANCE: "Saldo Awal",
    SOURCE_OPENING_BALANCE_REVERSAL: "Reversal Saldo Awal",
    "sales_invoice": "Faktur Penjualan",
    "sales_invoice_reversal": "Reversal Faktur Penjualan",
    "sales_receipt": "Penerimaan Penjualan",
    "sales_receipt_reversal": "Reversal Penerimaan Penjualan",
    "purchase_invoice": "Faktur Pembelian",
    "purchase_invoice_reversal": "Reversal Faktur Pembelian",
    "purchase_payment": "Pembayaran Pembelian",
    "purchase_payment_reversal": "Reversal Pembayaran Pembelian",
}

# Only sources with an existing detail-route + ?id= deep-link.
SOURCE_ROUTES = {
    SOURCE_RECEIPT: "/kas-bank/penerimaan",
    SOURCE_PAYMENT: "/kas-bank/pembayaran",
    SOURCE_TRANSFER: "/kas-bank/transfer",
    "purchase_invoice": "/pembelian/faktur-pembelian",
    "purchase_invoice_reversal": "/pembelian/faktur-pembelian",
    "purchase_payment": "/pembelian/pembayaran-pembelian",
    "purchase_payment_reversal": "/pembelian/pembayaran-pembelian",
}

LEGITIMATE_STATUSES = frozenset({STATUS_POSTED, STATUS_REVERSED})


class GeneralLedgerValidationError(ValueError):
    pass


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw or 0)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _money_float(d: Decimal) -> float:
    return float(d)


def _source_label(source_type: Optional[str]) -> str:
    st = str(source_type or "")
    return SOURCE_LABELS.get(st, st or "—")


def _journal_is_legitimate(doc: dict) -> bool:
    status = str((doc or {}).get("status") or "").strip()
    if not status:
        return True
    return status in LEGITIMATE_STATUSES


async def _resolve_source_path(db, source_type: str, source_id: str) -> Optional[str]:
    """Exact source_type + source_id mapping. Never match by name/amount."""
    st = str(source_type or "").strip()
    sid = str(source_id or "").strip()
    if not sid:
        return None
    if st in SOURCE_ROUTES:
        return SOURCE_ROUTES[st]
    if st != SOURCE_REVERSAL:
        return None
    for kind, coll in (("receipt", RECEIPTS), ("payment", PAYMENTS), ("transfer", TRANSFERS)):
        doc = await db[coll].find_one({"id": sid}, {"_id": 0, "id": 1})
        if doc:
            return SOURCE_ROUTES.get(
                {"receipt": SOURCE_RECEIPT, "payment": SOURCE_PAYMENT, "transfer": SOURCE_TRANSFER}[kind]
            )
    return None


async def _sum_account_lines(db, account_id: str, journal_ids: List[str]) -> Decimal:
    if not journal_ids:
        return Decimal("0.00")
    pipeline = [
        {"$match": {"account_id": account_id, "journal_id": {"$in": journal_ids}}},
        {
            "$group": {
                "_id": None,
                "debit": {"$sum": "$debit"},
                "credit": {"$sum": "$credit"},
            }
        },
    ]
    rows = await db[JOURNAL_LINES].aggregate(pipeline).to_list(1)
    if not rows:
        return Decimal("0.00")
    return _money(rows[0].get("debit")) - _money(rows[0].get("credit"))


def _row_matches_search(row: dict, needle: str) -> bool:
    blob = " ".join([
        str(row.get("journal_number") or ""),
        str(row.get("description") or ""),
        str(row.get("line_description") or ""),
    ]).lower()
    return needle in blob


async def get_general_ledger(
    db,
    *,
    account_id: str,
    start_date: str,
    end_date: str,
    source_type: Optional[str] = None,
    q: Optional[str] = None,
) -> dict:
    """Build Buku Besar for one COA over [start_date, end_date]. GET-safe."""
    aid = str(account_id or "").strip()
    start = str(start_date or "").strip()
    end = str(end_date or "").strip()
    if not aid:
        raise GeneralLedgerValidationError("Akun Perkiraan wajib dipilih")
    if not start or not end:
        raise GeneralLedgerValidationError("Rentang tanggal wajib diisi")
    if start > end:
        raise GeneralLedgerValidationError("Tanggal mulai tidak boleh setelah tanggal akhir")

    account = await get_account(db, aid)
    if not account:
        raise GeneralLedgerValidationError("Akun Perkiraan tidak ditemukan")

    opening_journals = await db[JOURNAL_ENTRIES].find(
        {"date": {"$lt": start}},
        {"_id": 0, "id": 1, "status": 1},
    ).to_list(100000)
    opening_ids = [j["id"] for j in opening_journals if j.get("id") and _journal_is_legitimate(j)]
    opening = await _sum_account_lines(db, aid, opening_ids)

    period_journals = await db[JOURNAL_ENTRIES].find(
        {"date": {"$gte": start, "$lte": end}},
        {"_id": 0},
    ).to_list(100000)
    by_jid = {
        j["id"]: j
        for j in period_journals
        if j.get("id") and _journal_is_legitimate(j)
    }
    journal_ids = list(by_jid.keys())

    items: List[dict] = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    running = opening
    path_cache: Dict[Tuple[str, str], Optional[str]] = {}

    if journal_ids:
        lines = await db[JOURNAL_LINES].find(
            {"account_id": aid, "journal_id": {"$in": journal_ids}},
            {"_id": 0},
        ).to_list(100000)

        def _sort_key(ln: dict) -> Tuple:
            j = by_jid.get(ln.get("journal_id") or "", {})
            return (
                str(j.get("date") or ""),
                str(j.get("number") or ""),
                str(ln.get("id") or ""),
            )

        lines.sort(key=_sort_key)

        for ln in lines:
            j = by_jid.get(ln.get("journal_id") or "")
            if not j:
                continue
            st = str(j.get("source_type") or "")
            sid = str(j.get("source_id") or "")
            cache_key = (st, sid)
            if cache_key not in path_cache:
                path_cache[cache_key] = await _resolve_source_path(db, st, sid)

            debit = _money(ln.get("debit"))
            credit = _money(ln.get("credit"))
            running = running + debit - credit
            total_debit += debit
            total_credit += credit

            journal_desc = str(j.get("description") or "")
            line_desc = str(ln.get("description") or "") if ln.get("description") is not None else ""

            items.append({
                "date": j.get("date"),
                "journal_id": j.get("id"),
                "journal_line_id": ln.get("id"),
                "journal_number": j.get("number"),
                "source_type": st,
                "source_id": sid,
                "source_label": _source_label(st),
                "source_path": path_cache[cache_key],
                "description": journal_desc,
                "line_description": line_desc,
                "debit": _money_float(debit),
                "credit": _money_float(credit),
                "running_balance": _money_float(running),
                "status": j.get("status"),
                "reversal_of": j.get("reversal_of"),
            })

    ending = opening + total_debit - total_credit

    visible = items
    st_filter = str(source_type or "").strip()
    if st_filter:
        visible = [row for row in visible if str(row.get("source_type") or "") == st_filter]

    needle = str(q or "").strip().lower()
    if needle:
        visible = [row for row in visible if _row_matches_search(row, needle)]

    return {
        "account_id": aid,
        "account_code": account.get("code"),
        "account_name": account.get("name"),
        "account_type": account.get("account_type"),
        "start_date": start,
        "end_date": end,
        "opening_balance": _money_float(opening),
        "total_debit": _money_float(total_debit),
        "total_credit": _money_float(total_credit),
        "ending_balance": _money_float(ending),
        "items": visible,
        "total": len(visible),
        "period_count": len(items),
    }
