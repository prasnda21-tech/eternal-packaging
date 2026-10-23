"""Buffer Stock is excluded from modern delivery lateness only.

Does not change dates, SJ, workflow, or delivered semantics.
"""
from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.delivery_schedule import schedule_rows, schedule_status_code
from services.sales_pwa import serialize_list_row
from services.status_po import filter_orders_for_view


def _import_server():
    from dotenv import load_dotenv

    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "test_database")
    import server

    return server


def _po(**extra):
    base = {
        "id": "po-buf-late",
        "po_number": "PO/BUF/LATE",
        "customer": "PT Buffer",
        "cancelled": False,
        "hold": False,
        "current_stage": 8,
        "delivery_status": "Belum Terkirim",
        "terkirim_confirmed": False,
        "qty_delivered": 0,
        "total_quantity": 100,
        "quantity": 100,
        "scheduled_delivery_date": "2020-01-15",
        "delivery_deadline_manual": "2020-01-15T00:00:00+07:00",
    }
    base.update(extra)
    return base


def _attach(server, order, lead_days=3):
    o = copy.deepcopy(order)
    server._attach_deadline_snapshot(o, lead_days)
    return o


def _pwa_overdue(order):
    return serialize_list_row(copy.deepcopy(order), [], [])["tanggal_kirim_overdue"]


def _overdue_view(server, order):
    prepared = _attach(server, order)
    return [o["id"] for o in filter_orders_for_view([prepared], "overdue")]


def test_case_a_non_buffer_yesterday_is_late():
    server = _import_server()
    o = _po(buffer_stock=False)
    snap = _attach(server, o)
    assert snap["delivery_deadline_status"] == "overdue"
    assert schedule_status_code(o, today="2026-09-19") == "terlambat"
    assert _pwa_overdue(o) is True
    assert o["id"] in _overdue_view(server, o)


def test_case_b_buffer_yesterday_is_not_late():
    server = _import_server()
    o = _po(buffer_stock=True)
    snap = _attach(server, o)
    assert snap["delivery_deadline_status"] != "overdue"
    assert snap["delivery_deadline_status"] == "normal"
    assert schedule_status_code(o, today="2026-09-19") != "terlambat"
    assert schedule_status_code(o, today="2026-09-19") == "terjadwal"
    assert _pwa_overdue(o) is False
    assert o["id"] not in _overdue_view(server, o)


def test_case_c_buffer_today_is_not_late():
    server = _import_server()
    today = "2026-09-19"
    o = _po(
        buffer_stock=True,
        scheduled_delivery_date=today,
        delivery_deadline_manual=f"{today}T00:00:00+07:00",
    )
    snap = _attach(server, o)
    assert snap["delivery_deadline_status"] != "overdue"
    assert schedule_status_code(o, today=today) == "hari_ini"
    assert _pwa_overdue(o) is False
    assert o["id"] not in _overdue_view(server, o)


def test_case_d_buffer_future_is_not_late():
    server = _import_server()
    o = _po(
        buffer_stock=True,
        scheduled_delivery_date="2099-12-31",
        delivery_deadline_manual="2099-12-31T00:00:00+07:00",
    )
    snap = _attach(server, o)
    assert snap["delivery_deadline_status"] not in ("overdue",)
    assert snap["delivery_deadline_status"] == "normal"
    assert schedule_status_code(o, today="2026-09-19") == "terjadwal"
    assert _pwa_overdue(o) is False
    assert o["id"] not in _overdue_view(server, o)


def test_case_e_buffer_no_date_preserves_unknown():
    server = _import_server()
    o = _po(
        buffer_stock=True,
        scheduled_delivery_date=None,
        delivery_deadline_manual=None,
    )
    snap = _attach(server, o)
    assert snap["delivery_deadline_status"] == "unknown"
    assert schedule_status_code(o, today="2026-09-19") == "belum_dijadwalkan"
    assert _pwa_overdue(o) is False
    assert o["id"] not in _overdue_view(server, o)


