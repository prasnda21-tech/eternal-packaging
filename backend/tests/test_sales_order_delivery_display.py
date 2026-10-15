"""Display progress for the Pesanan Penjualan column. Eligibility stays separate."""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from server import app, current_user
from services.sales_order_delivery_display import build_sales_order_delivery_progress


def _lines(*rows):
    return [{"quantity": qty, "unit": unit} for qty, unit in rows]


def _ship(status=None, **extra):
    doc = {"is_cancelled": False, "cancelled": False, "line_items": extra.pop("line_items", None)}
    if status is not None:
        doc["status"] = status
    doc.update(extra)
    return doc


def _conf(shipment_id, qty, items, action_id="act-1", **extra):
    rec = {
        "id": action_id,
        "action_id": action_id,
        "shipment_id": shipment_id,
        "qty": qty,
        "items": items,
    }
    rec.update(extra)
    return rec


def test_no_shipment_and_unconfirmed_sj_stay_zero():
    lines = _lines((1000, "pcs"))
    none = build_sales_order_delivery_progress(lines, [])
    assert none["status"] == "BELUM DIKIRIM"
    assert none["total_shipped"] == 0
    assert none["total_ordered"] == 1000
    assert none["verified"] is True

    created = build_sales_order_delivery_progress(lines, [
        _ship(id="sj-1", line_items=[{"line_idx": 0, "qty": 400}], total_qty=400),
    ])
    assert created["total_shipped"] == 0
    assert created["status"] == "BELUM DIKIRIM"
    assert created["verified"] is True

    header = build_sales_order_delivery_progress(lines, [
        _ship(id="sj-h", total_qty=1000),
    ])
    assert header["status"] == "BELUM DIKIRIM"
    assert header["total_shipped"] == 0
    assert header["verified"] is True


def test_confirmed_line_qty_is_the_display_source():
    lines = _lines((1000, "pcs"))
    partial = build_sales_order_delivery_progress(
        lines,
        [_ship(id="sj-1", line_items=[{"line_idx": 0, "qty": 400}])],
        confirmations=[_conf("sj-1", 400, [{"line_idx": 0, "qty": 400}])],
    )
    assert partial["status"] == "SEBAGIAN"
    assert partial["total_shipped"] == 400
    assert partial["total_remaining"] == 600

    full = build_sales_order_delivery_progress(
        lines,
        [_ship(id="sj-2", line_items=[{"line_idx": 0, "qty": 1000}])],
        confirmations=[_conf("sj-2", 1000, [{"line_idx": 0, "qty": 1000}], action_id="act-full")],
    )
    assert full["status"] == "SELESAI"
    assert full["total_shipped"] == 1000
    assert full["total_remaining"] == 0


def test_two_shipments_count_only_confirmed_ones():
    lines = _lines((1000, "pcs"))
    one = build_sales_order_delivery_progress(lines, [
        _ship(id="sj-a", line_items=[{"line_idx": 0, "qty": 400}]),
        _ship(id="sj-b", line_items=[{"line_idx": 0, "qty": 300}]),
    ], confirmations=[_conf("sj-a", 400, [{"line_idx": 0, "qty": 400}])])
    assert one["total_shipped"] == 400
    assert one["status"] == "SEBAGIAN"

    both = build_sales_order_delivery_progress(lines, [
        _ship(id="sj-a", line_items=[{"line_idx": 0, "qty": 400}]),
        _ship(id="sj-b", line_items=[{"line_idx": 0, "qty": 600}]),
    ], confirmations=[
        _conf("sj-a", 400, [{"line_idx": 0, "qty": 400}], action_id="a"),
        _conf("sj-b", 600, [{"line_idx": 0, "qty": 600}], action_id="b"),
    ])
    assert both["total_shipped"] == 1000
    assert both["status"] == "SELESAI"


