"""Local Mongo integration: allocation recording + ATP + minimum (isolated DB)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_DB = "eternal_packaging_test_stock_allocation_pkg"
assert "eternal_packaging_preview" not in TEST_DB
assert "uat_r2_manual" not in TEST_DB
os.environ["DB_NAME"] = TEST_DB
# Isolated single-node replica set on 27018 (does not touch preview mongod :27017).
os.environ["MONGO_URL"] = os.environ.get(
    "STOCK_ALLOC_TEST_MONGO_URL",
    "mongodb://127.0.0.1:27018/?replicaSet=eternal_test_rs",
)
os.environ.setdefault("JWT_SECRET", "test-secret-key-stock-allocation-pkg-32chars")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("STORAGE_PROVIDER", "local")

MONGO_URL = os.environ["MONGO_URL"]
os.environ["MONGO_URL"] = MONGO_URL

import server  # noqa: E402
from server import app, current_user  # noqa: E402
from services.stock_allocation import (  # noqa: E402
    attach_allocations_to_lines,
    load_allowed_warehouses_for_branch,
    realization_mismatch_warning,
    summarize_order_allocation_warnings,
    WARNING_EMPTY,
    WARNING_OVER,
    WARNING_UNDER,
)
from services.stock_sellable import (  # noqa: E402
    collect_allocation_commitment,
    stock_dapat_dijual_branch,
    stock_dapat_dijual_warehouse,
)
from services.stock_minimum import (  # noqa: E402
    evaluate_minimum_status,
    ensure_indexes as ensure_minimum_indexes,
    upsert_minimum,
    list_minimums,
)
from services.min_stock_widget import build_min_stock_snapshot  # noqa: E402
from services.physical_stock import (  # noqa: E402
    LEDGER_COLLECTION,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_SALES_DELIVERY,
    ensure_indexes as ensure_physical_indexes,
    list_physical_stock_catalog,
    post_movement,
)
from services.r2_go_live import set_r2_authority_active  # noqa: E402
import httpx  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def fx():
    client = AsyncIOMotorClient(MONGO_URL)
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    server.client = client
    server.db = database
    await ensure_physical_indexes(database)
    await ensure_minimum_indexes(database)
    await set_r2_authority_active(database, active=True)

    branch_id = f"br-{uuid.uuid4().hex[:8]}"
    wh_a = f"wh-a-{uuid.uuid4().hex[:8]}"
    wh_b = f"wh-b-{uuid.uuid4().hex[:8]}"
    item = f"mi-{uuid.uuid4().hex[:8]}"
    await database.branches.insert_one(
        {"id": branch_id, "code": "YGY", "name": "Yogyakarta", "active": True},
    )
    await database.warehouses.insert_many([
        {
            "id": wh_a, "branch_id": branch_id, "code": "GUD-A", "name": "Gudang A",
            "active": True, "is_operational": True,
        },
        {
            "id": wh_b, "branch_id": branch_id, "code": "GUD-B", "name": "Gudang B",
            "active": True, "is_operational": True,
        },
    ])
    await database.master_items.insert_one({
        "id": item,
        "item_code": "ALLOC-001",
        "item_name": "Barang Alokasi Uji",
        "unit": "PCS",
        "item_type": "INVENTORY",
        "is_active": True,
        "accurate_item_id": f"LOCAL-{item[:8]}",
    })
    for wid, qty, key in ((wh_a, 100, "open-a"), (wh_b, 40, "open-b")):
        await post_movement(database, {
            "master_item_id": item,
            "branch_id": branch_id,
            "warehouse_id": wid,
            "movement_type": MOVEMENT_OPENING_BALANCE,
            "qty_delta": qty,
            "reference_type": "opening",
            "reference_id": key,
            "idempotency_key": key,
        }, actor_id="seed")

    yield {
        "db": database,
        "client": client,
        "branch_id": branch_id,
        "wh_a": wh_a,
        "wh_b": wh_b,
        "item": item,
    }
    await client.drop_database(TEST_DB)
    client.close()


def _line(item, qty, line_id, allocs=None):
    row = {
        "line_id": line_id,
        "master_item_id": item,
        "name": "Barang Alokasi Uji",
        "quantity": qty,
        "unit": "PCS",
        "item_type": "INVENTORY",
    }
    if allocs is not None:
        row["stock_allocations"] = allocs
    return row


async def test_attach_empty_under_over_succeed_with_warnings(fx):
    allowed = await load_allowed_warehouses_for_branch(fx["db"], fx["branch_id"])
    lines = [_line(fx["item"], 10, "ln-empty")]
    out, warns, complete = attach_allocations_to_lines(
        lines, branch_id=fx["branch_id"], allowed_warehouses=allowed, raw_by_index=[[]],
    )
    assert complete is False
    assert any(WARNING_EMPTY in (w.get("message") or "") for w in warns)
    assert out[0].get("stock_allocations") == []

    out2, warns2, _ = attach_allocations_to_lines(
        [_line(fx["item"], 10, "ln-under")],
        branch_id=fx["branch_id"],
        allowed_warehouses=allowed,
        raw_by_index=[[{"warehouse_id": fx["wh_a"], "qty": 3}]],
    )
    assert any(WARNING_UNDER in (w.get("message") or "") for w in warns2)
    assert out2[0]["stock_allocation_status"] == "under"

    out3, warns3, _ = attach_allocations_to_lines(
        [_line(fx["item"], 10, "ln-over")],
        branch_id=fx["branch_id"],
        allowed_warehouses=allowed,
        raw_by_index=[[{"warehouse_id": fx["wh_a"], "qty": 15}]],
    )
    assert any(WARNING_OVER in (w.get("message") or "") for w in warns3)
    assert out3[0]["stock_allocation_status"] == "over"


async def test_two_warehouses_one_line_and_branch_unallocated(fx):
    allowed = await load_allowed_warehouses_for_branch(fx["db"], fx["branch_id"])
    lines, warns, complete = attach_allocations_to_lines(
        [_line(fx["item"], 10, "ln-split")],
        branch_id=fx["branch_id"],
        allowed_warehouses=allowed,
        raw_by_index=[[
            {"warehouse_id": fx["wh_a"], "qty": 6},
            {"warehouse_id": fx["wh_b"], "qty": 4},
        ]],
    )
    assert complete is True and warns == []
    assert len(lines[0]["stock_allocations"]) == 2

    order = {
        "id": "ord-split",
        "cancelled": False,
        "operational_branch_id": fx["branch_id"],
        "line_items": lines,
    }
    # Partial deliver 2 from wh_a (plan remaining a=4, b=4; open=8)
    deliveries = [
        {"order_id": "ord-split", "line_id": "ln-split", "warehouse_id": fx["wh_a"], "qty": 2},
    ]
    snap = collect_allocation_commitment([order], deliveries)
    rem = snap["remaining_by_master_warehouse"][fx["item"]]
    assert rem[fx["wh_a"]] == 4
    assert rem[fx["wh_b"]] == 4
    assert snap["open_commit_by_master"][fx["item"]] == 8
    assert snap["unallocated_open_by_master"].get(fx["item"], 0) == 0

    # Incomplete: empty allocation keeps full open as unallocated; branch ATP subtracts it.
    empty_order = {
        "id": "ord-empty",
        "line_items": [_line(fx["item"], 12, "ln-e", allocs=[])],
    }
    snap2 = collect_allocation_commitment([empty_order], [])
    assert snap2["unallocated_open_by_master"][fx["item"]] == 12
    assert fx["item"] in snap2["incomplete_masters"]
    branch_atp = stock_dapat_dijual_branch(
        stock_book=140,
        open_so_commitment=snap2["open_commit_by_master"][fx["item"]],
    )
    assert branch_atp["qty"] == 128  # 140-12


async def test_partial_multi_sj_no_double_subtract_and_mismatch_warning(fx):
    order_id = f"ord-{uuid.uuid4().hex[:8]}"
    line_id = "ln1"
    lines, _, _ = attach_allocations_to_lines(
        [_line(fx["item"], 20, line_id)],
        branch_id=fx["branch_id"],
        allowed_warehouses=await load_allowed_warehouses_for_branch(fx["db"], fx["branch_id"]),
        raw_by_index=[[
            {"warehouse_id": fx["wh_a"], "qty": 12},
            {"warehouse_id": fx["wh_b"], "qty": 8},
        ]],
    )
    order = {
        "id": order_id,
        "cancelled": False,
        "operational_branch_id": fx["branch_id"],
        "branch_id": fx["branch_id"],
        "line_items": lines,
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    await fx["db"].orders.insert_one(dict(order))

    # SJ1 delivers 5 from planned wh_a
    d1 = [{"order_id": order_id, "line_id": line_id, "warehouse_id": fx["wh_a"], "qty": 5}]
    snap1 = collect_allocation_commitment([order], d1)
    rem1 = snap1["remaining_by_master_warehouse"][fx["item"]]
    assert rem1[fx["wh_a"]] == 7
    assert rem1[fx["wh_b"]] == 8
    # Open SJ not in delivered → still in remaining; confirming again with same
    # attributed qty must not double-count (commitment uses attributed deliveries once).
    snap1b = collect_allocation_commitment([order], d1)
    assert snap1b["remaining_by_master_warehouse"][fx["item"]][fx["wh_a"]] == 7

    # SJ2 from different warehouse than plan for remainder of a line chunk
    warn = realization_mismatch_warning(
        line_id=line_id,
        planned_warehouse_id=fx["wh_a"],
        actual_warehouse_id=fx["wh_b"],
        planned_remaining=7,
        shipped_qty=3,
    )
    assert warn is not None
    assert warn["code"] == "REALIZATION_MISMATCH"
    # Qty over remaining allocation but within order qty — warning, not block
    over = realization_mismatch_warning(
        line_id=line_id,
        planned_warehouse_id=fx["wh_a"],
        actual_warehouse_id=fx["wh_a"],
        planned_remaining=2,
        shipped_qty=5,
    )
    assert over is not None

    # Legacy partial: delivered 6 total without warehouse on some rows + plan
    # must not double-subtract planned remaining below zero incorrectly.
    mixed = [
        {"order_id": order_id, "line_id": line_id, "warehouse_id": fx["wh_a"], "qty": 4},
        {"order_id": order_id, "line_id": line_id, "warehouse_id": "", "qty": 2},
    ]
    snap_legacy = collect_allocation_commitment([order], mixed)
    rem_a = snap_legacy["remaining_by_master_warehouse"][fx["item"]][fx["wh_a"]]
    assert rem_a == 8  # 12-4, empty-wh delivery does not invent extra WH subtract
    assert fx["item"] in snap_legacy["incomplete_masters"]


async def test_concurrent_patch_cas_and_no_physical_mutation(fx):
    order_id = f"ord-cas-{uuid.uuid4().hex[:8]}"
    lines, _, _ = attach_allocations_to_lines(
        [_line(fx["item"], 10, "ln1")],
        branch_id=fx["branch_id"],
        allowed_warehouses=await load_allowed_warehouses_for_branch(fx["db"], fx["branch_id"]),
        raw_by_index=[[{"warehouse_id": fx["wh_a"], "qty": 10}]],
    )
    stamp = "2026-03-01T10:00:00+00:00"
    await fx["db"].orders.insert_one({
        "id": order_id,
        "cancelled": False,
        "operational_branch_id": fx["branch_id"],
        "branch_id": fx["branch_id"],
        "line_items": lines,
        "updated_at": stamp,
        "po_number": "PO-CAS-1",
        "customer": "Cust",
    })
    ledger_before = await fx["db"][LEDGER_COLLECTION].count_documents({})

    app.dependency_overrides[current_user] = lambda: {
        "id": "u-cas",
        "email": "cas@test.local",
        "name": "CAS",
        "role": "admin",
        "permissions_override": {"sales_order.create": True, "po.edit": True},
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ok = await client.patch(
                f"/api/orders/{order_id}/stock-allocations",
                json={
                    "updated_at": stamp,
                    "line_items": [{
                        "stock_allocations": [
                            {"warehouse_id": fx["wh_a"], "qty": 6},
                            {"warehouse_id": fx["wh_b"], "qty": 4},
                        ],
                    }],
                },
            )
            assert ok.status_code == 200, ok.text
            body = ok.json()
            assert body.get("warnings") == [] or isinstance(body.get("warnings"), list)
            fresh_stamp = body["order"]["updated_at"]
            stale = await client.patch(
                f"/api/orders/{order_id}/stock-allocations",
                json={
                    "updated_at": stamp,
                    "line_items": [{"stock_allocations": [{"warehouse_id": fx["wh_a"], "qty": 10}]}],
                },
            )
            assert stale.status_code == 409, stale.text
            # Retry with fresh stamp succeeds (no duplicate ledger)
            retry = await client.patch(
                f"/api/orders/{order_id}/stock-allocations",
                json={
                    "updated_at": fresh_stamp,
                    "line_items": [{"stock_allocations": [
                        {"warehouse_id": fx["wh_a"], "qty": 5},
                        {"warehouse_id": fx["wh_b"], "qty": 5},
                    ]}],
                },
            )
            assert retry.status_code == 200, retry.text
    finally:
        app.dependency_overrides.pop(current_user, None)

    ledger_after = await fx["db"][LEDGER_COLLECTION].count_documents({})
    assert ledger_after == ledger_before  # allocation write ≠ physical mutation
    sales = await fx["db"][LEDGER_COLLECTION].count_documents({
        "movement_type": MOVEMENT_SALES_DELIVERY,
    })
    assert sales == 0


async def test_ssi_incomplete_not_aman_and_minimum_permission_http(fx):
    # Open SO with empty allocation → incomplete warehouse ATP
    await fx["db"].orders.insert_one({
        "id": "ord-inc",
        "cancelled": False,
        "operational_branch_id": fx["branch_id"],
        "branch_id": fx["branch_id"],
        "line_items": [_line(fx["item"], 15, "ln-inc", allocs=[])],
    })
    catalog = await list_physical_stock_catalog(
        fx["db"], q="ALLOC-001", branch_id=fx["branch_id"], warehouse_id=fx["wh_a"], page=1, limit=20,
    )
    rows = catalog.get("items") or []
    assert rows, "expected SSI row"
    row = rows[0]
    assert row.get("allocation_incomplete") is True
    whs = row.get("warehouse_balances") or []
    # When filtering by warehouse, nested may be single-scope on row
    status = row.get("qty_siap_jual_status")
    assert status == "INCOMPLETE" or any(
        (wb.get("stock_dapat_dijual_status") == "INCOMPLETE") for wb in whs
    )
    eval_incomplete = evaluate_minimum_status(
        stock_dapat_dijual=row.get("stock_dapat_dijual") if row.get("stock_dapat_dijual") is not None else 50,
        sellable_status="INCOMPLETE",
        minimum_qty=1,
        minimum_configured=True,
    )
    assert eval_incomplete["status"] == "SOURCE_INCOMPLETE"
    assert eval_incomplete["is_violation"] is False

    # Minimum upsert: update-only user forbidden; manage grant OK; clears no ledger
    ledger_before = await fx["db"][LEDGER_COLLECTION].count_documents({})

    async def as_user(override):
        app.dependency_overrides[current_user] = lambda: {
            "id": "u-min",
            "email": "min@test.local",
            "name": "Min",
            "role": "operator",
            "permissions_override": override,
            "primary_branch_id": fx["branch_id"],
            "branch_ids": [fx["branch_id"]],
        }

    transport = httpx.ASGITransport(app=app)
    try:
        await as_user({"current_stock.update": True, "current_stock.view": True})
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.put("/api/settings/stock-minimum", json={
                "master_item_id": fx["item"],
                "branch_id": fx["branch_id"],
                "warehouse_id": fx["wh_a"],
                "minimum_qty": 5,
            })
            assert denied.status_code == 403, denied.text

        await as_user({"stock_minimum.manage": True, "current_stock.view": True})
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ok = await client.put("/api/settings/stock-minimum", json={
                "master_item_id": fx["item"],
                "branch_id": fx["branch_id"],
                "warehouse_id": fx["wh_a"],
                "minimum_qty": 0,
                "unit": "PCS",
            })
            assert ok.status_code == 200, ok.text
            assert ok.json()["minimum"]["configured"] is True
            assert ok.json()["minimum"]["minimum_qty"] == 0

        await as_user({})
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            forbidden_view = await client.get("/api/dashboard/widget/min-stock")
            assert forbidden_view.status_code == 403

        await as_user({"current_stock.view": True})
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            widget = await client.get(
                "/api/dashboard/widget/min-stock",
                params={"branch_id": fx["branch_id"], "warehouse_id": fx["wh_a"]},
            )
            assert widget.status_code == 200, widget.text
            snap = widget.json()
            assert snap.get("grain") == "item_warehouse"
            # KPI fields present for full population
            assert "violation_count" in snap and "unevaluable_count" in snap
    finally:
        app.dependency_overrides.pop(current_user, None)

    assert await fx["db"][LEDGER_COLLECTION].count_documents({}) == ledger_before


async def test_pra_po_verify_accepts_empty_allocation_with_warning(fx):
    """Verify-path allocation attach: empty/under/over → warnings only."""
    pra = {
        "id": f"pra-{uuid.uuid4().hex[:8]}",
        "number": "PRA-ALLOC-1",
        "status": "submitted",
        "customer": "Cust Pra",
        "customer_id": None,
        "operational_branch_id": fx["branch_id"],
        "branch_id": fx["branch_id"],
        "line_items": [_line(fx["item"], 8, "pra-ln1")],
        "order_date": "2026-03-01",
        "payment_terms": "COD",
        "sales": "Sales A",
    }
    await fx["db"].pra_po.insert_one(dict(pra))
    # Seed minimal deps used by materialize if needed — call attach path directly
    # via allocation_payload to mirror verify.
    user = {"id": "admin-1", "email": "a@test.local", "name": "Admin", "role": "admin"}
    # Use attach helper the same way materialize does (service-level, real DB warehouses).
    allowed = await load_allowed_warehouses_for_branch(fx["db"], fx["branch_id"])
    lines, warns, complete = attach_allocations_to_lines(
        pra["line_items"],
        branch_id=fx["branch_id"],
        allowed_warehouses=allowed,
        raw_by_index=[[]],  # empty
    )
    assert complete is False
    assert any(WARNING_EMPTY in (w.get("message") or "") for w in warns)
    summary = summarize_order_allocation_warnings(lines)
    assert summary["allocation_incomplete"] is True

    # under allocation also warning-only
    _, warns_u, _ = attach_allocations_to_lines(
        pra["line_items"],
        branch_id=fx["branch_id"],
        allowed_warehouses=allowed,
        raw_by_index=[[{"warehouse_id": fx["wh_a"], "qty": 2}]],
    )
    assert any(WARNING_UNDER in (w.get("message") or "") for w in warns_u)
    _, warns_o, _ = attach_allocations_to_lines(
        pra["line_items"],
        branch_id=fx["branch_id"],
        allowed_warehouses=allowed,
        raw_by_index=[[{"warehouse_id": fx["wh_a"], "qty": 20}]],
    )
    assert any(WARNING_OVER in (w.get("message") or "") for w in warns_o)
    _ = user  # actor reserved for future HTTP verify expansion
