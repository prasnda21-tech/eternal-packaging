"""Rencana Jadwal Kirim — eligibility, filters, qty, late boundary (no live API)."""
from __future__ import annotations

import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.delivery_plan import build_plan_rows, mongo_match, paginate_rows, plan_row
from services.tanggal_pengiriman import calendar_date_to_wib_iso


def _po(**extra):
    base = {
        "id": "ord-plan-1",
        "po_number": "PO/PLAN/001",
        "customer": "PT Rencana",
        "customer_name": "PT Rencana",
        "branch_name": "Yogyakarta",
        "cancelled": False,
        "buffer_stock": False,
        "current_stage": 8,
        "delivery_status": "Belum Terkirim",
        "qty_delivered": 0,
        "total_quantity": 100,
        "line_items": [{"name": "Karton", "quantity": 100, "production_process": "Cetak"}],
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_approved_at": "2026-10-10T10:00:00+07:00",
        "payment_approved_at": "2026-10-10T12:00:00+07:00",
    }
    base.update(extra)
    return base


def test_automatic_date_included_without_sj():
    o = _po()  # Sat 10 + 3 working days = Wed 14 (skip Sunday)
    row = plan_row(o, lead_time_days=3, today="2026-10-12")
    assert row is not None
    assert row["tanggal_pengiriman"] == "2026-10-14"
    assert row["tanggal_pengiriman_source"] == "automatic"
    assert row["qty_remaining"] == 100


def test_manual_date_included():
    o = _po(
        delivery_deadline_manual=calendar_date_to_wib_iso("2026-10-20"),
        scheduled_delivery_date="2026-10-20",
    )
    row = plan_row(o, lead_time_days=3, today="2026-10-12")
    assert row["tanggal_pengiriman"] == "2026-10-20"
    assert row["tanggal_pengiriman_source"] == "manual"


def test_no_date_excluded():
    o = _po(
        design_approved_at=None,
        payment_approved_at=None,
        delivery_deadline_manual=None,
        scheduled_delivery_date=None,
    )
    assert plan_row(o, lead_time_days=3) is None


def test_buffer_stock_included_never_terlambat():
    o = _po(
        buffer_stock=True,
        delivery_deadline_manual=calendar_date_to_wib_iso("2026-10-10"),
        scheduled_delivery_date="2026-10-10",
        qty_delivered=10,
        priority="urgent",
    )
    row = plan_row(o, lead_time_days=3, today="2026-10-12")
    assert row is not None
    assert row["buffer_stock"] is True
    assert row["priority"] == "urgent"
    assert row["delivery_schedule_status"] == "terjadwal"
    assert row["delivery_schedule_status"] != "terlambat"


def test_cancelled_excluded():
    o = _po(cancelled=True, scheduled_delivery_date="2026-10-20")
    assert plan_row(o, lead_time_days=3) is None


def test_fully_delivered_excluded():
    o = _po(
        scheduled_delivery_date="2026-10-20",
        qty_delivered=100,
        delivery_status="Terkirim",
        current_stage=14,
    )
    assert plan_row(o, lead_time_days=3) is None


def test_partial_delivered_remaining():
    o = _po(
        scheduled_delivery_date="2026-10-20",
        qty_delivered=40,
        delivery_status="Sebagian Terkirim",
    )
    row = plan_row(o, lead_time_days=3, today="2026-10-12")
    assert row is not None
    assert row["qty_total"] == 100
    assert row["qty_delivered"] == 40
    assert row["qty_remaining"] == 60


def test_bypass_both_gates_automatic():
    o = _po(
        design_status="Waiting",
        payment_status="Waiting",
        design_approved_at=None,
        payment_approved_at=None,
        design_bypassed=True,
        design_bypassed_at="2026-10-11T08:00:00+07:00",
        payment_bypassed=True,
        payment_bypassed_at="2026-10-11T09:00:00+07:00",
    )
    row = plan_row(o, lead_time_days=3, today="2026-10-12")
    assert row is not None
    assert row["tanggal_pengiriman"] == "2026-10-14"  # latest 11 + 3
    assert row["tanggal_pengiriman_source"] == "automatic"
    assert row["design_ready"] is True
    assert row["payment_ready"] is True


