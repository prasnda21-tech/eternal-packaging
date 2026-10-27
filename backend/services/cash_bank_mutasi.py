"""Kas & Bank Mutasi — read model from journal_lines (Phase 2).

Saldo is derived ONLY from journal_lines for the COA linked to the selected
cash/bank. Draft vouchers never create journals, so they are excluded by design.
Original + reversal journals both remain as audit rows.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from services.cash_bank_master import get_cash_bank_raw
from services.cash_bank_vouchers import PAYMENTS, RECEIPTS, TRANSFERS
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    SOURCE_PAYMENT,
    SOURCE_RECEIPT,
    SOURCE_REVERSAL,
    SOURCE_TRANSFER,
)

SOURCE_LABELS = {
    SOURCE_RECEIPT: "Penerimaan",
    SOURCE_PAYMENT: "Pembayaran",
    SOURCE_TRANSFER: "Transfer",
    SOURCE_REVERSAL: "Reversal",
    "purchase_payment": "Pembayaran Pembelian",
    "purchase_payment_reversal": "Reversal Pembayaran Pembelian",
    "payroll_payment": "Pembayaran Payroll",
    "payroll_payment_reversal": "Reversal Pembayaran Payroll",
}

SOURCE_ROUTES = {
    SOURCE_RECEIPT: "/kas-bank/penerimaan",
    SOURCE_PAYMENT: "/kas-bank/pembayaran",
    SOURCE_TRANSFER: "/kas-bank/transfer",
    "receipt": "/kas-bank/penerimaan",
    "payment": "/kas-bank/pembayaran",
    "transfer": "/kas-bank/transfer",
}


class MutasiValidationError(ValueError):
    pass


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw or 0)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def _money_float(d: Decimal) -> float:
    return float(d)


def _source_label(source_type: Optional[str]) -> str:
    return SOURCE_LABELS.get(str(source_type or ""), str(source_type or "—"))


async def _resolve_voucher_meta(db, source_type: str, source_id: str) -> Dict[str, Any]:
    """Resolve voucher number + kind for source_type/source_id (exact ids)."""
    sid = str(source_id or "").strip()
    st = str(source_type or "").strip()
    if not sid:
        return {"voucher_number": None, "voucher_kind": None, "source_path": None}

    kind = None
    if st == SOURCE_RECEIPT:
        kind = "receipt"
        coll = RECEIPTS
    elif st == SOURCE_PAYMENT:
        kind = "payment"
        coll = PAYMENTS
    elif st == SOURCE_TRANSFER:
        kind = "transfer"
        coll = TRANSFERS
    elif st == SOURCE_REVERSAL:
        # Exact source_id is the voucher — probe collections in stable order.
        for k, coll_name in (("receipt", RECEIPTS), ("payment", PAYMENTS), ("transfer", TRANSFERS)):
            doc = await db[coll_name].find_one({"id": sid}, {"_id": 0, "number": 1, "id": 1})
            if doc:
                return {
                    "voucher_number": doc.get("number"),
                    "voucher_kind": k,
                    "source_path": SOURCE_ROUTES.get(k),
                }
        return {"voucher_number": None, "voucher_kind": None, "source_path": None}
    elif st in ("payroll_payment", "payroll_payment_reversal"):
        doc = await db["hr_payroll_payments"].find_one(
            {"id": sid}, {"_id": 0, "payment_number": 1}
        )
        return {
            "voucher_number": (doc or {}).get("payment_number"),
            "voucher_kind": None,
            "source_path": None,
        }
    else:
        return {"voucher_number": None, "voucher_kind": None, "source_path": None}

    doc = await db[coll].find_one({"id": sid}, {"_id": 0, "number": 1, "id": 1})
    return {
        "voucher_number": (doc or {}).get("number"),
        "voucher_kind": kind,
        "source_path": SOURCE_ROUTES.get(kind),
    }


async def account_balance_as_of(
    db,
    account_id: str,
    *,
    before_date: Optional[str] = None,
    through_date: Optional[str] = None,
) -> Decimal:
    """SUM(debit-credit) for account_id with optional date bounds on journal header.

    - before_date: journals with date < before_date (opening)
    - through_date: journals with date <= through_date (book balance)
    Includes all journal statuses (posted + reversed) so reversal nets correctly.
    """
    aid = str(account_id or "").strip()
    if not aid:
        return Decimal("0.00")

    date_filter: Dict[str, Any] = {}
    if before_date:
        date_filter["$lt"] = str(before_date)
    if through_date:
        date_filter["$lte"] = str(through_date)

    journal_query: Dict[str, Any] = {}
    if date_filter:
        journal_query["date"] = date_filter

    if journal_query:
        journals = await db[JOURNAL_ENTRIES].find(
            journal_query, {"_id": 0, "id": 1}
        ).to_list(100000)
        journal_ids = [j["id"] for j in journals if j.get("id")]
        if not journal_ids:
            return Decimal("0.00")
        line_query = {"account_id": aid, "journal_id": {"$in": journal_ids}}
    else:
        line_query = {"account_id": aid}

    pipeline = [
        {"$match": line_query},
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


async def get_mutasi(
    db,
    *,
    cash_bank_id: str,
    start_date: str,
    end_date: str,
    q: Optional[str] = None,
    source_type: Optional[str] = None,
    exclude_internal_transfer: bool = False,
) -> dict:
    """Build mutasi ledger for one cash/bank over [start_date, end_date].

    GET-safe: zero-write.
    Sort: date ASC, then created_at ASC, then line id ASC.
    When exclude_internal_transfer is set, transfer journals (and reversals of
    those transfers) are omitted from rows, totals, and count — not only the UI.
    """
    cb_id = str(cash_bank_id or "").strip()
    start = str(start_date or "").strip()
    end = str(end_date or "").strip()
    if not cb_id:
        raise MutasiValidationError("Kas/Bank wajib dipilih")
    if not start or not end:
        raise MutasiValidationError("Rentang tanggal wajib diisi")
    if start > end:
        raise MutasiValidationError("Tanggal mulai tidak boleh setelah tanggal akhir")

    cb = await get_cash_bank_raw(db, cb_id)
    if not cb:
        raise MutasiValidationError("Kas/Bank tidak ditemukan")
    account_id = str(cb.get("account_id") or "").strip()
    if not account_id:
        raise MutasiValidationError("Kas/Bank tidak memiliki Akun Perkiraan")

    opening = await account_balance_as_of(db, account_id, before_date=start)

    # Period journals
    journal_query: Dict[str, Any] = {"date": {"$gte": start, "$lte": end}}
    st_filter = str(source_type or "").strip()
    if st_filter:
        journal_query["source_type"] = st_filter

    journals = await db[JOURNAL_ENTRIES].find(journal_query, {"_id": 0}).to_list(100000)
    by_jid = {j["id"]: j for j in journals if j.get("id")}
    journal_ids = list(by_jid.keys())
    transfer_ids = {
        str(j.get("id") or "")
        for j in journals
        if str(j.get("source_type") or "") == SOURCE_TRANSFER and j.get("id")
    }
    drop_internal = bool(exclude_internal_transfer)

    items: List[dict] = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    running = opening

    if journal_ids:
        lines = await db[JOURNAL_LINES].find(
            {"account_id": account_id, "journal_id": {"$in": journal_ids}},
            {"_id": 0},
        ).to_list(100000)

        # Authoritative sort: date ASC, created_at, line id
        def _sort_key(ln: dict) -> Tuple:
            j = by_jid.get(ln.get("journal_id") or "", {})
            return (
                str(j.get("date") or ""),
                str(j.get("created_at") or ln.get("created_at") or ""),
                str(j.get("id") or ""),
                str(ln.get("id") or ""),
            )

        lines.sort(key=_sort_key)

        voucher_cache: Dict[Tuple[str, str], dict] = {}

        for ln in lines:
            j = by_jid.get(ln.get("journal_id") or "")
            if not j:
                continue
            st = str(j.get("source_type") or "")
            sid = str(j.get("source_id") or "")
            if drop_internal:
                if st == SOURCE_TRANSFER:
                    continue
                original = str(j.get("reversal_of") or "").strip()
                if original and original in transfer_ids:
                    continue
            cache_key = (st, sid)
            if cache_key not in voucher_cache:
                voucher_cache[cache_key] = await _resolve_voucher_meta(db, st, sid)
            meta = voucher_cache[cache_key]

            debit = _money(ln.get("debit"))
            credit = _money(ln.get("credit"))
            txn_no = meta.get("voucher_number") or j.get("number") or ""
            description = (
                ln.get("description")
                or j.get("description")
                or ""
            )

            running = running + debit - credit
            total_debit += debit
            total_credit += credit

            source_path = meta.get("source_path")
            if st == SOURCE_REVERSAL and not source_path:
                source_path = None
            elif st in SOURCE_ROUTES and not source_path:
                source_path = SOURCE_ROUTES[st]

            items.append({
                "journal_line_id": ln.get("id"),
                "journal_id": j.get("id"),
                "journal_number": j.get("number"),
                "date": j.get("date"),
                "transaction_number": txn_no,
                "source_type": st,
                "source_id": sid,
                "source_label": _source_label(st),
                "source_path": source_path,
                "voucher_kind": meta.get("voucher_kind"),
                "description": description,
                "debit": _money_float(debit),
                "credit": _money_float(credit),
                "running_balance": _money_float(running),
                "status": j.get("status"),
                "reversal_of": j.get("reversal_of"),
                "created_at": j.get("created_at") or ln.get("created_at"),
            })

    ending = opening + total_debit - total_credit

    # Optional search filter on returned rows only — summary totals stay period-authoritative
    # (already exclude_internal_transfer-aware when that flag is set).
    needle = str(q or "").strip().lower()
    visible = items
    if needle:
        visible = []
        for row in items:
            blob = " ".join([
                str(row.get("transaction_number") or ""),
                str(row.get("journal_number") or ""),
                str(row.get("description") or ""),
                str(row.get("source_label") or ""),
                str(row.get("source_id") or ""),
            ]).lower()
            if needle in blob:
                visible.append(row)

    return {
        "cash_bank_id": cb_id,
        "cash_bank_name": cb.get("name"),
        "account_id": account_id,
        "start_date": start,
        "end_date": end,
        "exclude_internal_transfer": drop_internal,
        "opening_balance": _money_float(opening),
        "total_debit": _money_float(total_debit),
        "total_credit": _money_float(total_credit),
        "ending_balance": _money_float(ending),
        "items": visible,
        "total": len(visible),
    }
