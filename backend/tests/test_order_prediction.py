"""Prediksi Order — workbook cases A–I. No live API required except optional I live skip."""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException

from permissions import ALL_PERMISSIONS, derived_nav_permissions
from services.order_prediction import (
    CONF_RENDAH,
    CONF_SEDANG,
    CONF_TINGGI,
    PATTERN_MINGGUAN,
    PERM_VIEW,
    STATUS_DATA_BELUM_CUKUP,
    STATUS_JATUH_TEMPO,
    STATUS_RISIKO,
    STATUS_TERLAMBAT,
    STATUS_TIDAK_AKTIF,
    STATUS_WAKTU_FOLLOW_UP,
    analyze_group,
    build_order_prediction,
    build_order_prediction_detail,
    can_view_order_prediction,
    customer_group_key,
    is_valid_business_order,
)


def _order(**kw):
    base = {
        "id": kw.get("id", "o1"),
        "po_number": kw.get("po_number", "PO-1"),
        "customer": kw.get("customer", "ABC Coffee"),
        "customer_name": kw.get("customer_name", kw.get("customer", "ABC Coffee")),
        "customer_id": kw.get("customer_id"),
        "outlet": kw.get("outlet", "Kemang"),
        "delivery_outlet_id": kw.get("delivery_outlet_id"),
        "order_date": kw.get("order_date", "2026-01-01"),
        "sales": kw.get("sales", "Vira"),
        "notes": kw.get("notes", ""),
        "cancelled": kw.get("cancelled", False),
    }
    for extra in ("is_test", "is_demo", "is_preview", "test_record"):
        if extra in kw:
            base[extra] = kw[extra]
    base.update({k: v for k, v in kw.items() if k not in base})
    return base


def _group(dates, **kw):
    orders = []
    for i, d in enumerate(dates, start=1):
        orders.append(_order(
            id=kw.get("id_prefix", "a") + str(i),
            po_number=kw.get("po_prefix", "PO-A") + f"-{i}",
            order_date=d,
            customer=kw.get("customer", "ABC Coffee"),
            outlet=kw.get("outlet", "Kemang"),
            customer_id=kw.get("customer_id"),
            delivery_outlet_id=kw.get("delivery_outlet_id"),
            sales=kw.get("sales", "Vira"),
        ))
    return orders


def test_case_a_weekly_median_predicts_28_jan():
    orders = _group(["2026-01-01", "2026-01-10", "2026-01-19"])
    row = analyze_group(orders, date(2026, 1, 20))
    assert row["total_orders"] == 3
    assert row["median_gap"] == 9
    assert row["average_gap"] == 9
    assert row["deviation_gap"] == 0
    assert row["cv"] == 0
    assert row["pattern"] == PATTERN_MINGGUAN
    assert row["prediction_mid"] == "2026-01-28"
    assert row["prediction_start"] == "2026-01-25"
    assert row["prediction_end"] == "2026-01-31"
    assert row["followup_start"] == "2026-01-23"
    assert row["tolerance_days"] == 3


def test_case_b_less_than_three_orders():
    orders = _group(["2026-01-01", "2026-01-10"])
    row = analyze_group(orders, date(2026, 1, 20))
    assert row["total_orders"] == 2
    assert row["followup_status"] == STATUS_DATA_BELUM_CUKUP
    assert row["pattern"] == STATUS_DATA_BELUM_CUKUP
    assert row["stability"] == STATUS_DATA_BELUM_CUKUP
    assert row["prediction_mid"] is None
    assert row["confidence"] == CONF_RENDAH


def test_case_c_same_customer_different_outlet_separate():
    kemang = _group(["2026-01-01", "2026-01-10", "2026-01-19"], outlet="Kemang", id_prefix="k", po_prefix="PO-K")
    tebet = _group(["2026-01-02", "2026-01-20"], outlet="Tebet", id_prefix="t", po_prefix="PO-T")
    data = build_order_prediction(kemang + tebet, now=datetime(2026, 1, 21, tzinfo=timezone.utc))
    keys = {(r["customer"], r["outlet"]) for r in data["rows"]}
    assert ("ABC Coffee", "Kemang") in keys
    assert ("ABC Coffee", "Tebet") in keys
    assert customer_group_key(kemang[0]) != customer_group_key(tebet[0])
    kemang_row = next(r for r in data["rows"] if r["outlet"] == "Kemang")
    tebet_row = next(r for r in data["rows"] if r["outlet"] == "Tebet")
    assert kemang_row["total_orders"] == 3
    assert tebet_row["total_orders"] == 2
    assert tebet_row["followup_status"] == STATUS_DATA_BELUM_CUKUP


def test_case_c_stable_ids_preferred_over_name():
    a = _order(customer_id="cust-1", delivery_outlet_id="out-kemang", outlet="Kemang Lama")
    b = _order(customer_id="cust-1", delivery_outlet_id="out-tebet", outlet="Tebet")
    assert customer_group_key(a) != customer_group_key(b)
    assert customer_group_key(a).startswith("id:cust-1")


