"""Focused backend tests for sales-order Repeat Order source lookup."""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from server import app, current_user
from services.sales_order_repeat import (
    build_repeat_source_dto,
    find_latest_valid_repeat_source,
    find_repeat_source_by_order_id,
)


TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


def _admin():
    return {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }


def _viewer_only():
    return {
        "email": "driver@eternalpackaging.com",
        "name": "Driver User",
        "role": "driver",
    }


def _base_order(**kw):
    cid = kw.get("customer_id", f"cust-{uuid.uuid4().hex[:8]}")
    now = kw.get("created_at") or datetime.now(timezone.utc).isoformat()
    return {
        "id": kw.get("id", str(uuid.uuid4())),
        "po_number": kw.get("po_number", f"PO-REP-{uuid.uuid4().hex[:6].upper()}"),
        "customer": kw.get("customer", "PT Repeat Customer"),
        "customer_id": cid,
        "customer_phone": kw.get("customer_phone", "08123456789"),
        "outlet": kw.get("outlet", "Pusat"),
        "recipient": kw.get("recipient", "Pak Budi"),
        "address": kw.get("address", "Jl. Industri 1"),
        "delivery_zone": kw.get("delivery_zone", "Jakarta"),
        "payment_terms": kw.get("payment_terms", "Net 30"),
        "sales": kw.get("sales", "Vira"),
        "expedition": kw.get("expedition", "Internal fleet"),
        "notes": kw.get("notes", "Catatan repeat"),
        "order_date": kw.get("order_date", now),
        "estimated_delivery": kw.get("estimated_delivery", now),
        "deadline": kw.get("deadline", now),
        "created_at": now,
        "updated_at": now,
        "cancelled": kw.get("cancelled", False),
        "current_stage": kw.get("current_stage", 12),
        "status_label": kw.get("status_label", "Packing"),
        "packing_status": kw.get("packing_status", "Selesai"),
        "delivery_status": kw.get("delivery_status", "Terkirim"),
        "payment_status": kw.get("payment_status", "Approved"),
        "design_status": kw.get("design_status", "Approved"),
        "design_approval_history": kw.get("design_approval_history", [{"action": "approve"}]),
        "payment_files": kw.get("payment_files", [{"id": "pay1", "name": "bukti.pdf", "data_url": TINY_PNG}]),
        "sample_files": kw.get("sample_files", [{"id": "sam1", "name": "sample.png", "data_url": TINY_PNG}]),
        "surat_jalan_files": kw.get("surat_jalan_files", [{"id": "sj1", "name": "sj.pdf"}]),
        "delivery_proofs": kw.get("delivery_proofs", [{"id": "dp1", "name": "proof.jpg"}]),
        "loading_checklist": kw.get("loading_checklist", {"done": True}),
        "timeline": kw.get("timeline", [{"stage": 12, "stage_label": "Packing"}]),
        "line_items": kw.get(
            "line_items",
            [
                {
                    "item_code": "PC-12",
                    "name": "Paper Cup 12oz",
                    "quantity": 1000,
                    "unit": "pcs",
                    "color": "Navy",
                    "production_process": "print",
                    "notes": "Motif A",
                    "accurate_item_id": "acc-1",
                    "master_item_id": "mst-1",
                },
                {
                    "item_code": "PC-12",
                    "name": "Paper Cup 12oz",
                    "quantity": 500,
                    "unit": "pcs",
                    "color": "Merah",
                    "production_process": "print",
                    "notes": "Motif B",
                    "accurate_item_id": "acc-1",
                    "master_item_id": "mst-1",
                },
            ],
        ),
        "design_files": kw.get(
            "design_files",
            [
                {
                    "id": "des-src-1",
                    "name": "design-a.png",
                    "data_url": TINY_PNG,
                    "content_type": "image/png",
                }
            ],
        ),
        **{k: v for k, v in kw.items() if k not in {
            "customer_id", "created_at", "po_number", "customer", "cancelled",
            "line_items", "design_files", "id", "order_date",
        }},
    }


