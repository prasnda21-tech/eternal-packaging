"""Read-performance tests for GET /api/sales-orders list payload and filter semantics."""
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx
from server import app, current_user


HEAVY_KEYS = (
    "sample_files",
    "design_files",
    "payment_files",
    "surat_jalan_files",
    "shipments",
    "timeline",
    "line_items",
    "_has_design_files",
    "_has_payment_files",
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


def _now():
    return datetime.now(timezone.utc).isoformat()


def _order(**overrides):
    oid = str(uuid.uuid4())
    marker = overrides.pop("marker", uuid.uuid4().hex[:8].upper())
    doc = {
        "id": oid,
        "po_number": f"PO-LIST-{marker}",
        "customer": f"Cust {marker}",
        "outlet": f"Outlet {marker}",
        "sales": "Vira",
        "order_date": "2026-09-01",
        "created_at": _now(),
        "total_quantity": 100,
        "current_stage": 0,
        "status_label": "Order Masuk",
        "cancelled": False,
        "design_status": "Waiting",
        "payment_status": "Waiting",
        "design_files": [{"name": "huge.bin", "data": "x" * 50000}],
        "payment_files": [{"name": "pay.bin", "data": "y" * 20000}],
        "sample_files": [{"name": "sample.bin", "data": "z" * 80000}],
        "surat_jalan_files": [{"name": "sj.bin", "data": "s" * 10000}],
        "timeline": [{"stage": 0, "note": "created"}],
        "line_items": [{"name": "Box", "quantity": 100}],
    }
    doc.update(overrides)
    return doc


@pytest.mark.anyio
async def test_list_omits_heavy_file_payload_and_keeps_table_fields():
    import server
    marker = uuid.uuid4().hex[:8].upper()
    hold_doc = _order(
        marker=f"H{marker}",
        current_stage=0,
        design_files=[],
        payment_files=[],
        created_at="2026-09-01T10:00:00+00:00",
    )
    ready_doc = _order(
        marker=f"R{marker}",
        current_stage=0,
        design_status="Approved",
        payment_status="Approved",
        design_files=[{"name": "d.pdf"}],
        payment_files=[{"name": "p.pdf"}],
        created_at="2026-09-01T11:00:00+00:00",
    )
    scheduled = _order(
        marker=f"S{marker}",
        current_stage=6,
        status_label="Penjadwalan",
        design_files=[],
        payment_files=[],
        created_at="2026-09-01T12:00:00+00:00",
    )
    cancelled = _order(
        marker=f"C{marker}",
        cancelled=True,
        current_stage=0,
        created_at="2026-09-01T13:00:00+00:00",
    )
    done = _order(
        marker=f"D{marker}",
        current_stage=14,
        status_label="Pengiriman",
        cancelled=False,
        design_files=[{"name": "d.pdf"}],
        payment_files=[{"name": "p.pdf"}],
        created_at="2026-09-01T14:00:00+00:00",
    )
    await server.db.orders.insert_many([hold_doc, ready_doc, scheduled, cancelled, done])
    await server.db.sales_deliveries.insert_one({
        "id": str(uuid.uuid4()),
        "po_id": ready_doc["id"],
        "is_cancelled": False,
        "total_qty": 40,
    })

    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-orders", params={"q": marker})
            assert listed.status_code == 200
            body = listed.json()
            by_id = {o["id"]: o for o in body["orders"]}
            assert set(by_id) == {hold_doc["id"], ready_doc["id"], scheduled["id"], cancelled["id"], done["id"]}

            for row in body["orders"]:
                for key in HEAVY_KEYS:
                    assert key not in row
                assert row["po_number"]
                assert row["customer"]
                assert "delivery_summary" in row
                assert "hold" in row
                assert "status_label" in row
                assert "total_quantity" in row

            assert by_id[hold_doc["id"]]["hold"] is True
            assert by_id[ready_doc["id"]]["hold"] is False
            assert by_id[scheduled["id"]]["hold"] is False
            assert by_id[done["id"]]["hold"] is False

            summary = by_id[ready_doc["id"]]["delivery_summary"]
            assert summary["total_ordered"] == 100
            assert summary["total_shipped"] == 40
            assert summary["total_remaining"] == 60
            assert summary["status"] == "SEBAGIAN"

            hold_list = await client.get("/api/sales-orders", params={"q": marker, "status": "hold"})
            hold_ids = {o["id"] for o in hold_list.json()["orders"]}
            assert hold_doc["id"] in hold_ids
            assert ready_doc["id"] not in hold_ids
            assert scheduled["id"] not in hold_ids

            aktif = await client.get("/api/sales-orders", params={"q": marker, "status": "aktif"})
            aktif_ids = {o["id"] for o in aktif.json()["orders"]}
            assert ready_doc["id"] in aktif_ids
            assert scheduled["id"] in aktif_ids
            assert hold_doc["id"] not in aktif_ids
            assert done["id"] not in aktif_ids
            assert cancelled["id"] not in aktif_ids

            selesai = await client.get("/api/sales-orders", params={"q": marker, "status": "selesai"})
            selesai_ids = {o["id"] for o in selesai.json()["orders"]}
            assert selesai_ids == {done["id"]}

            batal = await client.get("/api/sales-orders", params={"q": marker, "status": "cancelled"})
            batal_ids = {o["id"] for o in batal.json()["orders"]}
            assert batal_ids == {cancelled["id"]}

            created_order = [o["id"] for o in body["orders"]]
            assert created_order == [o["id"] for o in sorted(
                [hold_doc, ready_doc, scheduled, cancelled, done],
                key=lambda d: d["created_at"],
                reverse=True,
            )]
            assert body["orders"][0]["quantity_by_unit"] == [{"unit": "pcs", "quantity": 100}]

            mixed_id = str(uuid.uuid4())
            await server.db.orders.insert_one(_order(
                marker=f"M{marker}",
                id=mixed_id,
                customer=f"Cust {marker}",
                line_items=[
                    {"name": "Cup", "quantity": 10, "unit": "pcs"},
                    {"name": "Dus", "quantity": 2, "unit": "dus"},
                ],
                total_quantity=12,
                created_at="2026-09-02T00:00:00+00:00",
            ))
            paged = await client.get("/api/sales-orders", params={
                "q": marker,
                "status": "histori",
                "page": 1,
                "page_size": 1,
            })
            assert paged.status_code == 200
            page_body = paged.json()
            assert page_body["total"] == 2
            assert page_body["count"] == 1
            assert page_body["total"] != page_body["count"]
            assert {row["id"] for row in page_body["orders"]} <= {cancelled["id"], done["id"]}

            mixed = await client.get("/api/sales-orders", params={"q": f"M{marker}"})
            mixed_row = mixed.json()["orders"][0]
            assert mixed_row["quantity_by_unit"] == [
                {"unit": "pcs", "quantity": 10},
                {"unit": "dus", "quantity": 2},
            ]
            assert "line_items" not in mixed_row
            assert {part["unit"] for part in mixed_row["quantity_by_unit"]} == {"pcs", "dus"}
            assert not any(
                part["unit"] == "pcs" and part["quantity"] == 12
                for part in mixed_row["quantity_by_unit"]
            )
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": {"$regex": marker}})
        await server.db.sales_deliveries.delete_many({"po_id": ready_doc["id"]})


def _slim(marker, **overrides):
    oid = overrides.pop("id", None) or str(uuid.uuid4())
    label = overrides.pop("label", uuid.uuid4().hex[:6])
    doc = {
        "id": oid,
        "po_number": f"PO-GAB-{marker}-{label}",
        "customer": f"Cust {marker}",
        "outlet": f"Outlet {marker}",
        "sales": "Vira",
        "sales_user_id": overrides.get("sales_user_id", ""),
        "order_date": "2026-09-15",
        "created_at": "2026-09-15T00:00:00+00:00",
        "total_quantity": 10,
        "current_stage": 6,
        "status_label": "Penjadwalan",
        "cancelled": False,
        "design_status": "Waiting",
        "payment_status": "Waiting",
        "design_files": [],
        "payment_files": [],
        "line_items": [{"name": "Box", "quantity": 10, "unit": "pcs"}],
    }
    doc.update(overrides)
    return doc


@pytest.mark.anyio
async def test_gabungan_unions_aktif_and_histori_without_hold_or_unstaged():
    import server
    marker = uuid.uuid4().hex[:8].upper()
    owner_a = f"owner-a-{marker}"
    owner_b = f"owner-b-{marker}"
    aktif = _slim(marker, label="AKTIF", sales_user_id=owner_a, created_at="2026-09-15T06:00:00+00:00", customer=f"Alpha {marker}")
    selesai = _slim(
        marker, label="SELESAI", sales_user_id=owner_a, current_stage=14, status_label="Pengiriman",
        created_at="2026-09-16T05:00:00+00:00", order_date="2026-09-16", customer=f"Alpha {marker}",
    )
    batal = _slim(
        marker, label="BATAL", sales_user_id=owner_a, cancelled=True, current_stage=2,
        created_at="2026-09-17T05:00:00+00:00", order_date="2026-09-17", customer=f"Beta {marker}",
    )
    batal_stage = _slim(
        marker, label="BATAL14", sales_user_id=owner_a, cancelled=True, current_stage=14,
        created_at="2026-09-18T05:00:00+00:00", order_date="2026-09-18", customer=f"Gamma {marker}",
    )
    hold = _slim(
        marker, label="HOLD", sales_user_id=owner_a, current_stage=0,
        created_at="2026-09-19T05:00:00+00:00", order_date="2026-09-19", customer=f"Hold {marker}",
    )
    unstaged = _slim(
        marker, label="BARE", sales_user_id=owner_a,
        created_at="2026-09-20T05:00:00+00:00", order_date="2026-09-20", customer=f"Bare {marker}",
    )
    unstaged.pop("current_stage", None)
    aktif_old = _slim(
        marker, label="OLD-A", sales_user_id=owner_a, order_date="2026-01-02",
        created_at="2026-01-02T05:00:00+00:00", customer=f"Alpha {marker}",
    )
    histori_old = _slim(
        marker, label="OLD-H", sales_user_id=owner_a, cancelled=True, current_stage=1,
        order_date="2026-01-03", created_at="2026-01-03T05:00:00+00:00", customer=f"Alpha {marker}",
    )
    other_aktif = _slim(
        marker, label="OTH-A", sales_user_id=owner_b,
        created_at="2026-09-15T08:00:00+00:00", customer=f"Alpha {marker}",
    )
    other_histori = _slim(
        marker, label="OTH-H", sales_user_id=owner_b, cancelled=True, current_stage=4,
        created_at="2026-09-15T07:00:00+00:00", customer=f"Alpha {marker}",
    )
    docs = [aktif, selesai, batal, batal_stage, hold, unstaged, aktif_old, histori_old, other_aktif, other_histori]
    await server.db.orders.insert_many(docs)
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            gabungan = await client.get("/api/sales-orders", params={"q": marker, "status": "gabungan", "page": 1, "page_size": 50})
            assert gabungan.status_code == 200
            body = gabungan.json()
            ids = [row["id"] for row in body["orders"]]
            assert body["total"] == 8
            assert body["count"] == 8
            assert body["capped"] is False
            assert len(ids) == len(set(ids))
            assert set(ids) == {
                aktif["id"], selesai["id"], batal["id"], batal_stage["id"],
                aktif_old["id"], histori_old["id"], other_aktif["id"], other_histori["id"],
            }
            assert hold["id"] not in ids
            assert unstaged["id"] not in ids
            assert ids.count(batal_stage["id"]) == 1
            assert body["orders"][0]["delivery_summary"]["status"]

            dated = await client.get("/api/sales-orders", params={
                "q": marker,
                "status": "gabungan",
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
                "page": 1,
                "page_size": 50,
            })
            dated_ids = {row["id"] for row in dated.json()["orders"]}
            assert aktif_old["id"] not in dated_ids
            assert histori_old["id"] not in dated_ids
            assert aktif["id"] in dated_ids
            assert selesai["id"] in dated_ids
            assert batal["id"] in dated_ids

            needle = await client.get("/api/sales-orders", params={
                "q": f"Alpha {marker}",
                "status": "gabungan",
                "page": 1,
                "page_size": 50,
            })
            needle_ids = {row["id"] for row in needle.json()["orders"]}
            assert needle_ids == {
                aktif["id"], selesai["id"], aktif_old["id"], histori_old["id"],
                other_aktif["id"], other_histori["id"],
            }

            pages = []
            seen = []
            for page in range(1, 9):
                page_res = await client.get("/api/sales-orders", params={
                    "q": marker, "status": "gabungan", "page": page, "page_size": 1,
                })
                page_body = page_res.json()
                assert page_body["total"] == 8
                assert page_body["count"] == 1
                assert page_body["page"] == page
                assert page_body["page_size"] == 1
                seen.extend(row["id"] for row in page_body["orders"])
                pages.append(page_body["orders"][0]["id"])
            assert len(seen) == len(set(seen)) == 8
            assert pages[0] != pages[1]

            aktif_res = await client.get("/api/sales-orders", params={"q": marker, "status": "aktif"})
            assert {row["id"] for row in aktif_res.json()["orders"]} == {aktif["id"], aktif_old["id"], other_aktif["id"]}
            histori_res = await client.get("/api/sales-orders", params={"q": marker, "status": "histori"})
            assert {row["id"] for row in histori_res.json()["orders"]} == {
                selesai["id"], batal["id"], batal_stage["id"], histori_old["id"], other_histori["id"],
            }
            hold_res = await client.get("/api/sales-orders", params={"q": marker, "status": "hold"})
            # Missing current_stage is outside Aktif/Histori, and the existing HOLD
            # snapshot treats that absence as stage 0 when design/payment are empty.
            assert {row["id"] for row in hold_res.json()["orders"]} == {hold["id"], unstaged["id"]}
            all_res = await client.get("/api/sales-orders", params={"q": marker, "status": "all"})
            assert {row["id"] for row in all_res.json()["orders"]} == {doc["id"] for doc in docs}
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": {"$regex": marker}})


@pytest.mark.anyio
async def test_gabungan_owner_filter_applies_to_both_branches(monkeypatch):
    import server
    monkeypatch.setattr(server, "reject_sales_pwa_bypass", lambda user: None)
    marker = uuid.uuid4().hex[:8].upper()
    owner_a = f"owner-a-{marker}"
    owner_b = f"owner-b-{marker}"
    own_aktif = _slim(marker, label="OWN-A", sales_user_id=owner_a, customer=f"Own {marker}")
    own_histori = _slim(
        marker, label="OWN-H", sales_user_id=owner_a, cancelled=True, current_stage=2,
        customer=f"Own {marker}", created_at="2026-09-15T02:00:00+00:00",
    )
    other_aktif = _slim(marker, label="NO-A", sales_user_id=owner_b, customer=f"Own {marker}")
    other_histori = _slim(
        marker, label="NO-H", sales_user_id=owner_b, cancelled=True, current_stage=2,
        customer=f"Own {marker}", created_at="2026-09-15T03:00:00+00:00",
    )
    await server.db.orders.insert_many([own_aktif, own_histori, other_aktif, other_histori])

    def _owner():
        return {
            "email": f"owner-{marker}@example.com",
            "name": "Owner",
            "role": "sales_eternal",
            "id": owner_a,
            "user_id": owner_a,
            "permissions_override": {"sales_order.view": True},
        }

    app.dependency_overrides[current_user] = _owner
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-orders", params={"q": marker, "status": "gabungan", "page": 1, "page_size": 20})
            assert listed.status_code == 200
            assert {row["id"] for row in listed.json()["orders"]} == {own_aktif["id"], own_histori["id"]}
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": {"$regex": marker}})


@pytest.mark.anyio
async def test_gabungan_keeps_each_branch_window_when_histori_exceeds_1000():
    import server
    marker = uuid.uuid4().hex[:8].upper()
    aktif = _slim(
        marker, label="KEEP", customer=f"Cap {marker}",
        created_at="2020-01-01T00:00:00+00:00", order_date="2020-01-01",
    )
    dropped = _slim(
        marker, label="DROP", customer=f"Cap {marker}", cancelled=True, current_stage=1,
        created_at="2026-08-01T00:00:00+00:00",
    )
    newest = _slim(
        marker, label="NEW", customer=f"Cap {marker}", cancelled=True, current_stage=1,
        created_at="2026-08-01T00:16:40+00:00",
    )
    from datetime import timedelta
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    histori = []
    for index in range(1, 1000):
        created = (base + timedelta(seconds=index)).isoformat()
        histori.append(_slim(
            marker,
            label=f"H{index}",
            customer=f"Cap {marker}",
            cancelled=True,
            current_stage=1,
            created_at=created,
            order_date="2026-08-01",
        ))
    await server.db.orders.insert_many([aktif, dropped, newest, *histori])
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            combined = await client.get("/api/sales-orders", params={"q": marker, "status": "gabungan"})
            assert combined.status_code == 200
            combined_ids = {row["id"] for row in combined.json()["orders"]}
            assert combined.json()["count"] == 1001
            assert aktif["id"] in combined_ids
            assert newest["id"] in combined_ids
            assert dropped["id"] not in combined_ids

            paged = await client.get("/api/sales-orders", params={
                "q": marker, "status": "gabungan", "page": 21, "page_size": 50,
            })
            page_body = paged.json()
            assert page_body["capped"] is True
            assert page_body["total"] == 1001
            assert page_body["count"] == 1
            assert page_body["orders"][0]["id"] == aktif["id"]

            everything = await client.get("/api/sales-orders", params={"q": marker, "status": "all"})
            all_ids = {row["id"] for row in everything.json()["orders"]}
            assert everything.json()["count"] == 1000
            assert aktif["id"] not in all_ids
            assert dropped["id"] not in all_ids

            aktif_only = await client.get("/api/sales-orders", params={"q": marker, "status": "aktif"})
            assert {row["id"] for row in aktif_only.json()["orders"]} == {aktif["id"]}
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"customer": f"Cap {marker}"})


