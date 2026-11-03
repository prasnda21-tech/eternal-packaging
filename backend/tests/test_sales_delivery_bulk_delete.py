"""BUG-02: bulk SJ delete — validate-all, stock reverse, atomic doc+shipment sync."""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _draft(did="sj-1", po="po-1", qty=1000, sj_number=None):
    return {
        "id": did,
        "delivery_id": did,
        "po_id": po,
        "sj_number": sj_number or f"SJ/{did}",
        "shipment_index": 1,
        "line_items": [{"line_idx": 0, "line_id": "L0", "qty": qty, "name": "A"}],
        "total_qty": qty,
        "is_cancelled": False,
        "status": "SEBAGIAN",
    }


def test_source_bulk_no_longer_uses_unsafe_delete_many():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = src.index("async def bulk_delete_sales_deliveries_endpoint")
    end = src.index("# ---------- PDF PO import ----------")
    body = src[start:end]
    assert ".delete_many(" not in body
    assert "delete_many({" not in body
    assert "_bulk_hard_delete_sales_deliveries_atomic" in body
    assert "_sales_delivery_bulk_hard_delete_blocker" in body
    helper = src[
        src.index("async def _bulk_hard_delete_sales_deliveries_atomic")
        : src.index("async def bulk_delete_sales_deliveries_endpoint")
    ]
    assert "require_transaction_support" in helper
    assert "reverse_r2_deliveries_for_sales_delivery" in helper
    assert "start_transaction" in helper
    # Counter release must happen inside the transactional apply helper, not
    # only after docs are already gone.
    apply_helper = src[
        src.index("async def _apply_bulk_hard_deletes_in_session")
        : src.index("async def _hard_delete_draft_sales_delivery")
    ]
    assert "release_delivery_allocations" in apply_helper
    assert apply_helper.index("release_delivery_allocations") < apply_helper.index(
        "delete_one"
    )
    # Point-10: txn preflight must precede any reverse call.
    assert helper.index("require_transaction_support") < helper.index(
        "reverse_r2_deliveries_for_sales_delivery"
    )


def test_2_precheck_rejects_loading_dependency():
    import server

    delivery = _draft("sj-load")

    async def _run():
        with patch.object(
            server, "_sales_delivery_delete_blocker",
            new=AsyncMock(return_value="Surat Jalan tidak dapat dihapus karena sudah memiliki proses Loading."),
        ):
            reason = await server._sales_delivery_bulk_hard_delete_blocker(delivery)
        assert reason
        assert "Loading" in reason

    asyncio.run(_run())


