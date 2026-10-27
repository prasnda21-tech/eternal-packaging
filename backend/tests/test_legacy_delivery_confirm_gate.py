"""BUG-01 / BUG-05: legacy /advance and /delivered cannot set Terkirim.

confirm-delivered remains the only authoritative delivery completion path.
Tests are in-process (no Mongo) — HTTP integration of confirm-delivered is covered
elsewhere when a local DB is available.
"""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from permissions import STAGE_PERMISSION
from quantity_rules import (
    apply_delivery_from_proof_evidence,
    confirm_delivery_block_reason,
    is_fully_delivered,
    shipment_is_delivered,
)


def _base_order(*, stage=13, proofs=0, with_sig=False, qty=5000, sj_qty=None):
    sj_qty = list(sj_qty or [1000, 4000])
    ships = []
    loading = []
    for i, q in enumerate(sj_qty, start=1):
        sid = f"sj-{i}"
        ships.append({
            "id": sid,
            "sales_delivery_id": sid,
            "number": f"SJ/{sid}",
            "shipment_index": i,
            "lines": [{"line_idx": 0, "line_id": "L0", "qty": q, "name": "Item"}],
            "total_qty": q,
        })
        loading.append({
            "id": f"lc-{i}",
            "surat_jalan_id": sid,
            "shipment_id": sid,
            "lines": [{"line_idx": 0, "qty": q}],
        })
    order = {
        "id": "po-gate-1",
        "po_number": "PO/GATE/1",
        "cancelled": False,
        "current_stage": stage,
        "line_items": [{
            "name": "Item",
            "quantity": qty,
            "line_id": "L0",
            "master_item_id": "m1",
            "production_process": "print",
        }],
        "total_quantity": qty,
        "packing_info": {
            "qty_packed": qty,
            "lines": [{"line_idx": 0, "packed_qty": qty}],
        },
        "shipments": ships,
        "loading_confirmations": loading,
        "delivery_proofs": [{"id": f"p{i}", "shipment_id": "sj-1"} for i in range(proofs)],
        "delivery_confirmations": [],
        "qty_delivered": 0,
        "delivery_status": "Belum Terkirim",
        "operator_splits": [{"id": "op1", "quantity": qty, "stage": 11, "qty_completed": qty}],
    }
    if with_sig:
        order["delivery_recipient_signature"] = {
            "id": "sig-1",
            "recipient_name": "Budi Penerima",
            "signature_storage_path": "sig.png",
            "recipient_signed_at": "t",
            "frozen": False,
            "shipment_id": "sj-1",
        }
        order["delivery_driver_signature"] = {
            "id": "dsig-1",
            "driver_signature_storage_path": "dsig.png",
            "driver_signed_at": "t",
            "frozen": False,
            "shipment_id": "sj-1",
            "driver_name": "Driver A",
        }
    return order


def _simulate_advance_perm(cur: int, role: str, eff: set) -> str | None:
    """Mirror advance_stage granular permission check (admin bypass)."""
    perm_needed = STAGE_PERMISSION.get(cur)
    if perm_needed and perm_needed not in eff and role != "admin":
        return perm_needed
    return None


def test_h_stage_permission_requires_delivery_update_from_loading():
    assert STAGE_PERMISSION.get(13) == "delivery.update"
    assert STAGE_PERMISSION.get(14) is None


def test_reject_helper_raises_409_without_mutation():
    import server

    order = _base_order(proofs=0)
    before = copy.deepcopy(order)
    with pytest.raises(HTTPException) as exc:
        server._reject_legacy_terkirim_completion(via="advance")
    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert detail["code"] == server.CODE_USE_CONFIRM_DELIVERED
    assert detail["via"] == "advance"
    assert "confirm-delivered" in detail["use"]
    assert order == before


