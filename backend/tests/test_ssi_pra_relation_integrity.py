"""PR-A SSI relation integrity regressions.

Scope:
- parent_line_idx dual-storage after create remap / addon-parent update
- operator ↔ induk via explicit Add On links (never qty-similarity invent)
- SJ / confirmation line_id + line_idx for print vs polos attribution
- legacy ambiguous stays UNVERIFIED / unresolved
- five-column SSI formulas unchanged (multiwarna counted once)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from services.delivery_history import append_confirmation
from services.order_line_addon import (
    evaluate_process_group,
    resolve_parent_idx,
    sync_parent_line_relations,
    validate_service_print_requires_parent,
)
from services.physical_stock import aggregate_operational_orders
from services.sales_order_prices import _remap_parent_line_ids, price_order_lines
from services.stock_saat_ini_excel import resolve_qty_tercetak


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _price(raw_lines):
    class _Db:
        pass

    return await price_order_lines(_Db(), raw_lines)


def _inv(line_id, mid="m-inv", qty=1000, name="Induk"):
    return {
        "line_id": line_id,
        "master_item_id": mid,
        "name": name,
        "quantity": qty,
        "unit": "pcs",
        "unit_price": 1000,
        "price_source": "manual",
        "item_type": "INVENTORY",
        "production_process": "print",
    }


def _svc(line_id, mid, parent_line_id=None, parent_line_idx=None, qty=1000, name="Warna"):
    row = {
        "line_id": line_id,
        "master_item_id": mid,
        "name": name,
        "quantity": qty,
        "unit": "pcs",
        "unit_price": 200,
        "price_source": "manual",
        "item_type": "SERVICE",
        "production_process": "print",
    }
    if parent_line_id is not None:
        row["parent_line_id"] = parent_line_id
    if parent_line_idx is not None:
        row["parent_line_idx"] = parent_line_idx
    return row


def test_sync_parent_fills_idx_from_id_and_id_from_idx():
    lines = [
        {"line_id": "ln-main", "item_type": "INVENTORY", "quantity": 1000},
        {
            "line_id": "ln-w2",
            "item_type": "SERVICE",
            "parent_line_id": "ln-main",
            "quantity": 1000,
            "production_process": "print",
        },
        {
            "line_id": "ln-w3",
            "item_type": "SERVICE",
            "parent_line_idx": 0,
            "quantity": 1000,
            "production_process": "print",
        },
    ]
    sync_parent_line_relations(lines)
    assert lines[1]["parent_line_idx"] == 0
    assert lines[1]["parent_line_id"] == "ln-main"
    assert lines[2]["parent_line_id"] == "ln-main"
    assert lines[2]["parent_line_idx"] == 0


def test_sync_corrects_stale_idx_when_id_resolves():
    lines = [
        {"line_id": "ln-a", "item_type": "INVENTORY", "quantity": 100},
        {"line_id": "ln-b", "item_type": "INVENTORY", "quantity": 100},
        {
            "line_id": "ln-s",
            "item_type": "SERVICE",
            "parent_line_id": "ln-b",
            "parent_line_idx": 0,  # stale — should follow id → 1
            "quantity": 100,
            "production_process": "print",
        },
    ]
    sync_parent_line_relations(lines)
    assert resolve_parent_idx(lines[2], lines) == 1
    assert lines[2]["parent_line_idx"] == 1
    assert lines[2]["parent_line_id"] == "ln-b"


def test_sync_never_invents_parent_by_qty_similarity():
    """Ambiguous legacy SERVICE with same qty as induk must stay unresolved."""
    lines = [
        {"line_id": "ln-main", "item_type": "INVENTORY", "quantity": 5000, "name": "Cup"},
        {
            "line_id": "ln-orphan",
            "item_type": "SERVICE",
            "quantity": 5000,  # same qty — must NOT invent parent
            "name": "Sablon Cup",
            "production_process": "print",
        },
    ]
    before = dict(lines[1])
    sync_parent_line_relations(lines)
    assert resolve_parent_idx(lines[1], lines) is None
    assert "parent_line_id" not in lines[1]
    assert "parent_line_idx" not in lines[1]
    assert lines[1]["quantity"] == before["quantity"]
    assert validate_service_print_requires_parent(lines) is not None


@pytest.mark.anyio
async def test_price_remap_persists_parent_line_idx_for_warna23():
    priced, _, _ = await _price([
        _inv("tmp-main"),
        _svc("tmp-w2", "m-w2", parent_line_id="tmp-main", name="Warna 2"),
        _svc("tmp-w3", "m-w3", parent_line_id="tmp-main", name="Warna 3"),
    ])
    parent_id = priced[0]["line_id"]
    assert priced[0]["line_idx"] == 0
    for child in priced[1:]:
        assert child["parent_line_id"] == parent_id
        assert child["parent_line_idx"] == 0
        assert resolve_parent_idx(child, priced) == 0
    assert validate_service_print_requires_parent(priced) is None


@pytest.mark.anyio
async def test_price_remap_fixes_stale_client_parent_line_idx():
    priced, _, _ = await _price([
        _inv("tmp-a", mid="m-a", name="Induk A"),
        _inv("tmp-b", mid="m-b", name="Induk B"),
        _svc(
            "tmp-s",
            "m-s",
            parent_line_id="tmp-b",
            parent_line_idx=0,  # client stale idx for A
            name="Warna B",
        ),
    ])
    assert priced[2]["parent_line_id"] == priced[1]["line_id"]
    assert priced[2]["parent_line_idx"] == 1
    assert resolve_parent_idx(priced[2], priced) == 1


@pytest.mark.anyio
async def test_price_idx_only_parent_gets_parent_line_id_stamped():
    priced, _, _ = await _price([
        _inv("tmp-main"),
        _svc("tmp-w2", "m-w2", parent_line_idx=0, name="Warna 2"),
    ])
    assert priced[1]["parent_line_idx"] == 0
    assert priced[1]["parent_line_id"] == priced[0]["line_id"]


def test_remap_rejects_missing_parent_id_without_inventing():
    priced = [
        {**_inv("new-a"), "line_id": "uuid-a", "line_idx": 0},
        {
            **_svc("new-s", "m-s", parent_line_id="ghost", name="Orphan"),
            "line_id": "uuid-s",
            "line_idx": 1,
        },
    ]
    with pytest.raises(HTTPException) as caught:
        _remap_parent_line_ids(priced, {"new-a": "uuid-a", "new-s": "uuid-s"})
    assert caught.value.status_code == 400
    assert "ghost" in str(caught.value.detail)


def test_confirmation_items_stamp_stable_line_id_and_idx():
    order = {
        "line_items": [
            {"line_id": "ln-print", "name": "Cup Print", "quantity": 1000, "production_process": "print"},
            {"line_id": "ln-polos", "name": "Cup Polos", "quantity": 500, "production_process": "non_print"},
        ],
        "shipments": [],
        "delivery_confirmations": [],
    }
    shipment = {
        "id": "sh-1",
        "number": "SJ-1",
        "lines": [
            {"line_idx": 0, "line_id": "ln-print", "qty": 400, "name": "Cup Print"},
            {"line_idx": 1, "line_id": "ln-polos", "qty": 100, "name": "Cup Polos"},
        ],
    }
    order["shipments"] = [shipment]
    rec = append_confirmation(
        order,
        {"email": "op@x", "name": "Op"},
        qty_increment=500,
        now="2026-10-28T00:00:00+00:00",
        action_id="conf-1",
        shipment_id="sh-1",
        shipment=shipment,
    )
    assert rec is not None
    items = rec["items"]
    assert len(items) == 2
    by_idx = {it["line_idx"]: it for it in items}
    assert by_idx[0]["line_id"] == "ln-print"
    assert by_idx[0]["qty"] == 400
    assert by_idx[1]["line_id"] == "ln-polos"
    assert by_idx[1]["qty"] == 100
    # print vs polos distinguishable on one SO confirmation
    assert by_idx[0]["line_id"] != by_idx[1]["line_id"]


def test_confirmation_fills_line_id_from_order_when_shipment_line_omits_it():
    order = {
        "line_items": [
            {"line_id": "ln-only", "name": "Item", "quantity": 100},
        ],
        "shipments": [],
        "delivery_confirmations": [],
    }
    shipment = {
        "id": "sh-x",
        "lines": [{"line_idx": 0, "qty": 40}],  # no line_id on SJ row
    }
    rec = append_confirmation(
        order,
        {"email": "op@x"},
        qty_increment=40,
        now="t",
        shipment=shipment,
    )
    assert rec["items"][0]["line_idx"] == 0
    assert rec["items"][0]["line_id"] == "ln-only"


def test_multiwarna_three_processes_counted_once_on_parent():
    """Acceptance: satu induk + tiga warna → tercetak = parent qty sekali."""
    lines = [
        {
            "line_id": "ln-main",
            "master_item_id": "M1",
            "quantity": 8000,
            "production_process": "print",
            "item_type": "INVENTORY",
        },
        {
            "line_id": "ln-w1",
            "master_item_id": "SVC1",
            "parent_line_id": "ln-main",
            "parent_line_idx": 0,
            "quantity": 8000,
            "production_process": "print",
            "item_type": "SERVICE",
        },
        {
            "line_id": "ln-w2",
            "master_item_id": "SVC2",
            "parent_line_id": "ln-main",
            "parent_line_idx": 0,
            "quantity": 8000,
            "production_process": "print",
            "item_type": "SERVICE",
        },
        {
            "line_id": "ln-w3",
            "master_item_id": "SVC3",
            "parent_line_id": "ln-main",
            "parent_line_idx": 0,
            "quantity": 8000,
            "production_process": "print",
            "item_type": "SERVICE",
        },
    ]
    order = {
        "id": "so-3warna",
        "cancelled": False,
        "line_items": lines,
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 8000},
            {"line_idx": 1, "qty_completed": 8000},
            {"line_idx": 2, "qty_completed": 8000},
            {"line_idx": 3, "qty_completed": 8000},
        ],
    }
    group = evaluate_process_group(order, 0)
    assert group.fully_completed
    assert group.physical_ready_qty == 8000
    assert set(group.child_idxs) == {1, 2, 3}

    progress = aggregate_operational_orders(
        [order],
        exclude_master_ids=["SVC1", "SVC2", "SVC3"],
    )
    resolved = resolve_qty_tercetak(progress["M1"])
    assert resolved["qty"] == 8000
    assert "SVC1" not in progress
    assert "SVC2" not in progress
    assert "SVC3" not in progress


def test_partial_cancel_retry_confirmation_does_not_duplicate_items():
    order = {
        "line_items": [
            {"line_id": "ln-a", "name": "A", "quantity": 1000},
        ],
        "shipments": [],
        "delivery_confirmations": [],
    }
    shipment = {
        "id": "sh-retry",
        "lines": [{"line_idx": 0, "line_id": "ln-a", "qty": 300}],
    }
    r1 = append_confirmation(
        order, {"email": "a"}, qty_increment=300, now="t1",
        action_id="same-action", shipment=shipment,
    )
    r2 = append_confirmation(
        order, {"email": "a"}, qty_increment=300, now="t2",
        action_id="same-action", shipment=shipment,
    )
    assert r1 is r2
    assert len(order["delivery_confirmations"]) == 1
    assert order["delivery_confirmations"][0]["items"][0]["line_id"] == "ln-a"