def test_one_gate_missing_no_auto():
    o = _po(payment_approved_at=None, payment_status="Waiting", payment_bypassed=False)
    assert plan_row(o, lead_time_days=3) is None


def test_filter_source_and_sort_pagination():
    rows = build_plan_rows(
        [
            _po(id="a", po_number="PO/A", scheduled_delivery_date="2026-10-22"),
            _po(
                id="b",
                po_number="PO/B",
                design_approved_at="2026-10-10T10:00:00+07:00",
                payment_approved_at="2026-10-10T12:00:00+07:00",
            ),
            _po(id="c", po_number="PO/C", scheduled_delivery_date="2026-10-15"),
        ],
        lead_time_days=3,
        today="2026-10-12",
        filters={"source": "manual"},
    )
    assert [r["id"] for r in rows] == ["c", "a"]
    page, meta = paginate_rows(rows, page=1, page_size=1)
    assert len(page) == 1
    assert page[0]["id"] == "c"
    assert meta["total"] == 2
    assert meta["counts"]["by_source"]["manual"] == 2


def test_mongo_match_excludes_cancelled_keeps_buffer():
    m = mongo_match()
    assert m["cancelled"] == {"$ne": True}
    assert "buffer_stock" not in m


def test_schedule_status_today_not_terlambat():
    o = _po(scheduled_delivery_date="2026-10-19")
    row = plan_row(o, lead_time_days=3, today="2026-10-19")
    assert row["delivery_schedule_status"] == "hari_ini"
    assert row["delivery_schedule_status"] != "terlambat"


def _import_server():
    from dotenv import load_dotenv

    if (ROOT / ".env").exists():
        load_dotenv(ROOT / ".env")
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "test_database")
    import server

    return server


def test_deadline_status_today_wib_not_overdue():
    server = _import_server()
    from services.dashboard_today import wib_today

    today = wib_today().isoformat()
    o = {
        "id": "today-po",
        "current_stage": 8,
        "delivery_status": "Belum Terkirim",
        "buffer_stock": False,
    }
    iso = calendar_date_to_wib_iso(today)
    assert server._deadline_status_for(iso, o) == "normal"
    assert server._deadline_status_for(iso, o) != "overdue"


def test_deadline_status_yesterday_overdue_tomorrow_not():
    server = _import_server()
    from services.dashboard_today import wib_today

    today = wib_today()
    yday = (today - timedelta(days=1)).isoformat()
    tmrw = (today + timedelta(days=1)).isoformat()
    o = {"id": "x", "current_stage": 8, "delivery_status": "Belum", "buffer_stock": False}
    assert server._deadline_status_for(calendar_date_to_wib_iso(yday), o) == "overdue"
    st_tmrw = server._deadline_status_for(calendar_date_to_wib_iso(tmrw), o)
    assert st_tmrw != "overdue"
    assert st_tmrw in ("normal", "h1", "h6")


def test_deadline_status_preserves_h1_h6_absolute_future():
    server = _import_server()
    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    h6 = {"id": "h6", "current_stage": 5, "delivery_status": "Belum", "buffer_stock": False}
    assert server._deadline_status_for(soon, h6) == "h6"
    assert server._deadline_status_for(later, h6) == "h1"


# ---- Final verification gaps (pagination / qty / estimation / buffer / RBAC) ----


