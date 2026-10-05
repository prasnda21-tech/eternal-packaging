"""Dashboard Penjualan — read-only aggregates of existing sales documents.

GET must not insert, update, post, reverse, or backfill business documents.
Omzet follows the official sales-accounting basis: finalized invoice
`after_discount` (goods after discount, before PPN and other charges), grouped
by `invoice_date`. That is the same calendar date `journal_date_from_invoice`
uses when a faktur is later posted. Draft and cancelled invoices are excluded.
Partial invoices count once, at the issued invoice amount, never at PO value.

Outstanding uses the same receipt and customer-credit rules as Faktur
Penjualan: allocated receipt amounts that still count, plus posted credit
applications. Unapplied overpayment is not payment of that invoice. Cancelled
invoices are not open receivables. Aging is the database state at request
time, not a historical balance at the filter end date.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.customer_identity import extract_stable_customer_id
from services.financial_statement_common import parse_financial_date_range
from services.sales_invoices import (
    FINANCIAL_FINALIZED,
    effective_financial_status,
)
from services.sales_receipt_posting import receipt_allocated_amount, receipt_counts_toward_outstanding
from services.sales_user_mapping import (
    SETTINGS_KEY as SALES_MAPPING_KEY,
    exact_sales_label,
    mapping_dict_from_entries,
    resolve_from_map,
    sales_owner_match,
)
from services.sales_user_mapping import STATUS_MATCHED
from workflow_gates import design_ok

PERM_VIEW = "dashboard.sales.view"
WIB = timezone(timedelta(hours=7))
MONEY_Q = Decimal("0.01")
IN_BATCH = 500
MAX_RANGE_DAYS = 3660
DAY_GRAIN_MAX = 31
WEEK_GRAIN_MAX = 120
PRODUCT_LIMIT = 5
ORDER_LIST_CAP = 1000

MONTHS_ID = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")

ORDER_BUCKETS = (
    ("aktif", "Aktif", "/penjualan/pesanan-penjualan?status=aktif"),
    ("hold", "HOLD", "/penjualan/pesanan-penjualan?status=hold"),
    ("selesai", "Selesai", "/penjualan/pesanan-penjualan?tab=histori&status=selesai"),
    ("cancelled", "Dibatalkan", "/penjualan/pesanan-penjualan?tab=histori&status=cancelled"),
)
AGING_LABELS = (
    ("NOT_DUE", "Belum jatuh tempo"),
    ("1_30", "1–30 hari"),
    ("31_60", "31–60 hari"),
    ("61_90", "61–90 hari"),
    ("OVER_90", "> 90 hari"),
)
ORDER_LINK_PERMS = ("sales_order.view", "sales_order.create", "po.view")
UNASSIGNED_KEY = "unassigned"
UNASSIGNED_LABEL = "Belum ditetapkan"
OMITTED = (
    {
        "metric": "Target tercapai",
        "reason": "Tidak ada target penjualan resmi yang tersimpan.",
    },
    {
        "metric": "Aktivitas penjualan terbaru",
        "reason": "Tidak ada feed event penjualan yang boleh dibaca halaman ini.",
    },
    {
        "metric": "Draft penjualan / Menunggu konfirmasi",
        "reason": "Daftar Pesanan Penjualan tidak mempunyai status resmi itu.",
    },
)


class SalesDashboardValidationError(ValueError):
    pass


def wib_today(now: Optional[datetime] = None) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(WIB).date()


def _money(raw: Any) -> Optional[Decimal]:
    """None when the stored amount is missing or not a number. Never implies 0."""
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


def _section_error(exc: Exception) -> dict:
    return {
        "status": "error",
        "amount": None,
        "detail": str(exc) or "Gagal memuat",
    }


def _parse_range(start_date: str, end_date: str) -> Tuple[str, str]:
    start, end = parse_financial_date_range(
        start_date,
        end_date,
        empty_message="Rentang tanggal wajib diisi",
        start_invalid_message="Tanggal mulai tidak valid",
        end_invalid_message="Tanggal akhir tidak valid",
        order_message="Tanggal mulai tidak boleh setelah tanggal akhir",
        error_cls=SalesDashboardValidationError,
    )
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    if span > MAX_RANGE_DAYS:
        raise SalesDashboardValidationError("Rentang tanggal terlalu panjang")
    return start, end


def _shift_month(day: date, months: int) -> Optional[date]:
    month_index = (day.year * 12 + (day.month - 1)) + months
    year, month = month_index // 12, month_index % 12 + 1
    try:
        return day.replace(year=year, month=month)
    except ValueError:
        return None


def comparable_previous_range(preset: str, start: str, end: str, today: date) -> Optional[Tuple[str, str]]:
    """Equivalent prior window. None when the preset and dates are not that window.

    Hari ini: the previous calendar day.
    Bulan ini: day 1 through today's day-of-month, shifted to the previous month.
    The end day must exist in that month (31 March has no 31 February).
    Tahun ini: 1 January through today, shifted one year. 29 February has no
    equivalent in a non-leap year.
    Kustom, or a range that is not exactly the preset window: no comparison.
    """
    kind = str(preset or "").strip().lower()
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    if kind == "today":
        if start_d == end_d == today:
            previous = today - timedelta(days=1)
            return previous.isoformat(), previous.isoformat()
        return None
    if kind == "month":
        if start_d != today.replace(day=1) or end_d != today:
            return None
        prev_end = _shift_month(today, -1)
        prev_start = _shift_month(today.replace(day=1), -1)
        if prev_end is None or prev_start is None:
            return None
        return prev_start.isoformat(), prev_end.isoformat()
    if kind == "year":
        if start_d != date(today.year, 1, 1) or end_d != today:
            return None
        try:
            prev_end = today.replace(year=today.year - 1)
            prev_start = date(today.year - 1, 1, 1)
        except ValueError:
            return None
        return prev_start.isoformat(), prev_end.isoformat()
    return None


def grain_for(start: str, end: str) -> str:
    inclusive = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    if inclusive <= DAY_GRAIN_MAX:
        return "day"
    if inclusive <= WEEK_GRAIN_MAX:
        return "week"
    return "month"


def _fmt_day(day: date) -> str:
    return f"{day.day:02d} {MONTHS_ID[day.month - 1]}"


def trend_buckets(start: str, end: str, grain: str) -> List[dict]:
    start_d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    buckets: List[dict] = []
    if grain == "month":
        cursor = date(start_d.year, start_d.month, 1)
        while cursor <= end_d:
            key = f"{cursor.year:04d}-{cursor.month:02d}"
            buckets.append({
                "key": key,
                "label": f"{MONTHS_ID[cursor.month - 1]} {cursor.year}",
                "start": cursor.isoformat(),
                "end": key,
            })
            year = cursor.year + (1 if cursor.month == 12 else 0)
            month = 1 if cursor.month == 12 else cursor.month + 1
            cursor = date(year, month, 1)
        return buckets
    if grain == "week":
        cursor = start_d
        while cursor <= end_d:
            week_end = cursor + timedelta(days=(6 - cursor.weekday()))
            if week_end > end_d:
                week_end = end_d
            buckets.append({
                "key": cursor.isoformat(),
                "label": _fmt_day(cursor) if cursor == week_end else f"{_fmt_day(cursor)}–{_fmt_day(week_end)}",
                "start": cursor.isoformat(),
                "end": week_end.isoformat(),
            })
            cursor = week_end + timedelta(days=1)
        return buckets
    cursor = start_d
    while cursor <= end_d:
        iso = cursor.isoformat()
        buckets.append({"key": iso, "label": _fmt_day(cursor), "start": iso, "end": iso})
        cursor += timedelta(days=1)
    return buckets


def _calendar_day(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if len(text) < 10:
        return None
    prefix = text[:10]
    try:
        date.fromisoformat(prefix)
    except ValueError:
        return None
    return prefix


def _bucket_key(day: str, buckets: Sequence[dict], grain: str) -> Optional[str]:
    if grain == "month":
        return day[:7]
    for bucket in buckets:
        if bucket["start"] <= day <= bucket["end"]:
            return bucket["key"]
    return None


def order_is_hold(order: dict) -> bool:
    """Same HOLD gate as Pesanan Penjualan: stage < 5 and design/payment blockers."""
    try:
        stage = int(order.get("current_stage") or 0)
    except (TypeError, ValueError):
        stage = 0
    if stage >= 5:
        return False
    reasons = []
    if not order.get("design_files"):
        reasons.append("design")
    elif not design_ok(order):
        reasons.append("design_approval")
    if not order.get("payment_files"):
        reasons.append("payment")
    elif order.get("payment_status") != "Approved" and not order.get("payment_bypassed"):
        reasons.append("payment_approval")
    return bool(reasons)


def order_status_bucket(order: dict) -> str:
    """Mutually exclusive list classifications. One PO, one bucket."""
    if order.get("cancelled") is True:
        return "cancelled"
    if order_is_hold(order):
        return "hold"
    try:
        stage = int(order.get("current_stage") or 0)
    except (TypeError, ValueError):
        stage = 0
    if stage >= 14:
        return "selesai"
    return "aktif"


def _invoice_counts(invoice: dict) -> bool:
    if invoice.get("is_cancelled") is True:
        return False
    return effective_financial_status(invoice) == FINANCIAL_FINALIZED


def _payment_position(grand: Optional[Decimal], paid: Decimal) -> Tuple[str, Decimal]:
    """Same branches as apply_invoice_payment_fields, without writing the invoice."""
    if grand is None:
        return "invalid", Decimal("0.00")
    if paid <= 0:
        status = "BELUM DIBAYAR"
    elif paid < grand:
        status = "DP"
    else:
        status = "LUNAS"
    outstanding = (grand - paid).quantize(MONEY_Q)
    if outstanding < 0:
        outstanding = Decimal("0.00")
    return status, outstanding


def _share_percent(amount: Decimal, total: Decimal):
    """Percent of a known total. None when the total cannot be a denominator."""
    if total <= 0 or amount < 0:
        return None
    return float((amount / total * Decimal("100")).quantize(Decimal("0.1")))


async def _cursor_all(cursor) -> List[dict]:
    rows: List[dict] = []
    async for doc in cursor:
        rows.append(doc)
    return rows


async def _find_in(db, collection: str, field: str, values: Sequence[str], projection: dict) -> List[dict]:
    ids = [str(v) for v in values if v]
    if not ids:
        return []
    out: List[dict] = []
    proj = {"_id": 0, **projection}
    for index in range(0, len(ids), IN_BATCH):
        chunk = ids[index:index + IN_BATCH]
        docs = await _cursor_all(db[collection].find({field: {"$in": chunk}}, proj))
        out.extend(docs)
    return out


def _next_day(iso_day: str) -> str:
    return (date.fromisoformat(iso_day) + timedelta(days=1)).isoformat()


def _date_query(start: str, end: str) -> dict:
    return {"$gte": start, "$lt": _next_day(end)}


async def _scoped_po_ids(db, user: Optional[dict]) -> Optional[set]:
    match = sales_owner_match(user)
    if not match:
        return None
    rows = await _cursor_all(db.orders.find(match, {"_id": 0, "id": 1}))
    return {str(row.get("id")) for row in rows if row.get("id")}


def _scope_clause(po_ids: Optional[set]) -> dict:
    if po_ids is None:
        return {}
    return {"po_id": {"$in": list(po_ids)}}


async def _load_finalized_invoices(db, extra: dict, projection: dict) -> List[dict]:
    query = {
        "financial_status": FINANCIAL_FINALIZED,
        "is_cancelled": {"$ne": True},
        **extra,
    }
    rows = await _cursor_all(db.sales_invoices.find(query, {"_id": 0, **projection}))
    return [row for row in rows if _invoice_counts(row)]


def _sum_omzet(invoices: Sequence[dict]) -> Tuple[Optional[Decimal], int, Optional[str]]:
    total = Decimal("0.00")
    count = 0
    for inv in invoices:
        amount = _money(inv.get("after_discount"))
        if amount is None:
            return None, 0, "Ada faktur finalized dengan after_discount yang tidak dapat dibaca."
        total += amount
        count += 1
    return total.quantize(MONEY_Q), count, None


def _paid_for(invoice_id: str, receipts_by_invoice: Dict[str, List[dict]], credit_by_invoice: Dict[str, Decimal]) -> Decimal:
    paid = Decimal("0.00")
    for receipt in receipts_by_invoice.get(invoice_id, []):
        if not receipt_counts_toward_outstanding(receipt):
            continue
        paid += receipt_allocated_amount(receipt)
    paid += credit_by_invoice.get(invoice_id, Decimal("0.00"))
    return paid.quantize(MONEY_Q)


async def _load_receipts(db, invoice_ids: Sequence[str]) -> Dict[str, List[dict]]:
    rows = await _find_in(
        db,
        "sales_receipts",
        "invoice_id",
        invoice_ids,
        {
            "invoice_id": 1,
            "amount": 1,
            "allocated_amount": 1,
            "customer_credit_amount": 1,
            "financial_status": 1,
            "cancelled": 1,
        },
    )
    grouped: Dict[str, List[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("invoice_id") or ""), []).append(row)
    return grouped


async def _load_credit(db, invoice_ids: Sequence[str]) -> Dict[str, Decimal]:
    rows = await _find_in(
        db,
        "sales_customer_credit_applications",
        "invoice_id",
        invoice_ids,
        {"invoice_id": 1, "amount": 1, "financial_status": 1, "status": 1},
    )
    grouped: Dict[str, Decimal] = {}
    for row in rows:
        if str(row.get("financial_status") or "") != "active":
            continue
        if str(row.get("status") or "") != "posted":
            continue
        amount = _money(row.get("amount"))
        if amount is None:
            raise SalesDashboardValidationError("Pemakaian saldo pelanggan tidak dapat dibaca.")
        key = str(row.get("invoice_id") or "")
        grouped[key] = (grouped.get(key, Decimal("0.00")) + amount).quantize(MONEY_Q)
    return grouped


async def _load_mapping(db) -> Dict[str, str]:
    doc = await db.app_settings.find_one({"key": SALES_MAPPING_KEY}, {"_id": 0, "entries": 1})
    return mapping_dict_from_entries((doc or {}).get("entries"))


def _comparison_payload(
    *,
    available: bool,
    previous_start: Optional[str] = None,
    previous_end: Optional[str] = None,
    previous_amount: Optional[Decimal] = None,
    current_amount: Optional[Decimal] = None,
    status: str = "complete",
    detail: Optional[str] = None,
) -> dict:
    percent = None
    direction = None
    if available and previous_amount is not None and current_amount is not None and previous_amount != 0:
        percent = float(((current_amount - previous_amount) / previous_amount).quantize(Decimal("0.000001")))
        if current_amount > previous_amount:
            direction = "up"
        elif current_amount < previous_amount:
            direction = "down"
        else:
            direction = "flat"
    return {
        "available": available and status != "error",
        "status": status,
        "previous_start": previous_start,
        "previous_end": previous_end,
        "previous_amount": _num(previous_amount) if available and status != "error" else None,
        "percent": percent if status != "error" else None,
        "direction": direction if status != "error" else None,
        "detail": detail,
    }


def _build_omzet_and_trend(
    invoices: Sequence[dict],
    *,
    start: str,
    end: str,
    comparison: dict,
) -> Tuple[dict, dict]:
    total, count, invalid = _sum_omzet(invoices)
    if invalid:
        error = {"status": "error", "amount": None, "invoice_count": None, "grand_total": None, "detail": invalid, "comparison": _comparison_payload(available=False, status="error", detail=invalid)}
        return error, {"status": "error", "grain": grain_for(start, end), "grain_label": "", "points": [], "detail": invalid}
    grain = grain_for(start, end)
    buckets = trend_buckets(start, end, grain)
    points = {
        bucket["key"]: {
            "key": bucket["key"],
            "label": bucket["label"],
            "omzet": Decimal("0.00"),
            "invoice_count": 0,
        }
        for bucket in buckets
    }
    for inv in invoices:
        day = _calendar_day(inv.get("invoice_date"))
        if not day or day < start or day > end:
            error = {"status": "error", "amount": None, "invoice_count": None, "grand_total": None, "detail": "Ada faktur finalized tanpa invoice_date yang valid.", "comparison": _comparison_payload(available=False, status="error")}
            return error, {"status": "error", "grain": grain, "grain_label": "", "points": [], "detail": error["detail"]}
        key = _bucket_key(day, buckets, grain)
        if key not in points:
            continue
        points[key]["omzet"] += _money(inv.get("after_discount")) or Decimal("0.00")
        points[key]["invoice_count"] += 1
    grand = Decimal("0.00")
    for inv in invoices:
        amount = _money(inv.get("grand_total"))
        if amount is None:
            error = {"status": "error", "amount": None, "invoice_count": None, "grand_total": None, "detail": "Ada faktur finalized dengan grand_total yang tidak dapat dibaca.", "comparison": _comparison_payload(available=False, status="error")}
            return error, {"status": "error", "grain": grain, "grain_label": "", "points": [], "detail": error["detail"]}
        grand += amount
    grain_label = {"day": "Harian", "week": "Mingguan", "month": "Bulanan"}[grain]
    if count == 0:
        omzet = {
            "status": "empty",
            "amount": None,
            "invoice_count": 0,
            "grand_total": None,
            "detail": "Belum ada faktur pada periode ini",
            "basis": "after_discount",
            "label": "Omzet penjualan",
            "basis_note": "Setelah diskon barang, sebelum PPN dan biaya lainnya.",
            "comparison": comparison if comparison.get("status") == "error" else _comparison_payload(available=False, detail="Belum ada faktur pada periode ini"),
        }
        trend = {
            "status": "empty",
            "grain": grain,
            "grain_label": grain_label,
            "points": [],
            "detail": "Belum ada faktur pada periode ini",
        }
        return omzet, trend
    status = "zero" if total == 0 else "complete"
    omzet = {
        "status": status,
        "amount": _num(total),
        "invoice_count": count,
        "grand_total": _num(grand.quantize(MONEY_Q)),
        "detail": None,
        "basis": "after_discount",
        "label": "Omzet penjualan",
        "basis_note": "Setelah diskon barang, sebelum PPN dan biaya lainnya.",
        "date_basis": "invoice_date",
        "comparison": comparison,
    }
    trend = {
        "status": status,
        "grain": grain,
        "grain_label": grain_label,
        "points": [
            {
                "key": row["key"],
                "label": row["label"],
                "omzet": _num(row["omzet"]),
                "invoice_count": row["invoice_count"],
            }
            for row in points.values()
        ],
        "detail": None,
    }
    return omzet, trend


def _build_settlement(
    invoices: Sequence[dict],
    receipts: Dict[str, List[dict]],
    credit: Dict[str, Decimal],
    *,
    start: str,
    end: str,
) -> dict:
    grain = grain_for(start, end)
    grain_label = {"day": "Harian", "week": "Mingguan", "month": "Bulanan"}[grain]
    scope = "Status pelunasan saat ini untuk faktur terbit pada periode"
    if not invoices:
        return {
            "status": "empty",
            "scope": scope,
            "settled_count": 0,
            "settled_grand_total": None,
            "open_count": 0,
            "open_grand_total": None,
            "open_outstanding": None,
            "grain": grain,
            "grain_label": grain_label,
            "points": [],
            "detail": "Belum ada faktur pada periode ini",
        }
    buckets = trend_buckets(start, end, grain)
    points = {
        bucket["key"]: {
            "key": bucket["key"],
            "label": bucket["label"],
            "grand_total": Decimal("0.00"),
            "settled_grand": Decimal("0.00"),
            "outstanding": Decimal("0.00"),
        }
        for bucket in buckets
    }
    settled_count = 0
    settled_grand = Decimal("0.00")
    open_count = 0
    open_grand = Decimal("0.00")
    open_outstanding = Decimal("0.00")
    for inv in invoices:
        grand = _money(inv.get("grand_total"))
        paid = _paid_for(str(inv.get("id") or ""), receipts, credit)
        status, outstanding = _payment_position(grand, paid)
        if status == "invalid" or grand is None:
            return {
                "status": "error",
                "amount": None,
                "settled_count": None,
                "settled_grand_total": None,
                "open_count": None,
                "open_grand_total": None,
                "open_outstanding": None,
                "points": [],
                "detail": "Ada faktur finalized dengan grand_total yang tidak dapat dibaca.",
            }
        day = _calendar_day(inv.get("invoice_date"))
        key = _bucket_key(day, buckets, grain) if day else None
        if status == "LUNAS":
            settled_count += 1
            settled_grand += grand
            if key in points:
                points[key]["settled_grand"] += grand
                points[key]["grand_total"] += grand
        else:
            open_count += 1
            open_grand += grand
            open_outstanding += outstanding
            if key in points:
                points[key]["outstanding"] += outstanding
                points[key]["grand_total"] += grand
    return {
        "status": "complete",
        "scope": scope,
        "settled_count": settled_count,
        "settled_grand_total": _num(settled_grand.quantize(MONEY_Q)),
        "open_count": open_count,
        "open_grand_total": _num(open_grand.quantize(MONEY_Q)),
        "open_outstanding": _num(open_outstanding.quantize(MONEY_Q)),
        "grain": grain,
        "grain_label": grain_label,
        "points": [
            {
                "key": row["key"],
                "label": row["label"],
                "grand_total": _num(row["grand_total"].quantize(MONEY_Q)),
                "settled_grand": _num(row["settled_grand"].quantize(MONEY_Q)),
                "outstanding": _num(row["outstanding"].quantize(MONEY_Q)),
            }
            for row in points.values()
        ],
        "detail": "Lebih bayar yang belum dipakai ke faktur tidak dihitung sebagai pelunasan. Status ini adalah kondisi saat ini, bukan arus kas pada tanggal faktur.",
    }


def _aging_bucket(due_iso: str, today_iso: str) -> str:
    due = date.fromisoformat(due_iso)
    today = date.fromisoformat(today_iso)
    if due >= today:
        return "NOT_DUE"
    days = (today - due).days
    if days <= 30:
        return "1_30"
    if days <= 60:
        return "31_60"
    if days <= 90:
        return "61_90"
    return "OVER_90"


def _build_receivables(
    invoices: Sequence[dict],
    receipts: Dict[str, List[dict]],
    credit: Dict[str, Decimal],
    orders_by_id: Dict[str, dict],
    today_iso: str,
) -> dict:
    outstanding_total = Decimal("0.00")
    open_count = 0
    buckets = {key: Decimal("0.00") for key, _label in AGING_LABELS}
    unbucketed = Decimal("0.00")
    customers = set()
    unidentified = 0
    for inv in invoices:
        grand = _money(inv.get("grand_total"))
        paid = _paid_for(str(inv.get("id") or ""), receipts, credit)
        status, outstanding = _payment_position(grand, paid)
        if status == "invalid":
            return {
                "status": "error",
                "amount": None,
                "outstanding": None,
                "open_invoice_count": None,
                "customer_count": None,
                "detail": "Ada faktur dengan grand_total yang tidak dapat dibaca.",
                "scope": "Saat ini",
                "as_of_label": "Saat ini",
                "historical_as_of": False,
            }
        if status == "LUNAS" or outstanding <= 0:
            continue
        open_count += 1
        outstanding_total += outstanding
        order = orders_by_id.get(str(inv.get("po_id") or ""))
        stable = extract_stable_customer_id(order) if order else None
        if stable:
            customers.add(stable)
        else:
            unidentified += 1
        due = _calendar_day(inv.get("due_date")) or _calendar_day(inv.get("invoice_date"))
        if not due:
            unbucketed += outstanding
            continue
        buckets[_aging_bucket(due, today_iso)] += outstanding
    if open_count == 0:
        return {
            "status": "zero",
            "scope": "Saat ini",
            "as_of_label": "Saat ini",
            "historical_as_of": False,
            "outstanding": 0.0,
            "open_invoice_count": 0,
            "customer_count": 0,
            "unidentified_invoice_count": 0,
            "coverage_note": None,
            "aging": [{"key": key, "label": label, "amount": 0.0} for key, label in AGING_LABELS],
            "unbucketed_outstanding": 0.0,
            "detail": "Tidak ada piutang terbuka",
        }
    coverage = None
    customer_count: Optional[int] = len(customers)
    if unidentified:
        customer_count = None
        coverage = f"{unidentified} faktur berpiutang tanpa customer_id stabil, tidak digabung berdasarkan nama."
    return {
        "status": "complete" if not unidentified and unbucketed == 0 else "incomplete",
        "scope": "Saat ini",
        "as_of_label": "Saat ini",
        "historical_as_of": False,
        "outstanding": _num(outstanding_total.quantize(MONEY_Q)),
        "open_invoice_count": open_count,
        "customer_count": customer_count,
        "unidentified_invoice_count": unidentified,
        "coverage_note": coverage,
        "aging": [
            {"key": key, "label": label, "amount": _num(buckets[key].quantize(MONEY_Q))}
            for key, label in AGING_LABELS
        ],
        "unbucketed_outstanding": _num(unbucketed.quantize(MONEY_Q)),
        "detail": "Umur piutang dihitung dari sisa tagihan saat permintaan ini, bukan saldo historis di akhir filter.",
    }


def _build_orders(orders: Sequence[dict], *, can_link: bool) -> dict:
    counts = {key: 0 for key, _label, _href in ORDER_BUCKETS}
    for order in orders:
        counts[order_status_bucket(order)] += 1
    total = sum(counts.values())
    buckets = []
    for key, label, href in ORDER_BUCKETS:
        buckets.append({
            "key": key,
            "label": label,
            "count": counts[key],
            "href": href if can_link else None,
        })
    if total == 0:
        return {
            "status": "empty",
            "scope": "Saat ini",
            "total": 0,
            "buckets": buckets,
            "detail": "Belum ada pesanan",
            "list_note": None,
        }
    return {
        "status": "complete",
        "scope": "Saat ini",
        "total": total,
        "buckets": buckets,
        "detail": None,
        "list_note": f"Daftar Pesanan Penjualan memakai klasifikasi yang sama dan dibatasi {ORDER_LIST_CAP} pesanan.",
    }


async def _build_sales(db, invoices: Sequence[dict], mapping: Dict[str, str]) -> dict:
    total, count, invalid = _sum_omzet(invoices)
    if invalid:
        return {"status": "error", "amount": None, "groups": [], "detail": invalid}
    groups: Dict[str, dict] = {}
    unassigned_count = 0
    user_ids = set()
    for inv in invoices:
        label = exact_sales_label(inv.get("sales"))
        uid, status = resolve_from_map(mapping, label)
        amount = _money(inv.get("after_discount")) or Decimal("0.00")
        if status == STATUS_MATCHED and uid:
            key = f"user:{uid}"
            user_ids.add(uid)
            row = groups.setdefault(key, {
                "key": key,
                "label": label or uid,
                "source_label": label,
                "omzet": Decimal("0.00"),
                "invoice_count": 0,
            })
            row["omzet"] += amount
            row["invoice_count"] += 1
        else:
            unassigned_count += 1
            row = groups.setdefault(UNASSIGNED_KEY, {
                "key": UNASSIGNED_KEY,
                "label": UNASSIGNED_LABEL,
                "source_label": "",
                "omzet": Decimal("0.00"),
                "invoice_count": 0,
            })
            row["omzet"] += amount
            row["invoice_count"] += 1
    names: Dict[str, str] = {}
    if user_ids:
        users = await _find_in(db, "users", "id", list(user_ids), {"id": 1, "name": 1})
        for user in users:
            name = str(user.get("name") or "").strip()
            if name:
                names[str(user.get("id"))] = name
    public = []
    for key, row in groups.items():
        if key != UNASSIGNED_KEY:
            uid = key.split(":", 1)[1]
            if names.get(uid):
                row["label"] = names[uid]
        public.append(row)
    by_label: Dict[str, List[dict]] = {}
    for row in public:
        by_label.setdefault(row["label"], []).append(row)
    for label, rows in by_label.items():
        if len(rows) < 2 or label == UNASSIGNED_LABEL:
            continue
        for row in rows:
            extra = row.get("source_label") or row["key"]
            row["label"] = f"{label} ({extra})"
    public.sort(key=lambda row: (-row["omzet"], row["label"]))
    if count == 0:
        return {
            "status": "empty",
            "groups": [],
            "total_omzet": None,
            "unassigned_invoice_count": 0,
            "detail": "Belum ada faktur pada periode ini",
        }
    note = None
    if unassigned_count:
        note = f"{unassigned_count} faktur tanpa sales terpetakan masuk {UNASSIGNED_LABEL}, tidak ditebak dari nama."
    status = "complete" if not unassigned_count else "incomplete"
    show_share = status == "complete" and total > 0 and all(row["omzet"] >= 0 for row in public)
    return {
        "status": status,
        "groups": [
            {
                "key": row["key"],
                "label": row["label"],
                "omzet": _num(row["omzet"].quantize(MONEY_Q)),
                "invoice_count": row["invoice_count"],
                "share": _share_percent(row["omzet"], total) if show_share else None,
            }
            for row in public
        ],
        "total_omzet": _num(total),
        "unassigned_invoice_count": unassigned_count,
        "detail": note,
    }


def _line_identity(order_line: Optional[dict]) -> Tuple[Optional[str], str]:
    if not order_line:
        return None, ""
    accurate = str(order_line.get("accurate_item_id") or "").strip()
    master = str(order_line.get("master_item_id") or "").strip()
    if accurate:
        return f"accurate:{accurate}", accurate
    if master:
        return f"master:{master}", master
    return None, ""


def _match_order_line(invoice_line: dict, order_lines: Sequence[dict]) -> Optional[dict]:
    try:
        line_idx = int(invoice_line.get("line_idx"))
    except (TypeError, ValueError):
        return None
    found = None
    for line in order_lines:
        try:
            candidate = int(line.get("line_idx")) if line.get("line_idx") is not None else None
        except (TypeError, ValueError):
            candidate = None
        if candidate == line_idx:
            found = line
            break
    if found is None and 0 <= line_idx < len(order_lines):
        found = order_lines[line_idx]
    if not found:
        return None
    sent = str(invoice_line.get("line_id") or "").strip()
    stored = str(found.get("line_id") or found.get("id") or "").strip()
    if sent and stored and sent != stored:
        return None
    return found


def _build_products(invoices: Sequence[dict], orders_by_id: Dict[str, dict]) -> dict:
    if not invoices:
        return {
            "status": "empty",
            "basis": "line_subtotal",
            "items": [],
            "unidentified_line_count": 0,
            "detail": "Belum ada faktur pada periode ini",
        }
    groups: Dict[str, dict] = {}
    unidentified = 0
    identified_amount = Decimal("0.00")
    for inv in invoices:
        order = orders_by_id.get(str(inv.get("po_id") or "")) or {}
        order_lines = [line for line in (order.get("line_items") or []) if isinstance(line, dict)]
        for line in inv.get("lines") or []:
            if not isinstance(line, dict):
                continue
            amount = _money(line.get("line_subtotal"))
            if amount is None:
                return {
                    "status": "error",
                    "amount": None,
                    "items": [],
                    "detail": "Ada baris faktur dengan line_subtotal yang tidak dapat dibaca.",
                }
            matched = _match_order_line(line, order_lines)
            identity, _raw = _line_identity(matched)
            if not identity:
                unidentified += 1
                continue
            unit = str(line.get("unit") or "").strip()
            qty_raw = line.get("quantity")
            try:
                qty = int(qty_raw)
            except (TypeError, ValueError):
                qty = None
            label = str(line.get("item_name") or line.get("name") or "").strip() or identity
            row = groups.setdefault(identity, {
                "key": identity,
                "label": label,
                "amount": Decimal("0.00"),
                "quantity": 0,
                "unit": unit,
                "mixed_unit": False,
                "quantity_known": True,
            })
            row["amount"] += amount
            identified_amount += amount
            if row["unit"] != unit:
                row["mixed_unit"] = True
            if qty is None:
                row["quantity_known"] = False
            else:
                row["quantity"] += qty
    if not groups and unidentified:
        return {
            "status": "unavailable",
            "basis": "line_subtotal",
            "items": [],
            "unidentified_line_count": unidentified,
            "identified_amount": None,
            "detail": "Baris faktur tidak punya identitas barang yang stabil, jadi peringkat tidak ditampilkan.",
        }
    if not groups:
        return {
            "status": "empty",
            "basis": "line_subtotal",
            "items": [],
            "unidentified_line_count": 0,
            "detail": "Belum ada baris faktur pada periode ini",
        }
    ranked_all = sorted(groups.values(), key=lambda row: (-row["amount"], row["label"], row["key"]))
    share_base = identified_amount if identified_amount > 0 and all(row["amount"] >= 0 for row in ranked_all) else None

    def public_item(row: dict) -> dict:
        show_qty = row["quantity_known"] and not row["mixed_unit"]
        return {
            "key": row["key"],
            "label": row["label"],
            "amount": _num(row["amount"].quantize(MONEY_Q)),
            "quantity": row["quantity"] if show_qty else None,
            "unit": row["unit"] if show_qty else None,
            "share": None if share_base is None else _share_percent(row["amount"], share_base),
        }

    note = "Peringkat memakai nilai baris sebelum diskon faktur. Diskon faktur tidak dialokasikan ke barang."
    if unidentified:
        note = f"{note} {unidentified} baris tanpa identitas barang tidak masuk peringkat."
    return {
        "status": "complete" if not unidentified else "incomplete",
        "basis": "line_subtotal",
        "basis_note": "Nilai barang sebelum diskon faktur",
        "items": [public_item(row) for row in ranked_all[:PRODUCT_LIMIT]],
        "all_items": [public_item(row) for row in ranked_all],
        "unidentified_line_count": unidentified,
        "identified_amount": _num(identified_amount.quantize(MONEY_Q)),
        "detail": note,
    }


def _build_customers(
    invoices: Sequence[dict],
    orders_by_id: Dict[str, dict],
    prior_customer_ids: set,
) -> dict:
    if not invoices:
        return {
            "status": "empty",
            "new_count": 0,
            "repeat_count": 0,
            "new_percent": None,
            "repeat_percent": None,
            "percents_available": False,
            "unidentified_invoice_count": 0,
            "detail": "Belum ada faktur pada periode ini",
        }
    seen = set()
    new_count = 0
    repeat_count = 0
    unidentified = 0
    for inv in invoices:
        order = orders_by_id.get(str(inv.get("po_id") or ""))
        stable = extract_stable_customer_id(order) if order else None
        if not stable:
            unidentified += 1
            continue
        if stable in seen:
            continue
        seen.add(stable)
        if stable in prior_customer_ids:
            repeat_count += 1
        else:
            new_count += 1
    percents_available = unidentified == 0 and (new_count + repeat_count) > 0
    new_percent = None
    repeat_percent = None
    if percents_available:
        whole = Decimal(new_count + repeat_count)
        new_percent = float((Decimal(new_count) / whole).quantize(Decimal("0.000001")))
        repeat_percent = float((Decimal(repeat_count) / whole).quantize(Decimal("0.000001")))
    note = "Baru berarti customer_id stabil yang faktur finalized pertamanya jatuh pada periode ini."
    if unidentified:
        note = f"{note} {unidentified} faktur tanpa customer_id stabil tidak diklasifikasi dan tidak masuk persentase."
    return {
        "status": "complete" if percents_available or (not unidentified and new_count + repeat_count == 0) else "incomplete",
        "new_count": new_count,
        "repeat_count": repeat_count,
        "identified_count": new_count + repeat_count,
        "new_percent": new_percent,
        "repeat_percent": repeat_percent,
        "percents_available": percents_available,
        "unidentified_invoice_count": unidentified,
        "detail": note,
    }


def _build_customer_rank(invoices: Sequence[dict], orders_by_id: Dict[str, dict]) -> dict:
    """Omzet after_discount grouped by stable customer_id. Names never merge rows."""
    if not invoices:
        return {
            "status": "empty",
            "basis": "after_discount",
            "groups": [],
            "identified_amount": None,
            "unidentified_invoice_count": 0,
            "detail": "Belum ada faktur pada periode ini",
        }
    groups: Dict[str, dict] = {}
    unidentified = 0
    identified = Decimal("0.00")
    mixed_label = 0
    for inv in invoices:
        amount = _money(inv.get("after_discount"))
        if amount is None:
            return {
                "status": "error",
                "basis": "after_discount",
                "amount": None,
                "groups": [],
                "detail": "Ada faktur finalized dengan after_discount yang tidak dapat dibaca.",
            }
        order = orders_by_id.get(str(inv.get("po_id") or ""))
        stable = extract_stable_customer_id(order) if order else None
        if not stable:
            unidentified += 1
            continue
        label = ""
        if order:
            label = str(order.get("customer") or order.get("customer_name") or "").strip()
        row = groups.get(stable)
        if row is None:
            row = {
                "key": stable,
                "label": label or stable,
                "amount": Decimal("0.00"),
                "invoice_count": 0,
            }
            groups[stable] = row
        elif label and row["label"] not in {label, stable} and label != row["label"]:
            mixed_label += 1
        elif label and row["label"] == stable:
            row["label"] = label
        row["amount"] += amount
        row["invoice_count"] += 1
        identified += amount
    if not groups:
        return {
            "status": "incomplete",
            "basis": "after_discount",
            "groups": [],
            "identified_amount": None,
            "unidentified_invoice_count": unidentified,
            "detail": "Faktur tanpa identitas pelanggan yang stabil tidak diranking dan tidak digabung berdasarkan nama.",
        }
    public = sorted(groups.values(), key=lambda row: (-row["amount"], row["label"], row["key"]))
    note = "Omzet setelah diskon dikelompokkan menurut identitas pelanggan yang stabil. Nama yang sama dengan identitas berbeda tetap terpisah."
    if unidentified:
        note = f"{note} {unidentified} faktur tanpa identitas tidak masuk peringkat."
    if mixed_label:
        note = f"{note} Beberapa nama tampilan untuk identitas yang sama tidak memecah baris."
    status = "incomplete" if unidentified or mixed_label else ("zero" if identified == 0 else "complete")
    return {
        "status": status,
        "basis": "after_discount",
        "groups": [
            {
                "key": row["key"],
                "label": row["label"],
                "amount": _num(row["amount"].quantize(MONEY_Q)),
                "invoice_count": row["invoice_count"],
            }
            for row in public
        ],
        "identified_amount": _num(identified.quantize(MONEY_Q)),
        "unidentified_invoice_count": unidentified,
        "detail": note,
    }


async def _prior_customer_ids(db, customer_ids: Sequence[str], start: str, po_ids: Optional[set]) -> set:
    if not customer_ids:
        return set()
    order_query: Dict[str, Any] = {"customer_id": {"$in": list(customer_ids)}}
    if po_ids is not None:
        order_query["id"] = {"$in": list(po_ids)}
    orders = await _cursor_all(db.orders.find(order_query, {"_id": 0, "id": 1, "customer_id": 1}))
    po_to_customer = {}
    for order in orders:
        stable = extract_stable_customer_id(order)
        if stable and order.get("id"):
            po_to_customer[str(order["id"])] = stable
    if not po_to_customer:
        return set()
    invoices = await _load_finalized_invoices(
        db,
        {"po_id": {"$in": list(po_to_customer)}, "invoice_date": {"$lt": start}},
        {"id": 1, "po_id": 1, "invoice_date": 1, "financial_status": 1, "is_cancelled": 1},
    )
    found = set()
    for inv in invoices:
        day = _calendar_day(inv.get("invoice_date"))
        if not day or day >= start:
            continue
        stable = po_to_customer.get(str(inv.get("po_id") or ""))
        if stable:
            found.add(stable)
    return found


async def _load_orders_for(db, po_ids: Sequence[str]) -> Dict[str, dict]:
    if not po_ids:
        return {}
    rows: List[dict] = []
    ids = [str(v) for v in po_ids if v]
    for index in range(0, len(ids), IN_BATCH):
        chunk = ids[index:index + IN_BATCH]
        pipeline = [
            {"$match": {"id": {"$in": chunk}}},
            {"$project": {
                "_id": 0,
                "id": 1,
                "customer_id": 1,
                "customer": 1,
                "outlet": 1,
                "line_items": {
                    "$map": {
                        "input": {"$ifNull": ["$line_items", []]},
                        "as": "li",
                        "in": {
                            "line_idx": "$$li.line_idx",
                            "line_id": "$$li.line_id",
                            "id": "$$li.id",
                            "accurate_item_id": "$$li.accurate_item_id",
                            "master_item_id": "$$li.master_item_id",
                            "name": "$$li.name",
                            "item_name": "$$li.item_name",
                            "unit": "$$li.unit",
                        },
                    }
                },
            }},
        ]
        rows.extend(await _cursor_all(db.orders.aggregate(pipeline)))
    return {str(row.get("id")): row for row in rows if row.get("id")}


async def _load_order_statuses(db, user: Optional[dict]) -> List[dict]:
    match = sales_owner_match(user)
    pipeline = [
        {"$match": match or {}},
        {"$project": {
            "_id": 0,
            "id": 1,
            "cancelled": 1,
            "current_stage": 1,
            "design_status": 1,
            "design_bypassed": 1,
            "customer_approval": 1,
            "payment_status": 1,
            "payment_bypassed": 1,
            "design_files": {
                "$cond": [
                    {"$gt": [{"$size": {"$cond": [{"$isArray": "$design_files"}, "$design_files", []]}}, 0]},
                    [True],
                    [],
                ]
            },
            "payment_files": {
                "$cond": [
                    {"$gt": [{"$size": {"$cond": [{"$isArray": "$payment_files"}, "$payment_files", []]}}, 0]},
                    [True],
                    [],
                ]
            },
        }},
    ]
    return await _cursor_all(db.orders.aggregate(pipeline))


INVOICE_FIELDS = {
    "id": 1,
    "invoice_number": 1,
    "invoice_date": 1,
    "due_date": 1,
    "po_id": 1,
    "financial_status": 1,
    "is_cancelled": 1,
    "sales": 1,
    "after_discount": 1,
    "grand_total": 1,
    "lines": 1,
}


async def get_sales_dashboard(
    db,
    *,
    start_date: str,
    end_date: str,
    preset: str = "custom",
    user: Optional[dict] = None,
    permissions: Optional[Iterable[str]] = None,
    now: Optional[datetime] = None,
) -> dict:
    kind = str(preset or "custom").strip().lower()
    if kind not in {"today", "month", "year", "custom"}:
        raise SalesDashboardValidationError("Preset tidak valid")
    start, end = _parse_range(start_date, end_date)
    today = wib_today(now)
    perms = set(permissions or [])
    can_link_orders = any(key in perms for key in ORDER_LINK_PERMS)
    scoped = await _scoped_po_ids(db, user)

    base = {
        "preset": kind,
        "start_date": start,
        "end_date": end,
        "timezone": "Asia/Jakarta",
        "as_of_date": today.isoformat(),
        "loaded_at": datetime.now(timezone.utc).isoformat(),
        "historical_position": False,
        "omitted": list(OMITTED),
    }

    period_invoices: Optional[List[dict]] = None
    period_error: Optional[Exception] = None
    try:
        extra = {"invoice_date": _date_query(start, end)}
        extra.update(_scope_clause(scoped))
        period_invoices = await _load_finalized_invoices(db, extra, INVOICE_FIELDS)
    except Exception as exc:
        period_error = exc

    if period_error is not None or period_invoices is None:
        err = _section_error(period_error or RuntimeError("Gagal memuat"))
        base.update({
            "omzet": err,
            "trend": err,
            "settlement": err,
            "sales": err,
            "customers": err,
            "customer_rank": err,
            "products": err,
        })
    else:
        previous = comparable_previous_range(kind, start, end, today)
        comparison = _comparison_payload(available=False, detail="Pembanding tidak setara untuk rentang ini.")
        if previous:
            try:
                prev_extra = {"invoice_date": _date_query(previous[0], previous[1])}
                prev_extra.update(_scope_clause(scoped))
                prev_rows = await _load_finalized_invoices(
                    db,
                    prev_extra,
                    {"id": 1, "after_discount": 1, "financial_status": 1, "is_cancelled": 1, "invoice_date": 1},
                )
                prev_total, _prev_count, prev_invalid = _sum_omzet(prev_rows)
                current_total, _count, current_invalid = _sum_omzet(period_invoices)
                if prev_invalid or current_invalid:
                    comparison = _comparison_payload(available=False, status="error", detail=prev_invalid or current_invalid)
                else:
                    comparison = _comparison_payload(
                        available=True,
                        previous_start=previous[0],
                        previous_end=previous[1],
                        previous_amount=prev_total,
                        current_amount=current_total,
                        detail=f"Dibanding {previous[0]} – {previous[1]}",
                    )
            except Exception as exc:
                comparison = _comparison_payload(available=False, status="error", detail=str(exc) or "Pembanding gagal dimuat")
        try:
            base["omzet"], base["trend"] = _build_omzet_and_trend(period_invoices, start=start, end=end, comparison=comparison)
        except Exception as exc:
            err = _section_error(exc)
            base["omzet"] = err
            base["trend"] = err
        try:
            invoice_ids = [str(inv.get("id")) for inv in period_invoices if inv.get("id")]
            receipts = await _load_receipts(db, invoice_ids)
            credit = await _load_credit(db, invoice_ids)
            base["settlement"] = _build_settlement(period_invoices, receipts, credit, start=start, end=end)
        except Exception as exc:
            base["settlement"] = _section_error(exc)
        try:
            mapping = await _load_mapping(db)
            base["sales"] = await _build_sales(db, period_invoices, mapping)
        except Exception as exc:
            base["sales"] = _section_error(exc)
        po_ids = [str(inv.get("po_id") or "") for inv in period_invoices if inv.get("po_id")]
        try:
            orders_by_id = await _load_orders_for(db, po_ids)
        except Exception as exc:
            err = _section_error(exc)
            base["products"] = err
            base["customers"] = err
            base["customer_rank"] = err
            orders_by_id = None
        if orders_by_id is not None:
            try:
                base["products"] = _build_products(period_invoices, orders_by_id)
            except Exception as exc:
                base["products"] = _section_error(exc)
            try:
                base["customer_rank"] = _build_customer_rank(period_invoices, orders_by_id)
            except Exception as exc:
                base["customer_rank"] = _section_error(exc)
            try:
                stable_ids = []
                for inv in period_invoices:
                    order = orders_by_id.get(str(inv.get("po_id") or ""))
                    stable = extract_stable_customer_id(order) if order else None
                    if stable:
                        stable_ids.append(stable)
                prior = await _prior_customer_ids(db, list(set(stable_ids)), start, scoped)
                base["customers"] = _build_customers(period_invoices, orders_by_id, prior)
            except Exception as exc:
                base["customers"] = _section_error(exc)

    try:
        open_extra = _scope_clause(scoped)
        open_invoices = await _load_finalized_invoices(
            db,
            open_extra,
            {
                "id": 1,
                "po_id": 1,
                "invoice_date": 1,
                "due_date": 1,
                "grand_total": 1,
                "financial_status": 1,
                "is_cancelled": 1,
            },
        )
        open_ids = [str(inv.get("id")) for inv in open_invoices if inv.get("id")]
        open_receipts = await _load_receipts(db, open_ids)
        open_credit = await _load_credit(db, open_ids)
        open_orders = await _load_orders_for(db, [str(inv.get("po_id") or "") for inv in open_invoices])
        base["receivables"] = _build_receivables(open_invoices, open_receipts, open_credit, open_orders, today.isoformat())
    except Exception as exc:
        base["receivables"] = _section_error(exc)

    try:
        status_rows = await _load_order_statuses(db, user)
        base["orders"] = _build_orders(status_rows, can_link=can_link_orders)
    except Exception as exc:
        base["orders"] = _section_error(exc)

    return base
