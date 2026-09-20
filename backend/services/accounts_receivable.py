"""Accounts Receivable read model from posted journals.

Zero-write. Current-state only. Aging reference date is NOT historical as-of.
Accounting AR is derived from journal_entries + journal_lines, not from
sales_invoices.outstanding_amount.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from services.customer_identity import (
    build_identity_context,
    display_customer_name,
    normalize_customer_name,
    resolve_customer_identity,
)
from services.general_ledger import SOURCE_LABELS, _journal_is_legitimate
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, STATUS_POSTED
from services.sales_customers import COLLECTION as SALES_CUSTOMERS
from services.sales_invoices import FINANCIAL_CANCELLED, FINANCIAL_FINALIZED, effective_financial_status
from services.sales_receipt_posting import receipt_counts_toward_outstanding

PERM_VIEW = "accounts_receivable.view"

SOURCE_INVOICE = "sales_invoice"
SOURCE_INVOICE_REVERSAL = "sales_invoice_reversal"
SOURCE_RECEIPT = "sales_receipt"
SOURCE_RECEIPT_REVERSAL = "sales_receipt_reversal"

SALES_AR_SOURCES = frozenset({
    SOURCE_INVOICE,
    SOURCE_INVOICE_REVERSAL,
    SOURCE_RECEIPT,
    SOURCE_RECEIPT_REVERSAL,
})

BUCKET_NOT_DUE = "NOT_DUE"
BUCKET_1_30 = "1_30"
BUCKET_31_60 = "31_60"
BUCKET_61_90 = "61_90"
BUCKET_OVER_90 = "OVER_90"
AGING_BUCKETS = (BUCKET_NOT_DUE, BUCKET_1_30, BUCKET_31_60, BUCKET_61_90, BUCKET_OVER_90)

ROUNDING_TOLERANCE = Decimal("0.01")
IN_BATCH = 400

# Statement rows are journal-level AREC aggregates. Tie-break is stable and
# repeatable: date → journal_number → created_at → journal_id.
STATEMENT_SORT_KEYS = ("date", "journal_number", "created_at", "journal_id")

WARN_NEGATIVE_ACCOUNTING_OUTSTANDING = "NEGATIVE_ACCOUNTING_OUTSTANDING"
WARN_OVER_APPLIED_ACCOUNTING_RECEIPT = "OVER_APPLIED_ACCOUNTING_RECEIPT"
WARN_RECEIPT_AR_ACCOUNT_MISMATCH = "RECEIPT_AR_ACCOUNT_MISMATCH"
WARN_MULTIPLE_INVOICE_AREC_ACCOUNTS = "MULTIPLE_INVOICE_AREC_ACCOUNTS"
WARN_MULTIPLE_RECEIPT_AREC_ACCOUNTS = "MULTIPLE_RECEIPT_AREC_ACCOUNTS"
WARN_MISSING_SALES_INVOICE_DOCUMENT = "missing_sales_invoice_document"
WARN_MISSING_SALES_RECEIPT_DOCUMENT = "missing_sales_receipt_document"

AGING_DISCLAIMER = (
    "Piutang Accounting dihitung dari jurnal yang sudah diposting. "
    "Tanggal Acuan Aging hanya menentukan umur piutang, bukan historical accounting as-of."
)
STATEMENT_DISCLAIMER = (
    "Riwayat mengikuti tanggal jurnal. Sistem belum menyediakan historical accounting "
    "as-of sebelum reversal yang dibukukan pada tanggal asli."
)
STATEMENT_SHORT_NOTE = "Statement mengikuti tanggal jurnal."
GL_DIFF_LABEL = "Selisih non-Faktur / jurnal lain"

INDEX_SPECS = (
    ("sales_invoices", "financial_status"),
    ("sales_invoices", "invoice_date"),
    ("sales_invoices", "due_date"),
    ("sales_receipts", "receipt_date"),
    ("sales_receipts", "financial_status"),
    ("orders", "customer_id"),
)


class AccountsReceivableValidationError(ValueError):
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


def _today_iso() -> str:
    return date.today().isoformat()


def parse_iso_date(raw: Any, *, field: str, required: bool = False) -> Optional[str]:
    text = str(raw or "").strip()[:10]
    if not text:
        if required:
            raise AccountsReceivableValidationError(f"{field} wajib diisi")
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise AccountsReceivableValidationError(f"{field} tidak valid") from exc
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


def _is_arec(account: Optional[dict]) -> bool:
    return str((account or {}).get("account_type") or "").strip().upper() == "AREC"


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


def _analyze_invoice_ar(
    lines: Sequence[dict],
    accounts: Dict[str, dict],
) -> Tuple[Optional[dict], Decimal, Set[str], List[str]]:
    """AREC debit analysis.

    Exactly one AREC account (one or more debit lines): that account + sum.
    Multiple AREC accounts: do not pick an arbitrary first account.
    """
    by_account: Dict[str, Decimal] = {}
    account_docs: Dict[str, dict] = {}
    for ln in lines:
        debit = _money(ln.get("debit"))
        credit = _money(ln.get("credit"))
        if debit <= 0 or credit > 0:
            continue
        acc = accounts.get(str(ln.get("account_id") or ""))
        if not _is_arec(acc):
            continue
        aid = str(acc.get("id") or "")
        if not aid:
            continue
        by_account[aid] = by_account.get(aid, Decimal("0.00")) + debit
        account_docs[aid] = acc
    warnings: List[str] = []
    if not by_account:
        return None, Decimal("0.00"), set(), warnings
    account_ids = set(by_account)
    total = sum(by_account.values(), Decimal("0.00"))
    if len(by_account) == 1:
        aid = next(iter(by_account))
        return account_docs[aid], by_account[aid], account_ids, warnings
    warnings.append(WARN_MULTIPLE_INVOICE_AREC_ACCOUNTS)
    return None, total, account_ids, warnings


def _analyze_receipt_ar(
    lines: Sequence[dict],
    accounts: Dict[str, dict],
    invoice_ar_ids: Set[str],
) -> Tuple[Decimal, List[str]]:
    """Receipt AREC credits reduce invoice AR only on the same actual AR account(s).

    Never fall back to an unmatched AREC credit or the receipt document amount.
    """
    matched = Decimal("0.00")
    unmatched = Decimal("0.00")
    arec_accounts: Set[str] = set()
    for ln in lines:
        debit = _money(ln.get("debit"))
        credit = _money(ln.get("credit"))
        if credit <= 0 or debit > 0:
            continue
        aid = str(ln.get("account_id") or "")
        if not _is_arec(accounts.get(aid)):
            continue
        arec_accounts.add(aid)
        if aid in invoice_ar_ids:
            matched += credit
        else:
            unmatched += credit
    warnings: List[str] = []
    if len(arec_accounts) > 1:
        warnings.append(WARN_MULTIPLE_RECEIPT_AREC_ACCOUNTS)
    if unmatched > ROUNDING_TOLERANCE:
        warnings.append(WARN_RECEIPT_AR_ACCOUNT_MISMATCH)
    return matched, warnings


def _normalize_outstanding(raw: Decimal) -> Tuple[Decimal, List[str]]:
    """Keep raw arithmetic. Only absorb <= 0.01 rounding; never hide a real negative."""
    warnings: List[str] = []
    if raw < -ROUNDING_TOLERANCE:
        warnings.append(WARN_NEGATIVE_ACCOUNTING_OUTSTANDING)
        return raw, warnings
    if raw < 0:
        return Decimal("0.00"), warnings
    return raw, warnings


def _journal_ar_movement(lines: Sequence[dict], accounts: Dict[str, dict], ar_account_id: Optional[str] = None) -> Tuple[Decimal, Decimal]:
    debit_total = Decimal("0.00")
    credit_total = Decimal("0.00")
    for ln in lines:
        aid = str(ln.get("account_id") or "")
        acc = accounts.get(aid)
        if ar_account_id:
            if aid != ar_account_id:
                continue
        elif not _is_arec(acc):
            continue
        debit_total += _money(ln.get("debit"))
        credit_total += _money(ln.get("credit"))
    return debit_total, credit_total


def _apply_invoice_filters(row: dict, filters: dict) -> bool:
    customer_key = str(filters.get("customer_key") or "").strip()
    if customer_key and str(row.get("customer_key") or "") != customer_key:
        return False
    sales = str(filters.get("sales") or "").strip()
    if sales and str(row.get("sales") or "").strip() != sales:
        return False
    ar_account_id = str(filters.get("ar_account_id") or "").strip()
    if ar_account_id and str(row.get("actual_ar_account_id") or "") != ar_account_id:
        return False
    aging_bucket = str(filters.get("aging_bucket") or "").strip()
    if aging_bucket and str(row.get("aging_bucket") or "") != aging_bucket:
        return False
    needle = str(filters.get("q") or "").strip().lower()
    if needle:
        blob = " ".join([
            str(row.get("invoice_number") or ""),
            str(row.get("customer_name") or ""),
            str(row.get("po_number") or ""),
            str(row.get("sales") or ""),
            str(row.get("outlet") or ""),
            str(row.get("actual_ar_account_code") or ""),
            str(row.get("actual_ar_account_name") or ""),
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
        "po_id": row.get("po_id"),
        "po_number": row.get("po_number"),
        "customer_key": row.get("customer_key"),
        "customer_id": row.get("customer_id"),
        "customer_name": row.get("customer_name"),
        "customer_identity_kind": row.get("customer_identity_kind"),
        "customer_identity_ambiguous": bool(row.get("customer_identity_ambiguous")),
        "outlet": row.get("outlet"),
        "sales": row.get("sales"),
        "financial_status": row.get("financial_status"),
        "accounting_status": row.get("accounting_status"),
        "payment_status": row.get("payment_status"),
        "actual_ar_account_id": row.get("actual_ar_account_id"),
        "actual_ar_account_code": row.get("actual_ar_account_code"),
        "actual_ar_account_name": row.get("actual_ar_account_name"),
        "original_ar_amount": _money_float(row["original_ar_amount"]),
        "posted_receipt_amount": _money_float(row["posted_receipt_amount"]),
        "pending_receipt_amount": _money_float(row["pending_receipt_amount"]),
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


async def _build_identity(db, invoices: Sequence[dict], orders_by_id: Dict[str, dict]) -> Any:
    order_docs = list(orders_by_id.values())
    names: Set[str] = set()
    raw_names: Set[str] = set()
    stable_ids: Set[str] = set()
    for order in order_docs:
        display = display_customer_name(order)
        if display:
            raw_names.add(display)
            names.add(normalize_customer_name(display))
        sid = str(order.get("customer_id") or "").strip()
        if sid:
            stable_ids.add(sid)
    for inv in invoices:
        display = str(inv.get("customer") or inv.get("customer_name") or "").strip()
        if display:
            raw_names.add(display)
            names.add(normalize_customer_name(display))

    masters: List[dict] = []
    if stable_ids or names:
        clauses: List[dict] = []
        if stable_ids:
            clauses.append({"id": {"$in": list(stable_ids)}})
        if names:
            clauses.append({"normalized_name": {"$in": list(names)}})
        if raw_names:
            clauses.append({"name": {"$in": list(raw_names)}})
        masters = await _cursor_all(db[SALES_CUSTOMERS].find({"$or": clauses}, {"_id": 0}))

    extra_orders: List[dict] = []
    if names or raw_names or stable_ids:
        clauses = []
        if stable_ids:
            clauses.append({"customer_id": {"$in": list(stable_ids)}})
        if raw_names:
            clauses.append({"customer": {"$in": list(raw_names)}})
            clauses.append({"customer_name": {"$in": list(raw_names)}})
        extra_orders = await _cursor_all(
            db.orders.find(
                {"$or": clauses},
                {"_id": 0, "id": 1, "customer": 1, "customer_name": 1, "customer_id": 1, "outlet": 1, "name": 1},
            )
        )
    known_ids = {str(o.get("id")) for o in order_docs}
    for extra in extra_orders:
        eid = str(extra.get("id") or "")
        if eid and eid not in known_ids:
            order_docs.append(extra)
            known_ids.add(eid)
    return build_identity_context(masters, order_docs, [])


def _resolve_invoice_customer(invoice: dict, order: Optional[dict], context) -> Any:
    record = order or {
        "customer": invoice.get("customer") or invoice.get("customer_name"),
        "customer_name": invoice.get("customer_name") or invoice.get("customer"),
        "outlet": invoice.get("outlet"),
        "customer_id": invoice.get("customer_id"),
    }
    return resolve_customer_identity(record, context)


def _document_mismatch(invoice: Optional[dict], journal: dict) -> bool:
    if str(journal.get("status") or "") != STATUS_POSTED:
        return False
    if not invoice:
        return True
    return effective_financial_status(invoice) != FINANCIAL_FINALIZED


async def _compute_gl_arec_balances(db, account_ids: Sequence[str]) -> Dict[str, Decimal]:
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
        balances[aid] += _money(ln.get("debit")) - _money(ln.get("credit"))
    return balances


async def _collect_missing_sales_documents(db) -> List[dict]:
    """Orphan sales journals stay in GL residual. Do not invent a customer."""
    journals = await _cursor_all(
        db[JOURNAL_ENTRIES].find(
            {
                "source_type": {"$in": [SOURCE_INVOICE, SOURCE_RECEIPT]},
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
    receipt_ids = [
        str(j.get("source_id") or "")
        for j in journals
        if str(j.get("source_type") or "") == SOURCE_RECEIPT and j.get("source_id")
    ]
    invoices = {str(doc.get("id")) for doc in await _find_in(db, "sales_invoices", "id", invoice_ids, {"id": 1})}
    receipts = {str(doc.get("id")) for doc in await _find_in(db, "sales_receipts", "id", receipt_ids, {"id": 1})}
    anomalies: List[dict] = []
    for journal in journals:
        sid = str(journal.get("source_id") or "")
        st = str(journal.get("source_type") or "")
        if not sid:
            continue
        if st == SOURCE_INVOICE and sid not in invoices:
            anomalies.append({
                "code": WARN_MISSING_SALES_INVOICE_DOCUMENT,
                "source_type": st,
                "source_id": sid,
                "journal_id": journal.get("id"),
            })
        elif st == SOURCE_RECEIPT and sid not in receipts:
            anomalies.append({
                "code": WARN_MISSING_SALES_RECEIPT_DOCUMENT,
                "source_type": st,
                "source_id": sid,
                "journal_id": journal.get("id"),
            })
    return anomalies


async def _load_current_invoices(db, *, aging_reference_date: str) -> List[dict]:
    """Current aging from posted primary sales_invoice journals.

    Collection-batched: invoice journals, invoice docs, receipts, receipt
    journals, journal lines, COA, and orders are loaded via $in / cursor_all.
    No per-invoice journal/receipt/order/COA queries.
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
    invoices = await _find_in(db, "sales_invoices", "id", invoice_ids)
    invoices_by_id = {str(inv.get("id")): inv for inv in invoices if inv.get("id")}
    lines_by_jid = await _load_lines_by_journal(db, journal_ids)

    receipts = await _find_in(db, "sales_receipts", "invoice_id", invoice_ids)
    receipts_by_invoice: Dict[str, List[dict]] = {}
    receipt_ids: List[str] = []
    for rec in receipts:
        rid = str(rec.get("id") or "")
        iid = str(rec.get("invoice_id") or "")
        if not rid or not iid:
            continue
        receipts_by_invoice.setdefault(iid, []).append(rec)
        receipt_ids.append(rid)

    receipt_journals: List[dict] = []
    if receipt_ids:
        for i in range(0, len(receipt_ids), IN_BATCH):
            chunk = receipt_ids[i:i + IN_BATCH]
            receipt_journals.extend(await _cursor_all(
                db[JOURNAL_ENTRIES].find(
                    {
                        "source_type": SOURCE_RECEIPT,
                        "source_id": {"$in": chunk},
                        "reversal_of": None,
                    },
                    {"_id": 0},
                )
            ))
    receipt_journal_by_source = {str(j.get("source_id")): j for j in receipt_journals if j.get("source_id")}
    receipt_lines_by_jid = await _load_lines_by_journal(
        db, [str(j.get("id")) for j in receipt_journals if j.get("id")]
    )

    account_ids: Set[str] = set()
    for lines in list(lines_by_jid.values()) + list(receipt_lines_by_jid.values()):
        for ln in lines:
            if ln.get("account_id"):
                account_ids.add(str(ln.get("account_id")))
    accounts = await _load_accounts(db, account_ids)

    po_ids = [str(inv.get("po_id") or "") for inv in invoices if inv.get("po_id")]
    orders = await _find_in(
        db,
        "orders",
        "id",
        po_ids,
        {"id": 1, "po_number": 1, "customer": 1, "customer_name": 1, "customer_id": 1, "outlet": 1, "sales": 1, "name": 1},
    )
    orders_by_id = {str(o.get("id")): o for o in orders if o.get("id")}
    context = await _build_identity(db, invoices, orders_by_id)

    rows: List[dict] = []
    for journal in journals:
        invoice_id = str(journal.get("source_id") or "")
        invoice = invoices_by_id.get(invoice_id)
        if not invoice:
            # Missing sales document: keep AREC in GL residual only. Do not invent a customer.
            continue
        lines = lines_by_jid.get(str(journal.get("id") or ""), [])
        ar_account, original_ar, invoice_ar_ids, invoice_warnings = _analyze_invoice_ar(lines, accounts)
        status = str(journal.get("status") or "").strip()
        if status != STATUS_POSTED or original_ar <= 0:
            continue

        ar_account_id = str((ar_account or {}).get("id") or "") or None
        posted_receipt = Decimal("0.00")
        pending_receipt = Decimal("0.00")
        operational_paid = Decimal("0.00")
        integrity_warnings = list(invoice_warnings)
        for rec in receipts_by_invoice.get(invoice_id, []):
            rec_journal = receipt_journal_by_source.get(str(rec.get("id") or ""))
            rec_status = str((rec_journal or {}).get("status") or "").strip()
            rec_lines = receipt_lines_by_jid.get(str((rec_journal or {}).get("id") or ""), [])
            if rec_status == STATUS_POSTED:
                matched_credit, rec_warnings = _analyze_receipt_ar(rec_lines, accounts, invoice_ar_ids)
                posted_receipt += matched_credit
                integrity_warnings.extend(rec_warnings)
            if receipt_counts_toward_outstanding(rec):
                amount = _money(rec.get("amount"))
                operational_paid += amount
                if rec_status != STATUS_POSTED:
                    pending_receipt += amount

        raw_outstanding = original_ar - posted_receipt
        accounting_outstanding, outstanding_warnings = _normalize_outstanding(raw_outstanding)
        integrity_warnings.extend(outstanding_warnings)
        if posted_receipt > original_ar + ROUNDING_TOLERANCE:
            integrity_warnings.append(WARN_OVER_APPLIED_ACCOUNTING_RECEIPT)
        integrity_warnings = list(dict.fromkeys(integrity_warnings))
        integrity_warning = bool(integrity_warnings)

        financial_status = effective_financial_status(invoice) if invoice else None
        if invoice and financial_status == FINANCIAL_CANCELLED:
            operational_outstanding = Decimal("0.00")
        else:
            grand = _money((invoice or {}).get("grand_total"))
            operational_outstanding = grand - operational_paid
            if operational_outstanding < 0 and operational_outstanding >= -ROUNDING_TOLERANCE:
                operational_outstanding = Decimal("0.00")

        order = orders_by_id.get(str((invoice or {}).get("po_id") or ""))
        resolved = _resolve_invoice_customer(invoice or {}, order, context)
        due_date, due_fallback = _invoice_due(invoice or {})
        if not due_date:
            due_date = str((invoice or {}).get("invoice_date") or journal.get("date") or aging_reference_date)[:10]
            due_fallback = True
        bucket, days_overdue = aging_bucket_for(due_date, aging_reference_date)

        payment_status = (invoice or {}).get("payment_status")
        if invoice and financial_status != FINANCIAL_CANCELLED:
            grand = _money(invoice.get("grand_total"))
            if operational_paid <= 0:
                payment_status = "BELUM DIBAYAR"
            elif operational_paid < grand:
                payment_status = "DP"
            else:
                payment_status = "LUNAS"

        rows.append({
            "invoice_id": invoice_id,
            "invoice_number": (invoice or {}).get("invoice_number") or invoice_id,
            "invoice_date": (invoice or {}).get("invoice_date") or journal.get("date"),
            "due_date": due_date,
            "due_date_fallback": due_fallback,
            "po_id": (invoice or {}).get("po_id"),
            "po_number": (invoice or {}).get("po_number") or (order or {}).get("po_number"),
            "customer_key": resolved.key,
            "customer_id": resolved.stable_id,
            "customer_name": resolved.display_name or str((invoice or {}).get("customer") or ""),
            "customer_identity_kind": resolved.kind,
            "customer_identity_ambiguous": resolved.ambiguous,
            "outlet": (invoice or {}).get("outlet") or (order or {}).get("outlet") or "",
            "sales": (invoice or {}).get("sales") or (order or {}).get("sales") or "",
            "financial_status": financial_status,
            "accounting_status": status,
            "payment_status": payment_status,
            "actual_ar_account_id": ar_account_id,
            "actual_ar_account_code": (ar_account or {}).get("code"),
            "actual_ar_account_name": (ar_account or {}).get("name"),
            "original_ar_amount": original_ar,
            "posted_receipt_amount": posted_receipt,
            "pending_receipt_amount": pending_receipt,
            "accounting_outstanding": accounting_outstanding,
            "accounting_outstanding_raw": raw_outstanding,
            "operational_outstanding": operational_outstanding,
            "days_overdue": days_overdue,
            "aging_bucket": bucket if accounting_outstanding > 0 else None,
            "document_mismatch": _document_mismatch(invoice, journal),
            "integrity_warning": integrity_warning,
            "integrity_warnings": integrity_warnings,
        })
    return rows


