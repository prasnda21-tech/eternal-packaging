"""BUG-03: Hapus Loading vs PSS stock integrity — fail-closed when reverse unsupported."""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _order_legacy_loading(*, with_sj=True, delivered=False, sibling=True):
    ships = []
    confs = [{
        "id": "lc-1",
        "action_id": "act-1",
        "surat_jalan_id": "sj-1" if with_sj else None,
        "shipment_id": "sj-1" if with_sj else None,
        "sj_number": "SJ-1" if with_sj else None,
        "surat_jalan_name": "SJ-1",
        "lines": [{"line_idx": 0, "qty": 100, "name": "Cup"}],
        "confirmed_by": "a@x",
        "confirmed_at": "t0",
    }]
    if with_sj:
        ships.append({
            "id": "sj-1",
            "number": "SJ-1",
            "sales_delivery_id": "sj-1",
            "is_cancelled": True,
            "cancelled": True,
            "status": "cancelled",
            "lines": [{"line_idx": 0, "qty": 100}],
        })
    if sibling and with_sj:
        ships.append({
            "id": "sj-2",
            "number": "SJ-2",
            "sales_delivery_id": "sj-2",
            "lines": [{"line_idx": 0, "qty": 50}],
        })
        confs.append({
            "id": "lc-2",
            "action_id": "act-2",
            "surat_jalan_id": "sj-2",
            "shipment_id": "sj-2",
            "sj_number": "SJ-2",
            "lines": [{"line_idx": 0, "qty": 50, "name": "Cup"}],
        })
    return {
        "id": "po-1",
        "po_number": "PO-1",
        "total_quantity": 150,
        "line_items": [{
            "line_idx": 0, "line_id": "L0", "name": "Cup",
            "quantity": 150, "master_item_id": "mit-1",
            "production_process": "print", "unit": "PCS",
        }],
        "shipments": ships,
        "qty_delivered": 100 if delivered else 0,
        "delivery_status": "Terkirim" if delivered else "Belum Terkirim",
        "delivery_confirmations": (
            [{"shipment_id": "sj-1", "qty": 100}] if delivered else []
        ),
        "loading_confirmations": confs,
        "physical_stock_snapshot": {
            "enabled": True,
            "stock_out_trigger": "loading_confirmation",
        },
    }


def test_event_ids_cover_claim_and_legacy_paths():
    from services.physical_stock_engine import loading_confirmation_stock_event_ids

    order = _order_legacy_loading()
    conf = order["loading_confirmations"][0]
    ids = loading_confirmation_stock_event_ids(order, conf)
    assert f"loading:po-1:act-1:0" in ids
    assert f"stock_out:loading:po-1:lc-1:0" in ids
    assert f"stock_out:loading:po-1:act-1:0" in ids


def test_a_cancel_without_pss_movements_soft_cancels():
    """A: confirm path already done; Hapus with no PSS movements → soft-cancel OK."""
    import server

    order = _order_legacy_loading(with_sj=True)
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=copy.deepcopy(order))

    async def fake_lock(oid, mutate):
        o = copy.deepcopy(order)
        mutate(o)
        return o

    async def _run():
        with patch.object(server, "db", mock_db), patch.object(
            server, "_require_perm", AsyncMock(return_value=None),
        ), patch.object(
            server, "_with_quantity_lock", side_effect=fake_lock,
        ), patch.object(
            server, "_audit_po", AsyncMock(),
        ), patch.object(
            server, "_strip_sj_bytes", side_effect=lambda o: o,
        ), patch.object(
            server, "_sync_workflow_stage", lambda o: None,
        ), patch.object(
            server, "add_timeline", lambda *a, **k: None,
        ), patch(
            "services.physical_stock_engine.reverse_loading_confirmation_stock",
            new=AsyncMock(return_value={
                "reversed": False, "replayed": True, "movements": [],
                "reason": "no_pss_loading_stock_movements",
            }),
        ) as rev:
            out = await server.delete_loading_confirmation(
                "po-1", "lc-1", user={"email": "a@x", "id": "u1", "name": "A"},
            )
            rev.assert_awaited_once()
            assert out["cancelled_id"] == "lc-1"
            cancelled = next(
                c for c in out["order"]["loading_confirmations"] if c["id"] == "lc-1"
            )
            assert cancelled.get("cancelled") is True
            sibling = next(
                c for c in out["order"]["loading_confirmations"] if c["id"] == "lc-2"
            )
            assert sibling.get("cancelled") is not True

    asyncio.run(_run())


def test_b_sibling_loading_untouched_on_cancel():
    """B covered inside test_a — explicit qty_loaded isolation via quantity_rules."""
    from quantity_rules import (
        cancel_loading_confirmation,
        qty_loaded_for_line,
        validate_cancel_loading_confirmation,
    )

    order = _order_legacy_loading()
    assert validate_cancel_loading_confirmation(order, "lc-1") is None
    cancel_loading_confirmation(order, "lc-1", actor={"email": "a@x"}, cancelled_at="t1")
    assert order["loading_confirmations"][0]["cancelled"] is True
    assert order["loading_confirmations"][1].get("cancelled") is not True
    assert qty_loaded_for_line(order, 0) == 50


