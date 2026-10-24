"""Wave 1 production analytics. Local fixtures only. No production data."""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_production_dashboard_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import PERMISSION_CATALOG, ROLE_DEFAULT_PERMISSIONS, director_full_permission_set

pytestmark = pytest.mark.anyio
from server import app, current_user
from services.finance_widget_layout import COLLECTION, LayoutError
from services.production_analytics import build_operator_analytics, build_plant_analytics
from services.production_widget_layout import PRODUCTION_SPEC

TODAY = date(2026, 10, 23)
PACK = {
    "master_item_id": "master-pack",
    "transaction_uom": "PACK",
    "base_uom": "PCS",
    "conversion_factor": 10,
}


def _line(name, qty, unit, line_id, master_id=""):
    return {
        "name": name,
        "quantity": qty,
        "unit": unit,
        "production_process": "print",
        "line_id": line_id,
        "master_item_id": master_id,
    }


def _entry(qty, at, action_id, **extra):
    row = {"qty": qty, "confirmed_at": at, "action_id": action_id, "legacy": False}
    row.update(extra)
    return row


def _split(email, name, line_idx, quantity, completed, entries, user_id=""):
    return {
        "operator_user_id": user_id,
        "operator_email": email,
        "operator_name": name,
        "line_idx": line_idx,
        "quantity": quantity,
        "qty_completed": completed,
        "progress_entries": entries,
    }


def _order(oid, **extra):
    base = {
        "id": oid,
        "po_number": oid.upper(),
        "cancelled": False,
        "current_stage": 8,
        "design_status": "Approved",
        "design_bypassed": False,
        "design_files": [{"id": "design"}],
        "payment_status": "Approved",
        "payment_bypassed": False,
        "payment_files": [{"id": "pay"}],
        "operator_splits": [],
        "line_items": [_line("Cup", 100, "pcs", f"{oid}-0")],
        "sample_approved": True,
        "screener_info": {"screen_qty": 1},
        "helper_prep": {"qty_prepared": 1},
    }
    base.update(extra)
    return base


def _plant(orders, days=7):
    return build_plant_analytics(orders, days=days, today=TODAY, generated_at="2026-10-23T01:00:00+00:00")


def _ops(orders, days=7):
    return build_operator_analytics(orders, days=days, today=TODAY, generated_at="2026-10-23T01:00:00+00:00")


def test_two_operators_one_line_and_wib_boundary():
    order = _order(
        "po-line",
        operator_splits=[
            _split("a@x", "Operator A", 0, 100, 6, [
                _entry(6000, "2026-10-22T17:30:00+00:00", "act-a"),
            ], user_id="user-a"),
            _split("b@x", "Operator B", 0, 100, 4, [
                _entry(4000, "2026-10-23T02:00:00+00:00", "act-b", created_by="supervisor@x"),
            ], user_id="user-b"),
        ],
    )
    body = _plant([order])
    assert body["today"]["status"] == "complete"
    assert body["today"]["by_unit"] == {"pcs": 10000}
    assert body["today"]["updated_at"].startswith("2026-10-23T")
    ops = _ops([order])
    ranking = {row["name"]: row["qty"] for row in ops["performance"]["ranking"]["pcs"]}
    assert ranking == {"Operator A": 6000, "Operator B": 4000}
    assert "supervisor@x" not in ranking
    assert ops["performance"]["target_available"] is False
    assert "%" not in ops["performance"]["note"]
    assert "Target Awal" in ops["performance"]["note"]


def test_two_lines_one_po_counts_unique_po_and_lines():
    order = _order(
        "po-two",
        line_items=[_line("Cup", 10, "pcs", "a", ""), _line("Dus", 4, "pack", "b", "")],
        operator_splits=[
            _split("a@x", "Operator A", 0, 10, 2, [_entry(2, "2026-10-23T01:00:00+00:00", "l0")], "user-a"),
            _split("a@x", "Operator A", 1, 4, 1, [_entry(1, "2026-10-23T01:00:00+00:00", "l1")], "user-a"),
        ],
    )
    ops = _ops([order])
    row = ops["workload"]["operators"][0]
    assert row["active_po"] == 1
    assert row["active_lines"] == 2
    assert ops["performance"]["ranking"]["pcs"][0]["qty"] == 2
    assert ops["performance"]["ranking"]["pack"][0]["qty"] == 1
    workflow = _plant([order])["workflow"]["per_po"]["full"]
    assert workflow["total"] == 1
    assert sum(bar["count"] for bar in workflow["bars"]) == 1


