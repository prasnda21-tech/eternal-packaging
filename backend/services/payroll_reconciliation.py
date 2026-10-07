"""Read-only Payroll ↔ Bank reconciliation (Phase 1).

Zero writes. Aggregates periods, line nets, accrual journals, payments,
and payment journals into deterministic reconciliation statuses.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from services.hr_payroll import LINES, PERIODS, STATUS_FINAL, money_float, to_money
from services.journal_core import JOURNAL_ENTRIES, JOURNAL_LINES, STATUS_POSTED
from services.payroll_journal_posting import SOURCE_TYPE as ACCRUAL_SOURCE
from services.payroll_payment import (
    ACTIVE_STATUSES,
    PAYMENTS,
    PAYMENT_LINES,
    SOURCE_TYPE as PAYMENT_SOURCE,
    STATUS_APPROVED,
    STATUS_CANCELLED,
    STATUS_DRAFT,
    STATUS_PAID,
    STATUS_REVERSED,
    map_payment_visibility,
    payment_visibility_label,
)

STATUS_BELUM_FINAL = "BELUM_FINAL"
STATUS_FINAL_BELUM_JURNAL = "FINAL_BELUM_JURNAL"
STATUS_MISMATCH_NOMINAL = "MISMATCH_NOMINAL"
STATUS_BELUM_ADA_PAYMENT = "BELUM_ADA_PAYMENT"
STATUS_MENUNGGU_PERSETUJUAN = "MENUNGGU_PERSETUJUAN"
STATUS_SIAP_DIPOSTING = "SIAP_DIPOSTING"
STATUS_MISSING_BANK_JOURNAL = "MISSING_BANK_JOURNAL"
STATUS_SESUAI = "SESUAI"
STATUS_SESUAI_TANPA_PEMBAYARAN = "SESUAI_TANPA_PEMBAYARAN"

STATUS_LABELS = {
    STATUS_BELUM_FINAL: "Belum Final",
    STATUS_FINAL_BELUM_JURNAL: "Final, Jurnal Belum Ada",
    STATUS_MISMATCH_NOMINAL: "Nominal Tidak Sesuai",
    STATUS_BELUM_ADA_PAYMENT: "Belum Dibayar",
    STATUS_MENUNGGU_PERSETUJUAN: "Menunggu Persetujuan",
    STATUS_SIAP_DIPOSTING: "Siap Diposting",
    STATUS_MISSING_BANK_JOURNAL: "Jurnal Bank Bermasalah",
    STATUS_SESUAI: "Sesuai",
    STATUS_SESUAI_TANPA_PEMBAYARAN: "Selesai Tanpa Pembayaran",
}

PERM_VIEW_PAYROLL = "payroll.view"
PERM_VIEW_PAYMENT = "payroll_payment.view"

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _money_eq(a: Any, b: Any) -> bool:
    return to_money(a) == to_money(b)


def _scope_label(scope: Any) -> str:
    raw = str(scope or "monthly").strip().lower()
    if raw == "piece":
        return "Borongan"
    return "Bulanan"


def _period_label(period: dict) -> str:
    number = str(period.get("period_number") or "").strip()
    start = str(period.get("start_date") or "")[:10]
    end = str(period.get("end_date") or "")[:10]
    if start and end:
        return f"{start} – {end}" + (f" ({number})" if number else "")
    return number or str(period.get("id") or "")


def _rule(code: str, label: str, ok: bool, left: Any = None, right: Any = None, detail: str = "") -> dict:
    return {
        "code": code,
        "label": label,
        "ok": bool(ok),
        "left": money_float(to_money(left)) if left is not None else None,
        "right": money_float(to_money(right)) if right is not None else None,
        "detail": detail or "",
    }


def _journal_payable_bank_amounts(journal: Optional[dict], payable_account_id: str, bank_coa_id: str) -> Dict[str, Any]:
    """Sum Dr payable / Cr bank on a payment journal."""
    payable_debit = Decimal("0.00")
    bank_credit = Decimal("0.00")
    bank_credit_account = ""
    for line in (journal or {}).get("lines") or []:
        if not isinstance(line, dict):
            continue
        account_id = str(line.get("account_id") or "").strip()
        debit = to_money(line.get("debit"))
        credit = to_money(line.get("credit"))
        if payable_account_id and account_id == payable_account_id and debit > 0:
            payable_debit += debit
        if bank_coa_id and account_id == bank_coa_id and credit > 0:
            bank_credit += credit
            bank_credit_account = account_id
        elif credit > 0 and not bank_coa_id:
            # Prefer the sole credit line as bank when bank_coa missing.
            bank_credit += credit
            bank_credit_account = account_id or bank_credit_account
    if not bank_credit_account:
        credits = [
            str(line.get("account_id") or "").strip()
            for line in (journal or {}).get("lines") or []
            if isinstance(line, dict) and to_money(line.get("credit")) > 0
        ]
        if len(credits) == 1:
            bank_credit_account = credits[0]
            bank_credit = to_money(next(
                (ln.get("credit") for ln in (journal or {}).get("lines") or []
                 if str(ln.get("account_id") or "").strip() == bank_credit_account),
                0,
            ))
    return {
        "payable_debit": payable_debit,
        "bank_credit": bank_credit,
        "bank_credit_account": bank_credit_account,
    }


async def _batch_line_totals(db, period_ids: List[str]) -> Dict[str, dict]:
    if not period_ids:
        return {}
    rows = await db[LINES].aggregate([
        {"$match": {"period_id": {"$in": period_ids}}},
        {"$group": {
            "_id": "$period_id",
            "net": {"$sum": "$net"},
            "line_count": {"$sum": 1},
        }},
    ]).to_list(len(period_ids) + 10)
    out = {}
    for row in rows:
        pid = str(row.get("_id") or "")
        if pid:
            out[pid] = {
                "net": to_money(row.get("net")),
                "line_count": int(row.get("line_count") or 0),
            }
    for pid in period_ids:
        out.setdefault(pid, {"net": Decimal("0.00"), "line_count": 0})
    return out


async def _batch_journals_by_source(db, source_type: str, source_ids: List[str]) -> Dict[str, List[dict]]:
    ids = [str(sid) for sid in source_ids if str(sid or "").strip()]
    if not ids:
        return {}
    headers = await db[JOURNAL_ENTRIES].find(
        {"source_type": source_type, "source_id": {"$in": ids}, "reversal_of": None},
        {"_id": 0},
    ).to_list(len(ids) * 3 + 20)
    by_source: Dict[str, List[dict]] = {sid: [] for sid in ids}
    journal_ids = [str(h.get("id") or "") for h in headers if h.get("id")]
    lines_by_journal: Dict[str, List[dict]] = {jid: [] for jid in journal_ids}
    if journal_ids:
        lines = await db[JOURNAL_LINES].find(
            {"journal_id": {"$in": journal_ids}},
            {"_id": 0},
        ).to_list(len(journal_ids) * 20 + 50)
        for line in lines:
            jid = str(line.get("journal_id") or "")
            if jid in lines_by_journal:
                lines_by_journal[jid].append(line)
    for header in headers:
        sid = str(header.get("source_id") or "")
        jid = str(header.get("id") or "")
        doc = dict(header)
        doc["lines"] = list(lines_by_journal.get(jid) or [])
        by_source.setdefault(sid, []).append(doc)
    return by_source


async def _batch_payments(db, period_ids: List[str]) -> Dict[str, List[dict]]:
    ids = [str(pid) for pid in period_ids if str(pid or "").strip()]
    if not ids:
        return {}
    docs = await db[PAYMENTS].find(
        {"payroll_period_id": {"$in": ids}},
        {"_id": 0},
    ).sort("created_at", 1).to_list(len(ids) * 5 + 50)
    out: Dict[str, List[dict]] = {pid: [] for pid in ids}
    for doc in docs:
        pid = str(doc.get("payroll_period_id") or "")
        if pid in out:
            out[pid].append(doc)
        elif pid:
            out.setdefault(pid, []).append(doc)
    return out


async def _batch_payment_line_sums(db, payment_ids: List[str]) -> Dict[str, Decimal]:
    ids = [str(pid) for pid in payment_ids if str(pid or "").strip()]
    if not ids:
        return {}
    rows = await db[PAYMENT_LINES].aggregate([
        {"$match": {"payment_id": {"$in": ids}}},
        {"$group": {"_id": "$payment_id", "total": {"$sum": "$net_salary"}}},
    ]).to_list(len(ids) + 10)
    return {str(row["_id"]): to_money(row.get("total")) for row in rows if row.get("_id")}


async def _batch_banks(db, cash_bank_ids: List[str]) -> Dict[str, dict]:
    ids = sorted({str(cid).strip() for cid in cash_bank_ids if str(cid or "").strip()})
    if not ids:
        return {}
    docs = await db["cash_bank_accounts"].find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "name": 1, "account_id": 1, "active": 1},
    ).to_list(len(ids) + 5)
    return {str(doc["id"]): doc for doc in docs if doc.get("id")}


def _pick_posted(journals: List[dict]) -> Tuple[Optional[dict], bool]:
    """Return (primary posted journal, duplicate_anomaly)."""
    posted = [j for j in journals if str(j.get("status") or "") == STATUS_POSTED]
    if not posted:
        return None, False
    return posted[0], len(posted) > 1


def _history_marker(payments: List[dict]) -> Optional[str]:
    if any(str(p.get("status") or "") == STATUS_REVERSED for p in payments):
        return "Payment sebelumnya direversal"
    if any(str(p.get("status") or "") == STATUS_CANCELLED for p in payments):
        return "Payment sebelumnya dibatalkan"
    return None


def _active_payments(payments: List[dict]) -> List[dict]:
    return [p for p in payments if str(p.get("status") or "") in ACTIVE_STATUSES]


def reconcile_row(
    period: dict,
    *,
    line_total: dict,
    accrual_journals: List[dict],
    payments: List[dict],
    payment_journals_by_id: Dict[str, List[dict]],
    banks: Dict[str, dict],
    payment_line_sums: Optional[Dict[str, Decimal]] = None,
) -> dict:
    """Pure reconciliation for one period. No I/O."""
    period_id = str(period.get("id") or "")
    net = to_money((line_total or {}).get("net"))
    line_count = int((line_total or {}).get("line_count") or 0)
    mismatches: List[str] = []
    rules: List[dict] = []

    accrual, accrual_dup = _pick_posted(accrual_journals or [])
    if accrual_dup:
        mismatches.append("duplicate_accrual_journal")

    snap = (accrual or {}).get("payroll_payable") if isinstance((accrual or {}).get("payroll_payable"), dict) else {}
    payable_amount = to_money(snap.get("amount")) if snap else None
    payable_account_id = str(snap.get("account_id") or "").strip() if snap else ""
    zero_net = bool(snap.get("zero_net")) if snap else False
    if accrual and snap and payable_amount is None:
        payable_amount = to_money(0)

    active = _active_payments(payments or [])
    if len(active) > 1:
        mismatches.append("multiple_active_payments")
    payment = active[0] if len(active) == 1 else (active[-1] if active else None)
    history_marker = _history_marker(payments or []) if not payment else (
        _history_marker([p for p in payments if p is not payment]) if payments else None
    )

    bank = None
    bank_name = ""
    bank_coa_id = ""
    payment_amount = None
    payment_line_sum = None
    pay_journal = None
    pay_journal_dup = False
    pay_amounts = {"payable_debit": None, "bank_credit": None, "bank_credit_account": ""}

    if payment:
        payment_amount = to_money(payment.get("total_amount"))
        bank_coa_id = str(payment.get("bank_coa_id") or "").strip()
        cash_bank_id = str(payment.get("cash_bank_account_id") or "").strip()
        bank = banks.get(cash_bank_id)
        bank_name = str((bank or {}).get("name") or "")
        if payment_line_sums is not None and payment.get("id") in (payment_line_sums or {}):
            payment_line_sum = payment_line_sums[str(payment["id"])]
        journals = payment_journals_by_id.get(str(payment.get("id") or ""), [])
        pay_journal, pay_journal_dup = _pick_posted(journals)
        if pay_journal_dup:
            mismatches.append("duplicate_payment_journal")
        if pay_journal:
            pay_amounts = _journal_payable_bank_amounts(
                pay_journal,
                str(payment.get("payable_account_id") or payable_account_id or ""),
                bank_coa_id,
            )

    # --- rules (always computed for detail) ---
    period_is_final = str(period.get("status") or "") == STATUS_FINAL
    rules.append(_rule(
        "period_final", "Periode FINAL", period_is_final,
        detail=str(period.get("status") or ""),
    ))
    rules.append(_rule(
        "accrual_posted", "Jurnal akrual posted", bool(accrual),
        detail=(accrual or {}).get("number") or "",
    ))
    net_vs_payable_ok = accrual is not None and payable_amount is not None and _money_eq(net, payable_amount)
    rules.append(_rule(
        "net_eq_payable", "Payroll net = Utang Gaji",
        net_vs_payable_ok if accrual else False,
        left=net, right=payable_amount if payable_amount is not None else None,
    ))
    if accrual and payable_amount is not None and not _money_eq(net, payable_amount):
        mismatches.append("payroll_net_ne_accrual_payable")

    payment_eq_payable_ok = True
    if payment is not None and payable_amount is not None:
        payment_eq_payable_ok = _money_eq(payment_amount, payable_amount)
        if not payment_eq_payable_ok:
            mismatches.append("payment_ne_accrual_payable")
    rules.append(_rule(
        "payment_eq_payable", "Payment = Utang Gaji",
        bool(payment) and payment_eq_payable_ok if payment else False,
        left=payment_amount, right=payable_amount if payable_amount is not None else None,
    ))

    lines_eq_payment_ok = True
    if payment is not None and payment_line_sum is not None:
        lines_eq_payment_ok = _money_eq(payment_line_sum, payment_amount)
        if not lines_eq_payment_ok:
            mismatches.append("payment_lines_ne_payment_total")
    rules.append(_rule(
        "payment_lines_eq_total", "Baris payment = Total payment",
        lines_eq_payment_ok if payment is not None and payment_line_sum is not None else (payment is None),
        left=payment_line_sum, right=payment_amount,
    ))

    pay_journal_ok = True
    bank_coa_ok = True
    if payment and str(payment.get("status") or "") == STATUS_PAID:
        has_ref = bool(str(payment.get("accounting_journal_id") or "").strip())
        if not pay_journal:
            mismatches.append("paid_missing_payment_journal")
            pay_journal_ok = False
        else:
            if not _money_eq(pay_amounts["payable_debit"], payment_amount):
                mismatches.append("payment_journal_debit_ne_total")
                pay_journal_ok = False
            if not _money_eq(pay_amounts["bank_credit"], payment_amount):
                mismatches.append("payment_journal_credit_ne_total")
                pay_journal_ok = False
            if bank_coa_id and pay_amounts["bank_credit_account"] and pay_amounts["bank_credit_account"] != bank_coa_id:
                mismatches.append("bank_coa_mismatch")
                bank_coa_ok = False
            elif bank_coa_id and not pay_amounts["bank_credit_account"]:
                mismatches.append("bank_coa_mismatch")
                bank_coa_ok = False
        if has_ref and not pay_journal:
            mismatches.append("paid_missing_payment_journal")
    rules.append(_rule(
        "payment_journal_balanced", "Bank keluar = Payment",
        pay_journal_ok and bool(pay_journal) if payment and str(payment.get("status") or "") == STATUS_PAID else False,
        left=pay_amounts.get("bank_credit"), right=payment_amount,
    ))
    rules.append(_rule(
        "bank_coa_match", "Bank COA cocok",
        bank_coa_ok if payment and pay_journal else False,
        detail=pay_amounts.get("bank_credit_account") or bank_coa_id,
    ))

    # --- status priority ---
    status = STATUS_BELUM_FINAL
    if not period_is_final:
        status = STATUS_BELUM_FINAL
    elif not accrual:
        status = STATUS_FINAL_BELUM_JURNAL
        mismatches.append("final_missing_accrual_journal")
    elif zero_net and _money_eq(net, 0) and payable_amount is not None and _money_eq(payable_amount, 0):
        if payment and payment_amount is not None and payment_amount > 0:
            status = STATUS_MISMATCH_NOMINAL
        elif "payroll_net_ne_accrual_payable" in mismatches:
            status = STATUS_MISMATCH_NOMINAL
        else:
            status = STATUS_SESUAI_TANPA_PEMBAYARAN
    elif "payroll_net_ne_accrual_payable" in mismatches or "multiple_active_payments" in mismatches or "duplicate_accrual_journal" in mismatches:
        status = STATUS_MISMATCH_NOMINAL
    elif payment is None:
        status = STATUS_BELUM_ADA_PAYMENT
    elif "payment_ne_accrual_payable" in mismatches or "payment_lines_ne_payment_total" in mismatches:
        status = STATUS_MISMATCH_NOMINAL
    elif str(payment.get("status") or "") == STATUS_DRAFT:
        status = STATUS_MENUNGGU_PERSETUJUAN
    elif str(payment.get("status") or "") == STATUS_APPROVED:
        status = STATUS_SIAP_DIPOSTING
    elif str(payment.get("status") or "") == STATUS_PAID:
        if "paid_missing_payment_journal" in mismatches and not pay_journal:
            status = STATUS_MISSING_BANK_JOURNAL
        elif (
            "payment_journal_debit_ne_total" in mismatches
            or "payment_journal_credit_ne_total" in mismatches
            or "bank_coa_mismatch" in mismatches
            or "duplicate_payment_journal" in mismatches
        ):
            status = STATUS_MISMATCH_NOMINAL
        elif (
            net_vs_payable_ok
            and payment_eq_payable_ok
            and pay_journal
            and _money_eq(pay_amounts["payable_debit"], payment_amount)
            and _money_eq(pay_amounts["bank_credit"], payment_amount)
            and bank_coa_ok
            and (payment_line_sum is None or lines_eq_payment_ok)
        ):
            status = STATUS_SESUAI
        else:
            status = STATUS_MISMATCH_NOMINAL
    else:
        status = STATUS_BELUM_ADA_PAYMENT

    visibility = None
    if period_is_final:
        visibility = map_payment_visibility(payment.get("status") if payment else None)

    return {
        "period_id": period_id,
        "period_number": period.get("period_number"),
        "period_label": _period_label(period),
        "period_scope": period.get("period_scope") or "monthly",
        "period_scope_label": _scope_label(period.get("period_scope")),
        "start_date": period.get("start_date"),
        "end_date": period.get("end_date"),
        "payroll_status": period.get("status"),
        "net_total": money_float(net),
        "line_count": line_count,
        "zero_net": zero_net and _money_eq(net, 0),
        "accrual": {
            "journal_id": (accrual or {}).get("id"),
            "journal_number": (accrual or {}).get("number"),
            "date": (accrual or {}).get("date"),
            "posted": bool(accrual),
            "status": (accrual or {}).get("status"),
            "payable_account_id": payable_account_id or None,
            "payable_amount": money_float(payable_amount) if payable_amount is not None else None,
            "zero_net": zero_net,
            "duplicate_anomaly": accrual_dup,
        } if period_is_final or accrual else {
            "journal_id": None,
            "journal_number": None,
            "date": None,
            "posted": False,
            "status": None,
            "payable_account_id": None,
            "payable_amount": None,
            "zero_net": False,
            "duplicate_anomaly": False,
        },
        "payment": {
            "id": payment.get("id") if payment else None,
            "payment_number": payment.get("payment_number") if payment else None,
            "status": payment.get("status") if payment else None,
            "payment_status": visibility,
            "payment_status_label": payment_visibility_label(visibility) if visibility else "",
            "total_amount": money_float(payment_amount) if payment_amount is not None else None,
            "payment_date": payment.get("payment_date") if payment else None,
            "cash_bank_account_id": payment.get("cash_bank_account_id") if payment else None,
            "bank_coa_id": bank_coa_id or None,
            "bank_name": bank_name or None,
            "accounting_journal_id": payment.get("accounting_journal_id") if payment else None,
            "lines_total": money_float(payment_line_sum) if payment_line_sum is not None else None,
        },
        "payment_journal": {
            "journal_id": (pay_journal or {}).get("id") if pay_journal else None,
            "journal_number": (pay_journal or {}).get("number") if pay_journal else None,
            "date": (pay_journal or {}).get("date") if pay_journal else None,
            "posted": bool(pay_journal),
            "status": (pay_journal or {}).get("status") if pay_journal else None,
            "payable_debit": money_float(pay_amounts["payable_debit"]) if pay_amounts.get("payable_debit") is not None and pay_journal else None,
            "bank_credit": money_float(pay_amounts["bank_credit"]) if pay_amounts.get("bank_credit") is not None and pay_journal else None,
            "bank_credit_account": pay_amounts.get("bank_credit_account") or None,
            "duplicate_anomaly": pay_journal_dup,
        },
        "history_marker": history_marker,
        "reconciliation_status": status,
        "reconciliation_label": STATUS_LABELS.get(status, status),
        "mismatches": mismatches,
        "rules": rules,
        "links": {
            "payroll": f"/hr-payroll/penggajian?period={period_id}",
            "payment": f"/hr-payroll/penggajian?period={period_id}" if payment else None,
            "mutasi": (
                f"/kas-bank/mutasi?cash_bank_id={payment.get('cash_bank_account_id')}"
                f"&start_date={str(payment.get('payment_date') or '')[:10]}"
                f"&end_date={str(payment.get('payment_date') or '')[:10]}"
                f"&source_type={PAYMENT_SOURCE}"
                if payment and payment.get("cash_bank_account_id") and payment.get("payment_date")
                else "/kas-bank/mutasi"
            ),
            "general_ledger": (
                f"/buku-besar/buku-besar?account_id={bank_coa_id}"
                f"&start_date={str(payment.get('payment_date') or period.get('end_date') or '')[:10]}"
                f"&end_date={str(payment.get('payment_date') or period.get('end_date') or '')[:10]}"
                if bank_coa_id
                else "/buku-besar/buku-besar"
            ),
            "accrual_journal_id": (accrual or {}).get("id"),
            "payment_journal_id": (pay_journal or {}).get("id") if pay_journal else None,
        },
    }


async def list_reconciliation(
    db,
    *,
    limit: int = DEFAULT_LIMIT,
    status: str = "",
    period_scope: str = "",
    reconciliation_status: str = "",
) -> dict:
    try:
        limit = int(limit or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))

    filt: Dict[str, Any] = {}
    scope = str(period_scope or "").strip().lower()
    if scope in ("monthly", "piece"):
        if scope == "monthly":
            filt["$or"] = [
                {"period_scope": "monthly"},
                {"period_scope": {"$exists": False}},
                {"period_scope": None},
            ]
        else:
            filt["period_scope"] = "piece"
    period_status = str(status or "").strip().upper()
    if period_status:
        filt["status"] = period_status

    periods = await db[PERIODS].find(filt, {"_id": 0}).sort(
        [("updated_at", -1), ("created_at", -1)]
    ).to_list(limit * 3 if reconciliation_status else limit)
    # Over-fetch slightly when filtering by recon status, then trim.
    period_ids = [str(p["id"]) for p in periods if p.get("id")]
    line_totals = await _batch_line_totals(db, period_ids)
    accrual_map = await _batch_journals_by_source(db, ACCRUAL_SOURCE, period_ids)
    payment_map = await _batch_payments(db, period_ids)
    payment_ids = []
    cash_bank_ids = []
    for rows in payment_map.values():
        for pay in rows:
            if str(pay.get("status") or "") in ACTIVE_STATUSES:
                payment_ids.append(str(pay.get("id") or ""))
                if pay.get("cash_bank_account_id"):
                    cash_bank_ids.append(str(pay["cash_bank_account_id"]))
    payment_journal_map = await _batch_journals_by_source(db, PAYMENT_SOURCE, payment_ids)
    payment_journals_by_id = {pid: payment_journal_map.get(pid, []) for pid in payment_ids}
    banks = await _batch_banks(db, cash_bank_ids)
    payment_line_sums = await _batch_payment_line_sums(db, payment_ids)

    rows = []
    wanted = str(reconciliation_status or "").strip().upper()
    for period in periods:
        pid = str(period.get("id") or "")
        pay_docs = payment_map.get(pid) or []
        active = _active_payments(pay_docs)
        active_id = str(active[0]["id"]) if len(active) == 1 else (
            str(active[-1]["id"]) if active else ""
        )
        row = reconcile_row(
            period,
            line_total=line_totals.get(pid) or {"net": Decimal("0.00"), "line_count": 0},
            accrual_journals=accrual_map.get(pid) or [],
            payments=pay_docs,
            payment_journals_by_id=payment_journals_by_id,
            banks=banks,
            payment_line_sums=payment_line_sums,
        )
        if wanted and row["reconciliation_status"] != wanted:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break

    return {
        "items": rows,
        "count": len(rows),
        "limit": limit,
        "status_labels": dict(STATUS_LABELS),
    }


async def get_reconciliation_detail(db, period_id: str) -> dict:
    period = await db[PERIODS].find_one({"id": str(period_id)}, {"_id": 0})
    if not period:
        raise HTTPException(404, "Periode payroll tidak ditemukan.")
    pid = str(period["id"])
    line_totals = await _batch_line_totals(db, [pid])
    accrual_map = await _batch_journals_by_source(db, ACCRUAL_SOURCE, [pid])
    payment_map = await _batch_payments(db, [pid])
    pay_docs = payment_map.get(pid) or []
    payment_ids = [str(p["id"]) for p in pay_docs if p.get("id")]
    cash_bank_ids = [str(p["cash_bank_account_id"]) for p in pay_docs if p.get("cash_bank_account_id")]
    payment_journal_map = await _batch_journals_by_source(db, PAYMENT_SOURCE, payment_ids)
    banks = await _batch_banks(db, cash_bank_ids)
    payment_line_sums = await _batch_payment_line_sums(db, payment_ids)
    row = reconcile_row(
        period,
        line_total=line_totals.get(pid) or {"net": Decimal("0.00"), "line_count": 0},
        accrual_journals=accrual_map.get(pid) or [],
        payments=pay_docs,
        payment_journals_by_id=payment_journal_map,
        banks=banks,
        payment_line_sums=payment_line_sums,
    )
    row["payment_history"] = [
        {
            "id": p.get("id"),
            "payment_number": p.get("payment_number"),
            "status": p.get("status"),
            "total_amount": p.get("total_amount"),
            "payment_date": p.get("payment_date"),
            "cancelled_at": p.get("cancelled_at"),
            "reversed_at": p.get("reversed_at"),
        }
        for p in pay_docs
    ]
    return {"item": row}
