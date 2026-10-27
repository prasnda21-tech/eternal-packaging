"""BUG-04: canonical SJ create via sales-deliveries; legacy POST /surat-jalan disabled."""
from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_source_legacy_create_is_fail_closed_409():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = src.index("async def create_surat_jalan")
    end = src.index("async def preview_accurate_surat_jalan")
    body = src[start:end]
    assert "LEGACY_SJ_CREATE_DISABLED" in body
    assert "POST /sales-deliveries" in body
    assert "_qr_apply_shipment_record" not in body
    assert "creating_new" not in body


def test_source_canonical_create_still_uses_sales_deliveries_service():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = src.index("async def create_sales_delivery_endpoint")
    end = src.index("async def get_sales_delivery_endpoint")
    body = src[start:end]
    assert "_create_sales_delivery" in body
    assert "sales.delivery.create" in body


def test_a_c_legacy_create_returns_409_without_mutating_order():
    import server

    async def _run():
        with patch.object(server, "_require_perm", AsyncMock(return_value=None)), patch.object(
            server, "_with_quantity_lock", new=AsyncMock(),
        ) as lock, patch.object(
            server, "_audit_po", new=AsyncMock(),
        ) as audit:
            with pytest.raises(HTTPException) as exc:
                await server.create_surat_jalan(
                    "po-1",
                    server.SuratJalanPayload(delivery_note_number="SJ-X", notes="", lines=None),
                    user={"email": "a@x", "role": "admin"},
                )
            assert exc.value.status_code == 409
            detail = exc.value.detail
            assert detail["code"] == "LEGACY_SJ_CREATE_DISABLED"
            assert "sales-deliveries" in detail["canonical_endpoint"]
            lock.assert_not_awaited()
            audit.assert_not_awaited()

    asyncio.run(_run())


def test_i_legacy_create_still_requires_permission():
    import server

    async def deny(user, *perms):
        raise HTTPException(403, "denied")

    async def _run():
        with patch.object(server, "_require_perm", side_effect=deny):
            with pytest.raises(HTTPException) as exc:
                await server.create_surat_jalan(
                    "po-1",
                    server.SuratJalanPayload(),
                    user={"email": "x@x", "role": "operator"},
                )
            assert exc.value.status_code == 403

    asyncio.run(_run())


def test_a_canonical_endpoint_still_delegates_to_service():
    import server

    delivery = {
        "id": "sd-1", "po_id": "po-1", "po_number": "PO-1",
        "sj_number": "SJ/PO-1", "shipment_index": 1, "total_qty": 1000,
    }

    async def _run():
        with patch.object(server, "_require_any_perm", AsyncMock(return_value=None)), patch.object(
            server, "_create_sales_delivery",
            new=AsyncMock(return_value=delivery),
        ) as create, patch.object(
            server, "_audit_po", new=AsyncMock(),
        ):
            out = await server.create_sales_delivery_endpoint(
                server.SalesDeliveryCreatePayload(
                    po_id="po-1",
                    line_items=[{"line_idx": 0, "qty": 1000}],
                ),
                user={"email": "a@x", "id": "u1"},
            )
            create.assert_awaited_once()
            assert out["delivery"]["id"] == "sd-1"
            assert "berhasil" in out["message"].lower()

    asyncio.run(_run())


def test_b_ensure_order_shipment_synced_exists_on_canonical_path():
    from services import sales_deliveries as sd

    src = inspect.getsource(sd.create_sales_delivery)
    assert "ensure_order_shipment_synced" in src
    assert "_reuse_existing_delivery_if_retry" in src


def test_d_multi_sj_fingerprint_isolates_lines():
    from services.sales_deliveries import delivery_lines_fingerprint

    a = delivery_lines_fingerprint([{"line_idx": 0, "line_id": "L0", "qty": 1000}])
    b = delivery_lines_fingerprint([{"line_idx": 0, "line_id": "L0", "qty": 4000}])
    assert a != b
    assert a == delivery_lines_fingerprint([{"line_idx": 0, "line_id": "L0", "qty": 1000}])


