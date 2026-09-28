"""Dashboard Keuangan — read-only aggregation of existing financial services.

Does not post, reverse, recover, or activate dates. Card totals come from
Laba/Rugi and Neraca. Outstanding lists come from the piutang/utang services.
Cash movement uses the same legitimate journal set, then excludes transfers
between the company's own cash accounts from the external totals.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.account_type_reporting import reporting_metadata
from services.accounts_payable import get_accounts_payable_aging, get_accounts_payable_summary
from services.accounts_receivable import get_accounts_receivable_aging, get_accounts_receivable_summary
from services.balance_sheet import get_balance_sheet
from services.financial_inventory_opening import (
    build_financial_book_reconciliation,
    wib_business_date,
)
from services.financial_statement_common import (
    JOURNAL_ID_BATCH_SIZE,
    journal_is_legitimate,
    load_statement_accounts,
    money,
    money_float,
    parse_financial_date_range,
)
from services.inventory_reconciliation import get_inventory_reconciliation_summary
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    SOURCE_TRANSFER,
    reversal_incomplete_blocker,
)
from services.profit_loss import get_profit_loss
from services.purchase_accounting_settings import get_purchase_accounting_settings

PERM_VIEW = "dashboard.finance.view"

WIB = timezone(timedelta(hours=7))
LIST_LIMIT = 12
DAY_GRAIN_MAX = 31

CASH_NOTE = (
    "Pergerakan kas pada periode. Bukan laporan arus kas formal. "
    "Transfer antar rekening sendiri tidak dihitung sebagai kas masuk atau kas keluar eksternal."
)
AGING_LABEL = "Saat ini"
POSITION_SCOPE = "saldo_per_tanggal"
PERIOD_SCOPE = "selama_periode"


class FinancialDashboardValidationError(ValueError):
    pass


def _ratio(part: Decimal, whole: Decimal) -> Optional[float]:
    if whole == 0:
        return None
    return float((part / whole).quantize(Decimal("0.000001")))


def _chunk(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    step = max(int(size), 1)
    for index in range(0, len(items), step):
        yield items[index:index + step]


def _section_error(source: str, exc: Exception) -> dict:
    return {
        "status": "error",
        "source": source,
        "amount": None,
        "blocker_code": "LOAD_FAILED",
        "detail": str(exc) or "Gagal memuat",
    }


def _metric(amount: Optional[float], *, status: str, source: str, label: str, detail: Optional[str] = None, warning: Optional[str] = None) -> dict:
    return {
        "label": label,
        "amount": amount,
        "status": status,
        "source": source,
        "detail": detail,
        "warning": warning,
    }


def _parse_range(start_date: str, end_date: str) -> Tuple[str, str]:
    return parse_financial_date_range(
        start_date,
        end_date,
        empty_message="Rentang tanggal wajib diisi",
        start_invalid_message="Tanggal mulai tidak valid",
        end_invalid_message="Tanggal akhir tidak valid",
        order_message="Tanggal mulai tidak boleh setelah tanggal akhir",
        error_cls=FinancialDashboardValidationError,
    )


def _add_months(year: int, month: int, step: int) -> Tuple[int, int]:
    cursor = (year * 12 + (month - 1)) + step
    return cursor // 12, cursor % 12 + 1


def _bucket_keys(start: str, end: str, grain: str) -> List[str]:
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    keys: List[str] = []
    if grain == "month":
        year, month = start_d.year, start_d.month
        while (year, month) <= (end_d.year, end_d.month):
            keys.append(f"{year:04d}-{month:02d}")
            year, month = _add_months(year, month, 1)
        return keys
    cursor = start_d
    while cursor <= end_d:
        keys.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return keys


def _grain_for(start: str, end: str) -> str:
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    return "day" if span <= DAY_GRAIN_MAX else "month"


def _bucket_of(journal_date: str, grain: str) -> str:
    if grain == "month":
        return journal_date[:7]
    return journal_date


async def _legitimate_journals(db, date_query: dict) -> List[dict]:
    rows: List[dict] = []
    cursor = db[JOURNAL_ENTRIES].find(
        date_query,
        {"_id": 0, "id": 1, "date": 1, "status": 1, "source_type": 1, "reversal_of": 1},
    )
    async for doc in cursor:
        journal_id = str(doc.get("id") or "").strip()
        if not journal_id or not journal_is_legitimate(doc):
            continue
        doc["id"] = journal_id
        rows.append(doc)
    return rows


async def _lines_for(db, journal_ids: Sequence[str], account_ids: Optional[Sequence[str]] = None) -> List[dict]:
    if not journal_ids:
        return []
    lines: List[dict] = []
    account_filter = [str(item) for item in (account_ids or []) if item]
    for batch in _chunk(list(journal_ids), JOURNAL_ID_BATCH_SIZE):
        query: Dict[str, Any] = {"journal_id": {"$in": list(batch)}}
        if account_filter:
            query["account_id"] = {"$in": account_filter}
        cursor = db[JOURNAL_LINES].find(
            query,
            {"_id": 0, "journal_id": 1, "account_id": 1, "debit": 1, "credit": 1},
        )
        async for line in cursor:
            lines.append(line)
    return lines


def _account_index(accounts: Sequence[dict]) -> Dict[str, dict]:
    indexed: Dict[str, dict] = {}
    for account in accounts:
        account_id = str(account.get("id") or "").strip()
        if not account_id:
            continue
        meta = reporting_metadata(account.get("account_type")) or {}
        indexed[account_id] = {
            "id": account_id,
            "code": account.get("code"),
            "name": account.get("name"),
            "account_type": str(account.get("account_type") or ""),
            "statement": meta.get("statement"),
            "report_group": meta.get("report_group"),
        }
    return indexed


def _signed_income(debit: Decimal, credit: Decimal, report_group: str) -> Decimal:
    if report_group in ("revenue", "other_income"):
        return money(credit - debit)
    if report_group in ("cogs", "expense", "other_expense"):
        return money(debit - credit)
    return Decimal("0.00")


async def _performance(db, start: str, end: str) -> dict:
    statement = await get_profit_loss(db, start_date=start, end_date=end)
    totals = statement.get("totals") or {}
    revenue = money(totals.get("revenue_total"))
    cogs = money(totals.get("cogs_total"))
    gross = money(totals.get("gross_profit"))
    expense = money(totals.get("expense_total"))
    other_income = money(totals.get("other_income_total"))
    other_expense = money(totals.get("other_expense_total"))
    net = money(totals.get("net_profit"))
    journals = await _legitimate_journals(db, {"date": {"$gte": start, "$lte": end}})
    blocker = await reversal_incomplete_blocker(db, ({"date": {"$gte": start, "$lte": end}},))
    if not journals:
        status = "empty"
        amounts = None
    elif revenue == 0 and cogs == 0 and expense == 0 and other_income == 0 and other_expense == 0:
        status = "zero"
        amounts = (revenue, cogs, gross, expense, other_income, other_expense, net)
    else:
        status = "incomplete" if blocker else "complete"
        amounts = (revenue, cogs, gross, expense, other_income, other_expense, net)
    if blocker and status in {"empty", "zero", "complete"}:
        status = "incomplete"
    number = (lambda value: None if amounts is None else money_float(value))
    return {
        "status": status,
        "scope": PERIOD_SCOPE,
        "scope_label": "Selama periode",
        "source": "profit_loss.journal_lines",
        "start_date": start,
        "end_date": end,
        "revenue": number(revenue),
        "cogs": number(cogs),
        "gross_profit": number(gross),
        "operating_expense": number(expense),
        "other_income": number(other_income),
        "other_expense": number(other_expense),
        "net_profit": number(net),
        "gross_margin": None if amounts is None else _ratio(gross, revenue),
        "net_margin": None if amounts is None else _ratio(net, revenue),
        "blocker_code": blocker,
        "detail": (
            "Ada reversal yang belum selesai pada periode ini. Angka memakai jurnal yang sudah sah dan tidak memasukkan jurnal yang belum lengkap."
            if blocker else None
        ),
        "expense_items": [
            {
                "account_id": row.get("account_id"),
                "code": row.get("code"),
                "name": row.get("name"),
                "amount": row.get("amount"),
            }
            for group in (statement.get("groups") or [])
            if group.get("report_group") == "expense"
            for row in (group.get("items") or [])
        ] if amounts is not None else [],
    }


def _sum_type(rows: Sequence[dict], account_type: str) -> Tuple[Optional[float], int]:
    matched = [row for row in rows if str(row.get("account_type") or "") == account_type]
    if not matched:
        return None, 0
    total = money(sum((money(row.get("amount")) for row in matched), Decimal("0.00")))
    return money_float(total), len(matched)


async def _position(db, end: str, book: Optional[dict], book_error: Optional[str]) -> dict:
    sheet = await get_balance_sheet(db, as_of_date=end)
    rows = []
    for group in (sheet.get("groups") or {}).values():
        rows.extend(group.get("accounts") or [])
    blocker = await reversal_incomplete_blocker(db, ({"date": {"$lte": end}},))
    cash_amount, cash_count = _sum_type(rows, "BANK")
    ar_amount, ar_count = _sum_type(rows, "AREC")
    ap_amount, ap_count = _sum_type(rows, "APAY")
    inventory_amount, inventory_count = _sum_type(rows, "INTR")
    settings = await get_purchase_accounting_settings(db)
    clearing_id = str(settings.get("purchase_clearing_account_id") or "").strip()
    grni = _metric(None, status="unavailable", source="purchase_clearing_account", label="GRNI", detail="Akun GRNI belum diatur")
    if clearing_id:
        match = next((row for row in rows if str(row.get("account_id") or "") == clearing_id), None)
        if match:
            grni = _metric(
                match.get("amount"),
                status="incomplete" if blocker else "complete",
                source="balance_sheet.purchase_clearing_account",
                label="GRNI",
                detail="Saldo akun Purchase Clearing / GRNI per tanggal akhir.",
            )
        else:
            grni = _metric(
                None,
                status="unavailable",
                source="purchase_clearing_account",
                label="GRNI",
                detail="Akun GRNI tidak ada pada neraca tanggal akhir.",
            )

    coverage = "unavailable"
    coverage_warning = None
    if book_error:
        coverage = "error"
        coverage_warning = "Cakupan opening tidak dapat dibaca."
    elif isinstance(book, dict):
        if book.get("reconciliation_status") == "MATCH" and book.get("coverage_complete"):
            coverage = "complete"
        elif inventory_amount is None and book.get("financial_remaining") is None and book.get("inventory_gl_balance") is None:
            coverage = "unavailable"
            coverage_warning = "Nilai buku persediaan belum dapat dihitung."
        else:
            coverage = "incomplete"
            coverage_warning = (
                "Saldo tercatat dari jurnal sampai tanggal akhir. "
                "Cakupan opening atau rekonsiliasi persediaan belum lengkap. "
                "Ini bukan nilai estimasi dan bukan Published Pricing."
            )
    inventory_status = "unavailable" if inventory_amount is None else coverage
    if inventory_amount is None:
        inventory_warning = "Belum tersedia"
    elif coverage == "incomplete":
        inventory_warning = coverage_warning
    elif coverage == "error":
        inventory_status = "incomplete"
        inventory_warning = coverage_warning
    else:
        inventory_warning = None
    if blocker and inventory_status == "complete":
        inventory_status = "incomplete"
    base_status = "incomplete" if blocker else "complete"

    def positional(amount, count, label, source):
        if count == 0 or amount is None:
            return _metric(None, status="unavailable", source=source, label=label, detail="Belum tersedia")
        return _metric(
            amount,
            status=base_status,
            source=source,
            label=label,
            detail="Saldo awal dan mutasi jurnal yang sah sampai tanggal akhir." if blocker is None else "Saldo tercatat. Ada reversal yang belum selesai.",
            warning="Ada reversal yang belum selesai." if blocker else None,
        )

    inventory_metric = _metric(
        inventory_amount,
        status=inventory_status,
        source="balance_sheet.account_type_INTR",
        label="Nilai buku persediaan",
        detail=inventory_warning or "Saldo buku akun persediaan. Bukan Published Pricing.",
        warning=inventory_warning if inventory_status == "incomplete" else None,
    )
    inventory_metric["published_pricing_is_book"] = False
    inventory_metric["book_reconciliation_status"] = None if not isinstance(book, dict) else book.get("reconciliation_status")
    inventory_metric["coverage_complete"] = None if not isinstance(book, dict) else book.get("coverage_complete")
    return {
        "status": "incomplete" if blocker or coverage == "incomplete" else ("unavailable" if cash_count == ar_count == ap_count == inventory_count == 0 and grni["status"] == "unavailable" else "complete"),
        "scope": POSITION_SCOPE,
        "scope_label": "Saldo per tanggal",
        "as_of": end,
        "source": "balance_sheet.journal_lines",
        "blocker_code": blocker,
        "detail": "Ada reversal yang belum selesai sampai tanggal akhir." if blocker else None,
        "cash_and_bank": positional(cash_amount, cash_count, "Saldo kas dan bank", "balance_sheet.account_type_BANK"),
        "accounts_receivable": positional(ar_amount, ar_count, "Piutang usaha", "balance_sheet.account_type_AREC"),
        "accounts_payable": positional(ap_amount, ap_count, "Utang usaha", "balance_sheet.account_type_APAY"),
        "inventory_book": inventory_metric,
        "grni": grni,
    }


def _is_internal_transfer(journal: dict, transfer_ids: set) -> bool:
    if str(journal.get("source_type") or "") == SOURCE_TRANSFER:
        return True
    original = str(journal.get("reversal_of") or "").strip()
    return bool(original and original in transfer_ids)


async def _cash_movement(db, start: str, end: str, accounts: Dict[str, dict]) -> dict:
    bank_ids = [account_id for account_id, row in accounts.items() if row.get("account_type") == "BANK"]
    journals = await _legitimate_journals(db, {"date": {"$lte": end}})
    transfer_ids = {
        str(row.get("id") or "")
        for row in journals
        if str(row.get("source_type") or "") == SOURCE_TRANSFER
    }
    by_id = {str(row.get("id") or ""): row for row in journals}
    period_ids = []
    opening_ids = []
    for row in journals:
        journal_date = str(row.get("date") or "")
        if journal_date < start:
            opening_ids.append(row["id"])
        elif start <= journal_date <= end:
            period_ids.append(row["id"])
    relevant = opening_ids + period_ids
    lines = await _lines_for(db, relevant, bank_ids)
    names = {}
    async for cash_bank in db.cash_bank_accounts.find({}, {"_id": 0, "id": 1, "name": 1, "account_id": 1}):
        account_id = str(cash_bank.get("account_id") or "").strip()
        if account_id:
            names[account_id] = cash_bank.get("name")
    per_account: Dict[str, dict] = {
        account_id: {
            "account_id": account_id,
            "code": accounts[account_id].get("code"),
            "name": names.get(account_id) or accounts[account_id].get("name"),
            "opening": Decimal("0.00"),
            "cash_in": Decimal("0.00"),
            "cash_out": Decimal("0.00"),
            "internal_in": Decimal("0.00"),
            "internal_out": Decimal("0.00"),
        }
        for account_id in bank_ids
    }
    external_in = Decimal("0.00")
    external_out = Decimal("0.00")
    internal_volume = Decimal("0.00")
    period_activity = False
    for line in lines:
        account_id = str(line.get("account_id") or "")
        bucket = per_account.get(account_id)
        journal = by_id.get(str(line.get("journal_id") or ""))
        if not bucket or not journal:
            continue
        debit = money(line.get("debit"))
        credit = money(line.get("credit"))
        journal_date = str(journal.get("date") or "")
        if journal_date < start:
            bucket["opening"] = money(bucket["opening"] + debit - credit)
            continue
        period_activity = True
        if _is_internal_transfer(journal, transfer_ids):
            bucket["internal_in"] = money(bucket["internal_in"] + debit)
            bucket["internal_out"] = money(bucket["internal_out"] + credit)
            internal_volume = money(internal_volume + debit)
        else:
            bucket["cash_in"] = money(bucket["cash_in"] + debit)
            bucket["cash_out"] = money(bucket["cash_out"] + credit)
            external_in = money(external_in + debit)
            external_out = money(external_out + credit)
    blocker = await reversal_incomplete_blocker(
        db,
        ({"date": {"$lt": start}}, {"date": {"$gte": start, "$lte": end}}),
    )
    if not bank_ids:
        status = "unavailable"
        amounts = None
    elif not period_activity:
        status = "empty"
        amounts = None
    elif external_in == 0 and external_out == 0:
        status = "zero"
        amounts = (external_in, external_out, money(external_in - external_out))
    else:
        status = "complete"
        amounts = (external_in, external_out, money(external_in - external_out))
    if blocker and status in {"empty", "zero", "complete"}:
        status = "incomplete"
    number = (lambda value: None if amounts is None else money_float(value))
    account_rows = []
    for account_id in sorted(per_account, key=lambda key: (str(per_account[key].get("code") or ""), key)):
        row = per_account[account_id]
        ending = money(row["opening"] + row["cash_in"] - row["cash_out"] + row["internal_in"] - row["internal_out"])
        account_rows.append({
            "account_id": account_id,
            "code": row.get("code"),
            "name": row.get("name"),
            "opening": money_float(row["opening"]),
            "cash_in": money_float(row["cash_in"]),
            "cash_out": money_float(row["cash_out"]),
            "internal_in": money_float(row["internal_in"]),
            "internal_out": money_float(row["internal_out"]),
            "ending": money_float(ending),
        })
    return {
        "status": status,
        "scope": PERIOD_SCOPE,
        "scope_label": "Selama periode",
        "label": "Pergerakan kas",
        "formal_cash_flow_statement": False,
        "source": "journal_lines.account_type_BANK",
        "cash_in": number(external_in),
        "cash_out": number(external_out),
        "net_change": number(money(external_in - external_out)),
        "internal_transfer_volume": money_float(internal_volume) if bank_ids else None,
        "accounts": account_rows,
        "blocker_code": blocker,
        "detail": CASH_NOTE if blocker is None else f"{CASH_NOTE} Ada reversal yang belum selesai.",
        "note": CASH_NOTE,
    }


async def _trend(db, start: str, end: str, accounts: Dict[str, dict], performance: dict) -> dict:
    if performance.get("status") == "error":
        return {"status": "error", "grain": None, "points": [], "detail": performance.get("detail")}
    if performance.get("status") == "empty":
        return {"status": "empty", "grain": _grain_for(start, end), "points": [], "detail": "Tidak ada jurnal pada periode ini."}
    grain = _grain_for(start, end)
    keys = _bucket_keys(start, end, grain)
    blank = {"revenue": Decimal("0.00"), "cogs": Decimal("0.00"), "expense": Decimal("0.00"), "other_income": Decimal("0.00"), "other_expense": Decimal("0.00")}
    buckets = {key: dict(blank) for key in keys}
    journals = await _legitimate_journals(db, {"date": {"$gte": start, "$lte": end}})
    by_id = {row["id"]: row for row in journals}
    income_ids = [account_id for account_id, row in accounts.items() if row.get("statement") == "INCOME_STATEMENT"]
    lines = await _lines_for(db, list(by_id), income_ids)
    for line in lines:
        account = accounts.get(str(line.get("account_id") or ""))
        journal = by_id.get(str(line.get("journal_id") or ""))
        if not account or not journal:
            continue
        group = account.get("report_group")
        key = _bucket_of(str(journal.get("date") or ""), grain)
        slot = buckets.get(key)
        if slot is None or group not in slot:
            continue
        slot[group] = money(slot[group] + _signed_income(money(line.get("debit")), money(line.get("credit")), group))
    points = []
    for key in keys:
        slot = buckets[key]
        net = money(slot["revenue"] - slot["cogs"] - slot["expense"] + slot["other_income"] - slot["other_expense"])
        points.append({
            "period": key,
            "revenue": money_float(slot["revenue"]),
            "cogs": money_float(slot["cogs"]),
            "net_profit": money_float(net),
        })
    status = "zero" if performance.get("status") == "zero" else ("incomplete" if performance.get("blocker_code") else "complete")
    return {
        "status": status,
        "grain": grain,
        "source": "profit_loss.journal_lines",
        "points": points,
        "blocker_code": performance.get("blocker_code"),
        "detail": performance.get("detail"),
    }


def _composition(performance: dict) -> dict:
    if performance.get("status") == "error":
        return {"status": "error", "items": [], "detail": performance.get("detail")}
    if performance.get("status") == "empty":
        return {"status": "empty", "items": [], "detail": "Tidak ada jurnal pada periode ini."}
    items = [
        row for row in (performance.get("expense_items") or [])
        if money(row.get("amount")) != 0
    ]
    status = "zero" if not items else ("incomplete" if performance.get("blocker_code") else "complete")
    return {
        "status": status,
        "source": "profit_loss.expense_group",
        "items": items,
        "blocker_code": performance.get("blocker_code"),
        "detail": "Beban yang sudah masuk HPP tidak dimasukkan ke komposisi beban operasional." if status != "zero" else "Nol tercatat.",
    }


def _aging_side(summary: Optional[dict], error: Optional[str]) -> dict:
    if error:
        return {"status": "error", "detail": error, "buckets": []}
    if not summary:
        return {"status": "unavailable", "detail": "Belum tersedia", "buckets": []}
    buckets = [
        {"key": "not_due", "label": "Belum jatuh tempo", "amount": summary.get("not_due")},
        {"key": "d1_30", "label": "1–30 hari", "amount": summary.get("d1_30")},
        {"key": "d31_60", "label": "31–60 hari", "amount": summary.get("d31_60")},
        {"key": "d61_90", "label": "61–90 hari", "amount": summary.get("d61_90")},
        {"key": "over_90", "label": "> 90 hari", "amount": summary.get("over_90")},
    ]
    total = sum((money(row["amount"]) for row in buckets), Decimal("0.00"))
    return {
        "status": "zero" if total == 0 else "complete",
        "historical_as_of": False,
        "label": AGING_LABEL,
        "buckets": buckets,
        "detail": summary.get("disclaimer") or summary.get("reconciliation_note"),
    }


def _difference(summary: dict, gl_key: str, sub_key: str) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    rows = summary.get("account_breakdown") or []
    if not rows and summary.get(sub_key) is None:
        return None, None, None
    subledger = money(summary.get(sub_key))
    gl_total = money(sum((money(row.get(gl_key)) for row in rows), Decimal("0.00")))
    return money_float(subledger), money_float(gl_total), money_float(money(gl_total - subledger))


def _priority_rows(items: Sequence[dict], *, party_key: str, href: Optional[str]) -> List[dict]:
    ranked = list(items or [])
    ranked.sort(key=lambda row: (
        0 if int(row.get("days_overdue") or 0) > 0 else 1,
        -int(row.get("days_overdue") or 0),
        -float(row.get("accounting_outstanding") or 0),
        str(row.get("due_date") or ""),
        str(row.get("invoice_number") or ""),
    ))
    visible = ranked[:LIST_LIMIT]
    out = []
    for row in visible:
        overdue = int(row.get("days_overdue") or 0)
        out.append({
            "party": row.get(party_key),
            "document": row.get("invoice_number") or row.get("invoice_id"),
            "invoice_id": row.get("invoice_id"),
            "due_date": row.get("due_date"),
            "outstanding": row.get("accounting_outstanding"),
            "days_overdue": overdue,
            "status": "Terlambat" if overdue > 0 else "Belum jatuh tempo",
            "href": href,
        })
    return out


async def _subledger(db, *, kind: str, permissions: set) -> dict:
    if kind == "receivable":
        summary = await get_accounts_receivable_summary(db)
        aging = await get_accounts_receivable_aging(db)
        sub_key = "total_accounting_ar"
        gl_key = "gl_arec_balance"
        party = "customer_name"
        href = _href(permissions, "/buku-besar/piutang-usaha", "accounts_receivable.view", "nav.ledger.accounts_receivable")
        source = "accounts_receivable.current_state"
    else:
        summary = await get_accounts_payable_summary(db)
        aging = await get_accounts_payable_aging(db)
        sub_key = "total_accounting_ap"
        gl_key = "gl_apay_balance"
        party = "supplier_name"
        href = _href(permissions, "/buku-besar/hutang-usaha", "accounts_payable.view", "nav.ledger.accounts_payable")
        source = "accounts_payable.current_state"
    subledger, gl_balance, difference = _difference(summary, gl_key, sub_key)
    items = _priority_rows(aging.get("items") or [], party_key=party, href=href)
    status = "zero" if money(subledger) == 0 and not items else "complete"
    note = summary.get("reconciliation_note")
    if difference not in (None, 0, 0.0):
        note = f"{note or 'Subledger dan GL berbeda.'} Selisih tidak dipaksa menjadi sama."
    return {
        "status": status,
        "source": source,
        "as_of_label": AGING_LABEL,
        "historical_as_of": False,
        "total_outstanding": subledger,
        "gl_balance": gl_balance,
        "difference": difference,
        "difference_note": note,
        "items": items,
        "item_total": int(aging.get("total") or len(aging.get("items") or [])),
        "list_limit": LIST_LIMIT,
        "aging": _aging_side(summary, None),
        "detail": "Daftar mengikuti faktur, alokasi, pembatalan, dan reversal pada service piutang/utang. Kondisi saat ini, bukan saldo historis tanggal filter.",
    }


async def _document_rows(db, collection: str, query: dict, date_field: str) -> List[Tuple[str, Any]]:
    rows: List[Tuple[str, Any]] = []
    cursor = db[collection].find(query, {"_id": 0, "id": 1, date_field: 1})
    async for doc in cursor:
        doc_id = str(doc.get("id") or "").strip()
        if doc_id:
            rows.append((doc_id, doc.get(date_field)))
    return rows


async def _uncovered(db, collection: str, source_type: str, query: dict, date_field: str, effective: Optional[str]) -> Tuple[List[str], List[str]]:
    rows = await _document_rows(db, collection, query, date_field)
    if not rows:
        return [], []
    covered = set()
    ids = [doc_id for doc_id, _raw in rows]
    for batch in _chunk(ids, JOURNAL_ID_BATCH_SIZE):
        cursor = db[JOURNAL_ENTRIES].find(
            {"source_type": source_type, "source_id": {"$in": list(batch)}, "reversal_of": None},
            {"_id": 0, "source_id": 1, "status": 1},
        )
        async for journal in cursor:
            if journal_is_legitimate(journal):
                covered.add(str(journal.get("source_id") or ""))
    pending = [(doc_id, raw) for doc_id, raw in rows if doc_id not in covered]
    if not effective:
        return [doc_id for doc_id, _raw in pending], []
    action: List[str] = []
    historical: List[str] = []
    for doc_id, raw in pending:
        business = wib_business_date(raw)
        if business and business < effective:
            historical.append(doc_id)
        else:
            action.append(doc_id)
    return action, historical


def _control(key: str, label: str, count: int, *, kind: str, href: Optional[str], status: str, detail: Optional[str] = None) -> dict:
    return {
        "key": key,
        "label": label,
        "count": count,
        "kind": kind,
        "href": href if count else href,
        "status": status,
        "detail": detail,
    }


def _href(perms: set, path: str, *keys: str) -> Optional[str]:
    if not keys or any(key in perms for key in keys):
        return path
    return None


def _href_all(perms: set, path: str, *keys: str) -> Optional[str]:
    """Link only when every gate of the destination page is present."""
    if keys and all(key in perms for key in keys):
        return path
    return None


async def _controls(db, perms: set, book: Optional[dict], book_error: Optional[str], recon: Optional[dict], recon_error: Optional[str]) -> dict:
    settings = await get_purchase_accounting_settings(db)
    effective = str(settings.get("inventory_accounting_effective_date") or "").strip() or None
    items: List[dict] = []
    historical_count = 0
    try:
        if recon_error:
            raise RuntimeError(recon_error)
        pending = (recon or {}).get("pending") or {}
        gr_count = int(((pending.get("active_gr_candidates_without_journal") or {}).get("count")) or 0)
        pre_count = int(((pending.get("pre_effective_legacy") or {}).get("count")) or 0)
        cogs_count = int(((pending.get("pending_cogs") or {}).get("count")) or 0) + int(((pending.get("waiting_earlier_issue") or {}).get("count")) or 0)
        blocked_count = int(((pending.get("blocked_cogs") or {}).get("count")) or 0)
        recovery_count = int(((pending.get("accounting_recovery_required") or {}).get("count")) or 0)
        historical_count += pre_count
        items.append(_control(
            "goods_receipts_unposted",
            "Penerimaan barang belum diposting",
            gr_count,
            kind="action",
            href=_href(perms, "/pembelian/penerimaan-barang", "purchase_receipts.view", "nav.purchasing.receipts"),
            status="incomplete" if gr_count else "complete",
            detail="Antrean dari rekonsiliasi persediaan. Penerimaan sebelum tanggal efektif tidak dihitung di sini.",
        ))
        items.append(_control(
            "cogs_unposted",
            "Pengiriman yang HPP-nya belum diposting",
            cogs_count,
            kind="action",
            href=_href(perms, "/persediaan/nilai-persediaan", "inventory_valuation.view", "nav.inventory.inventory_value"),
            status="incomplete" if cogs_count else "complete",
        ))
        recon_status = str((recon or {}).get("reconciliation_status") or "")
        differ = recon_status in {"DIFFERENCE", "BLOCKED"} or blocked_count > 0
        items.append(_control(
            "inventory_reconciliation",
            "Rekonsiliasi persediaan blocked atau berbeda",
            blocked_count + (1 if recon_status in {"DIFFERENCE", "BLOCKED"} else 0),
            kind="action",
            href=_href(perms, "/persediaan/nilai-persediaan?tab=ledger-recon", "inventory_reconciliation.view"),
            status="incomplete" if differ else "complete",
            detail=f"Status rekonsiliasi: {recon_status or 'tidak diketahui'}.",
        ))
        items.append(_control(
            "inventory_recovery",
            "Opening atau penerimaan membutuhkan recovery",
            recovery_count,
            kind="action",
            href=_href_all(
                perms,
                "/pembelian/penerimaan-barang",
                "nav.purchasing.receipts",
                "purchase_receipts.view",
            ),
            status="incomplete" if recovery_count else "complete",
        ))
    except Exception as exc:
        items.append(_control(
            "inventory_reconciliation",
            "Rekonsiliasi persediaan",
            0,
            kind="action",
            href=None,
            status="error",
            detail=str(exc) or "Gagal memuat rekonsiliasi",
        ))

    queues = (
        ("purchase_invoices_unposted", "Faktur pembelian belum diposting", "purchase_invoices", "purchase_invoice", {"cancelled": {"$ne": True}}, "invoice_date", "/pembelian/faktur-pembelian", ("purchase_invoices.view", "nav.purchasing.invoices"), True),
        ("purchase_payments_unposted", "Pembayaran supplier belum diposting", "purchase_payments", "purchase_payment", {"cancelled": {"$ne": True}, "settlement_status": "PAID"}, "payment_date", "/pembelian/pembayaran-pembelian", ("purchase_payments.view", "nav.purchasing.payments"), True),
        ("sales_invoices_unposted", "Faktur penjualan belum diposting", "sales_invoices", "sales_invoice", {"financial_status": "finalized", "cancelled": {"$ne": True}}, "invoice_date", "/penjualan/faktur-penjualan", ("sales.invoice.view", "nav.sales.sales_invoice"), False),
        ("sales_receipts_unposted", "Penerimaan customer belum diposting", "sales_receipts", "sales_receipt", {"cancelled": {"$ne": True}, "financial_status": {"$nin": ["cancelled", "reversed"]}}, "receipt_date", "/penjualan/penerimaan-penjualan", ("sales.receipt.view", "nav.sales.sales_receipt"), False),
    )
    for key, label, collection, source_type, query, date_field, path, perm_keys, split_effective in queues:
        try:
            action, historical = await _uncovered(
                db, collection, source_type, query, date_field, effective if split_effective else None,
            )
            historical_count += len(historical)
            items.append(_control(
                key,
                label,
                len(action),
                kind="action",
                href=_href(perms, path, *perm_keys),
                status="incomplete" if action else "complete",
                detail="Dokumen sebelum tanggal efektif dipisah dari antrean." if split_effective else None,
            ))
        except Exception as exc:
            items.append(_control(key, label, 0, kind="action", href=None, status="error", detail=str(exc) or "Gagal memuat"))

    opening_count = 0
    opening_documents = 0
    opening_status = "unavailable"
    opening_detail = "Belum ada saldo awal finansial."
    try:
        opening_documents = await db.financial_inventory_openings.count_documents({
            "status": {"$in": ["NEEDS_RECOVERY", "BLOCKED", "BUILDING"]},
        })
        opening_count = opening_documents
        if book_error:
            opening_status = "error"
            opening_detail = book_error
        elif isinstance(book, dict) and book.get("reconciliation_status") not in (None, "MATCH"):
            opening_status = "incomplete"
            opening_detail = "Opening atau identitas buku persediaan belum lengkap. Dashboard tidak menjalankan recovery."
            if opening_count == 0 and book.get("blockers"):
                opening_count = len(book.get("blockers") or [])
        elif opening_count:
            opening_status = "incomplete"
            opening_detail = "Ada opening yang membutuhkan pemulihan."
        else:
            opening_status = "complete"
            opening_detail = None
    except Exception as exc:
        opening_status = "error"
        opening_detail = str(exc) or "Gagal memuat opening"
    if opening_documents:
        opening_tab = "opening"
    else:
        opening_tab = "recon"
    items.append(_control(
        "opening_incomplete",
        "Opening belum lengkap atau membutuhkan recovery",
        opening_count,
        kind="action",
        href=_href_all(
            perms,
            f"/persediaan/nilai-persediaan?tab={opening_tab}",
            "nav.inventory.inventory_value",
            "inventory_valuation.view",
        ),
        status=opening_status,
        detail=opening_detail,
    ))
    try:
        blocker = await reversal_incomplete_blocker(db, ({},))
    except Exception as exc:
        blocker = None
        items.append(_control(
            "reversal_incomplete",
            "Jurnal atau reversal belum selesai",
            0,
            kind="action",
            href=None,
            status="error",
            detail=str(exc) or "Gagal memuat jurnal",
        ))
    else:
        items.append(_control(
            "reversal_incomplete",
            "Jurnal atau reversal belum selesai",
            1 if blocker else 0,
            kind="action",
            href=_href(perms, "/buku-besar/jurnal-umum", "general_journal.view", "nav.ledger.general_journal"),
            status="incomplete" if blocker else "complete",
            detail="Reversal incomplete dari journal_core. Jurnal sumber yang masih sah tetap tampil di laporan.",
        ))
    items.append(_control(
        "pre_effective",
        "Transaksi sebelum tanggal efektif",
        historical_count,
        kind="historical",
        href=None,
        status="complete",
        detail="Dipisahkan dari antrean yang perlu diproses. Bukan kesalahan posting.",
    ))
    action_open = any(row.get("kind") == "action" and row.get("status") in {"incomplete", "error"} for row in items)
    return {
        "status": "incomplete" if action_open else "complete",
        "read_only": True,
        "items": items,
        "detail": "Dashboard hanya membaca. Tidak ada posting, recovery, reversal, atau aktivasi tanggal.",
    }


def _completeness(performance: dict, position: dict, cash: dict, controls: dict) -> dict:
    sections = (performance, position, cash, controls)
    if any(row.get("status") == "error" for row in sections):
        status = "incomplete"
        detail = "Sebagian sumber gagal dimuat. Bagian yang gagal tidak ditampilkan sebagai nol."
    elif any(row.get("blocker_code") for row in (performance, position, cash)):
        status = "incomplete"
        detail = "Ada reversal yang belum selesai. Angka yang tampil adalah saldo tercatat, bukan laporan final."
    elif controls.get("status") == "incomplete" or (position.get("inventory_book") or {}).get("status") == "incomplete":
        status = "incomplete"
        detail = "Pembukuan belum lengkap. Angka yang tampil adalah saldo tercatat, bukan laporan final."
    elif performance.get("status") == "empty" and position.get("status") == "unavailable":
        status = "unavailable"
        detail = "Belum ada jurnal pembukuan pada rentang dan tanggal akhir ini."
    else:
        status = "complete"
        detail = "Sumber yang terbaca lengkap untuk periode ini. Tetap bukan pengganti tutup buku."
    return {"status": status, "detail": detail}


async def get_financial_dashboard(db, *, start_date: str, end_date: str, permissions: Optional[Iterable[str]] = None) -> dict:
    """GET-safe company dashboard. One failed section does not zero the others."""
    start, end = _parse_range(start_date, end_date)
    perms = {str(key) for key in (permissions or []) if key}
    accounts = _account_index(await load_statement_accounts(db))

    try:
        performance = await _performance(db, start, end)
    except Exception as exc:
        performance = _section_error("profit_loss.journal_lines", exc)
    try:
        book = await build_financial_book_reconciliation(db)
        book_error = None
    except Exception as exc:
        book = None
        book_error = str(exc) or "Gagal membaca rekonsiliasi buku"
    try:
        position = await _position(db, end, book, book_error)
    except Exception as exc:
        position = _section_error("balance_sheet.journal_lines", exc)
    try:
        cash = await _cash_movement(db, start, end, accounts)
    except Exception as exc:
        cash = _section_error("journal_lines.account_type_BANK", exc)
    try:
        trend = await _trend(db, start, end, accounts, performance if isinstance(performance, dict) else {})
    except Exception as exc:
        trend = _section_error("profit_loss.journal_lines", exc)
    composition = _composition(performance if isinstance(performance, dict) else {})

    try:
        receivables = await _subledger(db, kind="receivable", permissions=perms)
        receivable_error = None
    except Exception as exc:
        receivables = _section_error("accounts_receivable.current_state", exc)
        receivable_error = str(exc) or "Gagal memuat piutang"
    try:
        payables = await _subledger(db, kind="payable", permissions=perms)
        payable_error = None
    except Exception as exc:
        payables = _section_error("accounts_payable.current_state", exc)
        payable_error = str(exc) or "Gagal memuat utang"

    try:
        recon = await get_inventory_reconciliation_summary(db)
        recon_error = None
    except Exception as exc:
        recon = None
        recon_error = str(exc) or "Gagal membaca rekonsiliasi persediaan"
    try:
        controls = await _controls(db, perms, book, book_error, recon, recon_error)
    except Exception as exc:
        controls = _section_error("posting_queues", exc)
        controls["items"] = []

    aging = {
        "label": AGING_LABEL,
        "historical_as_of": False,
        "source": "accounts_receivable.current_state+accounts_payable.current_state",
        "detail": "Umur piutang dan utang adalah kondisi saat ini, bukan saldo historis pada tanggal akhir filter.",
        "receivable": receivables.get("aging") if isinstance(receivables, dict) and receivables.get("aging") else _aging_side(None, receivable_error),
        "payable": payables.get("aging") if isinstance(payables, dict) and payables.get("aging") else _aging_side(None, payable_error),
    }
    completeness = _completeness(
        performance if isinstance(performance, dict) else {},
        position if isinstance(position, dict) else {},
        cash if isinstance(cash, dict) else {},
        controls if isinstance(controls, dict) else {},
    )
    return {
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Jakarta",
        "loaded_at": datetime.now(WIB).isoformat(timespec="seconds"),
        "writes": 0,
        "read_only": True,
        "branch_filter": None,
        "completeness": completeness,
        "performance": performance,
        "position": position,
        "cash_movement": cash,
        "charts": {
            "trend": trend,
            "expense_composition": composition,
            "aging": aging,
        },
        "receivables": receivables,
        "payables": payables,
        "controls": controls,
    }
