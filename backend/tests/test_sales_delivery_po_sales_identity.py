"""Stage C.1: new Surat Jalan Sales must follow the associated PO snapshot.

Server ignores client payload.sales (match, spoof, or missing).
Does not rewrite existing SJ. Does not require sales_master_id.
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_sj_po_sales")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from services.sales_deliveries import create_sales_delivery, po_sales_snapshot

DB_NAME = "eternal_packaging_sj_po_sales"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[DB_NAME]
    await client.drop_database(DB_NAME)
    await database.sales_deliveries.create_index("id", unique=True)
    await database.sales_deliveries.create_index("sj_number", unique=True)
    await database.sales_deliveries.create_index(
        [("po_id", 1), ("shipment_index", 1)], unique=True
    )
    yield database
    await client.drop_database(DB_NAME)
    client.close()


@pytest.fixture
def storage(monkeypatch):
    def put_object(path, data, content_type):
        return {"path": path, "size": len(data)}

    monkeypatch.setattr("services.sales_deliveries.obj_storage.put_object", put_object)
    return put_object


def _user():
    return {"email": "crm@example.com", "name": "CRM", "role": "admin"}


async def _po(db, *, sales="Vira", sales_master_id=None, qty=100, extra=None):
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": f"PO/2026/11/02/{uuid.uuid4().hex[:6]}",
        "customer": "PT Uji Sales",
        "outlet": "Outlet A",
        "recipient": "Penerima",
        "address": "Jl. Uji",
        "delivery_zone": "Barat",
        "sales": sales,
        "line_items": [
            {"name": "Cup", "quantity": qty, "unit": "pcs", "line_id": f"{po_id}_line_0"}
        ],
        "cancelled": False,
        "shipments": [],
        "updated_at": "2026-11-02T00:00:00+00:00",
    }
    if sales_master_id is not None:
        doc["sales_master_id"] = sales_master_id
    if extra:
        doc.update(extra)
    await db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _create(db, po, *, qty=10, sales=None, omit_sales=False):
    payload = {
        "po_id": po["id"],
        "line_items": [{"line_idx": 0, "qty": qty}],
    }
    if not omit_sales:
        payload["sales"] = sales
    import services.sales_deliveries as mod

    original = mod.handle_sales_delivery_created

    async def _stock(*_a, **_k):
        return []

    mod.handle_sales_delivery_created = _stock
    try:
        return await create_sales_delivery(db, payload, _user())
    finally:
        mod.handle_sales_delivery_created = original


def test_po_sales_snapshot_prefers_order_fields():
    assert po_sales_snapshot({"sales": "Vira", "seller": "Other"}) == "Vira"
    assert po_sales_snapshot({"seller": "Alfi"}) == "Alfi"
    assert po_sales_snapshot({"sales_name": "Euis"}) == "Euis"
    assert po_sales_snapshot({}) == ""
    assert po_sales_snapshot(None) == ""


@pytest.mark.anyio
async def test_matching_client_sales_uses_po(db, storage):
    po = await _po(db, sales="Vira", sales_master_id="sm-vira")
    created = await _create(db, po, sales="Vira")
    assert created["sales"] == "Vira"
    assert created["sales_name"] == "Vira"
    stored = await db.sales_deliveries.find_one({"id": created["id"]}, {"_id": 0})
    assert stored["sales"] == "Vira"


@pytest.mark.anyio
async def test_spoofed_client_sales_ignored(db, storage):
    po = await _po(db, sales="Vira")
    created = await _create(db, po, sales="HACKER-OVERRIDE")
    assert created["sales"] == "Vira"
    assert created["sales_name"] == "Vira"
    stored = await db.sales_deliveries.find_one({"id": created["id"]}, {"_id": 0})
    assert stored["sales"] == "Vira"
    assert stored["sales"] != "HACKER-OVERRIDE"


@pytest.mark.anyio
async def test_missing_client_sales_uses_po(db, storage):
    po = await _po(db, sales="Alfi")
    created = await _create(db, po, omit_sales=True)
    assert created["sales"] == "Alfi"
    assert created["sales_name"] == "Alfi"


@pytest.mark.anyio
async def test_legacy_po_without_sales_master_id(db, storage):
    po = await _po(db, sales="Legacy Sales")
    assert "sales_master_id" not in po
    created = await _create(db, po, sales="Ignored")
    assert created["sales"] == "Legacy Sales"


@pytest.mark.anyio
async def test_missing_po_sales_stores_empty(db, storage):
    po = await _po(db, sales="")
    created = await _create(db, po, sales="ShouldNotWin")
    assert created["sales"] == ""
    assert created["sales_name"] == ""


@pytest.mark.anyio
async def test_does_not_borrow_sales_from_other_customer_po(db, storage):
    other = await _po(db, sales="OtherPOSales")
    await db.orders.update_one(
        {"id": other["id"]},
        {"$set": {"customer": "PT Uji Sales", "created_at": "2026-11-01T00:00:00+00:00"}},
    )
    po = await _po(db, sales="")
    created = await _create(db, po, sales="ClientGuess")
    assert created["sales"] == ""
    assert created["sales"] != "OtherPOSales"


@pytest.mark.anyio
async def test_multi_sj_each_keeps_po_sales(db, storage):
    po = await _po(db, sales="Vira", qty=100)
    first = await _create(db, po, qty=10, sales="Spoof1")
    second = await _create(db, po, qty=15, sales="Spoof2")
    assert first["id"] != second["id"]
    assert first["sales"] == "Vira"
    assert second["sales"] == "Vira"
    assert first["shipment_index"] != second["shipment_index"]
    rows = await db.sales_deliveries.find({"po_id": po["id"]}, {"_id": 0}).to_list(10)
    assert len(rows) == 2
    assert {r["sales"] for r in rows} == {"Vira"}


@pytest.mark.anyio
async def test_invalid_po_reference(db, storage):
    with pytest.raises(HTTPException) as exc:
        await create_sales_delivery(
            db,
            {"po_id": "missing-po-id", "line_items": [{"line_idx": 0, "qty": 1}], "sales": "X"},
            _user(),
        )
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_existing_sj_sales_not_rewritten(db, storage):
    po = await _po(db, sales="Vira")
    legacy_id = str(uuid.uuid4())
    await db.sales_deliveries.insert_one(
        {
            "id": legacy_id,
            "delivery_id": legacy_id,
            "po_id": po["id"],
            "po_number": po["po_number"],
            "sj_number": f"SJ-{uuid.uuid4().hex[:8]}",
            "shipment_index": 1,
            "sales": "HistoricalOldName",
            "sales_name": "HistoricalOldName",
            "is_cancelled": False,
            "status": "SEBAGIAN",
            "line_items": [
                {
                    "line_idx": 0,
                    "line_id": f"{po['id']}_line_0",
                    "qty": 5,
                    "name": "Cup",
                    "unit": "pcs",
                }
            ],
            "total_qty": 5,
        }
    )
    # Different qty fingerprint so create is a new SJ (not incomplete-retry reuse).
    created = await _create(db, po, qty=7, sales="Spoof")
    assert created["id"] != legacy_id
    assert created["sales"] == "Vira"
    legacy = await db.sales_deliveries.find_one({"id": legacy_id}, {"_id": 0})
    assert legacy["sales"] == "HistoricalOldName"
