"""Focused tests: Bukti Payment on Penerimaan Penjualan (sales receipts)."""
import base64
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from server import app, current_user
from services import storage as obj_storage
from services.sales_receipt_payment_proof import KIND, SOURCE_TYPE, MAX_PROOF_BYTES
from workflow_gates import payment_ok, penjadwalan_unlocked, design_ok

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x08"
    b"\xff\xc4\x00\x14\x10\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xaa\xff\xd9"
)
TINY_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def bind_motor_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await server.db.sales_invoices.delete_many({"po_number": {"$regex": r"^TEST-PROOF-"}})
    await server.db.sales_receipts.delete_many({"po_number": {"$regex": r"^TEST-PROOF-"}})
    await server.db.orders.delete_many({"po_number": {"$regex": r"^TEST-PROOF-"}})
    await server.db.files.delete_many({"kind": KIND})
    obj_storage._reset_provider_for_tests()
    yield
    await server.db.files.delete_many({"kind": KIND})


async def _seed_order(*, design_approved=True, prefix="TEST-PROOF"):
    now = datetime.now(timezone.utc).isoformat()
    order_id = str(uuid.uuid4())
    po_number = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
    doc = {
        "id": order_id,
        "po_number": po_number,
        "customer": "PT Proof Test",
        "outlet": "Outlet",
        "recipient": "Budi",
        "address": "Jl. Test",
        "delivery_zone": "Barat",
        "sales": "Sales A",
        "order_date": "2026-09-01",
        "deadline": "2026-09-20",
        "current_stage": 2 if design_approved else 1,
        "status_label": server.STAGES[2 if design_approved else 1],
        "design_status": "Approved" if design_approved else "Waiting",
        "design_files": [{"id": "d1", "name": "d.png"}] if design_approved else [],
        "design_approved_by": "admin@eternalpackaging.com" if design_approved else None,
        "payment_status": "Waiting",
        "payment_files": [],
        "line_items": [
            {"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": 100, "unit": "pcs"}
        ],
        "total_quantity": 100,
        "operator_splits": [],
        "delivery_status": "Belum Terkirim",
        "timeline": [],
        "created_at": now,
        "updated_at": now,
    }
    await server.db.orders.insert_one(doc)
    return doc


