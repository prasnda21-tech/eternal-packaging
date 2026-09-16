"""HOLDING_TEAM-only append-only opening-balance correction."""
import asyncio
import copy
import uuid
import pytest
from fastapi import HTTPException

from services.product_stock_state import (
    HOLDING_TEAM_ONLY_MSG,
    OPENING_CORRECTION_CONFLICT_MSG,
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
HOLDING_A = {"id": "ht-a", "email": "alice.holding@test.com", "name": "Alice Holding", "role": "holding_team"}
HOLDING_B = {"id": "ht-b", "email": "bob.holding@test.com", "name": "Bob Holding", "role": "HOLDING_TEAM"}
FAUZAN_NOT_HOLDING = {
    "id": "fg-1",
    "email": "fauzan.ghani@test.com",
    "name": "Fauzan Ghani",
    "role": "admin",
}
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
    assert not state.get("pending_event")
    settings = await get_physical_stock_settings(test_db)
    assert settings["enabled"] is False


@pytest.mark.anyio
async def test_any_holding_team_user_is_allowed(test_db):
    prod_id = await _seed_opening(test_db)
    first = await correct_opening_balance(
        test_db,
        prod_id,
        {
            "unprinted_qty": 9000,
            "printed_qty": 1000,
            "note": "user A holding_team",
            "client_request_id": "user-a",
        },
        user=HOLDING_A,
    )
    assert first["unchanged"] is False
    assert first["stock_state"]["last_opening_correction"]["corrected_by"]["role"] == "holding_team"
    assert first["stock_state"]["last_opening_correction"]["corrected_by"]["email"] == "alice.holding@test.com"

    second = await correct_opening_balance(
        test_db,
        prod_id,
        {
            "unprinted_qty": 8500,
            "printed_qty": 500,
            "note": "user B HOLDING_TEAM normalized",
            "client_request_id": "user-b",
        },
        user=HOLDING_B,
    )
    assert second["unchanged"] is False
    assert second["corrected_by"]["role"] == "HOLDING_TEAM"
    state = await get_product_stock_state(test_db, prod_id)
    assert state["total_qty"] == 9000


@pytest.mark.anyio
async def test_named_user_without_holding_team_role_is_denied(test_db):
    prod_id = await _seed_opening(test_db)
    with pytest.raises(HTTPException) as ei:
        await correct_opening_balance(
            test_db,
            prod_id,
            {
                "unprinted_qty": 1,
                "printed_qty": 0,
                "note": "Fauzan bukan holding_team",
                "client_request_id": "fauzan-deny",
            },
            user=FAUZAN_NOT_HOLDING,
        )
    assert ei.value.status_code == 403
    assert ei.value.detail == HOLDING_TEAM_ONLY_MSG
    assert [d["movement_type"] for d in test_db.stock_movements._docs] == ["SALDO_AWAL"]


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


def _koreksi(docs):
    return [d for d in docs if d.get("movement_type") == "KOREKSI"]


def _saldo(docs):
    return [d for d in docs if d.get("movement_type") == "SALDO_AWAL"]


@pytest.mark.anyio
async def test_concurrent_distinct_request_ids_only_one_commits_same_snapshot(test_db):
    """Two distinct request IDs cannot both commit from the same before-state."""
    prod_id = await _seed_opening(test_db)
    barrier = asyncio.Barrier(2)
    orig = test_db.product_stock_state.update_one

    async def gated_update_one(query, update, upsert=False):
        is_claim = (
            query.get("pending_event") is None
            and "unprinted_qty" in query
            and query.get("initialized") is True
        )
        if is_claim:
            await barrier.wait()
        return await orig(query, update, upsert=upsert)

    test_db.product_stock_state.update_one = gated_update_one

    async def run(req_id, unp, pri):
        return await correct_opening_balance(
            test_db,
            prod_id,
            {
                "unprinted_qty": unp,
                "printed_qty": pri,
                "total_qty": unp + pri,
                "note": "koreksi concurrent {0}".format(req_id),
                "client_request_id": req_id,
            },
            user=HOLDING,
        )

    results = await asyncio.gather(
        run("req-a", 8000, 1500),
        run("req-b", 7000, 1000),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    conflicts = [r for r in results if isinstance(r, HTTPException) and r.status_code == 409]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].detail == OPENING_CORRECTION_CONFLICT_MSG

    winner = successes[0]
    state = await get_product_stock_state(test_db, prod_id)
    assert state["unprinted_qty"] == winner["new_qty"]["unprinted_qty"]
    assert state["printed_qty"] == winner["new_qty"]["printed_qty"]
    assert state["total_qty"] == winner["new_qty"]["total_qty"]
    assert not state.get("pending_event")
    koreksi = _koreksi(test_db.stock_movements._docs)
    assert len(koreksi) == 1
    assert koreksi[0]["id"] == winner["movement"]["id"]
    assert koreksi[0]["unprinted_before"] == 10000
    assert koreksi[0]["printed_before"] == 2000
    assert koreksi[0]["unprinted_after"] == state["unprinted_qty"]
    assert koreksi[0]["printed_after"] == state["printed_qty"]
    assert len(_saldo(test_db.stock_movements._docs)) == 1


@pytest.mark.anyio
async def test_concurrent_same_request_id_remains_idempotent(test_db):
    prod_id = await _seed_opening(test_db)
    barrier = asyncio.Barrier(2)
    orig = test_db.product_stock_state.update_one

    async def gated_update_one(query, update, upsert=False):
        is_claim = (
            query.get("pending_event") is None
            and "unprinted_qty" in query
            and query.get("initialized") is True
        )
        if is_claim:
            await barrier.wait()
        return await orig(query, update, upsert=upsert)

    test_db.product_stock_state.update_one = gated_update_one
    payload = {
        "unprinted_qty": 5000,
        "printed_qty": 0,
        "total_qty": 5000,
        "note": "koreksi",
        "client_request_id": "same-click",
    }

    results = await asyncio.gather(
        correct_opening_balance(test_db, prod_id, payload, user=HOLDING),
        correct_opening_balance(test_db, prod_id, payload, user=HOLDING),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    assert errors == []
    oks = results
    ids = {r["movement"]["id"] for r in oks if r.get("movement")}
    assert len(ids) == 1
    assert sum(1 for r in oks if r.get("idempotent")) >= 1
    koreksi = _koreksi(test_db.stock_movements._docs)
    assert len(koreksi) == 1
    state = await get_product_stock_state(test_db, prod_id)
    assert state["total_qty"] == 5000
    assert not state.get("pending_event")


@pytest.mark.anyio
async def test_in_flight_physical_engine_claim_is_not_clobbered(test_db):
    prod_id = await _seed_opening(test_db)
    engine_event = "stock_out:packing:{0}:act-1".format(prod_id)
    for doc in test_db.product_stock_state._docs:
        if doc.get("master_item_id") == prod_id or doc.get("_id") == prod_id:
            doc["pending_event"] = {
                "event_id": engine_event,
                "stage": "CLAIMED",
                "movement_type": "PENGIRIMAN",
            }
            break

    with pytest.raises(HTTPException) as ei:
        await correct_opening_balance(
            test_db,
            prod_id,
            {
                "unprinted_qty": 1,
                "printed_qty": 0,
                "note": "jangan clobber engine",
                "client_request_id": "ksa-vs-engine",
            },
            user=HOLDING,
        )
    assert ei.value.status_code == 409
    assert ei.value.detail == OPENING_CORRECTION_CONFLICT_MSG
    state = await get_product_stock_state(test_db, prod_id)
    assert state["unprinted_qty"] == 10000
    assert state["printed_qty"] == 2000
    assert state["total_qty"] == 12000
    assert state["pending_event"]["event_id"] == engine_event
    assert _koreksi(test_db.stock_movements._docs) == []
    assert len(_saldo(test_db.stock_movements._docs)) == 1


@pytest.mark.anyio
async def test_stale_snapshot_cas_does_not_overwrite_engine_qty(test_db):
    """Claim requires exact current buckets; a stale read cannot $set over a completed mutation."""
    prod_id = await _seed_opening(test_db)
    for doc in test_db.product_stock_state._docs:
        if doc.get("master_item_id") == prod_id or doc.get("_id") == prod_id:
            doc["unprinted_qty"] = 9000
            doc["printed_qty"] = 1800
            doc["total_qty"] = 10800
            break

    orig_find = test_db.product_stock_state.find_one

    async def stale_find_one(query=None, projection=None):
        row = await orig_find(query, projection)
        if not row or "unprinted_qty" not in row:
            return row
        stale = copy.deepcopy(row)
        stale["unprinted_qty"] = 10000
        stale["printed_qty"] = 2000
        stale["total_qty"] = 12000
        return stale

    test_db.product_stock_state.find_one = stale_find_one

    with pytest.raises(HTTPException) as ei:
        await correct_opening_balance(
            test_db,
            prod_id,
            {
                "unprinted_qty": 8000,
                "printed_qty": 1500,
                "total_qty": 9500,
                "note": "stale vs engine",
                "client_request_id": "stale-1",
            },
            user=HOLDING,
        )
    assert ei.value.status_code == 409
    assert ei.value.detail == OPENING_CORRECTION_CONFLICT_MSG

    test_db.product_stock_state.find_one = orig_find
    state = await get_product_stock_state(test_db, prod_id)
    assert state["unprinted_qty"] == 9000
    assert state["printed_qty"] == 1800
    assert state["total_qty"] == 10800
    assert _koreksi(test_db.stock_movements._docs) == []
    assert len(_saldo(test_db.stock_movements._docs)) == 1


@pytest.mark.anyio
async def test_unrelated_item_untouched(test_db):
    a = await _seed_opening(test_db, unprinted=10000, printed=2000)
    b = await _seed_opening(test_db, unprinted=300, printed=50)
    for doc in test_db.master_items._docs:
        if doc.get("id") == b:
            doc["item_code"] = "100020"
            doc["accurate_item_id"] = "acc-100020"
            doc["item_name"] = "14 oz PET Oval 11 gr SJP"
    await correct_opening_balance(
        test_db,
        a,
        {
            "unprinted_qty": 8000,
            "printed_qty": 1500,
            "total_qty": 9500,
            "note": "koreksi A",
            "client_request_id": "only-a",
        },
        user=HOLDING,
    )
    state_a = await get_product_stock_state(test_db, a)
    state_b = await get_product_stock_state(test_db, b)
    assert state_a["total_qty"] == 9500
    assert state_b["unprinted_qty"] == 300
    assert state_b["printed_qty"] == 50
    assert state_b["total_qty"] == 350
    assert not state_b.get("last_opening_correction")
    assert all(d["master_item_id"] != b for d in _koreksi(test_db.stock_movements._docs))
    assert len(_saldo(test_db.stock_movements._docs)) == 2


@pytest.mark.anyio
async def test_ledger_committed_state_pending_recovers_on_retry(test_db):
    """Same request id finishes state commit if KOREKSI exists but buckets were not applied."""
    prod_id = await _seed_opening(test_db)
    event_key = "opening_correction:{0}:crash-1".format(prod_id)
    claim = {
        "event_id": event_key,
        "master_item_id": prod_id,
        "stage": "CLAIMED",
        "source_event_type": "OPENING_BALANCE_CORRECTION",
        "client_request_id": "crash-1",
        "old_qty": {"unprinted_qty": 10000, "printed_qty": 2000, "total_qty": 12000},
        "new_qty": {"unprinted_qty": 8000, "printed_qty": 1500, "total_qty": 9500},
        "note": "crash recovery",
        "actor": HOLDING,
        "claimed_at": "2026-09-15T00:00:00+00:00",
    }
    for doc in test_db.product_stock_state._docs:
        if doc.get("master_item_id") == prod_id or doc.get("_id") == prod_id:
            doc["pending_event"] = copy.deepcopy(claim)
            break
    await test_db.stock_movements.insert_one({
        "_id": event_key,
        "id": event_key,
        "event_key": event_key,
        "master_item_id": prod_id,
        "movement_type": "KOREKSI",
        "unprinted_before": 10000,
        "unprinted_after": 8000,
        "printed_before": 2000,
        "printed_after": 1500,
        "balance_after": 9500,
    })
    res = await correct_opening_balance(
        test_db,
        prod_id,
        {
            "unprinted_qty": 8000,
            "printed_qty": 1500,
            "total_qty": 9500,
            "note": "crash recovery",
            "client_request_id": "crash-1",
        },
        user=HOLDING,
    )
    assert res["idempotent"] is True
    state = await get_product_stock_state(test_db, prod_id)
    assert state["unprinted_qty"] == 8000
    assert state["printed_qty"] == 1500
    assert state["total_qty"] == 9500
    assert not state.get("pending_event")
    assert len(_koreksi(test_db.stock_movements._docs)) == 1
    assert len(_saldo(test_db.stock_movements._docs)) == 1
