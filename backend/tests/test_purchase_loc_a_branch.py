"""PUR-LOC-01 LOC-A: YGY-first PO branch, legacy PATCH, GR cabang guard."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_pur_loc_a")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("APP_ENV", "preview")

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS  # noqa: E402
from services.purchase_branch import (  # noqa: E402
    BRANCH_ALREADY_SET_MSG,
    CROSS_PO_WAREHOUSE_MSG,
    LEGACY_BRANCH_REQUIRED_MSG,
    NON_YGY_IN_TRANSITION_MSG,
    YGY_INACTIVE_MSG,
    YGY_MISSING_MSG,
    resolve_active_ygy_branch,
)
from services.purchase_orders import (  # noqa: E402
    AUDIT_ACTION_BRANCH_SET,
    AUDIT_COLLECTION,
    create_purchase_order,
    get_purchase_order,
    set_purchase_order_branch,
)
from services.purchase_goods_receipts import create_purchase_goods_receipt  # noqa: E402

TEST_DB = os.environ["DB_NAME"]
pytestmark = pytest.mark.anyio

ADMIN = {"id": "u-admin", "email": "admin@example.com", "name": "Admin", "role": "admin"}
SCOPED = {
    "id": "u-scoped",
    "email": "scoped@example.com",
    "name": "Scoped",
    "role": "purchasing",
    "branch_ids": ["branch-smg"],
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[TEST_DB]
    await client.drop_database(TEST_DB)
    for name in (
        "purchase_orders",
        "purchase_goods_receipts",
        "purchase_invoices",
        "master_items",
        "branches",
        "warehouses",
        "audit_logs",
        "app_settings",
        "physical_stock_balances",
        "physical_stock_ledger",
    ):
        await database[name].create_index("id", unique=True)
    await database.purchase_orders.create_index("po_number", unique=True)
    from services.physical_stock_settings import SETTINGS_KEY

    await database.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "enabled": False, "stock_in_trigger": "goods_receipt"}},
        upsert=True,
    )
    yield database
    await client.drop_database(TEST_DB)
    client.close()


async def _seed_ygy(db, *, active=True):
    await db.branches.insert_one({
        "id": "branch-ygy",
        "code": "YGY",
        "name": "Yogyakarta",
        "active": active,
    })
    await db.warehouses.insert_one({
        "id": "wh-ygy",
        "branch_id": "branch-ygy",
        "code": "YGY-UTAMA",
        "name": "Gudang YGY",
        "active": True,
    })
    await db.branches.insert_one({
        "id": "branch-smg",
        "code": "SMG",
        "name": "Semarang",
        "active": True,
    })
    await db.warehouses.insert_one({
        "id": "wh-smg",
        "branch_id": "branch-smg",
        "code": "SMG-UTAMA",
        "name": "Gudang SMG",
        "active": True,
    })


async def _seed_item(db):
    item_id = f"item-{uuid.uuid4().hex[:8]}"
    await db.master_items.insert_one({
        "id": item_id,
        "item_code": "BR-LOCA",
        "name": "Bahan LOC-A",
        "item_name": "Bahan LOC-A",
        "is_active": True,
        "base_unit": "PCS",
        "item_type": "BARANG",
    })
    return item_id


async def _create_po(db, user=ADMIN, **extra):
    item_id = await _seed_item(db)
    payload = {
        "supplier": "Supplier LOC-A",
        "order_date": "2026-10-22",
        "lines": [{
            "master_item_id": item_id,
            "item_code": "BR-LOCA",
            "item_name": "Bahan LOC-A",
            "unit": "PCS",
            "ordered_qty": 10,
            "unit_price": 1000,
        }],
        "idempotency_key": str(uuid.uuid4()),
        **extra,
    }
    return await create_purchase_order(db, payload, user)


def test_permission_registered():
    assert "purchase_orders.edit_branch" in ALL_PERMISSIONS
    assert "purchase_orders.edit_branch" in ROLE_DEFAULT_PERMISSIONS["holding_team"]
    assert "purchase_orders.edit_branch" in ROLE_DEFAULT_PERMISSIONS["admin"]


async def test_ygy_auto_resolve(db):
    await _seed_ygy(db)
    po = await _create_po(db)
    assert po["branch_id"] == "branch-ygy"
    assert po["branch_code"] == "YGY"
    assert "Yogyakarta" in (po.get("branch_name") or "")


async def test_ygy_missing_fail_closed(db):
    await _seed_item(db)
    with pytest.raises(HTTPException) as ei:
        await _create_po(db)
    assert ei.value.status_code == 409
    assert YGY_MISSING_MSG in str(ei.value.detail)


async def test_ygy_inactive_fail_closed(db):
    await _seed_ygy(db, active=False)
    with pytest.raises(HTTPException) as ei:
        await resolve_active_ygy_branch(db)
    assert ei.value.status_code == 409
    assert YGY_INACTIVE_MSG in str(ei.value.detail)
    with pytest.raises(HTTPException) as ei2:
        await _create_po(db)
    assert ei2.value.status_code == 409


async def test_user_unauthorized_for_ygy(db):
    await _seed_ygy(db)
    with pytest.raises(HTTPException) as ei:
        await _create_po(db, user=SCOPED)
    assert ei.value.status_code == 403


async def test_client_non_ygy_rejected_in_transition(db):
    await _seed_ygy(db)
    with pytest.raises(HTTPException) as ei:
        await _create_po(db, branch_id="branch-smg")
    assert ei.value.status_code == 400
    assert NON_YGY_IN_TRANSITION_MSG in str(ei.value.detail)


async def test_legacy_po_get_without_branch(db):
    await _seed_ygy(db)
    item_id = await _seed_item(db)
    po_id = f"po-legacy-{uuid.uuid4().hex[:8]}"
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-LEGACY-{uuid.uuid4().hex[:4].upper()}",
        "supplier": "Legacy",
        "cancelled": False,
        "lines": [{
            "line_id": "pol-1",
            "line_idx": 0,
            "master_item_id": item_id,
            "item_code": "BR-LOCA",
            "item_name": "Bahan LOC-A",
            "unit": "PCS",
            "ordered_qty": 5,
            "unit_price": 1000,
            "line_subtotal": 5000,
        }],
    })
    got = await get_purchase_order(db, po_id)
    assert got["id"] == po_id
    assert not str(got.get("branch_id") or "").strip()


async def test_patch_branch_allowed_and_audit(db):
    await _seed_ygy(db)
    item_id = await _seed_item(db)
    po_id = f"po-legacy-{uuid.uuid4().hex[:8]}"
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-LEGACY-{uuid.uuid4().hex[:4].upper()}",
        "supplier": "Legacy",
        "cancelled": False,
        "lines": [{
            "line_id": "pol-1",
            "line_idx": 0,
            "master_item_id": item_id,
            "ordered_qty": 5,
            "unit_price": 1000,
            "item_name": "Bahan",
            "unit": "PCS",
        }],
    })
    updated = await set_purchase_order_branch(
        db,
        po_id,
        {"branch_id": "branch-ygy", "reason": "Verifikasi YGY historis"},
        ADMIN,
    )
    assert updated["branch_id"] == "branch-ygy"
    assert updated["supplier"] == "Legacy"
    audits = await db[AUDIT_COLLECTION].find(
        {"action": AUDIT_ACTION_BRANCH_SET, "entity_id": po_id}
    ).to_list(10)
    assert len(audits) == 1
    assert audits[0]["before"]["branch_id"] is None
    assert audits[0]["after"]["branch_id"] == "branch-ygy"
    assert "Verifikasi" in audits[0]["reason"]

    with pytest.raises(HTTPException) as ei:
        await set_purchase_order_branch(
            db, po_id, {"branch_id": "branch-smg", "reason": "coba ubah lagi"}, ADMIN,
        )
    assert ei.value.status_code == 400
    assert BRANCH_ALREADY_SET_MSG in str(ei.value.detail)


async def test_patch_branch_rejects_short_reason_and_gr_conflict(db):
    await _seed_ygy(db)
    item_id = await _seed_item(db)
    po_id = f"po-legacy-{uuid.uuid4().hex[:8]}"
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-LEGACY-{uuid.uuid4().hex[:4].upper()}",
        "supplier": "Legacy",
        "cancelled": False,
        "lines": [{
            "line_id": "pol-1",
            "line_idx": 0,
            "master_item_id": item_id,
            "ordered_qty": 5,
            "unit_price": 1000,
            "item_name": "Bahan",
            "unit": "PCS",
        }],
    })
    with pytest.raises(HTTPException) as ei:
        await set_purchase_order_branch(db, po_id, {"branch_id": "branch-ygy", "reason": "no"}, ADMIN)
    assert ei.value.status_code == 400

    await db.purchase_goods_receipts.insert_one({
        "id": f"gr-{uuid.uuid4().hex[:8]}",
        "purchase_order_id": po_id,
        "branch_id": "branch-smg",
        "warehouse_id": "wh-smg",
        "cancelled": False,
        "lines": [],
    })
    with pytest.raises(HTTPException) as ei2:
        await set_purchase_order_branch(
            db, po_id, {"branch_id": "branch-ygy", "reason": "tetap YGY"}, ADMIN,
        )
    assert ei2.value.status_code == 400


async def test_gr_same_branch_pass(db):
    await _seed_ygy(db)
    from services.physical_stock_settings import SETTINGS_KEY

    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "enabled": False}},
        upsert=True,
    )
    po = await _create_po(db)
    line_id = po["lines"][0]["line_id"]
    gr = await create_purchase_goods_receipt(
        db,
        {
            "purchase_order_id": po["id"],
            "warehouse_id": "wh-ygy",
            "receipt_date": "2026-10-22",
            "lines": [{"purchase_order_line_id": line_id, "received_qty": 2}],
            "idempotency_key": str(uuid.uuid4()),
        },
        ADMIN,
    )
    # Legacy PSS path may omit warehouse on enrich; pin from DB document.
    stored = await db.purchase_goods_receipts.find_one({"id": gr["id"]}, {"_id": 0})
    assert stored is not None
    assert stored.get("warehouse_id") == "wh-ygy"
    assert stored.get("branch_id") == "branch-ygy"


async def test_gr_cross_branch_reject_no_stock(db):
    await _seed_ygy(db)
    po = await _create_po(db)
    line_id = po["lines"][0]["line_id"]
    before_bal = await db.physical_stock_balances.count_documents({})
    before_gr = await db.purchase_goods_receipts.count_documents({})
    with pytest.raises(HTTPException) as ei:
        await create_purchase_goods_receipt(
            db,
            {
                "purchase_order_id": po["id"],
                "warehouse_id": "wh-smg",
                "branch_id": "branch-smg",  # client override ignored / rejected via warehouse
                "lines": [{"purchase_order_line_id": line_id, "received_qty": 1}],
                "idempotency_key": str(uuid.uuid4()),
            },
            ADMIN,
        )
    assert ei.value.status_code == 400
    detail = str(ei.value.detail)
    assert ("cabang" in detail.lower()) or (CROSS_PO_WAREHOUSE_MSG in detail)
    assert await db.purchase_goods_receipts.count_documents({}) == before_gr
    assert await db.physical_stock_balances.count_documents({}) == before_bal


async def test_gr_legacy_po_without_branch_reject(db):
    await _seed_ygy(db)
    item_id = await _seed_item(db)
    po_id = f"po-legacy-{uuid.uuid4().hex[:8]}"
    line_id = "pol-1"
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-LEGACY-{uuid.uuid4().hex[:4].upper()}",
        "supplier": "Legacy",
        "cancelled": False,
        "lines": [{
            "line_id": line_id,
            "line_idx": 0,
            "master_item_id": item_id,
            "item_code": "BR-LOCA",
            "item_name": "Bahan LOC-A",
            "unit": "PCS",
            "ordered_qty": 5,
            "unit_price": 1000,
            "line_subtotal": 5000,
        }],
    })
    before_gr = await db.purchase_goods_receipts.count_documents({})
    with pytest.raises(HTTPException) as ei:
        await create_purchase_goods_receipt(
            db,
            {
                "purchase_order_id": po_id,
                "warehouse_id": "wh-ygy",
                "lines": [{"purchase_order_line_id": line_id, "received_qty": 1}],
                "idempotency_key": str(uuid.uuid4()),
            },
            ADMIN,
        )
    assert ei.value.status_code == 400
    assert LEGACY_BRANCH_REQUIRED_MSG in str(ei.value.detail)
    assert await db.purchase_goods_receipts.count_documents({}) == before_gr


async def test_http_edit_branch_permission(db):
    """Permission gate uses purchase_orders.edit_branch exclusively."""
    import server
    from server import app, current_user
    import httpx

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = db
    await _seed_ygy(db)
    item_id = await _seed_item(db)
    po_id = f"po-legacy-{uuid.uuid4().hex[:8]}"
    await db.purchase_orders.insert_one({
        "id": po_id,
        "po_number": f"PB-HTTP-{uuid.uuid4().hex[:4].upper()}",
        "supplier": "Legacy",
        "cancelled": False,
        "lines": [{
            "line_id": "pol-1",
            "line_idx": 0,
            "master_item_id": item_id,
            "ordered_qty": 1,
            "unit_price": 1,
            "item_name": "X",
            "unit": "PCS",
        }],
    })

    async def _user_no_perm():
        return {
            "id": "u-noperm",
            "email": "noperm@example.com",
            "name": "No",
            "role": "operator",
            "permissions": ["purchase_orders.view", "purchase_orders.create"],
        }

    async def _user_with_perm():
        return {
            "id": "u-perm",
            "email": "perm@example.com",
            "name": "Yes",
            "role": "admin",
            "permissions": ["purchase_orders.edit_branch"],
        }

    app.dependency_overrides[current_user] = _user_no_perm
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(
            f"/api/purchase-orders/{po_id}/branch",
            json={"branch_id": "branch-ygy", "reason": "isi cabang"},
        )
        assert r.status_code in {401, 403}

    app.dependency_overrides[current_user] = _user_with_perm
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.patch(
            f"/api/purchase-orders/{po_id}/branch",
            json={"branch_id": "branch-ygy", "reason": "isi cabang"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["order"]["branch_id"] == "branch-ygy"
    app.dependency_overrides.pop(current_user, None)
