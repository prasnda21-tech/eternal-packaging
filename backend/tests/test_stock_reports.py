"""Kartu Stok / Mutasi Persediaan — read-only physical_stock_ledger report."""
from __future__ import annotations

import os
import sys
import uuid
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_stock_reports_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "local")

import server
from server import app, current_user
from services.physical_stock import (
    BALANCE_COLLECTION,
    BASELINE_RECONCILED,
    BASELINE_UNRECONCILED,
    LEDGER_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PRODUCTION_CONSUMPTION,
    MOVEMENT_PRODUCTION_OUTPUT,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_PURCHASE_RETURN,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_WAREHOUSE_TRANSFER_IN,
    MOVEMENT_WAREHOUSE_TRANSFER_OUT,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    names = (
        LEDGER_COLLECTION,
        BALANCE_COLLECTION,
        "master_items",
        "branches",
        "warehouses",
        "users",
        "stock_report_memorized",
        "app_settings",
    )
    for name in names:
        await server.db[name].delete_many({})
    yield
    for name in names:
        await server.db[name].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(role="admin", **extra):
    uid = extra.pop("id", None) or f"u-{role}-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": uid,
        "email": extra.pop("email", None) or f"{uid}@example.com",
        "name": role,
        "role": role,
        "status": "active",
        "user_id": uid,
        "permissions_override": extra.pop("permissions_override", {}),
    }
    doc.update(extra)
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_locations():
    branch_a = "br-a"
    branch_b = "br-b"
    wh_a1 = "wh-a1"
    wh_a2 = "wh-a2"
    wh_b1 = "wh-b1"
    item = "item-kartu-1"
    await server.db.branches.insert_many([
        {"id": branch_a, "code": "YGY", "name": "Jogja", "active": True},
        {"id": branch_b, "code": "SMG", "name": "Semarang", "active": True},
    ])
    await server.db.warehouses.insert_many([
        {"id": wh_a1, "branch_id": branch_a, "code": "A1", "name": "Gudang A1", "active": True},
        {"id": wh_a2, "branch_id": branch_a, "code": "A2", "name": "Gudang A2", "active": True},
        {"id": wh_b1, "branch_id": branch_b, "code": "B1", "name": "Gudang B1", "active": True},
    ])
    await server.db.master_items.insert_one({
        "id": item,
        "item_code": "BRG-KARTU",
        "item_name": "Barang Kartu Stok",
        "active": True,
    })
    return {
        "item": item,
        "branch_a": branch_a,
        "branch_b": branch_b,
        "wh_a1": wh_a1,
        "wh_a2": wh_a2,
        "wh_b1": wh_b1,
    }


async def _ledger(
    *,
    item,
    branch,
    warehouse,
    movement_type,
    qty_delta,
    balance_before,
    balance_after,
    occurred_at,
    reference_id="ref-1",
    reference_type="test",
    ledger_id=None,
):
    doc = {
        "id": ledger_id or str(uuid.uuid4()),
        "master_item_id": item,
        "branch_id": branch,
        "warehouse_id": warehouse,
        "movement_type": movement_type,
        "qty_delta": qty_delta,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "occurred_at": occurred_at,
        "created_at": occurred_at,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "reason": "",
    }
    await server.db[LEDGER_COLLECTION].insert_one(doc)
    return doc


async def _balance(item, branch, warehouse, qty, baseline=BASELINE_RECONCILED):
    await server.db[BALANCE_COLLECTION].update_one(
        {"master_item_id": item, "branch_id": branch, "warehouse_id": warehouse},
        {"$set": {
            "master_item_id": item,
            "branch_id": branch,
            "warehouse_id": warehouse,
            "physical_qty": qty,
            "baseline_status": baseline,
        }},
        upsert=True,
    )


async def _show(client, **extra):
    body = {
        "report_id": "kartu-stok",
        "date_from": "01/03/2026",
        "date_to": "31/03/2026",
        "master_item_id": extra.pop("master_item_id", ""),
        "branch_id": "",
        "warehouse_id": "",
        "page": 1,
        "page_size": 25,
    }
    body.update(extra)
    return await client.post("/api/laporan/persediaan/tampilkan", json=body)


