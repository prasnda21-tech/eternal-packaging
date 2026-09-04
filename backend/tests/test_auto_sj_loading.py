import uuid
from datetime import datetime, timezone
import httpx
import pytest
from server import app, db, current_user
from services.sales_deliveries import derive_base_sj_number, derive_sj_number
from quantity_rules import (
    available_for_sj_loading_line,
    loaded_qty_for_sj_line,
    sj_qty_for_line,
    validate_loading_confirmation,
)


import os
import server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
    server.db = server.client[os.environ.get("DB_NAME", "eternal_packaging")]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


async def create_helper_po(po_number_str: str, line_items: list, **kwargs):
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": po_number_str,
        "customer": kwargs.get("customer", "PT Auto SJ Testing"),
        "customer_name": kwargs.get("customer", "PT Auto SJ Testing"),
        "outlet": "Pusat",
        "recipient": "Budi",
        "address": "Jl. Auto SJ No. 1",
        "delivery_zone": "Zona 1",
        "sales": "Sales Admin",
        "expedition": "Armada Sendiri",
        "order_date": "2026-08-30",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "current_stage": kwargs.get("current_stage", 11),
        "cancelled": False,
        "line_items": line_items,
        "total_quantity": sum(int(li.get("quantity", 0)) for li in line_items),
        "packing_info": {
            "qty_packed": sum(int(li.get("quantity", 0)) for li in line_items),
            "lines": [
                {"line_idx": i, "packed_qty": int(li.get("quantity", 0))}
                for i, li in enumerate(line_items)
            ],
        },
        "shipments": kwargs.get("shipments", []),
        "surat_jalan_files": kwargs.get("surat_jalan_files", []),
        "loading_confirmations": kwargs.get("loading_confirmations", []),
    }
    for k, v in kwargs.items():
        doc[k] = v
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