def test_duplicate_cancelled_replaced_and_legacy_entries():
    order = _order(
        "po-clean",
        operator_splits=[_split("a@x", "Operator A", 0, 20, 9, [
            _entry(5, "2026-10-23T01:00:00+00:00", "same"),
            _entry(9, "2026-10-23T02:00:00+00:00", "same"),
            _entry(7, "2026-10-23T03:00:00+00:00", "gone", cancelled=True),
            _entry(8, "2026-10-23T03:00:00+00:00", "old", replaced=True),
            _entry(100, "2026-10-23T04:00:00+00:00", "legacy-row", legacy=True),
        ], "user-a")],
    )
    body = _plant([order])
    assert body["today"]["by_unit"] == {"pcs": 5}
    assert body["coverage"]["duplicate"] == 1
    assert body["coverage"]["cancelled"] == 2
    assert body["coverage"]["legacy"] == 1
    assert body["history_complete"] is False
    legacy_only = _order(
        "po-legacy",
        operator_splits=[_split("a@x", "Operator A", 0, 10, 10, [
            _entry(10, "2026-10-23T01:00:00+00:00", "legacy-only", legacy=True),
        ])],
    )
    legacy = _plant([legacy_only])
    assert legacy["today"]["status"] == "incomplete"
    assert legacy["today"]["by_unit"] == {}
    assert legacy["today"]["note"] == "Histori konfirmasi tidak tersedia."
    assert legacy["trend"]["series"] == {}


def test_units_conversion_and_blank_unit_stay_separate():
    orders = [
        _order(
            "po-pcs",
            line_items=[_line("Cup", 10, "pcs", "p", "")],
            operator_splits=[_split("a@x", "A", 0, 10, 6, [_entry(6, "2026-10-23T01:00:00+00:00", "pcs")])],
        ),
        _order(
            "po-pack",
            line_items=[_line("Dus", 4, "pack", "k", "master-pack")],
            operator_splits=[_split("a@x", "A", 0, 4, 2, [_entry(2, "2026-10-23T01:00:00+00:00", "pack")])],
        ),
        _order(
            "po-raw",
            line_items=[_line("Roll", 3, "roll", "r", "")],
            operator_splits=[_split("a@x", "A", 0, 3, 3, [_entry(3, "2026-10-23T01:00:00+00:00", "roll")])],
        ),
        _order(
            "po-blank",
            line_items=[{"name": "Blank", "quantity": 5, "unit": "", "production_process": "print", "line_id": "blank"}],
            operator_splits=[_split("a@x", "A", 0, 5, 5, [_entry(5, "2026-10-23T01:00:00+00:00", "blank")])],
        ),
    ]
    body = _plant(orders)
    assert body["today"]["by_unit"]["pcs"] == 6
    assert body["today"]["by_unit"]["pack"] == 2
    assert body["today"]["by_unit"]["roll"] == 3
    assert "" not in body["today"]["by_unit"]
    assert body["conversion"]["status"] == "unverified"
    assert "belum terverifikasi" in body["today"]["note"]
    assert body["coverage"]["no_unit"] == 1
    raw = _plant([orders[2]])
    assert raw["today"]["by_unit"] == {"roll": 3}
    assert raw["conversion"]["status"] == "unverified"
    assert set(body["trend"]["series"]) == {"pcs", "pack", "roll"}
    assert PACK["conversion_factor"] == 10


def test_trend_period_uses_confirmation_day_not_schedule():
    order = _order(
        "po-trend",
        work_date="2026-10-01",
        operator_splits=[_split("a@x", "A", 0, 30, 9, [
            _entry(4, "2026-10-23T01:00:00+00:00", "today"),
            _entry(5, "2026-10-10T01:00:00+00:00", "older"),
        ])],
    )
    week = _plant([order], days=7)
    month = _plant([order], days=30)
    assert [point["qty"] for point in week["trend"]["series"]["pcs"]][-1] == 4
    assert sum(point["qty"] for point in week["trend"]["series"]["pcs"]) == 4
    assert sum(point["qty"] for point in month["trend"]["series"]["pcs"]) == 9
    assert len(week["trend"]["series"]["pcs"]) == 7
    assert len(month["trend"]["series"]["pcs"]) == 30
    assert order["work_date"] == "2026-10-01"