async def test_kartu_stok_valid_opening_and_movements():
    fx = await _seed_locations()
    await _balance(fx["item"], fx["branch_a"], fx["wh_a1"], 130)
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_OPENING_BALANCE, qty_delta=100,
        balance_before=0, balance_after=100, occurred_at="2026-02-01T00:00:00",
        reference_id="opening:a1",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=50,
        balance_before=100, balance_after=150, occurred_at="2026-03-05T10:00:00",
        reference_id="GR-1",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PURCHASE_RETURN, qty_delta=-5,
        balance_before=150, balance_after=145, occurred_at="2026-03-06T10:00:00",
        reference_id="RET-1",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_SALES_DELIVERY, qty_delta=-20,
        balance_before=145, balance_after=125, occurred_at="2026-03-07T10:00:00",
        reference_id="SJ-1",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PRODUCTION_CONSUMPTION, qty_delta=-10,
        balance_before=125, balance_after=115, occurred_at="2026-03-08T10:00:00",
        reference_id="PROD-C",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PRODUCTION_OUTPUT, qty_delta=8,
        balance_before=115, balance_after=123, occurred_at="2026-03-09T10:00:00",
        reference_id="PROD-O",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_REVERSAL, qty_delta=20,
        balance_before=123, balance_after=143, occurred_at="2026-03-10T10:00:00",
        reference_id="REV-SJ-1",
    )
    # Transfer out/in across warehouses in period
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_WAREHOUSE_TRANSFER_OUT, qty_delta=-13,
        balance_before=143, balance_after=130, occurred_at="2026-03-11T10:00:00",
        reference_id="TR-1",
    )
    await _balance(fx["item"], fx["branch_a"], fx["wh_a2"], 13)
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a2"],
        movement_type=MOVEMENT_WAREHOUSE_TRANSFER_IN, qty_delta=13,
        balance_before=0, balance_after=13, occurred_at="2026-03-11T10:00:01",
        reference_id="TR-1",
    )

    admin = _user()
    await server.db.users.insert_one(admin)
    async with await client_for(admin) as client:
        shown = await _show(
            client,
            master_item_id=fx["item"],
            branch_id=fx["branch_a"],
            warehouse_id=fx["wh_a1"],
        )
        exported = await client.post("/api/laporan/persediaan/ekspor", json={
            "report_id": "kartu-stok",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "master_item_id": fx["item"],
            "branch_id": fx["branch_a"],
            "warehouse_id": fx["wh_a1"],
            "include_all": True,
        })

    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["history_status"] == "complete"
    assert "Histori Lengkap" in body["history_status_label"]
    # Period opening from prior OPENING_BALANCE balance_after=100
    lines = [r for r in body["rows"] if r.get("kind") != "group"]
    opening = lines[0]["values"]
    assert opening["jenis_mutasi"] == "Saldo Awal Periode"
    assert opening["saldo_sesudah"] == "100"
    assert body["totals"]["qty_masuk"] == "78"  # 50+8+20
    assert body["totals"]["qty_keluar"] == "48"  # 5+20+10+13
    # Reversal and production present
    kinds = [r["values"]["jenis_mutasi"] for r in lines]
    assert any("Reversal" in k for k in kinds)
    assert any("Produksi" in k for k in kinds)
    assert exported.status_code == 200
    book = load_workbook(BytesIO(exported.content))
    assert "Kartu Stok" in (book.active["A2"].value or "")


