"""Finance Safety Fix: Loading cancel ↔ inventory_issue / COGS sync.

Covers conditions A/B/C and focused guards without production data.
Uses MockDB (+ optional Motor when available) — MAIN AGENT focused suite.
"""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.inventory_issue_accounting import (
    CODE_LOADING_CANCEL_ISSUE_POSTED,
    CODE_LOADING_REVERSED,
    CODE_NOT_PENGIRIMAN,
    STATUS_EXCLUDED,
    STATUS_POSTED,
    classify_movement_row,
    find_loading_pengiriman_reversal,
    load_loading_reversed_pengiriman_ids,
    loading_pengiriman_reversal_event_id,
)
from services.inventory_issue_posting import (
    acquire_posting_claim,
    assert_movement_not_loading_reversed,
    clear_posting_claim,
    fence_loading_cancel_against_inventory_issues,
    release_loading_cancel_inventory_issue_fence,
)
from services.physical_stock_engine import (
    execute_durable_stock_event,
    loading_reversal_event_id,
    reverse_loading_confirmation_stock,
)
from services.product_stock_state import get_product_stock_state
from services.stock_ledger import MOVEMENT_PENGIRIMAN, MOVEMENT_REVERSAL_PENGIRIMAN

from tests.test_physical_stock_engine import MockDB, setup_initialized_product


def _settings():
    return {
        "cogs_account_id": "coa-cogs",
        "inventory_accounting_effective_date": "2020-01-01",
    }


def _pengiriman_movement(mid="mov-load-1", **extra):
    base = {
        "id": mid,
        "movement_type": MOVEMENT_PENGIRIMAN,
        "master_item_id": "item-fin-1",
        "date": "2026-01-15T03:00:00+00:00",
        "transaction_qty": 10,
        "base_qty": 10,
        "transaction_uom": "PCS",
        "base_uom": "PCS",
        "conversion_factor": 1,
        "reference_type": "loading_confirmation",
        "reference_id": "lc-1",
        "order_id": "po-1",
        "line_id": "L0",
        "voided": False,
        "cancelled": False,
    }
    base.update(extra)
    return base


def _conf(cid="lc-1", action_id="act-1", qty=100):
    return {
        "id": cid,
        "action_id": action_id,
        "surat_jalan_id": "sj-1",
        "shipment_id": "sj-1",
        "sj_number": "SJ-1",
        "surat_jalan_name": "SJ-1",
        "lines": [{"line_idx": 0, "qty": qty, "name": "Cup"}],
    }


def _order(confs=None, item="item-fin-1"):
    return {
        "id": "po-fin-1",
        "po_number": "PO-FIN-1",
        "line_items": [{
            "line_idx": 0, "line_id": "L0", "name": "Cup",
            "quantity": 200, "master_item_id": item,
            "production_process": "print", "unit": "PCS",
        }],
        "loading_confirmations": confs or [_conf()],
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "loading_confirmation",
        },
    }


# ── C / classify: queue exclusion ───────────────────────────────────────────

def test_c_classify_loading_reversed_is_excluded_not_postable():
    row = classify_movement_row(
        _pengiriman_movement(),
        master={"id": "item-fin-1", "item_type": "INVENTORY", "item_code": "X", "item_name": "Cup"},
        settings=_settings(),
        raw_method="FIFO",
        frozen_method=None,
        live_journal=None,
        loading_reversed=True,
    )
    assert row["status"] == STATUS_EXCLUDED
    assert row["can_post"] is False
    assert row["blocker_code"] == CODE_LOADING_REVERSED


def test_c_classify_posted_journal_still_posted_even_if_reversed_flag():
    """Active posted journal wins classification; cancel must 409 before reverse."""
    row = classify_movement_row(
        _pengiriman_movement(),
        master={"id": "item-fin-1", "item_type": "INVENTORY"},
        settings=_settings(),
        raw_method="FIFO",
        frozen_method=None,
        live_journal={"id": "j1", "status": STATUS_POSTED},
        loading_reversed=True,
    )
    assert row["status"] == STATUS_POSTED
    assert row["can_post"] is False


# ── Helpers: reversal identity ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_reversal_id_helpers_and_batch_load():
    db = MockDB()
    oid = "loading:po-fin-1:act-1:0"
    rid = loading_pengiriman_reversal_event_id(oid)
    assert rid == loading_reversal_event_id(oid)
    await db.stock_movements.insert_one({
        "id": rid,
        "event_key": rid,
        "movement_type": MOVEMENT_REVERSAL_PENGIRIMAN,
        "change_metadata": {"reversal_of_movement_id": oid},
    })
    found = await find_loading_pengiriman_reversal(db, oid)
    assert found and found.get("id") == rid
    ids = await load_loading_reversed_pengiriman_ids(db, [oid, "other"])
    assert oid in ids
    assert "other" not in ids


# ── D / J: posting guard rejects reversed movement ──────────────────────────

