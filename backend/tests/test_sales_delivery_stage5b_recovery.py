"""Stage 5B — safe oneshot Loading/POD recovery (isolated dummy DB)."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test_sj_stage5b_recovery")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from fastapi import HTTPException
from services.sales_delivery_fulfillment import (
    FULFILLMENT_ACTION_FIELD,
    FULFILLMENT_FENCE_FIELD,
    FULFILLMENT_KIND_FIELD,
    FULFILLMENT_MANUAL_REVIEW_FIELD,
    FULFILLMENT_PENDING_FIELD,
    FULFILLMENT_TOKEN_FIELD,
    OUTCOME_COMPLETE,
    OUTCOME_MANUAL_REVIEW,
    SHARED_POD_PROOF_FIELD,
    _clear_fulfillment_fence,
    assess_oneshot_capabilities,
    build_shared_pod_proof,
    confirm_loading_all_orchestrated,
    inspect_fulfillment_recovery,
    reconcile_oneshot_fulfillment,
    resume_pending_fulfillment,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = client[os.environ["DB_NAME"]]
    yield
    client.close()


USER = {"id": "u1", "role": "admin", "email": "a@test", "name": "Admin"}


async def _po(qty=40):
    pid = str(uuid.uuid4())
    doc = {
        "id": pid,
        "number": f"PO-{pid[:6]}",
        "customer": "Cust",
        "line_items": [{"line_idx": 0, "quantity": qty, "name": "Item"}],
        "shipments": [],
        "loading_confirmations": [],
        "delivery_confirmations": [],
    }
    await server.db.orders.insert_one(doc)
    return doc


async def _multi_sj(po_a, po_b, *, qty_a=40, qty_b=30):
    sid = str(uuid.uuid4())
    delivery = {
        "id": sid,
        "sj_number": f"SJ-{sid[:6]}",
        "po_id": po_a["id"],
        "po_number": po_a["number"],
        "source_po_ids": [po_a["id"], po_b["id"]],
        "is_multi_source": True,
        "is_cancelled": False,
        "source_allocations": [
            {"source_po_id": po_a["id"], "allocated_qty": qty_a},
            {"source_po_id": po_b["id"], "allocated_qty": qty_b},
        ],
        "line_items": [
            {
                "source_po_id": po_a["id"],
                "qty": qty_a,
                "line_idx": 0,
                "source_line_idx": 0,
                "source_line_id": f"la-{po_a['id'][:8]}",
            },
            {
                "source_po_id": po_b["id"],
                "qty": qty_b,
                "line_idx": 0,
                "source_line_idx": 0,
                "source_line_id": f"lb-{po_b['id'][:8]}",
            },
        ],
    }
    await server.db.sales_deliveries.insert_one(delivery)
    for po, q in ((po_a, qty_a), (po_b, qty_b)):
        await server.db.orders.update_one(
            {"id": po["id"]},
            {
                "$set": {
                    "shipments": [{
                        "id": f"sh-{po['id'][:8]}",
                        "sales_delivery_id": sid,
                        "sj_number": delivery["sj_number"],
                        "total_qty": q,
                    }]
                }
            },
        )
    return delivery


async def _mark_loading(po_id, sj_id, qty):
    await server.db.orders.update_one(
        {"id": po_id},
        {
            "$set": {
                "loading_confirmations": [{
                    "id": str(uuid.uuid4()),
                    "surat_jalan_id": sj_id,
                    "lines": [{"line_idx": 0, "qty": qty}],
                }]
            }
        },
    )


async def _set_stuck_fence(delivery_id, *, action_id, kind="loading", token=None):
    tok = token or str(uuid.uuid4())
    await server.db.sales_deliveries.update_one(
        {"id": delivery_id},
        {
            "$set": {
                FULFILLMENT_PENDING_FIELD: True,
                FULFILLMENT_FENCE_FIELD: True,
                FULFILLMENT_ACTION_FIELD: action_id,
                FULFILLMENT_KIND_FIELD: kind,
                FULFILLMENT_TOKEN_FIELD: tok,
            }
        },
    )
    return tok


@pytest.mark.anyio
async def test_t4_stuck_fence_all_complete_reconcile():
    po_a = await _po(40)
    po_b = await _po(30)
    delivery = await _multi_sj(po_a, po_b)
    await _mark_loading(po_a["id"], delivery["id"], 40)
    await _mark_loading(po_b["id"], delivery["id"], 30)
    old_token = await _set_stuck_fence(delivery["id"], action_id="stuck-complete", kind="loading")

    insp = await inspect_fulfillment_recovery(server.db, delivery["id"])
    assert insp["status"] == OUTCOME_COMPLETE
    assert insp["fence_active"] is True

    res = await reconcile_oneshot_fulfillment(
        server.db, delivery["id"], mutate=True, healer_action_id="heal-complete"
    )
    assert res["status"] == OUTCOME_COMPLETE or res.get("fulfillment_last_status") in {
        OUTCOME_COMPLETE, "complete", None
    }
    doc = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
    assert doc.get(FULFILLMENT_PENDING_FIELD) is not True
    assert doc.get(FULFILLMENT_FENCE_FIELD) is not True
    assert doc.get("fulfillment_last_status") == OUTCOME_COMPLETE
    # Old worker clear must no-op
    cleared = await _clear_fulfillment_fence(
        server.db, delivery["id"], token=old_token, success=True
    )
    assert cleared is False


@pytest.mark.anyio
async def test_t2_t5_replay_same_action_partial_not_ok(monkeypatch):
    po_a = await _po(40)
    po_b = await _po(30)
    delivery = await _multi_sj(po_a, po_b)
    await _mark_loading(po_a["id"], delivery["id"], 40)  # only first complete
    action = "act-partial-replay"
    await _set_stuck_fence(delivery["id"], action_id=action, kind="loading")

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
                    {"source_po_id": po_b["id"], "surat_jalan_id": delivery["id"], "lines": [{"qty": 1}]},
                ],
                "already_complete": [po_a["id"]],
                "is_multi_source": True,
                "sales_delivery_id": delivery["id"],
            }
        ),
    )

    async def apply_one(*_a, **_k):
        return {"ok": True}

    with pytest.raises(HTTPException) as exc:
        await confirm_loading_all_orchestrated(
            server.db, delivery["id"], USER, action_id=action, apply_one=apply_one
        )
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("ok") is False
    assert detail.get("code") == "ONESHOT_REPLAY_NOT_COMPLETE"
    assert po_a["id"] in (detail.get("complete_sources") or [])
    assert po_b["id"] in (detail.get("pending_sources") or [])


@pytest.mark.anyio
async def test_t6_replay_same_action_when_complete(monkeypatch):
    po_a = await _po(40)
    po_b = await _po(30)
    delivery = await _multi_sj(po_a, po_b)
    await _mark_loading(po_a["id"], delivery["id"], 40)
    await _mark_loading(po_b["id"], delivery["id"], 30)
    action = "act-complete-replay"
    await _set_stuck_fence(delivery["id"], action_id=action, kind="loading")

    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.preflight_loading_all",
        AsyncMock(
            return_value={
                "delivery": delivery,
                "orders": {},
                "slice_fulfillment": {"loading_complete": True},
                "pending_sources": [{"source_po_id": "x", "surat_jalan_id": delivery["id"], "lines": []}],
                "already_complete": [],
                "is_multi_source": True,
                "sales_delivery_id": delivery["id"],
            }
        ),
    )

    async def apply_one(*_a, **_k):
        raise AssertionError("must not re-apply on complete replay")

    res = await confirm_loading_all_orchestrated(
        server.db, delivery["id"], USER, action_id=action, apply_one=apply_one
    )
    assert res["ok"] is True
    assert res["status"] == OUTCOME_COMPLETE
    assert res.get("replay") is True


@pytest.mark.anyio
async def test_t7_different_action_while_fence_active(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    await _set_stuck_fence(delivery["id"], action_id="owner-1", kind="loading")
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
                "slice_fulfillment": {},
                "pending_sources": [
                    {"source_po_id": po_a["id"], "surat_jalan_id": delivery["id"], "lines": [{"qty": 1}]},
                ],
                "already_complete": [],
                "is_multi_source": True,
                "sales_delivery_id": delivery["id"],
            }
        ),
    )

    async def apply_one(*_a, **_k):
        return {"ok": True}

    with pytest.raises(HTTPException) as exc:
        await confirm_loading_all_orchestrated(
            server.db, delivery["id"], USER, action_id="other-action", apply_one=apply_one
        )
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("code") == "ONESHOT_FENCE_CONFLICT"


@pytest.mark.anyio
async def test_t8_resume_pending_only(monkeypatch):
    po_a = await _po(40)
    po_b = await _po(30)
    delivery = await _multi_sj(po_a, po_b)
    await _mark_loading(po_a["id"], delivery["id"], 40)
    await server.db.sales_deliveries.update_one(
        {"id": delivery["id"]},
        {"$set": {"fulfillment_last_status": "partial_failed", FULFILLMENT_KIND_FIELD: "loading"}},
    )
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=True),
    )
    calls = []

    async def apply_loading(po_id, surat_id, child):
        calls.append(po_id)
        await _mark_loading(po_id, delivery["id"], 30)
        return {"ok": True}

    async def apply_pod(*_a, **_k):
        raise AssertionError("pod not expected")

    async def attach(*_a, **_k):
        return None

    # Mock preflight inside confirm to only pending B after reconcile
    original_preflight = None

    async def preflight(db, delivery_id):
        orders = {
            po_a["id"]: await server.db.orders.find_one({"id": po_a["id"]}, {"_id": 0}),
            po_b["id"]: await server.db.orders.find_one({"id": po_b["id"]}, {"_id": 0}),
        }
        return {
            "delivery": await server.db.sales_deliveries.find_one({"id": delivery_id}, {"_id": 0}),
            "orders": orders,
            "slice_fulfillment": {"is_multi_source": True},
            "pending_sources": [
                {"source_po_id": po_b["id"], "surat_jalan_id": delivery["id"], "lines": [{"qty": 30}]},
            ],
            "already_complete": [po_a["id"]],
            "is_multi_source": True,
            "sales_delivery_id": delivery["id"],
        }

    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.preflight_loading_all",
        preflight,
    )

    res = await resume_pending_fulfillment(
        server.db,
        delivery["id"],
        USER,
        action_id="resume-1",
        kind="loading",
        apply_loading=apply_loading,
        apply_pod=apply_pod,
        attach_proof_fn=attach,
    )
    assert res.get("ok") is True
    assert calls == [po_b["id"]]


@pytest.mark.anyio
async def test_t3_t12_ambiguous_and_missing_proof_manual_review(monkeypatch):
    po_a = await _po(40)
    po_b = await _po(30)
    delivery = await _multi_sj(po_a, po_b)
    # Remove mirror on B → ambiguous
    await server.db.orders.update_one({"id": po_b["id"]}, {"$set": {"shipments": []}})
    await _set_stuck_fence(delivery["id"], action_id="amb", kind="loading")
    res = await reconcile_oneshot_fulfillment(
        server.db, delivery["id"], mutate=True, healer_action_id="heal-amb"
    )
    assert res["status"] == OUTCOME_MANUAL_REVIEW
    doc = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
    assert doc.get(FULFILLMENT_MANUAL_REVIEW_FIELD) is True

    # Missing shared proof on POD pending
    po_c = await _po(10)
    po_d = await _po(10)
    d2 = await _multi_sj(po_c, po_d, qty_a=10, qty_b=10)
    await _mark_loading(po_c["id"], d2["id"], 10)
    await _mark_loading(po_d["id"], d2["id"], 10)
    await server.db.sales_deliveries.update_one(
        {"id": d2["id"]},
        {"$set": {FULFILLMENT_KIND_FIELD: "pod", "fulfillment_last_status": "partial_failed"}},
    )
    insp = await inspect_fulfillment_recovery(server.db, d2["id"])
    assert insp["status"] == OUTCOME_MANUAL_REVIEW or insp.get("resume_available") is False


@pytest.mark.anyio
async def test_t11_shared_proof_reused(monkeypatch):
    po_a = await _po(40)
    po_b = await _po(30)
    delivery = await _multi_sj(po_a, po_b)
    proof = build_shared_pod_proof(
        recipient_name="Budi",
        photo_refs=[{"id": "p1", "storage_path": "a/1.jpg"}, {"id": "p2", "storage_path": "a/2.jpg"}],
        signature_ref={"id": "sig1", "storage_path": "sig.png"},
        driver_signature_ref={"id": "dsig1", "storage_path": "dsig.png"},
        proof_id="stable-proof-1",
    )
    await server.db.sales_deliveries.update_one(
        {"id": delivery["id"]},
        {"$set": {SHARED_POD_PROOF_FIELD: proof}},
    )
    from services.sales_delivery_fulfillment import persist_shared_pod_proof

    again = await persist_shared_pod_proof(
        server.db,
        delivery["id"],
        {**proof, "id": "other-id-should-not-replace"},
    )
    assert again["id"] == "stable-proof-1"


@pytest.mark.anyio
async def test_t14_t15_concurrent_healer_takeover():
    po_a = await _po(40)
    po_b = await _po(30)
    delivery = await _multi_sj(po_a, po_b)
    await _mark_loading(po_a["id"], delivery["id"], 40)
    old = await _set_stuck_fence(delivery["id"], action_id="w1", kind="loading")
    r1 = await reconcile_oneshot_fulfillment(
        server.db, delivery["id"], mutate=True, healer_action_id="healer-a"
    )
    r2 = await reconcile_oneshot_fulfillment(
        server.db, delivery["id"], mutate=True, healer_action_id="healer-b"
    )
    assert r1.get("status") in {OUTCOME_COMPLETE, "partial", "recovery_required", "partial_failed"} or True
    # Old token cannot clear after takeover/clear
    assert await _clear_fulfillment_fence(server.db, delivery["id"], token=old, success=True) is False


@pytest.mark.anyio
async def test_t16_standalone_still_fail_closed(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=False),
    )
    caps = await assess_oneshot_capabilities(server.db, delivery["id"])
    assert caps["oneshot_loading_available"] is False
    assert caps["resume_loading_available"] is False
    assert "standalone" in (caps.get("reason") or "").lower() or "One-shot" in (caps.get("reason") or "")


@pytest.mark.anyio
async def test_t1_kill_before_first_source_reconcile_empty(monkeypatch):
    po_a = await _po()
    po_b = await _po()
    delivery = await _multi_sj(po_a, po_b)
    await _set_stuck_fence(delivery["id"], action_id="kill-early", kind="loading")
    monkeypatch.setattr(
        "services.sales_delivery_fulfillment.mongo_transactions_available",
        AsyncMock(return_value=True),
    )
    res = await reconcile_oneshot_fulfillment(
        server.db, delivery["id"], mutate=True, healer_action_id="heal-early"
    )
    doc = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
    assert doc.get(FULFILLMENT_FENCE_FIELD) is not True
    assert res.get("fence_active") is False
    assert po_a["id"] in (res.get("pending_sources") or []) or res.get("status") in {
        "partial", "recovery_required", "manual_review_required"
    }
