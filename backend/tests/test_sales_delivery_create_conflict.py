"""Regression: creating a Surat Jalan must not report a false concurrency conflict.

Production unique indexes:
- sales_deliveries.id
- sales_deliveries.sj_number
- (po_id, shipment_index)

The create retry used count(active rows)+1. A deleted earlier shipment leaves a
higher shipment_index in place, and sj_number is unique across POs. The same
key was inserted three times and surfaced as HTTP 409
"Terjadi konflik transaksi bersamaan".
"""
import os
import sys
import uuid
from pathlib import Path

import pytest
from pymongo.errors import DuplicateKeyError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_sj_conflict")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from services.sales_deliveries import (
    create_sales_delivery,
    derive_sj_number,
    get_po_delivery_summary,
)

CONFLICT = "Terjadi konflik transaksi bersamaan. Silakan muat ulang dan coba lagi."
DB_NAME = "eternal_packaging_sj_conflict"


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
        if getattr(put_object, "fail", False):
            raise RuntimeError("storage unavailable")
        return {"path": path, "size": len(data)}

    monkeypatch.setattr("services.sales_deliveries.obj_storage.put_object", put_object)
    return put_object


def _user():
    return {"email": "crm@example.com", "name": "CRM", "role": "admin"}


class _CachedDb:
    """Motor builds a new collection object on every attribute access.

    Tests that replace insert_one/update_one need a stable collection.
    """

    def __init__(self, database):
        self._db = database
        self._cache = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        cached = self._cache.get(name)
        if cached is None:
            cached = getattr(self._db, name)
            self._cache[name] = cached
        return cached


async def _po(db, *, qty=1000, po_number=None):
    po_id = str(uuid.uuid4())
    number = po_number or f"PO/2026/09/22/{uuid.uuid4().hex[:6]}"
    doc = {
        "id": po_id,
        "po_number": number,
        "customer": "PT Uji",
        "outlet": "Gudang",
        "recipient": "Penerima",
        "address": "Jl. Uji",
        "delivery_zone": "Barat",
        "sales": "Sari",
        "line_items": [{"name": "Cup", "quantity": qty, "unit": "pcs", "line_id": f"{po_id}_line_0"}],
        "cancelled": False,
        "shipments": [],
        "updated_at": "2026-09-22T00:00:00+00:00",
    }
    await db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _seed_delivery(db, po, *, shipment_index, qty, cancelled=False):
    sj = derive_sj_number(po["po_number"], shipment_index)
    doc = {
        "id": str(uuid.uuid4()),
        "delivery_id": str(uuid.uuid4()),
        "po_id": po["id"],
        "po_number": po["po_number"],
        "sj_number": sj,
        "shipment_index": shipment_index,
        "is_cancelled": cancelled,
        "status": "SEBAGIAN",
        "line_items": [{
            "line_idx": 0,
            "line_id": f"{po['id']}_line_0",
            "qty": qty,
            "name": "Cup",
            "unit": "pcs",
        }],
        "total_qty": qty,
    }
    await db.sales_deliveries.insert_one(doc)
    return doc


@pytest.mark.anyio
async def test_opening_summary_has_no_conflict_and_authoritative_number(db, storage):
    po = await _po(db)
    await _seed_delivery(db, po, shipment_index=2, qty=100)
    summary = await get_po_delivery_summary(db, po["id"])
    assert summary["can_create_delivery"] is True
    assert summary["suggested_sj_number"] == derive_sj_number(po["po_number"], 3)
    assert summary["next_shipment_index"] == 3
    assert CONFLICT not in str(summary)


