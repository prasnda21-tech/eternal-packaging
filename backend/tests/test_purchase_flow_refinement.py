"""Purchasing flow refinement: master-item-only, optional PR, early invoice, receipts."""
import os
import sys
import uuid
from pathlib import Path

import pytest
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]



@pytest.fixture(autouse=True)
async def disable_physical_stock_for_purchase_flow_tests(bind_motor):
    """Purchase flow tests isolate cancel/payment/docs; stock V2 has its own suite."""
    from services.physical_stock_settings import SETTINGS_KEY
    from tests.purchase_ygy_fixtures import ensure_active_ygy
    await server.db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": {"key": SETTINGS_KEY, "enabled": False, "stock_in_trigger": "goods_receipt", "stock_out_trigger": "sales_delivery"}},
        upsert=True,
    )
    await ensure_active_ygy(server.db)


@pytest.fixture(autouse=True)
async def indexes(bind_motor):
    db = server.db
    for name in (
        "purchase_requests",
        "purchase_orders",
        "purchase_goods_receipts",
        "purchase_invoices",
        "purchase_payments",
    ):
        try:
            await db[name].drop_index("idempotency_key_1")
        except Exception:
            pass
        try:
            await db[name].create_index("id", unique=True)
        except Exception:
            pass
    # accurate_purchase_receipts uses accurate_receipt_id, not id — do not unique-index id
    try:
        await db.accurate_purchase_receipts.drop_index("id_1")
    except Exception:
        pass
    await db.purchase_requests.create_index("request_number", unique=True)
    await db.purchase_orders.create_index("po_number", unique=True)
    await db.purchase_goods_receipts.create_index("receipt_number", unique=True)
    await db.purchase_invoices.create_index("invoice_number", unique=True)
    await db.purchase_payments.create_index("payment_number", unique=True)


def admin():
    return {"id": "admin_test", "email": "admin@example.com", "name": "Admin", "role": "admin"}


async def client_ctx():
    app.dependency_overrides[current_user] = admin
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def mid(tag="A"):
    return f"mi-{tag}-{uuid.uuid4().hex[:8]}"


async def make_pr(c, lines):
    res = await c.post("/api/purchase-requests", json={"lines": lines})
    assert res.status_code == 200, res.text
    return res.json()["request"]


