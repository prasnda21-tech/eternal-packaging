"""Focused tests for PO-grain active late tracking (Owner final rules)."""
from __future__ import annotations

from datetime import datetime, timezone

from services.po_late_tracking import (
    CAT_KRITIS,
    CAT_LEWAT_1,
    CAT_TERLAMBAT,
    branch_mongo_scope,
    build_late_row,
    build_late_rows,
    calendar_days_between,
    classify_active_late,
    late_category,
    mongo_match,
    paginate_rows,
    summary_payload,
)


TODAY = "2026-10-29"


def _base(**extra):
    order = {
        "id": "o1",
        "po_number": "PO-1",
        "order_date": "2026-10-01",
        "customer_name": "Toko A",
        "branch_id": "br-a",
        "branch_name": "Yogyakarta",
        "current_stage": 8,
        "delivery_deadline_manual": "2026-10-25",
        "line_items": [{"name": "Box", "quantity": 100, "production_process": "Cetak"}],
        "qty_delivered": 0,
        "cancelled": False,
        "buffer_stock": False,
    }
    order.update(extra)
    return order


def test_calendar_days_and_categories():
    assert calendar_days_between("2026-10-25", "2026-10-29") == 4
    assert late_category(1) == CAT_LEWAT_1
    assert late_category(2) == CAT_TERLAMBAT
    assert late_category(3) == CAT_TERLAMBAT
    assert late_category(4) == CAT_KRITIS
    assert late_category(0) is None


def test_po_without_sj_past_effective_date_is_late():
    row = build_late_row(_base(shipments=[]), today=TODAY)
    assert row is not None
    assert row["days_late"] == 4
    assert row["category"] == CAT_KRITIS
    assert row["umur_po_days"] == 28


def test_today_due_not_late():
    assert build_late_row(_base(delivery_deadline_manual=TODAY), today=TODAY) is None
    fact = classify_active_late(_base(delivery_deadline_manual=TODAY), today=TODAY)
    assert fact["reason"] == "not_yet_late"


def test_categories_1_2_3_4():
    assert build_late_row(_base(delivery_deadline_manual="2026-10-28"), today=TODAY)["category"] == CAT_LEWAT_1
    assert build_late_row(_base(delivery_deadline_manual="2026-10-27"), today=TODAY)["category"] == CAT_TERLAMBAT
    assert build_late_row(_base(delivery_deadline_manual="2026-10-26"), today=TODAY)["category"] == CAT_TERLAMBAT
    assert build_late_row(_base(delivery_deadline_manual="2026-10-25"), today=TODAY)["category"] == CAT_KRITIS


def test_partial_still_late_multi_sj_one_row():
    order = _base(
        qty_delivered=40,
        shipments=[
            {"id": "s1", "number": "SJ-1", "delivery_date": "2026-10-20"},
            {"id": "s2", "number": "SJ-2", "delivery_date": "2026-10-22"},
        ],
    )
    rows = build_late_rows([order], today=TODAY)
    assert len(rows) == 1
    assert rows[0]["qty_remaining"] == 60


def test_fully_delivered_and_late_completion_excluded():
    done = _base(qty_delivered=100, delivery_status="Terkirim", current_stage=14)
    assert build_late_row(done, today=TODAY) is None
    # Finished late still excluded from active list
    done_late = _base(
        qty_delivered=100,
        delivery_status="Terkirim",
        current_stage=14,
        delivery_deadline_manual="2026-10-01",
    )
    assert build_late_row(done_late, today=TODAY) is None


def test_cancelled_and_buffer_excluded():
    assert build_late_row(_base(cancelled=True), today=TODAY) is None
    assert build_late_row(_base(buffer_stock=True), today=TODAY) is None


def test_missing_or_invalid_date_not_silently_late():
    bare = _base()
    bare.pop("delivery_deadline_manual")
    bare.pop("scheduled_delivery_date", None)
    fact = classify_active_late(bare, today=TODAY)
    assert fact["include"] is False
    assert fact["reason"] in {"missing_effective_date", "qty_basis_unreliable"} or fact["reason"].startswith("missing")


def test_reschedule_moves_effective_date():
    order = _base(
        delivery_deadline_manual=None,
        scheduled_delivery_date="2026-10-27",
        delivery_reschedules=[{"status": "scheduled", "new_date": "2026-10-27"}],
    )
    row = build_late_row(order, today=TODAY)
    assert row["days_late"] == 2
    # Push effective date to future → out of late
    future = _base(
        delivery_deadline_manual=None,
        scheduled_delivery_date="2026-11-05",
        delivery_reschedules=[{"status": "scheduled", "new_date": "2026-11-05"}],
    )
    assert build_late_row(future, today=TODAY) is None