async def _account_breakdown(db, invoices: Sequence[dict]) -> List[dict]:
    sales_by_account: Dict[str, Decimal] = {}
    for row in invoices:
        aid = str(row.get("actual_ar_account_id") or "")
        if not aid:
            continue
        sales_by_account[aid] = sales_by_account.get(aid, Decimal("0.00")) + _money(row.get("accounting_outstanding"))

    arec_docs = await _cursor_all(
        db.ledger_accounts.find({"account_type": "AREC"}, {"_id": 0, "id": 1, "code": 1, "name": 1, "account_type": 1})
    )
    account_ids = {str(doc.get("id")) for doc in arec_docs if doc.get("id")}
    account_ids.update(sales_by_account.keys())
    accounts = await _load_accounts(db, account_ids)
    gl_balances = await _compute_gl_arec_balances(db, list(account_ids))

    breakdown: List[dict] = []
    for aid in sorted(account_ids, key=lambda x: str((accounts.get(x) or {}).get("code") or x)):
        acc = accounts.get(aid) or {}
        sales_ar = sales_by_account.get(aid, Decimal("0.00"))
        gl_bal = gl_balances.get(aid, Decimal("0.00"))
        if sales_ar == 0 and gl_bal == 0:
            continue
        difference = gl_bal - sales_ar
        breakdown.append({
            "account_id": aid,
            "account_code": acc.get("code"),
            "account_name": acc.get("name"),
            "sales_ar_balance": _money_float(sales_ar),
            "gl_arec_balance": _money_float(gl_bal),
            "difference": _money_float(difference),
            "difference_label": GL_DIFF_LABEL,
        })
    return breakdown


