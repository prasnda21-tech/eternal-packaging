"""Public live-tracking payment proofs. Isolated synthetic data only."""
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
os.environ["DB_NAME"] = "eternal_packaging_tracking_payproof_test"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

PAY_DB = "eternal_packaging_tracking_payproof_test"

import server
from server import app, current_user
from services.customer_tracking import new_tracking_token
from services.sales_receipt_payment_proof import KIND
from services.sales_receipt_posting import _finalize_reversal_operational_state

PREFIX = "TRACKPAY"
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
FORBIDDEN = (
    "notes", "storage_path", "accounting", "journal", "cash_bank", "uploaded_by",
    "data_url", "url", "customer_id", "po_id", "ledger",
)


def _data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _admin():
    return {"role": "admin", "name": "Admin", "email": "admin@eternalpackaging.com", "id": "admin-1"}


def _po_view_only():
    return {
        "role": "operator",
        "name": "Operator",
        "email": "op-poview@test.com",
        "id": "op-poview-1",
        "permissions_override": {},
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bind_motor_and_clean():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[PAY_DB]
    assert server.db.name == PAY_DB
    assert server.db.name != "eternal_packaging"
    await _wipe()
    yield
    await _wipe()
    app.dependency_overrides.pop(current_user, None)


async def _wipe():
    ids = [
        row["id"]
        async for row in server.db.orders.find({"po_number": {"$regex": f"^{PREFIX}-"}}, {"id": 1})
    ]
    if not ids:
        return
    await server.db.files.delete_many({"po_id": {"$in": ids}, "kind": KIND})
    await server.db.sales_receipts.delete_many({"po_id": {"$in": ids}})
    await server.db.sales_invoices.delete_many({"po_id": {"$in": ids}})
    await server.db.customer_credit_applications.delete_many({"po_id": {"$in": ids}})
    await server.db.orders.delete_many({"id": {"$in": ids}})


async def _seed_order():
    now = datetime.now(timezone.utc).isoformat()
    order_id = str(uuid.uuid4())
    doc = {
        "id": order_id,
        "po_number": f"{PREFIX}-{uuid.uuid4().hex[:8].upper()}",
        "customer": "PT Track Pay",
        "notes": "catatan internal pesanan",
        "outlet": "Outlet",
        "address": "Jl. Uji",
        "sales": "Sales A",
        "order_date": "2026-09-01",
        "deadline": "2026-09-20",
        "current_stage": 2,
        "status_label": server.STAGES[2],
        "design_status": "Approved",
        "design_files": [{"id": "d1", "name": "d.png"}],
        "design_approved_by": "admin@eternalpackaging.com",
        "payment_status": "Waiting",
        "payment_files": [{"id": "legacy-pay", "name": "lama.png"}],
        "line_items": [{"line_idx": 0, "line_id": "l0", "name": "Cup", "quantity": 10, "unit": "pcs"}],
        "total_quantity": 10,
        "operator_splits": [],
        "delivery_status": "Belum Terkirim",
        "timeline": [],
        "tracking_token": new_tracking_token(),
        "tracking_token_created_at": now,
        "created_at": now,
        "updated_at": now,
    }
    await server.db.orders.insert_one(doc)
    return doc


async def _invoice(client, order, unit_price=10000, qty=10):
    created = await client.post("/api/sales-invoices", json={
        "po_id": order["id"],
        "invoice_date": "2026-09-01",
        "notes": "catatan internal faktur",
        "lines": [{"line_idx": 0, "quantity": qty, "unit_price": unit_price}],
    })
    assert created.status_code == 200, created.text
    invoice_id = created.json()["invoice"]["id"]
    finalized = await client.post(f"/api/sales-invoices/{invoice_id}/finalize")
    assert finalized.status_code == 200, finalized.text
    return finalized.json()["invoice"]


def _store():
    def fake_put(path, data, content_type):
        return {"path": path, "size": len(data)}

    return fake_put


async def _receipt(client, invoice_id, amount, proof, name="bukti.png", mime="image/png"):
    ext = "jpg" if mime == "image/jpeg" else "png"
    with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=_store()):
        with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value=ext):
            res = await client.post("/api/sales-receipts", json={
                "invoice_id": invoice_id,
                "amount": amount,
                "receipt_date": "2026-09-04",
                "notes": "catatan internal penerimaan",
                "payment_proof": {"name": name, "data_url": _data_url(proof, mime)},
            })
    assert res.status_code == 200, res.text
    return res.json()["receipt"]