def test_wib_order_date_midnight_utc_no_off_by_one():
    # 2026-10-01T17:00:00Z == 2026-10-02 00:00 WIB
    order = _base(order_date="2026-10-01T17:00:00+00:00", delivery_deadline_manual="2026-10-28")
    row = build_late_row(order, today=TODAY)
    assert row["order_date"] == "2026-10-02"
    assert row["umur_po_days"] == 27


def test_sort_default_most_late_first_and_pagination_total():
    orders = [
        _base(id="a", po_number="PO-A", delivery_deadline_manual="2026-10-28"),  # 1
        _base(id="b", po_number="PO-B", delivery_deadline_manual="2026-10-25"),  # 4
        _base(id="c", po_number="PO-C", delivery_deadline_manual="2026-10-27"),  # 2
    ]
    rows = build_late_rows(orders, today=TODAY)
    assert [r["id"] for r in rows] == ["b", "c", "a"]
    page, meta = paginate_rows(rows, page=1, page_size=2)
    assert meta["total"] == 3
    assert len(page) == 2
    page2, meta2 = paginate_rows(rows, page=2, page_size=2)
    assert meta2["total"] == 3
    assert len(page2) == 1


def test_filter_category_and_summary_counts():
    orders = [
        _base(id="1", delivery_deadline_manual="2026-10-28"),
        _base(id="2", delivery_deadline_manual="2026-10-27"),
        _base(id="3", delivery_deadline_manual="2026-10-25"),
    ]
    rows = build_late_rows(orders, today=TODAY)
    summary = summary_payload(rows, today=TODAY)
    assert summary["total"] == 3
    assert summary["counts"][CAT_LEWAT_1] == 1
    assert summary["counts"][CAT_TERLAMBAT] == 1
    assert summary["counts"][CAT_KRITIS] == 1
    only_krit = build_late_rows(orders, today=TODAY, category=CAT_KRITIS)
    assert len(only_krit) == 1
    assert only_krit[0]["id"] == "3"


def test_unreliable_qty_not_classified_late_or_done():
    order = {
        "id": "x",
        "po_number": "PO-X",
        "order_date": "2026-10-01",
        "delivery_deadline_manual": "2026-10-20",
        "line_items": [],
        "qty_delivered": None,
        "cancelled": False,
        "buffer_stock": False,
    }
    fact = classify_active_late(order, today=TODAY)
    assert fact["include"] is False
    assert fact["reason"] == "qty_basis_unreliable"


def test_branch_scope_direktur_holding_global_vs_cabang_assigned():
    """Does not rely on local-admin fallback — uses explicit access scopes."""
    assert branch_mongo_scope({"scope": "ALL", "branch_ids": None}) == {}
    assert branch_mongo_scope(
        {"scope": "ALL"}, requested_branch_id="br-smg",
    ) == {"branch_id": "br-smg"}
    assigned = branch_mongo_scope({"scope": "ASSIGNED", "branch_ids": ["br-ygy", "br-smg"]})
    assert assigned == {"branch_id": {"$in": ["br-ygy", "br-smg"]}}
    empty = branch_mongo_scope({"scope": "ASSIGNED", "branch_ids": []})
    assert empty == {"branch_id": {"$in": []}}
    match = mongo_match(
        sales_scope={},
        branch_scope=branch_mongo_scope({"scope": "ASSIGNED", "branch_ids": ["br-ygy"]}),
    )
    assert match["cancelled"] == {"$ne": True}
    assert match["buffer_stock"] == {"$ne": True}
    assert match["branch_id"] == {"$in": ["br-ygy"]}


def test_active_total_ignores_search_filter_universe():
    orders = [
        _base(id="1", po_number="PO-A", customer_name="Alpha", delivery_deadline_manual="2026-10-28"),
        _base(id="2", po_number="PO-B", customer_name="Beta", delivery_deadline_manual="2026-10-25"),
    ]
    universe = build_late_rows(orders, today=TODAY)
    filtered = build_late_rows(orders, today=TODAY, q="Beta")
    assert len(universe) == 2
    assert len(filtered) == 1
    assert summary_payload(universe, today=TODAY)["total"] == 2
    assert summary_payload(filtered, today=TODAY)["total"] == 1


def test_branch_options_from_scoped_orders_only():
    from services.po_late_tracking import branch_options_from_orders

    opts = branch_options_from_orders([
        {**_base(id="1"), "branch_id": "br-ygy", "branch_name": "Yogyakarta"},
        {**_base(id="2"), "branch_id": "br-smg", "branch_name": "Semarang"},
        {**_base(id="3"), "branch_id": "br-ygy", "branch_name": "Yogyakarta"},
        {**_base(id="4"), "branch_id": "", "branch_name": "Ghost"},
    ])
    assert {row["id"]: row["name"] for row in opts} == {
        "br-ygy": "Yogyakarta",
        "br-smg": "Semarang",
    }
    assert [row["id"] for row in opts] == ["br-smg", "br-ygy"]