@pytest.mark.anyio
async def test_case_1_2_auto_sj_created_with_exact_number_and_qty():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 16oz", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-08-30",
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert res.status_code == 200, res.text
            deliv = res.json()["delivery"]
            expected_sj = derive_base_sj_number(po_num)
            assert deliv["sj_number"] == expected_sj
            assert deliv["total_qty"] == 200

            updated_order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert len(updated_order["shipments"]) == 1
            sh = updated_order["shipments"][0]
            assert sh["id"] == deliv["id"]
            assert sh["number"] == expected_sj
            assert sh["total_qty"] == 200
            assert sh["lines"][0]["qty"] == 200

            files = updated_order.get("surat_jalan_files") or []
            assert len(files) == 1
            gen = files[0]
            assert gen["id"] == deliv["id"]
            assert gen["shipment_id"] == deliv["id"]
            assert gen["source"] == "SYSTEM_GENERATED"
            assert gen["content_type"] == "application/pdf"
            assert str(gen.get("storage_path") or "").strip()
            assert str(gen.get("name") or "").lower().endswith(".pdf")
            assert not str(gen.get("data_url") or "").startswith("data:")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_3_idempotent_no_duplicate_sj():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 12oz", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-08-30",
                "line_items": [{"line_idx": 0, "qty": 500}],
            })
            assert res1.status_code == 200

            res2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-08-30",
                "line_items": [{"line_idx": 0, "qty": 100}],
            })
            assert res2.status_code == 400

            updated_order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert len(updated_order["shipments"]) == 1
            files = updated_order.get("surat_jalan_files") or []
            assert len(files) == 1
            assert files[0]["id"] == updated_order["shipments"][0]["id"]
            assert files[0]["source"] == "SYSTEM_GENERATED"
            assert str(files[0].get("storage_path") or "").strip()
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_4_multiple_shipments_sj_numbering_and_independence():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Bowl 650ml", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            base_sj = derive_base_sj_number(po_num)

            r1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert r1.status_code == 200
            d1 = r1.json()["delivery"]
            assert d1["sj_number"] == base_sj
            assert d1["total_qty"] == 200

            r2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 150}],
            })
            assert r2.status_code == 200
            d2 = r2.json()["delivery"]
            assert d2["sj_number"] == f"{base_sj}_2"
            assert d2["total_qty"] == 150

            updated_order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert len(updated_order["shipments"]) == 2
            assert updated_order["shipments"][0]["number"] == base_sj
            assert updated_order["shipments"][0]["total_qty"] == 200
            assert updated_order["shipments"][1]["number"] == f"{base_sj}_2"
            assert updated_order["shipments"][1]["total_qty"] == 150
            files = updated_order.get("surat_jalan_files") or []
            assert len(files) == 2
            by_ship = {f.get("shipment_id"): f for f in files}
            assert by_ship[d1["id"]]["sj_number"] == base_sj
            assert by_ship[d2["id"]]["sj_number"] == f"{base_sj}_2"
            assert by_ship[d1["id"]]["storage_path"] != by_ship[d2["id"]]["storage_path"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_5_exact_line_identity_preserved():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [
        {"name": "Cup 12oz", "quantity": 300, "unit": "pcs", "line_id": "line_a"},
        {"name": "Cup 12oz", "quantity": 200, "unit": "pcs", "line_id": "line_b"},
    ])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [
                    {"line_idx": 0, "qty": 100},
                    {"line_idx": 1, "qty": 50},
                ],
            })
            assert res.status_code == 200
            deliv = res.json()["delivery"]
            assert len(deliv["line_items"]) == 2
            assert deliv["line_items"][0]["line_idx"] == 0
            assert deliv["line_items"][0]["qty"] == 100
            assert deliv["line_items"][1]["line_idx"] == 1
            assert deliv["line_items"][1]["qty"] == 50

            updated_order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            sh = updated_order["shipments"][0]
            assert len(sh["lines"]) == 2
            assert sh["lines"][0]["line_idx"] == 0
            assert sh["lines"][0]["qty"] == 100
            assert sh["lines"][1]["line_idx"] == 1
            assert sh["lines"][1]["qty"] == 50
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_6_overshipment_rejected():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Bowl 800ml", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 501}],
            })
            assert res.status_code == 400
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_7_to_11_loading_availability_decrements_and_rejects_overload():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 22oz", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res_deliv = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert res_deliv.status_code == 200
            deliv_id = res_deliv.json()["delivery"]["id"]

            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})

            # CASE 7: No loading yet -> available = 200
            assert sj_qty_for_line(order, deliv_id, 0) == 200
            assert loaded_qty_for_sj_line(order, deliv_id, 0) == 0
            assert available_for_sj_loading_line(order, deliv_id, 0) == 200

            # CASE 8: Confirm 80 pcs
            res_load1 = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": deliv_id,
                "lines": [{"line_idx": 0, "qty": 80}],
            })
            assert res_load1.status_code == 200
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert loaded_qty_for_sj_line(order, deliv_id, 0) == 80
            assert available_for_sj_loading_line(order, deliv_id, 0) == 120

            # CASE 9: Attempt loading 121 -> reject (400)
            res_load_err = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": deliv_id,
                "lines": [{"line_idx": 0, "qty": 121}],
            })
            assert res_load_err.status_code == 400
            assert "melebihi" in res_load_err.text

            # CASE 10: Confirm remaining 120 pcs
            res_load2 = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": deliv_id,
                "lines": [{"line_idx": 0, "qty": 120}],
            })
            assert res_load2.status_code == 200
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert loaded_qty_for_sj_line(order, deliv_id, 0) == 200
            assert available_for_sj_loading_line(order, deliv_id, 0) == 0

            # CASE 11: Additional loading after available is 0 -> reject (400)
            res_load3 = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": deliv_id,
                "lines": [{"line_idx": 0, "qty": 1}],
            })
            assert res_load3.status_code == 400
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_12_multi_line_sj_loading_availability():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [
        {"name": "Item A", "quantity": 300, "unit": "pcs"},
        {"name": "Item B", "quantity": 200, "unit": "pcs"},
    ])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res_deliv = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [
                    {"line_idx": 0, "qty": 120},
                    {"line_idx": 1, "qty": 80},
                ],
            })
            assert res_deliv.status_code == 200
            deliv_id = res_deliv.json()["delivery"]["id"]

            res_load = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": deliv_id,
                "lines": [
                    {"line_idx": 0, "qty": 100},
                    {"line_idx": 1, "qty": 30},
                ],
            })
            assert res_load.status_code == 200

            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert available_for_sj_loading_line(order, deliv_id, 0) == 20
            assert available_for_sj_loading_line(order, deliv_id, 1) == 50
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_13_14_no_driver_or_proof_side_effect():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 8oz", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 500}],
            })
            assert res.status_code == 200

            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order.get("delivery_status") != "Terkirim"
            assert not order.get("delivery_proof_photos")
            assert not order.get("delivery_confirmations")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_15_16_packing_prerequisite_enforced():
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(
        po_num,
        [{"name": "Cup 16oz", "quantity": 500, "unit": "pcs"}],
        current_stage=10,
        packing_info={"qty_packed": 100, "lines": [{"line_idx": 0, "packed_qty": 100}]},
    )

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res_err = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert res_err.status_code == 400
            assert "packing" in res_err.text

            res_ok = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 100}],
            })
            assert res_ok.status_code == 200
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_case_17_generated_file_is_viewable():
    """Generated Pengiriman PDF is a real file served through the existing view endpoint."""
    po_num = f"PO-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup 22oz", "quantity": 500, "unit": "pcs"}])

    app.dependency_overrides[current_user] = lambda: {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res_deliv = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert res_deliv.status_code == 200
            deliv_id = res_deliv.json()["delivery"]["id"]

            res_file = await client.get(f"/api/orders/{po['id']}/surat-jalan/files/{deliv_id}")
            assert res_file.status_code == 200
            assert res_file.content.startswith(b"%PDF")
            assert "pdf" in (res_file.headers.get("content-type") or "")
    finally:
        app.dependency_overrides.pop(current_user, None)