def test_repeated_action_and_same_shipment_do_not_double():
    lines = _lines((1000, "pcs"))
    ship = [_ship(id="sj-1", line_items=[{"line_idx": 0, "qty": 400}])]
    items = [{"line_idx": 0, "qty": 400}]
    replay = build_sales_order_delivery_progress(lines, ship, confirmations=[
        _conf("sj-1", 400, items, action_id="same"),
        _conf("sj-1", 400, items, action_id="same"),
    ])
    assert replay["total_shipped"] == 400

    repeated = build_sales_order_delivery_progress(lines, ship, confirmations=[
        _conf("sj-1", 400, items, action_id="one"),
        _conf("sj-1", 400, items, action_id="two"),
    ])
    assert repeated["total_shipped"] == 400


def test_cancelled_confirmation_and_shipment_do_not_count():
    lines = _lines((1000, "pcs"))
    cancelled_conf = build_sales_order_delivery_progress(
        lines,
        [_ship(id="sj-1", line_items=[{"line_idx": 0, "qty": 400}])],
        confirmations=[_conf("sj-1", 400, [{"line_idx": 0, "qty": 400}], cancelled=True)],
    )
    assert cancelled_conf["total_shipped"] == 0
    assert cancelled_conf["status"] == "BELUM DIKIRIM"
    assert cancelled_conf["verified"] is True

    cancelled_sj = build_sales_order_delivery_progress(
        lines,
        [_ship(id="sj-1", status="DIBATALKAN", line_items=[{"line_idx": 0, "qty": 400}])],
        confirmations=[_conf("sj-1", 400, [{"line_idx": 0, "qty": 400}])],
    )
    assert cancelled_sj["total_shipped"] == 0
    assert cancelled_sj["status"] == "BELUM DIKIRIM"


@pytest.mark.parametrize("flag", [
    {"is_cancelled": True},
    {"cancelled": True},
    {"status": "cancelled"},
    {"status": "deleted"},
    {"status": "superseded"},
    {"status": "DIBATALKAN"},
])
def test_invalid_shipments_do_not_count(flag):
    progress = build_sales_order_delivery_progress(_lines((1000, "pcs")), [
        _ship(id="sj-x", line_items=[{"line_idx": 0, "qty": 1000}], **flag),
    ], confirmations=[_conf("sj-x", 1000, [{"line_idx": 0, "qty": 1000}])])
    assert progress["total_shipped"] == 0
    assert progress["status"] == "BELUM DIKIRIM"


def test_legacy_delivered_snapshot_counts_once_and_header_only_is_unverified():
    lines = _lines((1000, "pcs"))
    legacy = build_sales_order_delivery_progress(
        lines,
        [_ship(id="sj-1", delivered_at="2026-01-01T00:00:00Z", line_items=[{"line_idx": 0, "qty": 400}])],
        confirmations=[_conf("sj-1", 400, [{"line_idx": 0, "qty": 400}])],
    )
    assert legacy["total_shipped"] == 400
    assert legacy["status"] == "SEBAGIAN"
    assert legacy["verified"] is True

    embedded = build_sales_order_delivery_progress(
        lines,
        [_ship(id="sd-1", line_items=[{"line_idx": 0, "qty": 400}])],
        embedded_shipments=[{
            "id": "sh-1",
            "sales_delivery_id": "sd-1",
            "delivered_at": "2026-01-01T00:00:00Z",
            "lines": [{"line_idx": 0, "qty": 400}],
        }],
    )
    assert embedded["total_shipped"] == 400

    unverified = build_sales_order_delivery_progress(lines, [
        _ship(id="sj-h", delivered_at="2026-01-01T00:00:00Z", total_qty=1000),
    ])
    assert unverified["status"] == "UNVERIFIED"
    assert unverified["verified"] is False
    assert unverified["total_shipped"] is None
    assert unverified["units"] == []


def test_snapshot_increment_mismatch_is_not_counted():
    progress = build_sales_order_delivery_progress(
        _lines((1000, "pcs")),
        [_ship(id="sj-1", line_items=[{"line_idx": 0, "qty": 800}])],
        confirmations=[_conf("sj-1", 400, [{"line_idx": 0, "qty": 800}])],
    )
    assert progress["status"] == "UNVERIFIED"
    assert progress["total_shipped"] is None
    assert progress["units"] == []