def test_pagination_no_loss_no_dup_stable_tiebreak():
    """Fixtures > page_size: pages partition without loss/dup; same-date sort stable."""
    orders = []
    # 5 POs on same date + 2 later + 1 ineligible (no date) + 1 buffer (now eligible)
    for i in range(5):
        orders.append(_po(
            id=f"same-{i}",
            po_number=f"PO/SAME/{i}",
            scheduled_delivery_date="2026-10-20",
        ))
    orders.append(_po(id="late-a", po_number="PO/LATE/A", scheduled_delivery_date="2026-10-25"))
    orders.append(_po(id="late-b", po_number="PO/LATE/B", scheduled_delivery_date="2026-10-25"))
    orders.append(_po(
        id="nodate",
        po_number="PO/NODATE",
        design_approved_at=None,
        payment_approved_at=None,
        scheduled_delivery_date=None,
        delivery_deadline_manual=None,
    ))
    orders.append(_po(
        id="buf",
        po_number="PO/BUF",
        buffer_stock=True,
        scheduled_delivery_date="2026-10-20",
    ))
    filtered = build_plan_rows(orders, lead_time_days=3, today="2026-10-12", filters={})
    assert len(filtered) == 8  # buffer included; nodate still excluded
    assert all(r["id"] != "nodate" for r in filtered)
    assert any(r["id"] == "buf" for r in filtered)
    # Stable tie-break: same date ordered by po_number then id (BUF before SAME/*)
    same = [r for r in filtered if r["tanggal_pengiriman"] == "2026-10-20"]
    assert [r["po_number"] for r in same] == ["PO/BUF"] + [f"PO/SAME/{i}" for i in range(5)]

    page1, meta1 = paginate_rows(filtered, page=1, page_size=3)
    page2, meta2 = paginate_rows(filtered, page=2, page_size=3)
    page3, meta3 = paginate_rows(filtered, page=3, page_size=3)
    assert meta1["total"] == meta2["total"] == meta3["total"] == 8
    assert meta1["total_pages"] == 3
    assert len(page1) == 3 and len(page2) == 3 and len(page3) == 2
    ids = [r["id"] for r in page1 + page2 + page3]
    assert len(ids) == len(set(ids)) == 8
    assert set(ids) == {r["id"] for r in filtered}
    # last page clamp
    page_over, meta_over = paginate_rows(filtered, page=99, page_size=3)
    assert meta_over["page"] == 3
    assert [r["id"] for r in page_over] == [r["id"] for r in page3]


def test_pagination_after_combined_filters():
    orders = [
        _po(id="m1", po_number="PO/ALPHA", scheduled_delivery_date="2026-10-18", customer_name="Alpha Co"),
        _po(id="m2", po_number="PO/BETA", scheduled_delivery_date="2026-10-19", customer_name="Beta Co"),
        _po(id="a1", po_number="PO/GAMMA", customer_name="Gamma Co"),  # automatic 10+3=13
        _po(id="m3", po_number="PO/ALPHA2", scheduled_delivery_date="2026-10-21", customer_name="Alpha Co"),
    ]
    rows = build_plan_rows(
        orders,
        lead_time_days=3,
        today="2026-10-12",
        filters={"q": "alpha", "source": "manual", "date_from": "2026-10-18", "date_to": "2026-10-22"},
    )
    assert [r["id"] for r in rows] == ["m1", "m3"]
    page, meta = paginate_rows(rows, page=1, page_size=1)
    assert meta["total"] == 2
    assert page[0]["id"] == "m1"
    page2, _ = paginate_rows(rows, page=2, page_size=1)
    assert page2[0]["id"] == "m3"


