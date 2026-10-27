"""Cross-module schedule reconcile: sales_deliveries authoritative vs orders.shipments.

Local fixtures only — no production DB.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.delivery_schedule import (
    SCHEDULE_PROJECTION,
    merge_schedule_shipment,
    reconcile_schedule_shipments,
    sales_delivery_is_active_for_schedule,
    schedule_rows,
    shipment_projection_from_sales_delivery,
)


def _po(**extra):
    base = {
        "id": "ord-1",
        "po_number": "PO/2026/REC",
        "customer": "PT Reconcile",
        "customer_name": "PT Reconcile",
        "deadline": "2026-10-20",
        "design_approved_at": "2026-10-01T10:00:00+07:00",
        "payment_approved_at": "2026-10-01T12:00:00+07:00",
        "delivery_status": "Belum Terkirim",
        "line_items": [{"name": "Karton", "quantity": 100}],
        "qty_delivered": 0,
        "packing_info": {"qty_packed": 100},
        "loading_info": {"lines": [{"loaded_qty": 100}]},
        "shipments": [],
        "priority": "normal",
        "buffer_stock": False,
    }
    base.update(extra)
    return base


def _sd(sid, number, date, **extra):
    doc = {
        "id": sid,
        "po_id": "ord-1",
        "sj_number": number,
        "delivery_date": date,
        "shipment_index": 1,
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Karton", "qty": 10, "unit": "pcs"}],
        "total_qty": 10,
        "is_cancelled": False,
        "status": "SEBAGIAN",
    }
    doc.update(extra)
    return doc


def test_sales_delivery_cancel_supersede_rules():
    assert sales_delivery_is_active_for_schedule(_sd("a", "SJ-A", "2026-10-24")) is True
    assert sales_delivery_is_active_for_schedule(_sd("a", "SJ-A", "2026-10-24", is_cancelled=True)) is False
    assert sales_delivery_is_active_for_schedule(_sd("a", "SJ-A", "2026-10-24", status="DIBATALKAN")) is False
    assert sales_delivery_is_active_for_schedule(_sd("a", "SJ-A", "2026-10-24", status="superseded")) is False
    assert sales_delivery_is_active_for_schedule(_sd("a", "SJ-A", "2026-10-24", superseded=True)) is False


def test_orphan_sales_delivery_appears_in_schedule():
    order = _po(shipments=[])
    sd = _sd("sd-orphan", "SJ-ORPHAN", "2026-10-24")
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=[sd])
    assert len(rows) == 1
    assert rows[0]["shipment_id"] == "sd-orphan"
    assert rows[0]["sj_delivery_date"] == "2026-10-24"
    assert rows[0]["effective_delivery_date"] == "2026-10-24"
    assert rows[0]["qty_sj"] == 10


def test_authoritative_date_wins_over_embedded_mismatch():
    order = _po(shipments=[{
        "id": "sd-b",
        "number": "SJ-B",
        "delivery_date": "2026-10-20",
        "shipment_index": 1,
        "lines": [{"name": "Karton", "qty": 10}],
        "delivery_status": "Belum Terkirim",
    }])
    sd = _sd("sd-b", "SJ-B", "2026-10-25")
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=[sd])
    assert len(rows) == 1
    assert rows[0]["sj_delivery_date"] == "2026-10-25"
    assert rows[0]["effective_delivery_date"] == "2026-10-25"


def test_missing_embedded_date_repaired_from_sales_delivery():
    order = _po(shipments=[{
        "id": "sd-c",
        "number": "SJ-C",
        "shipment_index": 1,
        "lines": [{"name": "Karton", "qty": 5}],
    }])
    sd = _sd("sd-c", "SJ-C", "2026-10-26", total_qty=5,
             line_items=[{"line_idx": 0, "name": "Karton", "qty": 5, "unit": "pcs"}])
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=[sd])
    assert len(rows) == 1
    assert rows[0]["sj_delivery_date"] == "2026-10-26"


def test_missing_embedded_id_number_uses_sales_delivery_adapter():
    # Embedding unusable; SD complete → one row from adapter (no double count).
    order = _po(shipments=[{
        "delivery_date": "2026-10-26",
        "shipment_index": 1,
        "lines": [{"name": "Karton", "qty": 5}],
    }])
    sd = _sd("sd-d", "SJ-D", "2026-10-26")
    ships = reconcile_schedule_shipments(order, [sd])
    assert len(ships) == 1
    assert ships[0]["id"] == "sd-d"
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=[sd])
    assert len(rows) == 1
    assert rows[0]["shipment_id"] == "sd-d"


def test_healthy_embed_matches_authoritative_no_duplicate():
    sh = {
        "id": "sd-ok",
        "sales_delivery_id": "sd-ok",
        "number": "SJ-OK",
        "delivery_date": "2026-10-24",
        "shipment_index": 1,
        "lines": [{"name": "Karton", "qty": 10, "unit": "pcs"}],
        "delivered_at": None,
        "delivery_status": "",
    }
    order = _po(shipments=[sh])
    sd = _sd("sd-ok", "SJ-OK", "2026-10-24")
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=[sd])
    assert len(rows) == 1
    assert rows[0]["shipment_id"] == "sd-ok"


def test_multi_sj_partial_delivered_buffer_urgent_sunday():
    order = _po(
        buffer_stock=True,
        priority="urgent",
        shipments=[
            {
                "id": "sd-1", "number": "SJ-1", "delivery_date": "2026-10-24",
                "shipment_index": 1, "lines": [{"name": "Karton", "qty": 40}],
                "delivery_status": "Terkirim", "delivered_at": "2026-10-24T08:00:00Z",
            },
            {
                "id": "sd-2", "number": "SJ-2", "delivery_date": "2026-10-25",
                "shipment_index": 2, "lines": [{"name": "Karton", "qty": 30}],
            },
        ],
    )
    sds = [
        _sd("sd-1", "SJ-1", "2026-10-24", shipment_index=1, total_qty=40,
            line_items=[{"line_idx": 0, "name": "Karton", "qty": 40, "unit": "pcs"}]),
        _sd("sd-2", "SJ-2", "2026-10-25", shipment_index=2, total_qty=30,
            line_items=[{"line_idx": 0, "name": "Karton", "qty": 30, "unit": "pcs"}]),
        # Orphan Sunday SJ — manual/SJ date allowed
        _sd("sd-3", "SJ-3", "2026-10-25", shipment_index=3),  # 25 Oct 2026 is Sunday
        _sd("sd-4", "SJ-4", "2026-10-27", shipment_index=4),
    ]
    # Fix Sunday date: 2026-10-25 is Sunday
    sds[2] = _sd("sd-3", "SJ-3", "2026-10-25", shipment_index=3,
                 line_items=[{"line_idx": 0, "name": "Karton", "qty": 10, "unit": "pcs"}], total_qty=10)
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=sds)
    ids = [r["shipment_id"] for r in rows]
    assert ids == ["sd-1", "sd-2", "sd-3", "sd-4"]
    assert all(r["buffer_stock"] for r in rows)
    assert all(r["urgent"] for r in rows)
    assert rows[2]["sj_delivery_date"] == "2026-10-25"


def test_cancelled_and_superseded_excluded_even_if_embedding_active_looking():
    order = _po(shipments=[
        {"id": "sd-x", "number": "SJ-X", "delivery_date": "2026-10-25", "shipment_index": 1,
         "lines": [{"name": "Karton", "qty": 5}]},
        {"id": "sd-y", "number": "SJ-Y", "delivery_date": "2026-10-25", "shipment_index": 2,
         "lines": [{"name": "Karton", "qty": 5}]},
        {"id": "sd-z", "number": "SJ-Z", "delivery_date": "2026-10-25", "shipment_index": 3,
         "lines": [{"name": "Karton", "qty": 5}]},
    ])
    sds = [
        _sd("sd-x", "SJ-X", "2026-10-25", is_cancelled=True),
        _sd("sd-y", "SJ-Y", "2026-10-25", status="superseded"),
        _sd("sd-z", "SJ-Z", "2026-10-25"),
    ]
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=sds)
    assert [r["shipment_id"] for r in rows] == ["sd-z"]


def test_cancelled_embedding_excluded_when_projection_keeps_flags():
    order = _po(shipments=[
        {
            "id": "sd-c", "number": "SJ-C", "delivery_date": "2026-10-25",
            "status": "cancelled", "is_cancelled": True, "shipment_index": 1,
            "lines": [{"name": "Karton", "qty": 5}],
        },
    ])
    # No SD list entry (already cancelled in SD collection filter) — embedding alone.
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=[])
    assert rows == []


def test_legacy_embedding_without_sales_delivery_kept():
    order = _po(shipments=[{
        "id": "legacy-1", "number": "SJ-LEGACY", "delivery_date": "2026-10-26",
        "shipment_index": 1, "lines": [{"name": "Karton", "qty": 8}],
    }])
    rows = schedule_rows(order, today="2026-10-27", sales_deliveries=[])
    assert len(rows) == 1
    assert rows[0]["shipment_id"] == "legacy-1"


def test_merge_preserves_embedded_delivery_status():
    embedded = {
        "id": "sd-m",
        "number": "SJ-OLD",
        "delivery_date": "2026-10-20",
        "delivery_status": "Terkirim",
        "delivered_at": "2026-10-24T10:00:00Z",
        "lines": [{"name": "Karton", "qty": 10}],
    }
    sd = _sd("sd-m", "SJ-NEW", "2026-10-24")
    merged = merge_schedule_shipment(embedded, sd)
    assert merged["delivery_date"] == "2026-10-24"
    assert merged["number"] == "SJ-NEW"
    assert merged["delivery_status"] == "Terkirim"
    assert merged["delivered_at"] == "2026-10-24T10:00:00Z"


def test_schedule_projection_includes_inactive_shipment_fields():
    for key in (
        "shipments.status",
        "shipments.is_cancelled",
        "shipments.cancelled",
        "shipments.superseded",
        "shipments.superseded_by_shipment_id",
        "shipments.sj_status",
        "shipments.deleted",
        "shipments.is_deleted",
    ):
        assert SCHEDULE_PROJECTION.get(key) == 1


def test_adapter_never_claims_persistence_flag_only():
    sd = _sd("sd-a", "SJ-A", "2026-10-24")
    adapter = shipment_projection_from_sales_delivery(sd)
    assert adapter["from_sales_delivery_reconcile"] is True
    assert adapter["id"] == "sd-a"
    assert adapter["lines"][0]["qty"] == 10


def test_embedding_only_mode_unchanged_when_sales_deliveries_omitted():
    order = _po(shipments=[{
        "id": "sh-1", "number": "SJ-1", "delivery_date": "2026-10-24",
        "shipment_index": 1, "lines": [{"name": "Karton", "qty": 3}],
    }])
    rows = schedule_rows(order, today="2026-10-27")  # sales_deliveries=None
    assert len(rows) == 1
    assert rows[0]["shipment_id"] == "sh-1"