def test_order_header_qty_is_not_the_display_source():
    progress = build_sales_order_delivery_progress(
        _lines((1000, "pcs")),
        [_ship(id="sj-1", line_items=[{"line_idx": 0, "qty": 1000}])],
        confirmations=[_conf("sj-1", 400, [{"line_idx": 0, "qty": 400}])],
    )
    assert progress["total_ordered"] == 1000
    assert progress["total_shipped"] == 400


def test_mixed_units_follow_confirmed_lines_only():
    lines = _lines((1000, "pcs"), (2, "dus"))
    partial = build_sales_order_delivery_progress(lines, [
        _ship(id="sj-1", line_items=[
            {"line_idx": 0, "qty": 1000},
            {"line_idx": 1, "qty": 2},
        ]),
    ], confirmations=[_conf("sj-1", 400, [{"line_idx": 0, "qty": 400}])])
    assert partial["total_ordered"] is None
    assert partial["total_shipped"] is None
    assert partial["status"] == "SEBAGIAN"
    assert partial["units"] == [
        {"unit": "pcs", "ordered": 1000, "shipped": 400},
        {"unit": "dus", "ordered": 2, "shipped": 0},
    ]
    assert partial["unverified_remainder"] is False

    done = build_sales_order_delivery_progress(lines, [
        _ship(id="sj-2", line_items=[
            {"line_idx": 0, "qty": 1000},
            {"line_idx": 1, "qty": 2},
        ]),
    ], confirmations=[_conf("sj-2", 1002, [
        {"line_idx": 0, "qty": 1000},
        {"line_idx": 1, "qty": 2},
    ], action_id="both")])
    assert done["status"] == "SELESAI"
    assert done["units"] == [
        {"unit": "pcs", "ordered": 1000, "shipped": 1000},
        {"unit": "dus", "ordered": 2, "shipped": 2},
    ]


def test_partial_unmapped_keeps_proven_units():
    lines = _lines((1000, "pcs"), (2, "dus"), (5, "pack"))
    partial = build_sales_order_delivery_progress(lines, [
        _ship(id="sj-1", delivered_at="2026-01-01T00:00:00Z", line_items=[
            {"line_idx": 0, "qty": 400},
            {"line_idx": 1, "qty": 1},
            {"line_idx": 9, "qty": 7},
        ]),
    ])
    assert partial["units"] == [
        {"unit": "pcs", "ordered": 1000, "shipped": 400},
        {"unit": "dus", "ordered": 2, "shipped": 1},
        {"unit": "pack", "ordered": 5, "shipped": 0},
    ]
    assert partial["unverified_remainder"] is True
    assert partial["total_shipped"] is None
    assert sum(row["shipped"] for row in partial["units"]) == 401


def test_unreadable_qty_does_not_publish_a_verified_total():
    progress = build_sales_order_delivery_progress(
        [{"quantity": "1.000", "unit": "pcs"}],
        [],
    )
    assert progress["verified"] is False
    assert progress["total_shipped"] is None
    assert progress["status"] != "SELESAI"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    return {"email": "admin@eternalpackaging.com", "name": "Admin Tester", "role": "admin"}


def _assert_list_gate_matches_detail(row, detail):
    """List totals and Kirim follow Detail. Zero-shipped label may differ."""
    summary = row["delivery_summary"]
    assert summary["total_ordered"] == detail["total_ordered"]
    assert summary["total_shipped"] == detail["total_shipped"]
    assert summary["total_remaining"] == detail["total_remaining"]
    kirim = (not row.get("cancelled")) and summary["total_remaining"] > 0
    assert kirim is detail["can_create_delivery"]
    if row.get("cancelled"):
        assert summary["status"] == "DIBATALKAN"
        assert detail["delivery_status"] == "DIBATALKAN"
    elif detail["total_shipped"] == 0:
        assert summary["status"] == "BELUM DIKIRIM"
        assert detail["delivery_status"] == "SEBAGIAN"
    else:
        assert summary["status"] == detail["delivery_status"]


