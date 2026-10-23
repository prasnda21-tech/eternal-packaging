"""Production dashboard snapshot and layout isolation. Local fixture data only."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_production_dashboard_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.finance_widget_layout import COLLECTION, LayoutError, get_finance_layout, save_finance_layout
from services.production_dashboard_snapshot import build_production_snapshot
from services.production_widget_layout import PRODUCTION_SPEC, get_production_layout, save_production_layout

pytestmark = pytest.mark.anyio
WIB = ZoneInfo("Asia/Jakarta")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def clean_db(bind_motor):
    await server.db[COLLECTION].delete_many({})
    await server.db.orders.delete_many({})
    yield
    await server.db[COLLECTION].delete_many({})
    await server.db.orders.delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _line(name, qty, unit, line_id):
    return {
        "name": name,
        "quantity": qty,
        "unit": unit,
        "production_process": "print",
        "line_id": line_id,
    }


def _order(oid, **extra):
    base = {
        "id": oid,
        "po_number": oid.upper(),
        "cancelled": False,
        "current_stage": 8,
        "priority": "normal",
        "urgent": False,
        "design_status": "Approved",
        "design_bypassed": False,
        "design_files": [{"id": "design"}],
        "payment_status": "Approved",
        "payment_bypassed": False,
        "payment_files": [{"id": "pay"}],
        "qty_delivered": 0,
        "buffer_stock": False,
        "hold": False,
        "delivery_deadline_status": "normal",
        "line_items": [_line("Cup", 100, "pcs", f"{oid}-0")],
        "operator_splits": [],
        "shipments": [],
    }
    base.update(extra)
    return base


def _snapshot_orders():
    return [
        _order(
            "po-multi",
            urgent=True,
            qty_delivered=10,
            shipments=[{"id": "sj-1"}, {"id": "sj-2"}],
            line_items=[_line("Cup", 100, "pcs", "po-multi-0"), _line("Dus", 8, "pack", "po-multi-1")],
            operator_splits=[
                {"line_idx": 0, "quantity": 40, "qty_completed": 10, "operator_email": "a@x", "operator_name": "A"},
                {"line_idx": 0, "quantity": 20, "qty_completed": 5, "operator_email": "b@x", "operator_name": "B"},
                {"quantity": 999, "qty_completed": 999, "operator_email": "c@x", "operator_name": "C"},
                {"line_idx": 1, "quantity": 4, "qty_completed": 3, "operator_email": "a@x", "operator_name": "A"},
            ],
        ),
        _order(
            "po-urgent",
            priority="urgent",
            delivery_deadline_status="h6",
            line_items=[_line("Cup", 10, "pcs", "po-urgent-0")],
            operator_splits=[{"line_idx": 0, "quantity": 10, "qty_completed": 10, "operator_email": "a@x", "operator_name": "A"}],
        ),
        _order("po-h1", delivery_deadline_status="h1", line_items=[_line("Cup", 4, "pcs", "po-h1-0")], operator_splits=[
            {"line_idx": 0, "quantity": 4, "qty_completed": 4, "operator_email": "a@x", "operator_name": "A"},
        ]),
        _order(
            "po-hold",
            current_stage=2,
            hold=True,
            design_status="Waiting",
            design_files=[],
            payment_status="Waiting",
            payment_files=[],
            line_items=[_line("Cup", 4, "pcs", "po-hold-0"), _line("Cup B", 4, "pcs", "po-hold-1")],
        ),
        _order("po-done", qty_delivered=50, current_stage=14, priority="urgent", line_items=[_line("Cup", 50, "pcs", "po-done-0")]),
        _order("po-buffer", buffer_stock=True, delivery_deadline_status="normal"),
    ]


def test_snapshot_keeps_unique_po_units_and_windows():
    body = build_production_snapshot(_snapshot_orders(), generated_at="2026-10-23T06:00:00+00:00")
    assert body["po_aktif"]["count"] == 5
    assert body["po_urgent"]["count"] == 1
    assert body["po_urgent"]["rule"] == "priority"
    assert body["po_hold"]["count"] == 1
    assert body["perlu_produksi"]["count"] == 3
    assert body["sisa_qty"]["value"] is None
    assert body["sisa_qty"]["by_unit"]["pcs"] == 193
    assert body["sisa_qty"]["by_unit"]["pack"] == 5
    assert "count" not in body["sisa_qty"]
    assert body["deadline"]["within_6h"] == 1
    assert body["deadline"]["within_24h"] == 2
    assert body["approval"]["design"] == 1
    assert body["approval"]["payment"] == 1
    assert body["approval"]["either_unique"] == 1
    rows = body["prioritas"]["rows"]
    assert rows
    assert all(row.get("order_id") and row.get("line_idx") is not None for row in rows)
    multi = [row for row in rows if row["order_id"] == "po-multi"]
    assert {row["line_idx"] for row in multi} == {0, 1}
    assert body["timezone"] == "Asia/Jakarta"
    kesiapan = body["kesiapan"]
    assert kesiapan["status"] == "complete"
    assert kesiapan["siap_produksi"] == {
        "ready": 4,
        "not_ready": 1,
        "hold_ready": 0,
        "note": "",
    }
    assert kesiapan["hasil_cetak"]["complete"] == 2
    assert kesiapan["hasil_cetak"]["partial"] == 1
    assert kesiapan["hasil_cetak"]["none"] == 2
    assert kesiapan["hasil_cetak"]["not_applicable"] == 0


@pytest.mark.asyncio
async def test_layout_keys_do_not_overwrite_each_other(clean_db):
    user_a = {"user_id": "user-a", "id": "user-a"}
    user_b = {"user_id": "user-b", "id": "user-b"}
    finance = {"dashboard.finance.view"}
    production = {"dashboard.production.view"}
    finance_saved = await save_finance_layout(server.db, user_a, finance, 0, [
        {"widget_id": "cash", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {"period_mode": "global"}},
    ])
    production_saved = await save_production_layout(server.db, user_a, production, 0, [
        {"widget_id": "po_aktif", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {}},
    ])
    assert finance_saved["dashboard_key"] == "keuangan"
    assert production_saved["dashboard_key"] == "produksi"
    assert production_saved["version"] == 1
    again = await save_production_layout(server.db, user_a, production, 1, [
        {"widget_id": "po_hold", "x": 3, "y": 0, "w": 3, "h": 2, "settings": {}},
    ])
    assert again["version"] == 2
    with pytest.raises(LayoutError) as stale:
        await save_production_layout(server.db, user_a, production, 1, [
            {"widget_id": "po_aktif", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {}},
        ])
    assert stale.value.status_code == 409
    finance_view = await get_finance_layout(server.db, user_a, finance)
    assert finance_view["version"] == 1
    assert [row["widget_id"] for row in finance_view["widgets"]] == ["cash"]
    other = await get_production_layout(server.db, user_b, production)
    assert other["version"] == 0
    assert other["dashboard_key"] == "produksi"
    from services.finance_widget_layout import default_widgets

    expected = [row["widget_id"] for row in default_widgets(production, PRODUCTION_SPEC)]
    assert "output_operator" not in expected
    assert [row["widget_id"] for row in other["widgets"]] == expected
    stored = await server.db[COLLECTION].find({"user_id": "user-a"}, {"_id": 0, "dashboard_key": 1, "version": 1}).to_list(5)
    assert {row["dashboard_key"]: row["version"] for row in stored} == {"keuangan": 1, "produksi": 2}


@pytest.mark.asyncio
async def test_snapshot_route_is_read_only_and_permissioned(clean_db):
    tomorrow = (datetime.now(WIB) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    past = (datetime.now(WIB) - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    active = _order(
        "po-live",
        qty_delivered=5,
        delivery_deadline_manual=tomorrow.isoformat(),
        line_items=[_line("Cup", 20, "pcs", "po-live-0")],
        operator_splits=[{"line_idx": 0, "quantity": 20, "qty_completed": 4, "operator_email": "a@x", "operator_name": "A"}],
    )
    buffered = _order(
        "po-buffer-live",
        buffer_stock=True,
        delivery_deadline_manual=past.isoformat(),
        line_items=[_line("Cup", 6, "pcs", "buf-0")],
    )
    await server.db.orders.insert_many([active, buffered])
    before = await server.db.orders.find({}, {"_id": 0}).to_list(5)

    async def viewer():
        return {
            "id": "viewer",
            "user_id": "viewer",
            "email": "viewer@example.com",
            "role": "helper",
            "status": "active",
            "permissions_override": {"dashboard.production.view": True},
        }

    app.dependency_overrides[current_user] = viewer
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.get("/api/dashboard/produksi/snapshot")
        # helper role default does not include the permission until override is read.
        # The override above includes it, so this call is the allowed one.
        assert denied.status_code == 200
        body = denied.json()
        forbidden_user = {
            "id": "none",
            "user_id": "none",
            "email": "none@example.com",
            "role": "helper",
            "status": "active",
            "permissions_override": {},
        }

        async def no_access():
            return forbidden_user

        app.dependency_overrides[current_user] = no_access
        blocked = await client.get("/api/dashboard/produksi/snapshot")
    assert blocked.status_code == 403
    assert "siap_produksi" not in blocked.text
    assert body["po_aktif"]["count"] == 2
    assert body["kesiapan"]["siap_produksi"]["ready"] == 2
    assert body["kesiapan"]["siap_produksi"]["not_ready"] == 0
    assert body["kesiapan"]["hasil_cetak"]["partial"] == 1
    assert body["kesiapan"]["hasil_cetak"]["none"] == 1
    assert body["deadline"]["within_24h"] == 1
    assert body["deadline"]["within_6h"] in (0, 1)
    assert body["sisa_qty"]["by_unit"].get("pcs") == 22
    after = await server.db.orders.find({}, {"_id": 0}).sort("id", 1).to_list(5)
    before = sorted(before, key=lambda row: row["id"])
    assert after == before


def test_kesiapan_separates_approvals_hold_and_confirmed_print():
    both = _order("both")
    one_pending = _order("one-pending", payment_status="Waiting", payment_files=[])
    both_pending = _order(
        "both-pending",
        design_status="Waiting",
        design_files=[],
        payment_status="Waiting",
        payment_files=[],
    )
    bypass = _order(
        "bypass",
        design_status="Waiting",
        design_files=[],
        design_bypassed=True,
        payment_status="Waiting",
        payment_files=[],
        payment_bypassed=True,
    )
    customer = _order(
        "customer",
        design_status="Waiting",
        design_files=[],
        customer_approval={"status": "approved"},
    )
    hold_ready = _order("hold-ready", hold=True)
    hold_open = _order(
        "hold-open",
        hold=True,
        design_status="Waiting",
        design_files=[],
        payment_status="Waiting",
        payment_files=[],
    )
    multi = _order(
        "multi",
        line_items=[_line("Cup", 10, "pcs", "multi-0"), _line("Dus", 4, "pack", "multi-1")],
        operator_splits=[
            {"line_idx": 0, "quantity": 10, "qty_completed": 10},
            {"line_idx": 1, "quantity": 4, "qty_completed": 1},
        ],
        shipments=[{"id": "sj-1"}, {"id": "sj-2"}],
    )
    two_ops = _order(
        "two-ops",
        line_items=[_line("Cup", 10, "pcs", "two-0")],
        operator_splits=[
            {"line_idx": 0, "quantity": 6, "qty_completed": 6, "operator_email": "a@x"},
            {"line_idx": 0, "quantity": 4, "qty_completed": 4, "operator_email": "b@x"},
            {"quantity": 50, "qty_completed": 50},
        ],
    )
    progress_only = _order(
        "progress-only",
        line_items=[_line("Cup", 10, "pcs", "progress-0")],
        operator_splits=[{"line_idx": 0, "quantity": 10, "qty_completed": 6}],
        progress_entries=[{"line_idx": 0, "qty_completed": 4}],
    )
    allocated = _order(
        "allocated",
        line_items=[_line("Cup", 10, "pcs", "alloc-0")],
        operator_splits=[{"line_idx": 0, "quantity": 10, "qty_completed": 0}],
    )
    partial = _order(
        "partial",
        line_items=[_line("Cup", 10, "pcs", "partial-0")],
        operator_splits=[{"line_idx": 0, "quantity": 10, "qty_completed": 4}],
    )
    full = _order(
        "full",
        line_items=[_line("Cup", 10, "pcs", "full-0")],
        operator_splits=[{"line_idx": 0, "quantity": 10, "qty_completed": 10}],
    )
    legacy = _order(
        "legacy",
        line_items=[{"name": "Cup", "quantity": 5, "unit": "pcs", "production_process": "", "line_id": "legacy-0"}],
        operator_splits=[{"line_idx": 0, "quantity": 5, "qty_completed": 5}],
    )
    no_lines = _order("no-lines", line_items=[])
    non_print = _order("non-print", line_items=[{
        "name": "Jasa",
        "quantity": 5,
        "unit": "pcs",
        "production_process": "non_print",
        "line_id": "np-0",
    }])
    bad_qty = _order("bad-qty", line_items=[_line("Cup", None, "pcs", "bad-0")])
    zero_qty = _order("zero-qty", line_items=[_line("Cup", 0, "pcs", "zero-0")])
    delivered = _order("done", qty_delivered=100, current_stage=14)
    body = build_production_snapshot([
        both,
        one_pending,
        both_pending,
        bypass,
        customer,
        hold_ready,
        hold_open,
        multi,
        two_ops,
        progress_only,
        allocated,
        partial,
        full,
        legacy,
        no_lines,
        non_print,
        bad_qty,
        zero_qty,
        delivered,
        dict(delivered),
        _order("both"),
    ])
    siap = body["kesiapan"]["siap_produksi"]
    cetak = body["kesiapan"]["hasil_cetak"]
    assert body["kesiapan"]["status"] == "complete"
    assert siap["ready"] == 15
    assert siap["not_ready"] == 3
    assert siap["hold_ready"] == 1
    assert siap["ready"] + siap["not_ready"] == 18
    assert "bukan izin menjadwalkan" in siap["note"]
    assert cetak["complete"] == 3
    assert cetak["partial"] == 3
    assert cetak["none"] == 8
    assert cetak["not_applicable"] == 4
    assert cetak["complete"] + cetak["partial"] + cetak["none"] + cetak["not_applicable"] == 18
    assert "tidak ada line cetak" in cetak["note"]
    assert "done" not in str(body["kesiapan"])
    empty = build_production_snapshot([])
    assert empty["kesiapan"]["status"] == "zero"
    assert empty["po_aktif"]["count"] == 0


@pytest.mark.asyncio
async def test_kesiapan_is_placeable_and_absent_from_the_default_layout(clean_db):
    from services.finance_widget_layout import default_widgets

    user = {"user_id": "user-k", "id": "user-k"}
    perms = {"dashboard.production.view"}
    default_ids = [row["widget_id"] for row in default_widgets(perms, PRODUCTION_SPEC)]
    assert "kesiapan" not in PRODUCTION_SPEC["order"]
    assert "kesiapan" not in default_ids
    assert PRODUCTION_SPEC["ready"]["kesiapan"] == "dashboard.production.view"
    saved = await save_production_layout(server.db, user, perms, 0, [
        {"widget_id": "kesiapan", "x": 0, "y": 0, "w": 8, "h": 4, "settings": {}},
    ])
    assert [row["widget_id"] for row in saved["widgets"]] == ["kesiapan"]
    stored = await get_production_layout(server.db, user, perms)
    assert [row["widget_id"] for row in stored["widgets"]] == ["kesiapan"]
    fresh = await get_production_layout(server.db, {"user_id": "user-new", "id": "user-new"}, perms)
    assert "kesiapan" not in [row["widget_id"] for row in fresh["widgets"]]