def test_qty_ignores_sj_line_sum_uses_canonical_qty_delivered():
    """Multiple / cancelled SJ lines must not inflate delivered beyond persisted qty_delivered."""
    from services.delivery_history import terkirim_qty_summary

    o = _po(
        scheduled_delivery_date="2026-10-20",
        qty_delivered=30,
        delivery_status="Sebagian Terkirim",
        shipments=[
            {
                "id": "sj-1",
                "number": "SJ1",
                "delivery_date": "2026-10-18",
                "lines": [{"line_id": "l1", "qty": 80}],
            },
            {
                "id": "sj-2",
                "number": "SJ2",
                "delivery_date": "2026-10-19",
                "cancelled": True,
                "lines": [{"line_id": "l1", "qty": 50}],
            },
            {
                "id": "sj-3",
                "number": "SJ3",
                "delivery_date": "2026-10-20",
                "status": "superseded",
                "lines": [{"line_id": "l1", "qty": 40}],
            },
        ],
    )
    summary = terkirim_qty_summary(o)
    assert summary["total_po_qty"] == 100
    assert summary["qty_delivered"] == 30  # not 80+50+40
    assert summary["qty_remaining"] == 70
    row = plan_row(o, lead_time_days=3, today="2026-10-12")
    assert row["qty_total"] == 100
    assert row["qty_delivered"] == 30
    assert row["qty_remaining"] == 70


def test_estimation_bypass_matrix_and_lead_time_and_manual_reset():
    from services.tanggal_pengiriman import (
        apply_manual_tanggal_pengiriman,
        clear_manual_tanggal_pengiriman,
        resolve_tanggal_pengiriman,
    )

    # Design bypass + payment approved
    o1 = _po(
        design_status="Waiting",
        design_approved_at=None,
        design_bypassed=True,
        design_bypassed_at="2026-10-10T08:00:00+07:00",
        payment_approved_at="2026-10-11T09:00:00+07:00",
    )
    assert plan_row(o1, lead_time_days=3)["tanggal_pengiriman"] == "2026-10-14"

    # Design approved + payment bypass
    o2 = _po(
        payment_status="Waiting",
        payment_approved_at=None,
        payment_bypassed=True,
        payment_bypassed_at="2026-10-12T09:00:00+07:00",
        design_approved_at="2026-10-10T10:00:00+07:00",
    )
    assert plan_row(o2, lead_time_days=3)["tanggal_pengiriman"] == "2026-10-15"

    # Design pending only
    o3 = _po(design_approved_at=None, design_status="Waiting", design_bypassed=False)
    assert plan_row(o3, lead_time_days=3) is None

    # Manual override then reset → automatic
    o4 = _po(
        design_approved_at="2026-10-10T10:00:00+07:00",
        payment_approved_at="2026-10-10T12:00:00+07:00",
    )
    apply_manual_tanggal_pengiriman(o4, "2026-10-30")
    assert resolve_tanggal_pengiriman(o4, 3)["source"] == "manual"
    assert plan_row(o4, lead_time_days=3)["tanggal_pengiriman"] == "2026-10-30"
    clear_manual_tanggal_pengiriman(o4)
    snap = resolve_tanggal_pengiriman(o4, 3)
    assert snap["source"] == "automatic"
    assert snap["tanggal_pengiriman"] == "2026-10-14"
    assert plan_row(o4, lead_time_days=3)["tanggal_pengiriman"] == "2026-10-14"

    # sla_working_days change on GET (no DB write — pure resolve)
    assert resolve_tanggal_pengiriman(o4, 1)["tanggal_pengiriman"] == "2026-10-12"
    assert resolve_tanggal_pengiriman(o4, 5)["tanggal_pengiriman"] == "2026-10-16"
    assert plan_row(o4, lead_time_days=5)["tanggal_pengiriman"] == "2026-10-16"


