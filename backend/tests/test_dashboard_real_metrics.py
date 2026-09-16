from datetime import date, datetime, timezone
from pathlib import Path

from services.dashboard_today import (
    build_shipments_today,
    due_today_orders,
    partition_orders_by_delivery,
    shipments_today_mongo_query,
    wib_today,
)
from quantity_rules import is_fully_delivered


SERVER = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")


def _schedule_rows(order, today):
    return [
        {
            "id": order["id"],
            "shipment_id": shipment["id"],
            "shipment_index": index,
            "order_number": order["po_number"],
            "customer_name": order.get("customer", ""),
            "sj_label": shipment["number"],
            "latest_sj_number": shipment["number"],
            "item_label": "Cup",
            "sj_item_lines": shipment.get("lines", []),
            "qty_sj": shipment.get("qty"),
            "tanggal_pengiriman": shipment["delivery_date"],
            "status": "hari_ini" if shipment["delivery_date"] == today else "terjadwal",
            "status_label": "Hari Ini" if shipment["delivery_date"] == today else "Terjadwal",
        }
        for index, shipment in enumerate(order.get("shipments", []), start=1)
    ]


def test_wib_business_date_crosses_utc_midnight_boundary():
    now = datetime(2026, 9, 16, 19, 53, tzinfo=timezone.utc)
    assert wib_today(now) == date(2026, 9, 17)


def test_shipments_today_is_exact_schedule_row_count():
    orders = [
        {
            "id": "po-1",
            "po_number": "PO-1",
            "customer": "Customer",
            "shipments": [
                {"id": "sj-1", "number": "SJ-1", "delivery_date": "2026-09-17", "qty": 10},
                {"id": "sj-2", "number": "SJ-2", "delivery_date": "2026-09-17", "qty": 20},
                {"id": "sj-3", "number": "SJ-3", "delivery_date": "2026-09-18", "qty": 30},
            ],
        },
        {
            "id": "activity-only",
            "po_number": "PO-ACTIVITY",
            "delivery_note_created_at": "2026-09-17T09:00:00+07:00",
            "delivery_proofs": [{"uploaded_at": "2026-09-17T10:00:00+07:00"}],
            "shipments": [],
        },
        {
            "id": "cancelled",
            "po_number": "PO-CANCELLED",
            "cancelled": True,
            "shipments": [
                {"id": "sj-x", "number": "SJ-X", "delivery_date": "2026-09-17", "qty": 99}
            ],
        },
    ]

    result = build_shipments_today(
        orders,
        date(2026, 9, 17),
        schedule_row_builder=_schedule_rows,
    )

    assert result["total"] == 2
    assert [row["shipment_id"] for row in result["preview"]] == ["sj-1", "sj-2"]
    assert [row["qty"] for row in result["preview"]] == [10, 20]
    assert result["source"] == "delivery_schedule.schedule_rows(status=hari_ini)"


def test_shipments_query_uses_persisted_delivery_date():
    query = shipments_today_mongo_query(date(2026, 9, 17))
    assert query == {
        "cancelled": {"$ne": True},
        "shipments": {
            "$elemMatch": {
                "delivery_date": {"$exists": True, "$nin": [None, ""]}
            }
        },
    }


def test_active_and_completed_follow_real_delivered_quantity():
    partial_at_final_stage = {
        "id": "partial",
        "current_stage": 14,
        "delivery_status": "Terkirim",
        "qty_delivered": 40,
        "line_items": [{"quantity": 100}],
    }
    complete_with_stale_stage = {
        "id": "complete",
        "current_stage": 13,
        "delivery_status": "Sebagian Terkirim",
        "qty_delivered": 100,
        "line_items": [{"quantity": 100}],
    }
    legacy_complete = {
        "id": "legacy",
        "current_stage": 14,
        "delivery_status": "Terkirim",
        "line_items": [{"quantity": 50}],
    }

    active, completed = partition_orders_by_delivery(
        [partial_at_final_stage, complete_with_stale_stage, legacy_complete],
        is_fully_delivered,
    )

    assert [order["id"] for order in active] == ["partial"]
    assert [order["id"] for order in completed] == ["complete", "legacy"]


def test_due_today_uses_tanggal_pengiriman_not_legacy_deadline():
    orders = [
        {
            "id": "real-today",
            "deadline": "2026-09-30T00:00:00Z",
            "delivery_deadline_effective": "2026-09-17",
        },
        {
            "id": "legacy-deadline-today",
            "deadline": "2026-09-17T00:00:00Z",
            "delivery_deadline_effective": "2026-09-30",
        },
    ]

    assert [order["id"] for order in due_today_orders(orders, date(2026, 9, 17))] == [
        "real-today"
    ]


def test_dashboard_and_destination_share_schedule_projection_without_caps():
    summary_start = SERVER.index("async def dashboard_summary(")
    summary_end = SERVER.index('@api_router.get("/dashboard/today")')
    summary = SERVER[summary_start:summary_end]
    assert "shipments_today_mongo_query(today), SCHEDULE_PROJECTION" in summary
    assert ").to_list(None)" in summary

    schedule_start = SERVER.index("async def list_delivery_schedule(")
    schedule_end = SERVER.index('@api_router.get("/delivery/reschedules")')
    schedule = SERVER[schedule_start:schedule_end]
    assert "SCHEDULE_PROJECTION" in schedule
    assert '.to_list(None)' in schedule
    assert '.to_list(1000)' not in schedule