@pytest.mark.anyio
async def test_sales_order_list_still_rejects_missing_permission_and_sales_eternal():
    import server
    app.dependency_overrides[current_user] = lambda: {
        "email": "nobody-sales-list@example.com",
        "name": "Nobody",
        "role": "role_without_sales_list",
        "id": "nobody-sales-list",
    }
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get("/api/sales-orders", params={"status": "gabungan"})
            assert denied.status_code == 403
            app.dependency_overrides[current_user] = lambda: {
                "email": "sales-eternal-list@example.com",
                "name": "Sales Eternal",
                "role": "sales_eternal",
                "id": "sales-eternal-list",
                "user_id": "sales-eternal-list",
                "permissions_override": {"sales_order.view": True, "po.view": True},
            }
            locked = await client.get("/api/sales-orders", params={"status": "gabungan"})
            assert locked.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


def _ids(response):
    assert response.status_code == 200
    return {row["id"] for row in response.json()["orders"]}


@pytest.mark.anyio
async def test_list_projection_customer_approval_reaches_hold_snapshot():
    """Approved customer design leaves HOLD only through the list projection."""
    import server
    marker = uuid.uuid4().hex[:8].upper()
    approved = _order(
        marker=marker,
        po_number=f"PO-LIST-{marker}-OK",
        current_stage=2,
        status_label="Design",
        design_status="Waiting",
        design_bypassed=False,
        payment_status="Approved",
        payment_bypassed=False,
        design_files=[{"name": "design.pdf", "data": "x" * 4000}],
        payment_files=[{"name": "pay.pdf", "data": "y" * 4000}],
        customer_approval={
            "status": "approved",
            "token": "secret-token",
            "note": "do-not-leak",
        },
        created_at="2026-09-03T10:00:00+00:00",
    )
    pending = _order(
        marker=marker,
        po_number=f"PO-LIST-{marker}-WAIT",
        current_stage=2,
        status_label="Design",
        design_status="Waiting",
        design_bypassed=False,
        payment_status="Approved",
        payment_bypassed=False,
        design_files=[{"name": "design.pdf", "data": "x" * 4000}],
        payment_files=[{"name": "pay.pdf", "data": "y" * 4000}],
        customer_approval={
            "status": "pending",
            "token": "secret-token",
            "note": "do-not-leak",
        },
        created_at="2026-09-03T11:00:00+00:00",
    )
    await server.db.orders.insert_many([approved, pending])
    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/sales-orders", params={"q": marker})
            assert listed.status_code == 200
            by_id = {row["id"]: row for row in listed.json()["orders"]}
            assert set(by_id) == {approved["id"], pending["id"]}

            approved_row = by_id[approved["id"]]
            pending_row = by_id[pending["id"]]
            assert approved_row["customer_approval"] == {"status": "approved"}
            assert pending_row["customer_approval"] == {"status": "pending"}
            assert "design_files" not in approved_row
            assert "payment_files" not in approved_row
            assert approved_row["hold"] is False
            assert approved_row["hold_reasons"] == []
            assert pending_row["hold"] is True
            assert pending_row["hold_reasons"] == ["Menunggu approval design"]

            params = {"q": marker, "page": 1, "page_size": 50}
            aktif_ids = _ids(await client.get("/api/sales-orders", params={**params, "status": "aktif"}))
            gabungan_ids = _ids(await client.get("/api/sales-orders", params={**params, "status": "gabungan"}))
            hold_ids = _ids(await client.get("/api/sales-orders", params={**params, "status": "hold"}))

            assert approved["id"] in aktif_ids
            assert approved["id"] in gabungan_ids
            assert approved["id"] not in hold_ids
            assert pending["id"] not in aktif_ids
            assert pending["id"] not in gabungan_ids
            assert pending["id"] in hold_ids

            gabungan = await client.get("/api/sales-orders", params={**params, "status": "gabungan"})
            gabungan_row = gabungan.json()["orders"][0]
            assert gabungan_row["id"] == approved["id"]
            assert gabungan_row["customer_approval"] == {"status": "approved"}
            assert gabungan_row["hold"] is False
            assert gabungan.json()["total"] == 1
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": {"$regex": marker}})