async def test_kartu_stok_missing_opening_incomplete():
    fx = await _seed_locations()
    await _balance(fx["item"], fx["branch_a"], fx["wh_a1"], 10, baseline=BASELINE_RECONCILED)
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=10,
        balance_before=0, balance_after=10, occurred_at="2026-03-05T10:00:00",
        reference_id="GR-X",
    )
    admin = _user()
    await server.db.users.insert_one(admin)
    async with await client_for(admin) as client:
        shown = await _show(client, master_item_id=fx["item"], warehouse_id=fx["wh_a1"], branch_id=fx["branch_a"])
    assert shown.status_code == 200
    body = shown.json()
    assert body["history_status"] == "incomplete"
    assert "Tidak Lengkap" in (body.get("gap") or "")
    # No claimed period opening row
    lines = [r["values"] for r in body["rows"] if r.get("kind") != "group"]
    assert all(r["jenis_mutasi"] != "Saldo Awal Periode" for r in lines)
    assert lines[0]["qty_masuk"] == "10"


async def test_kartu_stok_unreconciled_baseline():
    fx = await _seed_locations()
    await _balance(fx["item"], fx["branch_a"], fx["wh_a1"], -5, baseline=BASELINE_UNRECONCILED)
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_SALES_DELIVERY, qty_delta=-5,
        balance_before=0, balance_after=-5, occurred_at="2026-03-05T10:00:00",
        reference_id="SJ-U",
    )
    admin = _user()
    await server.db.users.insert_one(admin)
    async with await client_for(admin) as client:
        shown = await _show(client, master_item_id=fx["item"], warehouse_id=fx["wh_a1"], branch_id=fx["branch_a"])
    assert shown.status_code == 200
    body = shown.json()
    assert body["history_status"] == "unreconciled"
    assert "Belum Rekonsiliasi" in (body.get("gap") or body["history_status_label"])


async def test_kartu_stok_backdated_chain_disclaimer():
    fx = await _seed_locations()
    await _balance(fx["item"], fx["branch_a"], fx["wh_a1"], 30)
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_OPENING_BALANCE, qty_delta=20,
        balance_before=0, balance_after=20, occurred_at="2026-02-01T00:00:00",
    )
    # Chronological order: first then second, but balances don't chain (backdated write).
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=10,
        balance_before=20, balance_after=30, occurred_at="2026-03-10T10:00:00",
        reference_id="GR-LATE",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_MANUAL_ADJUSTMENT, qty_delta=5,
        balance_before=50, balance_after=55, occurred_at="2026-03-05T10:00:00",
        reference_id="ADJ-BACK",
    )
    admin = _user()
    await server.db.users.insert_one(admin)
    async with await client_for(admin) as client:
        shown = await _show(client, master_item_id=fx["item"], warehouse_id=fx["wh_a1"], branch_id=fx["branch_a"])
    body = shown.json()
    assert body["source_meta"]["backdated_chain"] is True
    assert "backdated" in (body.get("gap") or "").casefold() or "kronologis" in (body.get("gap") or "").casefold()
    # Still shows ledger-recorded balances, not recomputed
    lines = [r["values"] for r in body["rows"] if r.get("kind") != "group" and r["values"]["jenis_mutasi"] != "Saldo Awal Periode"]
    assert any(r["saldo_sebelum"] == "50" for r in lines)


async def test_kartu_stok_mixed_locations_no_merged_balance():
    fx = await _seed_locations()
    await _balance(fx["item"], fx["branch_a"], fx["wh_a1"], 10)
    await _balance(fx["item"], fx["branch_a"], fx["wh_a2"], 7)
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_OPENING_BALANCE, qty_delta=10,
        balance_before=0, balance_after=10, occurred_at="2026-02-01T00:00:00",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a2"],
        movement_type=MOVEMENT_OPENING_BALANCE, qty_delta=7,
        balance_before=0, balance_after=7, occurred_at="2026-02-01T00:00:00",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=3,
        balance_before=10, balance_after=13, occurred_at="2026-03-05T10:00:00",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a2"],
        movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=2,
        balance_before=7, balance_after=9, occurred_at="2026-03-06T10:00:00",
    )
    admin = _user()
    await server.db.users.insert_one(admin)
    async with await client_for(admin) as client:
        shown = await _show(client, master_item_id=fx["item"], branch_id=fx["branch_a"])
    body = shown.json()
    assert body["grouped"] is True
    assert "tidak digabung" in (body.get("gap") or "").casefold() or "lokasi" in (body.get("gap") or "").casefold()
    # Totals are qty only — never sum of balances
    assert "saldo_sesudah" not in body["totals"] or body["totals"].get("saldo_sesudah") in (None, "")
    assert body["totals"]["qty_masuk"] == "5"


