"""Dashboard Pengerjaan / Pengiriman Hari Ini — WIB, partial, empty, not created_at."""
from datetime import date, datetime, timezone, timedelta
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.dashboard_today import (
    build_shipments_today,
    build_work_today,
    delivery_status_label,
    is_shipment_today,
    is_work_today,
    wib_today,
    work_today_mongo_query,
)

WIB = timezone(timedelta(hours=7))


def test_wib_today_not_utc_date_when_evening_utc():
    # 2026-08-25 17:30 UTC = 2026-08-26 00:30 WIB
    now = datetime(2026, 8, 25, 17, 30, tzinfo=timezone.utc)
    assert wib_today(now) == date(2026, 8, 26)


def test_work_today_uses_work_date_not_created_at():
    today = date(2026, 8, 25)
    created_today = {
        "id": "1", "po_number": "PO-OLD", "cancelled": False,
        "created_at": "2026-08-25T01:00:00+00:00",
        "work_date": "2026-08-20",
        "current_stage": 8,
    }
    scheduled = {
        "id": "2", "po_number": "PO-WORK", "cancelled": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "work_date": "2026-08-25",
        "current_stage": 8,
        "customer": "Toko", "outlet": "A",
        "line_items": [{"name": "Cup", "quantity": 100}],
        "operator_splits": [{"operator_name": "Budi", "work_date": "2026-08-25", "quantity": 100, "qty_completed": 10}],
    }
    cancelled = {**scheduled, "id": "3", "po_number": "PO-X", "cancelled": True}
    assert not is_work_today(created_today, today)
    assert is_work_today(scheduled, today)
    assert not is_work_today(cancelled, today)
    out = build_work_today([created_today, scheduled, cancelled], today)
    assert out["total"] == 1
    assert out["preview"][0]["po_number"] == "PO-WORK"
    assert out["preview"][0]["operator"] == "Budi"


def test_work_today_operator_split_date():
    today = date(2026, 8, 25)
    o = {
        "id": "4", "po_number": "PO-OP", "cancelled": False,
        "work_date": None,
        "operator_splits": [{"operator_name": "Sari", "work_date": "2026-08-25"}],
        "current_stage": 10,
    }
    assert is_work_today(o, today)


def test_work_empty():
    out = build_work_today([], date(2026, 8, 25))
    assert out["total"] == 0
    assert out["preview"] == []


def test_shipment_today_not_created_at_po():
    today = date(2026, 8, 25)
    only_created = {
        "id": "s1", "po_number": "PO-C", "cancelled": False,
        "created_at": "2026-08-25T01:00:00+00:00",
        "current_stage": 5,
    }
    with_sj = {
        "id": "s2", "po_number": "PO-SJ", "cancelled": False, "customer": "X",
        "current_stage": 12,
        "delivery_status": "Sebagian Terkirim",
        "line_items": [{"name": "A", "quantity": 10000}, {"name": "B", "quantity": 5000}],
        "shipments": [{
            "number": "SJ-1",
            "created_at": "2026-08-25T03:00:00+07:00",
            "lines": [{"line_idx": 0, "qty": 4000}],
        }],
        "qty_delivered": 4000,
        "packing_info": {"qty_packed": 4000, "lines": [{"line_idx": 0, "packed_qty": 4000}]},
    }
    assert not is_shipment_today(only_created, today)
    assert is_shipment_today(with_sj, today)
    out = build_shipments_today([only_created, with_sj], today)
    assert out["total"] == 1
    row = out["preview"][0]
    assert row["po_number"] == "PO-SJ"
    assert row["surat_jalan"] == "SJ-1"
    assert row["qty"] == 4000
    assert row["status"] != "Terkirim"
    assert row["partial"] is True