def test_revised_allocation_keeps_previous_output():
    order = _order(
        "po-revise",
        operator_splits=[
            _split("a@x", "Operator A", 0, 50, 40, [_entry(40, "2026-10-01T01:00:00+00:00", "kept")], "user-a"),
            _split("b@x", "Operator B", 0, 60, 0, [], "user-b"),
        ],
    )
    ops = _ops([order], days=30)
    assert ops["performance"]["ranking"]["pcs"][0]["name"] == "Operator A"
    assert ops["performance"]["ranking"]["pcs"][0]["qty"] == 40
    names = {row["name"]: row for row in ops["workload"]["operators"]}
    assert names["Operator A"]["remaining_by_unit"] == {"pcs": 10}
    assert names["Operator B"]["remaining_by_unit"] == {"pcs": 60}
    assert names["Operator A"]["active_po"] == 1
    assert "Operator A" not in {row["name"] for row in _ops([order], days=7)["performance"]["ranking"].get("pcs", [])}


def test_snapshot_qty_completed_is_not_added_to_entries():
    order = _order(
        "po-snap",
        operator_splits=[_split("a@x", "A", 0, 100, 999, [_entry(10, "2026-10-23T01:00:00+00:00", "only")])],
    )
    assert _plant([order])["today"]["by_unit"] == {"pcs": 10}


def test_workflow_hold_dual_approval_and_unique_po():
    hold = _order(
        "po-hold",
        current_stage=2,
        design_status="Waiting",
        design_files=[],
        payment_status="Waiting",
        payment_files=[],
        sample_approved=False,
        screener_info={},
        helper_prep={},
        operator_splits=[],
    )
    both = _order(
        "po-both",
        current_stage=6,
        design_status="Waiting",
        payment_status="Waiting",
        sample_approved=False,
        screener_info={},
        helper_prep={},
    )
    active = _order(
        "po-active",
        line_items=[_line("Cup", 10, "pcs", "a"), _line("Cup", 10, "pcs", "b")],
        sample_approved=False,
        screener_info={},
        helper_prep={},
        operator_splits=[_split("a@x", "A", 0, 10, 0, []), _split("a@x", "A", 1, 10, 0, [])],
    )
    body = _plant([hold, both, active])["workflow"]
    full = body["per_po"]["full"]
    counts = {bar["stage"]: bar["count"] for bar in full["bars"]}
    assert counts["HOLD"] == 1
    assert counts["Menunggu Approval"] == 1
    assert full["approval_detail"] == {"design_waiting": 1, "payment_waiting": 1, "either_unique": 1}
    assert full["total"] == 3
    assert sum(bar["count"] for bar in full["bars"]) == 3
    production = body["per_po"]["production"]
    assert production["total"] == 1
    assert production["bars"] == [{"stage": "Screener", "count": 1}]
    assert body["per_line"]["status"] == "unavailable"
    assert "per line" in body["per_line"]["note"]


def test_zero_activity_is_not_a_legacy_gap():
    order = _order("po-empty", operator_splits=[])
    body = _plant([order])
    assert body["today"]["status"] == "zero"
    assert body["today"]["by_unit"] == {}
    assert "tidak tersedia" not in body["today"]["note"]


def test_permission_catalog_does_not_grant_operator_analytics_by_default():
    keys = [key for _module, key, _label in PERMISSION_CATALOG]
    assert "dashboard.production.operator_analytics" in keys
    assert keys.count("dashboard.production.operator_analytics") == 1
    for role_keys in ROLE_DEFAULT_PERMISSIONS.values():
        assert "dashboard.production.operator_analytics" not in role_keys
    assert "dashboard.production.operator_analytics" not in director_full_permission_set()
    assert "dashboard.production.branch_output" in keys
    assert "dashboard.production.machine_view" not in keys
    assert "dashboard.production.machine_manage" not in keys
    for _role, role_keys in ROLE_DEFAULT_PERMISSIONS.items():
        assert "dashboard.production.branch_output" not in role_keys
    assert "dashboard.production.branch_output" not in director_full_permission_set()


def test_line_snapshot_survives_a_later_master_factor():
    snapshot = _line("Dus", 10, "pack", "snap", "master-pack")
    snapshot.update({"conversion_factor": 50, "base_uom": "PCS", "transaction_uom": "PACK"})
    changed_master = dict(PACK, conversion_factor=100)
    order = _order(
        "po-snap-factor",
        line_items=[snapshot],
        operator_splits=[_split("a@x", "A", 0, 10, 0, [_entry(10, "2026-10-23T01:00:00+00:00", "snap-act")])],
    )
    body = _plant([order])
    assert body["today"]["by_unit"] == {"pcs": 500}
    assert body["conversion"]["status"] == "snapshot"
    assert "belum terverifikasi" not in body["today"]["note"]
    assert changed_master["conversion_factor"] == 100
    assert order["line_items"][0]["conversion_factor"] == 50


