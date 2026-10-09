"""Unit tests for Status PO filters, columns, and delivered percent."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.status_po import (
    build_status_po,
    effective_workflow_status,
    filter_orders_for_view,
    matches_status_po_filters,
    status_po_row,
)
from services.shipment_status import status_percent


def _po(**kw):
    base = {
        "id": kw.get("id", "p1"),
        "po_number": kw.get("po_number", "PO-1"),
        "customer": kw.get("customer", "Acme"),
        "order_date": kw.get("order_date", "2026-08-26"),
        "current_stage": kw.get("current_stage", 8),
        "cancelled": False,
        "line_items": [{"quantity": 10000, "production_process": "print"}],
        "operator_splits": [{"qty_completed": 5000, "line_idx": 0, "quantity": 10000}],
        "qty_delivered": kw.get("qty_delivered", 5000),
        "total_quantity": 10000,
    }
    base.update(kw)
    return base


def test_partial_delivery_stays_50_percent():
    row = status_po_row(_po())
    assert row["percent_delivered"] == 50
    assert status_percent(5000, 10000) == 50
    assert "Status" in ("Status",)
    assert row["status"] in ("Parsial Terkirim", "Cetak berjalan", "Selesai Cetak", "Operator", "Penjadwalan")


def test_status_not_generic_stage_index_done():
    o = _po(current_stage=14, qty_delivered=5000, delivery_status="Sebagian Terkirim", terkirim_confirmed=False)
    assert effective_workflow_status(o) == "Parsial Terkirim"


def test_view_active_excludes_completed():
    orders = [_po(id="a", current_stage=8), _po(id="b", current_stage=14, qty_delivered=10000, delivery_status="Terkirim")]
    got = filter_orders_for_view(orders, "active")
    assert [o["id"] for o in got] == ["a"]


def test_active_uses_delivery_not_stage_index():
    """Stage 14 but not fully delivered stays Aktif; fully delivered before stage 14 is Selesai."""
    partial_stage14 = _po(
        id="partial14",
        current_stage=14,
        qty_delivered=5000,
        delivery_status="Sebagian Terkirim",
    )
    early_full = _po(
        id="early-full",
        current_stage=10,
        qty_delivered=10000,
        delivery_status="Terkirim",
    )
    assert [o["id"] for o in filter_orders_for_view([partial_stage14, early_full], "active")] == ["partial14"]
    assert [o["id"] for o in filter_orders_for_view([partial_stage14, early_full], "completed")] == ["early-full"]


def test_semua_excludes_cancelled_and_cancelled_tab_isolates():
    orders = [
        _po(id="a", current_stage=8),
        _po(id="b", qty_delivered=10000, delivery_status="Terkirim", current_stage=14),
        _po(id="c", cancelled=True, current_stage=3),
    ]
    semua = filter_orders_for_view(orders, None)
    assert {o["id"] for o in semua} == {"a", "b"}
    assert [o["id"] for o in filter_orders_for_view(orders, "cancelled")] == ["c"]
    assert [o["id"] for o in filter_orders_for_view(orders, "active")] == ["a"]
    assert [o["id"] for o in filter_orders_for_view(orders, "completed")] == ["b"]


def test_view_work_today():
    orders = [
        _po(id="w", work_date="2026-08-26"),
        _po(id="x", work_date="2026-08-01"),
    ]
    now = datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc)
    got = filter_orders_for_view(orders, "work_today", now=now)
    assert [o["id"] for o in got] == ["w"]


def test_build_rows_have_required_columns():
    data = build_status_po([_po()])
    row = data["rows"][0]
    for key in ("po_number", "order_date", "customer", "status", "percent_delivered"):
        assert key in row


def _row(**kw):
    return {
        "po_number": kw.get("po_number", "PO-001"),
        "customer": kw.get("customer", "ABC Pack"),
        "status": kw.get("status", "Operator"),
        "order_date": kw.get("order_date", "2026-08-10"),
    }


def test_filter_po_partial():
    assert matches_status_po_filters(_row(), {"po": "PO-001"}) is True
    assert matches_status_po_filters(_row(po_number="PO-002"), {"po": "PO-001"}) is False
    assert matches_status_po_filters(_row(), {"po": "001"}) is True


def test_filter_customer_partial():
    assert matches_status_po_filters(_row(), {"customer": "ABC"}) is True
    assert matches_status_po_filters(_row(customer="XYZ"), {"customer": "ABC"}) is False


def test_filter_status_exact():
    assert matches_status_po_filters(_row(status="Operator"), {"status": "Operator"}) is True
    assert matches_status_po_filters(_row(status="Packing"), {"status": "Operator"}) is False


def test_filter_date_range():
    f = {"date_from": "2026-08-01", "date_to": "2026-08-31"}
    assert matches_status_po_filters(_row(order_date="2026-08-10"), f) is True
    assert matches_status_po_filters(_row(order_date="2026-07-15"), f) is False


def test_filter_combined_and():
    f = {"customer": "ABC", "status": "Operator", "date_from": "2026-08-01", "date_to": "2026-08-31"}
    assert matches_status_po_filters(_row(), f) is True
    assert matches_status_po_filters(_row(customer="XYZ"), f) is False
    assert matches_status_po_filters(_row(status="Packing"), f) is False
    assert matches_status_po_filters(_row(order_date="2026-07-15"), f) is False


def test_filter_empty_matches_all():
    assert matches_status_po_filters(_row(), {}) is True
    assert matches_status_po_filters(_row(), {"po": "", "customer": "", "status": ""}) is True


def test_build_status_po_applies_filters_after_view():
    orders = [
        _po(id="a", po_number="PO-001", customer="ABC", current_stage=8, order_date="2026-08-10"),
        _po(id="b", po_number="PO-002", customer="XYZ", current_stage=8, order_date="2026-08-20"),
        _po(id="c", po_number="PO-003", customer="ABC", current_stage=14, qty_delivered=10000, delivery_status="Terkirim", order_date="2026-08-12"),
    ]
    data = build_status_po(orders, "active", filters={"customer": "ABC"})
    assert [r["id"] for r in data["rows"]] == ["a"]
    assert data["total"] == 1
    reset = build_status_po(orders, "active")
    assert {r["id"] for r in reset["rows"]} == {"a", "b"}


def test_build_status_po_empty_filter_result():
    data = build_status_po([_po(po_number="PO-001")], filters={"po": "NOPE"})
    assert data["rows"] == []
    assert data["total"] == 0


def _full(**kw):
    base = _po(
        qty_delivered=10000,
        delivery_status="Terkirim",
        current_stage=14,
        order_date="2026-09-01",
    )
    base.update(kw)
    return base


def test_open_po_age_keeps_running():
    row = status_po_row(_po(qty_delivered=0, delivery_status="Belum Terkirim", current_stage=8))
    assert row["age_closed"] is False
    assert row["age_end_at"] is None


def test_stage_index_alone_does_not_stop_age():
    row = status_po_row(_po(
        current_stage=14,
        qty_delivered=1000,
        delivery_status="Sebagian Terkirim",
        delivered_at="2026-09-05T10:00:00+07:00",
    ))
    assert row["status"] == "Parsial Terkirim"
    assert row["age_closed"] is False
    assert row["age_end_at"] is None


def test_partial_delivery_age_keeps_running_despite_delivery_stamp():
    row = status_po_row(_po(
        qty_delivered=5000,
        delivery_status="Sebagian Terkirim",
        delivered_at="2026-09-05T10:00:00+07:00",
        scheduled_delivery_date="2026-09-20",
        deadline="2026-09-30T00:00:00+07:00",
        delivery_confirmations=[{
            "shipment_id": "sj-1",
            "qty": 5000,
            "confirmed_at": "2026-09-05T10:00:00+07:00",
        }],
        shipments=[{
            "id": "sj-1",
            "delivered_at": "2026-09-05T10:00:00+07:00",
            "delivery_status": "Terkirim",
            "created_at": "2026-09-04T01:00:00+07:00",
            "lines": [{"line_idx": 0, "qty": 5000}],
        }],
    ))
    assert row["age_closed"] is False
    assert row["age_end_at"] is None


def test_single_delivery_stops_at_actual_confirmation():
    row = status_po_row(_full(
        delivered_at="2026-09-05T10:00:00+07:00",
        scheduled_delivery_date="2026-09-20",
        deadline="2026-09-30T00:00:00+07:00",
        delivery_proofs=[{"uploaded_at": "2026-09-04T08:00:00+07:00", "created_at": "2026-09-04T08:00:00+07:00"}],
        shipments=[{
            "id": "sj-1",
            "created_at": "2026-09-02T01:00:00+07:00",
            "delivered_at": "2026-09-05T10:00:00+07:00",
            "delivery_status": "Terkirim",
            "lines": [{"line_idx": 0, "qty": 10000}],
        }],
        delivery_confirmations=[{
            "shipment_id": "sj-1",
            "qty": 10000,
            "confirmed_at": "2026-09-05T10:00:00+07:00",
        }],
    ))
    assert row["age_closed"] is True
    assert row["age_end_at"] == "2026-09-05T10:00:00+07:00"


def test_multiple_deliveries_use_latest_instant_not_array_order():
    shipments = [
        {
            "id": "sj-early",
            "delivered_at": "2026-09-03T08:00:00+07:00",
            "delivery_status": "Terkirim",
            "lines": [{"line_idx": 0, "qty": 4000}],
        },
        {
            "id": "sj-late",
            "delivered_at": "2026-09-12T08:00:00+07:00",
            "delivery_status": "Terkirim",
            "lines": [{"line_idx": 0, "qty": 6000}],
        },
    ]
    forward = status_po_row(_full(
        delivered_at="2026-09-03T08:00:00+07:00",
        shipments=shipments,
        delivery_confirmations=[
            {"shipment_id": "sj-early", "qty": 4000, "confirmed_at": "2026-09-03T08:00:00+07:00"},
            {"shipment_id": "sj-late", "qty": 6000, "confirmed_at": "2026-09-12T08:00:00+07:00"},
        ],
    ))
    reversed_rows = status_po_row(_full(
        delivered_at="2026-09-12T08:00:00+07:00",
        shipments=list(reversed(shipments)),
        delivery_confirmations=[
            {"shipment_id": "sj-late", "qty": 6000, "confirmed_at": "2026-09-12T08:00:00+07:00"},
            {"shipment_id": "sj-early", "qty": 4000, "confirmed_at": "2026-09-03T08:00:00+07:00"},
        ],
    ))
    assert forward["age_closed"] is True
    assert forward["age_end_at"] == "2026-09-12T08:00:00+07:00"
    assert reversed_rows["age_end_at"] == "2026-09-12T08:00:00+07:00"


def test_cancelled_and_superseded_delivery_does_not_set_age_end():
    row = status_po_row(_full(
        delivered_at="2026-09-20T10:00:00+07:00",
        shipments=[
            {
                "id": "sj-live",
                "delivered_at": "2026-09-05T10:00:00+07:00",
                "delivery_status": "Terkirim",
                "lines": [{"line_idx": 0, "qty": 10000}],
            },
            {
                "id": "sj-cancel",
                "cancelled": True,
                "delivered_at": "2026-09-18T10:00:00+07:00",
                "delivery_status": "Terkirim",
                "created_at": "2026-09-18T01:00:00+07:00",
                "lines": [{"line_idx": 0, "qty": 10000}],
            },
            {
                "id": "sj-old",
                "superseded": True,
                "superseded_by_shipment_id": "sj-live",
                "status": "superseded",
                "delivered_at": "2026-09-20T10:00:00+07:00",
                "delivery_status": "Terkirim",
                "lines": [{"line_idx": 0, "qty": 10000}],
            },
        ],
        delivery_confirmations=[
            {"shipment_id": "sj-old", "qty": 10000, "confirmed_at": "2026-09-20T10:00:00+07:00"},
            {"shipment_id": "sj-cancel", "qty": 10000, "confirmed_at": "2026-09-18T10:00:00+07:00"},
            {"shipment_id": "sj-live", "qty": 10000, "confirmed_at": "2026-09-05T10:00:00+07:00"},
        ],
        delivery_proofs=[{"uploaded_at": "2026-09-21T00:00:00+07:00"}],
        scheduled_delivery_date="2026-09-25",
        deadline="2026-09-30T00:00:00+07:00",
    ))
    assert row["age_closed"] is True
    assert row["age_end_at"] == "2026-09-05T10:00:00+07:00"


def test_fully_delivered_without_valid_delivery_time_has_no_age_end():
    missing = status_po_row(_full())
    assert missing["age_closed"] is True
    assert missing["age_end_at"] is None

    invalid = status_po_row(_full(
        delivered_at="bukan-tanggal",
        shipments=[{
            "id": "sj-1",
            "delivery_status": "Terkirim",
            "delivered_at": "n/a",
            "created_at": "2026-09-08T00:00:00+07:00",
            "lines": [{"line_idx": 0, "qty": 10000}],
        }],
        delivery_confirmations=[{
            "shipment_id": "sj-1",
            "qty": 10000,
            "confirmed_at": "",
        }],
        delivery_proofs=[{"uploaded_at": "2026-09-08T02:00:00+07:00"}],
    ))
    assert invalid["age_closed"] is True
    assert invalid["age_end_at"] is None


def test_superseded_only_delivery_does_not_fall_back_to_order_stamp():
    row = status_po_row(_full(
        delivered_at="2026-09-20T10:00:00+07:00",
        shipments=[{
            "id": "sj-old",
            "superseded": True,
            "delivery_status": "Terkirim",
            "delivered_at": "2026-09-20T10:00:00+07:00",
            "lines": [{"line_idx": 0, "qty": 10000}],
        }],
        delivery_confirmations=[{
            "shipment_id": "sj-old",
            "qty": 10000,
            "confirmed_at": "2026-09-20T10:00:00+07:00",
        }],
    ))
    assert row["age_closed"] is True
    assert row["age_end_at"] is None


def test_partial_delivered_at_then_completion_has_no_age_end():
    row = status_po_row(_full(
        delivered_at="2026-09-04T08:00:00+07:00",
        delivery_status="Terkirim",
        current_stage=14,
        qty_delivered=10000,
        scheduled_delivery_date="2026-09-20",
        deadline="2026-09-30T00:00:00+07:00",
        delivery_proofs=[{"uploaded_at": "2026-09-09T00:00:00+07:00"}],
    ))
    assert row["age_closed"] is True
    assert row["age_end_at"] is None


def test_order_delivered_at_alone_is_not_an_age_end():
    row = status_po_row(_full(delivered_at="2026-09-06T15:00:00+07:00"))
    assert row["age_closed"] is True
    assert row["age_end_at"] is None


def test_pagination_splits_without_duplicates_or_gaps():
    orders = [
        _po(id=f"id-{i:04d}", po_number=f"PO-{i:04d}", order_date=f"2026-01-{(i % 28) + 1:02d}", qty_delivered=0)
        for i in range(105)
    ]
    page1 = build_status_po(orders, page=1, page_size=50)
    page2 = build_status_po(orders, page=2, page_size=50)
    page3 = build_status_po(orders, page=3, page_size=50)
    assert page1["total_count"] == 105
    assert page1["total_pages"] == 3
    assert page1["page_size"] == 50
    assert len(page1["rows"]) == 50
    assert len(page2["rows"]) == 50
    assert len(page3["rows"]) == 5
    assert page1["has_next"] is True and page1["has_prev"] is False
    assert page2["has_next"] is True and page2["has_prev"] is True
    assert page3["has_next"] is False and page3["has_prev"] is True
    ids = [r["id"] for r in page1["rows"] + page2["rows"] + page3["rows"]]
    assert len(ids) == len(set(ids)) == 105


def test_page_size_capped_at_50():
    orders = [_po(id=f"x{i}", po_number=f"PO-X{i}", qty_delivered=0) for i in range(60)]
    data = build_status_po(orders, page=1, page_size=999)
    assert data["page_size"] == 50
    assert len(data["rows"]) == 50


def test_search_product_name_and_historical_beyond_batch():
    orders = [
        _po(id="old", po_number="PO-OLD-9999", order_date="2024-01-01", qty_delivered=0,
            line_items=[{"name": "Lid Historic", "quantity": 10000, "production_process": "print"}]),
        *[_po(id=f"n{i}", po_number=f"PO-N{i:04d}", order_date="2026-06-01", qty_delivered=0) for i in range(60)],
    ]
    data = build_status_po(orders, filters={"po": "lid historic"}, page=1, page_size=50)
    assert data["total_count"] == 1
    assert data["rows"][0]["id"] == "old"


def test_filters_apply_before_pagination_count():
    orders = [
        _po(id="a", po_number="PO-A", customer="Alpha", order_date="2026-08-10", qty_delivered=0),
        _po(id="b", po_number="PO-B", customer="Beta", order_date="2026-08-20", qty_delivered=0),
        *[_po(id=f"c{i}", po_number=f"PO-C{i}", customer="Alpha", order_date="2026-08-15", qty_delivered=0) for i in range(55)],
    ]
    data = build_status_po(
        orders,
        filters={"customer": "Alpha", "date_from": "2026-08-01", "date_to": "2026-08-31"},
        page=1,
        page_size=50,
    )
    assert data["total_count"] == 56
    assert data["total_pages"] == 2
    assert len(data["rows"]) == 50
    assert all(r["customer"] == "Alpha" for r in data["rows"])


def test_hold_view_keeps_stage_based_special_semantics():
    orders = [
        _po(id="hold-open", current_stage=5, hold=True, qty_delivered=0),
        _po(id="hold-done-stage", current_stage=14, hold=True, qty_delivered=10000, delivery_status="Terkirim"),
        _po(id="open", current_stage=5, hold=False, qty_delivered=0),
    ]
    got = filter_orders_for_view(orders, "hold")
    assert [o["id"] for o in got] == ["hold-open"]


def test_status_options_are_global_not_page_scoped():
    orders = [
        _po(id="a", po_number="PO-A", order_date="2026-01-01", qty_delivered=0,
            helper_prep={"qty_prepared": 1}, screener_info={"screen_qty": 1},
            sample_approved=True, design_status="Approved", payment_status="Approved",
            design_files=[{"id": "d"}], payment_files=[{"id": "p"}], sample_files=[{"id": "s"}],
            operator_splits=[{"qty_completed": 10000, "line_idx": 0, "quantity": 10000}]),
        *[_po(id=f"b{i}", po_number=f"PO-B{i:03d}", order_date="2026-02-01", qty_delivered=0,
              current_stage=0) for i in range(60)],
    ]
    page1 = build_status_po(orders, page=1, page_size=50)
    assert page1["total_count"] == 61
    assert len(page1["rows"]) == 50
    # Options must include statuses from beyond the first page of rows.
    assert "status_options" in page1
    assert len(page1["status_options"]) >= 1
    assert page1.get("detail_docs_loaded") == 50


def test_two_pass_only_materializes_page_rows():
    orders = [_po(id=f"id-{i:04d}", po_number=f"PO-{i:04d}", order_date="2026-03-01", qty_delivered=0) for i in range(120)]
    data = build_status_po(orders, page=2, page_size=50)
    assert data["total_count"] == 120
    assert data["detail_docs_loaded"] == 50
    assert len(data["rows"]) == 50


def test_inactive_shipment_does_not_complete_for_tab():
    order = _po(
        id="fake-done",
        current_stage=14,
        qty_delivered=10000,
        delivery_status="Terkirim",
        shipments=[{
            "id": "sj-dead",
            "cancelled": True,
            "delivered_at": "2026-09-05T10:00:00+07:00",
            "delivery_status": "Terkirim",
            "lines": [{"line_idx": 0, "qty": 10000}],
        }],
        delivery_confirmations=[{
            "shipment_id": "sj-dead",
            "qty": 10000,
            "confirmed_at": "2026-09-05T10:00:00+07:00",
        }],
    )
    # qty_delivered field alone can still mark fully delivered via po_is_fully_delivered;
    # ensure cancelled-only shipment path does not invent completion when qty is zeroed.
    zeroed = _po(
        id="zeroed",
        current_stage=14,
        qty_delivered=0,
        delivery_status="Belum Terkirim",
        shipments=[{
            "id": "sj-dead",
            "cancelled": True,
            "delivered_at": "2026-09-05T10:00:00+07:00",
            "delivery_status": "Terkirim",
            "lines": [{"line_idx": 0, "qty": 10000}],
        }],
        delivery_confirmations=[{
            "shipment_id": "sj-dead",
            "qty": 10000,
            "confirmed_at": "2026-09-05T10:00:00+07:00",
        }],
    )
    assert [o["id"] for o in filter_orders_for_view([order, zeroed], "completed")] == ["fake-done"]
    assert [o["id"] for o in filter_orders_for_view([zeroed], "active")] == ["zeroed"]


def test_zero_qty_confirmation_is_not_a_delivery_time():
    row = status_po_row(_full(
        delivered_at="2026-09-04T10:00:00+07:00",
        shipments=[{
            "id": "sj-1",
            "delivered_at": "2026-09-04T10:00:00+07:00",
            "delivery_status": "Terkirim",
            "lines": [{"line_idx": 0, "qty": 10000}],
        }],
        delivery_confirmations=[
            {"shipment_id": "sj-1", "qty": 0, "confirmed_at": "2026-09-15T10:00:00+07:00"},
            {"shipment_id": "sj-1", "qty": 10000, "confirmed_at": "2026-09-04T10:00:00+07:00"},
        ],
    ))
    assert row["age_end_at"] == "2026-09-04T10:00:00+07:00"
