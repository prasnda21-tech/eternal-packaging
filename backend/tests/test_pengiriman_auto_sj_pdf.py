"""Buat Pengiriman auto-generates a real Surat Jalan PDF and exposes it to Loading."""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.sales_deliveries import (
    derive_base_sj_number,
    generate_sales_delivery_pdf_bytes,
)
from services.surat_jalan_files import SOURCE_SYSTEM_GENERATED
from quantity_rules import (
    available_for_sj_loading_line,
    validate_loading_confirmation,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
def isolated_storage(monkeypatch):
    store = {}

    def put_object(path, data, content_type):
        if getattr(put_object, "fail", False):
            raise RuntimeError("storage unavailable")
        store[path] = {"data": data, "content_type": content_type, "size": len(data)}
        return {"path": path, "size": len(data)}

    def get_object(path):
        rec = store.get(path)
        if not rec:
            raise FileNotFoundError(path)
        return rec["data"], rec["content_type"]

    monkeypatch.setattr("services.sales_deliveries.obj_storage.put_object", put_object)
    monkeypatch.setattr("services.sales_deliveries.obj_storage.get_object", get_object)
    monkeypatch.setattr("services.storage.put_object", put_object)
    monkeypatch.setattr("services.storage.get_object", get_object)
    monkeypatch.setattr("server.obj_storage.put_object", put_object)
    monkeypatch.setattr("server.obj_storage.get_object", get_object)
    return store, put_object


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def admin_user():
    return {
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }


def helper_user():
    return {
        "email": "helper@eternalpackaging.com",
        "name": "Helper Tester",
        "role": "helper",
    }


async def create_helper_po(po_number_str: str, line_items: list, **kwargs):
    po_id = str(uuid.uuid4())
    packed_default = [
        {"line_idx": i, "packed_qty": int(li.get("quantity", 0))}
        for i, li in enumerate(line_items)
    ]
    doc = {
        "id": po_id,
        "po_number": po_number_str,
        "customer": kwargs.get("customer", "PT Auto SJ PDF"),
        "customer_name": kwargs.get("customer", "PT Auto SJ PDF"),
        "outlet": "Pusat",
        "recipient": "Budi",
        "address": "Jl. Auto SJ PDF No. 1",
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
        "packing_info": kwargs.get("packing_info", {
            "qty_packed": sum(int(li.get("quantity", 0)) for li in line_items),
            "lines": packed_default,
        }),
        "shipments": kwargs.get("shipments", []),
        "surat_jalan_files": kwargs.get("surat_jalan_files", []),
        "loading_confirmations": kwargs.get("loading_confirmations", []),
        "invoice_ids": [],
        "receipt_ids": [],
    }
    for k, v in kwargs.items():
        doc[k] = v
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.anyio
async def test_buat_pengiriman_creates_real_generated_pdf(isolated_storage):
    store, _ = isolated_storage
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Paper Cup 12oz", "quantity": 10000, "unit": "pcs"}])
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "delivery_date": "2026-09-04",
                "line_items": [{"line_idx": 0, "qty": 8000}],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            expected_sj = derive_base_sj_number(po_num)
            assert delivery["sj_number"] == expected_sj
            assert delivery["total_qty"] == 8000
            assert delivery.get("generated_sj_file_id") == delivery["id"]

            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert len(order["shipments"]) == 1
            sh = order["shipments"][0]
            assert sh["id"] == delivery["id"]
            assert sh["file_id"] == delivery["id"]
            files = order.get("surat_jalan_files") or []
            assert len(files) == 1
            gen = files[0]
            assert gen["id"] == delivery["id"]
            assert gen["shipment_id"] == delivery["id"]
            assert gen["source"] == SOURCE_SYSTEM_GENERATED
            assert gen["source_type"] == "PENGIRIMAN"
            assert gen["content_type"] == "application/pdf"
            assert gen["sj_number"] == expected_sj
            assert gen["storage_path"]
            assert gen["storage_path"] in store
            pdf = store[gen["storage_path"]]["data"]
            assert pdf.startswith(b"%PDF")
            assert len(pdf) > 500
            rendered = generate_sales_delivery_pdf_bytes(delivery)
            assert rendered.startswith(b"%PDF")
            assert delivery["sj_number"] == expected_sj

            rec = await server.db.files.find_one({"id": gen["id"], "is_deleted": {"$ne": True}}, {"_id": 0})
            assert rec
            assert rec["kind"] == "surat_jalan"
            assert rec["content_type"] == "application/pdf"
            assert rec["order_id"] == po["id"]
            assert rec["storage_path"] == gen["storage_path"]

            viewed = await client.get(f"/api/orders/{po['id']}/surat-jalan/files/{gen['id']}")
            assert viewed.status_code == 200
            assert viewed.content.startswith(b"%PDF")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_exact_same_name_lines_stay_separate_in_pdf_and_file(isolated_storage):
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [
        {"name": "Paper Cup", "quantity": 300, "unit": "pcs", "line_id": "line_a"},
        {"name": "Paper Cup", "quantity": 200, "unit": "pcs", "line_id": "line_b"},
    ])
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [
                    {"line_idx": 0, "line_id": "line_a", "qty": 100},
                    {"line_idx": 1, "line_id": "line_b", "qty": 50},
                ],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            assert len(delivery["line_items"]) == 2
            assert delivery["line_items"][0]["line_id"] == "line_a"
            assert delivery["line_items"][1]["line_id"] == "line_b"

            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            gen = (order.get("surat_jalan_files") or [])[0]
            assert len(gen["lines"]) == 2
            assert gen["lines"][0]["line_idx"] == 0
            assert gen["lines"][0]["line_id"] == "line_a"
            assert gen["lines"][0]["qty"] == 100
            assert gen["lines"][1]["line_idx"] == 1
            assert gen["lines"][1]["line_id"] == "line_b"
            assert gen["lines"][1]["qty"] == 50

            pdf = generate_sales_delivery_pdf_bytes(delivery)
            assert pdf.startswith(b"%PDF")
            assert len(delivery["line_items"]) == 2
            assert delivery["line_items"][0]["name"] == "Paper Cup"
            assert delivery["line_items"][1]["name"] == "Paper Cup"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_partial_shipments_keep_independent_pdfs(isolated_storage):
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Bowl", "quantity": 500, "unit": "pcs"}])
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            r1 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            r2 = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 150}],
            })
            assert r1.status_code == 200 and r2.status_code == 200
            d1, d2 = r1.json()["delivery"], r2.json()["delivery"]
            base = derive_base_sj_number(po_num)
            assert d1["sj_number"] == base
            assert d2["sj_number"] == f"{base}-2"
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            files = order.get("surat_jalan_files") or []
            assert len(files) == 2
            assert {f["shipment_id"] for f in files} == {d1["id"], d2["id"]}
            assert {f["sj_number"] for f in files} == {base, f"{base}-2"}
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_retry_does_not_duplicate_shipment_or_pdf(isolated_storage):
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup", "quantity": 500, "unit": "pcs"}])
    payload = {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 500}]}
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            r1 = await client.post("/api/sales-deliveries", json=payload)
            r2 = await client.post("/api/sales-deliveries", json=payload)
            assert r1.status_code == 200
            assert r2.status_code == 400
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert len(order["shipments"]) == 1
            assert len(order.get("surat_jalan_files") or []) == 1
            recs = await server.db.files.find({"order_id": po["id"], "kind": "surat_jalan"}).to_list(20)
            assert len(recs) == 1
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_storage_failure_does_not_create_fake_file(isolated_storage):
    store, put_object = isolated_storage
    put_object.fail = True
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup", "quantity": 500, "unit": "pcs"}])
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert res.status_code == 200, res.text
            delivery = res.json()["delivery"]
            assert delivery.get("generated_sj_file_error") == "storage_failed"
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert len(order["shipments"]) == 1
            assert len(order.get("surat_jalan_files") or []) == 0
            rec = await server.db.files.find_one({"id": delivery["id"]})
            assert rec is None
            assert store == {}
            missing = await client.get(f"/api/orders/{po['id']}/surat-jalan/files/{delivery['id']}")
            assert missing.status_code == 404
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_retry_completes_missing_generated_document(isolated_storage):
    store, put_object = isolated_storage
    put_object.fail = True
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup", "quantity": 500, "unit": "pcs"}])
    payload = {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 200}]}
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            first = await client.post("/api/sales-deliveries", json=payload)
            assert first.status_code == 200
            sid = first.json()["delivery"]["id"]
            put_object.fail = False
            second = await client.post("/api/sales-deliveries", json=payload)
            assert second.status_code == 200
            assert second.json()["delivery"]["id"] == sid
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert len(order["shipments"]) == 1
            files = order.get("surat_jalan_files") or []
            assert len(files) == 1
            assert files[0]["id"] == sid
            assert files[0]["storage_path"] in store
            assert store[files[0]["storage_path"]]["data"].startswith(b"%PDF")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_manual_upload_still_works_and_coexists(isolated_storage):
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup", "quantity": 500, "unit": "pcs"}])
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            created = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert created.status_code == 200
            tiny_pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
            import base64
            data_url = "data:application/pdf;base64," + base64.b64encode(tiny_pdf).decode("ascii")
            uploaded = await client.post(f"/api/orders/{po['id']}/attachment", json={
                "kind": "surat_jalan",
                "name": "signed-scan.pdf",
                "data_url": data_url,
            })
            assert uploaded.status_code == 200, uploaded.text
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            files = order.get("surat_jalan_files") or []
            assert len(files) == 2
            sources = {f.get("source") for f in files}
            names = {f.get("name") for f in files}
            assert SOURCE_SYSTEM_GENERATED in sources
            assert "signed-scan.pdf" in names
            generated = [f for f in files if f.get("source") == SOURCE_SYSTEM_GENERATED]
            manual = [f for f in files if f.get("name") == "signed-scan.pdf"]
            assert generated[0]["id"] != manual[0]["id"]
            assert generated[0]["storage_path"] != manual[0].get("storage_path")
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_unauthorized_file_view_is_403(isolated_storage):
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(po_num, [{"name": "Cup", "quantity": 500, "unit": "pcs"}])
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            created = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert created.status_code == 200
            file_id = created.json()["delivery"]["id"]
        app.dependency_overrides[current_user] = helper_user
        async with _client() as client:
            denied = await client.get(f"/api/orders/{po['id']}/surat-jalan/files/{file_id}")
            assert denied.status_code == 403
            uploaded = await client.post(f"/api/orders/{po['id']}/attachment", json={
                "kind": "surat_jalan",
                "name": "x.pdf",
                "data_url": "data:application/pdf;base64,JVBERi0=",
            })
            assert uploaded.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_loading_api_includes_generated_sj_and_packing_cap(isolated_storage):
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(
        po_num,
        [{"name": "Paper Cup 12oz", "quantity": 10000, "unit": "pcs"}],
        packing_info={},
    )
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            created = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 8000}],
            })
            assert created.status_code == 200, created.text
            delivery = created.json()["delivery"]
            await server.db.orders.update_one(
                {"id": po["id"]},
                {"$set": {"packing_info": {"qty_packed": 6000, "lines": [{"line_idx": 0, "packed_qty": 6000}]}}},
            )
            listed = await client.get(f"/api/orders/{po['id']}")
            assert listed.status_code == 200
            order = listed.json()["order"]
            public_files = order.get("surat_jalan_files") or []
            assert any(f.get("id") == delivery["id"] for f in public_files)
            assert all("storage_path" not in f for f in public_files)
            sj_files = order.get("sj_files") or []
            assert any((e.get("file_id") or e.get("id")) == delivery["id"] for e in sj_files)

            fresh = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert available_for_sj_loading_line(fresh, delivery["id"], 0) == 6000
            assert validate_loading_confirmation(fresh, delivery["id"], [{"line_idx": 0, "qty": 8000}])
            assert validate_loading_confirmation(fresh, delivery["id"], [{"line_idx": 0, "qty": 6000}]) is None

            over = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": delivery["id"],
                "lines": [{"line_idx": 0, "qty": 8000}],
            })
            assert over.status_code == 400
            ok = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": delivery["id"],
                "lines": [{"line_idx": 0, "qty": 6000}],
            })
            assert ok.status_code == 200, ok.text
            after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert available_for_sj_loading_line(after, delivery["id"], 0) == 0
            extra = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": delivery["id"],
                "lines": [{"line_idx": 0, "qty": 1}],
            })
            assert extra.status_code == 400
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_packing_zero_still_blocks_loading_after_generated_pdf(isolated_storage):
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(
        po_num,
        [{"name": "Cup", "quantity": 500, "unit": "pcs"}],
        packing_info={},
        current_stage=11,
    )
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            created = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert created.status_code == 200, created.text
            delivery = created.json()["delivery"]
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert (order.get("surat_jalan_files") or [])
            assert available_for_sj_loading_line(order, delivery["id"], 0) == 0
            blocked = await client.post(f"/api/orders/{po['id']}/loading-confirm", json={
                "surat_jalan_id": delivery["id"],
                "lines": [{"line_idx": 0, "qty": 1}],
            })
            assert blocked.status_code == 400
            assert "packing" in blocked.text.lower()
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_generated_pdf_does_not_skip_workflow_or_mutate_unrelated(isolated_storage):
    po_num = f"PO-PDF-{uuid.uuid4().hex[:6].upper()}"
    po = await create_helper_po(
        po_num,
        [{"name": "Cup", "quantity": 500, "unit": "pcs"}],
        current_stage=11,
        invoice_ids=["inv-keep"],
        hpp_total=12345,
    )
    before = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            res = await client.post("/api/sales-deliveries", json={
                "po_id": po["id"],
                "line_items": [{"line_idx": 0, "qty": 200}],
            })
            assert res.status_code == 200
            after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert after.get("current_stage") == 11
            assert after.get("invoice_ids") == ["inv-keep"]
            assert after.get("hpp_total") == 12345
            assert after.get("delivery_status") != "Terkirim"
            assert not after.get("delivery_proof_photos")
            assert before.get("po_number") == after.get("po_number")
    finally:
        app.dependency_overrides.pop(current_user, None)