def test_partial_delivery_not_full():
    o = {
        "delivery_status": "Terkirim",
        "current_stage": 13,
        "qty_delivered": 4000,
        "line_items": [{"name": "A", "quantity": 10000}],
        "shipments": [{"lines": [{"line_idx": 0, "qty": 4000}]}],
        "packing_info": {"qty_packed": 4000, "lines": [{"line_idx": 0, "packed_qty": 4000}]},
    }
    assert delivery_status_label(o) == "Sebagian Terkirim"


def test_full_terkirim_ok():
    o = {
        "delivery_status": "Terkirim",
        "current_stage": 14,
        "qty_delivered": 100,
        "line_items": [{"name": "A", "quantity": 100}],
        "shipments": [{"lines": [{"line_idx": 0, "qty": 100}]}],
        "packing_info": {"qty_packed": 100, "lines": [{"line_idx": 0, "packed_qty": 100}]},
    }
    assert delivery_status_label(o) == "Terkirim"


def test_shipment_empty():
    out = build_shipments_today([], date(2026, 8, 25))
    assert out["total"] == 0


def test_shipment_today_uses_operational_date_not_uploaded_at():
    today = date(2026, 8, 28)
    later = date(2026, 8, 30)
    o = {
        "id": "s3", "po_number": "PO-DATE", "cancelled": False,
        "current_stage": 12,
        "delivery_note_created_at": "2026-08-30T12:00:00+07:00",
        "surat_jalan_files": [{"id": "f1", "uploaded_at": "2026-08-28T09:00:00+07:00"}],
        "shipments": [{"created_at": "2026-08-28T09:00:00+07:00", "number": "SJ-1"}],
    }
    assert not is_shipment_today(o, today)
    assert is_shipment_today(o, later)


def test_parse_operational_date_key():
    from services.dashboard_today import operational_iso, parse_operational_date_key
    assert parse_operational_date_key("2026-08-30") == "2026-08-30"
    assert parse_operational_date_key("2026-02-31") is None
    assert parse_operational_date_key("") is None
    assert parse_operational_date_key("30/08/2026") is None
    iso = operational_iso("2026-08-30")
    assert iso.startswith("2026-08-30T12:00:00")
    assert "+07:00" in iso


def test_resolve_sj_operational_date_empty_is_today_wib():
    from datetime import datetime, timezone
    from services.dashboard_today import resolve_sj_operational_date
    now = datetime(2026, 8, 27, 17, 30, tzinfo=timezone.utc)  # 2026-08-28 00:30 WIB
    assert resolve_sj_operational_date({}, None, now) == "2026-08-28"
    assert resolve_sj_operational_date({}, "", now) == "2026-08-28"


def test_resolve_sj_operational_date_selected_wins():
    from services.dashboard_today import resolve_sj_operational_date
    o = {
        "delivery_note_created_at": "2026-08-28T12:00:00+07:00",
        "scheduled_delivery_date": "2026-08-28",
    }
    assert resolve_sj_operational_date(o, "2026-08-30") == "2026-08-30"


def test_resolve_sj_operational_date_keeps_existing_on_empty():
    from datetime import datetime, timezone
    from services.dashboard_today import resolve_sj_operational_date
    now = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
    o = {"delivery_note_created_at": "2026-08-30T12:00:00+07:00"}
    assert resolve_sj_operational_date(o, "", now) == "2026-08-30"
    o2 = {"scheduled_delivery_date": "2026-08-31"}
    assert resolve_sj_operational_date(o2, None, now) == "2026-08-31"


def test_resolve_sj_operational_date_rejects_invalid():
    from services.dashboard_today import resolve_sj_operational_date
    try:
        resolve_sj_operational_date({}, "2026-02-31")
        assert False, "expected invalid date"
    except ValueError as e:
        assert "tidak valid" in str(e)


def test_work_query_not_full_scan():
    q = work_today_mongo_query(date(2026, 8, 25))
    assert q["cancelled"] == {"$ne": True}
    assert "$or" in q
    assert {"work_date": "2026-08-25"} in q["$or"]
