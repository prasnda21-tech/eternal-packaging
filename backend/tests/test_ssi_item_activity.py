"""SSI item activity timeline — Fisik / SO / Cetak (read-only)."""
from __future__ import annotations

from services.ssi_item_activity import (
    LANE_CETAK,
    LANE_FISIK,
    LANE_SO,
    _so_cetak_events_from_orders,
)
from services.stock_saat_ini_excel import compute_excel_columns


def test_so_commitment_is_not_physical_stock_mutation():
    orders = [
        {
            "id": "po-so-1",
            "po_number": "PO-SO-1",
            "created_at": "2026-10-01T10:00:00+00:00",
            "created_by_name": "Sales",
            "line_items": [
                {
                    "master_item_id": "M1",
                    "quantity": 32_000,
                    "production_process": "print",
                    "item_type": "INVENTORY",
                }
            ],
            "operator_splits": [],
            "shipments": [],
        }
    ]
    so, cetak, _cov = _so_cetak_events_from_orders(orders, "M1")
    assert so
    assert all(ev["lane"] == LANE_SO for ev in so)
    assert all(ev["affects_physical_stock"] is False for ev in so)
    assert all(ev["metric_affected"] == "Komitmen SO" for ev in so)
    assert cetak == []


def test_operator_print_is_cetak_lane_not_stock():
    orders = [
        {
            "id": "po-ctk",
            "po_number": "PO-CTK",
            "created_at": "2026-10-02T10:00:00+00:00",
            "line_items": [
                {
                    "master_item_id": "M1",
                    "quantity": 8_000,
                    "production_process": "print",
                    "item_type": "INVENTORY",
                }
            ],
            "operator_splits": [
                {
                    "line_idx": 0,
                    "qty_completed": 8_000,
                    "completed_at": "2026-10-03T08:00:00+00:00",
                    "operator_name": "Op A",
                }
            ],
            "shipments": [],
        }
    ]
    so, cetak, _cov = _so_cetak_events_from_orders(orders, "M1")
    assert any(ev["activity_code"] == "SO_COMMITMENT_ACTIVE" for ev in so)
    assert len(cetak) == 1
    assert cetak[0]["lane"] == LANE_CETAK
    assert cetak[0]["affects_physical_stock"] is False
    assert cetak[0]["metric_affected"] == "Qty Tercetak"
    assert cetak[0]["qty_pcs"] == 8_000


def test_multiwarna_service_line_not_double_counted_in_cetak():
    orders = [
        {
            "id": "po-mw",
            "created_at": "2026-10-04T10:00:00+00:00",
            "line_items": [
                {
                    "line_id": "ln-main",
                    "master_item_id": "M1",
                    "quantity": 8_000,
                    "production_process": "print",
                    "item_type": "INVENTORY",
                },
                {
                    "line_id": "ln-w2",
                    "master_item_id": "SVC1",
                    "parent_line_id": "ln-main",
                    "quantity": 8_000,
                    "production_process": "print",
                    "item_type": "SERVICE",
                },
            ],
            "operator_splits": [
                {"line_idx": 0, "qty_completed": 8_000, "completed_at": "2026-10-05T09:00:00+00:00"},
                {"line_idx": 1, "qty_completed": 8_000, "completed_at": "2026-10-05T09:05:00+00:00"},
            ],
            "shipments": [],
        }
    ]
    _so, cetak, _cov = _so_cetak_events_from_orders(orders, "M1")
    # Only parent inventory print line for M1 — SERVICE warna ignored for M1 touch.
    assert sum(ev["qty_pcs"] for ev in cetak if ev["activity_code"] == "OPERATOR_PRINT_COMPLETED") == 8_000


def test_mixed_print_plain_shipment_cetak_ref_excludes_polos():
    orders = [
        {
            "id": "po-mix",
            "created_at": "2026-10-06T10:00:00+00:00",
            "line_items": [
                {
                    "master_item_id": "M1",
                    "quantity": 10_000,
                    "production_process": "print",
                },
                {
                    "master_item_id": "M1",
                    "quantity": 2_000,
                    "production_process": "non_print",
                },
            ],
            "operator_splits": [
                {"line_idx": 0, "qty_completed": 8_000, "completed_at": "2026-10-07T10:00:00+00:00"},
            ],
            "shipments": [
                {
                    "id": "sj-print",
                    "delivery_status": "Terkirim",
                    "delivered_at": "2026-10-08T10:00:00+00:00",
                    "qty_delivered": 4_000,
                    "lines": [{"line_idx": 0, "qty": 4_000}],
                },
                {
                    "id": "sj-polos",
                    "delivery_status": "Terkirim",
                    "delivered_at": "2026-10-08T11:00:00+00:00",
                    "qty_delivered": 1_000,
                    "lines": [{"line_idx": 1, "qty": 1_000}],
                },
            ],
        }
    ]
    so, cetak, _cov = _so_cetak_events_from_orders(orders, "M1")
    print_del = [ev for ev in cetak if ev["activity_code"] == "PRINT_DELIVERY_PROVEN"]
    assert len(print_del) == 1
    assert print_del[0]["qty_pcs"] == 4_000
    assert print_del[0]["affects_physical_stock"] is False
    fulfilled = [ev for ev in so if ev["activity_code"] == "SO_FULFILLED"]
    assert sum(ev["qty_pcs"] for ev in fulfilled) == 5_000


def test_cancelled_so_emits_commitment_cancel_not_stock():
    orders = [
        {
            "id": "po-cx",
            "cancelled": True,
            "cancelled_at": "2026-10-09T12:00:00+00:00",
            "created_at": "2026-10-01T10:00:00+00:00",
            "line_items": [
                {"master_item_id": "M1", "quantity": 10_000, "production_process": "print"},
            ],
            "operator_splits": [],
            "shipments": [],
        }
    ]
    so, cetak, _cov = _so_cetak_events_from_orders(orders, "M1")
    assert any(ev["activity_code"] == "SO_CANCELLED" for ev in so)
    assert all(ev["affects_physical_stock"] is False for ev in so)
    assert cetak == []


def test_owner_excel_numbers_unchanged_by_activity_module():
    row = compute_excel_columns(
        saldo_awal=10_000,
        delivered_total=4_000,
        po_ordered=32_000,
        so_delivered=4_000,
        qty_tercetak=8_000,
        cetak_delivered_explicit=4_000,
        stock_book=6_000,
    )
    assert row["stock"] == 6_000
    assert row["stock_dapat_dijual"] == -22_000
    assert row["qty_tercetak"] == 8_000
    assert row["qty_polos"] == 2_000
    assert row["tercetak_belum_kirim"] == 4_000
    assert row["cetak_sudah_dikirim"] == 4_000


def test_fisik_lane_constant():
    assert LANE_FISIK == "FISIK"
    assert LANE_SO == "SO"
    assert LANE_CETAK == "CETAK"