def test_buffer_included_in_rows_not_terlambat():
    """Owner: Buffer Stock stays visible in Rencana Jadwal Kirim, never terlambat."""
    orders = [
        _po(id="ok", po_number="PO/OK", scheduled_delivery_date="2026-10-20", customer_name="Keep Me"),
        _po(
            id="buf1",
            po_number="PO/BUFFER/HIT",
            buffer_stock=True,
            scheduled_delivery_date="2026-10-10",
            customer_name="Keep Me Buffer",
            design_approved_at="2026-10-01T00:00:00+07:00",
            payment_approved_at="2026-10-01T00:00:00+07:00",
            priority="urgent",
        ),
    ]
    rows = build_plan_rows(orders, lead_time_days=3, today="2026-10-12")
    assert {r["id"] for r in rows} == {"ok", "buf1"}
    buf = next(r for r in rows if r["id"] == "buf1")
    assert buf["buffer_stock"] is True
    assert buf["priority"] == "urgent"
    assert buf["delivery_schedule_status"] != "terlambat"
    assert buf["delivery_schedule_status"] == "terjadwal"
    _, meta = paginate_rows(rows, page=1, page_size=50)
    assert meta["total"] == 2
    assert meta["counts"]["total"] == 2
    assert meta["counts"]["by_status"].get("terlambat", 0) == 0
    searched = build_plan_rows(orders, lead_time_days=3, filters={"q": "BUFFER"}, today="2026-10-12")
    assert [r["id"] for r in searched] == ["buf1"]
    m = mongo_match()
    assert "buffer_stock" not in m


def test_rbac_endpoint_wiring_scope_before_paginate():
    """Contract: perm gate + sales_owner_match merged into mongo_match before build/paginate."""
    import inspect
    from services.sales_user_mapping import sales_owner_match

    server = _import_server()
    src = inspect.getsource(server.list_delivery_plan)
    assert "_require_any_perm" in src
    assert "delivery.schedule.view" in src
    assert "sales_owner_match" in src
    assert "_delivery_plan_mongo_match" in src or "mongo_match" in src
    assert "_build_delivery_plan_rows" in src or "build_plan_rows" in src
    assert "_paginate_delivery_plan_rows" in src or "paginate_rows" in src
    # order: match → build → paginate (eligibility+filter before page slice)
    assert src.index("mongo_match") < src.index("build_delivery_plan_rows") or src.index(
        "_delivery_plan_mongo_match"
    ) < src.index("_build_delivery_plan_rows")
    assert src.index("_build_delivery_plan_rows") < src.index("_paginate_delivery_plan_rows")

    scope = sales_owner_match({"role": "sales_eternal", "id": "sales-u-1"})
    assert scope == {"sales_user_id": "sales-u-1"}
    merged = mongo_match(scope)
    assert merged["sales_user_id"] == "sales-u-1"
    assert "buffer_stock" not in merged
    assert merged["cancelled"] == {"$ne": True}
    # empty scope for non-sales does not invent filter
    assert sales_owner_match({"role": "admin", "id": "a1"}) == {}


def test_endpoint_is_get_only_no_write_helpers_in_handler():
    import inspect

    server = _import_server()
    src = inspect.getsource(server.list_delivery_plan)
    assert "db.orders.update" not in src
    assert "db.orders.insert" not in src
    assert "_persist_order" not in src
    assert "await db.orders.find" in src


def test_totals_over_full_filtered_set_not_page():
    # qty_total comes from canonical line_items via terkirim_qty_summary (_po default 100/line).
    rows = build_plan_rows(
        [
            _po(id="a", po_number="PO/A", scheduled_delivery_date="2026-10-20", qty_delivered=10),
            _po(id="b", po_number="PO/B", scheduled_delivery_date="2026-10-21", qty_delivered=0),
            _po(id="c", po_number="PO/C", scheduled_delivery_date="2026-10-22", qty_delivered=5),
        ],
        lead_time_days=3,
        today="2026-10-12",
    )
    page, meta = paginate_rows(rows, page=1, page_size=1)
    assert len(page) == 1
    assert meta["total"] == 3
    assert meta["totals"]["total_po"] == 3
    assert meta["totals"]["qty_total"] == 300
    assert meta["totals"]["qty_delivered"] == 15
    assert meta["totals"]["qty_remaining"] == 285