def test_case_d_cancelled_and_test_excluded():
    good = _group(["2026-01-01", "2026-01-10", "2026-01-19"])
    cancelled = _order(id="cx", po_number="PO-CX", order_date="2026-01-22", cancelled=True)
    test_flag = _order(id="tx", po_number="PO-TX", order_date="2026-01-22", is_test=True)
    test_po = _order(id="tp", po_number="TEST_T28-1", order_date="2026-01-22", customer="Kopi Rumah")
    test_qa = _order(id="tq", po_number="PO-QA15-X", order_date="2026-01-22", customer="TEST_QA reimport")
    demo = _order(id="dm", po_number="PO-DM", order_date="2026-01-22", customer="DEMO Cafe")
    data = build_order_prediction(good + [cancelled, test_flag, test_po, test_qa, demo], now=datetime(2026, 1, 23, tzinfo=timezone.utc))
    assert data["summary"]["total_customers"] == 1
    assert data["rows"][0]["total_orders"] == 3
    assert not is_valid_business_order(cancelled)
    assert not is_valid_business_order(test_flag)
    assert not is_valid_business_order(test_po)
    assert not is_valid_business_order(test_qa)
    assert not is_valid_business_order(demo)


def test_case_e_new_order_recomputes():
    first = _group(["2026-01-01", "2026-01-10"])
    today = datetime(2026, 1, 30, tzinfo=timezone.utc)
    before = build_order_prediction(first, now=today)
    assert before["rows"][0]["total_orders"] == 2
    assert before["rows"][0]["prediction_mid"] is None
    after_orders = first + [_order(id="a3", po_number="PO-A-3", order_date="2026-01-19")]
    after = build_order_prediction(after_orders, now=today)
    assert after["rows"][0]["total_orders"] == 3
    assert after["rows"][0]["prediction_mid"] == "2026-01-28"
    assert after["order_total"] == 3


def test_case_f_omzet_stays_null():
    orders = _group(["2026-01-01", "2026-01-10", "2026-01-19"])
    data = build_order_prediction(orders, now=datetime(2026, 1, 20, tzinfo=timezone.utc))
    row = data["rows"][0]
    assert row["omzet_90d"] is None
    assert row["predicted_omzet"] is None
    assert row["crm_priority"] is None
    assert 0 not in (row["omzet_90d"], row["predicted_omzet"], row["crm_priority"])
    detail = build_order_prediction_detail(orders, row["customer_key"], now=datetime(2026, 1, 20, tzinfo=timezone.utc))
    assert detail["analysis"]["omzet_90d"] is None
    assert all(h["omzet"] is None for h in detail["history"])
    assert all(h["omzet"] != 0 for h in detail["history"])


def test_case_g_confidence_from_cv():
    high = analyze_group(_group(["2026-01-01", "2026-01-10", "2026-01-19"]), date(2026, 1, 20))
    assert high["cv"] == 0
    assert high["confidence"] == CONF_TINGGI
    mid = analyze_group(_group(["2026-01-01", "2026-01-10", "2026-01-25"]), date(2026, 1, 26))
    assert mid["cv"] is not None
    assert 0.25 < mid["cv"] <= 0.50
    assert mid["confidence"] == CONF_SEDANG
    low = analyze_group(_group(["2026-01-01", "2026-01-08", "2026-03-01"]), date(2026, 3, 2))
    assert low["cv"] is not None and low["cv"] > 0.50
    assert low["confidence"] == CONF_RENDAH
    short = analyze_group(_group(["2026-01-01"]), date(2026, 1, 20))
    assert short["confidence"] == CONF_RENDAH


def test_case_h_followup_status_by_today():
    orders = _group(["2026-01-01", "2026-01-10", "2026-01-19"])
    assert analyze_group(orders, date(2026, 1, 20))["followup_status"] != STATUS_DATA_BELUM_CUKUP
    assert analyze_group(orders, date(2026, 1, 20))["followup_status"] == "BELUM WAKTUNYA"
    assert analyze_group(orders, date(2026, 1, 24))["followup_status"] == STATUS_WAKTU_FOLLOW_UP
    assert analyze_group(orders, date(2026, 1, 28))["followup_status"] == STATUS_JATUH_TEMPO
    assert analyze_group(orders, date(2026, 2, 2))["followup_status"] == STATUS_TERLAMBAT
    risiko = analyze_group(orders, date(2026, 2, 12))["followup_status"]
    assert risiko == STATUS_RISIKO
    assert analyze_group(orders, date(2026, 3, 5))["followup_status"] == STATUS_TIDAK_AKTIF