@pytest.mark.anyio
async def test_pr_requires_master_item_only():
    async with await client_ctx() as c:
        try:
            bad = await c.post("/api/purchase-requests", json={
                "lines": [{"item_name": "Free Text Only", "requested_qty": 5, "unit": "pcs"}],
            })
            assert bad.status_code == 400
            assert "Master Item" in bad.text

            ok = await make_pr(c, [{
                "master_item_id": mid("pr"),
                "item_name": "Mastered Item",
                "item_code": "M-1",
                "requested_qty": 5,
                "unit": "pcs",
            }])
            assert ok["lines"][0]["master_item_id"]
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_po_direct_without_pr():
    async with await client_ctx() as c:
        try:
            item = mid("direct")
            po = (await c.post("/api/purchase-orders", json={
                "supplier": "Direct Supplier",
                "lines": [{
                    "master_item_id": item,
                    "item_name": "Direct Item",
                    "item_code": "D-1",
                    "ordered_qty": 20,
                    "unit_price": 1500,
                    "unit": "pcs",
                }],
            })).json()["order"]
            assert po["po_number"].startswith("PB-")
            assert not po.get("request_id")
            assert po["lines"][0]["master_item_id"] == item
            assert po["receiving_status"] == "BELUM DITERIMA"
            assert po["invoice_status"] == "BELUM DIFAKTUR"
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_po_from_pr_editable_extra_and_qty_remaining():
    async with await client_ctx() as c:
        try:
            a = mid("A")
            b = mid("B")
            extra = mid("X")
            pr = await make_pr(c, [
                {"master_item_id": a, "item_name": "Item A", "item_code": "A", "requested_qty": 100, "unit": "pcs"},
                {"master_item_id": b, "item_name": "Item B", "item_code": "B", "requested_qty": 50, "unit": "pcs"},
            ])
            po = (await c.post("/api/purchase-orders", json={
                "request_id": pr["id"],
                "supplier": "S",
                "lines": [
                    {"request_line_id": pr["lines"][0]["line_id"], "ordered_qty": 40, "unit_price": 10},
                    {
                        "master_item_id": extra,
                        "item_name": "Extra Item",
                        "item_code": "X",
                        "ordered_qty": 7,
                        "unit_price": 5,
                        "unit": "pcs",
                    },
                ],
            })).json()["order"]
            assert len(po["lines"]) == 2
            linked = [l for l in po["lines"] if l.get("request_line_id")]
            plain = [l for l in po["lines"] if not l.get("request_line_id")]
            assert len(linked) == 1 and linked[0]["ordered_qty"] == 40
            assert len(plain) == 1 and plain[0]["master_item_id"] == extra

            left = (await c.get(f"/api/purchase-requests/{pr['id']}")).json()["request"]
            by_id = {l["line_id"]: l for l in left["lines"]}
            assert by_id[pr["lines"][0]["line_id"]]["cumulative_ordered_qty"] == 40
            assert by_id[pr["lines"][0]["line_id"]]["remaining_order_qty"] == 60
            # Extra PO line must NOT reduce Item B remaining
            assert by_id[pr["lines"][1]["line_id"]]["cumulative_ordered_qty"] == 0
            assert by_id[pr["lines"][1]["line_id"]]["remaining_order_qty"] == 50
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_po_from_pr_swapped_item_does_not_fulfill_pr():
    async with await client_ctx() as c:
        try:
            a = mid("swap-a")
            swapped = mid("swap-b")
            pr = await make_pr(c, [{
                "master_item_id": a, "item_name": "Original", "item_code": "O", "requested_qty": 10, "unit": "pcs",
            }])
            po = (await c.post("/api/purchase-orders", json={
                "request_id": pr["id"],
                "supplier": "S",
                "lines": [{
                    "request_line_id": pr["lines"][0]["line_id"],
                    "master_item_id": swapped,
                    "item_name": "Swapped",
                    "item_code": "S1",
                    "ordered_qty": 10,
                    "unit_price": 1,
                    "unit": "pcs",
                }],
            })).json()["order"]
            assert not po["lines"][0].get("request_line_id")
            left = (await c.get(f"/api/purchase-requests/{pr['id']}")).json()["request"]
            assert left["lines"][0]["remaining_order_qty"] == 10
            assert left["lines"][0]["cumulative_ordered_qty"] == 0
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_po_receipt_partial_full_over_and_early_invoice():
    async with await client_ctx() as c:
        try:
            item = mid("recv")
            po = (await c.post("/api/purchase-orders", json={
                "supplier": "S",
                "lines": [{
                    "master_item_id": item,
                    "item_name": "Recv Item",
                    "item_code": "R1",
                    "ordered_qty": 100,
                    "unit_price": 2000,
                    "unit": "pcs",
                }],
            })).json()["order"]
            lid = po["lines"][0]["line_id"]

            # Early invoice without ack → reject
            early_bad = await c.post("/api/purchase-invoices", json={
                "invoice_source": "purchase_order",
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 100}],
            })
            assert early_bad.status_code == 400
            assert "mendahului" in early_bad.text.lower() or "acknowledgement" in early_bad.text.lower()

            # Early invoice with ack → pass; received_qty unchanged
            early_ok = (await c.post("/api/purchase-invoices", json={
                "invoice_source": "purchase_order",
                "purchase_order_id": po["id"],
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 100}],
            })).json()["invoice"]
            assert early_ok["precedes_goods_receipt"] is True
            assert early_ok["invoice_source"] == "purchase_order"

            po_after_inv = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po_after_inv["lines"][0]["cumulative_received_qty"] == 0
            assert po_after_inv["lines"][0]["cumulative_invoiced_qty"] == 100
            assert po_after_inv["has_early_invoice"] is True
            assert po_after_inv["invoice_status"] == "DIFAKTUR"
            assert po_after_inv["receiving_status"] == "BELUM DITERIMA"

            # Cumulative invoice > ordered → reject
            over_inv = await c.post("/api/purchase-invoices", json={
                "invoice_source": "purchase_order",
                "purchase_order_id": po["id"],
                "precedes_goods_receipt": True,
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 1}],
            })
            assert over_inv.status_code == 400

            # Partial receipt still works after early invoice
            gr1 = (await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 30}],
            })).json()["receipt"]
            assert gr1["receipt_number"]

            po_mid = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po_mid["lines"][0]["cumulative_received_qty"] == 30
            assert po_mid["receiving_status"] == "SEBAGIAN DITERIMA"

            # Over receipt → reject
            over = await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 71}],
            })
            assert over.status_code == 400

            # Full remaining
            await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 70}],
            })
            po_full = (await c.get(f"/api/purchase-orders/{po['id']}")).json()["order"]
            assert po_full["lines"][0]["cumulative_received_qty"] == 100
            assert po_full["receiving_status"] == "DITERIMA"
            # Early invoice did not auto-create a second invoice
            invs = (await c.get("/api/purchase-invoices", params={"purchase_order_id": po["id"]})).json()["invoices"]
            assert len(invs) == 1
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_invoice_from_receipt_and_payments():
    async with await client_ctx() as c:
        try:
            item = mid("inv-rec")
            po = (await c.post("/api/purchase-orders", json={
                "supplier": "Pay Sup",
                "lines": [{
                    "master_item_id": item,
                    "item_name": "Pay Item",
                    "item_code": "P1",
                    "ordered_qty": 10,
                    "unit_price": 1000,
                    "unit": "pcs",
                }],
            })).json()["order"]
            lid = po["lines"][0]["line_id"]
            gr = (await c.post("/api/purchase-goods-receipts", json={
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "received_qty": 10}],
            })).json()["receipt"]

            preview = await c.get(f"/api/purchase-invoices/from-receipt/{gr['id']}/preview")
            assert preview.status_code == 200, preview.text
            assert preview.json()["receipt_id"] == gr["id"]

            inv = (await c.post("/api/purchase-invoices", json={
                "invoice_source": "receipt",
                "receipt_id": gr["id"],
                "purchase_order_id": po["id"],
                "lines": [{"purchase_order_line_id": lid, "invoice_qty": 10}],
            })).json()["invoice"]
            assert inv["invoice_source"] == "receipt"
            assert inv["precedes_goods_receipt"] is False
            assert inv["grand_total"] == 10000
            assert inv["outstanding_amount"] == 10000

            # Draft payment does NOT settle invoice until confirm-paid
            pay1 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"],
                "amount": 4000,
                "idempotency_key": f"pay-dp-{uuid.uuid4().hex}",
            })).json()["payment"]
            assert pay1["payment_number"]
            inv_draft = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert inv_draft["payment_status"] == "BELUM_DIBAYAR"
            assert inv_draft["outstanding_amount"] == 10000

            # Submit + confirm partial => DP (chat requires both users active in DB)
            actor = admin()
            peer = {
                "id": f"peer-pay-{uuid.uuid4().hex[:8]}",
                "email": f"peer-pay-{uuid.uuid4().hex[:8]}@example.com",
                "name": "Peer Pay",
                "role": "holding_team",
                "status": "active",
            }
            await server.db.users.update_one(
                {"id": actor["id"]},
                {"$set": {**actor, "status": "active", "email": actor.get("email") or f"admin-{uuid.uuid4().hex[:6]}@example.com"}},
                upsert=True,
            )
            await server.db.users.update_one({"id": peer["id"]}, {"$set": peer}, upsert=True)
            sub = await c.post(f"/api/purchase-payments/{pay1['id']}/submit", json={"target_user_id": peer["id"]})
            assert sub.status_code == 200, sub.text
            conf = await c.post(f"/api/purchase-payments/{pay1['id']}/confirm-paid")
            assert conf.status_code == 200, conf.text
            inv_dp = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert inv_dp["payment_status"] == "DIBAYAR_SEBAGIAN"
            assert inv_dp["outstanding_amount"] == 6000

            # Overpay reject (even as draft against remaining outstanding)
            over = await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"],
                "amount": 6001,
            })
            assert over.status_code == 400

            # Full remaining = LUNAS after submit+confirm
            pay2 = (await c.post("/api/purchase-payments", json={
                "purchase_invoice_id": inv["id"],
                "amount": 6000,
                "idempotency_key": f"pay-full-{uuid.uuid4().hex}",
            })).json()["payment"]
            assert (await c.post(f"/api/purchase-payments/{pay2['id']}/submit", json={"target_user_id": peer["id"]})).status_code == 200
            assert (await c.post(f"/api/purchase-payments/{pay2['id']}/confirm-paid")).status_code == 200
            inv_full = (await c.get(f"/api/purchase-invoices/{inv['id']}")).json()["invoice"]
            assert inv_full["payment_status"] == "LUNAS"
            assert inv_full["outstanding_amount"] == 0
        finally:
            app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_direct_receipt_document_only_no_stock_movement():
    """Direct receipt without PO remains document-only (official stock path BLOCKED)."""
    async with await client_ctx() as c:
        try:
            item = mid("doc")
            before = await server.db.stock_movements.count_documents({})
            rec = (await c.post("/api/purchase-receipts", json={
                "supplier": "Doc Sup",
                "receipt_date": "2026-09-10",
                "lines": [{
                    "master_item_id": item,
                    "item_name": "Doc Item",
                    "item_code": "DOC",
                    "received_qty": 5,
                    "unit": "pcs",
                }],
            })).json()["receipt"]
            assert rec["source"] == "manual"
            assert not rec.get("purchase_order_id")
            after = await server.db.stock_movements.count_documents({})
            assert after == before

            free = await c.post("/api/purchase-receipts", json={
                "supplier": "X",
                "receipt_date": "2026-09-10",
                "lines": [{"item_name": "Free", "received_qty": 1}],
            })
            assert free.status_code == 400
        finally:
            app.dependency_overrides.pop(current_user, None)
