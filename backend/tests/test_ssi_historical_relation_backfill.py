"""PR-B historical SSI relation backfill — dry-run default, no qty/name invent."""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_ssi_prb_uat_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from scripts import ssi_historical_relation_backfill as cli
from services.ssi_historical_relation_backfill import (
    STATUS_ALREADY_OK,
    STATUS_AMBIGUOUS,
    STATUS_CONFLICT,
    STATUS_MISSING,
    STATUS_VERIFIED_CANDIDATE,
    apply_order_relation_ops,
    assert_no_stock_side_effects,
    plan_order_relation_backfill,
    plan_orders_relation_backfill,
    snapshot_ssi_columns_for_order,
)


def _inv(line_id, mid="M1", qty=8000, name="Cup", **extra):
    row = {
        "line_id": line_id,
        "master_item_id": mid,
        "name": name,
        "quantity": qty,
        "item_type": "INVENTORY",
        "production_process": "print",
    }
    row.update(extra)
    return row


def _svc(line_id, mid, qty=8000, name="Sablon", **extra):
    row = {
        "line_id": line_id,
        "master_item_id": mid,
        "name": name,
        "quantity": qty,
        "item_type": "SERVICE",
        "production_process": "print",
    }
    row.update(extra)
    return row


def test_dry_run_plan_does_not_mutate_order():
    order = {
        "id": "po-dry",
        "line_items": [
            _inv("ln-main"),
            _svc("ln-w2", "SVC2", parent_line_id="ln-main"),
        ],
        "operator_splits": [{"line_idx": 0, "qty_completed": 100}],
        "qty_delivered": 0,
    }
    before = copy.deepcopy(order)
    plan = plan_order_relation_backfill(order)
    assert order == before
    assert plan["counts"][STATUS_VERIFIED_CANDIDATE] >= 1


def test_explicit_parent_id_is_verified_candidate_for_idx_sync():
    order = {
        "id": "po-parent-id",
        "line_items": [
            _inv("ln-main"),
            _svc("ln-w2", "SVC2", parent_line_id="ln-main"),
            _svc("ln-w3", "SVC3", parent_line_id="ln-main"),
        ],
    }
    plan = plan_order_relation_backfill(order)
    addon = [f for f in plan["findings"] if f["kind"] == "addon_parent" and f["status"] == STATUS_VERIFIED_CANDIDATE]
    assert len(addon) == 2
    after = apply_order_relation_ops(order, plan["ops"])
    assert after["line_items"][1]["parent_line_idx"] == 0
    assert after["line_items"][2]["parent_line_idx"] == 0
    assert after["line_items"][1]["parent_line_id"] == "ln-main"
    assert_no_stock_side_effects(order, after)


def test_multiwarna_three_processes_sync_to_same_parent():
    order = {
        "id": "po-3w",
        "line_items": [
            _inv("ln-main", qty=8000),
            _svc("ln-w1", "SVC1", parent_line_idx=0, qty=8000),
            _svc("ln-w2", "SVC2", parent_line_id="ln-main", qty=8000),
            _svc("ln-w3", "SVC3", parent_line_id="ln-main", parent_line_idx=0, qty=8000),
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 8000},
            {"line_idx": 1, "qty_completed": 8000},
            {"line_idx": 2, "qty_completed": 8000},
            {"line_idx": 3, "qty_completed": 8000},
        ],
    }
    plan = plan_order_relation_backfill(order)
    after = apply_order_relation_ops(order, plan["ops"])
    for i in (1, 2, 3):
        assert after["line_items"][i]["parent_line_idx"] == 0
        assert after["line_items"][i]["parent_line_id"] == "ln-main"
    # Tercetak once on parent after dual-key sync (operator already present)
    snap = snapshot_ssi_columns_for_order(after, stock_book=6000)
    assert snap["M1"]["qty_tercetak"] == 8000
    assert "SVC1" not in snap