async def _create_invoice(client, order, unit_price=100000, qty=100):
    res = await client.post(
        "/api/sales-invoices",
        json={
            "po_id": order["id"],
            "invoice_date": "2026-09-01",
            "lines": [{"line_idx": 0, "quantity": qty, "unit_price": unit_price}],
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["invoice"]


def _admin():
    return {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com", "id": "admin-1"}


@pytest.mark.anyio
async def test_create_receipt_with_valid_payment_proof_stores_bytes_and_relations():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    stored = []

    def fake_put(path, data, content_type):
        stored.append({"path": path, "data": data, "content_type": content_type})
        return {"path": path, "size": len(data)}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=100000, qty=100)
            outstanding_before = float(inv.get("outstanding_amount") or inv.get("grand_total"))
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="png"):
                    res = await client.post(
                        "/api/sales-receipts",
                        json={
                            "invoice_id": inv["id"],
                            "amount": 30000,
                            "receipt_date": "2026-09-04",
                            "notes": "DP partial",
                            "payment_proof": {
                                "name": "bukti-dp.png",
                                "data_url": _data_url(TINY_PNG, "image/png"),
                            },
                        },
                    )
            assert res.status_code == 200, res.text
            receipt = res.json()["receipt"]
            assert receipt["amount"] == 30000
            assert receipt["payment_state"] == "DP"
            assert receipt["invoice_id"] == inv["id"]
            assert receipt["po_id"] == order["id"]
            assert receipt["has_payment_proof"] is True
            proof = receipt["payment_proof"]
            assert proof["id"]
            assert proof["receipt_id"] == receipt["id"]
            assert proof["invoice_id"] == inv["id"]
            assert proof["po_id"] == order["id"]
            assert proof["source_type"] == SOURCE_TYPE
            assert "storage_path" not in proof
            assert "data_url" not in proof
            assert "url" not in (proof or {})

            assert len(stored) == 1
            assert stored[0]["data"] == TINY_PNG
            assert order["id"] in stored[0]["path"]
            assert receipt["id"] in stored[0]["path"]

            file_rec = await server.db.files.find_one({"id": proof["id"], "kind": KIND}, {"_id": 0})
            assert file_rec is not None
            assert file_rec["receipt_id"] == receipt["id"]
            assert file_rec["invoice_id"] == inv["id"]
            assert file_rec["po_id"] == order["id"]
            assert file_rec["storage_path"]
            assert file_rec.get("is_deleted") is False

            inv_after = (await client.get(f"/api/sales-invoices/{inv['id']}")).json()["invoice"]
            assert float(inv_after["outstanding_amount"]) == outstanding_before - 30000
            assert inv_after["payment_status"] == "DP"

            # Proof bytes via safe endpoint; no raw URL in JSON
            with mock.patch(
                "services.sales_receipt_payment_proof.obj_storage.get_object",
                return_value=(TINY_PNG, "image/png"),
            ):
                view = await client.get(f"/api/sales-receipts/{receipt['id']}/payment-proof")
            assert view.status_code == 200
            assert view.content == TINY_PNG
            assert "image/png" in (view.headers.get("content-type") or "")
            assert "eternal-packaging/" not in (view.headers.get("content-disposition") or "")
            assert "storage_path" not in view.text

            # GET detail zero-write: files count unchanged
            files_before = await server.db.files.count_documents({"kind": KIND})
            detail = await client.get(f"/api/sales-receipts/{receipt['id']}")
            assert detail.status_code == 200
            assert detail.json()["receipt"]["has_payment_proof"] is True
            files_after = await server.db.files.count_documents({"kind": KIND})
            assert files_after == files_before
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_partial_then_lunas_cumulative_and_multiple_proofs():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin

    def fake_put(path, data, content_type):
        return {"path": path, "size": len(data)}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=100000, qty=100)  # 10_000_000
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="jpg"):
                    r1 = await client.post(
                        "/api/sales-receipts",
                        json={
                            "invoice_id": inv["id"],
                            "amount": 3000000,
                            "payment_proof": {
                                "name": "a.jpg",
                                "data_url": _data_url(TINY_JPEG, "image/jpeg"),
                            },
                        },
                    )
                    r2 = await client.post(
                        "/api/sales-receipts",
                        json={
                            "invoice_id": inv["id"],
                            "amount": 7000000,
                            "payment_proof": {
                                "name": "b.jpg",
                                "data_url": _data_url(TINY_JPEG, "image/jpeg"),
                            },
                        },
                    )
            assert r1.status_code == 200
            assert r2.status_code == 200
            a = r1.json()["receipt"]
            b = r2.json()["receipt"]
            assert a["payment_state"] == "DP"
            assert b["payment_state"] == "LUNAS"
            assert a["payment_proof"]["id"] != b["payment_proof"]["id"]
            assert a["payment_proof"]["receipt_id"] == a["id"]
            assert b["payment_proof"]["receipt_id"] == b["id"]

            inv_after = (await client.get(f"/api/sales-invoices/{inv['id']}")).json()["invoice"]
            assert inv_after["payment_status"] == "LUNAS"
            assert float(inv_after["outstanding_amount"]) == 0

            listed = await client.get("/api/sales-receipts", params={"po_id": order["id"]})
            assert listed.status_code == 200
            receipts = listed.json()["receipts"]
            assert len(receipts) == 2
            by_id = {r["id"]: r for r in receipts}
            assert by_id[a["id"]]["payment_proof"]["id"] == a["payment_proof"]["id"]
            assert by_id[b["id"]]["payment_proof"]["id"] == b["payment_proof"]["id"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_proof_does_not_set_lunas_or_duplicate_payment_files():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin

    def fake_put(path, data, content_type):
        return {"path": path, "size": len(data)}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=100000, qty=100)
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="pdf"):
                    res = await client.post(
                        "/api/sales-receipts",
                        json={
                            "invoice_id": inv["id"],
                            "amount": 1000,
                            "payment_proof": {
                                "name": "proof.pdf",
                                "data_url": _data_url(TINY_PDF, "application/pdf"),
                            },
                        },
                    )
            assert res.status_code == 200
            receipt = res.json()["receipt"]
            assert receipt["payment_state"] == "DP"
            assert receipt["amount"] == 1000

            po = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
            assert po["payment_status"] == "Approved"
            assert po["payment_approved_by"] == "system"
            assert po["payment_approved_by_name"] == "Penerimaan Penjualan"
            # No duplicate payment_files from receipt proof
            assert po.get("payment_files") in ([], None)
            assert len(po.get("payment_receipts") or []) == 1
            assert (po["payment_receipts"][0].get("payment_proof") or {}).get("id") == receipt["payment_proof"]["id"]
            assert payment_ok(po) is True
            assert design_ok(po) is True
            assert penjadwalan_unlocked(po) is True
            assert int(po.get("current_stage") or 0) >= 5
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_receipt_does_not_skip_design_prerequisite():
    order = await _seed_order(design_approved=False)
    app.dependency_overrides[current_user] = _admin

    def fake_put(path, data, content_type):
        return {"path": path, "size": len(data)}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=50000, qty=10)
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="png"):
                    res = await client.post(
                        "/api/sales-receipts",
                        json={
                            "invoice_id": inv["id"],
                            "amount": 10000,
                            "payment_proof": {
                                "name": "p.png",
                                "data_url": _data_url(TINY_PNG, "image/png"),
                            },
                        },
                    )
            assert res.status_code == 200
            po = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
            assert po["payment_status"] == "Approved"
            assert design_ok(po) is False
            assert penjadwalan_unlocked(po) is False
            assert int(po.get("current_stage") or 0) < 5
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_historical_receipt_without_proof_readable():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=10000, qty=10)
            res = await client.post(
                "/api/sales-receipts",
                json={"invoice_id": inv["id"], "amount": 5000},
            )
            assert res.status_code == 200
            receipt = res.json()["receipt"]
            assert receipt.get("has_payment_proof") is False
            assert not receipt.get("payment_proof")

            detail = await client.get(f"/api/sales-receipts/{receipt['id']}")
            assert detail.status_code == 200
            assert detail.json()["receipt"]["has_payment_proof"] is False

            view = await client.get(f"/api/sales-receipts/{receipt['id']}/payment-proof")
            assert view.status_code == 404
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invalid_file_type_rejected_no_receipt_no_fake_file():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=10000, qty=10)
            before_receipts = await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]})
            before_files = await server.db.files.count_documents({"kind": KIND})
            res = await client.post(
                "/api/sales-receipts",
                json={
                    "invoice_id": inv["id"],
                    "amount": 1000,
                    "payment_proof": {
                        "name": "hack.exe",
                        "data_url": _data_url(b"MZ\x90\x00not-an-image", "application/octet-stream"),
                    },
                },
            )
            assert res.status_code == 400
            after_receipts = await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]})
            after_files = await server.db.files.count_documents({"kind": KIND})
            assert after_receipts == before_receipts
            assert after_files == before_files
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_oversized_file_rejected():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=10000, qty=10)
            huge = TINY_PNG + (b"x" * (MAX_PROOF_BYTES + 100))
            # Header claims png but size will fail on decode length check
            res = await client.post(
                "/api/sales-receipts",
                json={
                    "invoice_id": inv["id"],
                    "amount": 1000,
                    "payment_proof": {
                        "name": "big.png",
                        "data_url": _data_url(huge, "image/png"),
                    },
                },
            )
            assert res.status_code == 400
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_unauthorized_file_view_rejected():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin

    def fake_put(path, data, content_type):
        return {"path": path, "size": len(data)}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=10000, qty=10)
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="png"):
                    created = await client.post(
                        "/api/sales-receipts",
                        json={
                            "invoice_id": inv["id"],
                            "amount": 2000,
                            "payment_proof": {
                                "name": "p.png",
                                "data_url": _data_url(TINY_PNG, "image/png"),
                            },
                        },
                    )
            receipt_id = created.json()["receipt"]["id"]

            def no_financial_view():
                # helper has po.view by default but not sales.receipt.view / payment.view
                return {
                    "role": "helper",
                    "name": "Helper",
                    "email": "helper-noview@test.com",
                    "id": "helper-noview-1",
                    "permissions_override": {},
                }

            app.dependency_overrides[current_user] = no_financial_view
            view = await client.get(f"/api/sales-receipts/{receipt_id}/payment-proof")
            assert view.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_po_view_only_cannot_get_payment_proof():
    """po.view alone must not open Bukti Payment; Detail PO still allowed."""
    from permissions import ROLE_DEFAULT_PERMISSIONS

    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin

    def fake_put(path, data, content_type):
        return {"path": path, "size": len(data)}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=10000, qty=10)
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="png"):
                    created = await client.post(
                        "/api/sales-receipts",
                        json={
                            "invoice_id": inv["id"],
                            "amount": 1500,
                            "payment_proof": {
                                "name": "p.png",
                                "data_url": _data_url(TINY_PNG, "image/png"),
                            },
                        },
                    )
            assert created.status_code == 200
            receipt_id = created.json()["receipt"]["id"]

            op_defaults = set(ROLE_DEFAULT_PERMISSIONS["operator"])
            assert "po.view" in op_defaults
            assert "sales.receipt.view" not in op_defaults
            assert "payment.view" not in op_defaults

            def operator_po_view():
                return {
                    "role": "operator",
                    "name": "Operator",
                    "email": "op-poview@test.com",
                    "id": "op-poview-1",
                    "permissions_override": {},
                }

            app.dependency_overrides[current_user] = operator_po_view
            view = await client.get(f"/api/sales-receipts/{receipt_id}/payment-proof")
            assert view.status_code == 403

            po = await client.get(f"/api/orders/{order['id']}")
            assert po.status_code == 200
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_receipt_view_or_payment_view_can_get_proof():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin

    def fake_put(path, data, content_type):
        return {"path": path, "size": len(data)}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=10000, qty=10)
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="png"):
                    with mock.patch(
                        "services.sales_receipt_payment_proof.obj_storage.get_object",
                        return_value=(TINY_PNG, "image/png"),
                    ):
                        created = await client.post(
                            "/api/sales-receipts",
                            json={
                                "invoice_id": inv["id"],
                                "amount": 1800,
                                "payment_proof": {
                                    "name": "p.png",
                                    "data_url": _data_url(TINY_PNG, "image/png"),
                                },
                            },
                        )
                        receipt_id = created.json()["receipt"]["id"]

                        def receipt_view_only():
                            return {
                                "role": "helper",
                                "name": "Receipt Viewer",
                                "email": "receipt-view@test.com",
                                "id": "rv-1",
                                "permissions_override": {
                                    "sales.receipt.view": True,
                                    "sales.receipt.create": False,
                                    "payment.view": False,
                                },
                            }

                        app.dependency_overrides[current_user] = receipt_view_only
                        ok1 = await client.get(f"/api/sales-receipts/{receipt_id}/payment-proof")
                        assert ok1.status_code == 200

                        def payment_view_only():
                            return {
                                "role": "helper",
                                "name": "Payment Viewer",
                                "email": "payment-view@test.com",
                                "id": "pv-1",
                                "permissions_override": {
                                    "payment.view": True,
                                    "sales.receipt.view": False,
                                    "sales.receipt.create": False,
                                },
                            }

                        app.dependency_overrides[current_user] = payment_view_only
                        ok2 = await client.get(f"/api/sales-receipts/{receipt_id}/payment-proof")
                        assert ok2.status_code == 200
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_view_without_create_cannot_upload_proof():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin

    def fake_put(path, data, content_type):
        return {"path": path, "size": len(data)}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=10000, qty=10)
            created = await client.post(
                "/api/sales-receipts",
                json={"invoice_id": inv["id"], "amount": 1200},
            )
            assert created.status_code == 200
            receipt_id = created.json()["receipt"]["id"]

            def view_only():
                return {
                    "role": "helper",
                    "name": "View Only",
                    "email": "view-only@test.com",
                    "id": "vo-1",
                    "permissions_override": {
                        "sales.receipt.view": True,
                        "payment.view": True,
                        "sales.receipt.create": False,
                        "sales.invoice.create": False,
                        "sales_order.create": False,
                        "po.edit": False,
                    },
                }

            app.dependency_overrides[current_user] = view_only
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="png"):
                    attach = await client.post(
                        f"/api/sales-receipts/{receipt_id}/payment-proof",
                        json={
                            "payment_proof": {
                                "name": "p.png",
                                "data_url": _data_url(TINY_PNG, "image/png"),
                            }
                        },
                    )
                    create = await client.post(
                        "/api/sales-receipts",
                        json={
                            "invoice_id": inv["id"],
                            "amount": 500,
                            "payment_proof": {
                                "name": "p2.png",
                                "data_url": _data_url(TINY_PNG, "image/png"),
                            },
                        },
                    )
            assert attach.status_code == 403
            assert create.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_proof_view_gate_source_excludes_po_view():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = src.index('@api_router.get("/sales-receipts/{id}/payment-proof")')
    end = src.index('@api_router.get("/sales-receipts/{id}/pdf")')
    chunk = src[start:end]
    assert '"sales.receipt.view"' in chunk
    assert '"payment.view"' in chunk
    assert '"po.view"' not in chunk
    assert '"sales.invoice.view"' not in chunk


