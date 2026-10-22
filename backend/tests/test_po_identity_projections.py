"""Focused locks: PO identity fields on projections + Buffer overdue exclusion paths."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _import_server():
    from dotenv import load_dotenv

    if (ROOT / ".env").exists():
        load_dotenv(ROOT / ".env")
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "test_database")
    import server

    return server


def test_sales_order_list_and_summary_include_identity():
    server = _import_server()
    assert server._SALES_ORDER_LIST_PROJECTION.get("priority") == 1
    assert server._SALES_ORDER_LIST_PROJECTION.get("buffer_stock") == 1
    summary = server._orders_list_summary_projection()
    assert summary.get("priority") == 1
    assert summary.get("buffer_stock") == 1
    ac = server._orders_action_center_projection()
    assert ac.get("priority") == 1
    assert ac.get("buffer_stock") == 1


def test_sales_pwa_and_status_po_expose_identity():
    from services.sales_pwa import serialize_list_row
    from services.status_po import status_po_row

    order = {
        "id": "o1",
        "po_number": "PO/1",
        "customer": "A",
        "priority": "urgent",
        "buffer_stock": True,
        "cancelled": False,
        "current_stage": 3,
        "order_date": "2026-10-01",
        "delivery_status": "Belum Terkirim",
        "line_items": [{"name": "X", "quantity": 10}],
    }
    pwa = serialize_list_row(order, [], [])
    assert pwa["priority"] == "urgent"
    assert pwa["urgent"] is True
    assert pwa["buffer_stock"] is True
    spo = status_po_row(order)
    assert spo["priority"] == "urgent"
    assert spo["buffer_stock"] is True


def test_kepala_produksi_buffer_never_customer_terlambat():
    from services.kepala_produksi_board import classify_row_kpi_flags

    with patch("services.kepala_produksi_board.wib_today", return_value=date(2026, 10, 20)):
        flags = classify_row_kpi_flags({
            "effective_delivery_date": "2026-10-01",
            "fully_delivered": False,
            "buffer_stock": True,
            "qty_production_remaining": 5,
            "priority": "urgent",
            "explicit_urgent": True,
        })
    assert flags["terlambat"] is False
    assert flags["urgent"] is True
    assert flags["perlu-produksi"] is True

    with patch("services.kepala_produksi_board.wib_today", return_value=date(2026, 10, 20)):
        late = classify_row_kpi_flags({
            "effective_delivery_date": "2026-10-01",
            "fully_delivered": False,
            "buffer_stock": False,
            "qty_production_remaining": 5,
        })
    assert late["terlambat"] is True


def test_reminders_exclude_buffer_from_overdue_bucket():
    """Operational deadline overdue + buffer_stock → skipped from reminder list."""
    import asyncio
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    server = _import_server()
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    shaped = server._shape_order_for_reminders_compute({
        "id": "buf-rem",
        "po_number": "PO/BUF/REM",
        "outlet": "O",
        "customer": "C",
        "deadline": past,
        "priority": "normal",
        "buffer_stock": True,
        "current_stage": 3,
        "status_label": "Produksi",
        "operator_splits": [],
    })
    fake_orders = MagicMock()
    agg = MagicMock()
    agg.to_list = AsyncMock(return_value=[shaped])
    fake_orders.aggregate = MagicMock(return_value=agg)
    original = server.db
    server.db = SimpleNamespace(orders=fake_orders)
    try:
        out = asyncio.run(server.reminders(user={"role": "admin", "email": "a@x.com"}))
    finally:
        server.db = original
    assert out["counts"]["overdue"] == 0
    assert all(r["id"] != "buf-rem" for r in out["reminders"])


def test_apply_priority_fields_copies_buffer():
    from services.kepala_produksi_board import apply_priority_fields

    row: dict = {}
    order = {
        "priority": "urgent",
        "buffer_stock": True,
        "hold": False,
        "hold_reasons": [],
        "total_quantity": 10,
        "qty_produced": 0,
        "line_items": [{"quantity": 10, "name": "X"}],
        "operator_splits": [],
        "delivery_status": "Belum Terkirim",
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_approved_at": "2026-10-01T00:00:00+07:00",
        "payment_approved_at": "2026-10-01T00:00:00+07:00",
        "effective_delivery_date": "2026-10-01",
        "delivery_deadline_effective": "2026-10-01",
    }
    apply_priority_fields(
        row,
        order,
        order["line_items"][0],
        produced=0,
        printable=10,
        scheduled=0,
    )
    assert row["buffer_stock"] is True
    assert row["urgent"] is True
    assert row["kpi_flags"]["terlambat"] is False or row.get("effective_delivery_date")


def test_pesanan_overdue_filter_excludes_buffer_stock():
    """Pesanan Tanggal Kirim overdue: normal included; buffer past date excluded; legacy missing = normal."""
    server = _import_server()
    today = date(2026, 10, 20)
    past = "2026-10-01"
    future = "2026-10-25"
    normal = {
        "id": "n1",
        "tanggal_pengiriman": past,
        "buffer_stock": False,
        "cancelled": False,
        "current_stage": 3,
        "sales": "Vira",
    }
    buffer_late = {
        "id": "b1",
        "tanggal_pengiriman": past,
        "buffer_stock": True,
        "cancelled": False,
        "current_stage": 3,
        "sales": "Vira",
    }
    legacy_missing = {
        "id": "l1",
        "tanggal_pengiriman": past,
        # no buffer_stock key → normal classification
        "cancelled": False,
        "current_stage": 3,
        "sales": "Vira",
    }
    future_ok = {
        "id": "f1",
        "tanggal_pengiriman": future,
        "buffer_stock": False,
        "cancelled": False,
        "current_stage": 3,
        "sales": "Vira",
    }
    with patch("services.sales_date_presets.wib_today", return_value=today):
        overdue = server._apply_sales_order_header_filters(
            [normal, buffer_late, legacy_missing, future_ok],
            tanggal_pengiriman_preset="overdue",
        )
        today_rows = server._apply_sales_order_header_filters(
            [
                {**normal, "id": "t1", "tanggal_pengiriman": today.isoformat()},
                {**buffer_late, "id": "t2", "tanggal_pengiriman": today.isoformat()},
            ],
            tanggal_pengiriman_preset="today",
        )
        sales_vira = server._apply_sales_order_header_filters(
            [normal, {**normal, "id": "other", "sales": "Budi"}],
            sales="Vira",
        )
    overdue_ids = {o["id"] for o in overdue}
    assert "n1" in overdue_ids
    assert "b1" not in overdue_ids
    assert "l1" in overdue_ids
    assert "f1" not in overdue_ids
    # Other presets unchanged — buffer not stripped from today
    assert {o["id"] for o in today_rows} == {"t1", "t2"}
    assert {o["id"] for o in sales_vira} == {"n1"}
