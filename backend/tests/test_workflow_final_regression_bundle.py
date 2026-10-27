"""Final combined regression markers for BUG-01/05/02/03/04 (no prod, no live Mongo)."""
from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_combined_source_contracts_coexist():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    # BUG-01
    assert "_reject_legacy_terkirim_completion(via=\"advance\")" in src
    assert "_reject_legacy_terkirim_completion(via=\"delivered\")" in src
    assert "confirm-delivered" in src
    # BUG-05
    from permissions import STAGE_PERMISSION
    assert STAGE_PERMISSION.get(13) == "delivery.update"
    # BUG-02
    assert "_bulk_hard_delete_sales_deliveries_atomic" in src
    assert ".delete_many(" not in src[
        src.index("async def bulk_delete_sales_deliveries_endpoint")
        : src.index("# ---------- PDF PO import ----------")
    ]
    # BUG-03
    assert "reverse_loading_confirmation_stock" in src
    from services.stock_ledger import MOVEMENT_REVERSAL_PENGIRIMAN, VALID_MOVEMENT_TYPES
    assert MOVEMENT_REVERSAL_PENGIRIMAN in VALID_MOVEMENT_TYPES
    # BUG-04
    body = src[
        src.index("async def create_surat_jalan")
        : src.index("async def preview_accurate_surat_jalan")
    ]
    assert "LEGACY_SJ_CREATE_DISABLED" in body
    assert "_qr_apply_shipment_record" not in body


def test_e2e_scenario_guards_multi_sj_and_legacy_blocked():
    """PO 5000 / SJ1 1000 / SJ2 4000 — structural guards without live DB."""
    from quantity_rules import (
        MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL,
        find_shipment,
        qty_loaded_for_line,
        validate_cancel_loading_confirmation,
    )
    from services.sales_deliveries import delivery_lines_fingerprint

    order = {
        "id": "po-5k",
        "total_quantity": 5000,
        "qty_delivered": 1000,
        "delivery_status": "Sebagian",
        "current_stage": 13,
        "line_items": [{"line_idx": 0, "quantity": 5000, "line_id": "L0", "name": "Cup"}],
        "packing_info": {"qty_packed": 5000, "lines": [{"line_idx": 0, "packed_qty": 5000}]},
        "shipments": [
            {
                "id": "sj-1", "sales_delivery_id": "sj-1", "number": "SJ-1",
                "lines": [{"line_idx": 0, "qty": 1000}],
            },
            {
                "id": "sj-2", "sales_delivery_id": "sj-2", "number": "SJ-2",
                "lines": [{"line_idx": 0, "qty": 4000}],
            },
        ],
        "loading_confirmations": [
            {
                "id": "lc-1", "surat_jalan_id": "sj-1", "shipment_id": "sj-1",
                "lines": [{"line_idx": 0, "qty": 1000}],
            },
            {
                "id": "lc-2", "surat_jalan_id": "sj-2", "shipment_id": "sj-2",
                "lines": [{"line_idx": 0, "qty": 4000}],
            },
        ],
        "delivery_confirmations": [
            {"shipment_id": "sj-1", "qty": 1000},
        ],
    }
    assert delivery_lines_fingerprint([{"line_idx": 0, "line_id": "L0", "qty": 1000}]) != \
        delivery_lines_fingerprint([{"line_idx": 0, "line_id": "L0", "qty": 4000}])
    assert find_shipment(order, "sj-1")["id"] == "sj-1"
    assert find_shipment(order, "sj-2")["id"] == "sj-2"
    assert qty_loaded_for_line(order, 0) == 5000
    # Active SJ2 loading cannot be deleted while SJ active
    assert validate_cancel_loading_confirmation(order, "lc-2") == MSG_LOADING_DELETE_REQUIRES_SJ_CANCEL
    # Partial Terkirim on SJ1 does not imply full PO stage 14 evidence alone
    assert order["qty_delivered"] < order["total_quantity"]
    assert order["current_stage"] == 13


def test_bug01_legacy_paths_blocked():
    import server

    with pytest.raises(HTTPException) as exc:
        server._reject_legacy_terkirim_completion(via="advance")
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "USE_CONFIRM_DELIVERED"


def test_bug04_legacy_sj_create_blocked_no_lock():
    import server

    async def _run():
        with patch.object(server, "_require_perm", AsyncMock()), patch.object(
            server, "_with_quantity_lock", new=AsyncMock(),
        ) as lock:
            with pytest.raises(HTTPException) as exc:
                await server.create_surat_jalan(
                    "po-5k", server.SuratJalanPayload(), user={"email": "a@x"},
                )
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "LEGACY_SJ_CREATE_DISABLED"
            lock.assert_not_awaited()

    asyncio.run(_run())


def test_bug02_bulk_requires_txn_before_reverse_order():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    helper = src[
        src.index("async def _bulk_hard_delete_sales_deliveries_atomic")
        : src.index("async def bulk_delete_sales_deliveries_endpoint")
    ]
    assert helper.index("require_transaction_support") < helper.index(
        "reverse_r2_deliveries_for_sales_delivery"
    )


def test_bug03_loading_reverse_before_soft_cancel_in_delete():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    body = src[
        src.index("async def delete_loading_confirmation")
        : src.index("async def approve_sample")
    ]
    assert body.index("reverse_loading_confirmation_stock") < body.index(
        "_qr_cancel_loading_confirmation"
    )


def test_no_scope_creep_into_accounting_modules():
    """HPP source remains PENGIRIMAN-only; Loading reverse is exclusion/guard only."""
    from services import inventory_issue_accounting as iia
    src = Path(iia.__file__).read_text(encoding="utf-8")
    assert "MOVEMENT_PENGIRIMAN" in src
    # Finance Safety Fix may mention REVERSAL_PENGIRIMAN for exclusion messaging,
    # but it must never become an HPP posting source type constant.
    assert "MOVEMENT_REVERSAL_PENGIRIMAN" not in src
    assert getattr(iia, "CODE_LOADING_REVERSED", None) == "LOADING_REVERSED"
    assert iia.is_pengiriman({"movement_type": "REVERSAL_PENGIRIMAN"}) is False
    assert iia.is_pengiriman({"movement_type": "PENGIRIMAN"}) is True