def test_identical_items_without_identity_stay_missing_or_ambiguous():
    """Two identical INVENTORY lines — SJ without identity must not invent by qty/name."""
    order = {
        "id": "po-twin",
        "line_items": [
            _inv("ln-a", mid="M1", qty=1000, name="Cup"),
            _inv("ln-b", mid="M1", qty=1000, name="Cup"),
        ],
        "shipments": [{
            "id": "sh-1",
            "delivery_status": "Terkirim",
            "lines": [{"qty": 500, "name": "Cup"}],  # no line_id / line_idx / accurate
        }],
    }
    plan = plan_order_relation_backfill(order)
    sj = [f for f in plan["findings"] if f["kind"] == "sj_line"]
    assert sj
    assert sj[0]["status"] in (STATUS_MISSING, STATUS_AMBIGUOUS, STATUS_CONFLICT)
    assert plan["ops"] == [] or all(
        op.get("field") != "sj_line" for op in plan["ops"]
    )


def test_ambiguous_accurate_item_id_never_picks_one_row():
    """Shared accurate_item_id on 2 SO lines without line_idx → AMBIGUOUS, no op."""
    order = {
        "id": "po-aid-amb",
        "line_items": [
            {**_inv("ln-a", mid="M1", qty=1000), "accurate_item_id": "ACC-1"},
            {**_inv("ln-b", mid="M1", qty=1000), "accurate_item_id": "ACC-1"},
        ],
        "shipments": [{
            "id": "sh-1",
            "lines": [{"accurate_item_id": "ACC-1", "qty": 50}],
        }],
    }
    plan = plan_order_relation_backfill(order)
    sj = [f for f in plan["findings"] if f["kind"] == "sj_line"][0]
    assert sj["status"] == STATUS_AMBIGUOUS
    assert "ambigu" in (sj.get("reason") or "").lower()
    assert plan["ops"] == []


def test_never_invent_addon_parent_by_qty_or_name():
    order = {
        "id": "po-orphan",
        "line_items": [
            _inv("ln-main", qty=5000, name="Cup Berlian"),
            _svc("ln-orphan", "SVC1", qty=5000, name="Sablon Cup Berlian"),  # same qty+similar name
        ],
    }
    plan = plan_order_relation_backfill(order)
    orphan = [f for f in plan["findings"] if f.get("line_id") == "ln-orphan"][0]
    assert orphan["status"] == STATUS_MISSING
    assert orphan.get("owner_hold") is True
    assert plan["ops"] == []


def test_conflict_when_parent_id_and_idx_disagree():
    order = {
        "id": "po-conflict",
        "line_items": [
            _inv("ln-a", mid="Ma", name="A"),
            _inv("ln-b", mid="Mb", name="B"),
            _svc("ln-s", "SVC", parent_line_id="ln-a", parent_line_idx=1),
        ],
    }
    plan = plan_order_relation_backfill(order)
    f = [x for x in plan["findings"] if x.get("line_id") == "ln-s"][0]
    assert f["status"] == STATUS_CONFLICT
    assert not any(op.get("line_idx") == 2 for op in plan["ops"])


def test_already_filled_relation_not_overwritten():
    order = {
        "id": "po-ok",
        "line_items": [
            _inv("ln-main"),
            _svc("ln-w2", "SVC2", parent_line_id="ln-main", parent_line_idx=0),
        ],
    }
    plan = plan_order_relation_backfill(order)
    f = [x for x in plan["findings"] if x.get("line_id") == "ln-w2"][0]
    assert f["status"] == STATUS_ALREADY_OK
    assert plan["ops"] == []


def test_sj_print_and_polos_stamp_distinct_line_ids():
    order = {
        "id": "po-mix",
        "line_items": [
            _inv("ln-print", mid="M1", qty=10000, production_process="print"),
            {
                **_inv("ln-polos", mid="M1", qty=2000, name="Cup Polos"),
                "production_process": "non_print",
            },
        ],
        "shipments": [{
            "id": "sh-mix",
            "delivery_status": "Terkirim",
            "lines": [
                {"line_idx": 0, "qty": 4000},
                {"line_idx": 1, "qty": 1000},
            ],
        }],
        "delivery_confirmations": [{
            "id": "c1",
            "qty": 5000,
            "items": [
                {"line_idx": 0, "qty": 4000},
                {"line_idx": 1, "qty": 1000},
            ],
        }],
    }
    plan = plan_order_relation_backfill(order)
    assert plan["counts"][STATUS_VERIFIED_CANDIDATE] >= 2
    after = apply_order_relation_ops(order, plan["ops"])
    assert after["shipments"][0]["lines"][0]["line_id"] == "ln-print"
    assert after["shipments"][0]["lines"][1]["line_id"] == "ln-polos"
    assert after["delivery_confirmations"][0]["items"][0]["line_id"] == "ln-print"
    assert after["delivery_confirmations"][0]["items"][1]["line_id"] == "ln-polos"
    assert after["shipments"][0]["lines"][0]["qty"] == 4000
    assert_no_stock_side_effects(order, after)