def test_role_defaults_keep_view_and_upload_separate():
    helper = set(ROLE_DEFAULT_PERMISSIONS["helper"])
    driver = set(ROLE_DEFAULT_PERMISSIONS["driver"])
    crm = set(ROLE_DEFAULT_PERMISSIONS["crm"])
    assert "delivery_note.view_file" not in helper
    assert "delivery_note.manage" not in helper
    assert "loading.update" not in helper
    assert "delivery_note.view_file" in driver
    assert "delivery_note.manage" not in driver
    assert "loading.update" in driver
    assert "delivery_note.manage" in crm
    assert "delivery_note.view_file" not in crm
    assert "loading.update" not in crm
    # Every built-in Loading-capable default also has file view
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        if "loading.update" in perms:
            assert "delivery_note.view_file" in perms, role


def _sj_pdf_base_delivery(**overrides):
    base = {
        "customer": "Outlet Dummy PDF",
        "address": "Jl. Dummy 1",
        "sales": "Sales Dummy",
        "sj_number": "SJ-DUMMY-001",
        "expedition": "Internal",
        "created_by_name": "Admin",
        "delivery_date": "2026-10-19",
        "delivery_zone": "Zona A",
        "po_number": "PO-DUMMY-1",
        "recipient": "Penerima Dummy",
        "driver_name": "Kurir Dummy",
        "lines": [{"name": "Cup Dummy", "qty": 12, "unit": "PCS", "notes": ""}],
        "line_items": [{"name": "Cup Dummy", "qty": 12, "unit": "PCS", "line_idx": 0}],
    }
    base.update(overrides)
    return base