@pytest.mark.anyio
async def test_gap_after_deleted_shipment_creates_one_surat_jalan(db, storage):
    po = await _po(db)
    kept = await _seed_delivery(db, po, shipment_index=2, qty=100)
    calls = {"stock": 0}

    async def _stock(*_args, **_kwargs):
        calls["stock"] += 1
        return []

    import services.sales_deliveries as mod
    original = mod.handle_sales_delivery_created
    mod.handle_sales_delivery_created = _stock
    try:
        created = await create_sales_delivery(
            db,
            {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 50}]},
            _user(),
        )
    finally:
        mod.handle_sales_delivery_created = original

    assert created["id"]
    assert created["shipment_index"] == 3
    assert created["sj_number"] == derive_sj_number(po["po_number"], 3)
    assert created["sj_number"] != kept["sj_number"]
    rows = await db.sales_deliveries.find({"po_id": po["id"]}).to_list(10)
    assert len(rows) == 2
    assert calls["stock"] == 1
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1
    assert order["shipments"][0]["id"] == created["id"]
    assert order["shipments"][0]["number"] == created["sj_number"]
    assert order["updated_at"] != "touched-by-read"


@pytest.mark.anyio
async def test_sj_number_owned_by_other_po_is_not_false_conflict(db, storage):
    blocked = await _po(db, po_number="PO/2026/09/22/000031")
    other = await _po(db, po_number="SO/2026/09/22/000031")
    taken = derive_sj_number(blocked["po_number"], 1)
    assert taken == derive_sj_number(other["po_number"], 1)
    await db.sales_deliveries.insert_one({
        "id": str(uuid.uuid4()),
        "po_id": other["id"],
        "po_number": other["po_number"],
        "sj_number": taken,
        "shipment_index": 1,
        "is_cancelled": False,
        "line_items": [{"line_idx": 0, "qty": 10, "line_id": "x"}],
        "total_qty": 10,
    })
    created = await create_sales_delivery(
        db,
        {"po_id": blocked["id"], "line_items": [{"line_idx": 0, "qty": 40}]},
        _user(),
    )
    assert created["sj_number"] != taken
    assert created["sj_number"] == derive_sj_number(blocked["po_number"], created["shipment_index"])
    own = await db.sales_deliveries.count_documents({"po_id": blocked["id"]})
    assert own == 1


@pytest.mark.anyio
async def test_one_submit_and_replay_do_not_duplicate(db, storage):
    po = await _po(db, qty=500)
    payload = {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 500}]}
    first = await create_sales_delivery(db, payload, _user())
    with pytest.raises(HTTPException) as exc:
        await create_sales_delivery(db, payload, _user())
    assert exc.value.status_code == 400
    assert CONFLICT not in str(exc.value.detail)
    rows = await db.sales_deliveries.find({"po_id": po["id"]}).to_list(10)
    assert len(rows) == 1
    assert rows[0]["id"] == first["id"]
    assert rows[0]["sj_number"] == first["sj_number"]
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1


@pytest.mark.anyio
async def test_retry_after_commit_keeps_same_surat_jalan(db, storage):
    storage.fail = True
    po = await _po(db, qty=500)
    payload = {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 200}]}
    first = await create_sales_delivery(db, payload, _user())
    assert first.get("generated_sj_file_error") == "storage_failed"
    storage.fail = False
    second = await create_sales_delivery(db, payload, _user())
    assert second["id"] == first["id"]
    assert second["sj_number"] == first["sj_number"]
    assert await db.sales_deliveries.count_documents({"po_id": po["id"]}) == 1
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1
    assert len(order.get("surat_jalan_files") or []) == 1


@pytest.mark.anyio
async def test_post_insert_duplicate_key_does_not_create_another_surat_jalan(db, storage):
    po = await _po(db, qty=800)
    wrapped = _CachedDb(db)
    real = wrapped.orders.update_one
    state = {"n": 0}

    async def boom(*args, **kwargs):
        state["n"] += 1
        result = await real(*args, **kwargs)
        if state["n"] == 1:
            raise DuplicateKeyError("E11000 duplicate key after shipment commit")
        return result

    wrapped.orders.update_one = boom
    with pytest.raises(DuplicateKeyError):
        await create_sales_delivery(
            wrapped,
            {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 80}]},
            _user(),
        )
    rows = await db.sales_deliveries.find({"po_id": po["id"]}).to_list(10)
    assert len(rows) == 1
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1