def _aggregate_customers(invoices: Sequence[dict]) -> List[dict]:
    grouped: Dict[str, dict] = {}
    for row in invoices:
        key = str(row.get("customer_key") or "")
        if key not in grouped:
            grouped[key] = {
                "customer_key": key,
                "customer_id": row.get("customer_id"),
                "customer_name": row.get("customer_name"),
                "customer_identity_kind": row.get("customer_identity_kind"),
                "ambiguous": bool(row.get("customer_identity_ambiguous")),
                "total_invoiced_ar": Decimal("0.00"),
                "posted_receipts": Decimal("0.00"),
                "accounting_outstanding": Decimal("0.00"),
                "pending_receipt_amount": Decimal("0.00"),
                "operational_outstanding": Decimal("0.00"),
                "buckets": _empty_buckets(),
                "oldest_due_date": None,
                "invoice_count": 0,
                "overdue_invoice_count": 0,
            }
        g = grouped[key]
        if not g.get("customer_name") and row.get("customer_name"):
            g["customer_name"] = row.get("customer_name")
        if not g.get("customer_id") and row.get("customer_id"):
            g["customer_id"] = row.get("customer_id")
        g["total_invoiced_ar"] += _money(row.get("original_ar_amount"))
        g["posted_receipts"] += _money(row.get("posted_receipt_amount"))
        g["accounting_outstanding"] += _money(row.get("accounting_outstanding"))
        g["pending_receipt_amount"] += _money(row.get("pending_receipt_amount"))
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
            "customer_key": g["customer_key"],
            "customer_id": g["customer_id"],
            "customer_name": g["customer_name"],
            "customer_identity_kind": g["customer_identity_kind"],
            "ambiguous": g["ambiguous"],
            "total_invoiced_ar": _money_float(g["total_invoiced_ar"]),
            "posted_receipts": _money_float(g["posted_receipts"]),
            "accounting_outstanding": _money_float(g["accounting_outstanding"]),
            "pending_receipt_amount": _money_float(g["pending_receipt_amount"]),
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
    items.sort(key=lambda r: (-r["accounting_outstanding"], str(r.get("customer_name") or ""), str(r.get("customer_key") or "")))
    return items