async def test_kartu_stok_branch_scope_403_and_pagination_export_no_write():
    fx = await _seed_locations()
    await _balance(fx["item"], fx["branch_a"], fx["wh_a1"], 5)
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_OPENING_BALANCE, qty_delta=5,
        balance_before=0, balance_after=5, occurred_at="2026-02-01T00:00:00",
    )
    for i in range(3):
        await _ledger(
            item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
            movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=1,
            balance_before=5 + i, balance_after=6 + i,
            occurred_at=f"2026-03-0{i+2}T10:00:00",
            reference_id=f"GR-{i}",
            ledger_id=f"led-{i}",
        )

    # Non-global local role with explicit branch assignment (holding_team is ALL locally).
    locked = _user(
        role="designer",
        branch_ids=[fx["branch_a"]],
        permissions_override={
            "current_stock.view": True,
            "current_stock.export": False,
            "master_items.view": True,
        },
    )
    await server.db.users.insert_one(locked)
    before_count = await server.db[LEDGER_COLLECTION].count_documents({})
    async with await client_for(locked) as client:
        denied = await _show(
            client,
            master_item_id=fx["item"],
            branch_id=fx["branch_b"],
            warehouse_id=fx["wh_b1"],
        )
        page1 = await _show(
            client,
            master_item_id=fx["item"],
            branch_id=fx["branch_a"],
            warehouse_id=fx["wh_a1"],
            page=1,
            page_size=2,
        )
        page2 = await _show(
            client,
            master_item_id=fx["item"],
            branch_id=fx["branch_a"],
            warehouse_id=fx["wh_a1"],
            page=2,
            page_size=2,
        )
        export = await client.post("/api/laporan/persediaan/ekspor", json={
            "report_id": "kartu-stok",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "master_item_id": fx["item"],
            "branch_id": fx["branch_a"],
            "warehouse_id": fx["wh_a1"],
        })
    assert denied.status_code == 403
    assert page1.status_code == 200
    assert page1.json()["totals"]["qty_masuk"] == page2.json()["totals"]["qty_masuk"]
    assert export.status_code == 403
    after_count = await server.db[LEDGER_COLLECTION].count_documents({})
    assert after_count == before_count
    bal = await server.db[BALANCE_COLLECTION].find_one(
        {"master_item_id": fx["item"], "warehouse_id": fx["wh_a1"]}, {"_id": 0}
    )
    assert bal["physical_qty"] == 5


async def test_kartu_stok_requires_item_and_stable_order():
    fx = await _seed_locations()
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=1,
        balance_before=0, balance_after=1, occurred_at="2026-03-05T12:00:00",
        reference_id="B", ledger_id="id-b",
    )
    await _ledger(
        item=fx["item"], branch=fx["branch_a"], warehouse=fx["wh_a1"],
        movement_type=MOVEMENT_PURCHASE_RECEIPT, qty_delta=1,
        balance_before=1, balance_after=2, occurred_at="2026-03-05T10:00:00",
        reference_id="A", ledger_id="id-a",
    )
    admin = _user()
    await server.db.users.insert_one(admin)
    async with await client_for(admin) as client:
        missing = await _show(client, master_item_id="")
        shown = await _show(client, master_item_id=fx["item"], warehouse_id=fx["wh_a1"], branch_id=fx["branch_a"])
    assert missing.status_code == 400
    refs = [
        r["values"]["referensi"]
        for r in shown.json()["rows"]
        if r.get("kind") != "group"
    ]
    # Chronological ASC — A (10:00) before B (12:00)
    assert refs[0].endswith("A")
    assert refs[1].endswith("B")