@pytest.mark.anyio
async def test_d_j_assert_post_rejects_loading_reversed():
    db = MockDB()
    oid = "mov-rev-1"
    await db.stock_movements.insert_one({
        "id": loading_pengiriman_reversal_event_id(oid),
        "event_key": loading_pengiriman_reversal_event_id(oid),
        "movement_type": MOVEMENT_REVERSAL_PENGIRIMAN,
        "change_metadata": {"reversal_of_movement_id": oid},
    })
    with pytest.raises(Exception) as exc:
        await assert_movement_not_loading_reversed(db, oid)
    assert getattr(exc.value, "code", None) == CODE_LOADING_REVERSED

    # Retry posting same reversed movement still rejected (no journal write path).
    with pytest.raises(Exception) as exc2:
        await assert_movement_not_loading_reversed(db, oid)
    assert getattr(exc2.value, "code", None) == CODE_LOADING_REVERSED


# ── B / I: cancel + reverse without posted issue ────────────────────────────

@pytest.mark.anyio
async def test_a_b_i_cancel_reverse_unposted_and_idempotent():
    db = MockDB()
    await setup_initialized_product(db, "item-fin-1", unprinted=0.0, printed=500.0)
    event_id = "loading:po-fin-1:act-1:0"
    await execute_durable_stock_event(
        db,
        event_id=event_id,
        master_item_id="item-fin-1",
        movement_type=MOVEMENT_PENGIRIMAN,
        transaction_qty=100,
        transaction_uom="PCS",
        source_entity="SUDAH_DICETAK",
        destination_entity="LOADING_PENGIRIMAN",
        reference_number="PO-FIN-1",
        reference_type="loading_confirmation",
        reference_id="po-fin-1",
        order_id="po-fin-1",
        line_idx=0,
        line_id="L0",
        action_id="act-1",
        is_print=True,
        user={"email": "t@x"},
    )
    order = _order()
    out1 = await reverse_loading_confirmation_stock(
        db, order, order["loading_confirmations"][0], user={"email": "t@x"},
    )
    assert out1["reversed"] is True
    st = await get_product_stock_state(db, "item-fin-1")
    assert st["printed_qty"] == 500.0

    # I: retry cancel reverse → replay, no double qty restore
    out2 = await reverse_loading_confirmation_stock(
        db, order, order["loading_confirmations"][0], user={"email": "t@x"},
    )
    assert out2["replayed"] is True
    st2 = await get_product_stock_state(db, "item-fin-1")
    assert st2["printed_qty"] == 500.0

    # C: reversed original excluded from active posting candidates
    rev_ids = await load_loading_reversed_pengiriman_ids(db, [event_id])
    assert event_id in rev_ids
    row = classify_movement_row(
        _pengiriman_movement(event_id),
        master={"id": "item-fin-1", "item_type": "INVENTORY"},
        settings=_settings(),
        raw_method="FIFO",
        frozen_method=None,
        live_journal=None,
        loading_reversed=True,
    )
    assert row["status"] == STATUS_EXCLUDED
    assert row["can_post"] is False


# ── E / F: posted active issue → cancel 409, no stock/status change ─────────

@pytest.mark.anyio
async def test_e_f_posted_issue_blocks_cancel_no_stock_change():
    db = MockDB()
    await setup_initialized_product(db, "item-fin-1", unprinted=0.0, printed=500.0)
    event_id = "loading:po-fin-1:act-1:0"
    await execute_durable_stock_event(
        db,
        event_id=event_id,
        master_item_id="item-fin-1",
        movement_type=MOVEMENT_PENGIRIMAN,
        transaction_qty=100,
        transaction_uom="PCS",
        source_entity="SUDAH_DICETAK",
        destination_entity="LOADING_PENGIRIMAN",
        reference_number="PO-FIN-1",
        reference_type="loading_confirmation",
        reference_id="po-fin-1",
        order_id="po-fin-1",
        line_idx=0,
        line_id="L0",
        action_id="act-1",
        is_print=True,
        user={"email": "t@x"},
    )
    before = await get_product_stock_state(db, "item-fin-1")
    assert before["printed_qty"] == 400.0

    async def fake_active(db_, mid):
        return {"id": "j-posted-1", "status": STATUS_POSTED, "source_id": mid}

    order = _order()
    with patch(
        "services.inventory_issue_posting.find_active_inventory_issue_journal",
        new=fake_active,
    ):
        with pytest.raises(HTTPException) as exc:
            await reverse_loading_confirmation_stock(
                db, order, order["loading_confirmations"][0], user={"email": "t@x"},
            )
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["code"] == CODE_LOADING_CANCEL_ISSUE_POSTED
    after = await get_product_stock_state(db, "item-fin-1")
    assert after["printed_qty"] == before["printed_qty"]
    # No REVERSAL written
    rev = await db.stock_movements.find_one({"id": loading_reversal_event_id(event_id)})
    assert rev is None


# ── G: historically reversed journal is not active posted ───────────────────