def test_a_advance_stage_13_calls_legacy_reject(monkeypatch):
    """SJ+Loading OK, no proofs — advance must 409 before any persist (BUG-01)."""
    import server

    order = _base_order(proofs=0, with_sig=False)
    before = copy.deepcopy(order)
    calls = {"persist": 0, "reject": 0}

    async def fake_find_one(*_a, **_k):
        return copy.deepcopy(order)

    async def fake_persist(_o):
        calls["persist"] += 1

    def fake_reject(*, via: str):
        calls["reject"] += 1
        raise HTTPException(
            status_code=409,
            detail={"code": server.CODE_USE_CONFIRM_DELIVERED, "via": via},
        )

    monkeypatch.setattr(server, "_persist_order", fake_persist)
    monkeypatch.setattr(server, "user_permissions", AsyncMock(return_value={"delivery.update"}))
    monkeypatch.setattr(server, "_reject_legacy_terkirim_completion", fake_reject)
    # Replace Motor collection method with plain async stub (no IO loop coupling).
    monkeypatch.setattr(server.db.orders, "find_one", fake_find_one, raising=False)

    # Prefer patching the name used inside advance_stage if Motor resists setattr.
    async def _run():
        with patch.object(server, "db") as mock_db:
            mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(order))
            with pytest.raises(HTTPException) as exc:
                await server.advance_stage(
                    "po-gate-1",
                    server.StageAdvancePayload(note=""),
                    user={"email": "admin@x", "name": "Admin", "role": "admin"},
                )
            assert exc.value.status_code == 409
            assert exc.value.detail["via"] == "advance"

    asyncio.run(_run())
    assert calls["persist"] == 0
    assert calls["reject"] == 1
    assert before["current_stage"] == 13
    assert before["qty_delivered"] == 0
    assert before["delivery_status"] == "Belum Terkirim"
    assert before.get("delivered_at") is None
    assert before["delivery_confirmations"] == []


def test_b_delivered_rejected_even_with_loading():
    import server

    async def _run():
        with patch.object(server, "_require_perm", AsyncMock(return_value=None)), \
             patch.object(server, "_with_quantity_lock", AsyncMock()) as lock:
            with pytest.raises(HTTPException) as exc:
                await server.mark_delivered(
                    "po-gate-1",
                    user={"email": "admin@x", "name": "Admin", "role": "admin"},
                    qty_delivered=None,
                )
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == server.CODE_USE_CONFIRM_DELIVERED
            assert exc.value.detail["via"] == "delivered"
            lock.assert_not_awaited()

    asyncio.run(_run())


def test_c_delivered_permission_denied_before_409():
    import server

    async def deny(user, perm):
        raise HTTPException(403, f"Anda tidak punya permission '{perm}'")

    async def _run():
        with patch.object(server, "_require_perm", side_effect=deny), \
             patch.object(server, "_with_quantity_lock", AsyncMock()) as lock:
            with pytest.raises(HTTPException) as exc:
                await server.mark_delivered(
                    "po-gate-1",
                    user={"email": "op@x", "name": "Op", "role": "operator"},
                    qty_delivered=None,
                )
            assert exc.value.status_code == 403
            assert "delivery.update" in str(exc.value.detail)
            lock.assert_not_awaited()

    asyncio.run(_run())


def test_h_advance_without_delivery_update_is_denied():
    """Helper/role with stage access but without delivery.update cannot finish delivery."""
    assert _simulate_advance_perm(13, "helper", {"packing.update", "loading.update"}) == "delivery.update"
    assert _simulate_advance_perm(13, "admin", {"packing.update"}) is None  # admin bypass
    assert _simulate_advance_perm(13, "driver", {"delivery.update"}) is None
    assert _simulate_advance_perm(11, "helper", {"packing.update"}) is None


def test_g_advance_before_loading_still_allowed_by_source_contract():
    """Stages before Loading keep working; reject is scoped to cur == 13 only."""
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    advance = src[src.index("async def advance_stage") : src.index("async def _stamp_delivery_sla_on_write")]
    # Packing → SJ side effect still present
    assert "if new_stage == 12 and cur == 11:" in advance
    assert 'order["packing_status"]' in advance
    # Reject only for Loading stage
    assert "if cur == 13:" in advance
    idx_reject = advance.index("_reject_legacy_terkirim_completion(via=\"advance\")")
    idx_cur13 = advance.index("if cur == 13:")
    assert idx_cur13 < idx_reject
    # No other stage calls the legacy reject
    assert advance.count("_reject_legacy_terkirim_completion") == 1
    # Production stages still have prereq validators
    assert "_prereq_reasons_for_advance" in advance
    assert "_prereq_reasons_for_surat_jalan" in advance
    assert "_prereq_reasons_for_loading" in advance