def _assert_public_row(row):
    assert set(row) <= {
        "receipt_id", "receipt_number", "invoice_number", "receipt_date",
        "amount", "status_label", "active", "file_name", "content_type",
    }
    blob = str(row).lower()
    for word in FORBIDDEN:
        assert word not in blob


@pytest.mark.anyio
async def test_saved_proof_is_public_immediately_and_reads_do_not_write(bind_motor_and_clean):
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    stored = {}

    def fake_get(path):
        assert path in stored
        return stored[path], "image/png"

    def fake_put(path, data, content_type):
        stored[path] = data
        return {"path": path, "size": len(data)}

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            invoice = await _invoice(client, order)
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.put_object", side_effect=fake_put):
                with mock.patch("services.sales_receipt_payment_proof.obj_storage.ext_for", return_value="png"):
                    created = await client.post("/api/sales-receipts", json={
                        "invoice_id": invoice["id"],
                        "amount": 3000,
                        "notes": "catatan internal penerimaan",
                        "payment_proof": {"name": "bukti.png", "data_url": _data_url(TINY_PNG, "image/png")},
                    })
            assert created.status_code == 200, created.text
            receipt = created.json()["receipt"]
            before = {
                "orders": await server.db.orders.count_documents({"id": order["id"]}),
                "receipts": await server.db.sales_receipts.count_documents({"po_id": order["id"]}),
                "invoices": await server.db.sales_invoices.count_documents({"po_id": order["id"]}),
                "journals": await server.db.journal_entries.count_documents({"source_id": {"$in": [receipt["id"], invoice["id"]]}}),
                "files": await server.db.files.count_documents({"kind": KIND, "po_id": order["id"]}),
                "payment_status": (await server.db.orders.find_one({"id": order["id"]})).get("payment_status"),
                "outstanding": (await server.db.sales_invoices.find_one({"id": invoice["id"]})).get("outstanding_amount"),
                "stage": (await server.db.orders.find_one({"id": order["id"]})).get("current_stage"),
            }
            with mock.patch("services.sales_receipt_payment_proof.obj_storage.get_object", side_effect=fake_get):
                listed = await client.get(f"/api/public/tracking/{order['tracking_token']}/payment-proofs")
                opened = await client.get(
                    f"/api/public/tracking/{order['tracking_token']}/payment-proofs/{receipt['id']}"
                )
            assert listed.status_code == 200
            assert listed.headers.get("location") is None
            rows = listed.json()["proofs"]
            assert len(rows) == 1
            _assert_public_row(rows[0])
            assert rows[0]["receipt_id"] == receipt["id"]
            assert rows[0]["receipt_number"] == receipt["receipt_number"]
            assert rows[0]["amount"] == 3000
            assert rows[0]["status_label"] == "Sebagian"
            assert rows[0]["active"] is True
            assert "catatan internal" not in listed.text
            assert opened.status_code == 200
            assert opened.content == TINY_PNG
            assert opened.headers.get("location") is None
            assert "storage" not in (opened.headers.get("content-disposition") or "")
            po = await server.db.orders.find_one({"id": order["id"]})
            assert po.get("payment_files") == [{"id": "legacy-pay", "name": "lama.png"}]
            assert po.get("payment_status") == before["payment_status"] == "Approved"
            assert (await server.db.sales_invoices.find_one({"id": invoice["id"]})).get("outstanding_amount") == before["outstanding"]
            assert po.get("current_stage") == before["stage"]
            assert await server.db.orders.count_documents({"id": order["id"]}) == before["orders"]
            assert await server.db.sales_receipts.count_documents({"po_id": order["id"]}) == before["receipts"]
            assert await server.db.sales_invoices.count_documents({"po_id": order["id"]}) == before["invoices"]
            assert await server.db.journal_entries.count_documents({"source_id": {"$in": [receipt["id"], invoice["id"]]}}) == before["journals"]
            assert await server.db.files.count_documents({"kind": KIND, "po_id": order["id"]}) == before["files"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_two_partial_receipts_stay_separate_and_reversal_is_not_active(bind_motor_and_clean):
    order = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            invoice = await _invoice(client, order, unit_price=10000, qty=10)
            first = await _receipt(client, invoice["id"], 3000, TINY_PNG, "a.png")
            second = await _receipt(client, invoice["id"], 2000, TINY_JPEG, "b.jpg", "image/jpeg")
            listed = await client.get(f"/api/public/tracking/{order['tracking_token']}/payment-proofs")
            rows = listed.json()["proofs"]
            assert [row["receipt_id"] for row in rows] == [first["id"], second["id"]]
            assert rows[0]["file_name"] == "a.png"
            assert rows[1]["file_name"] == "b.jpg"
            assert rows[0]["amount"] == 3000
            assert rows[1]["amount"] == 2000
            assert all(row["active"] is True for row in rows)
            await _finalize_reversal_operational_state(server.db, first, _admin())
            after = await client.get(f"/api/public/tracking/{order['tracking_token']}/payment-proofs")
            by_id = {row["receipt_id"]: row for row in after.json()["proofs"]}
            assert by_id[first["id"]]["status_label"] == "Dibatalkan"
            assert by_id[first["id"]]["active"] is False
            assert by_id[second["id"]]["active"] is True
            invoices = await client.get(f"/api/public/tracking/{order['tracking_token']}/invoices")
            published = invoices.json()["invoices"][0]
            assert published["paid_total"] == 2000
            assert published["payment_label"] == "Sebagian"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_foreign_token_deleted_and_unproven_proofs_are_rejected(bind_motor_and_clean):
    order_a = await _seed_order()
    order_b = await _seed_order()
    app.dependency_overrides[current_user] = _admin
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            invoice_a = await _invoice(client, order_a)
            invoice_b = await _invoice(client, order_b)
            proof = await _receipt(client, invoice_b["id"], 1000, TINY_PNG)
            bare = await client.post("/api/sales-receipts", json={
                "invoice_id": invoice_a["id"],
                "amount": 500,
            })
            assert bare.status_code == 200, bare.text
            credit_id = str(uuid.uuid4())
            await server.db.customer_credit_applications.insert_one({
                "id": credit_id,
                "po_id": order_a["id"],
                "invoice_id": invoice_a["id"],
                "amount": 100,
                "notes": "catatan internal saldo",
            })
            own = await client.get(f"/api/public/tracking/{order_a['tracking_token']}/payment-proofs")
            assert own.json()["proofs"] == []
            assert "catatan internal saldo" not in own.text
            foreign_list = await client.get(f"/api/public/tracking/{order_a['tracking_token']}/payment-proofs")
            assert all(row["receipt_id"] != proof["id"] for row in foreign_list.json()["proofs"])
            foreign_file = await client.get(
                f"/api/public/tracking/{order_a['tracking_token']}/payment-proofs/{proof['id']}"
            )
            assert foreign_file.status_code == 404
            missing = await client.get(
                f"/api/public/tracking/{order_b['tracking_token']}/payment-proofs/{bare.json()['receipt']['id']}"
            )
            assert missing.status_code == 404
            for token in ("short", "tidak-ada-token-xxxx", order_b["tracking_token"] + "x"):
                assert (await client.get(f"/api/public/tracking/{token}/payment-proofs")).status_code == 404
                assert (await client.get(f"/api/public/tracking/{token}/payment-proofs/{proof['id']}")).status_code == 404
            await server.db.files.update_one(
                {"receipt_id": proof["id"], "kind": KIND},
                {"$set": {"is_deleted": True}},
            )
            gone_list = await client.get(f"/api/public/tracking/{order_b['tracking_token']}/payment-proofs")
            assert gone_list.json()["proofs"] == []
            gone_file = await client.get(
                f"/api/public/tracking/{order_b['tracking_token']}/payment-proofs/{proof['id']}"
            )
            assert gone_file.status_code == 404

            app.dependency_overrides[current_user] = _po_view_only
            internal = await client.get(f"/api/sales-receipts/{proof['id']}/payment-proof")
            assert internal.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


def test_public_payment_proof_routes_do_not_write():
    text = Path(__file__).resolve().parents[1].joinpath("services/customer_tracking_payment_proofs.py").read_text()
    for word in ("update_one", "insert_one", "delete_one", "delete_many", "replace_one", "ensure_tracking_token"):
        assert word not in text
    routes = Path(__file__).resolve().parents[1].joinpath("services/customer_tracking.py").read_text()
    block = routes.split("async def public_tracking_payment_proofs")[1].split("async def tracking_link")[0]
    for word in ("update_one", "insert_one", "delete_one", "ensure_tracking_token", "RedirectResponse"):
        assert word not in block