@pytest.mark.anyio
async def test_g_reversed_journal_not_active_allows_cancel_fence():
    """find_active_posted_primary excludes status=reversed — fence must allow."""
    db = MockDB()
    mov = _pengiriman_movement("mov-g-1")
    # Seed a reversed primary (status=reversed) — must NOT block fence.
    from services.journal_core import JOURNAL_ENTRIES
    await db[JOURNAL_ENTRIES].insert_one({
        "id": "j-old",
        "source_type": "inventory_issue",
        "source_id": "mov-g-1",
        "status": "reversed",
        "reversal_of": None,
    })
    held = await fence_loading_cancel_against_inventory_issues(db, [mov])
    assert held and held[0][0] == "mov-g-1"
    await release_loading_cancel_inventory_issue_fence(db, held)


# ── H: concurrent post claim vs cancel fence ────────────────────────────────

@pytest.mark.anyio
async def test_h_cancel_fails_when_posting_claim_held():
    db = MockDB()
    mov = _pengiriman_movement("mov-h-1")
    claimed = await acquire_posting_claim(db, "mov-h-1", token="finance-token")
    assert claimed is not None
    with pytest.raises(HTTPException) as exc:
        await fence_loading_cancel_against_inventory_issues(db, [mov])
    assert exc.value.status_code == 409
    assert "POSTING_IN_PROGRESS" in str(exc.value.detail.get("code") or "")
    await clear_posting_claim(db, "mov-h-1", token="finance-token")


@pytest.mark.anyio
async def test_h_post_guard_after_cancel_reverse():
    """After reverse exists, post guard rejects (no journal)."""
    db = MockDB()
    oid = "mov-h-post"
    await db.stock_movements.insert_one({
        "id": loading_pengiriman_reversal_event_id(oid),
        "movement_type": MOVEMENT_REVERSAL_PENGIRIMAN,
        "change_metadata": {"reversal_of_movement_id": oid},
    })
    with pytest.raises(Exception) as exc:
        await assert_movement_not_loading_reversed(db, oid)
    assert getattr(exc.value, "code", None) == CODE_LOADING_REVERSED


# ── K: no PSS Loading movement → fence no-op / reverse no-op ────────────────

@pytest.mark.anyio
async def test_k_no_pss_movements_normal_cancel_path():
    db = MockDB()
    order = _order()
    out = await reverse_loading_confirmation_stock(
        db, order, order["loading_confirmations"][0], user={"email": "t@x"},
    )
    assert out.get("reason") == "no_pss_loading_stock_movements"
    assert out["reversed"] is False


# ── Endpoint: posted issue 409 does not soft-cancel Loading ─────────────────

def test_e_f_endpoint_posted_issue_keeps_loading_status():
    import server

    order = {
        "id": "po-1",
        "po_number": "PO-1",
        "total_quantity": 100,
        "line_items": [{
            "line_idx": 0, "line_id": "L0", "name": "Cup",
            "quantity": 100, "master_item_id": "mit-1",
            "production_process": "print", "unit": "PCS",
        }],
        "shipments": [{
            "id": "sj-1", "number": "SJ-1", "sales_delivery_id": "sj-1",
            "is_cancelled": True, "cancelled": True, "status": "cancelled",
            "lines": [{"line_idx": 0, "qty": 100}],
        }],
        "qty_delivered": 0,
        "loading_confirmations": [{
            "id": "lc-1", "action_id": "act-1",
            "surat_jalan_id": "sj-1", "shipment_id": "sj-1",
            "sj_number": "SJ-1", "surat_jalan_name": "SJ-1",
            "lines": [{"line_idx": 0, "qty": 100, "name": "Cup"}],
            "confirmed_by": "a@x", "confirmed_at": "t0",
        }],
        "physical_stock_snapshot": {
            "enabled": True, "stock_out_trigger": "loading_confirmation",
        },
    }
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(order))
    mutate_called = {"n": 0}

    async def fake_lock(oid, mutate):
        mutate_called["n"] += 1
        o = copy.deepcopy(order)
        mutate(o)
        return o

    async def _run():
        with patch.object(server, "db", mock_db), patch.object(
            server, "_require_perm", AsyncMock(return_value=None),
        ), patch.object(
            server, "_with_quantity_lock", side_effect=fake_lock,
        ), patch.object(
            server, "_audit_po", new=AsyncMock(),
        ) as audit, patch(
            "services.physical_stock_engine.reverse_loading_confirmation_stock",
            new=AsyncMock(side_effect=HTTPException(409, {
                "message": "posted",
                "code": CODE_LOADING_CANCEL_ISSUE_POSTED,
            })),
        ):
            with pytest.raises(HTTPException) as exc:
                await server.delete_loading_confirmation(
                    "po-1", "lc-1", user={"email": "a@x", "id": "u1"},
                )
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == CODE_LOADING_CANCEL_ISSUE_POSTED
            assert mutate_called["n"] == 0
            audit.assert_not_awaited()

    asyncio.run(_run())


def test_c_non_pengiriman_still_excluded():
    row = classify_movement_row(
        _pengiriman_movement(movement_type=MOVEMENT_REVERSAL_PENGIRIMAN),
        master={"id": "item-fin-1", "item_type": "INVENTORY"},
        settings=_settings(),
        raw_method="FIFO",
        frozen_method=None,
        live_journal=None,
        loading_reversed=False,
    )
    assert row["status"] == STATUS_EXCLUDED
    assert row["blocker_code"] == CODE_NOT_PENGIRIMAN