def test_missing_transaction_factor_keeps_the_original_unit():
    order = _order(
        "po-pack-only",
        line_items=[_line("Dus", 4, "pack", "k", "master-pack")],
        operator_splits=[_split("a@x", "A", 0, 4, 2, [_entry(2, "2026-10-23T01:00:00+00:00", "pack-only")])],
    )
    body = _plant([order])
    ops = _ops([order])
    assert body["today"]["by_unit"] == {"pack": 2}
    assert "pcs" not in body["today"]["by_unit"]
    assert body["conversion"]["status"] == "unverified"
    assert body["today"]["note"] == "Konversi historis belum terverifikasi. Qty ditampilkan dalam satuan asli."
    assert ops["performance"]["ranking"]["pack"][0]["qty"] == 2
    assert "pcs" not in ops["performance"]["ranking"]
    assert ops["workload"]["operators"][0]["remaining_by_unit"] == {"pack": 2}
    assert order["line_items"][0]["unit"] == "pack"


def test_operator_widget_is_absent_without_its_permission():
    assert list(PRODUCTION_SPEC["order"]) == [
        "perlu_produksi",
        "po_urgent",
        "po_hold",
        "po_aktif",
        "output_hari_ini",
        "tren_output",
        "sisa_qty",
        "deadline",
        "distribusi_tahap",
        "approval",
        "output_operator",
        "beban_operator",
        "prioritas",
    ]
    from services.finance_widget_layout import default_widgets

    view_only = [row["widget_id"] for row in default_widgets(["dashboard.production.view"], PRODUCTION_SPEC)]
    assert "output_operator" not in view_only
    assert "beban_operator" not in view_only
    assert view_only[-1] == "prioritas"
    both = [row["widget_id"] for row in default_widgets(
        ["dashboard.production.view", "dashboard.production.operator_analytics"],
        PRODUCTION_SPEC,
    )]
    assert both == list(PRODUCTION_SPEC["order"])
    assert PRODUCTION_SPEC["ready"]["output_hari_ini"] == "dashboard.production.view"
    assert PRODUCTION_SPEC["ready"]["output_operator"] == "dashboard.production.operator_analytics"
    with pytest.raises(LayoutError) as denied:
        save_finance_layout_sync_guard()
    assert denied.value.code == "LAYOUT_FORBIDDEN"


def save_finance_layout_sync_guard():
    raise_layout()