def _order(**overrides):
    oid = str(uuid.uuid4())
    marker = overrides.pop("marker")
    doc = {
        "id": oid,
        "po_number": f"PO-DISP-{marker}",
        "customer": f"Cust {marker}",
        "outlet": "Outlet",
        "sales": "Vira",
        "created_by_user": "Tiana",
        "order_date": "2026-10-10",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_quantity": 1000,
        "current_stage": 11,
        "status_label": "Packing",
        "cancelled": False,
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_files": [{"name": "d.pdf"}],
        "payment_files": [{"name": "p.pdf"}],
        "line_items": [{"name": "Cup", "quantity": 1000, "unit": "pcs"}],
    }
    doc.update(overrides)
    return doc


@pytest.mark.anyio
async def test_list_display_ignores_invalid_shipments_but_gate_does_not(bind_motor):
    import server
    marker = uuid.uuid4().hex[:8].upper()
    packing = _order(marker=f"P{marker}", current_stage=11, status_label="Packing")
    packing_full = _order(marker=f"F{marker}", current_stage=11, status_label="Packing")
    legacy = _order(marker=f"L{marker}", current_stage=11, status_label="Packing")
    selesai = _order(
        marker=f"S{marker}",
        current_stage=14,
        status_label="Pengiriman",
        total_quantity=5000,
        line_items=[{"name": "Cup", "quantity": 1000, "unit": "pcs"}],
    )
    await server.db.orders.insert_many([packing, packing_full, selesai, legacy])
    await server.db.sales_deliveries.insert_many([
        {
            "id": str(uuid.uuid4()),
            "po_id": packing["id"],
            "is_cancelled": False,
            "cancelled": False,
            "status": "SELESAI",
            "total_qty": 400,
            "line_items": [{"line_idx": 0, "qty": 400}],
        },
        {
            "id": str(uuid.uuid4()),
            "po_id": packing["id"],
            "is_cancelled": False,
            "cancelled": False,
            "status": "superseded",
            "total_qty": 600,
            "line_items": [{"line_idx": 0, "qty": 600}],
        },
        {
            "id": str(uuid.uuid4()),
            "po_id": packing_full["id"],
            "is_cancelled": False,
            "status": "SELESAI",
            "total_qty": 1000,
            "line_items": [{"line_idx": 0, "qty": 1000}],
        },
        {
            "id": str(uuid.uuid4()),
            "po_id": selesai["id"],
            "is_cancelled": False,
            "status": "SELESAI",
            "total_qty": 1000,
            "line_items": [{"line_idx": 0, "qty": 400}],
        },
        {
            "id": str(uuid.uuid4()),
            "po_id": legacy["id"],
            "is_cancelled": False,
            "status": "SELESAI",
            "total_qty": 1000,
        },
    ])
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-orders", params={"q": marker, "status": "gabungan"})
            assert listed.status_code == 200
            by_id = {row["id"]: row for row in listed.json()["orders"]}

            full_row = by_id[packing_full["id"]]
            assert full_row["list_tab"] == "active"
            assert full_row["current_stage"] == 11
            assert full_row["delivery_progress"]["status"] == "BELUM DIKIRIM"
            assert full_row["delivery_progress"]["total_shipped"] == 0
            assert full_row["delivery_summary"]["total_shipped"] == 1000
            assert full_row["delivery_summary"]["total_remaining"] == 0
            assert full_row["delivery_summary"]["status"] == "SELESAI"

            packing_row = by_id[packing["id"]]
            assert packing_row["list_tab"] == "active"
            assert packing_row["created_by_user"] == "Tiana"
            assert packing_row["delivery_summary"]["total_shipped"] == 400
            assert packing_row["delivery_summary"]["total_remaining"] == 600
            assert packing_row["delivery_summary"]["status"] == "SEBAGIAN"
            assert packing_row["delivery_progress"]["total_shipped"] == 0
            assert packing_row["delivery_progress"]["total_ordered"] == 1000
            assert packing_row["delivery_progress"]["status"] == "BELUM DIKIRIM"
            assert packing_row["delivery_progress"]["needs_verification"] is False

            legacy_row = by_id[legacy["id"]]
            assert legacy_row["delivery_summary"]["total_ordered"] == 1000
            assert legacy_row["delivery_summary"]["total_shipped"] == 0
            assert legacy_row["delivery_summary"]["total_remaining"] == 1000
            assert legacy_row["delivery_summary"]["status"] == "BELUM DIKIRIM"
            assert legacy_row["delivery_progress"]["status"] == "BELUM DIKIRIM"
            assert legacy_row["delivery_progress"]["total_shipped"] == 0
            assert legacy_row["delivery_progress"]["needs_verification"] is False

            selesai_row = by_id[selesai["id"]]
            assert selesai_row["list_tab"] == "completed"
            assert selesai_row["delivery_summary"]["total_ordered"] == 1000
            assert selesai_row["delivery_summary"]["total_shipped"] == 400
            assert selesai_row["delivery_summary"]["total_remaining"] == 600
            assert selesai_row["delivery_summary"]["status"] == "SEBAGIAN"
            assert selesai_row["delivery_progress"]["total_ordered"] == 1000
            assert selesai_row["delivery_progress"]["total_shipped"] == 0
            assert selesai_row["delivery_progress"]["status"] == "BELUM DIKIRIM"

            from services.sales_deliveries import get_po_delivery_summary
            for row in by_id.values():
                detail = await get_po_delivery_summary(server.db, row["id"])
                _assert_list_gate_matches_detail(row, detail)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_list_gate_matches_detail_for_remaining_cases(bind_motor):
    import server
    from services.sales_deliveries import get_po_delivery_summary
    marker = uuid.uuid4().hex[:8].upper()
    none = _order(marker=f"N{marker}")
    two = _order(marker=f"W{marker}")
    cancelled_flag = _order(marker=f"C{marker}")
    cancelled_status = _order(marker=f"X{marker}")
    dibatalkan = _order(marker=f"Z{marker}")
    missing_idx = _order(marker=f"M{marker}")
    multi = _order(
        marker=f"U{marker}",
        total_quantity=9999,
        line_items=[
            {"name": "Cup", "quantity": 1000, "unit": "pcs"},
            {"name": "Dus", "quantity": 2, "unit": "dus"},
        ],
    )
    await server.db.orders.insert_many([
        none, two, cancelled_flag, cancelled_status, dibatalkan, missing_idx, multi,
    ])
    await server.db.sales_deliveries.insert_many([
        {"id": str(uuid.uuid4()), "po_id": two["id"], "is_cancelled": False, "status": "SELESAI", "total_qty": 400, "line_items": [{"line_idx": 0, "qty": 400}]},
        {"id": str(uuid.uuid4()), "po_id": two["id"], "is_cancelled": False, "status": "SEBAGIAN", "total_qty": 300, "line_items": [{"line_idx": 0, "qty": 300}]},
        {"id": str(uuid.uuid4()), "po_id": cancelled_flag["id"], "is_cancelled": False, "cancelled": True, "status": "SELESAI", "total_qty": 1000, "line_items": [{"line_idx": 0, "qty": 1000}]},
        {"id": str(uuid.uuid4()), "po_id": cancelled_status["id"], "is_cancelled": False, "cancelled": False, "status": "cancelled", "total_qty": 1000, "line_items": [{"line_idx": 0, "qty": 1000}]},
        {"id": str(uuid.uuid4()), "po_id": dibatalkan["id"], "is_cancelled": False, "cancelled": False, "status": "DIBATALKAN", "total_qty": 1000, "line_items": [{"line_idx": 0, "qty": 1000}]},
        {"id": str(uuid.uuid4()), "po_id": missing_idx["id"], "is_cancelled": False, "status": "SELESAI", "total_qty": 400, "line_items": [{"qty": 400}]},
        {"id": str(uuid.uuid4()), "po_id": multi["id"], "is_cancelled": False, "status": "SELESAI", "total_qty": 400, "line_items": [{"line_idx": 0, "qty": 400}]},
    ])
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-orders", params={"q": marker, "status": "gabungan"})
            assert listed.status_code == 200
            by_id = {row["id"]: row for row in listed.json()["orders"]}
            assert by_id[none["id"]]["list_tab"] == "active"
            assert by_id[none["id"]]["delivery_summary"]["total_remaining"] == 1000
            assert by_id[two["id"]]["delivery_summary"]["total_shipped"] == 700
            assert by_id[two["id"]]["delivery_summary"]["total_remaining"] == 300
            assert by_id[dibatalkan["id"]]["delivery_summary"]["total_shipped"] == 1000
            assert by_id[dibatalkan["id"]]["delivery_summary"]["total_remaining"] == 0
            assert by_id[dibatalkan["id"]]["delivery_progress"]["total_shipped"] == 0
            assert by_id[missing_idx["id"]]["delivery_summary"]["total_shipped"] == 400
            assert by_id[multi["id"]]["delivery_summary"]["total_ordered"] == 1002
            assert by_id[multi["id"]]["delivery_summary"]["total_shipped"] == 400
            assert by_id[multi["id"]]["delivery_summary"]["total_remaining"] == 602
            assert by_id[multi["id"]]["delivery_progress"]["total_ordered"] is None
            assert by_id[multi["id"]]["delivery_progress"]["units"] == [
                {"unit": "pcs", "ordered": 1000, "shipped": 0},
                {"unit": "dus", "ordered": 2, "shipped": 0},
            ]
            assert by_id[multi["id"]]["delivery_summary"]["total_remaining"] == 602
            for row in by_id.values():
                detail = await get_po_delivery_summary(server.db, row["id"])
                _assert_list_gate_matches_detail(row, detail)
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_list_progress_reads_confirmation_and_gate_stays_on_sj(bind_motor):
    import server
    from services.sales_deliveries import get_po_delivery_summary
    marker = uuid.uuid4().hex[:8].upper()
    confirmed = _order(marker=f"K{marker}")
    loading_only = _order(marker=f"L{marker}")
    sj_id = str(uuid.uuid4())
    confirmed["delivery_confirmations"] = [{
        "id": "conf-1",
        "action_id": "act-1",
        "shipment_id": sj_id,
        "qty": 400,
        "items": [{"line_idx": 0, "qty": 400}],
        "confirmed_at": "2026-10-15T00:00:00Z",
    }]
    confirmed["shipments"] = [{
        "id": sj_id,
        "sales_delivery_id": sj_id,
        "delivery_status": "Terkirim",
        "delivered_at": "2026-10-15T00:00:00Z",
        "lines": [{"line_idx": 0, "qty": 400}],
    }]
    loading_only["loading_confirmations"] = [{
        "id": "load-1",
        "shipment_id": "sj-load",
        "lines": [{"line_idx": 0, "qty": 1000}],
    }]
    loading_only["loading_checklist"] = {"driver_user_id": "driver-1", "driver_name": "Driver"}
    await server.db.orders.insert_many([confirmed, loading_only])
    await server.db.sales_deliveries.insert_many([
        {
            "id": sj_id,
            "po_id": confirmed["id"],
            "sj_number": f"SJ-{marker}",
            "is_cancelled": False,
            "status": "SELESAI",
            "total_qty": 400,
            "line_items": [{"line_idx": 0, "qty": 400}],
        },
        {
            "id": str(uuid.uuid4()),
            "po_id": loading_only["id"],
            "sj_number": f"SJ-L-{marker}",
            "is_cancelled": False,
            "status": "SELESAI",
            "total_qty": 1000,
            "line_items": [{"line_idx": 0, "qty": 1000}],
        },
    ])
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-orders", params={"q": marker, "status": "gabungan"})
            assert listed.status_code == 200
            by_id = {row["id"]: row for row in listed.json()["orders"]}
            row = by_id[confirmed["id"]]
            assert row["delivery_progress"]["total_shipped"] == 400
            assert row["delivery_progress"]["status"] == "SEBAGIAN"
            assert row["delivery_summary"]["total_shipped"] == 400
            assert row["delivery_summary"]["total_remaining"] == 600
            assert row["current_stage"] == 11
            detail = await get_po_delivery_summary(server.db, confirmed["id"])
            _assert_list_gate_matches_detail(row, detail)
            loaded = by_id[loading_only["id"]]
            assert loaded["delivery_progress"]["total_shipped"] == 0
            assert loaded["delivery_summary"]["total_shipped"] == 1000
            assert loaded["delivery_summary"]["total_remaining"] == 0
            detail_loaded = await get_po_delivery_summary(server.db, loading_only["id"])
            _assert_list_gate_matches_detail(loaded, detail_loaded)
    finally:
        app.dependency_overrides.pop(current_user, None)