def test_2_batch_blocked_deletes_nothing():
    """SJ1 valid + SJ2 blocked → entire batch rejected, atomic helper never called."""
    import server

    d1 = _draft("sj-ok", qty=1000)
    d2 = _draft("sj-bad", qty=4000)
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[d1, d2])
    mock_db = MagicMock()
    mock_db.sales_deliveries.find = MagicMock(return_value=cursor)

    async def _run():
        with patch.object(server, "db", mock_db), patch.object(
            server,
            "_sales_delivery_bulk_hard_delete_blocker",
            new=AsyncMock(side_effect=[None, "Surat Jalan tidak dapat dihapus karena sudah memiliki proses Loading."]),
        ), patch.object(
            server,
            "_bulk_hard_delete_sales_deliveries_atomic",
            new=AsyncMock(),
        ) as atomic, patch.object(
            server, "_require_any_perm", AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc:
                await server.bulk_delete_sales_deliveries_endpoint(
                    server.BulkPayload(ids=["sj-ok", "sj-bad"], reason="bulk"),
                    user={"email": "admin@x", "role": "admin", "id": "u1"},
                )
            assert exc.value.status_code == 400
            detail = exc.value.detail
            assert detail["code"] == "BULK_DELETE_BLOCKED"
            assert len(detail["blockers"]) == 1
            assert detail["blockers"][0]["id"] == "sj-bad"
            atomic.assert_not_awaited()

    asyncio.run(_run())


def test_3_stock_reverse_failure_deletes_nothing():
    import server
    from services.physical_stock import PhysicalStockAbortError

    deliveries = [_draft("sj-1"), _draft("sj-2", qty=4000)]

    async def _run():
        with patch(
            "services.physical_stock.reverse_r2_deliveries_for_sales_delivery",
            new=AsyncMock(side_effect=PhysicalStockAbortError("boom")),
        ), patch(
            "services.physical_stock.require_transaction_support",
            new=AsyncMock(),
        ), patch.object(server.db.sales_deliveries, "delete_one", new=AsyncMock()) as delete_one, \
             patch.object(server, "_remove_matching_business_sj_shipment", new=AsyncMock()) as sync:
            with pytest.raises(HTTPException) as exc:
                await server._bulk_hard_delete_sales_deliveries_atomic(
                    deliveries,
                    actor_id="u1",
                    user={"email": "a@x", "id": "u1"},
                )
            assert exc.value.status_code == 503
            assert exc.value.detail["code"] == "BULK_DELETE_STOCK_REVERSE_FAILED"
            delete_one.assert_not_awaited()
            sync.assert_not_awaited()

    asyncio.run(_run())


def test_4_txn_unavailable_rejects_before_any_reverse():
    """Point-10: no replica set → refuse before stock reverse (zero reverse calls)."""
    import server
    from services.physical_stock import PhysicalStockUnavailableError

    deliveries = [_draft("sj-1"), _draft("sj-2", qty=4000)]
    reverse = AsyncMock(return_value={"reversed_confirmations": [], "confirmation_ids": []})

    async def _run():
        with patch(
            "services.physical_stock.reverse_r2_deliveries_for_sales_delivery",
            new=reverse,
        ), patch(
            "services.physical_stock.require_transaction_support",
            new=AsyncMock(side_effect=PhysicalStockUnavailableError("no replica")),
        ), patch.object(server.db.sales_deliveries, "delete_one", new=AsyncMock()) as delete_one, \
             patch.object(server, "_apply_bulk_hard_deletes_in_session", new=AsyncMock()) as apply:
            with pytest.raises(HTTPException) as exc:
                await server._bulk_hard_delete_sales_deliveries_atomic(
                    deliveries, actor_id="u1", user={"email": "a@x"},
                )
            assert exc.value.status_code == 503
            assert exc.value.detail["code"] == "BULK_DELETE_TXN_UNAVAILABLE"
            delete_one.assert_not_awaited()
            apply.assert_not_awaited()
            reverse.assert_not_awaited()

    asyncio.run(_run())


def test_partial_reverse_sj2_fails_deletes_nothing():
    """SJ1 reverse ok + SJ2 reverse fail → no doc delete; reversed_before_failure lists SJ1."""
    import server
    from services.physical_stock import PhysicalStockAbortError

    d1 = _draft("sj-1")
    d2 = _draft("sj-2", qty=4000)
    reverse = AsyncMock(
        side_effect=[
            {"reversed_confirmations": [], "confirmation_ids": [], "id": "sj-1"},
            PhysicalStockAbortError("sj2 boom"),
        ]
    )

    async def _run():
        with patch(
            "services.physical_stock.reverse_r2_deliveries_for_sales_delivery",
            new=reverse,
        ), patch(
            "services.physical_stock.require_transaction_support",
            new=AsyncMock(),
        ), patch.object(
            server, "_apply_bulk_hard_deletes_in_session", new=AsyncMock(),
        ) as apply:
            with pytest.raises(HTTPException) as exc:
                await server._bulk_hard_delete_sales_deliveries_atomic(
                    [d1, d2], actor_id="u1", user={"email": "a@x", "id": "u1"},
                )
            assert exc.value.status_code == 503
            detail = exc.value.detail
            assert detail["code"] == "BULK_DELETE_STOCK_REVERSE_FAILED"
            assert detail["id"] == "sj-2"
            assert len(detail["reversed_before_failure"]) == 1
            assert detail["reversed_before_failure"][0]["id"] == "sj-1"
            apply.assert_not_awaited()
            assert reverse.await_count == 2

    asyncio.run(_run())


def test_txn_fail_after_all_reverses_leaves_docs():
    """All reverses ok + Mongo txn fail → docs remain; retry message; reverse already done."""
    import server

    deliveries = [_draft("sj-1"), _draft("sj-2", qty=4000)]
    reverse = AsyncMock(
        return_value={"reversed_confirmations": [], "confirmation_ids": []},
    )

    async def _run():
        with patch(
            "services.physical_stock.reverse_r2_deliveries_for_sales_delivery",
            new=reverse,
        ), patch(
            "services.physical_stock.require_transaction_support",
            new=AsyncMock(),
        ), patch.object(
            server, "_apply_bulk_hard_deletes_in_session",
            new=AsyncMock(side_effect=RuntimeError("txn boom")),
        ), patch.object(
            server, "_write_audit", new=AsyncMock(),
        ) as audit:
            session = MagicMock()
            txn_cm = MagicMock()
            txn_cm.__aenter__ = AsyncMock(return_value=None)
            txn_cm.__aexit__ = AsyncMock(return_value=False)
            session.start_transaction = MagicMock(return_value=txn_cm)
            session_cm = MagicMock()
            session_cm.__aenter__ = AsyncMock(return_value=session)
            session_cm.__aexit__ = AsyncMock(return_value=False)

            async def _start():
                return session_cm

            with patch.object(server.client, "start_session", side_effect=_start):
                with pytest.raises(HTTPException) as exc:
                    await server._bulk_hard_delete_sales_deliveries_atomic(
                        deliveries, actor_id="u1", user={"email": "a@x", "id": "u1"},
                    )
            assert exc.value.status_code == 503
            assert exc.value.detail["code"] == "BULK_DELETE_TXN_FAILED"
            assert "idempotent" in exc.value.detail["message"].lower()
            assert reverse.await_count == 2
            audit.assert_not_awaited()

    asyncio.run(_run())


def test_1_and_6_atomic_success_reverses_once_then_deletes():
    import server

    d1 = _draft("sj-1", qty=1000)
    d2 = _draft("sj-2", qty=4000)
    deliveries = [d1, d2]
    reverse_calls = []

    async def fake_reverse(db, delivery, *, actor_id, reason=""):
        reverse_calls.append(delivery["id"])
        return {"reversed_confirmations": [], "confirmation_ids": [], "replayed": True}

    class _DeleteResult:
        deleted_count = 1

    mock_db = MagicMock()
    mock_db.sales_deliveries.find_one = AsyncMock(side_effect=[copy.deepcopy(d1), copy.deepcopy(d2)])
    mock_db.sales_deliveries.update_one = AsyncMock()
    mock_db.sales_deliveries.delete_one = AsyncMock(return_value=_DeleteResult())

    async def _run():
        # Session-body path (fence + counter release + document + shipment sync)
        with patch.object(server, "db", mock_db), patch.object(
            server, "_sales_delivery_delete_blocker", new=AsyncMock(return_value=None),
        ), patch.object(
            server, "_remove_matching_business_sj_shipment", new=AsyncMock(),
        ) as sync, patch(
            "services.sales_delivery_allocations.release_delivery_allocations",
            new=AsyncMock(return_value=[{"released": True}]),
        ) as release:
            out_releases = await server._apply_bulk_hard_deletes_in_session(
                MagicMock(), deliveries,
            )
            assert mock_db.sales_deliveries.delete_one.await_count == 2
            assert mock_db.sales_deliveries.update_one.await_count == 2
            assert sync.await_count == 2
            assert release.await_count == 2
            assert len(out_releases) == 2

        # Orchestration: reverse once per SJ, then session apply, then audit
        apply = AsyncMock(return_value=[
            {"id": "sj-1", "release": []},
            {"id": "sj-2", "release": []},
        ])
        with patch(
            "services.physical_stock.reverse_r2_deliveries_for_sales_delivery",
            new=fake_reverse,
        ), patch(
            "services.physical_stock.require_transaction_support",
            new=AsyncMock(),
        ), patch.object(
            server, "_apply_bulk_hard_deletes_in_session", new=apply,
        ), patch(
            "services.sales_delivery_allocations.release_delivery_allocations",
            new=AsyncMock(return_value=[]),
        ), patch.object(
            server.client, "start_session",
        ) as start_session, patch.object(
            server, "_write_audit", new=AsyncMock(),
        ):
            session = MagicMock()
            txn_cm = MagicMock()
            txn_cm.__aenter__ = AsyncMock(return_value=None)
            txn_cm.__aexit__ = AsyncMock(return_value=False)
            session.start_transaction = MagicMock(return_value=txn_cm)
            session_cm = MagicMock()
            session_cm.__aenter__ = AsyncMock(return_value=session)
            session_cm.__aexit__ = AsyncMock(return_value=False)

            async def _start():
                return session_cm

            start_session.side_effect = _start
            out = await server._bulk_hard_delete_sales_deliveries_atomic(
                deliveries, actor_id="u1", user={"email": "a@x", "id": "u1"},
            )
        assert out["deleted_count"] == 2
        assert {x["id"] for x in out["deleted"]} == {"sj-1", "sj-2"}
        assert reverse_calls == ["sj-1", "sj-2"]
        apply.assert_awaited_once()

    asyncio.run(_run())


def test_5_sibling_not_in_batch_untouched_by_helper_contract():
    """Atomic helper only receives selected deliveries — sibling ids never passed."""
    import server

    selected = [_draft("sj-1", qty=1000)]
    seen_ids = []

    async def fake_reverse(db, delivery, *, actor_id, reason=""):
        seen_ids.append(delivery["id"])
        return {"reversed_confirmations": [], "confirmation_ids": []}

    async def _run():
        apply = AsyncMock(return_value=[{"id": "sj-1", "release": []}])
        with patch(
            "services.physical_stock.reverse_r2_deliveries_for_sales_delivery",
            new=fake_reverse,
        ), patch(
            "services.physical_stock.require_transaction_support",
            new=AsyncMock(),
        ), patch.object(
            server, "_apply_bulk_hard_deletes_in_session", new=apply,
        ), patch(
            "services.sales_delivery_allocations.release_delivery_allocations",
            new=AsyncMock(return_value=[]),
        ), patch.object(
            server, "_write_audit", new=AsyncMock(),
        ):
            session = MagicMock()
            txn_cm = MagicMock()
            txn_cm.__aenter__ = AsyncMock(return_value=None)
            txn_cm.__aexit__ = AsyncMock(return_value=False)
            session.start_transaction = MagicMock(return_value=txn_cm)
            session_cm = MagicMock()
            session_cm.__aenter__ = AsyncMock(return_value=session)
            session_cm.__aexit__ = AsyncMock(return_value=False)

            async def _start():
                return session_cm

            with patch.object(server.client, "start_session", side_effect=_start):
                await server._bulk_hard_delete_sales_deliveries_atomic(
                    selected, actor_id="u1", user={"email": "a@x"},
                )
        assert seen_ids == ["sj-1"]
        assert "sj-2" not in seen_ids
        assert apply.await_args.args[1] == selected

    asyncio.run(_run())


def test_7_concurrent_conflict_aborts_without_partial_success_flag():
    import server

    d1 = _draft("sj-1")

    class _DeleteResult:
        deleted_count = 0

    mock_db = MagicMock()
    mock_db.sales_deliveries.find_one = AsyncMock(return_value=copy.deepcopy(d1))
    mock_db.sales_deliveries.update_one = AsyncMock()
    mock_db.sales_deliveries.delete_one = AsyncMock(return_value=_DeleteResult())

    async def _run():
        with patch.object(server, "db", mock_db), patch.object(
            server, "_sales_delivery_delete_blocker", new=AsyncMock(return_value=None),
        ), patch.object(
            server, "_remove_matching_business_sj_shipment", new=AsyncMock(),
        ) as sync, patch(
            "services.sales_delivery_allocations.release_delivery_allocations",
            new=AsyncMock(return_value=[]),
        ):
            with pytest.raises(HTTPException) as exc:
                await server._apply_bulk_hard_deletes_in_session(MagicMock(), [d1])
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "BULK_DELETE_CONFLICT"
            sync.assert_not_awaited()

    asyncio.run(_run())


def test_8_permission_denied():
    import server

    async def deny(user, *perms):
        raise HTTPException(403, "denied")

    async def _run():
        with patch.object(server, "_require_any_perm", side_effect=deny), \
             patch.object(server, "_bulk_hard_delete_sales_deliveries_atomic", new=AsyncMock()) as atomic:
            with pytest.raises(HTTPException) as exc:
                await server.bulk_delete_sales_deliveries_endpoint(
                    server.BulkPayload(ids=["sj-1"], reason="bulk"),
                    user={"email": "x@x", "role": "operator"},
                )
            assert exc.value.status_code == 403
            atomic.assert_not_awaited()

    asyncio.run(_run())


def test_9_precheck_requires_stock_reversal_rejected_from_bulk():
    import server

    delivery = _draft("sj-stock")

    async def _run():
        with patch.object(server, "_sales_delivery_delete_blocker", new=AsyncMock(return_value=None)), \
             patch(
                 "services.physical_stock._r2_confirmation_ids_for_sales_delivery",
                 new=AsyncMock(return_value=["conf-1"]),
             ):
            reason = await server._sales_delivery_bulk_hard_delete_blocker(delivery)
        assert reason
        assert "satu per satu" in reason.lower() or "mutasi" in reason.lower()

    asyncio.run(_run())


def test_9b_operational_only_passes_precheck():
    import server

    delivery = _draft("sj-ok")

    async def _run():
        with patch.object(server, "_sales_delivery_delete_blocker", new=AsyncMock(return_value=None)), \
             patch(
                 "services.physical_stock._r2_confirmation_ids_for_sales_delivery",
                 new=AsyncMock(return_value=[]),
             ), patch(
                 "services.physical_stock.assess_sales_delivery_cancel_stock",
                 new=AsyncMock(return_value={"mode": "operational_only", "reason": "ok"}),
             ):
            assert await server._sales_delivery_bulk_hard_delete_blocker(delivery) is None

    asyncio.run(_run())


def test_10_bug01_reject_still_present():
    """Frozen BUG-01/05 must remain while BUG-02 lands on the same branch."""
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "_reject_legacy_terkirim_completion" in src
    assert 'via="delivered"' in src or "via=\"delivered\"" in src
    from permissions import STAGE_PERMISSION
    assert STAGE_PERMISSION.get(13) == "delivery.update"
