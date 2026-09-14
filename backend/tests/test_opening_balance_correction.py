"""HOLDING_TEAM-only append-only opening-balance correction."""
import uuid
import pytest
from fastapi import HTTPException

from services.product_stock_state import (
    HOLDING_TEAM_ONLY_MSG,
    correct_opening_balance,
    initialize_opening_balance,
    get_product_stock_state,
)
from services.physical_stock_settings import get_physical_stock_settings

from tests.test_physical_stock_authority import MockDb


@pytest.fixture
def test_db():
    return MockDb()


HOLDING = {"id": "ht-1", "email": "holding@test.com", "name": "Holding", "role": "holding_team"}
ADMIN = {"id": "ad-1", "email": "admin@test.com", "name": "Admin", "role": "admin"}
HEAD = {"id": "hp-1", "email": "kepala@test.com", "name": "Kepala", "role": "head_production"}


async def _seed_opening(test_db, *, unprinted=10000, printed=2000):
    prod_id = str(uuid.uuid4())
    await test_db.master_items.insert_one({
        "id": prod_id,
        "item_code": "100019",
        "item_name": "14 oz PET Oval 11 gr SJP",
        "unit": "PCS",
        "accurate_item_id": "acc-100019",
    })
    await initialize_opening_balance(test_db, prod_id, {
        "unprinted_qty": unprinted,
        "printed_qty": printed,
        "total_qty": unprinted + printed,
        "base_uom": "PCS",
    }, user=ADMIN)
    return prod_id


@pytest.mark.anyio
async def test_holding_team_corrects_current_physical(test_db):
    prod_id = await _seed_opening(test_db)
    before_movements = list(test_db.stock_movements._docs)
    assert len(before_movements) == 1
    assert before_movements[0]["movement_type"] == "SALDO_AWAL"
    opening_id = before_movements[0]["id"]

    res = await correct_opening_balance(
        test_db,
        prod_id,
        {
            "unprinted_qty": 8000,
            "printed_qty": 1500,
            "total_qty": 9500,
            "note": "Salah input saldo awal 12000",
            "client_request_id": "corr-1",
        },
        user=HOLDING,
    )
    assert res["unchanged"] is False
    state = res["stock_state"]
    assert state["unprinted_qty"] == 8000
    assert state["printed_qty"] == 1500
    assert state["total_qty"] == 9500
    assert state["initialized"] is True
    assert state["status"] == "INITIALIZED"
    audit = state["last_opening_correction"]
    assert audit["old_qty"]["total_qty"] == 12000
    assert audit["new_qty"]["total_qty"] == 9500
    assert audit["note"] == "Salah input saldo awal 12000"
    assert audit["corrected_by"]["role"] == "holding_team"
    assert audit["corrected_at"]
    mov = res["movement"]
    assert mov["movement_type"] == "KOREKSI"
    assert mov["event_key"] == "opening_correction:{0}:corr-1".format(prod_id)
    types = [d["movement_type"] for d in test_db.stock_movements._docs]
    assert types == ["SALDO_AWAL", "KOREKSI"]
    assert any(d["id"] == opening_id and d["movement_type"] == "SALDO_AWAL" for d in test_db.stock_movements._docs)
    settings = await get_physical_stock_settings(test_db)
    assert settings["enabled"] is False


@pytest.mark.anyio
async def test_other_roles_403(test_db):
    prod_id = await _seed_opening(test_db)
    payload = {
        "unprinted_qty": 1,
        "printed_qty": 0,
        "note": "coba",
        "client_request_id": "x",
    }
    for user in (ADMIN, HEAD, {"role": "crm"}, None):
        with pytest.raises(HTTPException) as ei:
            await correct_opening_balance(test_db, prod_id, payload, user=user)
        assert ei.value.status_code == 403
        assert ei.value.detail == HOLDING_TEAM_ONLY_MSG
    assert [d["movement_type"] for d in test_db.stock_movements._docs] == ["SALDO_AWAL"]


@pytest.mark.anyio
async def test_double_submit_same_request_id_does_not_duplicate(test_db):
    prod_id = await _seed_opening(test_db)
    payload = {
        "unprinted_qty": 5000,
        "printed_qty": 0,
        "note": "koreksi",
        "client_request_id": "same-click",
    }
    first = await correct_opening_balance(test_db, prod_id, payload, user=HOLDING)
    second = await correct_opening_balance(test_db, prod_id, payload, user=HOLDING)
    assert second["idempotent"] is True
    assert second["movement"]["id"] == first["movement"]["id"]
    koreksi = [d for d in test_db.stock_movements._docs if d["movement_type"] == "KOREKSI"]
    saldo = [d for d in test_db.stock_movements._docs if d["movement_type"] == "SALDO_AWAL"]
    assert len(koreksi) == 1
    assert len(saldo) == 1
    state = await get_product_stock_state(test_db, prod_id)
    assert state["total_qty"] == 5000


@pytest.mark.anyio
async def test_does_not_create_second_saldo_awal(test_db):
    prod_id = await _seed_opening(test_db)
    await correct_opening_balance(
        test_db,
        prod_id,
        {"unprinted_qty": 100, "printed_qty": 50, "note": "fix", "client_request_id": "n1"},
        user=HOLDING,
    )
    with pytest.raises(HTTPException) as ei:
        await initialize_opening_balance(test_db, prod_id, {
            "unprinted_qty": 1, "printed_qty": 0, "total_qty": 1, "base_uom": "PCS",
        }, user=HOLDING)
    assert ei.value.status_code == 409
    assert sum(1 for d in test_db.stock_movements._docs if d["movement_type"] == "SALDO_AWAL") == 1