def _build_summary(invoices: Sequence[dict], customers: Sequence[dict], breakdown: Sequence[dict], aging_reference_date: str) -> dict:
    buckets = _empty_buckets()
    total_accounting = Decimal("0.00")
    total_operational = Decimal("0.00")
    total_pending = Decimal("0.00")
    overdue_count = 0
    for row in invoices:
        outstanding = _money(row.get("accounting_outstanding"))
        total_accounting += outstanding
        total_operational += _money(row.get("operational_outstanding"))
        total_pending += _money(row.get("pending_receipt_amount"))
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
        "total_accounting_ar": _money_float(total_accounting),
        "total_operational_outstanding": _money_float(total_operational),
        "total_pending_receipts": _money_float(total_pending),
        "not_due": _money_float(buckets[BUCKET_NOT_DUE]),
        "d1_30": _money_float(buckets[BUCKET_1_30]),
        "d31_60": _money_float(buckets[BUCKET_31_60]),
        "d61_90": _money_float(buckets[BUCKET_61_90]),
        "over_90": _money_float(buckets[BUCKET_OVER_90]),
        "customer_count": len(customers),
        "invoice_count": len(invoices),
        "overdue_invoice_count": overdue_count,
        "account_breakdown": list(breakdown),
        "rounding_tolerance": _money_float(ROUNDING_TOLERANCE),
    }


