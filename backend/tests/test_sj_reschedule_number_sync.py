"""Targeted tests: reschedule Tanggal Kirim must sync unique Nomor Surat Jalan.

Covers:
A normal reschedule date segment
B collision with an existing SJ on the new date
C two different POs scheduled to the same date
D save without date change keeps SJ
E repeated same-date request is idempotent
F historical / delivered SJ is not renumbered
G PDF preview/download uses the new SJ + delivery date
H qty / line items / packing / loading / completion stay unchanged
+ concurrent reschedule of two SJs onto the same date
"""
import asyncio
import io
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pdfplumber
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.sales_deliveries import (
    allocate_sj_number_for_date,
    derive_base_sj_number,
    parse_sj_number_parts,
    sj_number_date_key,
    _format_sj_for_date,
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
    return store


def admin_user():
    return {
        "id": "usr-admin-sj-sync",
        "email": "admin@eternalpackaging.com",
        "name": "Admin Tester",
        "role": "admin",
    }


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    chunks = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def _seq_tag() -> str:
    return f"{int(uuid.uuid4().hex[:8], 16) % 900000 + 100000:06d}"


async def _ensure_sj_unique_index():
    try:
        await server.db.sales_deliveries.create_index("sj_number", unique=True)
    except Exception:
        pass


async def _create_po(po_number: str, *, qty=1000, extra=None) -> dict:
    po_id = str(uuid.uuid4())
    doc = {
        "id": po_id,
        "po_number": po_number,
        "customer": "PT SJ Sync",
        "outlet": "Pusat",
        "recipient": "Bpk Budi",
        "address": "Jl. Industri No 12",
        "sales": "Vira",
        "line_items": [{"name": "Cup 12oz", "quantity": qty, "unit": "pcs", "line_id": "L0"}],
        "total_quantity": qty,
        "item_count": 1,
        "current_stage": 12,
        "status_label": "Surat Jalan",
        "delivery_status": "Belum Terkirim",
        "cancelled": False,
        "packing_info": {"qty_packed": 800, "status": "Sudah"},
        "loading_info": {"qty_loaded": 400},
        "created_at": "2026-09-12T10:00:00Z",
    }
    if extra:
        doc.update(extra)
    await server.db.orders.insert_one(doc)
    doc.pop("_id", None)
    return doc


async def _create_delivery(client, po_id: str, *, delivery_date: str, qty: int = 500) -> dict:
    res = await client.post("/api/sales-deliveries", json={
        "po_id": po_id,
        "delivery_date": delivery_date,
        "line_items": [{"line_idx": 0, "qty": qty}],
    })
    assert res.status_code == 200, res.text
    return res.json()["delivery"]


async def _reschedule(client, po_id: str, *, new_date: str, reason="Telat truk", action_id=None):
    payload = {
        "reason": reason,
        "new_date": new_date,
        "action_id": action_id or str(uuid.uuid4()),
    }
    return await client.post(f"/api/orders/{po_id}/delivery-reschedule", json=payload)


def test_parse_and_format_canonical_ymd_and_dmy():
    ymd = parse_sj_number_parts("SJ/2026/09/12/000016")
    assert ymd["style"] == "ymd"
    assert ymd["date_key"] == "2026-09-12"
    assert ymd["seq"] == 16
    assert _format_sj_for_date(ymd, "2026-09-17") == "SJ/2026/09/17/000016"
    assert sj_number_date_key("SJ/2026/09/12/000016_2") == "2026-09-12"

    dmy = parse_sj_number_parts("SJ/12/09/26/016")
    assert dmy["style"] == "dmy"
    assert dmy["date_key"] == "2026-09-12"
    assert _format_sj_for_date(dmy, "2026-09-17") == "SJ/17/09/26/016"


@pytest.mark.anyio
async def test_a_normal_reschedule_rewrites_sj_date_segment(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po_num = f"PO/2026/09/12/{tag}016"
    po = await _create_po(po_num, extra={"scheduled_delivery_date": "2026-09-12"})
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            delivery = await _create_delivery(client, po["id"], delivery_date="2026-09-12")
            assert delivery["sj_number"] == f"SJ/2026/09/12/{tag}016"
            assert delivery["total_qty"] == 500

            res = await _reschedule(client, po["id"], new_date="2026-09-17")
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["order"]["scheduled_delivery_date"] == "2026-09-17"
            assert body["order"]["po_number"] == po_num

            fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assert fresh["delivery_date"] == "2026-09-17"
            assert sj_number_date_key(fresh["sj_number"]) == "2026-09-17"
            assert fresh["sj_number"] != delivery["sj_number"]
            assert fresh["sj_number"].startswith("SJ/2026/09/17/")
            assert fresh["total_qty"] == 500

            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order["po_number"] == po_num
            assert order["shipments"][0]["number"] == fresh["sj_number"]
            assert order["shipments"][0]["delivery_date"] == "2026-09-17"
            assert order["delivery_note_number"] == fresh["sj_number"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_b_collision_does_not_reuse_occupied_sequence(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    occupied = f"SJ/2026/09/17/{tag}016"
    po_b = await _create_po(f"PO/2026/09/17/{tag}016", extra={"scheduled_delivery_date": "2026-09-17"})
    po_a = await _create_po(f"PO/2026/09/12/{tag}016", extra={"scheduled_delivery_date": "2026-09-12"})
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            occ = await _create_delivery(client, po_b["id"], delivery_date="2026-09-17")
            assert occ["sj_number"] == occupied

            moving = await _create_delivery(client, po_a["id"], delivery_date="2026-09-12")
            assert moving["sj_number"] == f"SJ/2026/09/12/{tag}016"

            res = await _reschedule(client, po_a["id"], new_date="2026-09-17")
            assert res.status_code == 200, res.text
            moved = await server.db.sales_deliveries.find_one({"id": moving["id"]}, {"_id": 0})
            still = await server.db.sales_deliveries.find_one({"id": occ["id"]}, {"_id": 0})
            assert still["sj_number"] == occupied
            assert moved["sj_number"] != occupied
            assert sj_number_date_key(moved["sj_number"]) == "2026-09-17"
            assert moved["delivery_date"] == "2026-09-17"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_c_two_pos_same_target_date_get_unique_sj(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po_a = await _create_po(f"PO/2026/09/12/{tag}101", extra={"scheduled_delivery_date": "2026-09-12"})
    po_b = await _create_po(f"PO/2026/09/13/{tag}201", extra={"scheduled_delivery_date": "2026-09-13"})
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            da = await _create_delivery(client, po_a["id"], delivery_date="2026-09-12")
            db_ = await _create_delivery(client, po_b["id"], delivery_date="2026-09-13")
            ra = await _reschedule(client, po_a["id"], new_date="2026-09-17")
            rb = await _reschedule(client, po_b["id"], new_date="2026-09-17")
            assert ra.status_code == 200, ra.text
            assert rb.status_code == 200, rb.text
            fa = await server.db.sales_deliveries.find_one({"id": da["id"]}, {"_id": 0})
            fb = await server.db.sales_deliveries.find_one({"id": db_["id"]}, {"_id": 0})
            assert sj_number_date_key(fa["sj_number"]) == "2026-09-17"
            assert sj_number_date_key(fb["sj_number"]) == "2026-09-17"
            assert fa["sj_number"] != fb["sj_number"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_d_unchanged_date_keeps_sj_number(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po = await _create_po(f"PO/2026/09/12/{tag}016", extra={"scheduled_delivery_date": "2026-09-12"})
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            delivery = await _create_delivery(client, po["id"], delivery_date="2026-09-12")
            original = delivery["sj_number"]
            res = await _reschedule(client, po["id"], new_date="2026-09-12", reason="Tetap tanggal yang sama")
            assert res.status_code == 200, res.text
            fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assert fresh["sj_number"] == original
            assert fresh["delivery_date"] == "2026-09-12"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_e_repeated_same_request_does_not_reallocate(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po = await _create_po(f"PO/2026/09/12/{tag}016", extra={"scheduled_delivery_date": "2026-09-12"})
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            delivery = await _create_delivery(client, po["id"], delivery_date="2026-09-12")
            first = await _reschedule(client, po["id"], new_date="2026-09-17", action_id="act-same-17")
            assert first.status_code == 200, first.text
            after_first = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assigned = after_first["sj_number"]
            assert sj_number_date_key(assigned) == "2026-09-17"

            replay = await _reschedule(client, po["id"], new_date="2026-09-17", action_id="act-same-17")
            assert replay.status_code == 200, replay.text
            after_replay = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assert after_replay["sj_number"] == assigned

            again = await _reschedule(client, po["id"], new_date="2026-09-17", action_id="act-same-17-b")
            assert again.status_code == 200, again.text
            after_again = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assert after_again["sj_number"] == assigned
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_f_delivered_historical_sj_is_not_renumbered(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po = await _create_po(
        f"PO/2026/09/12/{tag}016",
        extra={
            "scheduled_delivery_date": "2026-09-12",
            "delivery_status": "Terkirim",
            "current_stage": 14,
        },
    )
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            delivery = await _create_delivery(client, po["id"], delivery_date="2026-09-12")
            original = delivery["sj_number"]
            await server.db.orders.update_one(
                {"id": po["id"]},
                {"$set": {"delivery_status": "Terkirim", "current_stage": 14}},
            )
            res = await _reschedule(client, po["id"], new_date="2026-09-17")
            assert res.status_code == 200, res.text
            fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assert fresh["sj_number"] == original
            assert sj_number_date_key(fresh["sj_number"]) == "2026-09-12"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_g_pdf_preview_and_download_use_new_sj_and_date(isolated_storage):
    store = isolated_storage
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po = await _create_po(f"PO/2026/09/12/{tag}016", extra={"scheduled_delivery_date": "2026-09-12"})
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            delivery = await _create_delivery(client, po["id"], delivery_date="2026-09-12")
            old_sj = delivery["sj_number"]
            res = await _reschedule(client, po["id"], new_date="2026-09-17")
            assert res.status_code == 200, res.text
            fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            new_sj = fresh["sj_number"]
            assert new_sj != old_sj

            live = await client.get(f"/api/sales-deliveries/{delivery['id']}/pdf")
            assert live.status_code == 200, live.text
            assert live.content.startswith(b"%PDF")
            live_text = _extract_pdf_text(live.content)
            assert new_sj in live_text
            assert old_sj not in live_text
            assert "17/09/26" in live_text

            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            gen = (order.get("surat_jalan_files") or [None])[0]
            assert gen and gen.get("sj_number") == new_sj
            viewed = await client.get(f"/api/orders/{po['id']}/surat-jalan/files/{gen['id']}")
            assert viewed.status_code == 200
            stored_text = _extract_pdf_text(viewed.content)
            assert new_sj in stored_text
            assert old_sj not in stored_text
            assert "17/09/26" in stored_text
            assert gen["storage_path"] in store
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_h_reschedule_does_not_mutate_qty_or_workflow(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po = await _create_po(
        f"PO/2026/09/12/{tag}016",
        extra={
            "scheduled_delivery_date": "2026-09-12",
            "packing_info": {"qty_packed": 800, "status": "Sudah"},
            "loading_info": {"qty_loaded": 400, "status": "Sebagian"},
        },
    )
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            delivery = await _create_delivery(client, po["id"], delivery_date="2026-09-12", qty=500)
            before = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            res = await _reschedule(client, po["id"], new_date="2026-09-17")
            assert res.status_code == 200, res.text
            after = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assert after["po_number"] == before["po_number"]
            assert after["line_items"] == before["line_items"]
            assert after["packing_info"]["qty_packed"] == 800
            assert after["loading_info"]["qty_loaded"] == 400
            assert after["delivery_status"] == "Belum Terkirim"
            assert fresh["total_qty"] == 500
            assert fresh["line_items"][0]["qty"] == 500
            assert after["shipments"][0]["total_qty"] == 500
            assert after["shipments"][0]["lines"][0]["qty"] == 500
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_concurrency_two_reschedules_same_date_unique(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po_a = await _create_po(f"PO/2026/09/10/{tag}116", extra={"scheduled_delivery_date": "2026-09-10"})
    po_b = await _create_po(f"PO/2026/09/11/{tag}216", extra={"scheduled_delivery_date": "2026-09-11"})
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            da = await _create_delivery(client, po_a["id"], delivery_date="2026-09-10")
            db_ = await _create_delivery(client, po_b["id"], delivery_date="2026-09-11")

            ra, rb = await asyncio.gather(
                _reschedule(client, po_a["id"], new_date="2026-09-17", action_id="conc-a"),
                _reschedule(client, po_b["id"], new_date="2026-09-17", action_id="conc-b"),
            )
            assert ra.status_code == 200, ra.text
            assert rb.status_code == 200, rb.text
            fa = await server.db.sales_deliveries.find_one({"id": da["id"]}, {"_id": 0})
            fb = await server.db.sales_deliveries.find_one({"id": db_["id"]}, {"_id": 0})
            assert sj_number_date_key(fa["sj_number"]) == "2026-09-17"
            assert sj_number_date_key(fb["sj_number"]) == "2026-09-17"
            assert fa["sj_number"] != fb["sj_number"]
            assert fa["delivery_date"] == "2026-09-17"
            assert fb["delivery_date"] == "2026-09-17"
            oa = await server.db.orders.find_one({"id": po_a["id"]}, {"_id": 0})
            ob = await server.db.orders.find_one({"id": po_b["id"]}, {"_id": 0})
            assert oa["scheduled_delivery_date"] == "2026-09-17"
            assert ob["scheduled_delivery_date"] == "2026-09-17"
            assert oa["shipments"][0]["number"] == fa["sj_number"]
            assert ob["shipments"][0]["number"] == fb["sj_number"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_f_delivery_proofs_none_matches_empty_list(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    po_none = await _create_po(
        f"PO/2026/09/12/{tag}801",
        extra={"scheduled_delivery_date": "2026-09-12", "delivery_proofs": None},
    )
    po_empty = await _create_po(
        f"PO/2026/09/12/{tag}802",
        extra={"scheduled_delivery_date": "2026-09-12", "delivery_proofs": []},
    )
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            d_none = await _create_delivery(client, po_none["id"], delivery_date="2026-09-12")
            d_empty = await _create_delivery(client, po_empty["id"], delivery_date="2026-09-12")
            r_none = await _reschedule(client, po_none["id"], new_date="2026-09-17")
            r_empty = await _reschedule(client, po_empty["id"], new_date="2026-09-17")
            assert r_none.status_code == 200, r_none.text
            assert r_empty.status_code == 200, r_empty.text
            fn = await server.db.sales_deliveries.find_one({"id": d_none["id"]}, {"_id": 0})
            fe = await server.db.sales_deliveries.find_one({"id": d_empty["id"]}, {"_id": 0})
            assert sj_number_date_key(fn["sj_number"]) == "2026-09-17"
            assert sj_number_date_key(fe["sj_number"]) == "2026-09-17"
            assert fn["sj_number"] != fe["sj_number"]
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_g_delivery_proofs_nonempty_stay_unchanged(isolated_storage):
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    proofs = [{"id": "proof-1", "name": "pod.jpg", "uploaded_at": "2026-09-12T10:00:00Z"}]
    po = await _create_po(
        f"PO/2026/09/12/{tag}803",
        extra={"scheduled_delivery_date": "2026-09-12", "delivery_proofs": proofs},
    )
    app.dependency_overrides[current_user] = admin_user
    try:
        async with _client() as client:
            delivery = await _create_delivery(client, po["id"], delivery_date="2026-09-12")
            res = await _reschedule(client, po["id"], new_date="2026-09-17")
            assert res.status_code == 200, res.text
            order = await server.db.orders.find_one({"id": po["id"]}, {"_id": 0})
            assert order["delivery_proofs"] == proofs
            fresh = await server.db.sales_deliveries.find_one({"id": delivery["id"]}, {"_id": 0})
            assert sj_number_date_key(fresh["sj_number"]) == "2026-09-17"
            assert order["po_number"] == f"PO/2026/09/12/{tag}803"
    finally:
        app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_allocator_skips_occupied_candidate():
    await _ensure_sj_unique_index()
    tag = _seq_tag()
    occupied = f"SJ/2026/09/17/{tag}016"
    await server.db.sales_deliveries.insert_one({
        "id": str(uuid.uuid4()),
        "sj_number": occupied,
        "is_cancelled": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    allocated = await allocate_sj_number_for_date(
        server.db, f"SJ/2026/09/12/{tag}016", "2026-09-17",
    )
    assert allocated != occupied
    assert allocated.startswith("SJ/2026/09/17/")
    assert derive_base_sj_number(f"PO/2026/09/12/{tag}016") == f"SJ/2026/09/12/{tag}016"
