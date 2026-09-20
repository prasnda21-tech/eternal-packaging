"""Phase 4B.1 inventory accounting foundation — no journals, no stock mutation."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_inventory_foundation_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.chart_of_accounts import create_account, ensure_indexes as ensure_coa
from services.hpp_valuation import (
    COST_BASIS_UOM,
    FINANCIAL_INVENTORY_MODEL_VERSION,
    LAYERS,
    FINANCIAL_INVENTORY_BASIS_GR,
    REASON_INVALID_FINANCIAL_UOM_VERSION,
    REASON_LEGACY_HPP_VERSION,
    REASON_NO_ACTIVE_HPP_VERSION,
    REASON_NO_FINANCIAL_INVENTORY_BASIS,
    REASON_PURCHASE_INVENTORY_NOT_CONFIGURED,
    SETTINGS_KEY as HPP_SETTINGS_KEY,
    VERSIONS,
    build_acquisition_events,
    get_hpp_settings,
    query_cost_boundary,
    recalculate_hpp,
)
from services.inventory_accounting import (
    REASON_ALLOCATION_NOT_FINANCIAL,
    REASON_LEGACY_BEFORE_EFFECTIVE_DATE,
    REASON_NON_INVENTORY_ITEM,
    REASON_PURCHASE_ORDER_RECOGNITION,
    annotate_acquisition_event,
    financial_inventory_item_key,
    financial_layer_view,
    financial_uom_snapshot,
    get_goods_receipt_accounting_readiness,
    inventory_event_in_accounting_scope,
)
from services.purchase_accounting_settings import SETTINGS_KEY
from services.purchase_invoice_posting import attach_invoice_accounting_fields


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await ensure_coa(server.db)
    await server.db.app_settings.delete_many({"key": {"$in": [SETTINGS_KEY, "hpp_valuation"]}})
    await server.db[VERSIONS].delete_many({})
    await server.db[LAYERS].delete_many({})
    await server.db.purchase_orders.delete_many({})
    await server.db.purchase_goods_receipts.delete_many({})
    await server.db.purchase_receipt_po_links.delete_many({})
    await server.db.accurate_purchase_receipts.delete_many({})
    await server.db.purchase_invoices.delete_many({})
    await server.db.master_items.delete_many({})
    await server.db.product_unit_settings.delete_many({})
    await server.db.hpp_cost_versions.create_index("id", unique=True)
    await server.db.hpp_cost_layers.create_index([("calculation_version", 1), ("layer_id", 1)], unique=True)
    yield


def _admin():
    app.dependency_overrides[current_user] = lambda: {
        "id": "admin-inv",
        "email": "admin@eternalpackaging.com",
        "name": "Admin INV",
        "role": "admin",
    }
    return {"id": "admin-inv", "email": "admin@eternalpackaging.com", "name": "Admin INV", "role": "admin"}


async def _coa(code_prefix: str, name: str, account_type: str) -> dict:
    return await create_account(
        server.db,
        {"code": f"{code_prefix}{uuid.uuid4().hex[:6]}", "name": name, "account_type": account_type},
        user=_admin(),
    )


async def _configure_ap(payable=None, clearing=None):
    payable = payable or await _coa("21", "Hutang Usaha", "APAY")
    clearing = clearing or await _coa("21", "GRNI", "OCLY")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {
            "key": SETTINGS_KEY,
            "payable_account_id": payable["id"],
            "purchase_clearing_account_id": clearing["id"],
        }},
        upsert=True,
    )
    return payable, clearing


async def _configure_inventory(effective="2026-10-01"):
    payable, clearing = await _configure_ap()
    inventory = await _coa("13", "Persediaan", "INTR")
    cogs = await _coa("51", "HPP", "COGS")
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {
            "inventory_account_id": inventory["id"],
            "cogs_account_id": cogs["id"],
            "inventory_accounting_effective_date": effective,
        }},
        upsert=True,
    )
    return payable, clearing, inventory, cogs


def _po_gr(mid="m-inv", unit="PCS", qty=1000, price=500, received=None, receipt_date="2026-10-01"):
    received = qty if received is None else received
    po = {
        "id": "po-1",
        "po_number": "PB-1",
        "order_date": "2026-09-01",
        "cancelled": False,
        "lines": [{
            "line_id": "ln-1",
            "ordered_qty": qty,
            "unit_price": price,
            "item_name": "Item",
            "item_code": "IT-1",
            "unit": unit,
            "master_item_id": mid,
            "accurate_item_id": "",
        }],
    }
    gr = {
        "id": "gr-1",
        "receipt_number": "GR-1",
        "purchase_order_id": "po-1",
        "receipt_date": receipt_date,
        "cancelled": False,
        "lines": [{
            "line_id": "gl-1",
            "purchase_order_line_id": "ln-1",
            "received_qty": received,
            "unit": unit,
            "master_item_id": mid,
        }],
    }
    return po, gr


async def test_effective_date_scope_is_string_only():
    assert inventory_event_in_accounting_scope("2026-09-30", "2026-10-01") is False
    assert inventory_event_in_accounting_scope("2026-10-01", "2026-10-01") is True
    assert inventory_event_in_accounting_scope("2026-10-02", "2026-10-01") is True
    assert inventory_event_in_accounting_scope("2026-10-01", None) is False
    assert inventory_event_in_accounting_scope("2026-10-01", "") is False
    assert inventory_event_in_accounting_scope(None, "2026-10-01") is False


async def test_uom_carton_and_same_uom_snapshots():
    carton = financial_uom_snapshot(
        transaction_qty=1,
        transaction_uom="CARTON",
        conversion_factor=1000,
        base_uom="PCS",
        transaction_unit_cost=500000,
    )
    assert carton["transaction_qty"] == 1
    assert carton["transaction_uom"] == "CARTON"
    assert carton["conversion_factor"] == 1000
    assert carton["base_qty"] == 1000
    assert carton["base_uom"] == "PCS"
    assert carton["transaction_unit_cost"] == 500000
    assert carton["base_unit_cost"] == 500
    assert carton["total_cost"] == 500000

    same = financial_uom_snapshot(
        transaction_qty=100,
        transaction_uom="PCS",
        conversion_factor=1,
        base_uom="PCS",
        transaction_unit_cost=500,
    )
    assert same["base_qty"] == 100
    assert same["base_unit_cost"] == 500
    assert same["total_cost"] == 50000


async def test_financial_item_key_prefers_master_and_does_not_split():
    key = financial_inventory_item_key({"master_item_id": "m1", "accurate_item_id": ""})
    assert key == "m:m1"
    later = financial_inventory_item_key({"master_item_id": "m1", "accurate_item_id": "ACC-99"})
    assert later == "m:m1"


async def test_allocation_and_po_recognition_are_not_financial():
    po, gr = _po_gr()
    po_events = build_acquisition_events(
        method="FIFO", recognition_point="purchase_order",
        purchase_orders=[po], goods_receipts=[gr], allocations=[],
    )
    annotated_po = annotate_acquisition_event(po_events[0], inventory_configured=True, effective_date="2026-01-01")
    assert annotated_po["financial_inventory_eligible"] is False
    assert annotated_po["financial_ineligibility_reason"] == REASON_PURCHASE_ORDER_RECOGNITION

    alloc = {
        "id": "alloc-1",
        "purchase_order_id": "po-1",
        "purchase_order_line_id": "ln-1",
        "allocated_qty": 10,
        "accurate_receipt_id": "acc-r1",
        "accurate_line_id": "acc-l1",
        "receipt_date": "2026-10-01",
        "cancelled": False,
    }
    events = build_acquisition_events(
        method="FIFO", recognition_point="goods_receipt",
        purchase_orders=[po], goods_receipts=[gr], allocations=[alloc],
    )
    by_type = {e["source_type"]: annotate_acquisition_event(e, inventory_configured=True, effective_date="2026-10-01") for e in events}
    assert by_type["goods_receipt_allocation"]["financial_inventory_eligible"] is False
    assert by_type["goods_receipt_allocation"]["financial_ineligibility_reason"] == REASON_ALLOCATION_NOT_FINANCIAL


async def test_manual_projection_does_not_duplicate_canonical_gr():
    po, gr = _po_gr()
    events = build_acquisition_events(
        method="FIFO", recognition_point="goods_receipt",
        purchase_orders=[po], goods_receipts=[gr], allocations=[],
    )
    financial = [e for e in events if e["source_type"] == "goods_receipt"]
    assert len(financial) == 1
    assert financial[0]["source_id"] == "gr-1"
    await server.db.accurate_purchase_receipts.insert_one({
        "accurate_receipt_id": "manual-proj",
        "source": "manual",
        "goods_receipt_id": "gr-1",
        "receipt_number": "GR-1",
        "cancelled": False,
        "lines": [{"purchase_order_line_id": "ln-1", "received_qty": 1000}],
    })
    still = build_acquisition_events(
        method="FIFO", recognition_point="goods_receipt",
        purchase_orders=[po], goods_receipts=[gr], allocations=[],
    )
    assert len([e for e in still if e["source_type"] == "goods_receipt"]) == 1


async def test_receipt_partial_and_service_mix_readiness():
    await _configure_inventory("2026-10-01")
    inv = {"id": "m-inv", "item_type": "INVENTORY", "unit": "PCS"}
    svc = {"id": "m-svc", "item_type": "SERVICE", "unit": "PCS"}
    await server.db.master_items.insert_many([inv, svc])
    po = {
        "id": "po-mix",
        "po_number": "PB-MIX",
        "order_date": "2026-09-01",
        "cancelled": False,
        "lines": [
            {"line_id": "ln-inv", "ordered_qty": 1000, "unit_price": 500, "unit": "PCS", "master_item_id": "m-inv"},
            {"line_id": "ln-svc", "ordered_qty": 1, "unit_price": 100000, "unit": "PCS", "master_item_id": "m-svc"},
        ],
    }
    gr_partial = {
        "id": "gr-partial",
        "receipt_number": "GR-P",
        "purchase_order_id": "po-mix",
        "receipt_date": "2026-10-02",
        "cancelled": False,
        "lines": [{"line_id": "gl-p", "purchase_order_line_id": "ln-inv", "received_qty": 400, "unit": "PCS"}],
    }
    await server.db.purchase_orders.insert_one(po)
    await server.db.purchase_goods_receipts.insert_one(gr_partial)
    journals_before = await server.db.journal_entries.count_documents({})
    ready = await get_goods_receipt_accounting_readiness(server.db, "gr-partial")
    assert ready["settings_ready"] is True
    assert ready["in_scope_by_date"] is True
    assert ready["financial_inventory_amount"] == 200000
    assert len(ready["eligible_inventory_lines"]) == 1
    assert await server.db.journal_entries.count_documents({}) == journals_before

    gr_mix = {
        "id": "gr-mix",
        "receipt_number": "GR-M",
        "purchase_order_id": "po-mix",
        "receipt_date": "2026-10-02",
        "cancelled": False,
        "lines": [
            {"line_id": "gl-i", "purchase_order_line_id": "ln-inv", "received_qty": 100, "unit": "PCS"},
            {"line_id": "gl-s", "purchase_order_line_id": "ln-svc", "received_qty": 1, "unit": "PCS"},
        ],
    }
    await server.db.purchase_goods_receipts.insert_one(gr_mix)
    mix = await get_goods_receipt_accounting_readiness(server.db, "gr-mix")
    assert mix["financial_inventory_amount"] == 50000
    ineligible = mix["ineligible_lines"]
    assert any(row.get("financial_ineligibility_reason") == REASON_NON_INVENTORY_ITEM for row in ineligible)


async def test_legacy_date_and_ap_invoice_readiness_unchanged():
    await _configure_inventory("2026-10-01")
    await server.db.master_items.insert_one({"id": "m-inv", "item_type": "INVENTORY", "unit": "PCS"})
    po, gr = _po_gr(receipt_date="2026-09-30")
    await server.db.purchase_orders.insert_one(po)
    await server.db.purchase_goods_receipts.insert_one(gr)
    ready = await get_goods_receipt_accounting_readiness(server.db, "gr-1")
    assert ready["in_scope_by_date"] is False
    assert ready["financial_inventory_amount"] == 0
    assert any(
        REASON_LEGACY_BEFORE_EFFECTIVE_DATE in (row.get("financial_ineligibility_reasons") or [])
        for row in ready["ineligible_lines"]
    )

    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$unset": {
            "inventory_account_id": "",
            "cogs_account_id": "",
            "inventory_accounting_effective_date": "",
        }},
    )
    inv = {
        "id": "inv-ap",
        "invoice_number": "FPB-AP",
        "invoice_date": "2026-09-01",
        "supplier": "S",
        "grand_total": 1000,
        "cancelled": False,
    }
    await server.db.purchase_invoices.insert_one(inv)
    attached = await attach_invoice_accounting_fields(server.db, [inv])
    assert attached[0]["accounting_ready"] is True


async def test_carton_recalc_version_immutability_and_zero_journals():
    await _configure_inventory("2026-10-01")
    mid = "m-carton"
    await server.db.master_items.insert_one({"id": mid, "item_type": "INVENTORY", "unit": "PCS"})
    await server.db.product_unit_settings.insert_one({
        "master_item_id": mid,
        "transaction_uom": "CARTON",
        "base_uom": "PCS",
        "conversion_factor": 1000,
    })
    po, gr = _po_gr(mid=mid, unit="CARTON", qty=1, price=500000, received=1, receipt_date="2026-10-01")
    await server.db.purchase_orders.insert_one(po)
    await server.db.purchase_goods_receipts.insert_one(gr)
    journals_before = await server.db.journal_entries.count_documents({})
    stock_before = await server.db.product_stock_state.count_documents({})

    settings = await recalculate_hpp(server.db, method="FIFO", recognition_point="goods_receipt", user=_admin())
    assert settings["last_recalculation_status"] == "SUCCESS"
    assert settings["cost_basis_uom"] == COST_BASIS_UOM
    assert settings["financial_inventory_model_version"] == FINANCIAL_INVENTORY_MODEL_VERSION
    assert settings["financial_cogs_ready"] is True
    assert settings["financial_cogs_ready_reason"] is None
    version_a = settings["active_version"]
    ver_ready = await server.db[VERSIONS].find_one({"id": version_a}, {"_id": 0})
    assert ver_ready["financial_inventory_basis"] == FINANCIAL_INVENTORY_BASIS_GR
    assert ver_ready["financial_eligible_acquisition_count"] >= 1
    layer_a = await server.db[LAYERS].find_one({"calculation_version": version_a}, {"_id": 0})
    assert layer_a["conversion_factor"] == 1000
    assert layer_a["base_qty"] == 1000
    assert layer_a["base_unit_cost"] == 500
    assert layer_a["total_cost"] == 500000
    assert layer_a["financial_inventory_eligible"] is True
    assert layer_a["financial_inventory_item_key"] == f"m:{mid}"
    assert layer_a["canonical_source_type"] == "purchase_goods_receipt"
    assert layer_a["recognized_qty"] == 1
    assert layer_a["unit_cost"] == 500000
    carton_view = financial_layer_view(layer_a)
    assert carton_view["qty"] == 1000
    assert carton_view["uom"] == "PCS"
    assert carton_view["unit_cost"] == 500
    assert carton_view["total_cost"] == 500000

    await server.db.product_unit_settings.update_one(
        {"master_item_id": mid},
        {"$set": {"conversion_factor": 1200}},
    )
    settings_b = await recalculate_hpp(server.db, method="FIFO", recognition_point="goods_receipt", user=_admin())
    version_b = settings_b["active_version"]
    assert version_b != version_a
    untouched = await server.db[LAYERS].find_one({"calculation_version": version_a}, {"_id": 0})
    assert untouched["conversion_factor"] == 1000
    assert untouched["base_qty"] == 1000
    layer_b = await server.db[LAYERS].find_one({"calculation_version": version_b}, {"_id": 0})
    assert layer_b["conversion_factor"] == 1200
    ver_a = await server.db[VERSIONS].find_one({"id": version_a}, {"_id": 0})
    assert ver_a["status"] == "SUPERSEDED"
    assert ver_a["financial_inventory_model_version"] == FINANCIAL_INVENTORY_MODEL_VERSION

    assert await server.db.journal_entries.count_documents({}) == journals_before
    assert await server.db.product_stock_state.count_documents({}) == stock_before
    assert await server.db.journal_entries.count_documents({
        "source_type": {"$in": ["purchase_goods_receipt", "inventory_issue", "cogs"]}
    }) == 0


async def test_manual_receipt_cancel_route_and_accurate_409():
    _admin()
    journals_before = await server.db.journal_entries.count_documents({})
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            created = await client.post("/api/purchase-receipts", json={
                "supplier": "Manual",
                "receipt_date": "2026-09-17",
                "lines": [{
                    "master_item_id": f"manual-{uuid.uuid4().hex[:8]}",
                    "item_code": "MAN-1",
                    "item_name": "Manual Item",
                    "unit": "PCS",
                    "received_qty": 4,
                }],
                "idempotency_key": f"manual-receipt-{uuid.uuid4()}",
            })
            assert created.status_code == 200, created.text
            manual_id = created.json()["receipt"]["accurate_receipt_id"]
            missing_reason = await client.post(f"/api/purchase-receipts/{manual_id}/cancel", json={"reason": "  "})
            assert missing_reason.status_code == 400
            cancelled = await client.post(
                f"/api/purchase-receipts/{manual_id}/cancel",
                json={"reason": "UAT manual cancel"},
            )
            assert cancelled.status_code == 200, cancelled.text
            body = cancelled.json()["receipt"]
            assert body["cancelled"] is True
            retry = await client.post(f"/api/purchase-receipts/{manual_id}/cancel", json={})
            assert retry.status_code == 200
            assert retry.json()["receipt"]["cancelled"] is True

            accurate_id = f"accurate-{uuid.uuid4()}"
            await server.db.accurate_purchase_receipts.insert_one({
                "accurate_receipt_id": accurate_id,
                "receipt_number": "ACC-HISTORY",
                "source": "accurate",
                "valid": True,
                "lines": [],
            })
            blocked = await client.post(
                f"/api/purchase-receipts/{accurate_id}/cancel",
                json={"reason": "tidak boleh"},
            )
            assert blocked.status_code == 409
        assert await server.db.journal_entries.count_documents({}) == journals_before
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_financial_cogs_ready_requires_current_model_version():
    await _configure_inventory("2026-10-01")
    journals_before = await server.db.journal_entries.count_documents({})

    none = await get_hpp_settings(server.db)
    assert none["financial_cogs_ready"] is False
    assert none["financial_cogs_ready_reason"] == REASON_NO_ACTIVE_HPP_VERSION
    assert none["method"] == "FIFO"

    await server.db.app_settings.update_one(
        {"key": HPP_SETTINGS_KEY},
        {"$set": {
            "key": HPP_SETTINGS_KEY,
            "method": "FIFO",
            "recognition_point": "goods_receipt",
            "active_version": "legacy-v1",
        }},
        upsert=True,
    )
    await server.db[VERSIONS].insert_one({
        "id": "legacy-v1",
        "status": "ACTIVE",
        "method": "FIFO",
    })
    legacy = await get_hpp_settings(server.db)
    assert legacy["financial_cogs_ready"] is False
    assert legacy["financial_cogs_ready_reason"] == REASON_LEGACY_HPP_VERSION
    boundary = await query_cost_boundary(server.db)
    assert boundary["financial_cogs_ready"] is False
    assert boundary["financial_inventory_model_version"] is None
    assert boundary["cost_basis_uom"] is None
    assert boundary["financial_cost_basis_uom"] is None
    assert boundary["financial_inventory_basis"] is None
    assert boundary["financial_eligible_acquisition_count"] is None
    assert boundary["supported_financial_inventory_model_version"] == FINANCIAL_INVENTORY_MODEL_VERSION

    await server.db[VERSIONS].update_one(
        {"id": "legacy-v1"},
        {"$set": {
            "financial_uom_normalization_valid": False,
            "financial_inventory_model_version": FINANCIAL_INVENTORY_MODEL_VERSION,
            "cost_basis_uom": COST_BASIS_UOM,
            "financial_inventory_basis": FINANCIAL_INVENTORY_BASIS_GR,
            "financial_eligible_acquisition_count": 1,
        }},
    )
    invalid = await get_hpp_settings(server.db)
    assert invalid["financial_cogs_ready"] is False
    assert invalid["financial_cogs_ready_reason"] == REASON_INVALID_FINANCIAL_UOM_VERSION

    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$unset": {
            "inventory_account_id": "",
            "cogs_account_id": "",
            "inventory_accounting_effective_date": "",
        }},
    )
    unconfigured = await get_hpp_settings(server.db)
    assert unconfigured["financial_cogs_ready"] is False
    assert unconfigured["financial_cogs_ready_reason"] == REASON_PURCHASE_INVENTORY_NOT_CONFIGURED
    assert await server.db.journal_entries.count_documents({}) == journals_before


async def test_po_management_version_is_not_financial_cogs_ready():
    await _configure_inventory("2026-10-01")
    await server.db.master_items.insert_one({"id": "m-inv", "item_type": "INVENTORY", "unit": "PCS"})
    po, _gr = _po_gr(mid="m-inv", unit="PCS", qty=100, price=500, receipt_date="2026-10-01")
    await server.db.purchase_orders.insert_one(po)
    journals_before = await server.db.journal_entries.count_documents({})
    settings = await recalculate_hpp(server.db, method="FIFO", recognition_point="purchase_order", user=_admin())
    assert settings["last_recalculation_status"] == "SUCCESS"
    assert settings["financial_cogs_ready"] is False
    assert settings["financial_cogs_ready_reason"] == REASON_NO_FINANCIAL_INVENTORY_BASIS
    version = await server.db[VERSIONS].find_one({"id": settings["active_version"]}, {"_id": 0})
    assert version["financial_inventory_basis"] == FINANCIAL_INVENTORY_BASIS_GR
    assert version["financial_eligible_acquisition_count"] == 0
    layers = await server.db[LAYERS].find({"calculation_version": settings["active_version"]}, {"_id": 0}).to_list(10)
    assert layers
    view = financial_layer_view(layers[0])
    assert view["financial_inventory_eligible"] is False
    assert view["financial_ineligibility_reason"] == REASON_PURCHASE_ORDER_RECOGNITION
    assert await server.db.journal_entries.count_documents({}) == journals_before


async def test_service_pre_effective_and_allocation_have_no_financial_basis():
    await _configure_inventory("2026-10-01")
    await server.db.master_items.insert_many([
        {"id": "m-svc", "item_type": "SERVICE", "unit": "PCS"},
        {"id": "m-inv", "item_type": "INVENTORY", "unit": "PCS"},
    ])
    journals_before = await server.db.journal_entries.count_documents({})

    po_svc = {
        "id": "po-svc", "po_number": "PB-SVC", "order_date": "2026-09-01", "cancelled": False,
        "lines": [{"line_id": "ln-svc", "ordered_qty": 1, "unit_price": 100000, "unit": "PCS", "master_item_id": "m-svc"}],
    }
    gr_svc = {
        "id": "gr-svc", "receipt_number": "GR-SVC", "purchase_order_id": "po-svc",
        "receipt_date": "2026-10-02", "cancelled": False,
        "lines": [{"line_id": "gl-svc", "purchase_order_line_id": "ln-svc", "received_qty": 1, "unit": "PCS"}],
    }
    await server.db.purchase_orders.insert_one(po_svc)
    await server.db.purchase_goods_receipts.insert_one(gr_svc)
    svc = await recalculate_hpp(server.db, method="FIFO", recognition_point="goods_receipt", user=_admin())
    assert svc["financial_cogs_ready"] is False
    assert svc["financial_cogs_ready_reason"] == REASON_NO_FINANCIAL_INVENTORY_BASIS
    svc_ver = await server.db[VERSIONS].find_one({"id": svc["active_version"]}, {"_id": 0})
    assert svc_ver["financial_eligible_acquisition_count"] == 0

    await server.db.purchase_goods_receipts.delete_many({})
    await server.db.purchase_orders.delete_many({})
    po_old, gr_old = _po_gr(mid="m-inv", unit="PCS", qty=100, price=500, receipt_date="2026-09-30")
    await server.db.purchase_orders.insert_one(po_old)
    await server.db.purchase_goods_receipts.insert_one(gr_old)
    pre = await recalculate_hpp(server.db, method="FIFO", recognition_point="goods_receipt", user=_admin())
    assert pre["financial_cogs_ready"] is False
    assert pre["financial_cogs_ready_reason"] == REASON_NO_FINANCIAL_INVENTORY_BASIS
    pre_ver = await server.db[VERSIONS].find_one({"id": pre["active_version"]}, {"_id": 0})
    assert pre_ver["financial_eligible_acquisition_count"] == 0

    await server.db.purchase_goods_receipts.delete_many({})
    await server.db.purchase_receipt_po_links.insert_one({
        "id": "alloc-1",
        "purchase_order_id": "po-1",
        "purchase_order_line_id": "ln-1",
        "allocated_qty": 100,
        "accurate_receipt_id": "acc-r1",
        "accurate_line_id": "acc-l1",
        "receipt_date": "2026-10-02",
        "cancelled": False,
    })
    alloc = await recalculate_hpp(server.db, method="FIFO", recognition_point="goods_receipt", user=_admin())
    assert alloc["financial_cogs_ready"] is False
    assert alloc["financial_cogs_ready_reason"] == REASON_NO_FINANCIAL_INVENTORY_BASIS
    alloc_ver = await server.db[VERSIONS].find_one({"id": alloc["active_version"]}, {"_id": 0})
    assert alloc_ver["financial_eligible_acquisition_count"] == 0
    query = await query_cost_boundary(server.db)
    assert query["financial_inventory_basis"] == FINANCIAL_INVENTORY_BASIS_GR
    assert query["financial_eligible_acquisition_count"] == 0
    assert query["financial_cogs_ready"] is False
    assert await server.db.journal_entries.count_documents({}) == journals_before