async def _filtered_current(db, *, aging_reference_date: Optional[str], q: Optional[str], customer_key: Optional[str], sales: Optional[str], ar_account_id: Optional[str], aging_bucket: Optional[str]) -> Tuple[str, List[dict]]:
    ref = parse_iso_date(aging_reference_date, field="aging_reference_date") or _today_iso()
    if aging_bucket and str(aging_bucket).strip() and str(aging_bucket).strip() not in AGING_BUCKETS:
        raise AccountsReceivableValidationError("aging_bucket tidak valid")
    rows = await _load_current_invoices(db, aging_reference_date=ref)
    filters = {
        "q": q,
        "customer_key": customer_key,
        "sales": sales,
        "ar_account_id": ar_account_id,
        "aging_bucket": aging_bucket,
    }
    return ref, [row for row in rows if _apply_invoice_filters(row, filters)]


async def get_accounts_receivable_summary(
    db,
    *,
    aging_reference_date: Optional[str] = None,
    q: Optional[str] = None,
    customer_key: Optional[str] = None,
    sales: Optional[str] = None,
    ar_account_id: Optional[str] = None,
    aging_bucket: Optional[str] = None,
) -> dict:
    ref, rows = await _filtered_current(
        db,
        aging_reference_date=aging_reference_date,
        q=q,
        customer_key=customer_key,
        sales=sales,
        ar_account_id=ar_account_id,
        aging_bucket=aging_bucket,
    )
    customers = _aggregate_customers(rows)
    breakdown = await _account_breakdown(db, rows)
    summary = _build_summary(rows, customers, breakdown, ref)
    summary["integrity_anomalies"] = await _collect_missing_sales_documents(db)
    return summary


