"""Owner Analytics Dashboard — thin read-only facade (Phase 2 + 3A + 3B + 3C).

Reuses sales_dashboard omzet (after_discount / invoice_date / finalized).
Adds Average PO Value from orders.selling_total when prices are complete.
Operational counts reuse delivery schedule + quantity delivery predicates.
Phase 3A reuses financial_dashboard cash helpers and cash_bank_owner_recon MTD.
Phase 3B reuses accounts_receivable / accounts_payable current-invoice loaders
for accounting outstanding + aging (Saat Ini). Does not recompute settlements.
Phase 3C aggregates active purchase_invoices by invoice_date / grand_total
(Option A). Returns and payments do not reduce the purchasing KPI.

No branch filter. No profit calculation. No OTP/OTD percentages.
GET must not post, reverse, or backfill business documents.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from quantity_rules import is_fully_delivered
from services.accounts_payable import (
    BUCKET_1_30 as AP_BUCKET_1_30,
    BUCKET_31_60 as AP_BUCKET_31_60,
    BUCKET_61_90 as AP_BUCKET_61_90,
    BUCKET_NOT_DUE as AP_BUCKET_NOT_DUE,
    BUCKET_OVER_90 as AP_BUCKET_OVER_90,
    UNKNOWN_SUPPLIER_KEY,
    _current_invoice_sets as _ap_current_invoice_sets,
    supplier_display_name,
    supplier_identity_key,
)
from services.accounts_receivable import (
    BUCKET_1_30 as AR_BUCKET_1_30,
    BUCKET_31_60 as AR_BUCKET_31_60,
    BUCKET_61_90 as AR_BUCKET_61_90,
    BUCKET_NOT_DUE as AR_BUCKET_NOT_DUE,
    BUCKET_OVER_90 as AR_BUCKET_OVER_90,
    _current_invoice_sets as _ar_current_invoice_sets,
)
from services.cash_bank_owner_recon import get_owner_recon_mtd_global
from services.dashboard_today import partition_orders_by_delivery, wib_today
from services.delivery_schedule import (
    DEFAULT_LEAD_TIME_DAYS,
    SCHEDULE_PROJECTION,
    schedule_rows,
)
from services.financial_dashboard import (
    _account_index,
    _bucket_keys,
    _bucket_of,
    _cash_movement,
    _cash_movement_trend,
    _grain_for,
    _position,
)
from services.financial_statement_common import load_statement_accounts, parse_financial_date_range
from services.purchase_common import with_active_filter
from services.purchase_invoices import COLLECTION as PURCHASE_INVOICE_COLLECTION
from services.sales_dashboard import (
    SalesDashboardValidationError,
    _calendar_day,
    _share_percent,
    get_sales_dashboard,
)
from services.status_po import STAGES

AP_COVERAGE_NOTE = (
    "Nilai hutang mengikuti jurnal AP yang telah diposting. "
    "Penyesuaian supplier credit/advance/return yang belum tercakup engine "
    "tidak termasuk dalam perhitungan."
)
PURCHASE_BASIS_NOTE = (
    "Nilai grand_total faktur pembelian aktif. Belum dikurangi retur; "
    "diskon dan PPN belum dipisahkan dalam model faktur."
)
PURCHASE_DISCLOSURE = (
    "Nilai faktur aktif sebelum retur. "
    "Diskon dan PPN belum dipisahkan dalam model faktur."
)
TOP_SUPPLIER_LIMIT = 5
OTHER_SUPPLIER_KEY = "other:"
OTHER_SUPPLIER_LABEL = "Lainnya"

PERM_VIEW = "dashboard.owner.view"
OWNER_ROLES = frozenset({"direktur_utama", "holding_team"})
OWNER_DENIED = "Dashboard Owner hanya untuk role Direktur atau Holding."
PERM_DENIED = f"Permission '{PERM_VIEW}' diperlukan"

WIB = timezone(timedelta(hours=7))
MONEY_Q = Decimal("0.01")
MAX_RANGE_DAYS = 3660
OPS_ORDER_LIMIT = 5000
DELIVERY_PREVIEW = 8
EXCEPTION_LIMIT = 12

OPS_PROJECTION = {
    **SCHEDULE_PROJECTION,
    "hold": 1,
    "priority": 1,
    "outlet": 1,
    "order_date": 1,
    "qty_delivered": 1,
    "packing_required_total": 1,
    "packing_info": 1,
    "delivery_deadline_effective": 1,
    "delivery_deadline_status": 1,
    # Needed so shipment_is_active can exclude cancelled/superseded SJ.
    "shipments.cancelled": 1,
    "shipments.is_cancelled": 1,
    "shipments.deleted": 1,
    "shipments.is_deleted": 1,
    "shipments.superseded": 1,
    "shipments.superseded_by_shipment_id": 1,
    "shipments.status": 1,
    "shipments.sj_status": 1,
}


class OwnerAnalyticsValidationError(ValueError):
    pass


class OwnerAnalyticsAccessError(PermissionError):
    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def owner_role_allowed(user: Optional[dict]) -> bool:
    role = str((user or {}).get("role") or "").strip().lower()
    return role in OWNER_ROLES


def assert_owner_dashboard_access(user: Optional[dict], permissions: Optional[Sequence[str]]) -> None:
    """Role allowlist AND permission. Admin is not a bypass even with the key."""
    if not owner_role_allowed(user):
        raise OwnerAnalyticsAccessError(OWNER_DENIED)
    perms = set(permissions or [])
    if PERM_VIEW not in perms:
        raise OwnerAnalyticsAccessError(PERM_DENIED)


def _money(raw: Any) -> Optional[Decimal]:
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    try:
        return Decimal(str(raw).strip()).quantize(MONEY_Q)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _num(amount: Optional[Decimal]) -> Optional[float]:
    if amount is None:
        return None
    return float(amount.quantize(MONEY_Q))


def _parse_range(start_date: str, end_date: str) -> Tuple[str, str]:
    start, end = parse_financial_date_range(
        start_date,
        end_date,
        empty_message="Rentang tanggal wajib diisi",
        start_invalid_message="Tanggal mulai tidak valid",
        end_invalid_message="Tanggal akhir tidak valid",
        order_message="Tanggal mulai tidak boleh setelah tanggal akhir",
        error_cls=OwnerAnalyticsValidationError,
    )
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    if span > MAX_RANGE_DAYS:
        raise OwnerAnalyticsValidationError("Rentang tanggal terlalu panjang")
    return start, end


def _next_day(iso_day: str) -> str:
    return (date.fromisoformat(iso_day) + timedelta(days=1)).isoformat()


def _date_query(start: str, end: str) -> dict:
    return {"$gte": start, "$lt": _next_day(end)}


def _section_error(exc: Exception) -> dict:
    return {
        "status": "error",
        "detail": str(exc) or "Gagal memuat",
    }


async def _sla_read_context(db) -> tuple:
    """Official sla_working_days + operational holidays. Read only."""
    from services.tanggal_pengiriman import classify_delivery_deadline_setting
    from services.production_wave2 import holiday_dates

    doc = await db.app_settings.find_one({"key": "delivery_deadline"}, {"_id": 0})
    days = int(classify_delivery_deadline_setting(doc)["sla_working_days"])
    return days, await holiday_dates(db)


async def _lead_time_days(db) -> int:
    days, _ = await _sla_read_context(db)
    return days


def _build_aov(omzet: dict) -> dict:
    status = str((omzet or {}).get("status") or "")
    if status == "error":
        return {"status": "error", "amount": None, "invoice_count": None, "detail": omzet.get("detail")}
    if status in {"empty", ""}:
        return {
            "status": "empty",
            "amount": None,
            "invoice_count": 0,
            "detail": "Belum ada invoice final pada periode ini.",
            "basis": "after_discount / invoice_count",
        }
    count = omzet.get("invoice_count")
    amount = omzet.get("amount")
    try:
        count_i = int(count)
    except (TypeError, ValueError):
        return {
            "status": "error",
            "amount": None,
            "invoice_count": None,
            "detail": "Jumlah invoice tidak dapat dibaca.",
        }
    if count_i <= 0 or amount is None:
        return {
            "status": "empty",
            "amount": None,
            "invoice_count": count_i,
            "detail": "Belum ada invoice final pada periode ini.",
            "basis": "after_discount / invoice_count",
        }
    try:
        avg = (Decimal(str(amount)) / Decimal(count_i)).quantize(MONEY_Q)
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return {
            "status": "error",
            "amount": None,
            "invoice_count": count_i,
            "detail": "AOV tidak dapat dihitung.",
        }
    return {
        "status": status if status in {"complete", "zero", "incomplete"} else "complete",
        "amount": float(avg),
        "invoice_count": count_i,
        "basis": "after_discount / invoice_count",
        "detail": None,
    }


async def get_average_po_value(db, *, start_date: str, end_date: str) -> dict:
    """Average of selling_total for non-cancelled POs with complete prices."""
    start, end = _parse_range(start_date, end_date)
    base = {
        "cancelled": {"$ne": True},
        "order_date": _date_query(start, end),
    }
    qualifying = await db.orders.find(
        {**base, "selling_prices_complete": True},
        {"_id": 0, "id": 1, "selling_total": 1, "order_date": 1, "branch_id": 1},
    ).to_list(OPS_ORDER_LIMIT)
    excluded = await db.orders.count_documents(
        {
            **base,
            "$or": [
                {"selling_prices_complete": {"$ne": True}},
                {"selling_prices_complete": {"$exists": False}},
            ],
        }
    )
    if not qualifying:
        return {
            "status": "empty" if excluded == 0 else "incomplete",
            "amount": None,
            "po_count": 0,
            "excluded_incomplete_price_count": int(excluded),
            "basis": "selling_total",
            "date_basis": "order_date",
            "detail": (
                "Belum ada PO dengan harga lengkap pada periode ini."
                if excluded == 0
                else f"{int(excluded)} PO tidak dihitung karena harga belum lengkap."
            ),
        }
    total = Decimal("0.00")
    for row in qualifying:
        amount = _money(row.get("selling_total"))
        if amount is None:
            return {
                "status": "error",
                "amount": None,
                "po_count": None,
                "excluded_incomplete_price_count": int(excluded),
                "basis": "selling_total",
                "date_basis": "order_date",
                "detail": "Ada PO dengan selling_total yang tidak dapat dibaca.",
            }
        total += amount
    count = len(qualifying)
    avg = (total / Decimal(count)).quantize(MONEY_Q)
    status = "incomplete" if excluded else ("zero" if avg == 0 else "complete")
    detail = None
    if excluded:
        detail = f"{int(excluded)} PO tidak dihitung karena harga belum lengkap."
    return {
        "status": status,
        "amount": _num(avg),
        "po_count": count,
        "excluded_incomplete_price_count": int(excluded),
        "total_selling": _num(total),
        "basis": "selling_total",
        "date_basis": "order_date",
        "detail": detail,
    }


def _slim_delivery_row(row: dict) -> dict:
    return {
        "id": row.get("shipment_id") or row.get("id"),
        "order_id": row.get("id"),
        "po_number": row.get("order_number") or row.get("po_number"),
        "customer": row.get("customer_name") or row.get("customer"),
        "delivery_date": row.get("effective_delivery_date") or row.get("tanggal_pengiriman"),
        "status": row.get("status"),
        "status_label": row.get("status_label") or row.get("status"),
        "sj_number": row.get("latest_sj_number") or row.get("sj_label"),
    }


def _build_operations(
    orders: List[dict],
    *,
    lead_time_days: int,
    today: date,
    holidays: Optional[list] = None,
) -> dict:
    active, completed = partition_orders_by_delivery(orders, is_fully_delivered)
    today_key = today.isoformat()
    stage_breakdown = []
    for idx, label in enumerate(STAGES):
        cnt = sum(1 for o in active if int(o.get("current_stage") or 0) == idx)
        stage_breakdown.append({"stage": idx, "label": label, "count": cnt})

    late_rows: List[dict] = []
    today_rows: List[dict] = []
    upcoming_rows: List[dict] = []
    for order in orders:
        for row in schedule_rows(
            order, today=today_key, lead_time_days=lead_time_days, holidays=holidays,
        ):
            code = str(row.get("status") or "")
            slim = _slim_delivery_row(row)
            if code == "terlambat":
                late_rows.append(slim)
            elif code == "hari_ini":
                today_rows.append(slim)
            elif code == "terjadwal":
                upcoming_rows.append(slim)

    late_rows.sort(key=lambda r: (r.get("delivery_date") or "", r.get("po_number") or ""))
    today_rows.sort(key=lambda r: (r.get("po_number") or ""))
    upcoming_rows.sort(key=lambda r: (r.get("delivery_date") or "", r.get("po_number") or ""))

    bottleneck = None
    if active:
        top = max(stage_breakdown, key=lambda row: row["count"])
        if top["count"] > 0:
            bottleneck = {"stage": top["stage"], "label": top["label"], "count": top["count"]}

    exceptions: List[dict] = []
    for row in late_rows[:EXCEPTION_LIMIT]:
        exceptions.append({
            "category": "pengiriman",
            "label": "Pengiriman terlambat",
            "po_number": row.get("po_number"),
            "customer": row.get("customer"),
            "date": row.get("delivery_date"),
            "detail": "Jadwal pengiriman lewat tanggal efektif (baris SJ aktif).",
            "href_hint": "delivery_schedule_late",
        })
    hold_active = [o for o in active if o.get("hold")]
    for order in hold_active[: max(0, EXCEPTION_LIMIT - len(exceptions))]:
        exceptions.append({
            "category": "po",
            "label": "PO HOLD",
            "po_number": order.get("po_number"),
            "customer": order.get("customer") or order.get("customer_name"),
            "date": str(order.get("order_date") or "")[:10] or None,
            "detail": "PO aktif berstatus HOLD.",
            "href_hint": "status_po",
        })
    if bottleneck and bottleneck["count"] >= 3 and len(exceptions) < EXCEPTION_LIMIT:
        exceptions.append({
            "category": "produksi",
            "label": "Bottleneck tahap",
            "po_number": None,
            "customer": None,
            "date": None,
            "detail": f"{bottleneck['count']} PO aktif di tahap {bottleneck['label']}.",
            "href_hint": "production",
        })

    # Disjoint PO partition for chart: aktif vs selesai. Late is overlay on aktif.
    late_po_ids = {
        str(r.get("order_id") or "")
        for r in late_rows
        if r.get("order_id")
    }
    late_active_po = sum(1 for o in active if str(o.get("id") or "") in late_po_ids)

    return {
        "status": "complete" if orders else "empty",
        "po_active": {
            "status": "complete",
            "count": len(active),
            "label": "PO aktif",
            "detail": "PO belum terkirim penuh (quantity rules).",
        },
        "po_completed": {
            "status": "complete",
            "count": len(completed),
            "label": "PO selesai",
            "detail": "PO terkirim penuh.",
        },
        "po_status": {
            "status": "complete",
            "partition": "disjoint_active_completed",
            "aktif": len(active),
            "selesai": len(completed),
            "terlambat_overlay": late_active_po,
            "detail": (
                "Aktif dan Selesai saling lepas. "
                "Terlambat adalah overlay pada PO aktif yang punya baris jadwal terlambat."
            ),
        },
        "stage_breakdown": {
            "status": "complete",
            "unit": "po_count",
            "rows": stage_breakdown,
            "active_po_count": len(active),
            "bottleneck": bottleneck,
            "detail": "Jumlah PO aktif per current_stage. Bukan qty produksi.",
        },
        "delivery": {
            "status": "complete",
            "late_count": len(late_rows),
            "today_count": len(today_rows),
            "upcoming_count": len(upcoming_rows),
            "late_label": "Pengiriman terlambat (jadwal SJ aktif)",
            "source": "delivery_schedule.schedule_rows",
            "late": late_rows[:DELIVERY_PREVIEW],
            "today": today_rows[:DELIVERY_PREVIEW],
            "upcoming": upcoming_rows[:DELIVERY_PREVIEW],
            "detail": (
                "Hitungan baris jadwal pengiriman per SJ aktif bertanggal. "
                "Buffer stock tidak dihitung terlambat. Bukan OTD%."
            ),
        },
        "exceptions": {
            "status": "empty" if not exceptions else "complete",
            "items": exceptions,
            "detail": None if exceptions else "Tidak ada pengecualian operasional pada data saat ini.",
        },
        "otp_otd": {
            "status": "not_ready",
            "otp": None,
            "otd": None,
            "detail": "OTP/OTD persen ditunda — sumber deadline produksi/OTD belum memadai.",
        },
    }


def _profit_placeholder() -> dict:
    return {
        "status": "not_ready",
        "gross_margin": None,
        "gross_profit": None,
        "net_profit": None,
        "detail": "Data Belum Siap.",
    }


def _finance_cash_card(metric: dict, *, as_of: str) -> dict:
    return {
        "status": metric.get("status") or "unavailable",
        "amount": metric.get("amount"),
        "label": "Saldo Kas & Bank",
        "scope": "current_snapshot",
        "scope_label": "Saat Ini",
        "as_of": as_of,
        "source": metric.get("source") or "balance_sheet.account_type_BANK",
        "detail": metric.get("detail"),
        "warning": metric.get("warning"),
        "note": "Saldo ledger COA BANK. Bukan otomatis saldo rekening yang sudah direkonsiliasi.",
    }


def _finance_flow_card(cash: dict, *, field: str, label: str, start: str, end: str) -> dict:
    status = str(cash.get("status") or "unavailable")
    amount = cash.get(field)
    if status == "error":
        amount = None
    return {
        "status": status,
        "amount": amount,
        "label": label,
        "scope": "selected_period",
        "scope_label": "Periode",
        "start_date": start,
        "end_date": end,
        "source": cash.get("source") or "journal_lines.account_type_BANK",
        "detail": cash.get("detail"),
        "blocker_code": cash.get("blocker_code"),
        "note": cash.get("note"),
    }


def _finance_arap_from_rows(
    rows: Sequence[dict],
    *,
    as_of: str,
    label: str,
    source: str,
    not_due_key: str,
    bucket_keys: Sequence[str],
    note: Optional[str] = None,
) -> dict:
    """Map precomputed AR/AP invoice rows into Owner Saat Ini card. No settlement math."""
    buckets = {key: Decimal("0.00") for key in bucket_keys}
    total = Decimal("0.00")
    classified_positive = Decimal("0.00")
    overdue_amount = Decimal("0.00")
    not_due_amount = Decimal("0.00")
    invoice_count = 0
    overdue_invoice_count = 0
    fallback_count = 0
    fallback_amount = Decimal("0.00")
    mismatch_count = 0
    mismatch_amount = Decimal("0.00")
    has_negative = False
    warnings: List[str] = []

    for row in rows:
        amount = _money(row.get("accounting_outstanding"))
        if amount is None:
            amount = Decimal("0.00")
        total += amount
        invoice_count += 1
        if amount < -MONEY_Q:
            has_negative = True
        bucket = row.get("aging_bucket")
        if amount > 0 and bucket in buckets:
            buckets[bucket] += amount
            classified_positive += amount
            if bucket == not_due_key:
                not_due_amount += amount
            else:
                overdue_amount += amount
                overdue_invoice_count += 1
        if amount > 0 and row.get("due_date_fallback"):
            fallback_count += 1
            fallback_amount += amount
        if amount > 0 and row.get("document_mismatch"):
            mismatch_count += 1
            mismatch_amount += amount
        for code in row.get("integrity_warnings") or []:
            warnings.append(str(code))

    aging_sum = sum(buckets.values(), Decimal("0.00"))
    reconcile_ok = aging_sum == classified_positive
    completeness = "complete"
    detail_parts: List[str] = []
    if has_negative:
        completeness = "incomplete"
        detail_parts.append("Terdapat saldo accounting negatif.")
    if not reconcile_ok:
        completeness = "incomplete"
        detail_parts.append("Jumlah aging tidak selaras dengan outstanding terklasifikasi.")
    if fallback_count:
        completeness = "incomplete"
        detail_parts.append(
            f"{fallback_count} dokumen memakai tanggal jatuh tempo cadangan."
        )
    if mismatch_count:
        completeness = "incomplete"
        detail_parts.append(
            f"{mismatch_count} dokumen accounting aktif tidak selaras status dokumen."
        )
    if warnings:
        # Presence of integrity warnings on any row → incomplete, without listing invoices.
        completeness = "incomplete"
        if "Peringatan integritas accounting terdeteksi." not in detail_parts:
            detail_parts.append("Peringatan integritas accounting terdeteksi.")

    status = "zero" if total == 0 and completeness == "complete" else completeness
    if completeness == "complete" and total != 0:
        status = "complete"

    return {
        "status": status,
        "amount": _num(total),
        "label": label,
        "scope": "current_snapshot",
        "scope_label": "Saat Ini",
        "as_of": as_of,
        "source": source,
        "overdue_amount": _num(overdue_amount),
        "not_due_amount": _num(not_due_amount),
        "aging": {
            "not_due": _num(buckets[not_due_key]),
            "d1_30": _num(buckets[bucket_keys[1]]),
            "d31_60": _num(buckets[bucket_keys[2]]),
            "d61_90": _num(buckets[bucket_keys[3]]),
            "over_90": _num(buckets[bucket_keys[4]]),
        },
        "invoice_count": invoice_count,
        "overdue_invoice_count": overdue_invoice_count,
        "due_date_fallback_count": fallback_count,
        "due_date_fallback_amount": _num(fallback_amount),
        "document_mismatch_count": mismatch_count,
        "document_mismatch_amount": _num(mismatch_amount),
        "completeness": completeness,
        "aging_reconcile_ok": reconcile_ok,
        "detail": " ".join(detail_parts) if detail_parts else None,
        "note": note,
    }


async def _owner_receivables(db, *, as_of: str) -> dict:
    ref, _all_rows, rows = await _ar_current_invoice_sets(
        db,
        aging_reference_date=as_of,
        q=None,
        customer_key=None,
        sales=None,
        ar_account_id=None,
        aging_bucket=None,
    )
    return _finance_arap_from_rows(
        rows,
        as_of=ref,
        label="Piutang Usaha",
        source="accounts_receivable.accounting_outstanding",
        not_due_key=AR_BUCKET_NOT_DUE,
        bucket_keys=(
            AR_BUCKET_NOT_DUE,
            AR_BUCKET_1_30,
            AR_BUCKET_31_60,
            AR_BUCKET_61_90,
            AR_BUCKET_OVER_90,
        ),
        note="Piutang accounting dari jurnal AREC yang sudah diposting. Saat Ini (WIB).",
    )


async def _owner_payables(db, *, as_of: str) -> dict:
    ref, _all_rows, rows = await _ap_current_invoice_sets(
        db,
        aging_reference_date=as_of,
        q=None,
        supplier_key=None,
        ap_account_id=None,
        aging_bucket=None,
    )
    return _finance_arap_from_rows(
        rows,
        as_of=ref,
        label="Hutang Usaha",
        source="accounts_payable.accounting_outstanding",
        not_due_key=AP_BUCKET_NOT_DUE,
        bucket_keys=(
            AP_BUCKET_NOT_DUE,
            AP_BUCKET_1_30,
            AP_BUCKET_31_60,
            AP_BUCKET_61_90,
            AP_BUCKET_OVER_90,
        ),
        note=AP_COVERAGE_NOTE,
    )


async def _build_finance(db, *, start: str, end: str, today: date) -> dict:
    """Cash & Bank + AR/AP Owner section. Isolated failures must not break Phase 2."""
    as_of = today.isoformat()
    finance: Dict[str, Any] = {
        "status": "complete",
        "timezone": "Asia/Jakarta",
        "as_of_date": as_of,
        "period_start": start,
        "period_end": end,
    }
    accounts = None
    try:
        accounts = _account_index(await load_statement_accounts(db))
    except Exception as exc:
        err = _section_error(exc)
        finance["cash_balance"] = {**err, "label": "Saldo Kas & Bank", "scope_label": "Saat Ini", "amount": None}
        finance["cash_in"] = {**err, "label": "Kas Masuk", "scope_label": "Periode", "amount": None}
        finance["cash_out"] = {**err, "label": "Kas Keluar", "scope_label": "Periode", "amount": None}
        finance["cash_trend"] = {**err, "grain": None, "points": [], "timezone": "Asia/Jakarta"}
        finance["reconciliation"] = {**err, "status_label": "Belum Lengkap", "unmatched_count": None, "review_count": None}

    if accounts is not None:
        try:
            position = await _position(db, as_of, None, None)
            cash_metric = position.get("cash_and_bank") if isinstance(position, dict) else None
            if not isinstance(cash_metric, dict):
                raise RuntimeError("Saldo kas/bank tidak tersedia")
            finance["cash_balance"] = _finance_cash_card(cash_metric, as_of=as_of)
        except Exception as exc:
            finance["cash_balance"] = {
                **_section_error(exc),
                "label": "Saldo Kas & Bank",
                "scope_label": "Saat Ini",
                "amount": None,
                "as_of": as_of,
            }

        try:
            cash = await _cash_movement(db, start, end, accounts)
            finance["cash_in"] = _finance_flow_card(cash, field="cash_in", label="Kas Masuk", start=start, end=end)
            finance["cash_out"] = _finance_flow_card(cash, field="cash_out", label="Kas Keluar", start=start, end=end)
        except Exception as exc:
            err = _section_error(exc)
            finance["cash_in"] = {**err, "label": "Kas Masuk", "scope_label": "Periode", "amount": None}
            finance["cash_out"] = {**err, "label": "Kas Keluar", "scope_label": "Periode", "amount": None}

        try:
            trend = await _cash_movement_trend(db, start, end, accounts)
            finance["cash_trend"] = trend
            cash_in = finance.get("cash_in") or {}
            cash_out = finance.get("cash_out") or {}
            if (
                cash_in.get("status") in {"complete", "zero", "incomplete"}
                and trend.get("status") in {"complete", "zero", "incomplete"}
                and cash_in.get("amount") is not None
                and trend.get("cash_in") is not None
                and round(float(cash_in["amount"]), 2) != round(float(trend["cash_in"]), 2)
            ):
                finance["cash_trend"] = {
                    **trend,
                    "status": "error",
                    "points": [],
                    "detail": "Agregasi tren kas tidak konsisten dengan total Kas Masuk.",
                }
            elif (
                cash_out.get("status") in {"complete", "zero", "incomplete"}
                and trend.get("status") in {"complete", "zero", "incomplete"}
                and cash_out.get("amount") is not None
                and trend.get("cash_out") is not None
                and round(float(cash_out["amount"]), 2) != round(float(trend["cash_out"]), 2)
            ):
                finance["cash_trend"] = {
                    **trend,
                    "status": "error",
                    "points": [],
                    "detail": "Agregasi tren kas tidak konsisten dengan total Kas Keluar.",
                }
        except Exception as exc:
            finance["cash_trend"] = {
                **_section_error(exc),
                "grain": None,
                "points": [],
                "timezone": "Asia/Jakarta",
                "cash_in": None,
                "cash_out": None,
            }

        try:
            finance["reconciliation"] = await get_owner_recon_mtd_global(db, today=today)
        except Exception as exc:
            finance["reconciliation"] = {
                **_section_error(exc),
                "status_label": "Belum Lengkap",
                "unmatched_count": None,
                "review_count": None,
                "label": "Rekonsiliasi Bulan Ini",
                "scope_label": "Bulan Ini (WIB)",
            }

    # Phase 3B — Saat Ini snapshot; independent of Owner period filters and cash COA load.
    try:
        finance["receivables"] = await _owner_receivables(db, as_of=as_of)
    except Exception as exc:
        finance["receivables"] = {
            **_section_error(exc),
            "label": "Piutang Usaha",
            "scope_label": "Saat Ini",
            "amount": None,
            "overdue_amount": None,
            "not_due_amount": None,
            "aging": None,
            "as_of": as_of,
        }

    try:
        finance["payables"] = await _owner_payables(db, as_of=as_of)
    except Exception as exc:
        finance["payables"] = {
            **_section_error(exc),
            "label": "Hutang Usaha",
            "scope_label": "Saat Ini",
            "amount": None,
            "overdue_amount": None,
            "not_due_amount": None,
            "aging": None,
            "as_of": as_of,
            "note": AP_COVERAGE_NOTE,
        }

    subsection_statuses = [
        str((finance.get(key) or {}).get("status") or "")
        for key in (
            "cash_balance",
            "cash_in",
            "cash_out",
            "cash_trend",
            "reconciliation",
            "receivables",
            "payables",
        )
    ]
    if any(status == "error" for status in subsection_statuses):
        finance["status"] = "incomplete"
    elif any(status in {"incomplete", "unavailable"} for status in subsection_statuses):
        finance["status"] = "incomplete"
    else:
        finance["status"] = "complete"
    return finance


def _drilldowns(permissions: Optional[Sequence[str]]) -> dict:
    perms = set(permissions or [])
    def can(*keys: str) -> bool:
        return any(k in perms for k in keys)

    return {
        "sales_dashboard": {
            "allowed": can("dashboard.sales.view", "nav.dashboard.sales"),
            "path": "/dashboard/penjualan",
        },
        "status_po": {
            "allowed": can("nav.reports.status_po", "schedule.view"),
            "path": "/laporan/status-po",
        },
        "delivery_schedule": {
            "allowed": can("delivery.schedule.view", "schedule.view", "nav.delivery_schedule"),
            "path": "/produksi/jadwal-pengiriman",
        },
        "delivery_schedule_late": {
            "allowed": can("delivery.schedule.view", "schedule.view", "nav.delivery_schedule"),
            "path": "/produksi/jadwal-pengiriman?status=terlambat",
        },
        "production": {
            "allowed": can("nav.production_schedule", "schedule.view"),
            "path": "/produksi",
        },
        "dashboard_production": {
            "allowed": can("dashboard.production.view", "nav.dashboard"),
            "path": "/dashboard",
        },
    }


def _purchasing_error(start: str, end: str, detail: str) -> dict:
    return {
        "status": "error",
        "scope": "period",
        "scope_label": "Periode",
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Jakarta",
        "total": {
            "status": "error",
            "label": "Total Pembelian Faktur",
            "amount": None,
            "invoice_count": None,
            "basis": "grand_total",
            "detail": detail,
        },
        "trend": {"status": "error", "grain": None, "points": [], "detail": detail},
        "top_suppliers": {"status": "error", "rows": [], "detail": detail},
        "distribution": {
            "status": "error",
            "groups": [],
            "show_share": False,
            "detail": detail,
        },
        "completeness": "error",
        "note": PURCHASE_DISCLOSURE,
        "detail": detail,
    }


async def _load_eligible_purchase_invoices(db, *, start: str, end: str) -> List[dict]:
    """Active purchase invoices in invoice_date range. One row per invoice id."""
    query = with_active_filter({"invoice_date": {"$gte": start, "$lt": _next_day(end)}})
    cursor = db[PURCHASE_INVOICE_COLLECTION].find(
        query,
        {"_id": 0, "id": 1, "invoice_date": 1, "grand_total": 1, "supplier": 1, "cancelled": 1},
    )
    seen: Dict[str, dict] = {}
    async for doc in cursor:
        invoice_id = str(doc.get("id") or "").strip()
        if not invoice_id or invoice_id in seen:
            continue
        seen[invoice_id] = doc
    return list(seen.values())


async def _build_purchasing(db, *, start: str, end: str) -> dict:
    """Phase 3C — period purchasing from active purchase_invoices.grand_total."""
    invoices = await _load_eligible_purchase_invoices(db, start=start, end=end)
    grain = _grain_for(start, end)
    keys = _bucket_keys(start, end, grain)
    empty_points = [
        {"period": key, "amount": 0.0, "invoice_count": 0}
        for key in keys
    ]
    if not invoices:
        return {
            "status": "empty",
            "scope": "period",
            "scope_label": "Periode",
            "start_date": start,
            "end_date": end,
            "timezone": "Asia/Jakarta",
            "total": {
                "status": "empty",
                "label": "Total Pembelian Faktur",
                "amount": None,
                "invoice_count": 0,
                "basis": "grand_total",
                "detail": "Belum ada faktur pembelian aktif pada periode ini.",
            },
            "trend": {
                "status": "empty",
                "grain": grain,
                "points": empty_points,
                "detail": "Belum ada faktur pembelian aktif pada periode ini.",
            },
            "top_suppliers": {
                "status": "empty",
                "rows": [],
                "detail": "Belum ada faktur pembelian aktif pada periode ini.",
            },
            "distribution": {
                "status": "empty",
                "groups": [],
                "show_share": False,
                "detail": "Belum ada faktur pembelian aktif pada periode ini.",
            },
            "completeness": "complete",
            "note": PURCHASE_DISCLOSURE,
            "detail": PURCHASE_BASIS_NOTE,
        }

    total = Decimal("0.00")
    buckets: Dict[str, Dict[str, Any]] = {
        key: {"amount": Decimal("0.00"), "invoice_count": 0}
        for key in keys
    }
    suppliers: Dict[str, Dict[str, Any]] = {}
    unknown_invoice_count = 0
    has_negative = False

    for invoice in invoices:
        day = _calendar_day(invoice.get("invoice_date"))
        if not day:
            return _purchasing_error(
                start,
                end,
                "Ada faktur pembelian aktif tanpa invoice_date yang valid.",
            )
        amount = _money(invoice.get("grand_total"))
        if amount is None:
            return _purchasing_error(
                start,
                end,
                "Ada faktur pembelian dengan grand_total yang tidak dapat dibaca.",
            )
        if amount < 0:
            has_negative = True
        total += amount
        key = _bucket_of(day, grain)
        if key in buckets:
            buckets[key]["amount"] += amount
            buckets[key]["invoice_count"] += 1
        supplier_key = supplier_identity_key(invoice.get("supplier"))
        label = supplier_display_name(invoice.get("supplier"))
        if supplier_key == UNKNOWN_SUPPLIER_KEY:
            unknown_invoice_count += 1
            label = "Supplier tidak diketahui"
        row = suppliers.setdefault(
            supplier_key,
            {
                "key": supplier_key,
                "label": label,
                "amount": Decimal("0.00"),
                "invoice_count": 0,
            },
        )
        row["amount"] += amount
        row["invoice_count"] += 1

    total_q = total.quantize(MONEY_Q)
    trend_sum = sum((buckets[key]["amount"] for key in keys), Decimal("0.00")).quantize(MONEY_Q)
    if trend_sum != total_q:
        return _purchasing_error(
            start,
            end,
            "Agregasi tren pembelian tidak konsisten dengan Total Pembelian Faktur.",
        )

    ranked = sorted(
        suppliers.values(),
        key=lambda row: (-row["amount"], str(row["label"] or "").lower(), str(row["key"])),
    )
    top_rows = ranked[:TOP_SUPPLIER_LIMIT]
    remainder = ranked[TOP_SUPPLIER_LIMIT:]
    remainder_amount = sum((row["amount"] for row in remainder), Decimal("0.00"))
    remainder_count = sum(int(row["invoice_count"]) for row in remainder)

    completeness = "incomplete" if unknown_invoice_count else "complete"
    show_share = (
        completeness == "complete"
        and total_q > 0
        and not has_negative
        and all(row["amount"] >= 0 for row in ranked)
    )

    distribution_groups: List[dict] = []
    for row in top_rows:
        distribution_groups.append({
            "key": row["key"],
            "label": row["label"],
            "amount": _num(row["amount"]),
            "invoice_count": row["invoice_count"],
            "share": _share_percent(row["amount"], total_q) if show_share else None,
        })
    if remainder:
        distribution_groups.append({
            "key": OTHER_SUPPLIER_KEY,
            "label": f"{OTHER_SUPPLIER_LABEL} ({len(remainder)} supplier)",
            "amount": _num(remainder_amount.quantize(MONEY_Q)),
            "invoice_count": remainder_count,
            "share": _share_percent(remainder_amount, total_q) if show_share else None,
            "supplier_count": len(remainder),
        })

    total_status = "zero" if total_q == 0 else ("incomplete" if completeness == "incomplete" else "complete")
    section_status = total_status
    detail_parts = [PURCHASE_BASIS_NOTE]
    if unknown_invoice_count:
        detail_parts.append(
            f"{unknown_invoice_count} faktur tanpa nama supplier masuk "
            "Supplier tidak diketahui."
        )
    if has_negative:
        detail_parts.append(
            "Ada nominal faktur negatif; persentase distribusi disembunyikan."
        )

    return {
        "status": section_status,
        "scope": "period",
        "scope_label": "Periode",
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Jakarta",
        "total": {
            "status": total_status,
            "label": "Total Pembelian Faktur",
            "amount": _num(total_q),
            "invoice_count": len(invoices),
            "basis": "grand_total",
            "detail": PURCHASE_BASIS_NOTE,
        },
        "trend": {
            "status": "complete" if total_q != 0 else "zero",
            "grain": grain,
            "points": [
                {
                    "period": key,
                    "amount": _num(buckets[key]["amount"].quantize(MONEY_Q)),
                    "invoice_count": buckets[key]["invoice_count"],
                }
                for key in keys
            ],
            "detail": None,
        },
        "top_suppliers": {
            "status": total_status,
            "rows": [
                {
                    "key": row["key"],
                    "label": row["label"],
                    "amount": _num(row["amount"].quantize(MONEY_Q)),
                    "invoice_count": row["invoice_count"],
                    "share": _share_percent(row["amount"], total_q) if show_share else None,
                }
                for row in top_rows
            ],
            "detail": None if not unknown_invoice_count else (
                f"{unknown_invoice_count} faktur tanpa nama supplier."
            ),
        },
        "distribution": {
            "status": total_status,
            "groups": distribution_groups,
            "show_share": show_share,
            "detail": None if show_share else (
                "Persentase distribusi disembunyikan karena data tidak lengkap "
                "atau mengandung nominal negatif."
                if (unknown_invoice_count or has_negative)
                else None
            ),
        },
        "completeness": completeness,
        "note": PURCHASE_DISCLOSURE,
        "detail": " ".join(detail_parts),
    }


async def get_owner_analytics(
    db,
    *,
    start_date: str,
    end_date: str,
    preset: str = "custom",
    user: Optional[dict] = None,
    permissions: Optional[Sequence[str]] = None,
    now: Optional[datetime] = None,
) -> dict:
    kind = str(preset or "custom").strip().lower()
    if kind not in {"today", "month", "year", "custom"}:
        raise OwnerAnalyticsValidationError("Preset tidak valid")
    start, end = _parse_range(start_date, end_date)
    today = wib_today(now)
    loaded_at = datetime.now(timezone.utc).isoformat()

    base = {
        "preset": kind,
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Jakarta",
        "as_of_date": today.isoformat(),
        "loaded_at": loaded_at,
        "branch_filter": None,
        "otp_otd": {
            "status": "not_ready",
            "detail": "OTP/OTD persen ditunda — sumber deadline produksi/OTD belum memadai.",
        },
        "profit": _profit_placeholder(),
        "drilldowns": _drilldowns(permissions),
    }

    # Sales — reuse official omzet engine (global for non-sales_eternal owners).
    try:
        sales = await get_sales_dashboard(
            db,
            start_date=start,
            end_date=end,
            preset=kind,
            user=user,
            permissions=permissions or [],
            now=now,
        )
        omzet = sales.get("omzet") or _section_error(RuntimeError("Omzet kosong"))
        trend = sales.get("trend") or _section_error(RuntimeError("Tren kosong"))
        base["omzet"] = omzet
        base["trend"] = trend
        base["invoice_count"] = {
            "status": omzet.get("status") if isinstance(omzet, dict) else "error",
            "count": omzet.get("invoice_count") if isinstance(omzet, dict) else None,
            "detail": omzet.get("detail") if isinstance(omzet, dict) else None,
        }
        base["aov_invoice"] = _build_aov(omzet if isinstance(omzet, dict) else {})
        if isinstance(omzet, dict) and omzet.get("comparison"):
            base["omzet_comparison"] = omzet.get("comparison")
    except SalesDashboardValidationError:
        raise
    except Exception as exc:
        err = _section_error(exc)
        base["omzet"] = err
        base["trend"] = err
        base["invoice_count"] = err
        base["aov_invoice"] = err

    try:
        base["average_po"] = await get_average_po_value(db, start_date=start, end_date=end)
    except OwnerAnalyticsValidationError:
        raise
    except Exception as exc:
        base["average_po"] = _section_error(exc)

    try:
        lead, holidays = await _sla_read_context(db)
        orders = await db.orders.find(
            {"cancelled": {"$ne": True}},
            OPS_PROJECTION,
        ).sort("deadline", 1).to_list(OPS_ORDER_LIMIT)
        ops = _build_operations(
            orders, lead_time_days=lead, today=today, holidays=holidays,
        )
        base["operations"] = ops
        base["po_active"] = ops["po_active"]
        base["delivery_late"] = {
            "status": "complete",
            "count": ops["delivery"]["late_count"],
            "label": ops["delivery"]["late_label"],
            "detail": ops["delivery"]["detail"],
        }
    except Exception as exc:
        err = _section_error(exc)
        base["operations"] = err
        base["po_active"] = err
        base["delivery_late"] = err

    # Phase 3A Cash & Bank — isolated from Phase 2 failures.
    try:
        base["finance"] = await _build_finance(db, start=start, end=end, today=today)
    except Exception as exc:
        base["finance"] = {
            **_section_error(exc),
            "cash_balance": {"status": "error", "amount": None, "detail": str(exc) or "Gagal memuat"},
            "cash_in": {"status": "error", "amount": None},
            "cash_out": {"status": "error", "amount": None},
            "cash_trend": {"status": "error", "points": [], "grain": None},
            "reconciliation": {
                "status": "error",
                "status_label": "Belum Lengkap",
                "unmatched_count": None,
                "review_count": None,
            },
        }

    # Phase 3C Purchasing — isolated; must not wipe finance / Phase 2.
    try:
        base["purchasing"] = await _build_purchasing(db, start=start, end=end)
    except Exception as exc:
        base["purchasing"] = _purchasing_error(start, end, str(exc) or "Gagal memuat pembelian")

    return base