@pytest.mark.anyio
async def test_latest_valid_po_selected_for_exact_customer():
    import server

    cid = f"cust-latest-{uuid.uuid4().hex[:8]}"
    older = _base_order(
        customer_id=cid,
        po_number=f"PO-OLD-{uuid.uuid4().hex[:5].upper()}",
        created_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    )
    newer = _base_order(
        customer_id=cid,
        po_number=f"PO-NEW-{uuid.uuid4().hex[:5].upper()}",
        created_at=datetime.now(timezone.utc).isoformat(),
        notes="newest valid",
    )
    other = _base_order(
        customer_id=f"cust-other-{uuid.uuid4().hex[:8]}",
        po_number=f"PO-OTH-{uuid.uuid4().hex[:5].upper()}",
        created_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    )
    await server.db.orders.insert_many([older, newer, other])

    found = await find_latest_valid_repeat_source(server.db, cid)
    assert found is not None
    assert found["po_number"] == newer["po_number"]
    assert found["notes"] == "newest valid"


@pytest.mark.anyio
async def test_latest_cancelled_skipped():
    import server

    cid = f"cust-cancel-{uuid.uuid4().hex[:8]}"
    valid = _base_order(
        customer_id=cid,
        po_number=f"PO-VAL-{uuid.uuid4().hex[:5].upper()}",
        created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )
    cancelled = _base_order(
        customer_id=cid,
        po_number=f"PO-CAN-{uuid.uuid4().hex[:5].upper()}",
        created_at=datetime.now(timezone.utc).isoformat(),
        cancelled=True,
        status_label="Cancelled",
    )
    await server.db.orders.insert_many([valid, cancelled])

    found = await find_latest_valid_repeat_source(server.db, cid)
    assert found is not None
    assert found["po_number"] == valid["po_number"]


@pytest.mark.anyio
async def test_deleted_inactive_skipped_by_absence_and_test_flags():
    import server

    cid = f"cust-testflag-{uuid.uuid4().hex[:8]}"
    valid = _base_order(
        customer_id=cid,
        po_number=f"PO-OK-{uuid.uuid4().hex[:5].upper()}",
        created_at=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
    )
    testish = _base_order(
        customer_id=cid,
        po_number=f"PO-TST-{uuid.uuid4().hex[:5].upper()}",
        created_at=datetime.now(timezone.utc).isoformat(),
        is_test=True,
    )
    await server.db.orders.insert_many([valid, testish])

    found = await find_latest_valid_repeat_source(server.db, cid)
    assert found is not None
    assert found["po_number"] == valid["po_number"]


@pytest.mark.anyio
async def test_other_customer_po_never_selected():
    import server

    cid_a = f"cust-a-{uuid.uuid4().hex[:8]}"
    cid_b = f"cust-b-{uuid.uuid4().hex[:8]}"
    await server.db.orders.insert_one(_base_order(customer_id=cid_b, customer="Other Co"))
    found = await find_latest_valid_repeat_source(server.db, cid_a)
    assert found is None


@pytest.mark.anyio
async def test_repeat_payload_excludes_operational_and_workflow():
    order = _base_order()
    dto = build_repeat_source_dto(order)

    forbidden = {
        "id", "source_order_id", "current_stage", "status_label", "timeline",
        "packing_status", "delivery_status", "loading_checklist",
        "payment_status", "design_status", "design_approval_history",
        "payment_files", "sample_files", "surat_jalan_files", "delivery_proofs",
        "operator_splits", "accurate_id", "import_source",
    }
    for key in forbidden:
        assert key not in dto

    assert dto["source_po_number"] == order["po_number"]
    assert dto["customer_id"] == order["customer_id"]
    assert dto["outlet"] == "Pusat"
    assert dto["line_count"] == 2
    assert len(dto["line_items"]) == 2
    assert dto["line_items"][0]["name"] == "Paper Cup 12oz"
    assert dto["line_items"][1]["name"] == "Paper Cup 12oz"
    assert dto["line_items"][0]["color"] != dto["line_items"][1]["color"]
    assert "line_id" not in dto["line_items"][0]
    assert "id" not in dto["line_items"][0]
    assert "discount" not in dto
    assert "discount_amount" not in dto
    assert dto["design_file_count"] == 0
    assert dto["design_files"] == []