def test_partial_and_cancelled_sj_identity_safe():
    order = {
        "id": "po-partial",
        "line_items": [_inv("ln-main", qty=10000)],
        "shipments": [
            {
                "id": "sh-ok",
                "delivery_status": "Terkirim",
                "lines": [{"line_idx": 0, "qty": 4000}],
            },
            {
                "id": "sh-cancel",
                "delivery_status": "Dibatalkan",
                "is_cancelled": True,
                "lines": [{"line_idx": 0, "qty": 2000}],
            },
        ],
        "qty_delivered": 4000,
        "operator_splits": [{"line_idx": 0, "qty_completed": 8000}],
    }
    plan = plan_order_relation_backfill(order)
    after = apply_order_relation_ops(order, plan["ops"])
    assert after["qty_delivered"] == 4000
    assert after["operator_splits"] == order["operator_splits"]
    assert after["shipments"][0]["lines"][0]["line_id"] == "ln-main"
    assert after["shipments"][1]["lines"][0]["line_id"] == "ln-main"
    assert after["shipments"][1]["is_cancelled"] is True
    assert_no_stock_side_effects(order, after)


def test_retry_apply_idempotent():
    order = {
        "id": "po-retry",
        "line_items": [
            _inv("ln-main"),
            _svc("ln-w2", "SVC2", parent_line_id="ln-main"),
        ],
        "shipments": [{
            "id": "sh1",
            "lines": [{"line_idx": 0, "qty": 10}],
        }],
    }
    plan1 = plan_order_relation_backfill(order)
    once = apply_order_relation_ops(order, plan1["ops"])
    plan2 = plan_order_relation_backfill(once)
    twice = apply_order_relation_ops(once, plan2["ops"])
    assert twice["line_items"][1]["parent_line_idx"] == once["line_items"][1]["parent_line_idx"]
    assert twice["shipments"][0]["lines"][0]["line_id"] == once["shipments"][0]["lines"][0]["line_id"]
    # Second plan should have no further candidate ops (already ok)
    assert plan2["counts"][STATUS_VERIFIED_CANDIDATE] == 0
    assert plan2["counts"][STATUS_ALREADY_OK] >= 1


def test_customer_sablon_service_not_treated_as_inventory_parent_target():
    """SERVICE process Add On is never rewritten to INVENTORY."""
    order = {
        "id": "po-svc",
        "line_items": [
            _inv("ln-main"),
            _svc("ln-sablon", "SVC1", parent_line_id="ln-main"),
        ],
    }
    after = apply_order_relation_ops(order, plan_order_relation_backfill(order)["ops"])
    assert after["line_items"][1]["item_type"] == "SERVICE"
    assert after["line_items"][1]["master_item_id"] == "SVC1"


def test_stale_parent_id_is_conflict_not_qty_guess():
    order = {
        "id": "po-stale",
        "line_items": [
            _inv("ln-main", qty=100),
            _svc("ln-s", "SVC", qty=100, parent_line_id="ln-ghost"),
        ],
    }
    plan = plan_order_relation_backfill(order)
    f = [x for x in plan["findings"] if x.get("line_id") == "ln-s"][0]
    assert f["status"] == STATUS_CONFLICT
    assert "ghost" in (f.get("parent_line_id") or f.get("reason") or "")


def test_cli_dry_run_default_zero_write(tmp_path):
    orders = [{
        "id": "po-cli",
        "line_items": [
            _inv("ln-main"),
            _svc("ln-w2", "SVC2", parent_line_id="ln-main"),
        ],
    }]
    src = tmp_path / "orders.json"
    src.write_text(json.dumps(orders), encoding="utf-8")
    original = src.read_text(encoding="utf-8")
    rc = cli.main(["--fixture-json", str(src), "--compare-ssi"])
    assert rc == 0
    assert src.read_text(encoding="utf-8") == original


