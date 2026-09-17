"""Safe voiding of a mistaken, unused physical-stock initialization."""
import uuid

import pytest
from fastapi import HTTPException

from services.product_stock_state import (
    OPENING_VOID_REASON,
    STATUS_UNINITIALIZED,
    get_product_stock_state,
    initialize_opening_balance,
    void_opening_balance,
)
from services.stock_engine_v2 import apply_stock_opname
from services.stock_ledger import get_latest_balance
from tests.test_physical_stock_authority import MockDb


HOLDING = {
    "id": "ht-1",
    "email": "holding@test.com",
    "name": "Holding",
    "role": "holding_team",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def test_db():
    return MockDb()


async def _seed_mistaken_opening(test_db):
    mid = str(uuid.uuid4())
    await test_db.master_items.insert_one({
        "id": mid,
        "item_code": "100019",
        "item_name": "14 oz PET Oval 11 gr SJP",
        "unit": "PCS",
        "accurate_item_id": "acc-100019",
    })
    await initialize_opening_balance(
        test_db,
        mid,
        {
            "unprinted_qty": 10000,
            "printed_qty": 2000,
            "total_qty": 12000,
            "base_uom": "PCS",
        },
        user=HOLDING,
    )
    return mid


@pytest.mark.anyio
async def test_void_preserves_opening_audit_and_resets_state(test_db):
    mid = await _seed_mistaken_opening(test_db)

    result = await void_opening_balance(test_db, mid, user=HOLDING)

    state = result["stock_state"]
    assert state["status"] == STATUS_UNINITIALIZED
    assert state["initialized"] is False
    assert state["total_qty"] == 0
    assert state["unprinted_qty"] == 0
    assert state["printed_qty"] == 0
    assert state["opening_movement_id"] is None
    assert state["initialized_at"] is None
    assert state["initialized_by"] is None
    assert state["last_initialization_void"]["void_reason"] == OPENING_VOID_REASON

    assert len(test_db.stock_movements._docs) == 1
    movement = test_db.stock_movements._docs[0]
    assert movement["movement_type"] == "SALDO_AWAL"
    assert movement["status"] == "VOID"
    assert movement["voided_at"]
    assert movement["voided_by"]["email"] == HOLDING["email"]
    assert movement["void_reason"] == OPENING_VOID_REASON
    assert await get_latest_balance(test_db, mid, "acc-100019") is None


@pytest.mark.anyio
async def test_void_is_idempotent(test_db):
    mid = await _seed_mistaken_opening(test_db)
    first = await void_opening_balance(test_db, mid, user=HOLDING)
    second = await void_opening_balance(test_db, mid, user=HOLDING)

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert len(test_db.stock_movements._docs) == 1
    assert test_db.stock_movements._docs[0]["status"] == "VOID"


@pytest.mark.anyio
async def test_void_rejects_any_dependent_v2_movement(test_db):
    mid = await _seed_mistaken_opening(test_db)
    await test_db.stock_movements.insert_one({
        "_id": "dependent",
        "id": "dependent",
        "event_key": "dependent",
        "master_item_id": mid,
        "accurate_item_id": "acc-100019",
        "movement_type": "KOREKSI",
        "created_at": "2099-01-01T00:00:00+00:00",
        "balance_after": 11000,
    })

    with pytest.raises(HTTPException) as exc:
        await void_opening_balance(test_db, mid, user=HOLDING)

    assert exc.value.status_code == 409
    assert "movement V2 lain" in exc.value.detail
    state = await get_product_stock_state(test_db, mid)
    assert state["initialized"] is True
    assert all(not movement.get("voided_at") for movement in test_db.stock_movements._docs)


@pytest.mark.anyio
async def test_void_rejects_state_drift_without_movement(test_db):
    mid = await _seed_mistaken_opening(test_db)
    for state in test_db.product_stock_state._docs:
        if state.get("master_item_id") == mid:
            state["total_qty"] = 11999
            state["unprinted_qty"] = 9999

    with pytest.raises(HTTPException) as exc:
        await void_opening_balance(test_db, mid, user=HOLDING)

    assert exc.value.status_code == 409
    assert "tidak lagi sama" in exc.value.detail
    assert test_db.stock_movements._docs[0].get("status") is None


@pytest.mark.anyio
async def test_void_rejects_production_admin(test_db):
    mid = await _seed_mistaken_opening(test_db)
    with pytest.raises(HTTPException) as exc:
        await void_opening_balance(
            test_db,
            mid,
            user={"role": "admin"},
            app_env="production",
            mongo_url="mongodb://mongo.internal/app",
        )
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_voided_item_can_receive_new_official_opening(test_db):
    mid = await _seed_mistaken_opening(test_db)
    await void_opening_balance(test_db, mid, user=HOLDING)

    official = await initialize_opening_balance(
        test_db,
        mid,
        {
            "unprinted_qty": 700,
            "printed_qty": 0,
            "total_qty": 700,
            "base_uom": "PCS",
            "initialization_source": "STOCK_OPNAME",
            "client_request_id": "official-count",
        },
        user=HOLDING,
    )

    assert official["stock_state"]["initialized"] is True
    assert official["stock_state"]["total_qty"] == 700
    assert official["stock_state"]["opening_generation"] == 2
    assert len(test_db.stock_movements._docs) == 2
    old, new = test_db.stock_movements._docs
    assert old["status"] == "VOID"
    assert new["movement_type"] == "SALDO_AWAL"
    assert new.get("status") is None
    assert new["id"].endswith(":v2")


@pytest.mark.anyio
async def test_first_opname_initializes_once_then_is_idempotent(test_db):
    mid = str(uuid.uuid4())
    await test_db.master_items.insert_one({
        "id": mid,
        "item_code": "SKU-FIRST",
        "item_name": "First Count",
        "unit": "PCS",
        "accurate_item_id": "acc-first",
    })

    first = await apply_stock_opname(
        test_db,
        master_item_id=mid,
        target_stock_current_qty=25,
        outstanding_so_qty=0,
        note="Opname pertama",
        user=HOLDING,
        client_request_id="first-count",
    )
    retry = await apply_stock_opname(
        test_db,
        master_item_id=mid,
        target_stock_current_qty=25,
        outstanding_so_qty=0,
        note="Opname pertama",
        user=HOLDING,
        client_request_id="first-count",
    )

    assert first["opname_action"] == "SALDO_AWAL"
    assert first["movement"]["movement_type"] == "SALDO_AWAL"
    assert first["stock_state"]["total_qty"] == 25
    assert retry["idempotent"] is True
    assert retry["opname_action"] == "SALDO_AWAL"
    assert len(test_db.stock_movements._docs) == 1