@pytest.mark.anyio
async def test_artwork_never_copied_into_repeat_dto():
    order = _base_order()
    dto = build_repeat_source_dto(order)
    assert dto["design_files"] == []
    assert dto["design_file_count"] == 0
    assert "bukti.pdf" not in str(dto)
    assert "sj.pdf" not in str(dto)
    assert "proof.jpg" not in str(dto)
    assert "design-a.png" not in str(dto.get("design_files"))


@pytest.mark.anyio
async def test_http_repeat_source_success_and_zero_writes():
    import server

    cid = f"cust-http-{uuid.uuid4().hex[:8]}"
    order = _base_order(customer_id=cid, po_number=f"PO-HTTP-{uuid.uuid4().hex[:5].upper()}")
    await server.db.orders.insert_one(order)
    before = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0, "updated_at": 1, "created_at": 1})

    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/sales-orders/repeat-source", params={"customer_id": cid})
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["found"] is True
            assert data["customer_id"] == cid
            src = data["source"]
            assert src["source_po_number"] == order["po_number"]
            assert src["customer_id"] == cid
            assert len(src["line_items"]) == 2
            assert "current_stage" not in src
            assert "payment_files" not in src
            assert src["design_file_count"] == 0
            assert src["design_files"] == []
            assert data.get("mode") == "customer"
    finally:
        app.dependency_overrides.pop(current_user, None)

    after = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
    assert after["updated_at"] == before["updated_at"]
    assert after["created_at"] == before["created_at"]
    assert after["design_files"][0]["id"] == "des-src-1"
    assert after["current_stage"] == order["current_stage"]