async def get_accounts_receivable_aging(
    db,
    *,
    aging_reference_date: Optional[str] = None,
    q: Optional[str] = None,
    customer_key: Optional[str] = None,
    sales: Optional[str] = None,
    ar_account_id: Optional[str] = None,
    aging_bucket: Optional[str] = None,
) -> dict:
    ref, rows = await _filtered_current(
        db,
        aging_reference_date=aging_reference_date,
        q=q,
        customer_key=customer_key,
        sales=sales,
        ar_account_id=ar_account_id,
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


async def get_accounts_receivable_customers(
    db,
    *,
    aging_reference_date: Optional[str] = None,
    q: Optional[str] = None,
    customer_key: Optional[str] = None,
    sales: Optional[str] = None,
    ar_account_id: Optional[str] = None,
    aging_bucket: Optional[str] = None,
) -> dict:
    ref, rows = await _filtered_current(
        db,
        aging_reference_date=aging_reference_date,
        q=q,
        customer_key=customer_key,
        sales=sales,
        ar_account_id=ar_account_id,
        aging_bucket=aging_bucket,
    )
    customers = _aggregate_customers(rows)
    return {
        "aging_reference_date": ref,
        "disclaimer": AGING_DISCLAIMER,
        "current_state_only": True,
        "historical_as_of": False,
        "items": customers,
        "total": len(customers),
    }


async def get_accounts_receivable_statement(
    db,
    *,
    customer_key: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> dict:
    key = str(customer_key or "").strip()
    if not key:
        raise AccountsReceivableValidationError("customer_key wajib dipilih")
    start = parse_iso_date(start_date, field="start_date", required=True)
    end = parse_iso_date(end_date, field="end_date", required=True)
    if start > end:
        raise AccountsReceivableValidationError("Tanggal mulai tidak boleh setelah tanggal akhir")

    journals = await _cursor_all(
        db[JOURNAL_ENTRIES].find(
            {"source_type": {"$in": list(SALES_AR_SOURCES)}},
            {"_id": 0},
        )
    )
    journals = [j for j in journals if j.get("id") and _journal_is_legitimate(j)]
    invoice_source_ids = [
        str(j.get("source_id") or "")
        for j in journals
        if str(j.get("source_type") or "") in {SOURCE_INVOICE, SOURCE_INVOICE_REVERSAL} and j.get("source_id")
    ]
    receipt_source_ids = [
        str(j.get("source_id") or "")
        for j in journals
        if str(j.get("source_type") or "") in {SOURCE_RECEIPT, SOURCE_RECEIPT_REVERSAL} and j.get("source_id")
    ]
    invoices = await _find_in(db, "sales_invoices", "id", invoice_source_ids)
    receipts = await _find_in(db, "sales_receipts", "id", receipt_source_ids)
    extra_invoice_ids = [str(r.get("invoice_id") or "") for r in receipts if r.get("invoice_id")]
    if extra_invoice_ids:
        more = await _find_in(db, "sales_invoices", "id", extra_invoice_ids)
        known = {str(inv.get("id")) for inv in invoices}
        invoices.extend([inv for inv in more if str(inv.get("id")) not in known])

    invoices_by_id = {str(inv.get("id")): inv for inv in invoices if inv.get("id")}
    receipts_by_id = {str(rec.get("id")): rec for rec in receipts if rec.get("id")}

    po_ids = [str(inv.get("po_id") or "") for inv in invoices if inv.get("po_id")]
    orders = await _find_in(
        db,
        "orders",
        "id",
        po_ids,
        {"id": 1, "po_number": 1, "customer": 1, "customer_name": 1, "customer_id": 1, "outlet": 1, "sales": 1, "name": 1},
    )
    orders_by_id = {str(o.get("id")): o for o in orders if o.get("id")}
    context = await _build_identity(db, invoices, orders_by_id)

    invoice_customer: Dict[str, Any] = {}
    for inv in invoices:
        order = orders_by_id.get(str(inv.get("po_id") or ""))
        invoice_customer[str(inv.get("id"))] = _resolve_invoice_customer(inv, order, context)

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
        receipt = None
        if st in {SOURCE_INVOICE, SOURCE_INVOICE_REVERSAL}:
            invoice = invoices_by_id.get(sid)
        elif st in {SOURCE_RECEIPT, SOURCE_RECEIPT_REVERSAL}:
            receipt = receipts_by_id.get(sid)
            invoice = invoices_by_id.get(str((receipt or {}).get("invoice_id") or ""))
        if not invoice:
            continue
        resolved = invoice_customer.get(str(invoice.get("id")))
        if not resolved or resolved.key != key:
            continue
        debit, credit = _journal_ar_movement(lines_by_jid.get(str(journal.get("id") or ""), []), accounts)
        if debit == 0 and credit == 0:
            continue
        ref_number = None
        if receipt:
            ref_number = receipt.get("receipt_number") or receipt.get("id")
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
            "debit": debit,
            "credit": credit,
            "invoice_id": invoice.get("id"),
            "invoice_number": invoice.get("invoice_number"),
            "receipt_id": (receipt or {}).get("id"),
            "receipt_number": (receipt or {}).get("receipt_number"),
            "status": journal.get("status"),
        })

    movements.sort(key=lambda r: (
        str(r.get("date") or ""),
        str(r.get("journal_number") or ""),
        str(r.get("created_at") or ""),
        str(r.get("journal_id") or ""),
    ))

    opening = Decimal("0.00")
    period_debit = Decimal("0.00")
    period_credit = Decimal("0.00")
    period_rows: List[dict] = []
    running = Decimal("0.00")
    for mv in movements:
        net = mv["debit"] - mv["credit"]
        jdate = str(mv.get("date") or "")[:10]
        if jdate and jdate < start:
            opening += net
            continue
        if jdate and jdate > end:
            continue
        period_debit += mv["debit"]
        period_credit += mv["credit"]
        period_rows.append(mv)

    running = opening
    items = []
    for mv in period_rows:
        running = running + mv["debit"] - mv["credit"]
        items.append({
            "date": mv.get("date"),
            "journal_id": mv.get("journal_id"),
            "journal_number": mv.get("journal_number"),
            "source_type": mv.get("source_type"),
            "source_id": mv.get("source_id"),
            "source_label": mv.get("source_label"),
            "reference": mv.get("reference"),
            "description": mv.get("description"),
            "debit": _money_float(mv["debit"]),
            "credit": _money_float(mv["credit"]),
            "running_balance": _money_float(running),
            "invoice_id": mv.get("invoice_id"),
            "invoice_number": mv.get("invoice_number"),
            "receipt_id": mv.get("receipt_id"),
            "receipt_number": mv.get("receipt_number"),
            "status": mv.get("status"),
        })

    ending = opening + period_debit - period_credit

    pending: List[dict] = []
    customer_invoice_ids = {iid for iid, resolved in invoice_customer.items() if resolved and resolved.key == key}
    if customer_invoice_ids:
        pending_receipts = await _find_in(db, "sales_receipts", "invoice_id", list(customer_invoice_ids))
        pending_ids = [str(r.get("id")) for r in pending_receipts if r.get("id")]
        pending_journals = await _cursor_all(
            db[JOURNAL_ENTRIES].find(
                {
                    "source_type": SOURCE_RECEIPT,
                    "source_id": {"$in": pending_ids} if pending_ids else {"$in": []},
                    "reversal_of": None,
                },
                {"_id": 0, "id": 1, "source_id": 1, "status": 1},
            )
        ) if pending_ids else []
        posted_receipt_ids = {
            str(j.get("source_id"))
            for j in pending_journals
            if str(j.get("status") or "") == STATUS_POSTED
        }
        for rec in pending_receipts:
            if not receipt_counts_toward_outstanding(rec):
                continue
            if str(rec.get("id") or "") in posted_receipt_ids:
                continue
            inv = invoices_by_id.get(str(rec.get("invoice_id") or ""))
            pending.append({
                "receipt_id": rec.get("id"),
                "receipt_number": rec.get("receipt_number"),
                "receipt_date": rec.get("receipt_date"),
                "amount": _money_float(_money(rec.get("amount"))),
                "invoice_id": rec.get("invoice_id"),
                "invoice_number": (inv or {}).get("invoice_number"),
            })
        pending.sort(key=lambda r: (str(r.get("receipt_date") or ""), str(r.get("receipt_number") or "")))

    display_name = next((r.display_name for r in invoice_customer.values() if r and r.key == key and r.display_name), "")
    stable_id = next((r.stable_id for r in invoice_customer.values() if r and r.key == key and r.stable_id), None)
    identity_kind = next((r.kind for r in invoice_customer.values() if r and r.key == key), "")
    ambiguous = any(r.ambiguous for r in invoice_customer.values() if r and r.key == key)

    return {
        "customer_key": key,
        "customer_id": stable_id,
        "customer_name": display_name,
        "customer_identity_kind": identity_kind,
        "customer_identity_ambiguous": ambiguous,
        "start_date": start,
        "end_date": end,
        "opening_balance": _money_float(opening),
        "period_debit": _money_float(period_debit),
        "period_credit": _money_float(period_credit),
        "ending_balance": _money_float(ending),
        "items": items,
        "pending_receipts": pending,
        "disclaimer": STATEMENT_DISCLAIMER,
        "note": STATEMENT_SHORT_NOTE,
        "current_state_only": True,
        "historical_as_of": False,
    }