def test_header_filter_and_sort_before_pagination():
    rows = build_plan_rows(
        [
            _po(id="a", po_number="PO/A", customer_name="Alpha", scheduled_delivery_date="2026-10-22"),
            _po(id="b", po_number="PO/B", customer_name="Beta", scheduled_delivery_date="2026-10-20"),
            _po(id="c", po_number="PO/C", customer_name="Alpha Corp", scheduled_delivery_date="2026-10-21"),
        ],
        lead_time_days=3,
        today="2026-10-12",
        sort_by="tanggal_pengiriman",
        sort_dir="asc",
        hf=["customer_name:contains:Alpha"],
    )
    assert [r["id"] for r in rows] == ["c", "a"]
    page, meta = paginate_rows(rows, page=1, page_size=1)
    assert page[0]["id"] == "c"
    assert meta["total"] == 2
    assert meta["totals"]["total_po"] == 2


def test_plan_row_notes_and_delivery_zone_from_same_po():
    o = _po(
        id="nz-1",
        notes="SABLON WARNA MERAH",
        delivery_zone="JT",
        scheduled_delivery_date="2026-10-20",
        line_items=[{
            "name": "Karton",
            "quantity": 100,
            "production_process": "Cetak",
            "notes": "line-only-not-used",
        }],
    )
    row = plan_row(o, lead_time_days=3, today="2026-10-12")
    assert row["id"] == "nz-1"
    assert row["notes"] == "SABLON WARNA MERAH"
    assert row["delivery_zone"] == "JT"
    assert "line-only" not in row["notes"]


def test_plan_row_notes_zone_empty_legacy():
    o = _po(id="legacy", scheduled_delivery_date="2026-10-20", notes=None, delivery_zone="")
    row = plan_row(o, lead_time_days=3, today="2026-10-12")
    assert row["notes"] == ""
    assert row["delivery_zone"] == ""


def test_same_customer_name_independent_notes_zone():
    a = _po(
        id="a1",
        po_number="PO/A1",
        customer_name="Same Co",
        notes="Catatan A",
        delivery_zone="JS",
        scheduled_delivery_date="2026-10-20",
    )
    b = _po(
        id="b1",
        po_number="PO/B1",
        customer_name="Same Co",
        notes="Catatan B",
        delivery_zone="JT",
        scheduled_delivery_date="2026-10-21",
    )
    rows = build_plan_rows([a, b], lead_time_days=3, today="2026-10-12")
    by_id = {r["id"]: r for r in rows}
    assert by_id["a1"]["notes"] == "Catatan A" and by_id["a1"]["delivery_zone"] == "JS"
    assert by_id["b1"]["notes"] == "Catatan B" and by_id["b1"]["delivery_zone"] == "JT"


def test_date_range_inclusive_cross_month_year_totals():
    orders = [
        _po(id="d12", po_number="PO/12", scheduled_delivery_date="2026-12-12"),
        _po(id="d13", po_number="PO/13", scheduled_delivery_date="2026-12-13"),
        _po(id="d14", po_number="PO/14", scheduled_delivery_date="2026-12-14"),
        _po(id="d15", po_number="PO/15", scheduled_delivery_date="2026-12-15"),
        _po(id="d16", po_number="PO/16", scheduled_delivery_date="2026-12-16"),
        _po(id="y27", po_number="PO/27", scheduled_delivery_date="2027-01-02"),
    ]
    rows = build_plan_rows(
        orders,
        lead_time_days=3,
        today="2026-12-01",
        filters={"date_from": "2026-12-12", "date_to": "2026-12-15"},
    )
    assert [r["id"] for r in rows] == ["d12", "d13", "d14", "d15"]
    page, meta = paginate_rows(rows, page=1, page_size=2)
    assert len(page) == 2
    assert meta["total"] == 4
    assert meta["totals"]["total_po"] == 4

    cross = build_plan_rows(
        orders,
        lead_time_days=3,
        today="2026-12-01",
        filters={"date_from": "2026-12-15", "date_to": "2027-01-02"},
    )
    assert [r["id"] for r in cross] == ["d15", "d16", "y27"]


def test_projection_includes_notes_and_zone():
    from services.delivery_plan import PLAN_PROJECTION

    assert PLAN_PROJECTION.get("notes") == 1
    assert PLAN_PROJECTION.get("delivery_zone") == 1