def test_c_delivered_qty_rejects_cancel():
    from quantity_rules import (
        MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL,
        validate_cancel_loading_confirmation,
    )

    # Active SJ still present → Hapus Loading blocked (must Batalkan SJ first).
    order = _order_legacy_loading(delivered=False)
    order["shipments"][0].update({
        "is_cancelled": False, "cancelled": False, "status": "SEBAGIAN",
    })
    assert validate_cancel_loading_confirmation(order, "lc-1") == MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL

    # Fully delivered PO → blocked regardless of SJ cancel state.
    full = _order_legacy_loading(delivered=True)
    full["qty_delivered"] = 150
    full["total_quantity"] = 150
    full["packing_info"] = {"qty_packed": 150, "lines": [{"line_idx": 0, "packed_qty": 150}]}
    err = validate_cancel_loading_confirmation(full, "lc-1")
    assert err
    assert "seluruhnya" in err.lower() or "terkirim" in err.lower()


def test_f_pss_reverse_failure_keeps_status():
    """F: reverse engine error → Loading must NOT be soft-cancelled."""
    import server

    order = _order_legacy_loading()
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
            new=AsyncMock(side_effect=HTTPException(503, {
                "message": "fail",
                "code": "LOADING_DELETE_STOCK_REVERSE_FAILED",
            })),
        ):
            with pytest.raises(HTTPException) as exc:
                await server.delete_loading_confirmation(
                    "po-1", "lc-1",
                    user={"email": "a@x", "id": "u1"},
                )
            assert exc.value.status_code == 503
            assert exc.value.detail["code"] == "LOADING_DELETE_STOCK_REVERSE_FAILED"
            assert mutate_called["n"] == 0
            audit.assert_not_awaited()

    asyncio.run(_run())


def test_d_repeat_cancel_after_soft_cancel_is_noop_validate():
    from quantity_rules import (
        cancel_loading_confirmation,
        validate_cancel_loading_confirmation,
    )

    order = _order_legacy_loading()
    cancel_loading_confirmation(order, "lc-1", actor={"email": "a@x"}, cancelled_at="t1")
    err = validate_cancel_loading_confirmation(order, "lc-1")
    assert err  # already cancelled → not found / not cancellable


def test_e_sj_cancel_does_not_auto_reverse_loading_pss():
    """E: single SJ cancel path uses R2 reverse helper, not PSS loading reverse."""
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    # Single delete still soft-cancels SJ; must not call reverse_loading_confirmation_stock
    start = src.index("async def delete_sales_delivery_endpoint")
    end = src.index("async def bulk_delete_sales_deliveries_endpoint")
    body = src[start:end]
    assert "reverse_loading_confirmation_stock" not in body
    assert "reverse_r2_deliveries_for_sales_delivery" in body


def test_h_permission_loading_delete_required():
    import server

    async def deny(user, *perms):
        raise HTTPException(403, "denied")

    async def _run():
        with patch.object(server, "_require_perm", side_effect=deny), patch.object(
            server, "_with_quantity_lock", new=AsyncMock(),
        ) as lock:
            with pytest.raises(HTTPException) as exc:
                await server.delete_loading_confirmation(
                    "po-1", "lc-1", user={"email": "x", "role": "operator"},
                )
            assert exc.value.status_code == 403
            lock.assert_not_awaited()

    asyncio.run(_run())


def test_i_r2_and_standalone_should_not_claim_loading():
    """I: mode gates — claim helpers respect exclusivity (unit-level)."""
    from services.physical_stock_engine import should_claim_loading_stock

    order = _order_legacy_loading()

    async def _run():
        with patch(
            "services.physical_stock_engine.legacy_pss_stock_out_writes_enabled",
            new=AsyncMock(return_value=False),
        ):
            assert await should_claim_loading_stock(MagicMock(), order) is False
        with patch(
            "services.physical_stock_engine.legacy_pss_stock_out_writes_enabled",
            new=AsyncMock(return_value=True),
        ):
            assert await should_claim_loading_stock(MagicMock(), order) is True
            order2 = copy.deepcopy(order)
            order2["physical_stock_snapshot"]["stock_out_trigger"] = "sales_delivery"
            assert await should_claim_loading_stock(MagicMock(), order2) is False

    asyncio.run(_run())


def test_i_reverse_noop_when_no_movements():
    from services.physical_stock_engine import reverse_loading_confirmation_stock

    order = _order_legacy_loading()
    conf = order["loading_confirmations"][0]
    mock_db = MagicMock()
    mock_db.stock_movements.find_one = AsyncMock(return_value=None)

    async def _run():
        out = await reverse_loading_confirmation_stock(mock_db, order, conf, user={"email": "a"})
        assert out["replayed"] is True
        assert out["movements"] == []

    asyncio.run(_run())


def test_source_committed_ignores_cancelled_loading():
    from services.physical_stock_engine import is_source_committed

    order = _order_legacy_loading()
    order["loading_confirmations"][0]["cancelled"] = True
    mock_db = MagicMock()
    mock_db.orders.find_one = AsyncMock(return_value=order)

    async def _run():
        ok = await is_source_committed(mock_db, {
            "reference_type": "loading_confirmation",
            "order_id": "po-1",
            "action_id": "act-1",
        })
        assert ok is False

    asyncio.run(_run())


def test_j_bug01_bug02_still_present():
    """J: regression freeze — BUG-01/05 + BUG-02 helpers remain on branch."""
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "_reject_legacy_terkirim_completion" in src
    assert "_bulk_hard_delete_sales_deliveries_atomic" in src
    from permissions import STAGE_PERMISSION
    assert STAGE_PERMISSION.get(13) == "delivery.update"


def test_g_concurrent_second_cancel_fails_validate():
    """G: after first cancel, second request cannot re-cancel (no double status flip)."""
    from quantity_rules import (
        cancel_loading_confirmation,
        validate_cancel_loading_confirmation,
    )

    order = _order_legacy_loading()
    cancel_loading_confirmation(order, "lc-1", actor={"email": "a"}, cancelled_at="t1")
    assert validate_cancel_loading_confirmation(order, "lc-1")