def test_cli_refuses_production_db_name():
    rc = cli.main(["--db-name", "eternal_packaging", "--apply", "--i-understand-local-uat-only"])
    assert rc == 2


def test_cli_apply_requires_safety_latch(tmp_path):
    src = tmp_path / "o.json"
    src.write_text(json.dumps([{"id": "x", "line_items": []}]), encoding="utf-8")
    rc = cli.main(["--fixture-json", str(src), "--apply"])
    assert rc == 2


def test_before_after_ssi_columns_no_double_count():
    order = {
        "id": "po-ssi",
        "cancelled": False,
        "line_items": [
            _inv("ln-main", qty=8000),
            _svc("ln-w2", "SVC2", parent_line_id="ln-main", qty=8000),
            _svc("ln-w3", "SVC3", parent_line_id="ln-main", qty=8000),
        ],
        "operator_splits": [
            {"line_idx": 0, "qty_completed": 8000},
            {"line_idx": 1, "qty_completed": 8000},
            {"line_idx": 2, "qty_completed": 8000},
        ],
        "shipments": [{
            "id": "sh1",
            "delivery_status": "Terkirim",
            "qty_delivered": 4000,
            "lines": [{"line_idx": 0, "qty": 4000}],
        }],
    }
    before = snapshot_ssi_columns_for_order(order, stock_book=6000)
    plan = plan_order_relation_backfill(order)
    after_order = apply_order_relation_ops(order, plan["ops"])
    after = snapshot_ssi_columns_for_order(after_order, stock_book=6000)
    assert_no_stock_side_effects(order, after_order)
    # Parent tercetak once — not 3x
    assert after["M1"]["qty_tercetak"] == 8000
    assert before["M1"]["qty_tercetak"] == 8000
    # Cetak proven 4000 — not doubled by warna lines
    assert after["M1"]["cetak_sudah_dikirim"] == 4000
    assert after["M1"]["tercetak_belum_kirim"] == 4000
    assert "SVC2" not in after


@pytest.mark.anyio
async def test_isolated_uat_mongo_apply_and_idempotent():
    from motor.motor_asyncio import AsyncIOMotorClient

    db_name = "eternal_ssi_prb_uat_test"
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[db_name]
    oid = "uat-prb-rel-1"
    try:
        await db.orders.delete_many({"id": oid})
        order = {
            "id": oid,
            "po_number": "PO-UAT-PRB-1",
            "line_items": [
                _inv("ln-main", qty=1000),
                _svc("ln-w2", "SVC2", parent_line_id="ln-main", qty=1000),
            ],
            "operator_splits": [
                {"line_idx": 0, "qty_completed": 1000},
                {"line_idx": 1, "qty_completed": 1000},
            ],
            "qty_delivered": 0,
            "shipments": [{
                "id": "sh-uat",
                "lines": [{"line_idx": 0, "qty": 200}],
            }],
        }
        await db.orders.insert_one(order)
        # dry-run via CLI planning path
        loaded = await db.orders.find({"id": oid}, {"_id": 0}).to_list(1)
        plan = plan_orders_relation_backfill(loaded)
        assert plan["counts"][STATUS_VERIFIED_CANDIDATE] >= 1
        # apply once
        after = apply_order_relation_ops(loaded[0], plan["plans"][0]["ops"])
        assert_no_stock_side_effects(loaded[0], after)
        await db.orders.replace_one({"id": oid}, after)
        again = await db.orders.find_one({"id": oid}, {"_id": 0})
        plan2 = plan_order_relation_backfill(again)
        assert plan2["counts"][STATUS_VERIFIED_CANDIDATE] == 0
        assert again["line_items"][1]["parent_line_idx"] == 0
        assert again["shipments"][0]["lines"][0]["line_id"] == "ln-main"
        assert again["qty_delivered"] == 0
        assert again["operator_splits"][0]["qty_completed"] == 1000
    finally:
        await db.orders.delete_many({"id": oid})
        client.close()


@pytest.fixture
def anyio_backend():
    return "asyncio"
