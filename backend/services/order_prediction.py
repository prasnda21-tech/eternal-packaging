"""Prediksi Order — derived report from authoritative Order Masuk.

Read-only. Does not write orders. Omzet is unavailable and stays null.
Timezone: WIB (UTC+7), same convention as Analitik Output / Status PO.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median, stdev
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote, unquote

from services.customer_identity import (
    CustomerIdentityContext,
    build_identity_context,
    collect_outlet_names,
    display_customer_name,
    extract_stable_customer_id,
    format_outlet_label,
    resolve_customer_identity,
)

WIB = timezone(timedelta(hours=7))

PERM_VIEW = "reports.order_prediction.view"
NAV_KEY = "nav.reports.order_prediction"

MIN_ORDERS = 3
TOLERANCE_MIN_DAYS = 3
TOLERANCE_MEDIAN_RATIO = 0.20
FOLLOWUP_LEAD_DAYS = 5
INACTIVE_MIN_DAYS = 40
INACTIVE_MEDIAN_MULT = 2
LOOKBACK_DAYS = 90

STATUS_DATA_BELUM_CUKUP = "DATA BELUM CUKUP"
STATUS_BELUM_WAKTUNYA = "BELUM WAKTUNYA"
STATUS_WAKTU_FOLLOW_UP = "WAKTU FOLLOW-UP"
STATUS_JATUH_TEMPO = "JATUH TEMPO"
STATUS_TERLAMBAT = "TERLAMBAT"
STATUS_RISIKO = "RISIKO"
STATUS_TIDAK_AKTIF = "TIDAK AKTIF"

STATUS_SORT = {
    STATUS_RISIKO: 0,
    STATUS_TERLAMBAT: 1,
    STATUS_JATUH_TEMPO: 2,
    STATUS_WAKTU_FOLLOW_UP: 3,
    STATUS_BELUM_WAKTUNYA: 4,
    STATUS_TIDAK_AKTIF: 5,
    STATUS_DATA_BELUM_CUKUP: 6,
}

PATTERN_MINGGUAN = "MINGGUAN"
PATTERN_2_MINGGUAN = "2 MINGGUAN"
PATTERN_BULANAN = "BULANAN"
PATTERN_2_BULANAN = "2 BULANAN"
PATTERN_3_BULANAN = "3 BULANAN"
PATTERN_TIDAK_RUTIN = "TIDAK RUTIN"

STABILITY_SANGAT = "SANGAT STABIL"
STABILITY_CUKUP = "CUKUP STABIL"
STABILITY_FLUKTUATIF = "FLUKTUATIF"
STABILITY_TIDAK = "TIDAK STABIL"

CONF_TINGGI = "TINGGI"
CONF_SEDANG = "SEDANG"
CONF_RENDAH = "RENDAH"

REKOMENDASI = {
    STATUS_DATA_BELUM_CUKUP: "Tambah histori sampai minimal 3 order.",
    STATUS_BELUM_WAKTUNYA: "Pantau; mulai follow-up pada tanggal yang direkomendasikan.",
    STATUS_WAKTU_FOLLOW_UP: "CRM/sales hubungi customer sebelum estimasi kebutuhan berikutnya.",
    STATUS_JATUH_TEMPO: "Konfirmasi kebutuhan, desain, dan tanggal produksi.",
    STATUS_TERLAMBAT: "Konfirmasi kebutuhan, harga, stok, dan kendala terakhir.",
    STATUS_RISIKO: "Sales visit; cek kompetitor, komplain, dan perubahan kebutuhan.",
    STATUS_TIDAK_AKTIF: "Evaluasi customer dan terapkan aturan reshuffle prospek.",
}

STATUS_ALIASES = {
    "": "",
    "semua": "",
    STATUS_DATA_BELUM_CUKUP: STATUS_DATA_BELUM_CUKUP,
    "data_belum_cukup": STATUS_DATA_BELUM_CUKUP,
    STATUS_BELUM_WAKTUNYA: STATUS_BELUM_WAKTUNYA,
    "belum_waktunya": STATUS_BELUM_WAKTUNYA,
    STATUS_WAKTU_FOLLOW_UP: STATUS_WAKTU_FOLLOW_UP,
    "waktu_follow_up": STATUS_WAKTU_FOLLOW_UP,
    STATUS_JATUH_TEMPO: STATUS_JATUH_TEMPO,
    "jatuh_tempo": STATUS_JATUH_TEMPO,
    STATUS_TERLAMBAT: STATUS_TERLAMBAT,
    "terlambat": STATUS_TERLAMBAT,
    STATUS_RISIKO: STATUS_RISIKO,
    "risiko": STATUS_RISIKO,
    STATUS_TIDAK_AKTIF: STATUS_TIDAK_AKTIF,
    "tidak_aktif": STATUS_TIDAK_AKTIF,
    "terlambat_risiko": "TERLAMBAT_RISIKO",
    "terlambat / risiko": "TERLAMBAT_RISIKO",
}

PATTERN_ALIASES = {
    PATTERN_MINGGUAN: PATTERN_MINGGUAN,
    "mingguan": PATTERN_MINGGUAN,
    PATTERN_2_MINGGUAN: PATTERN_2_MINGGUAN,
    "2_mingguan": PATTERN_2_MINGGUAN,
    PATTERN_BULANAN: PATTERN_BULANAN,
    "bulanan": PATTERN_BULANAN,
    PATTERN_2_BULANAN: PATTERN_2_BULANAN,
    "2_bulanan": PATTERN_2_BULANAN,
    PATTERN_3_BULANAN: PATTERN_3_BULANAN,
    "3_bulanan": PATTERN_3_BULANAN,
    PATTERN_TIDAK_RUTIN: PATTERN_TIDAK_RUTIN,
    "tidak_rutin": PATTERN_TIDAK_RUTIN,
    STATUS_DATA_BELUM_CUKUP: STATUS_DATA_BELUM_CUKUP,
}

CONF_ALIASES = {
    CONF_TINGGI: CONF_TINGGI,
    "tinggi": CONF_TINGGI,
    CONF_SEDANG: CONF_SEDANG,
    "sedang": CONF_SEDANG,
    CONF_RENDAH: CONF_RENDAH,
    "rendah": CONF_RENDAH,
}

ORDER_PREDICTION_PROJECTION = {
    "_id": 0,
    "id": 1,
    "po_number": 1,
    "order_date": 1,
    "created_at": 1,
    "customer": 1,
    "customer_name": 1,
    "customer_id": 1,
    "outlet": 1,
    "delivery_outlet_id": 1,
    "sales": 1,
    "sales_user": 1,
    "seller": 1,
    "notes": 1,
    "cancelled": 1,
    "is_test": 1,
    "is_demo": 1,
    "is_preview": 1,
    "test_record": 1,
}

_WS_RE = re.compile(r"\s+")
_TEST_PO_RE = re.compile(r"^(TEST|DEMO|PREVIEW)[-_]", re.I)
_TEST_NAME_RE = re.compile(r"^(TEST|DEMO|PREVIEW)([\s_-]|$)", re.I)


def wib_today(now: Optional[datetime] = None) -> date:
    dt = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(WIB).date()


def parse_iso_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=WIB)
        return dt.astimezone(WIB).date()
    s = str(value).strip()
    if not s:
        return None
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=WIB)
            return dt.astimezone(WIB).date()
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def excel_round(value: Optional[float]) -> Optional[int]:
    """Excel-style half-up rounding for prediction day offsets."""
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n):
        return None
    if n >= 0:
        return int(math.floor(n + 0.5))
    return int(math.ceil(n - 0.5))


def normalize_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _WS_RE.sub(" ", text)


def _customer_name(order: dict) -> str:
    return display_customer_name(order)


def _outlet_name(order: dict) -> str:
    return str(order.get("outlet") or "").strip()


def _customer_id(order: dict) -> Optional[str]:
    return extract_stable_customer_id(order)


def _outlet_id(order: dict) -> Optional[str]:
    text = str(order.get("delivery_outlet_id") or order.get("outlet_id") or "").strip()
    return text or None


def _sales_name(order: dict) -> str:
    return str(order.get("sales") or order.get("sales_user") or order.get("seller") or "").strip()


def customer_group_key(order: dict, context: Optional[CustomerIdentityContext] = None) -> str:
    """Logical customer identity only. Outlet is never part of the key."""
    return resolve_customer_identity(order, context).key


def encode_customer_key(key: str) -> str:
    return quote(key or "", safe="")


def decode_customer_key(raw: str) -> str:
    return unquote(raw or "")


def is_valid_business_order(order: dict) -> bool:
    """Valid Order Masuk only. Does not mutate the source document."""
    if not order:
        return False
    if order.get("cancelled") is True:
        return False
    if order.get("is_test") or order.get("is_demo") or order.get("is_preview") or order.get("test_record"):
        return False
    po = str(order.get("po_number") or "").strip()
    if _TEST_PO_RE.match(po):
        return False
    name = _customer_name(order)
    if _TEST_NAME_RE.match(name):
        return False
    if parse_iso_date(order.get("order_date") or order.get("created_at")) is None:
        return False
    if not _customer_id(order) and not name:
        return False
    return True


def order_date_of(order: dict) -> Optional[date]:
    return parse_iso_date(order.get("order_date") or order.get("created_at"))


def can_view_order_prediction(eff: Optional[Iterable[str]]) -> bool:
    perms = set(eff or [])
    return PERM_VIEW in perms


def report_parameters() -> dict:
    return {
        "min_orders": MIN_ORDERS,
        "tolerance_min_days": TOLERANCE_MIN_DAYS,
        "tolerance_median_ratio": TOLERANCE_MEDIAN_RATIO,
        "followup_lead_days": FOLLOWUP_LEAD_DAYS,
        "inactive_min_days": INACTIVE_MIN_DAYS,
        "inactive_median_multiplier": INACTIVE_MEDIAN_MULT,
        "lookback_days": LOOKBACK_DAYS,
        "timezone": "WIB",
        "omzet_available": False,
    }


def _gaps(dates: Sequence[date]) -> List[int]:
    if len(dates) < 2:
        return []
    return [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]


def _stability(cv: Optional[float], enough: bool) -> str:
    if not enough or cv is None:
        return STATUS_DATA_BELUM_CUKUP
    if cv <= 0.25:
        return STABILITY_SANGAT
    if cv <= 0.50:
        return STABILITY_CUKUP
    if cv <= 0.75:
        return STABILITY_FLUKTUATIF
    return STABILITY_TIDAK


def _pattern(median_gap: Optional[float], enough: bool) -> str:
    if not enough or median_gap is None:
        return STATUS_DATA_BELUM_CUKUP
    if median_gap <= 10:
        return PATTERN_MINGGUAN
    if median_gap <= 18:
        return PATTERN_2_MINGGUAN
    if median_gap <= 40:
        return PATTERN_BULANAN
    if median_gap <= 70:
        return PATTERN_2_BULANAN
    if median_gap <= 100:
        return PATTERN_3_BULANAN
    return PATTERN_TIDAK_RUTIN


def _confidence(cv: Optional[float], enough: bool) -> str:
    if not enough:
        return CONF_RENDAH
    if cv is None:
        return CONF_RENDAH
    if cv <= 0.25:
        return CONF_TINGGI
    if cv <= 0.50:
        return CONF_SEDANG
    return CONF_RENDAH


def _tolerance_days(median_gap: Optional[float], deviation: Optional[float]) -> Optional[int]:
    if median_gap is None:
        return None
    candidates = [TOLERANCE_MIN_DAYS, excel_round(median_gap * TOLERANCE_MEDIAN_RATIO) or 0]
    if deviation is not None:
        candidates.append(excel_round(deviation) or 0)
    return max(candidates)


def _inactive_threshold(median_gap: Optional[float]) -> int:
    if median_gap is None:
        return INACTIVE_MIN_DAYS
    return max(INACTIVE_MIN_DAYS, median_gap * INACTIVE_MEDIAN_MULT)


def followup_status(
    *,
    enough: bool,
    today: date,
    followup_start: Optional[date],
    pred_mid: Optional[date],
    pred_end: Optional[date],
    days_since: Optional[int],
    median_gap: Optional[float],
    tolerance: Optional[int],
) -> str:
    if not enough:
        return STATUS_DATA_BELUM_CUKUP
    if pred_mid is None or pred_end is None or followup_start is None or days_since is None:
        return STATUS_DATA_BELUM_CUKUP

    inactive_at = _inactive_threshold(median_gap)
    close_after = max(int(tolerance or 0), excel_round((median_gap or 0) * 0.5) or 0)
    late_until = pred_end + timedelta(days=close_after)

    if days_since >= inactive_at and today > pred_end:
        return STATUS_TIDAK_AKTIF
    if today < followup_start:
        return STATUS_BELUM_WAKTUNYA
    if today < pred_mid:
        return STATUS_WAKTU_FOLLOW_UP
    if today <= pred_end:
        return STATUS_JATUH_TEMPO
    if today <= late_until:
        return STATUS_TERLAMBAT
    return STATUS_RISIKO


def annotate_history(orders: Sequence[dict]) -> List[dict]:
    """Attach previous-order date and gap. Input must already be one customer+outlet, date-asc."""
    rows = []
    prev_date = None
    for order in orders:
        d = order_date_of(order)
        gap = (d - prev_date).days if d and prev_date else None
        omzet = order.get("omzet")
        if omzet is not None and str(omzet).strip() == "":
            omzet = None
        source = str(order.get("history_source") or order.get("source") or "ORDER MASUK").strip() or "ORDER MASUK"
        rows.append({
            "id": order.get("id"),
            "po_number": order.get("po_number") or "",
            "customer": _customer_name(order),
            "outlet": _outlet_name(order),
            "order_date": d.isoformat() if d else None,
            "previous_order_date": prev_date.isoformat() if prev_date else None,
            "gap_days": gap,
            "sales": _sales_name(order) or None,
            "omzet": omzet,
            "notes": str(order.get("notes") or "").strip() or None,
            "source": source,
        })
        if d:
            prev_date = d
    return rows


def analyze_group(
    orders: Sequence[dict],
    today: date,
    *,
    context: Optional[CustomerIdentityContext] = None,
    customer_key: str = "",
) -> dict:
    dated = []
    for o in orders:
        d = order_date_of(o)
        if d:
            dated.append((d, o))
    dated.sort(key=lambda x: (x[0], str(x[1].get("po_number") or ""), str(x[1].get("id") or "")))
    dates = [d for d, _ in dated]
    src = [o for _, o in dated]
    history = annotate_history(src)

    first = dates[0] if dates else None
    last = dates[-1] if dates else None
    total = len(dates)
    enough = total >= MIN_ORDERS
    gaps = _gaps(dates)
    med = float(median(gaps)) if gaps else None
    avg = float(mean(gaps)) if gaps else None
    dev = float(stdev(gaps)) if len(gaps) >= 2 else None
    cv = (dev / avg) if dev is not None and avg not in (None, 0) else None
    days_since = (today - last).days if last else None
    lookback = today - timedelta(days=LOOKBACK_DAYS)
    orders_90 = sum(1 for d in dates if d >= lookback)

    sample = src[-1] if src else {}
    resolved = resolve_customer_identity(sample, context) if sample else None
    key = customer_key or (resolved.key if resolved else "")
    customer = (resolved.display_name if resolved else "") or _customer_name(sample)
    outlets = collect_outlet_names(src)
    outlet = format_outlet_label(src)
    sales_values = sorted({_sales_name(o) for o in src if _sales_name(o)})

    pred_mid = None
    pred_start = None
    pred_end = None
    followup_start = None
    tolerance = None
    if enough and last and med is not None:
        offset = excel_round(med)
        if offset is not None:
            pred_mid = last + timedelta(days=offset)
            tolerance = _tolerance_days(med, dev)
            pred_start = pred_mid - timedelta(days=tolerance)
            pred_end = pred_mid + timedelta(days=tolerance)
            followup_start = min(pred_start, pred_mid - timedelta(days=FOLLOWUP_LEAD_DAYS))

    status = followup_status(
        enough=enough,
        today=today,
        followup_start=followup_start,
        pred_mid=pred_mid,
        pred_end=pred_end,
        days_since=days_since,
        median_gap=med,
        tolerance=tolerance,
    )
    pattern = _pattern(med, enough)
    stability = _stability(cv, enough)
    confidence = _confidence(cv, enough)

    sort_date = followup_start or pred_mid or last
    return {
        "customer_key": key,
        "customer": customer,
        "outlet": outlet,
        "outlets": outlets,
        "customer_id": (resolved.stable_id if resolved else None) or _customer_id(sample),
        "outlet_id": None,
        "identity_ambiguous": bool(resolved.ambiguous) if resolved else False,
        "sales": sales_values[0] if len(sales_values) == 1 else None,
        "sales_values": sales_values,
        "total_orders": total,
        "first_order_date": first.isoformat() if first else None,
        "last_order_date": last.isoformat() if last else None,
        "days_since_order": days_since,
        "median_gap": med,
        "average_gap": avg,
        "deviation_gap": dev,
        "cv": cv,
        "stability": stability,
        "pattern": pattern,
        "prediction_mid": pred_mid.isoformat() if pred_mid else None,
        "prediction_start": pred_start.isoformat() if pred_start else None,
        "prediction_end": pred_end.isoformat() if pred_end else None,
        "followup_start": followup_start.isoformat() if followup_start else None,
        "tolerance_days": tolerance,
        "followup_status": status,
        "recommendation": REKOMENDASI.get(status, ""),
        "orders_90d": orders_90,
        "omzet_90d": None,
        "predicted_omzet": None,
        "crm_priority": None,
        "confidence": confidence,
        "history": list(reversed(history)),
        "_sort_status": STATUS_SORT.get(status, 99),
        "_sort_date": (sort_date or date.max).isoformat(),
    }


def build_order_rows(groups: Sequence[dict]) -> List[dict]:
    rows = []
    for g in groups:
        for h in reversed(g.get("history") or []):
            rows.append({
                **h,
                "customer_key": g.get("customer_key"),
                "customer": g.get("customer"),
                "outlet": g.get("outlet"),
            })
    rows.sort(key=lambda r: (r.get("order_date") or "", r.get("po_number") or ""), reverse=True)
    return rows


def _normalize_status_filter(raw: Optional[str]) -> str:
    return STATUS_ALIASES.get(str(raw or "").strip(), STATUS_ALIASES.get(str(raw or "").strip().lower(), str(raw or "").strip()))


def _normalize_pattern_filter(raw: Optional[str]) -> str:
    text = str(raw or "").strip()
    return PATTERN_ALIASES.get(text, PATTERN_ALIASES.get(text.lower(), text))


def _normalize_conf_filter(raw: Optional[str]) -> str:
    text = str(raw or "").strip()
    return CONF_ALIASES.get(text, CONF_ALIASES.get(text.lower(), text))


def matches_row_filters(
    row: dict,
    *,
    search: str = "",
    status: str = "",
    pattern: str = "",
    confidence: str = "",
    sales: str = "",
) -> bool:
    q = (search or "").strip().lower()
    if q:
        blob = " ".join([
            str(row.get("customer") or ""),
            str(row.get("outlet") or ""),
            str(row.get("customer_key") or ""),
        ]).lower()
        if q not in blob:
            return False
    st = _normalize_status_filter(status)
    if st == "TERLAMBAT_RISIKO":
        if row.get("followup_status") not in (STATUS_TERLAMBAT, STATUS_RISIKO):
            return False
    elif st and row.get("followup_status") != st:
        return False
    pat = _normalize_pattern_filter(pattern)
    if pat and row.get("pattern") != pat:
        return False
    conf = _normalize_conf_filter(confidence)
    if conf and row.get("confidence") != conf:
        return False
    sl = (sales or "").strip().lower()
    if sl:
        values = [str(v).lower() for v in (row.get("sales_values") or [])]
        if sl not in values and sl != str(row.get("sales") or "").lower():
            return False
    return True


def sort_rows(rows: Sequence[dict]) -> List[dict]:
    return sorted(
        rows,
        key=lambda r: (
            r.get("_sort_status", 99),
            r.get("_sort_date") or "9999-99-99",
            str(r.get("customer") or "").lower(),
            str(r.get("outlet") or "").lower(),
        ),
    )


def public_row(row: dict, *, include_history: bool = False) -> dict:
    out = {k: v for k, v in row.items() if not k.startswith("_")}
    if not include_history:
        out.pop("history", None)
    return out


def summarize(rows: Sequence[dict]) -> dict:
    counts = {
        "total_customers": len(rows),
        "waktu_follow_up": 0,
        "jatuh_tempo": 0,
        "terlambat": 0,
        "risiko": 0,
        "terlambat_risiko": 0,
        "tidak_aktif": 0,
        "data_belum_cukup": 0,
        "belum_waktunya": 0,
    }
    for r in rows:
        st = r.get("followup_status")
        if st == STATUS_WAKTU_FOLLOW_UP:
            counts["waktu_follow_up"] += 1
        elif st == STATUS_JATUH_TEMPO:
            counts["jatuh_tempo"] += 1
        elif st == STATUS_TERLAMBAT:
            counts["terlambat"] += 1
            counts["terlambat_risiko"] += 1
        elif st == STATUS_RISIKO:
            counts["risiko"] += 1
            counts["terlambat_risiko"] += 1
        elif st == STATUS_TIDAK_AKTIF:
            counts["tidak_aktif"] += 1
        elif st == STATUS_DATA_BELUM_CUKUP:
            counts["data_belum_cukup"] += 1
        elif st == STATUS_BELUM_WAKTUNYA:
            counts["belum_waktunya"] += 1
    return counts


def collect_sales_options(rows: Sequence[dict]) -> List[str]:
    names = set()
    for r in rows:
        for v in r.get("sales_values") or []:
            if v:
                names.add(v)
    return sorted(names, key=lambda s: s.lower())


def build_order_prediction(
    orders: Sequence[dict],
    *,
    now: Optional[datetime] = None,
    search: str = "",
    status: str = "",
    pattern: str = "",
    confidence: str = "",
    sales: str = "",
    skip: int = 0,
    limit: int = 500,
    supplemental: Optional[Sequence[dict]] = None,
    masters: Optional[Sequence[dict]] = None,
    identity_context: Optional[CustomerIdentityContext] = None,
) -> dict:
    today = wib_today(now)
    from services.order_prediction_import import merge_prediction_orders

    if supplemental is not None:
        combined = merge_prediction_orders(orders, supplemental)
    else:
        combined = [o for o in orders if is_valid_business_order(o)]
        for o in combined:
            o.setdefault("history_source", "ORDER MASUK")
            o.setdefault("source", "ORDER MASUK")

    valid = [o for o in combined if is_valid_business_order(o)]
    context = identity_context or build_identity_context(masters, valid, [])
    groups: Dict[str, List[dict]] = {}
    for o in valid:
        key = customer_group_key(o, context)
        if not key:
            continue
        groups.setdefault(key, []).append(o)

    analyzed = [
        analyze_group(items, today, context=context, customer_key=key)
        for key, items in groups.items()
    ]
    analyzed = sort_rows(analyzed)
    summary = summarize(analyzed)
    sales_options = collect_sales_options(analyzed)
    filtered = [
        r for r in analyzed
        if matches_row_filters(
            r, search=search, status=status, pattern=pattern, confidence=confidence, sales=sales,
        )
    ]
    skip = max(0, int(skip or 0))
    limit = max(1, min(int(limit or 500), 2000))
    page = filtered[skip:skip + limit]
    order_rows = build_order_rows(analyzed)
    if search:
        q = search.strip().lower()
        order_rows = [
            r for r in order_rows
            if q in " ".join([
                str(r.get("customer") or ""),
                str(r.get("outlet") or ""),
                str(r.get("po_number") or ""),
            ]).lower()
        ]

    return {
        "as_of": today.isoformat(),
        "parameters": report_parameters(),
        "summary": summary,
        "rows": [public_row(r) for r in page],
        "total": len(filtered),
        "skip": skip,
        "limit": limit,
        "sales_options": sales_options,
        "order_rows": order_rows[:2000],
        "order_total": len(order_rows),
        "source": "orders+import" if supplemental is not None else "orders",
    }


def build_order_prediction_detail(
    orders: Sequence[dict],
    customer_key: str,
    *,
    now: Optional[datetime] = None,
    supplemental: Optional[Sequence[dict]] = None,
    masters: Optional[Sequence[dict]] = None,
    identity_context: Optional[CustomerIdentityContext] = None,
) -> Optional[dict]:
    today = wib_today(now)
    from services.order_prediction_import import merge_prediction_orders

    if supplemental is not None:
        combined = merge_prediction_orders(orders, supplemental)
    else:
        combined = [o for o in orders if is_valid_business_order(o)]
        for o in combined:
            o.setdefault("history_source", "ORDER MASUK")
            o.setdefault("source", "ORDER MASUK")

    valid = [o for o in combined if is_valid_business_order(o)]
    context = identity_context or build_identity_context(masters, valid, [])
    key = decode_customer_key(customer_key)
    grouped: List[dict] = []
    for o in valid:
        if customer_group_key(o, context) == key:
            grouped.append(o)
    if not grouped:
        return None
    row = analyze_group(grouped, today, context=context, customer_key=key)
    return {
        "as_of": today.isoformat(),
        "parameters": report_parameters(),
        "analysis": public_row(row, include_history=False),
        "history": row.get("history") or [],
        "source": "orders+import" if supplemental is not None else "orders",
    }
