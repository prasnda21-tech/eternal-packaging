"""Accounts Payable read model from posted journals.

Zero-write. Current-state only. Aging reference date is NOT historical as-of.
Accounting AP is derived from journal_entries + journal_lines, not from
purchase_invoices.outstanding_amount.

Public liability amounts use credit - debit so normal AP is positive.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.general_ledger import SOURCE_LABELS, _journal_is_legitimate
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, STATUS_POSTED
from services.purchase_common import is_cancelled
from services.purchase_payments import is_settlement_paid

PERM_VIEW = "accounts_payable.view"

SOURCE_INVOICE = "purchase_invoice"
SOURCE_INVOICE_REVERSAL = "purchase_invoice_reversal"
SOURCE_PAYMENT = "purchase_payment"
SOURCE_PAYMENT_REVERSAL = "purchase_payment_reversal"

PURCHASE_AP_SOURCES = frozenset({
    SOURCE_INVOICE,
    SOURCE_INVOICE_REVERSAL,
    SOURCE_PAYMENT,
    SOURCE_PAYMENT_REVERSAL,
})

BUCKET_NOT_DUE = "NOT_DUE"
BUCKET_1_30 = "1_30"
BUCKET_31_60 = "31_60"
BUCKET_61_90 = "61_90"
BUCKET_OVER_90 = "OVER_90"
AGING_BUCKETS = (BUCKET_NOT_DUE, BUCKET_1_30, BUCKET_31_60, BUCKET_61_90, BUCKET_OVER_90)

ROUNDING_TOLERANCE = Decimal("0.01")
IN_BATCH = 400
UNKNOWN_SUPPLIER_KEY = "unknown:"

WARN_PAYMENT_AP_ACCOUNT_MISMATCH = "PAYMENT_AP_ACCOUNT_MISMATCH"
WARN_NEGATIVE_ACCOUNTING_AP = "NEGATIVE_ACCOUNTING_AP"
WARN_OVER_APPLIED_ACCOUNTING_PAYMENT = "OVER_APPLIED_ACCOUNTING_PAYMENT"
WARN_MULTIPLE_INVOICE_APAY_ACCOUNTS = "MULTIPLE_INVOICE_APAY_ACCOUNTS"
WARN_MULTIPLE_PAYMENT_APAY_ACCOUNTS = "MULTIPLE_PAYMENT_APAY_ACCOUNTS"
WARN_MISSING_PURCHASE_INVOICE_DOCUMENT = "missing_purchase_invoice_document"
WARN_MISSING_PURCHASE_PAYMENT_DOCUMENT = "missing_purchase_payment_document"

AGING_DISCLAIMER = (
    "Hutang Accounting dihitung dari jurnal yang sudah diposting. "
    "Tanggal Acuan Aging hanya menentukan umur hutang, bukan historical accounting as-of."
)
STATEMENT_DISCLAIMER = (
    "Riwayat mengikuti tanggal jurnal. Sistem belum menyediakan historical accounting "
    "as-of sebelum reversal yang dibukukan pada tanggal asli."
)
STATEMENT_SHORT_NOTE = "Statement mengikuti tanggal jurnal."
GL_DIFF_LABEL = "Selisih non-Faktur / jurnal lain"
RECONCILIATION_SCOPE = "all_current_ap"
RECONCILIATION_NOTE = (
    "Rekonsiliasi GL menggunakan seluruh Hutang Accounting saat ini dan tidak "
    "dipersempit oleh filter supplier atau aging."
)
WIB = timezone(timedelta(hours=7))

INTEGRITY_WARNING_LABELS = {
    WARN_PAYMENT_AP_ACCOUNT_MISMATCH: "Akun AP pembayaran tidak sesuai faktur",
    WARN_NEGATIVE_ACCOUNTING_AP: "Saldo Accounting AP negatif",
    WARN_OVER_APPLIED_ACCOUNTING_PAYMENT: "Pembayaran accounting melebihi nilai AP",
    WARN_MULTIPLE_INVOICE_APAY_ACCOUNTS: "Faktur menggunakan lebih dari satu akun AP",
    WARN_MULTIPLE_PAYMENT_APAY_ACCOUNTS: "Pembayaran menggunakan lebih dari satu akun AP",
    WARN_MISSING_PURCHASE_INVOICE_DOCUMENT: "Jurnal faktur tidak memiliki dokumen Faktur Pembelian",
    WARN_MISSING_PURCHASE_PAYMENT_DOCUMENT: "Jurnal pembayaran tidak memiliki dokumen Pembayaran Pembelian",
}

INDEX_SPECS = (
    ("purchase_invoices", "invoice_date"),
    ("purchase_invoices", "cancelled"),
    ("purchase_payments", "payment_date"),
    ("purchase_payments", "settlement_status"),
    ("purchase_payments", "purchase_invoice_id"),
)


class AccountsPayableValidationError(ValueError):
    pass


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def _money_float(d: Decimal) -> float:
    return float(d)


def _source_label(source_type: Optional[str]) -> str:
    st = str(source_type or "")
    return SOURCE_LABELS.get(st, st or "—")


def normalize_supplier_name(raw: Any) -> str:
    return re.sub(r"\s+", " ", str(raw or "").strip()).lower()


def supplier_identity_key(raw: Any) -> str:
    norm = normalize_supplier_name(raw)
    if not norm:
        return UNKNOWN_SUPPLIER_KEY
    return f"name:{norm}"


def supplier_display_name(raw: Any) -> str:
    text = str(raw or "").strip()
    return text or "Supplier tidak diketahui"


def wib_calendar_date(now: Optional[datetime] = None) -> date:
    instant = now if now is not None else datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(WIB).date()


def wib_month_start(now: Optional[datetime] = None) -> str:
    day = wib_calendar_date(now)
    return f"{day.year:04d}-{day.month:02d}-01"


def _today_iso(now: Optional[datetime] = None) -> str:
    return wib_calendar_date(now).isoformat()


def parse_iso_date(raw: Any, *, field: str, required: bool = False) -> Optional[str]:
    text = str(raw or "").strip()[:10]
    if not text:
        if required:
            raise AccountsPayableValidationError(f"{field} wajib diisi")
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise AccountsPayableValidationError(f"{field} tidak valid") from exc
    return text


def aging_bucket_for(due_iso: str, reference_iso: str) -> Tuple[str, int]:
    due = date.fromisoformat(due_iso)
    ref = date.fromisoformat(reference_iso)
    if due >= ref:
        return BUCKET_NOT_DUE, 0
    days = (ref - due).days
    if days <= 30:
        return BUCKET_1_30, days
    if days <= 60:
        return BUCKET_31_60, days
    if days <= 90:
        return BUCKET_61_90, days
    return BUCKET_OVER_90, days


def _empty_buckets() -> Dict[str, Decimal]:
    return {key: Decimal("0.00") for key in AGING_BUCKETS}


def _is_apay(account: Optional[dict]) -> bool:
    return str((account or {}).get("account_type") or "").strip().upper() == "APAY"


async def _cursor_all(cursor) -> List[dict]:
    rows: List[dict] = []
    async for doc in cursor:
        rows.append(doc)
    return rows


async def _find_in(db, collection: str, field: str, values: Sequence[str], projection: Optional[dict] = None) -> List[dict]:
    """Batched $in loader. No to_list cap — totals must not silently truncate."""
    ids = [str(v) for v in values if v]
    if not ids:
        return []
    proj = {"_id": 0}
    if projection:
        proj.update(projection)
    out: List[dict] = []
    for i in range(0, len(ids), IN_BATCH):
        chunk = ids[i:i + IN_BATCH]
        docs = await _cursor_all(db[collection].find({field: {"$in": chunk}}, proj))
        out.extend(docs)
    return out


async def ensure_indexes(db) -> List[str]:
    """Additive report indexes only. Never drops or replaces existing indexes."""
    created: List[str] = []
    for coll_name, field in INDEX_SPECS:
        name = await db[coll_name].create_index(field)
        created.append(f"{coll_name}.{name}")
    return created


def _invoice_due(invoice: dict) -> Tuple[str, bool]:
    due = str((invoice or {}).get("due_date") or "").strip()[:10]
    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
            return due, False
        except ValueError:
            pass
    fallback = str((invoice or {}).get("invoice_date") or "").strip()[:10]
    if fallback:
        try:
            datetime.strptime(fallback, "%Y-%m-%d")
            return fallback, True
        except ValueError:
            pass
    return "", True


def _analyze_invoice_ap(
    lines: Sequence[dict],
    accounts: Dict[str, dict],
) -> Tuple[Optional[dict], Decimal, Set[str], List[str], Dict[str, Decimal]]:
    """APAY credit analysis.

    Exactly one APAY account (one or more credit lines): that account + sum.
    Multiple APAY accounts: do not pick an arbitrary first account.
    """
    by_account: Dict[str, Decimal] = {}
    account_docs: Dict[str, dict] = {}
    for ln in lines:
        debit = _money(ln.get("debit"))
        credit = _money(ln.get("credit"))
        if credit <= 0 or debit > 0:
            continue
        acc = accounts.get(str(ln.get("account_id") or ""))
        if not _is_apay(acc):
            continue
        aid = str(acc.get("id") or "")
        if not aid:
            continue
        by_account[aid] = by_account.get(aid, Decimal("0.00")) + credit
        account_docs[aid] = acc
    warnings: List[str] = []
    if not by_account:
        return None, Decimal("0.00"), set(), warnings, {}
    account_ids = set(by_account)
    total = sum(by_account.values(), Decimal("0.00"))
    if len(by_account) == 1:
        aid = next(iter(by_account))
        return account_docs[aid], by_account[aid], account_ids, warnings, dict(by_account)
    warnings.append(WARN_MULTIPLE_INVOICE_APAY_ACCOUNTS)
    return None, total, account_ids, warnings, dict(by_account)


def _analyze_payment_ap(
    lines: Sequence[dict],
    accounts: Dict[str, dict],
    invoice_ap_ids: Set[str],
) -> Tuple[Decimal, Dict[str, Decimal], Set[str], List[str]]:
    """Payment APAY debits reduce invoice AP only on the same actual AP account(s).

    Never fall back to an unmatched APAY debit or the payment document amount.
    """
    matched = Decimal("0.00")
    unmatched = Decimal("0.00")
    matched_by: Dict[str, Decimal] = {}
    unmatched_ids: Set[str] = set()
    apay_accounts: Set[str] = set()
    for ln in lines:
        debit = _money(ln.get("debit"))
        credit = _money(ln.get("credit"))
        if debit <= 0 or credit > 0:
            continue
        aid = str(ln.get("account_id") or "")
        if not _is_apay(accounts.get(aid)):
            continue
        apay_accounts.add(aid)
        if aid in invoice_ap_ids:
            matched += debit
            matched_by[aid] = matched_by.get(aid, Decimal("0.00")) + debit
        else:
            unmatched += debit
            unmatched_ids.add(aid)
    warnings: List[str] = []
    if len(apay_accounts) > 1:
        warnings.append(WARN_MULTIPLE_PAYMENT_APAY_ACCOUNTS)
    if unmatched > ROUNDING_TOLERANCE:
        warnings.append(WARN_PAYMENT_AP_ACCOUNT_MISMATCH)
    return matched, matched_by, unmatched_ids, warnings


def _normalize_outstanding(raw: Decimal) -> Tuple[Decimal, List[str]]:
    warnings: List[str] = []
    if raw < -ROUNDING_TOLERANCE:
        warnings.append(WARN_NEGATIVE_ACCOUNTING_AP)
        return raw, warnings
    if raw < 0:
        return Decimal("0.00"), warnings
    return raw, warnings


def _journal_ap_movement(lines: Sequence[dict], accounts: Dict[str, dict], ap_account_id: Optional[str] = None) -> Tuple[Decimal, Decimal]:
    """Return (ap_increase, ap_decrease) using liability convention credit/debit."""
    increase = Decimal("0.00")
    decrease = Decimal("0.00")
    for ln in lines:
        aid = str(ln.get("account_id") or "")
        acc = accounts.get(aid)
        if ap_account_id:
            if aid != ap_account_id:
                continue
        elif not _is_apay(acc):
            continue
        increase += _money(ln.get("credit"))
        decrease += _money(ln.get("debit"))
    return increase, decrease


def _apply_invoice_filters(row: dict, filters: dict) -> bool:
    supplier_key = str(filters.get("supplier_key") or "").strip()
    if supplier_key and str(row.get("supplier_key") or "") != supplier_key:
        return False
    ap_account_id = str(filters.get("ap_account_id") or "").strip()
    if ap_account_id and str(row.get("actual_ap_account_id") or "") != ap_account_id:
        return False
    aging_bucket = str(filters.get("aging_bucket") or "").strip()
    if aging_bucket and str(row.get("aging_bucket") or "") != aging_bucket:
        return False
    needle = str(filters.get("q") or "").strip().lower()
    if needle:
        blob = " ".join([
            str(row.get("invoice_number") or ""),
            str(row.get("supplier_name") or ""),
            str(row.get("po_number") or ""),
            str(row.get("actual_ap_account_code") or ""),
            str(row.get("actual_ap_account_name") or ""),
        ]).lower()
        if needle not in blob:
            return False
    return True


def _public_invoice(row: dict) -> dict:
    return {
        "invoice_id": row.get("invoice_id"),
        "invoice_number": row.get("invoice_number"),
        "invoice_date": row.get("invoice_date"),
        "due_date": row.get("due_date"),
        "due_date_fallback": bool(row.get("due_date_fallback")),
        "purchase_order_id": row.get("purchase_order_id"),
        "po_number": row.get("po_number"),
        "supplier_key": row.get("supplier_key"),
        "supplier_name": row.get("supplier_name"),
        "accounting_status": row.get("accounting_status"),
        "payment_status": row.get("payment_status"),
        "actual_ap_account_id": row.get("actual_ap_account_id"),
        "actual_ap_account_code": row.get("actual_ap_account_code"),
        "actual_ap_account_name": row.get("actual_ap_account_name"),
        "original_ap_amount": _money_float(row["original_ap_amount"]),
        "posted_payment_amount": _money_float(row["posted_payment_amount"]),
        "pending_payment_amount": _money_float(row["pending_payment_amount"]),
        "accounting_outstanding": _money_float(row["accounting_outstanding"]),
        "accounting_outstanding_raw": _money_float(row.get("accounting_outstanding_raw", row["accounting_outstanding"])),
        "operational_outstanding": _money_float(row["operational_outstanding"]),
        "days_overdue": row.get("days_overdue"),
        "aging_bucket": row.get("aging_bucket"),
        "document_mismatch": bool(row.get("document_mismatch")),
        "integrity_warning": bool(row.get("integrity_warning")),
        "integrity_warnings": list(row.get("integrity_warnings") or []),
    }


async def _load_accounts(db, account_ids: Iterable[str]) -> Dict[str, dict]:
    docs = await _find_in(db, "ledger_accounts", "id", list({str(i) for i in account_ids if i}))
    return {str(doc.get("id")): doc for doc in docs if doc.get("id")}


async def _load_lines_by_journal(db, journal_ids: Sequence[str]) -> Dict[str, List[dict]]:
    lines = await _find_in(db, JOURNAL_LINES, "journal_id", journal_ids)
    by_jid: Dict[str, List[dict]] = {}
    for ln in lines:
        by_jid.setdefault(str(ln.get("journal_id") or ""), []).append(ln)
    return by_jid


def _document_mismatch(invoice: Optional[dict], journal: dict) -> bool:
    if str(journal.get("status") or "") != STATUS_POSTED:
        return False
    if not invoice:
        return True
    return is_cancelled(invoice)


async def _compute_gl_apay_balances(db, account_ids: Sequence[str]) -> Dict[str, Decimal]:
    """Liability convention: credit - debit. Normal AP is positive."""
    ids = [str(i) for i in account_ids if i]
    if not ids:
        return {}
    lines = await _find_in(db, JOURNAL_LINES, "account_id", ids)
    journal_ids = [str(ln.get("journal_id") or "") for ln in lines if ln.get("journal_id")]
    journals = await _find_in(db, JOURNAL_ENTRIES, "id", journal_ids, {"id": 1, "status": 1})
    legit = {str(j.get("id")) for j in journals if j.get("id") and _journal_is_legitimate(j)}
    balances: Dict[str, Decimal] = {aid: Decimal("0.00") for aid in ids}
    for ln in lines:
        jid = str(ln.get("journal_id") or "")
        if jid not in legit:
            continue
        aid = str(ln.get("account_id") or "")
        if aid not in balances:
            balances[aid] = Decimal("0.00")
        balances[aid] += _money(ln.get("credit")) - _money(ln.get("debit"))
    return balances


async def _collect_missing_purchase_documents(db) -> List[dict]:
    journals = await _cursor_all(
        db[JOURNAL_ENTRIES].find(
            {
                "source_type": {"$in": [SOURCE_INVOICE, SOURCE_PAYMENT]},
                "reversal_of": None,
            },
            {"_id": 0, "id": 1, "source_type": 1, "source_id": 1, "status": 1},
        )
    )
    invoice_ids = [
        str(j.get("source_id") or "")
        for j in journals
        if str(j.get("source_type") or "") == SOURCE_INVOICE and j.get("source_id")
    ]
    payment_ids = [
        str(j.get("source_id") or "")
        for j in journals
        if str(j.get("source_type") or "") == SOURCE_PAYMENT and j.get("source_id")
    ]
    invoices = {str(doc.get("id")) for doc in await _find_in(db, "purchase_invoices", "id", invoice_ids, {"id": 1})}
    payments = {str(doc.get("id")) for doc in await _find_in(db, "purchase_payments", "id", payment_ids, {"id": 1})}
    anomalies: List[dict] = []
    for journal in journals:
        sid = str(journal.get("source_id") or "")
        st = str(journal.get("source_type") or "")
        if not sid:
            continue
        if st == SOURCE_INVOICE and sid not in invoices:
            anomalies.append({
                "code": WARN_MISSING_PURCHASE_INVOICE_DOCUMENT,
                "source_type": st,
                "source_id": sid,
                "journal_id": journal.get("id"),
            })
        elif st == SOURCE_PAYMENT and sid not in payments:
            anomalies.append({
                "code": WARN_MISSING_PURCHASE_PAYMENT_DOCUMENT,
                "source_type": st,
                "source_id": sid,
                "journal_id": journal.get("id"),
            })
    journal_ids = [str(item.get("journal_id") or "") for item in anomalies if item.get("journal_id")]
    if journal_ids:
        lines = await _find_in(db, JOURNAL_LINES, "journal_id", journal_ids)
        account_ids = {str(ln.get("account_id") or "") for ln in lines if ln.get("account_id")}
        accounts = await _load_accounts(db, account_ids)
        by_jid: Dict[str, Set[str]] = {}
        for ln in lines:
            acc = accounts.get(str(ln.get("account_id") or ""))
            if not _is_apay(acc):
                continue
            aid = str(acc.get("id") or "")
            if aid:
                by_jid.setdefault(str(ln.get("journal_id") or ""), set()).add(aid)
        for item in anomalies:
            item["account_ids"] = sorted(by_jid.get(str(item.get("journal_id") or ""), set()))
    return anomalies


def payment_counts_toward_outstanding(payment: Optional[dict]) -> bool:
    return bool(payment) and (not is_cancelled(payment)) and is_settlement_paid(payment)


async def _load_current_invoices(db, *, aging_reference_date: str) -> List[dict]:
    """Current aging from posted primary purchase_invoice journals.

    Collection-batched: invoice journals, invoice docs, payments, payment
    journals, journal lines, and COA are loaded via $in / cursor_all.
    No per-invoice journal/payment/COA queries.
    """
    journals = await _cursor_all(
        db[JOURNAL_ENTRIES].find(
            {"source_type": SOURCE_INVOICE, "reversal_of": None},
            {"_id": 0},
        )
    )
    if not journals:
        return []

    invoice_ids = [str(j.get("source_id") or "") for j in journals if j.get("source_id")]
    journal_ids = [str(j.get("id") or "") for j in journals if j.get("id")]
    invoices = await _find_in(db, "purchase_invoices", "id", invoice_ids)
    invoices_by_id = {str(inv.get("id")): inv for inv in invoices if inv.get("id")}
    lines_by_jid = await _load_lines_by_journal(db, journal_ids)

    payments = await _find_in(db, "purchase_payments", "purchase_invoice_id", invoice_ids)
    payments_by_invoice: Dict[str, List[dict]] = {}
    payment_ids: List[str] = []
    for rec in payments:
        rid = str(rec.get("id") or "")
        iid = str(rec.get("purchase_invoice_id") or "")
        if not rid or not iid:
            continue
        payments_by_invoice.setdefault(iid, []).append(rec)
        payment_ids.append(rid)

    payment_journals: List[dict] = []
    if payment_ids:
        for i in range(0, len(payment_ids), IN_BATCH):
            chunk = payment_ids[i:i + IN_BATCH]
            payment_journals.extend(await _cursor_all(
                db[JOURNAL_ENTRIES].find(
                    {
                        "source_type": SOURCE_PAYMENT,
                        "source_id": {"$in": chunk},
                        "reversal_of": None,
                    },
                    {"_id": 0},
                )
            ))
    payment_journal_by_source = {str(j.get("source_id")): j for j in payment_journals if j.get("source_id")}
    payment_lines_by_jid = await _load_lines_by_journal(
        db, [str(j.get("id")) for j in payment_journals if j.get("id")]
    )

    account_ids: Set[str] = set()
    for lines in list(lines_by_jid.values()) + list(payment_lines_by_jid.values()):
        for ln in lines:
            if ln.get("account_id"):
                account_ids.add(str(ln.get("account_id")))
    accounts = await _load_accounts(db, account_ids)

    rows: List[dict] = []
    for journal in journals:
        invoice_id = str(journal.get("source_id") or "")
        invoice = invoices_by_id.get(invoice_id)
        if not invoice:
            continue
        lines = lines_by_jid.get(str(journal.get("id") or ""), [])
        ap_account, original_ap, invoice_ap_ids, invoice_warnings, invoice_credits = _analyze_invoice_ap(lines, accounts)
        status = str(journal.get("status") or "").strip()
        if status != STATUS_POSTED or original_ap <= 0:
            continue

        ap_account_id = str((ap_account or {}).get("id") or "") or None
        posted_payment = Decimal("0.00")
        pending_payment = Decimal("0.00")
        operational_paid = Decimal("0.00")
        integrity_warnings = list(invoice_warnings)
        purchase_ap_by_account = {aid: amt for aid, amt in invoice_credits.items()}
        unmatched_payment_ap_account_ids: List[str] = []
        for rec in payments_by_invoice.get(invoice_id, []):
            rec_journal = payment_journal_by_source.get(str(rec.get("id") or ""))
            rec_status = str((rec_journal or {}).get("status") or "").strip()
            rec_lines = payment_lines_by_jid.get(str((rec_journal or {}).get("id") or ""), [])
            if rec_status == STATUS_POSTED:
                matched_debit, matched_by, unmatched_ids, rec_warnings = _analyze_payment_ap(
                    rec_lines, accounts, invoice_ap_ids
                )
                posted_payment += matched_debit
                integrity_warnings.extend(rec_warnings)
                for aid, amt in matched_by.items():
                    purchase_ap_by_account[aid] = purchase_ap_by_account.get(aid, Decimal("0.00")) - amt
                unmatched_payment_ap_account_ids.extend(sorted(unmatched_ids))
            if payment_counts_toward_outstanding(rec):
                amount = _money(rec.get("amount"))
                operational_paid += amount
                if rec_status != STATUS_POSTED:
                    pending_payment += amount

        raw_outstanding = original_ap - posted_payment
        accounting_outstanding, outstanding_warnings = _normalize_outstanding(raw_outstanding)
        integrity_warnings.extend(outstanding_warnings)
        if posted_payment > original_ap + ROUNDING_TOLERANCE:
            integrity_warnings.append(WARN_OVER_APPLIED_ACCOUNTING_PAYMENT)
        integrity_warnings = list(dict.fromkeys(integrity_warnings))
        integrity_warning = bool(integrity_warnings)

        if is_cancelled(invoice):
            operational_outstanding = Decimal("0.00")
        else:
            grand = _money(invoice.get("grand_total"))
            operational_outstanding = grand - operational_paid
            if operational_outstanding < 0 and operational_outstanding >= -ROUNDING_TOLERANCE:
                operational_outstanding = Decimal("0.00")

        due_date, due_fallback = _invoice_due(invoice)
        if not due_date:
            due_date = str(invoice.get("invoice_date") or journal.get("date") or aging_reference_date)[:10]
            due_fallback = True
        bucket, days_overdue = aging_bucket_for(due_date, aging_reference_date)

        payment_status = invoice.get("payment_status")
        if not is_cancelled(invoice):
            grand = _money(invoice.get("grand_total"))
            if operational_paid <= 0:
                payment_status = "BELUM DIBAYAR"
            elif operational_paid < grand:
                payment_status = "DP"
            else:
                payment_status = "LUNAS"
        else:
            payment_status = "DIBATALKAN"

        rows.append({
            "invoice_id": invoice_id,
            "invoice_number": invoice.get("invoice_number") or invoice_id,
            "invoice_date": invoice.get("invoice_date") or journal.get("date"),
            "due_date": due_date,
            "due_date_fallback": due_fallback,
            "purchase_order_id": invoice.get("purchase_order_id"),
            "po_number": invoice.get("po_number"),
            "supplier_key": supplier_identity_key(invoice.get("supplier")),
            "supplier_name": supplier_display_name(invoice.get("supplier")),
            "accounting_status": status,
            "payment_status": payment_status,
            "actual_ap_account_id": ap_account_id,
            "actual_ap_account_code": (ap_account or {}).get("code"),
            "actual_ap_account_name": (ap_account or {}).get("name"),
            "original_ap_amount": original_ap,
            "posted_payment_amount": posted_payment,
            "pending_payment_amount": pending_payment,
            "accounting_outstanding": accounting_outstanding,
            "accounting_outstanding_raw": raw_outstanding,
            "operational_outstanding": operational_outstanding,
            "days_overdue": days_overdue,
            "aging_bucket": bucket if accounting_outstanding > 0 else None,
            "document_mismatch": _document_mismatch(invoice, journal),
            "integrity_warning": integrity_warning,
            "integrity_warnings": integrity_warnings,
            "purchase_ap_by_account": purchase_ap_by_account,
            "unmatched_payment_ap_account_ids": list(dict.fromkeys(unmatched_payment_ap_account_ids)),
        })
    return rows


def _integrity_account_ids(invoices: Sequence[dict], missing_docs: Sequence[dict]) -> Set[str]:
    affected: Set[str] = set()
    for row in invoices:
        warnings = row.get("integrity_warnings") or []
        if not warnings:
            continue
        if any(code != WARN_PAYMENT_AP_ACCOUNT_MISMATCH for code in warnings):
            affected.update(str(aid) for aid in (row.get("purchase_ap_by_account") or {}) if aid)
        if WARN_PAYMENT_AP_ACCOUNT_MISMATCH in warnings:
            affected.update(str(aid) for aid in (row.get("unmatched_payment_ap_account_ids") or []) if aid)
    for item in missing_docs:
        affected.update(str(aid) for aid in (item.get("account_ids") or []) if aid)
    return affected


def _aggregate_integrity_anomalies(invoices: Sequence[dict], missing_docs: Sequence[dict]) -> List[dict]:
    items: List[dict] = []
    seen: Set[Tuple[str, str]] = set()
    for row in invoices:
        invoice_id = str(row.get("invoice_id") or "")
        for code in row.get("integrity_warnings") or []:
            key = (str(code), invoice_id)
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "code": code,
                "label": INTEGRITY_WARNING_LABELS.get(code, code),
                "invoice_id": row.get("invoice_id"),
                "invoice_number": row.get("invoice_number"),
                "supplier_name": row.get("supplier_name"),
            })
    for doc in missing_docs:
        code = str(doc.get("code") or "")
        identity = str(doc.get("journal_id") or doc.get("source_id") or "")
        key = (code, identity)
        if not code or key in seen:
            continue
        seen.add(key)
        items.append({
            "code": code,
            "label": INTEGRITY_WARNING_LABELS.get(code, code),
            "journal_id": doc.get("journal_id"),
            "source_type": doc.get("source_type"),
            "source_id": doc.get("source_id"),
        })
    items.sort(key=lambda r: (
        str(r.get("code") or ""),
        str(r.get("invoice_id") or ""),
        str(r.get("journal_id") or ""),
        str(r.get("source_id") or ""),
    ))
    return items


async def _account_breakdown(db, invoices: Sequence[dict], *, anomaly_account_ids: Optional[Set[str]] = None) -> List[dict]:
    purchase_by_account: Dict[str, Decimal] = {}
    for row in invoices:
        by_account = row.get("purchase_ap_by_account") or {}
        if by_account:
            for aid, amt in by_account.items():
                key = str(aid or "")
                if not key:
                    continue
                purchase_by_account[key] = purchase_by_account.get(key, Decimal("0.00")) + _money(amt)
            continue
        aid = str(row.get("actual_ap_account_id") or "")
        if not aid:
            continue
        purchase_by_account[aid] = purchase_by_account.get(aid, Decimal("0.00")) + _money(row.get("accounting_outstanding"))

    apay_docs = await _cursor_all(
        db.ledger_accounts.find({"account_type": "APAY"}, {"_id": 0, "id": 1, "code": 1, "name": 1, "account_type": 1})
    )
    account_ids = {str(doc.get("id")) for doc in apay_docs if doc.get("id")}
    account_ids.update(purchase_by_account.keys())
    accounts = await _load_accounts(db, account_ids)
    gl_balances = await _compute_gl_apay_balances(db, list(account_ids))
    flagged = set(anomaly_account_ids or ())

    breakdown: List[dict] = []
    for aid in sorted(account_ids, key=lambda x: str((accounts.get(x) or {}).get("code") or x)):
        acc = accounts.get(aid) or {}
        purchase_ap = purchase_by_account.get(aid, Decimal("0.00"))
        gl_bal = gl_balances.get(aid, Decimal("0.00"))
        if purchase_ap == 0 and gl_bal == 0:
            continue
        difference = gl_bal - purchase_ap
        breakdown.append({
            "account_id": aid,
            "account_code": acc.get("code"),
            "account_name": acc.get("name"),
            "purchase_ap_balance": _money_float(purchase_ap),
            "gl_apay_balance": _money_float(gl_bal),
            "difference": _money_float(difference),
            "difference_label": GL_DIFF_LABEL,
            "difference_has_integrity_anomaly": (
                aid in flagged and abs(difference) > ROUNDING_TOLERANCE
            ),
        })
    return breakdown


def _aggregate_suppliers(invoices: Sequence[dict]) -> List[dict]:
    grouped: Dict[str, dict] = {}
    for row in invoices:
        key = str(row.get("supplier_key") or "")
        if key not in grouped:
            grouped[key] = {
                "supplier_key": key,
                "supplier_name": row.get("supplier_name"),
                "total_invoiced_ap": Decimal("0.00"),
                "posted_payments": Decimal("0.00"),
                "accounting_outstanding": Decimal("0.00"),
                "pending_payment_amount": Decimal("0.00"),
                "operational_outstanding": Decimal("0.00"),
                "buckets": _empty_buckets(),
                "oldest_due_date": None,
                "invoice_count": 0,
                "overdue_invoice_count": 0,
            }
        g = grouped[key]
        if not g.get("supplier_name") and row.get("supplier_name"):
            g["supplier_name"] = row.get("supplier_name")
        g["total_invoiced_ap"] += _money(row.get("original_ap_amount"))
        g["posted_payments"] += _money(row.get("posted_payment_amount"))
        g["accounting_outstanding"] += _money(row.get("accounting_outstanding"))
        g["pending_payment_amount"] += _money(row.get("pending_payment_amount"))
        g["operational_outstanding"] += _money(row.get("operational_outstanding"))
        g["invoice_count"] += 1
        outstanding = _money(row.get("accounting_outstanding"))
        bucket = row.get("aging_bucket")
        if outstanding > 0 and bucket in g["buckets"]:
            g["buckets"][bucket] += outstanding
            due = str(row.get("due_date") or "")[:10]
            if due and (g["oldest_due_date"] is None or due < g["oldest_due_date"]):
                g["oldest_due_date"] = due
            if bucket != BUCKET_NOT_DUE:
                g["overdue_invoice_count"] += 1

    items = []
    for g in grouped.values():
        items.append({
            "supplier_key": g["supplier_key"],
            "supplier_name": g["supplier_name"],
            "total_invoiced_ap": _money_float(g["total_invoiced_ap"]),
            "posted_payments": _money_float(g["posted_payments"]),
            "accounting_outstanding": _money_float(g["accounting_outstanding"]),
            "pending_payment_amount": _money_float(g["pending_payment_amount"]),
            "operational_outstanding": _money_float(g["operational_outstanding"]),
            "not_due": _money_float(g["buckets"][BUCKET_NOT_DUE]),
            "d1_30": _money_float(g["buckets"][BUCKET_1_30]),
            "d31_60": _money_float(g["buckets"][BUCKET_31_60]),
            "d61_90": _money_float(g["buckets"][BUCKET_61_90]),
            "over_90": _money_float(g["buckets"][BUCKET_OVER_90]),
            "oldest_due_date": g["oldest_due_date"],
            "invoice_count": g["invoice_count"],
            "overdue_invoice_count": g["overdue_invoice_count"],
        })
    items.sort(key=lambda r: (-r["accounting_outstanding"], str(r.get("supplier_name") or ""), str(r.get("supplier_key") or "")))
    return items


def _build_summary(invoices: Sequence[dict], suppliers: Sequence[dict], breakdown: Sequence[dict], aging_reference_date: str) -> dict:
    buckets = _empty_buckets()
    total_accounting = Decimal("0.00")
    total_operational = Decimal("0.00")
    total_pending = Decimal("0.00")
    overdue_count = 0
    for row in invoices:
        outstanding = _money(row.get("accounting_outstanding"))
        total_accounting += outstanding
        total_operational += _money(row.get("operational_outstanding"))
        total_pending += _money(row.get("pending_payment_amount"))
        bucket = row.get("aging_bucket")
        if outstanding > 0 and bucket in buckets:
            buckets[bucket] += outstanding
            if bucket != BUCKET_NOT_DUE:
                overdue_count += 1
    return {
        "aging_reference_date": aging_reference_date,
        "disclaimer": AGING_DISCLAIMER,
        "current_state_only": True,
        "historical_as_of": False,
        "total_accounting_ap": _money_float(total_accounting),
        "total_operational_outstanding": _money_float(total_operational),
        "total_pending_payments": _money_float(total_pending),
        "not_due": _money_float(buckets[BUCKET_NOT_DUE]),
        "d1_30": _money_float(buckets[BUCKET_1_30]),
        "d31_60": _money_float(buckets[BUCKET_31_60]),
        "d61_90": _money_float(buckets[BUCKET_61_90]),
        "over_90": _money_float(buckets[BUCKET_OVER_90]),
        "supplier_count": len(suppliers),
        "invoice_count": len(invoices),
        "overdue_invoice_count": overdue_count,
        "account_breakdown": list(breakdown),
        "rounding_tolerance": _money_float(ROUNDING_TOLERANCE),
    }


async def _current_invoice_sets(
    db,
    *,
    aging_reference_date: Optional[str],
    q: Optional[str],
    supplier_key: Optional[str],
    ap_account_id: Optional[str],
    aging_bucket: Optional[str],
) -> Tuple[str, List[dict], List[dict]]:
    ref = parse_iso_date(aging_reference_date, field="aging_reference_date") or _today_iso()
    if aging_bucket and str(aging_bucket).strip() and str(aging_bucket).strip() not in AGING_BUCKETS:
        raise AccountsPayableValidationError("aging_bucket tidak valid")
    all_rows = await _load_current_invoices(db, aging_reference_date=ref)
    filters = {
        "q": q,
        "supplier_key": supplier_key,
        "ap_account_id": ap_account_id,
        "aging_bucket": aging_bucket,
    }
    filtered = [row for row in all_rows if _apply_invoice_filters(row, filters)]
    return ref, all_rows, filtered


async def _filtered_current(
    db,
    *,
    aging_reference_date: Optional[str],
    q: Optional[str],
    supplier_key: Optional[str],
    ap_account_id: Optional[str],
    aging_bucket: Optional[str],
) -> Tuple[str, List[dict]]:
    ref, _all_rows, filtered = await _current_invoice_sets(
        db,
        aging_reference_date=aging_reference_date,
        q=q,
        supplier_key=supplier_key,
        ap_account_id=ap_account_id,
        aging_bucket=aging_bucket,
    )
    return ref, filtered


async def get_accounts_payable_summary(
    db,
    *,
    aging_reference_date: Optional[str] = None,
    q: Optional[str] = None,
    supplier_key: Optional[str] = None,
    ap_account_id: Optional[str] = None,
    aging_bucket: Optional[str] = None,
) -> dict:
    ref, all_rows, filtered = await _current_invoice_sets(
        db,
        aging_reference_date=aging_reference_date,
        q=q,
        supplier_key=supplier_key,
        ap_account_id=ap_account_id,
        aging_bucket=aging_bucket,
    )
    suppliers = _aggregate_suppliers(filtered)
    missing_docs = await _collect_missing_purchase_documents(db)
    anomaly_accounts = _integrity_account_ids(all_rows, missing_docs)
    breakdown = await _account_breakdown(db, all_rows, anomaly_account_ids=anomaly_accounts)
    display_account = str(ap_account_id or "").strip()
    if display_account:
        breakdown = [row for row in breakdown if str(row.get("account_id") or "") == display_account]
    summary = _build_summary(filtered, suppliers, breakdown, ref)
    summary["reconciliation_scope"] = RECONCILIATION_SCOPE
    summary["reconciliation_note"] = RECONCILIATION_NOTE
    summary["integrity_anomalies"] = _aggregate_integrity_anomalies(all_rows, missing_docs)
    return summary


async def get_accounts_payable_aging(
    db,
    *,
    aging_reference_date: Optional[str] = None,
    q: Optional[str] = None,
    supplier_key: Optional[str] = None,
    ap_account_id: Optional[str] = None,
    aging_bucket: Optional[str] = None,
) -> dict:
    ref, rows = await _filtered_current(
        db,
        aging_reference_date=aging_reference_date,
        q=q,
        supplier_key=supplier_key,
        ap_account_id=ap_account_id,
        aging_bucket=aging_bucket,
    )
    visible = [
        row for row in rows
        if _money(row.get("accounting_outstanding")) > 0 or row.get("integrity_warning")
    ]
    visible.sort(key=lambda r: (
        str(r.get("due_date") or ""),
        str(r.get("invoice_number") or ""),
        str(r.get("invoice_id") or ""),
    ))
    return {
        "aging_reference_date": ref,
        "disclaimer": AGING_DISCLAIMER,
        "current_state_only": True,
        "historical_as_of": False,
        "items": [_public_invoice(row) for row in visible],
        "total": len(visible),
    }


async def get_accounts_payable_suppliers(
    db,
    *,
    aging_reference_date: Optional[str] = None,
    q: Optional[str] = None,
    supplier_key: Optional[str] = None,
    ap_account_id: Optional[str] = None,
    aging_bucket: Optional[str] = None,
) -> dict:
    ref, rows = await _filtered_current(
        db,
        aging_reference_date=aging_reference_date,
        q=q,
        supplier_key=supplier_key,
        ap_account_id=ap_account_id,
        aging_bucket=aging_bucket,
    )
    suppliers = _aggregate_suppliers(rows)
    return {
        "aging_reference_date": ref,
        "disclaimer": AGING_DISCLAIMER,
        "current_state_only": True,
        "historical_as_of": False,
        "items": suppliers,
        "total": len(suppliers),
    }


async def get_accounts_payable_statement(
    db,
    *,
    supplier_key: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> dict:
    key = str(supplier_key or "").strip()
    if not key:
        raise AccountsPayableValidationError("supplier_key wajib dipilih")
    start = parse_iso_date(start_date, field="start_date", required=True)
    end = parse_iso_date(end_date, field="end_date", required=True)
    if start > end:
        raise AccountsPayableValidationError("Tanggal mulai tidak boleh setelah tanggal akhir")

    journals = await _cursor_all(
        db[JOURNAL_ENTRIES].find(
            {"source_type": {"$in": list(PURCHASE_AP_SOURCES)}},
            {"_id": 0},
        )
    )
    journals = [j for j in journals if j.get("id") and _journal_is_legitimate(j)]
    invoice_source_ids = [
        str(j.get("source_id") or "")
        for j in journals
        if str(j.get("source_type") or "") in {SOURCE_INVOICE, SOURCE_INVOICE_REVERSAL} and j.get("source_id")
    ]
    payment_source_ids = [
        str(j.get("source_id") or "")
        for j in journals
        if str(j.get("source_type") or "") in {SOURCE_PAYMENT, SOURCE_PAYMENT_REVERSAL} and j.get("source_id")
    ]
    invoices = await _find_in(db, "purchase_invoices", "id", invoice_source_ids)
    payments = await _find_in(db, "purchase_payments", "id", payment_source_ids)
    extra_invoice_ids = [str(r.get("purchase_invoice_id") or "") for r in payments if r.get("purchase_invoice_id")]
    if extra_invoice_ids:
        more = await _find_in(db, "purchase_invoices", "id", extra_invoice_ids)
        known = {str(inv.get("id")) for inv in invoices}
        invoices.extend([inv for inv in more if str(inv.get("id")) not in known])

    invoices_by_id = {str(inv.get("id")): inv for inv in invoices if inv.get("id")}
    payments_by_id = {str(rec.get("id")): rec for rec in payments if rec.get("id")}

    invoice_supplier: Dict[str, str] = {}
    invoice_supplier_name: Dict[str, str] = {}
    for inv in invoices:
        invoice_supplier[str(inv.get("id"))] = supplier_identity_key(inv.get("supplier"))
        invoice_supplier_name[str(inv.get("id"))] = supplier_display_name(inv.get("supplier"))

    lines_by_jid = await _load_lines_by_journal(db, [str(j.get("id")) for j in journals if j.get("id")])
    account_ids: Set[str] = set()
    for lines in lines_by_jid.values():
        for ln in lines:
            if ln.get("account_id"):
                account_ids.add(str(ln.get("account_id")))
    accounts = await _load_accounts(db, account_ids)

    movements: List[dict] = []
    for journal in journals:
        st = str(journal.get("source_type") or "")
        sid = str(journal.get("source_id") or "")
        invoice = None
        payment = None
        if st in {SOURCE_INVOICE, SOURCE_INVOICE_REVERSAL}:
            invoice = invoices_by_id.get(sid)
        elif st in {SOURCE_PAYMENT, SOURCE_PAYMENT_REVERSAL}:
            payment = payments_by_id.get(sid)
            invoice = invoices_by_id.get(str((payment or {}).get("purchase_invoice_id") or ""))
        if not invoice:
            continue
        if invoice_supplier.get(str(invoice.get("id"))) != key:
            continue
        increase, decrease = _journal_ap_movement(lines_by_jid.get(str(journal.get("id") or ""), []), accounts)
        if increase == 0 and decrease == 0:
            continue
        ref_number = None
        if payment:
            ref_number = payment.get("payment_number") or payment.get("id")
        elif invoice:
            ref_number = invoice.get("invoice_number") or invoice.get("id")
        movements.append({
            "date": journal.get("date"),
            "journal_id": journal.get("id"),
            "journal_number": journal.get("number"),
            "created_at": journal.get("created_at"),
            "source_type": st,
            "source_id": sid,
            "source_label": _source_label(st),
            "reference": ref_number,
            "description": journal.get("description") or "",
            "ap_increase": increase,
            "ap_decrease": decrease,
            "invoice_id": invoice.get("id"),
            "invoice_number": invoice.get("invoice_number"),
            "payment_id": (payment or {}).get("id"),
            "payment_number": (payment or {}).get("payment_number"),
            "status": journal.get("status"),
        })

    movements.sort(key=lambda r: (
        str(r.get("date") or ""),
        str(r.get("journal_number") or ""),
        str(r.get("created_at") or ""),
        str(r.get("journal_id") or ""),
    ))

    opening = Decimal("0.00")
    period_increase = Decimal("0.00")
    period_decrease = Decimal("0.00")
    period_rows: List[dict] = []
    for mv in movements:
        net = mv["ap_increase"] - mv["ap_decrease"]
        jdate = str(mv.get("date") or "")[:10]
        if jdate and jdate < start:
            opening += net
            continue
        if jdate and jdate > end:
            continue
        period_increase += mv["ap_increase"]
        period_decrease += mv["ap_decrease"]
        period_rows.append(mv)

    running = opening
    items = []
    for mv in period_rows:
        running = running + mv["ap_increase"] - mv["ap_decrease"]
        items.append({
            "date": mv.get("date"),
            "journal_id": mv.get("journal_id"),
            "journal_number": mv.get("journal_number"),
            "source_type": mv.get("source_type"),
            "source_id": mv.get("source_id"),
            "source_label": mv.get("source_label"),
            "reference": mv.get("reference"),
            "description": mv.get("description"),
            "ap_increase": _money_float(mv["ap_increase"]),
            "ap_decrease": _money_float(mv["ap_decrease"]),
            "running_ap": _money_float(running),
            "invoice_id": mv.get("invoice_id"),
            "invoice_number": mv.get("invoice_number"),
            "payment_id": mv.get("payment_id"),
            "payment_number": mv.get("payment_number"),
            "status": mv.get("status"),
        })

    ending = opening + period_increase - period_decrease

    pending: List[dict] = []
    supplier_invoice_ids = {iid for iid, skey in invoice_supplier.items() if skey == key}
    if supplier_invoice_ids:
        pending_payments = await _find_in(db, "purchase_payments", "purchase_invoice_id", list(supplier_invoice_ids))
        pending_ids = [str(r.get("id")) for r in pending_payments if r.get("id")]
        pending_journals = await _cursor_all(
            db[JOURNAL_ENTRIES].find(
                {
                    "source_type": SOURCE_PAYMENT,
                    "source_id": {"$in": pending_ids} if pending_ids else {"$in": []},
                    "reversal_of": None,
                },
                {"_id": 0, "id": 1, "source_id": 1, "status": 1},
            )
        ) if pending_ids else []
        posted_payment_ids = {
            str(j.get("source_id"))
            for j in pending_journals
            if str(j.get("status") or "") == STATUS_POSTED
        }
        for rec in pending_payments:
            if not payment_counts_toward_outstanding(rec):
                continue
            if str(rec.get("id") or "") in posted_payment_ids:
                continue
            inv = invoices_by_id.get(str(rec.get("purchase_invoice_id") or ""))
            pending.append({
                "payment_id": rec.get("id"),
                "payment_number": rec.get("payment_number"),
                "payment_date": rec.get("payment_date"),
                "amount": _money_float(_money(rec.get("amount"))),
                "invoice_id": rec.get("purchase_invoice_id"),
                "invoice_number": (inv or {}).get("invoice_number"),
            })
        pending.sort(key=lambda r: (str(r.get("payment_date") or ""), str(r.get("payment_number") or "")))

    display_name = next((invoice_supplier_name[iid] for iid in supplier_invoice_ids if invoice_supplier_name.get(iid)), "")

    return {
        "supplier_key": key,
        "supplier_name": display_name,
        "start_date": start,
        "end_date": end,
        "opening_balance": _money_float(opening),
        "period_ap_increase": _money_float(period_increase),
        "period_ap_decrease": _money_float(period_decrease),
        "ending_balance": _money_float(ending),
        "items": items,
        "pending_payments": pending,
        "disclaimer": STATEMENT_DISCLAIMER,
        "note": STATEMENT_SHORT_NOTE,
        "current_state_only": True,
        "historical_as_of": False,
    }