@pytest.mark.anyio
async def test_header_filters_and_invoice_pre_pagination_acceptance():
    """Owner matrix: Sales ∩ Tanggal PO ∩ Status Faktur; process independent of tab; >50 page."""
    import server

    marker = uuid.uuid4().hex[:8].upper()
    base = dict(
        design_status="Approved",
        payment_status="Approved",
        design_files=[{"name": "d.pdf"}],
        payment_files=[{"name": "p.pdf"}],
        line_items=[{"line_idx": 0, "name": "Box", "quantity": 100, "unit": "pcs"}],
        total_quantity=100,
    )
    po001 = _order(
        marker=f"A{marker}",
        sales="Sales A",
        order_date="2026-10-10",
        current_stage=0,
        design_files=[],
        payment_files=[],
        design_status="Waiting",
        payment_status="Waiting",
        line_items=[{"line_idx": 0, "name": "Box", "quantity": 100, "unit": "pcs"}],
        total_quantity=100,
        created_by_user="Creator A",
        created_at="2026-10-10T10:00:00+00:00",
    )
    po002 = _order(
        marker=f"B{marker}",
        **base,
        sales="Sales A",
        order_date="2026-10-12",
        current_stage=5,
        status_label="Penjadwalan",
        created_by_user="Creator A",
        created_at="2026-10-12T10:00:00+00:00",
    )
    po003 = _order(
        marker=f"C{marker}",
        **base,
        sales="Sales B",
        order_date="2026-10-10",
        current_stage=5,
        status_label="Penjadwalan",
        created_by_user="",
        created_at="2026-10-10T11:00:00+00:00",
    )
    filler = []
    for i in range(55):
        filler.append(_order(
            marker=f"F{i:02d}{marker}",
            **base,
            sales="Sales A",
            order_date="2026-10-05",
            current_stage=5,
            status_label="Penjadwalan",
            created_by_user="Creator A",
            created_at=f"2026-10-05T{i % 20:02d}:00:00+00:00",
        ))
    await server.db.orders.insert_many([po001, po002, po003, *filler])
    await server.db.sales_invoices.insert_many([
        {
            "id": str(uuid.uuid4()),
            "po_id": po001["id"],
            "financial_status": "draft",
            "lines": [{"line_idx": 0, "quantity": 100}],
        },
        {
            "id": str(uuid.uuid4()),
            "po_id": po003["id"],
            "financial_status": "finalized",
            "lines": [{"line_idx": 0, "quantity": 100}],
        },
    ])

    app.dependency_overrides[current_user] = _admin
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            matrix = await client.get("/api/sales-orders", params={
                "list_tab": "all",
                "q": marker,
                "sales": "Sales A",
                "order_date_from": "2026-10-01",
                "order_date_to": "2026-10-11",
                "invoice_status": "sudah",
                "page": 1,
                "page_size": 50,
            })
            assert matrix.status_code == 200, matrix.text
            body = matrix.json()
            assert body["total"] == 1
            assert [row["id"] for row in body["orders"]] == [po001["id"]]
            assert body["orders"][0]["invoice_status"] == "sudah"

            hold = await client.get("/api/sales-orders", params={
                "list_tab": "all",
                "q": marker,
                "process_label": "HOLD",
                "page": 1,
                "page_size": 50,
            })
            assert hold.status_code == 200
            hold_ids = {row["id"] for row in hold.json()["orders"]}
            assert po001["id"] in hold_ids
            assert po002["id"] not in hold_ids

            active_hold = await client.get("/api/sales-orders", params={
                "list_tab": "active",
                "q": marker,
                "process_label": "HOLD",
                "page": 1,
                "page_size": 50,
            })
            assert active_hold.status_code == 200
            # Missing design/payment files → HOLD badge, but five-tab stays Aktif
            # unless approval files are waiting. Intersection must keep HOLD rows.
            assert po001["id"] in {row["id"] for row in active_hold.json()["orders"]}
            assert all(row.get("hold") for row in active_hold.json()["orders"])

            creator = await client.get("/api/sales-orders", params={
                "list_tab": "all",
                "q": marker,
                "created_by": "__unknown__",
                "page": 1,
                "page_size": 50,
            })
            assert creator.status_code == 200
            assert [row["id"] for row in creator.json()["orders"]] == [po003["id"]]

            # Invoice match beyond first page of unfiltered Sales A set.
            page_probe = await client.get("/api/sales-orders", params={
                "list_tab": "all",
                "q": marker,
                "sales": "Sales A",
                "invoice_status": "sudah",
                "page": 1,
                "page_size": 50,
            })
            assert page_probe.status_code == 200
            assert page_probe.json()["total"] == 1
            assert page_probe.json()["orders"][0]["id"] == po001["id"]

            opts = await client.get("/api/sales-orders/options")
            assert opts.status_code == 200
            opt_body = opts.json()
            assert "creator_options" in opt_body
            assert "qty_unit_options" in opt_body
    finally:
        app.dependency_overrides.pop(current_user, None)
        await server.db.orders.delete_many({"po_number": {"$regex": marker}})
        await server.db.sales_invoices.delete_many({"po_id": {"$in": [po001["id"], po002["id"], po003["id"]]}})