def test_case_i_unauthorized_denied():
    assert can_view_order_prediction(set()) is False
    assert can_view_order_prediction({"po.view", "schedule.view"}) is False
    assert can_view_order_prediction({PERM_VIEW}) is True
    assert PERM_VIEW in ALL_PERMISSIONS
    assert "nav.reports.order_prediction" in ALL_PERMISSIONS
    nav = derived_nav_permissions({PERM_VIEW})
    assert "nav.reports.order_prediction" in nav


def test_super_admin_effective_access():
    from permissions import ROLE_DEFAULT_PERMISSIONS, effective_permissions

    assert PERM_VIEW in ALL_PERMISSIONS
    assert PERM_VIEW in ROLE_DEFAULT_PERMISSIONS["admin"]
    admin = effective_permissions("admin", None)
    assert PERM_VIEW in admin
    assert "nav.reports.order_prediction" in admin
    assert can_view_order_prediction(admin) is True


def test_default_sort_risiko_first():
    risiko = _group(["2026-01-01", "2026-01-10", "2026-01-19"], customer="Risiko Co", outlet="A", id_prefix="r", po_prefix="PO-R")
    belum = _group(["2026-03-01", "2026-03-10", "2026-03-19"], customer="Baru Co", outlet="B", id_prefix="b", po_prefix="PO-B")
    kurang = _group(["2026-03-01"], customer="Kurang Co", outlet="C", id_prefix="k", po_prefix="PO-K")
    data = build_order_prediction(
        risiko + belum + kurang,
        now=datetime(2026, 2, 12, 1, tzinfo=timezone.utc),
    )
    statuses = [r["followup_status"] for r in data["rows"]]
    assert statuses[0] == STATUS_RISIKO
    assert STATUS_DATA_BELUM_CUKUP in statuses
    assert statuses.index(STATUS_RISIKO) < statuses.index(STATUS_DATA_BELUM_CUKUP)


def test_history_gap_first_order_null():
    orders = _group(["2026-01-01", "2026-01-10", "2026-01-19"])
    detail = build_order_prediction_detail(orders, customer_group_key(orders[0]), now=datetime(2026, 1, 20, tzinfo=timezone.utc))
    hist = list(reversed(detail["history"]))
    assert hist[0]["gap_days"] is None
    assert hist[1]["gap_days"] == 9
    assert hist[2]["gap_days"] == 9
    assert detail["history"][0]["order_date"] == "2026-01-19"


def test_orders_90d_counts_without_omzet():
    orders = _group(["2025-10-01", "2026-01-01", "2026-01-10", "2026-01-19"])
    row = analyze_group(orders, date(2026, 1, 20))
    assert row["total_orders"] == 4
    assert row["orders_90d"] == 3
    assert row["omzet_90d"] is None


def test_search_and_status_filter():
    a = _group(["2026-01-01", "2026-01-10", "2026-01-19"], customer="Kopi A", outlet="Kemang")
    b = _group(["2026-01-02"], customer="Teh B", outlet="Tebet", id_prefix="b", po_prefix="PO-B")
    data = build_order_prediction(a + b, now=datetime(2026, 1, 20, tzinfo=timezone.utc), search="kopi")
    assert data["total"] == 1
    assert data["rows"][0]["customer"] == "Kopi A"
    filtered = build_order_prediction(a + b, now=datetime(2026, 1, 20, tzinfo=timezone.utc), status="data_belum_cukup")
    assert filtered["total"] == 1
    assert filtered["rows"][0]["customer"] == "Teh B"
    late = build_order_prediction(a, now=datetime(2026, 2, 12, tzinfo=timezone.utc), status="terlambat_risiko")
    assert late["total"] == 1


def raise_if_cannot_view(eff):
    if not can_view_order_prediction(eff):
        raise HTTPException(403, f"Permission '{PERM_VIEW}' diperlukan")


def test_case_i_raises_403():
    with pytest.raises(HTTPException) as ei:
        raise_if_cannot_view({"po.view"})
    assert ei.value.status_code == 403
    assert PERM_VIEW in str(ei.value.detail)


def test_case_i_local_api_403():
    """Unauthorized helper against local API must 403. Skips if backend/login unavailable."""
    import os
    import requests

    base = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8000").rstrip("/")
    if "localhost" not in base and "127.0.0.1" not in base:
        pytest.skip("not local")
    try:
        health = requests.get(f"{base}/api/system/health", timeout=3)
    except requests.RequestException:
        pytest.skip("local backend down")
    if health.status_code != 200:
        pytest.skip("local backend unhealthy")
    s = requests.Session()
    login = s.post(
        f"{base}/api/auth/login",
        json={"email": "helper@eternalpackaging.com", "password": "Eternal123!"},
        timeout=8,
    )
    if login.status_code != 200:
        pytest.skip("helper login unavailable")
    r = s.get(f"{base}/api/reports/order-prediction", timeout=10)
    assert r.status_code == 403, r.text
    assert PERM_VIEW in str(r.json().get("detail") or r.text)