@pytest.mark.anyio
async def test_http_no_previous_po():
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(
                "/api/sales-orders/repeat-source",
                params={"customer_id": f"missing-{uuid.uuid4().hex}"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["found"] is False
            assert data["source"] is None
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_http_unauthorized_source_inaccessible():
    import server

    cid = f"cust-unauth-{uuid.uuid4().hex[:8]}"
    await server.db.orders.insert_one(_base_order(customer_id=cid))

    app.dependency_overrides[current_user] = _viewer_only
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/sales-orders/repeat-source", params={"customer_id": cid})
            assert res.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_specific_po_repeat_uses_exact_selected_order():
    import server

    cid = f"cust-specific-{uuid.uuid4().hex[:8]}"
    older = _base_order(
        customer_id=cid,
        po_number=f"PO-OLD-{uuid.uuid4().hex[:5].upper()}",
        created_at=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        notes="older",
    )
    selected = _base_order(
        customer_id=cid,
        po_number=f"PO-SEL-{uuid.uuid4().hex[:5].upper()}",
        created_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        notes="selected-po",
    )
    newer = _base_order(
        customer_id=cid,
        po_number=f"PO-NEW-{uuid.uuid4().hex[:5].upper()}",
        created_at=datetime.now(timezone.utc).isoformat(),
        notes="newest",
    )
    await server.db.orders.insert_many([older, selected, newer])

    found = await find_repeat_source_by_order_id(server.db, selected["id"])
    assert found is not None
    assert found["po_number"] == selected["po_number"]
    assert found["notes"] == "selected-po"

    latest = await find_latest_valid_repeat_source(server.db, cid)
    assert latest["po_number"] == newer["po_number"]

    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get(
                "/api/sales-orders/repeat-source",
                params={"order_id": selected["id"]},
            )
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["found"] is True
            assert data["mode"] == "order"
            assert data["source"]["source_po_number"] == selected["po_number"]
            assert data["source"]["notes"] == "selected-po"
            assert data["source"]["design_files"] == []
            assert "current_stage" not in data["source"]
            assert "packing_status" not in data["source"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_latest_repeat_prefers_canonical_customer_id_over_name_fallback():
    import server

    cid = f"cust-canon-{uuid.uuid4().hex[:8]}"
    name = f"PT Canon {uuid.uuid4().hex[:5]}"
    await server.db.sales_customers.insert_one(
        {"id": cid, "name": name, "normalized_name": name.lower()}
    )
    unlinked_newer = _base_order(
        customer_id=None,
        customer=name,
        po_number=f"PO-UNL-{uuid.uuid4().hex[:5].upper()}",
        created_at=datetime.now(timezone.utc).isoformat(),
        notes="unlinked-newer",
    )
    linked_older = _base_order(
        customer_id=cid,
        customer=name,
        po_number=f"PO-LNK-{uuid.uuid4().hex[:5].upper()}",
        created_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        notes="linked-older",
    )
    await server.db.orders.insert_many([unlinked_newer, linked_older])

    found = await find_latest_valid_repeat_source(server.db, cid)
    assert found is not None
    assert found["po_number"] == linked_older["po_number"]
    assert found["notes"] == "linked-older"


@pytest.mark.anyio
async def test_latest_repeat_name_fallback_only_when_unlinked():
    import server

    cid = f"cust-fb-{uuid.uuid4().hex[:8]}"
    name = f"PT Fallback {uuid.uuid4().hex[:5]}"
    await server.db.sales_customers.insert_one(
        {"id": cid, "name": name, "normalized_name": name.lower()}
    )
    unlinked = _base_order(
        customer_id=None,
        customer=name,
        po_number=f"PO-FB-{uuid.uuid4().hex[:5].upper()}",
        notes="legacy-unlinked",
    )
    other = _base_order(
        customer_id=f"other-{uuid.uuid4().hex[:8]}",
        customer=name,
        po_number=f"PO-OTH-{uuid.uuid4().hex[:5].upper()}",
        created_at=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        notes="other-stable-id",
    )
    await server.db.orders.insert_many([unlinked, other])

    found = await find_latest_valid_repeat_source(server.db, cid)
    assert found is not None
    assert found["po_number"] == unlinked["po_number"]

    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/sales-orders/repeat-source", params={"customer_id": cid})
            assert res.status_code == 200, res.text
            data = res.json()
            assert data["found"] is True
            assert data["customer_id"] == cid
            assert data["source"]["customer_id"] == cid
            assert data["source"]["source_po_number"] == unlinked["po_number"]
            assert data["source"]["design_files"] == []
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_create_with_design_files_gets_new_ids_and_stage_zero():
    """Repeat-seeded designs on create get new ids; workflow starts fresh."""
    po_num = f"PO-DES-{uuid.uuid4().hex[:6].upper()}"
    payload = {
        "po_number": po_num,
        "customer": "PT Design Seed",
        "customer_id": f"cust-des-{uuid.uuid4().hex[:8]}",
        "line_items": [
            {"name": "Item A", "quantity": 10, "unit": "pcs", "production_process": "print"},
        ],
        "design_files": [
            {"name": "copied-design.png", "data_url": TINY_PNG, "content_type": "image/png"},
        ],
    }
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post("/api/sales-orders", json=payload)
            assert res.status_code == 200, res.text
            order = res.json()["order"]
            assert order["current_stage"] == 0
            assert order["status_label"] == "Order Masuk"
            assert order["packing_status"] == "Belum"
            assert order["delivery_status"] == "Belum Terkirim"
            assert len(order["design_files"]) == 1
            assert order["design_files"][0]["name"] == "copied-design.png"
            assert order["design_files"][0]["id"] != "des-src-1"
            assert order["design_files"][0]["data_url"] == TINY_PNG
    finally:
        app.dependency_overrides.pop(current_user, None)