def raise_layout():
    from services.finance_widget_layout import validate_payload

    validate_payload(
        [{"widget_id": "output_operator", "x": 0, "y": 0, "w": 6, "h": 4, "settings": {}}],
        ["dashboard.production.view"],
        PRODUCTION_SPEC,
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def clean_db(bind_motor):
    await server.db.orders.delete_many({"id": {"$regex": "^wave1-"}})
    await server.db.product_unit_settings.delete_many({"master_item_id": "wave1-master"})
    yield
    await server.db.orders.delete_many({"id": {"$regex": "^wave1-"}})
    await server.db.product_unit_settings.delete_many({"master_item_id": "wave1-master"})
    app.dependency_overrides.pop(current_user, None)


def _user(user_id, **override):
    return {
        "id": user_id,
        "user_id": user_id,
        "email": f"{user_id}@example.com",
        "role": "helper",
        "status": "active",
        "permissions_override": override,
    }


@pytest.mark.asyncio
async def test_routes_are_permissioned_and_do_not_mutate(clean_db):
    order = _order(
        "wave1-po",
        line_items=[_line("Dus", 4, "pack", "wave", "wave1-master")],
        operator_splits=[_split("a@x", "Operator A", 0, 4, 1, [
            _entry(1, "2026-10-22T17:30:00+00:00", "wave-act"),
        ], "user-a")],
    )
    await server.db.orders.insert_one(dict(order))
    await server.db.product_unit_settings.insert_one({
        "master_item_id": "wave1-master",
        "transaction_uom": "PACK",
        "base_uom": "PCS",
        "conversion_factor": 10,
    })
    before_orders = await server.db.orders.find({"id": "wave1-po"}, {"_id": 0}).to_list(1)
    before_units = await server.db.product_unit_settings.find({"master_item_id": "wave1-master"}, {"_id": 0}).to_list(1)

    async def viewer():
        return _user("viewer", **{"dashboard.production.view": True})

    app.dependency_overrides[current_user] = viewer
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        plant = await client.get("/api/dashboard/produksi/analytics", params={"days": 7})
        denied = await client.get("/api/dashboard/produksi/operator-analytics", params={"days": 7})
        branch_denied = await client.get("/api/dashboard/produksi/branch-output", params={"days": 7})
        bad_period = await client.get("/api/dashboard/produksi/analytics", params={"days": 14})

        async def analyst():
            return _user("analyst", **{
                "dashboard.production.view": True,
                "dashboard.production.operator_analytics": True,
            })

        app.dependency_overrides[current_user] = analyst
        allowed = await client.get("/api/dashboard/produksi/operator-analytics", params={"days": 30})
        branch_still = await client.get("/api/dashboard/produksi/branch-output", params={"days": 7})

        async def branch_reader():
            return _user("branch", **{
                "dashboard.production.view": True,
                "dashboard.production.branch_output": True,
            })

        app.dependency_overrides[current_user] = branch_reader
        branch_ok = await client.get("/api/dashboard/produksi/branch-output", params={"days": 7})

        async def stranger():
            return _user("stranger")

        app.dependency_overrides[current_user] = stranger
        blocked = await client.get("/api/dashboard/produksi/analytics")

    assert plant.status_code == 200
    confirmed_day = "2026-10-23"
    today_units = plant.json()["today"]["by_unit"]
    if plant.json()["today_wib"] == confirmed_day:
        assert today_units == {"pack": 1}
    else:
        assert today_units == {}
        trend = plant.json()["trend"]["series"]["pack"]
        assert any(point["day"] == confirmed_day and point["qty"] == 1 for point in trend)
    assert plant.json()["conversion"]["status"] == "unverified"
    assert "pcs" not in plant.json()["today"]["by_unit"]
    assert denied.status_code == 403
    assert branch_denied.status_code == 403
    assert branch_still.status_code == 403
    assert branch_ok.status_code == 200
    assert "composition" in plant.json()
    assert bad_period.status_code == 400
    assert allowed.status_code == 200
    assert allowed.json()["performance"]["ranking"]["pack"][0]["qty"] == 1
    assert allowed.json()["performance"]["target_available"] is False
    assert blocked.status_code == 403
    after_orders = await server.db.orders.find({"id": "wave1-po"}, {"_id": 0}).to_list(1)
    after_units = await server.db.product_unit_settings.find({"master_item_id": "wave1-master"}, {"_id": 0}).to_list(1)
    assert after_orders == before_orders
    assert after_units == before_units


@pytest.mark.asyncio
async def test_new_default_layout_does_not_overwrite_a_saved_layout(clean_db):
    from services.production_widget_layout import get_production_layout, reset_production_layout, save_production_layout

    both = ["dashboard.production.view", "dashboard.production.operator_analytics"]
    user = {"id": "wave-user", "user_id": "wave-user", "email": "wave@example.com"}
    view = await get_production_layout(server.db, user, both)
    boxes = {row["widget_id"]: (row["x"], row["y"], row["w"], row["h"]) for row in view["widgets"]}
    assert [row["widget_id"] for row in view["widgets"]] == list(PRODUCTION_SPEC["order"])
    assert boxes["perlu_produksi"] == (0, 0, 3, 2)
    assert boxes["po_aktif"] == (9, 0, 3, 2)
    assert boxes["output_hari_ini"] == (0, 2, 4, 3)
    assert boxes["tren_output"] == (4, 2, 4, 3)
    assert boxes["sisa_qty"] == (8, 2, 4, 3)
    assert boxes["deadline"] == (0, 5, 4, 3)
    assert boxes["distribusi_tahap"] == (4, 5, 4, 3)
    assert boxes["approval"] == (8, 5, 4, 3)
    assert boxes["output_operator"] == (0, 8, 6, 3)
    assert boxes["beban_operator"] == (6, 8, 6, 3)
    assert boxes["prioritas"] == (0, 11, 12, 4)
    saved = await save_production_layout(
        server.db,
        user,
        ["dashboard.production.view"],
        view["version"],
        [{"widget_id": "po_hold", "x": 0, "y": 0, "w": 3, "h": 2, "settings": {}}],
    )
    assert saved["version"] == view["version"] + 1
    again = await get_production_layout(server.db, user, both)
    assert [row["widget_id"] for row in again["widgets"]] == ["po_hold"]
    with pytest.raises(LayoutError) as denied:
        await save_production_layout(
            server.db,
            user,
            ["dashboard.production.view"],
            saved["version"],
            saved["widgets"] + [{"widget_id": "beban_operator", "x": 6, "y": 0, "w": 6, "h": 3, "settings": {}}],
        )
    assert denied.value.code == "LAYOUT_FORBIDDEN"
    reset = await reset_production_layout(server.db, user, both)
    assert [row["widget_id"] for row in reset["widgets"]] == list(PRODUCTION_SPEC["order"])
    await server.db[COLLECTION].delete_many({"user_id": "wave-user"})
