"""Stage 3: SJ-level one-shot Loading/POD orchestration + cancel invoice guard."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_sj_stage3_oneshot")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from services.sales_delivery_fulfillment import (
    ONESHOT_BLOCKED_STANDALONE,
    ONESHOT_MISSING_MIRROR,
    SHARED_POD_PROOF_FIELD,
    assess_oneshot_capabilities,
    attach_shared_proof_refs_to_order,
    build_shared_pod_proof,
    confirm_delivered_all_orchestrated,
    confirm_loading_all_orchestrated,
    preflight_loading_all,
    preflight_pod_all,
)
@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


USER = {"id": "u1", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}


async def _po(*, qty=100, price=1000.0, customer_id="cust-s3", branch="br-s3"):
    po_id = str(uuid.uuid4())
    line_id = f"{po_id}_line_0"
    doc = {
        "id": po_id,
        "po_number": f"PO-{po_id[:8]}",
        "customer": "PT Stage3",
        "customer_id": customer_id,
        "currency": "IDR",
        "operational_branch_id": branch,
        "branch_id": "1",
        "cancelled": False,
        "line_items": [{
            "line_id": line_id,
            "name": "Item S3",
            "quantity": qty,
            "unit": "pcs",
            "unit_price": price,
            "production_process": "non_print",
        }],
        "shipments": [],
        "loading_confirmations": [],
        "delivery_confirmations": [],
        "delivery_proofs": [],
    }
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _multi_sj(po_a, po_b, *, qty_a=40, qty_b=30):
    """Insert operational multi-PO SJ + mirrors (bypass create stock path for unit focus)."""
    sj_id = str(uuid.uuid4())
    sj_number = f"SJ-{sj_id[:8]}"
    delivery = {
        "id": sj_id,
        "sj_number": sj_number,
        "po_id": po_a["id"],
        "po_number": po_a["po_number"],
        "customer": po_a["customer"],
        "customer_id": po_a["customer_id"],
        "is_multi_source": True,
        "source_po_ids": [po_a["id"], po_b["id"]],
        "is_cancelled": False,
        "status": "SEBAGIAN",
        "total_qty": qty_a + qty_b,
        "line_items": [
            {
                "line_idx": 0,
                "line_id": po_a["line_items"][0]["line_id"],
                "source_po_id": po_a["id"],
                "source_line_id": po_a["line_items"][0]["line_id"],
                "source_line_idx": 0,
                "qty": qty_a,
                "name": "Item S3",
            },
            {
                "line_idx": 0,
                "line_id": po_b["line_items"][0]["line_id"],
                "source_po_id": po_b["id"],
                "source_line_id": po_b["line_items"][0]["line_id"],
                "source_line_idx": 0,
                "qty": qty_b,
                "name": "Item S3",
            },
        ],
        "source_allocations": [
            {
                "source_po_id": po_a["id"],
                "source_po_number": po_a["po_number"],
                "source_line_id": po_a["line_items"][0]["line_id"],
                "source_line_idx": 0,
                "allocated_qty": qty_a,
            },
            {
                "source_po_id": po_b["id"],
                "source_po_number": po_b["po_number"],
                "source_line_id": po_b["line_items"][0]["line_id"],
                "source_line_idx": 0,
                "allocated_qty": qty_b,
            },
        ],
    }
    await server.db.sales_deliveries.insert_one(delivery)
    for po, q in ((po_a, qty_a), (po_b, qty_b)):
        entry = {
            "id": sj_id,
            "sales_delivery_id": sj_id,
            "number": sj_number,
            "sj_number": sj_number,
            "lines": [{"line_idx": 0, "line_id": po["line_items"][0]["line_id"], "qty": q}],
            "total_qty": q,
            "source_po_ids": [po_a["id"], po_b["id"]],
            "is_multi_source": True,
        }
        await server.db.orders.update_one({"id": po["id"]}, {"$push": {"shipments": entry}})
    delivery.pop("_id", None)
    return delivery


@pytest.mark.anyio
async def test_t7_missing_mirror_fail_closed(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    await server.db.orders.update_one({"id": po_b["id"]}, {"$set": {"shipments": []}})
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=True),
    )
    with pytest.raises(HTTPException) as exc:
        await preflight_loading_all(server.db, delivery["id"])
    assert exc.value.status_code == 409
    assert ONESHOT_MISSING_MIRROR.split(":")[0] in str(exc.value.detail)


@pytest.mark.anyio
async def test_t11_standalone_oneshot_blocked(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=False),
    )
    caps = await assess_oneshot_capabilities(server.db, delivery["id"])
    assert caps["oneshot_loading_available"] is False
    assert caps["oneshot_pod_available"] is False
    assert "standalone" in (caps.get("reason") or "").lower() or ONESHOT_BLOCKED_STANDALONE[:20] in (caps.get("reason") or "")

    with pytest.raises(HTTPException) as exc:
        await preflight_loading_all(server.db, delivery["id"])
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_t1_t5_oneshot_loading_success_and_retry(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.preflight_loading_all",
        AsyncMock(
            return_value={
                "delivery": delivery,
                "orders": {po_a["id"]: po_a, po_b["id"]: po_b},
                "slice_fulfillment": {"is_multi_source": True, "sources": []},
                "pending_sources": [
                    {"source_po_id": po_a["id"], "surat_jalan_id": delivery["id"], "lines": [{"qty": 1}]},
                    {"source_po_id": po_b["id"], "surat_jalan_id": delivery["id"], "lines": [{"qty": 1}]},
                ],
                "already_complete": [],
                "is_multi_source": True,
                "sales_delivery_id": delivery["id"],
            }
        ),
    )
    calls = []

    async def apply_one(po_id, surat_id, child_action):
        calls.append((po_id, child_action))
        return {"confirmation": {"id": "c"}, "idempotent": False}

    action = f"act-{uuid.uuid4().hex[:8]}"
    res = await confirm_loading_all_orchestrated(
        server.db, delivery["id"], USER, action_id=action, apply_one=apply_one
    )
    assert res["ok"] is True
    assert len(calls) == 2
    assert {c[0] for c in calls} == {po_a["id"], po_b["id"]}

    # Retry same action while fence cleared — second run with empty pending = idempotent path via preflight mock
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.preflight_loading_all",
        AsyncMock(
            return_value={
                "delivery": delivery,
                "orders": {po_a["id"]: po_a, po_b["id"]: po_b},
                "slice_fulfillment": {"is_multi_source": True, "loading_complete": True, "sources": []},
                "pending_sources": [],
                "already_complete": [po_a["id"], po_b["id"]],
                "is_multi_source": True,
                "sales_delivery_id": delivery["id"],
            }
        ),
    )
    res2 = await confirm_loading_all_orchestrated(
        server.db, delivery["id"], USER, action_id=action + "-2", apply_one=apply_one
    )
    assert res2["idempotent"] is True


@pytest.mark.anyio
async def test_t8_failure_after_first_loading(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.preflight_loading_all",
        AsyncMock(
            return_value={
                "delivery": delivery,
                "orders": {po_a["id"]: po_a, po_b["id"]: po_b},
                "slice_fulfillment": {"is_multi_source": True, "sources": []},
                "pending_sources": [
                    {"source_po_id": po_a["id"], "surat_jalan_id": delivery["id"], "lines": [{"qty": 1}]},
                    {"source_po_id": po_b["id"], "surat_jalan_id": delivery["id"], "lines": [{"qty": 1}]},
                ],
                "already_complete": [],
                "is_multi_source": True,
                "sales_delivery_id": delivery["id"],
            }
        ),
    )

    async def apply_one(po_id, surat_id, child_action):
        if po_id == po_b["id"]:
            raise HTTPException(400, "stock gagal source kedua")
        return {"ok": True}

    with pytest.raises(HTTPException) as exc:
        await confirm_loading_all_orchestrated(
            server.db, delivery["id"], USER, action_id="fail-load", apply_one=apply_one
        )
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "ONESHOT_LOADING_PARTIAL_FAILED"
    assert any(r.get("source_po_id") == po_a["id"] for r in detail.get("applied") or [])


@pytest.mark.anyio
async def test_t9_t12_pod_shared_proof_and_partial_fail(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=True),
    )
    proof = build_shared_pod_proof(
        recipient_name="Budi",
        photo_refs=[{"id": "p1", "storage_path": "a/1.jpg"}, {"id": "p2", "storage_path": "a/2.jpg"}],
        signature_ref={"id": "sig1", "storage_path": "sig.png", "recipient_name": "Budi"},
        driver_signature_ref={"id": "dsig1", "storage_path": "dsig.png"},
    )
    # Mark loading complete in slice mock via preflight_pod_all internals — set loading on orders
    for po in (po_a, po_b):
        await server.db.orders.update_one(
            {"id": po["id"]},
            {
                "$set": {
                    "loading_confirmations": [{
                        "id": str(uuid.uuid4()),
                        "surat_jalan_id": delivery["id"],
                        "lines": [{"line_idx": 0, "qty": 40 if po["id"] == po_a["id"] else 30}],
                    }]
                }
            },
        )

    attached = []

    async def attach(po_id, delivery_doc, shared):
        attached.append(po_id)
        order = await server.db.orders.find_one({"id": po_id}, {"_id": 0})
        attach_shared_proof_refs_to_order(order, delivery=delivery_doc, shared_proof=shared)
        assert any(p.get("shared_proof_id") == proof["id"] for p in order["delivery_proofs"])
        await server.db.orders.update_one(
            {"id": po_id},
            {"$set": {"delivery_proofs": order["delivery_proofs"]}},
        )

    async def apply_ok(po_id, shipment_id, child_action, shared):
        return {"ok": True}

    res = await confirm_delivered_all_orchestrated(
        server.db,
        delivery["id"],
        USER,
        action_id="pod-ok",
        shared_proof=proof,
        apply_one=apply_ok,
        attach_proof_fn=attach,
    )
    assert res["ok"] is True
    assert res["shared_proof_id"] == proof["id"]
    assert set(attached) == {po_a["id"], po_b["id"]}
    stored = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
    assert (stored.get(SHARED_POD_PROOF_FIELD) or {}).get("id") == proof["id"]

    # Partial fail path
    async def apply_fail(po_id, shipment_id, child_action, shared):
        if po_id == po_b["id"]:
            raise HTTPException(400, "pod gagal")
        return {"ok": True}

    # Reset delivered flags by clearing confirmations; loading still present
    for po in (po_a, po_b):
        await server.db.orders.update_one(
            {"id": po["id"]},
            {"$set": {"delivery_confirmations": [], "qty_delivered": 0}},
        )
    with pytest.raises(HTTPException) as exc:
        await confirm_delivered_all_orchestrated(
            server.db,
            delivery["id"],
            USER,
            action_id="pod-fail",
            shared_proof=proof,
            apply_one=apply_fail,
            attach_proof_fn=attach,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail.get("code") == "ONESHOT_POD_PARTIAL_FAILED"


@pytest.mark.anyio
async def test_t15_standalone_cancel_blocks_invoiced_sj():
    from services.warehouse_stock_standalone import cancel_sales_delivery_document_standalone

    po = await _po(qty=50)
    sj_id = str(uuid.uuid4())
    delivery = {
        "id": sj_id,
        "sj_number": f"SJ-{sj_id[:8]}",
        "po_id": po["id"],
        "source_po_ids": [po["id"]],
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "line_id": po["line_items"][0]["line_id"], "qty": 10, "source_po_id": po["id"], "source_line_id": po["line_items"][0]["line_id"]}],
        "source_allocations": [{
            "source_po_id": po["id"],
            "source_line_id": po["line_items"][0]["line_id"],
            "source_line_idx": 0,
            "allocated_qty": 10,
        }],
    }
    await server.db.sales_deliveries.insert_one(delivery)
    inv_id = str(uuid.uuid4())
    await server.db.sales_invoices.insert_one({
        "id": inv_id,
        "invoice_number": f"INV-{inv_id[:8]}",
        "po_id": po["id"],
        "source_sj_ids": [sj_id],
        "financial_status": "draft",
        "lines": [{
            "source_po_id": po["id"],
            "source_po_line_id": po["line_items"][0]["line_id"],
            "source_sj_id": sj_id,
            "quantity": 5,
        }],
    })
    # Also put a billing claim so invoice_ids_for_sales_delivery finds active invoice
    with pytest.raises(HTTPException) as exc:
        await cancel_sales_delivery_document_standalone(
            server.db, delivery, actor_id="u1", reason="test"
        )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_t16_pending_sj_rejected():
    from services.sales_delivery_allocations import ALLOCATION_COMMIT_PENDING_FIELD

    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    await server.db.sales_deliveries.update_one(
        {"id": delivery["id"]},
        {"$set": {ALLOCATION_COMMIT_PENDING_FIELD: True}},
    )
    with pytest.raises(HTTPException) as exc:
        await assess_oneshot_capabilities(server.db, delivery["id"])
    assert exc.value.status_code in (404, 409)


@pytest.mark.anyio
async def test_t10_rs_probe_true_allows_capability(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=True),
    )
    caps = await assess_oneshot_capabilities(server.db, delivery["id"])
    assert caps["engine_transactions_available"] is True
    assert caps["oneshot_loading_available"] is True


@pytest.mark.anyio
async def test_t18_single_po_oneshot_not_blocked_by_multi_rule(monkeypatch):
    po = await _po()
    sj_id = str(uuid.uuid4())
    delivery = {
        "id": sj_id,
        "sj_number": f"SJ-{sj_id[:8]}",
        "po_id": po["id"],
        "is_multi_source": False,
        "source_po_ids": [po["id"]],
        "is_cancelled": False,
        "line_items": [{
            "line_idx": 0,
            "line_id": po["line_items"][0]["line_id"],
            "source_po_id": po["id"],
            "source_line_id": po["line_items"][0]["line_id"],
            "qty": 10,
        }],
        "source_allocations": [{
            "source_po_id": po["id"],
            "source_line_id": po["line_items"][0]["line_id"],
            "source_line_idx": 0,
            "allocated_qty": 10,
        }],
    }
    await server.db.sales_deliveries.insert_one(delivery)
    await server.db.orders.update_one(
        {"id": po["id"]},
        {"$push": {"shipments": {"id": sj_id, "sales_delivery_id": sj_id, "number": delivery["sj_number"], "lines": [{"line_idx": 0, "qty": 10}], "total_qty": 10}}},
    )
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=False),
    )
    caps = await assess_oneshot_capabilities(server.db, sj_id)
    # Single-PO: standalone engine block does not apply (multi=false)
    assert caps["is_multi_source"] is False
    assert caps["oneshot_loading_available"] is True