def test_sj_pdf_phone_space_underline_does_not_cross_glyph_band():
    """Phone sits below signature underline; ASCII spaces stay whitespace."""
    import io
    import pdfplumber
    from services.sales_deliveries import _format_sj_pdf_phone_display

    assert _format_sj_pdf_phone_display("0812\u00a03456") == "0812 3456"
    assert _format_sj_pdf_phone_display("  ") == ""

    cases = {
        "spaces": "0812 3456 7890",
        "nospace": "081234567890",
        "empty": "",
        "long": "0812 3456 7890 1234 5678 9012",
    }
    for label, phone in cases.items():
        delivery = _sj_pdf_base_delivery(recipient_phone=phone or None)
        before_phone = delivery.get("recipient_phone")
        before_sj = delivery.get("sj_number")
        pdf = generate_sales_delivery_pdf_bytes(
            delivery,
            recipient_name="Penerima Dummy",
            recipient_signed_at="2026-10-19T10:00:00+00:00",
            driver_name="Kurir Dummy",
            driver_signed_at="2026-10-19T10:00:00+00:00",
        )
        assert delivery.get("recipient_phone") == before_phone
        assert delivery.get("sj_number") == before_sj
        assert pdf.startswith(b"%PDF")
        with pdfplumber.open(io.BytesIO(pdf)) as doc:
            page = doc.pages[0]
            text = page.extract_text() or ""
            assert "Cup Dummy" in text or "Kode Barang" in text
            assert "Penerima" in text
            if not phone:
                continue
            shown = _format_sj_pdf_phone_display(phone)
            assert shown[:4] in text
            if " " in shown:
                # Spaces survive as whitespace separators (not tofu blocks).
                assert shown.split()[0] in text and shown.split()[1] in text
            phone_chars = [
                c for c in (page.chars or [])
                if c.get("text", "").isdigit() and c.get("x0", 0) > 470 and 320 < c.get("top", 0) < 380
            ]
            assert phone_chars, label
            phone_top = min(c["top"] for c in phone_chars)
            phone_bottom = max(c["bottom"] for c in phone_chars)
            # Signature name underline in the Penerima column (above phone band).
            sig_lines = [
                ln for ln in (page.lines or [])
                if (ln.get("width") or 0) > 20
                and (ln.get("x0") or 0) > 450
                and 320 < (ln.get("top") or 0) < 360
            ]
            assert sig_lines, label
            assert all((ln.get("top") or 0) <= phone_top + 0.5 for ln in sig_lines), (
                label, phone_top, [ln.get("top") for ln in sig_lines],
            )
            # Underline must not cut through the phone glyph vertical band.
            assert all(
                not (phone_top < (ln.get("top") or 0) < phone_bottom)
                for ln in sig_lines
            ), (label, phone_top, phone_bottom, [ln.get("top") for ln in sig_lines])


def test_sj_pdf_custom_signature_labels_keep_four_columns():
    import io
    import pdfplumber

    design = {
        "surat_jalan": {
            "signature_labels": ["Gudang", "QC", "Driver", "Customer"],
            "date_label": "Tanggal",
        }
    }
    pdf = generate_sales_delivery_pdf_bytes(
        _sj_pdf_base_delivery(recipient_phone="0812 3456 7890"),
        recipient_name="Penerima Dummy",
        design=design,
    )
    assert pdf.startswith(b"%PDF")
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        text = doc.pages[0].extract_text() or ""
    assert "Gudang" in text and "QC" in text and "Driver" in text and "Customer" in text
    assert "0812" in text and "3456" in text