def test_case_f_buffer_delivered_stays_terkirim():
    server = _import_server()
    o = _po(
        buffer_stock=True,
        delivery_status="Terkirim",
        current_stage=14,
        terkirim_confirmed=True,
        qty_delivered=100,
    )
    snap = _attach(server, o)
    assert snap["delivery_deadline_status"] == "terkirim"
    assert schedule_status_code(o, today="2026-09-19") == "terkirim"
    assert _pwa_overdue(o) is False
    assert o["id"] not in _overdue_view(server, o)


def test_case_g_missing_buffer_stock_matches_normal_overdue():
    server = _import_server()
    missing = _po()
    missing.pop("buffer_stock", None)
    falsey = _po(buffer_stock=False)
    none = _po(buffer_stock=None)
    for o in (missing, falsey, none):
        snap = _attach(server, o)
        assert snap["delivery_deadline_status"] == "overdue"
        assert schedule_status_code(o, today="2026-09-19") == "terlambat"
        assert _pwa_overdue(o) is True
        assert o["id"] in _overdue_view(server, o)


def test_parity_same_order_false_vs_true():
    server = _import_server()
    a = _po(buffer_stock=False)
    b = _po(buffer_stock=True)
    assert _attach(server, a)["delivery_deadline_status"] == "overdue"
    assert _attach(server, b)["delivery_deadline_status"] != "overdue"
    assert schedule_status_code(a, today="2026-09-19") == "terlambat"
    assert schedule_status_code(b, today="2026-09-19") != "terlambat"
    assert _pwa_overdue(a) is True
    assert _pwa_overdue(b) is False
    assert a["id"] in _overdue_view(server, a)
    assert b["id"] not in _overdue_view(server, b)
    # Non-late fields stay intact.
    sa, sb = _attach(server, a), _attach(server, b)
    assert sa["tanggal_pengiriman"] == sb["tanggal_pengiriman"] == "2020-01-15"
    assert sa["delivery_deadline_effective"] == sb["delivery_deadline_effective"]
    assert a["scheduled_delivery_date"] == b["scheduled_delivery_date"]
    assert a["current_stage"] == b["current_stage"]
    assert a["delivery_status"] == b["delivery_status"]


def test_non_buffer_h1_h6_unknown_terkirim_unchanged():
    from datetime import timedelta

    server = _import_server()
    future = _po(
        buffer_stock=False,
        scheduled_delivery_date="2099-12-31",
        delivery_deadline_manual="2099-12-31T00:00:00+07:00",
    )
    assert _attach(server, future)["delivery_deadline_status"] == "normal"
    no_date = _po(
        buffer_stock=False,
        scheduled_delivery_date=None,
        delivery_deadline_manual=None,
    )
    assert _attach(server, no_date)["delivery_deadline_status"] == "unknown"
    done = _po(buffer_stock=False, delivery_status="Terkirim", current_stage=14)
    done_snap = _attach(server, done)
    assert done_snap["delivery_deadline_status_basis"] == "qty"
    assert done_snap["delivery_deadline_status"] == "overdue"
    closed = _po(buffer_stock=False, delivery_status="Terkirim", current_stage=14, qty_delivered=100)
    assert _attach(server, closed)["delivery_deadline_status"] == "terkirim"
    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    h6 = {"id": "h6", "current_stage": 5, "delivery_status": "Belum", "buffer_stock": False}
    assert server._deadline_status_for(soon, h6) == "h6"
    assert server._deadline_status_for(later, h6) == "h1"
    assert server._deadline_status_for(soon, {**h6, "buffer_stock": True}) == "h6"
    assert server._deadline_status_for(later, {**h6, "buffer_stock": True}) == "h1"


@pytest.mark.parametrize("flag", [False, None])
def test_falsey_buffer_stock_stays_overdue(flag):
    server = _import_server()
    o = _po(buffer_stock=flag)
    assert _attach(server, o)["delivery_deadline_status"] == "overdue"