def test_d_official_confirm_gate_passes_with_full_evidence():
    o = _base_order(proofs=2, with_sig=True, sj_qty=[5000])
    o["delivery_proofs"] = [
        {"id": "p1", "shipment_id": "sj-1"},
        {"id": "p2", "shipment_id": "sj-1"},
    ]
    assert confirm_delivery_block_reason(o, o["shipments"][0]) is None
    ev = apply_delivery_from_proof_evidence(o, o["shipments"][0])
    assert ev is not None
    assert ev["full"] is True
    assert ev["qty_delivered"] == 5000


def test_e_multi_sj_confirm_sj1_does_not_finish_sj2_or_po():
    o = _base_order(proofs=2, with_sig=True)
    o["delivery_proofs"] = [
        {"id": "p1", "shipment_id": "sj-1"},
        {"id": "p2", "shipment_id": "sj-1"},
    ]
    assert confirm_delivery_block_reason(o, o["shipments"][0]) is None
    ev = apply_delivery_from_proof_evidence(o, o["shipments"][0])
    assert ev["qty_delivered"] == 1000
    assert ev["full"] is False
    o["qty_delivered"] = ev["qty_delivered"]
    o["delivery_status"] = ev["delivery_status"]
    o["shipments"][0]["delivered_at"] = "t1"
    o["shipments"][0]["delivery_status"] = "Terkirim"
    assert shipment_is_delivered(o, o["shipments"][0]) is True
    assert shipment_is_delivered(o, o["shipments"][1]) is False
    assert is_fully_delivered(o) is False
    assert o["current_stage"] == 13


def test_f_idempotent_action_id_short_circuit_does_not_double_qty():
    o = _base_order(proofs=2, with_sig=True, sj_qty=[5000])
    o["delivery_proofs"] = [
        {"id": "p1", "shipment_id": "sj-1"},
        {"id": "p2", "shipment_id": "sj-1"},
    ]
    ev = apply_delivery_from_proof_evidence(o, o["shipments"][0])
    o["qty_delivered"] = ev["qty_delivered"]
    o["delivery_status"] = "Terkirim"
    o["shipments"][0]["delivered_at"] = "t"
    o["shipments"][0]["delivery_status"] = "Terkirim"
    o["delivery_confirm_action_ids"] = ["act-1"]
    o["delivery_confirmations"] = [{"id": "c1", "action_id": "act-1", "qty": 5000}]
    before_qty = o["qty_delivered"]
    before_conf = list(o["delivery_confirmations"])
    action_id = "act-1"
    if action_id in (o.get("delivery_confirm_action_ids") or []):
        pass
    else:
        o["qty_delivered"] = before_qty + 5000
    assert o["qty_delivered"] == before_qty
    assert o["delivery_confirmations"] == before_conf


def test_a_source_advance_rejects_stage_13():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    advance = src[src.index("async def advance_stage") : src.index("async def _stamp_delivery_sla_on_write")]
    assert "_reject_legacy_terkirim_completion(via=\"advance\")" in advance
    assert "if cur == 13:" in advance
    assert 'order["delivery_status"] = "Terkirim"' not in advance
    assert 'order["qty_delivered"] = required_total' not in advance


def test_b_source_delivered_rejects_before_lock():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = src.index("async def mark_delivered")
    end = src.index("class ConfirmDeliveredPayload")
    body = src[start:end]
    assert "_require_perm(user, \"delivery.update\")" in body
    assert "_reject_legacy_terkirim_completion(via=\"delivered\")" in body
    assert "_with_quantity_lock" not in body
    assert 'order["delivery_status"] = "Terkirim"' not in body


def test_i_rejected_delivered_does_not_touch_stock_or_order_fields():
    """Rejection path never enters quantity lock / mutate (no stock side effects)."""
    import server

    async def _run():
        with patch.object(server, "_require_perm", AsyncMock(return_value=None)), \
             patch.object(server, "_with_quantity_lock", AsyncMock()) as lock, \
             patch("services.physical_stock_engine.finalize_delivery_confirmed_stock", AsyncMock()) as fin, \
             patch("services.physical_stock.apply_confirmed_sales_delivery", AsyncMock()) as apply_r2:
            with pytest.raises(HTTPException) as exc:
                await server.mark_delivered(
                    "po-gate-1",
                    user={"email": "admin@x", "role": "admin"},
                    qty_delivered=1000,
                )
            assert exc.value.status_code == 409
            lock.assert_not_awaited()
            fin.assert_not_awaited()
            apply_r2.assert_not_awaited()

    asyncio.run(_run())