@pytest.mark.anyio
async def test_concurrent_full_shipment_is_rejected_without_partial_write(db, storage):
    import asyncio

    po = await _po(db, qty=1000)
    payload = {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 1000}]}

    async def once():
        try:
            doc = await create_sales_delivery(db, payload, _user())
            return ("ok", doc)
        except HTTPException as exc:
            return ("err", exc.status_code, str(exc.detail))

    results = await asyncio.gather(once(), once())
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1
    assert len(errs) == 1
    assert errs[0][1] == 400
    assert CONFLICT not in errs[0][2]
    rows = await db.sales_deliveries.find({"po_id": po["id"]}).to_list(10)
    assert len(rows) == 1
    assert rows[0]["id"] == oks[0][1]["id"]
    assert rows[0]["sj_number"] == oks[0][1]["sj_number"]
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1
    assert order["shipments"][0]["total_qty"] == 1000


@pytest.mark.anyio
async def test_retry_after_real_conflict_does_not_overship(db, storage):
    po = await _po(db, qty=1000)
    wrapped = _CachedDb(db)
    real = wrapped.sales_deliveries.insert_one
    state = {"n": 0}

    async def race(doc, *args, **kwargs):
        if state["n"] == 0:
            state["n"] = 1
            winner = dict(doc)
            winner["id"] = str(uuid.uuid4())
            winner["delivery_id"] = winner["id"]
            winner["line_items"] = [{**doc["line_items"][0], "qty": 600}]
            winner["total_qty"] = 600
            winner.pop("_id", None)
            await real(winner)
            raise DuplicateKeyError("E11000 concurrent shipment_index")
        return await real(doc, *args, **kwargs)

    wrapped.sales_deliveries.insert_one = race
    with pytest.raises(HTTPException) as exc:
        await create_sales_delivery(
            wrapped,
            {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 700}]},
            _user(),
        )
    assert exc.value.status_code == 400
    assert CONFLICT not in str(exc.value.detail)
    rows = await db.sales_deliveries.find({"po_id": po["id"]}).to_list(10)
    assert len(rows) == 1
    assert rows[0]["total_qty"] == 600
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert order.get("shipments") in (None, [])


@pytest.mark.anyio
async def test_transient_duplicate_key_retries_same_identity(db, storage):
    po = await _po(db, qty=300)
    wrapped = _CachedDb(db)
    real = wrapped.sales_deliveries.insert_one
    state = {"n": 0}

    async def flaky(doc, *args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise DuplicateKeyError("E11000 transient shipment_index")
        return await real(doc, *args, **kwargs)

    wrapped.sales_deliveries.insert_one = flaky
    created = await create_sales_delivery(
        wrapped,
        {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 30}]},
        _user(),
    )
    assert state["n"] == 2
    assert created["shipment_index"] == 1
    assert created["sj_number"] == derive_sj_number(po["po_number"], 1)
    assert await db.sales_deliveries.count_documents({"po_id": po["id"]}) == 1


@pytest.mark.anyio
async def test_create_success_requires_orders_shipment_sync(db, storage):
    po = await _po(db, qty=500)
    created = await create_sales_delivery(
        db,
        {"po_id": po["id"], "delivery_date": "2026-10-24", "line_items": [{"line_idx": 0, "qty": 100}]},
        _user(),
    )
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1
    assert order["shipments"][0]["id"] == created["id"]
    assert order["shipments"][0]["sales_delivery_id"] == created["id"]
    assert order["shipments"][0]["delivery_date"] == "2026-10-24"
    assert order["shipments"][0]["number"] == created["sj_number"]


@pytest.mark.anyio
async def test_failed_po_update_does_not_report_success(db, storage):
    po = await _po(db, qty=500)
    wrapped = _CachedDb(db)

    async def no_match(filt, update, **kwargs):
        class _R:
            matched_count = 0
            modified_count = 0
        return _R()

    wrapped.orders.update_one = no_match

    with pytest.raises(HTTPException) as exc:
        await create_sales_delivery(
            wrapped,
            {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 50}]},
            _user(),
        )
    assert exc.value.status_code == 500
    detail = str(exc.value.detail).lower()
    assert "gagal menyinkronkan" in detail or "jangan anggap sukses" in detail
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert order.get("shipments") in (None, [])