def test_e_f_canonical_rejects_over_remain_and_cancelled_po():
    """Source contract: create_sales_delivery checks cancelled + remaining qty."""
    from services import sales_deliveries as sd

    src = inspect.getsource(sd.create_sales_delivery)
    assert 'order.get("cancelled")' in src
    assert "melebihi sisa PO" in src or "remaining_qty" in src


def test_j_historical_shipment_still_readable_via_quantity_rules():
    from quantity_rules import shipments_stored, find_shipment

    order = {
        "id": "po-legacy",
        "shipments": [
            {
                "id": "ship-only-legacy",
                "number": "SJ-LEGACY",
                "lines": [{"line_idx": 0, "qty": 100}],
                # no sales_delivery_id — historical orphan shape
            },
            {
                "id": "sd-2",
                "sales_delivery_id": "sd-2",
                "number": "SJ-CANON",
                "lines": [{"line_idx": 0, "qty": 400}],
            },
        ],
    }
    stored = shipments_stored(order)
    assert len(stored) == 2
    assert find_shipment(order, "ship-only-legacy")["number"] == "SJ-LEGACY"
    assert find_shipment(order, "sd-2")["sales_delivery_id"] == "sd-2"


def test_g_loading_still_resolves_sj_token_from_shipment_id():
    from quantity_rules import loading_confirmation_sj_token, find_shipment

    conf = {"id": "lc-1", "surat_jalan_id": "sd-2", "shipment_id": "sd-2"}
    order = {
        "shipments": [
            {"id": "sd-1", "number": "SJ-1", "sales_delivery_id": "sd-1"},
            {"id": "sd-2", "number": "SJ-2", "sales_delivery_id": "sd-2"},
        ],
    }
    token = loading_confirmation_sj_token(conf)
    assert token
    sh = find_shipment(order, token)
    assert sh["id"] == "sd-2"


def test_h_delete_sales_delivery_endpoint_still_present():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "async def delete_sales_delivery_endpoint" in src
    assert "async def bulk_delete_sales_deliveries_endpoint" in src


def test_k_retry_reuse_helper_documented_not_blind_duplicate():
    from services.sales_deliveries import _reuse_existing_delivery_if_retry

    doc = inspect.getdoc(_reuse_existing_delivery_if_retry) or ""
    assert "fingerprint" in doc.lower() or "matching" in doc.lower()
    # No client idempotency_key on SalesDeliveryCreatePayload — do not claim unlimited retry safety.
    import server
    fields = getattr(server.SalesDeliveryCreatePayload, "model_fields", None) or {}
    assert "idempotency_key" not in fields


def test_l_bug01_02_03_regression_markers():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    assert "_reject_legacy_terkirim_completion" in src
    assert "_bulk_hard_delete_sales_deliveries_atomic" in src
    assert "reverse_loading_confirmation_stock" in src
    from permissions import STAGE_PERMISSION
    assert STAGE_PERMISSION.get(13) == "delivery.update"
    from services.stock_ledger import MOVEMENT_REVERSAL_PENGIRIMAN, VALID_MOVEMENT_TYPES
    assert MOVEMENT_REVERSAL_PENGIRIMAN in VALID_MOVEMENT_TYPES


def test_frontend_active_create_uses_sales_deliveries_not_legacy():
    """Read-only proof: Pengiriman Pesanan calls createSalesDelivery; createSuratJalan unused."""
    root = Path(__file__).resolve().parents[2] / "frontend" / "src"
    pengiriman = (root / "pages" / "PengirimanPesanan.jsx").read_text(encoding="utf-8")
    assert "createSalesDelivery" in pengiriman
    assert "createSuratJalan" not in pengiriman
    hits = []
    for path in list(root.rglob("*.js")) + list(root.rglob("*.jsx")):
        if path.name == "api.js":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "createSuratJalan" in text:
            hits.append(str(path.relative_to(root)))
    assert hits == []