@pytest.mark.anyio
async def test_code_default_roles_proof_access_not_from_po_view_alone():
    from permissions import ROLE_DEFAULT_PERMISSIONS

    view_keys = {"sales.receipt.view", "payment.view"}
    create_keys = {"sales.receipt.create", "sales.invoice.create", "sales_order.create", "po.edit"}
    can_view = sorted(
        r for r, perms in ROLE_DEFAULT_PERMISSIONS.items() if view_keys & set(perms) or r == "admin"
    )
    can_create = sorted(
        r for r, perms in ROLE_DEFAULT_PERMISSIONS.items() if create_keys & set(perms) or r == "admin"
    )
    assert "operator" not in can_view
    assert "helper" not in can_view
    assert "designer" not in can_view
    assert "screener" not in can_view
    assert "driver" not in can_view
    for r in ("admin", "head_production", "head_operations", "crm"):
        assert r in can_view
        assert r in can_create
    assert "po.view" in ROLE_DEFAULT_PERMISSIONS["operator"]


@pytest.mark.anyio
async def test_idempotent_retry_does_not_duplicate_proof():
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    put_calls = []

    def fake_put(path, data, content_type):
        put_calls.append(path)
        return {"path": path, "size": len(data)}

    key = f"idem-{uuid.uuid4()}"
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            inv = await _create_invoice(client, order, unit_price=10000, qty=10)
            payload = {
                "invoice_id": inv["id"],
                "amount": 2500,
                "idempotency_key": key,
                "payment_proof": {
                    "name": "p.png",
                    "data_url": _data_url(TINY_PNG, "image/png"),
                },
            }
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="png"):
                    r1 = await client.post("/api/sales-receipts", json=payload)
                    r2 = await client.post("/api/sales-receipts", json=payload)
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert r1.json()["receipt"]["id"] == r2.json()["receipt"]["id"]
            assert r1.json()["receipt"]["payment_proof"]["id"] == r2.json()["receipt"]["payment_proof"]["id"]
            assert len(put_calls) == 1
            count = await server.db.sales_receipts.count_documents({"invoice_id": inv["id"]})
            assert count == 1
            files = await server.db.files.count_documents(
                {"receipt_id": r1.json()["receipt"]["id"], "kind": KIND, "is_deleted": {"$ne": True}}
            )
            assert files == 1
    finally:
        app.dependency_overrides.pop(current_user, None)