@pytest.mark.anyio
async def test_idempotent_sync_when_shipment_already_present(db, storage):
    from services.sales_deliveries import ensure_order_shipment_synced

    po = await _po(db, qty=200)
    entry = {
        "id": "sd-pre",
        "number": "SJ-PRE",
        "shipment_index": 1,
        "delivery_date": "2026-10-26",
        "lines": [{"line_idx": 0, "qty": 20, "name": "Item"}],
        "total_qty": 20,
        "sales_delivery_id": "sd-pre",
    }
    await db.orders.update_one({"id": po["id"]}, {"$push": {"shipments": entry}})
    # Second ensure must not raise / must not duplicate.
    await ensure_order_shipment_synced(db, po["id"], entry, stamp={"delivery_note_number": "SJ-PRE"})
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1


@pytest.mark.anyio
async def test_retry_after_partial_shipment_sync_reuses_same_sj(db, storage):
    """sales_deliveries insert OK + orders.shipments fail → HTTP 500 → retry must not double SJ."""
    po = await _po(db, qty=500)
    wrapped = _CachedDb(db)
    payload = {
        "po_id": po["id"],
        "delivery_date": "2026-10-24",
        "line_items": [{"line_idx": 0, "qty": 120}],
    }

    async def no_match(filt, update, **kwargs):
        class _R:
            matched_count = 0
            modified_count = 0
        return _R()

    wrapped.orders.update_one = no_match
    with pytest.raises(HTTPException) as exc:
        await create_sales_delivery(wrapped, payload, _user())
    assert exc.value.status_code == 500

    orphan = await db.sales_deliveries.find({"po_id": po["id"]}, {"_id": 0}).to_list(10)
    assert len(orphan) == 1
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert order.get("shipments") in (None, [])

    # Real db retry: reuse fingerprint, repair shipment, no second document.
    second = await create_sales_delivery(db, payload, _user())
    assert second["id"] == orphan[0]["id"]
    assert second["sj_number"] == orphan[0]["sj_number"]
    assert await db.sales_deliveries.count_documents({"po_id": po["id"]}) == 1
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1
    assert order["shipments"][0]["id"] == orphan[0]["id"]
    assert order["shipments"][0]["delivery_date"] == "2026-10-24"


@pytest.mark.anyio
async def test_concurrent_retry_after_orphan_does_not_duplicate(db, storage):
    """Two retries against an orphan SD: lock serializes; still one SJ + one shipment."""
    import asyncio

    # Full remaining qty so a second complete create cannot silently open another SJ
    # (same-qty later partials remain allowed by reuse contract when remaining > 0).
    po = await _po(db, qty=250)
    sid = str(uuid.uuid4())
    line = {
        "line_idx": 0,
        "line_id": f"{po['id']}_line_0",
        "item_code": "SKU-1",
        "name": "Item A",
        "unit": "pcs",
        "ordered_qty": 250,
        "qty": 250,
        "notes": "",
    }
    await db.sales_deliveries.insert_one(
        {
            "id": sid,
            "delivery_id": sid,
            "po_id": po["id"],
            "po_number": po["po_number"],
            "sj_number": derive_sj_number(po["po_number"], 1),
            "shipment_index": 1,
            "delivery_date": "2026-10-25",
            "line_items": [line],
            "total_qty": 250,
            "status": "SELESAI",
            "is_cancelled": False,
            "created_at": "2026-10-25T01:00:00+00:00",
            "created_by": "crm@example.com",
            "created_by_name": "CRM",
        }
    )
    payload = {"po_id": po["id"], "line_items": [{"line_idx": 0, "qty": 250}]}

    async def once():
        try:
            doc = await create_sales_delivery(db, payload, _user())
            return ("ok", doc)
        except HTTPException as exc:
            return ("err", exc.status_code, str(exc.detail))

    results = await asyncio.gather(once(), once())
    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1
    assert oks[0][1]["id"] == sid
    assert len(errs) == 1
    assert errs[0][1] == 400
    assert await db.sales_deliveries.count_documents({"po_id": po["id"]}) == 1
    order = await db.orders.find_one({"id": po["id"]}, {"_id": 0})
    assert len(order.get("shipments") or []) == 1
    assert order["shipments"][0]["id"] == sid
