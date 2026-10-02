"""Live Tracking pretty URL. Isolated database only. No production writes."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

import httpx
import pytest
from pymongo import MongoClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("DB_NAME", "eternal_packaging_live_tracking_url_test")
os.environ.setdefault("CORS_ORIGINS", "*")

ISOLATED_DB = "eternal_packaging_live_tracking_url_test"
assert ISOLATED_DB != "eternal_packaging"
assert "prod" not in ISOLATED_DB

import server
from services.customer_tracking import new_tracking_alias, new_tracking_token
from services.tracking_public_url import customer_slug, encode_segment

USER = {
    "id": "u-live-url",
    "name": "Staff Uji",
    "email": "live-url@example.com",
    "role": "admin",
    "status": "active",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def isolated(monkeypatch):
    # Bound server selection so a dead local Mongo fails the test instead of
    # waiting forever inside drop_database.
    sync = MongoClient(
        os.environ["MONGO_URL"],
        serverSelectionTimeoutMS=4000,
        connectTimeoutMS=4000,
        socketTimeoutMS=20000,
    )
    sync.drop_database(ISOLATED_DB)
    from motor.motor_asyncio import AsyncIOMotorClient

    motor = AsyncIOMotorClient(
        os.environ["MONGO_URL"],
        serverSelectionTimeoutMS=4000,
        connectTimeoutMS=4000,
        socketTimeoutMS=20000,
    )
    database = motor[ISOLATED_DB]
    assert database.name == ISOLATED_DB
    monkeypatch.setattr(server, "db", database)
    try:
        yield database
    finally:
        motor.close()
        sync.drop_database(ISOLATED_DB)
        sync.close()


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://test")


async def _auth_headers():
    await server.db.users.delete_many({"email": USER["email"]})
    await server.db.users.insert_one(dict(USER))
    token = server.token_for(USER)
    return {"Authorization": f"Bearer {token}"}


async def _insert(**extra):
    doc = {
        "id": extra.pop("id", str(uuid.uuid4())),
        "po_number": extra.pop("po_number", "PO-2026-10-02-000012"),
        "customer": extra.pop("customer", "Frutavor"),
        "current_stage": 0,
        "cancelled": extra.pop("cancelled", False),
        "line_items": extra.pop("line_items", [{"name": "Cup", "quantity": 1, "unit": "pcs"}]),
    }
    if "tracking_token" in extra:
        doc["tracking_token"] = extra.pop("tracking_token")
        doc["tracking_token_created_at"] = extra.pop(
            "tracking_token_created_at", "2020-01-01T00:00:00+00:00"
        )
    doc.update(extra)
    await server.db.orders.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


def _alias_of(body, long_token):
    alias = body["code"]
    assert len(alias) == 22
    assert "=" not in alias
    assert alias != long_token[:22]
    assert body["token"] == long_token
    assert body["legacy_path"] == f"/tracking/{long_token}"
    assert body["public_path"].endswith(f"/{alias}")
    assert body["path"] == body["public_path"]
    assert long_token not in body["public_path"]
    return alias


@pytest.mark.anyio
async def test_share_url_uses_customer_po_and_existing_code(isolated):
    code = new_tracking_token()
    order = await _insert(
        customer="Frutavor",
        po_number="PO-2026-10-02-000012",
        tracking_token=code,
        order_date="2026-10-02",
        deadline="2026-10-02",
    )
    headers = await _auth_headers()
    before = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
    async with _client() as client:
        shared = await client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
        opened = await client.get(f"/api/public/tracking/{code}")
    assert shared.status_code == 200
    body = shared.json()
    assert body["token"] == code
    alias = _alias_of(body, code)
    assert body["po_label"] == "single"
    assert body["public_path"] == f"/t/frutavor/PO-2026-10-02-000012/{alias}"
    assert "02/10/2026" not in body["public_path"]
    assert opened.status_code == 200
    payload = opened.json()
    assert payload["customer_name"] == "Frutavor"
    assert payload["order_number"] == "PO-2026-10-02-000012"
    assert payload["summary"]["order_date"] == "2026-10-02"
    assert "tracking_token" not in payload
    assert "tracking_public_code" not in payload
    after = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
    assert after["tracking_token"] == before["tracking_token"] == code
    assert after["tracking_public_code"] == alias
    assert after["customer"] == "Frutavor"
    assert after["po_number"] == "PO-2026-10-02-000012"


@pytest.mark.anyio
async def test_punctuation_unicode_and_slash_po_stay_one_segment(isolated):
    code = new_tracking_token()
    customer = "Café Niño & Co."
    order = await _insert(
        customer=customer,
        po_number="PO/2026/10/02/000012",
        tracking_token=code,
    )
    headers = await _auth_headers()
    async with _client() as client:
        shared = await client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
        opened = await client.get(f"/api/public/tracking/{code}")
    body = shared.json()
    slug = customer_slug(customer)
    assert slug == "café-niño-co"
    alias = _alias_of(body, code)
    assert body["public_path"] == f"/t/{encode_segment(slug)}/PO-2026-10-02-000012/{alias}"
    assert "/" not in body["public_path"].split("/")[3]
    assert opened.json()["customer_name"] == customer
    assert opened.json()["order_number"] == "PO/2026/10/02/000012"


@pytest.mark.anyio
async def test_slug_does_not_select_another_customer(isolated):
    code_a = new_tracking_token()
    code_b = new_tracking_token()
    await _insert(customer="Café Niño & Co.", po_number="PO/A", tracking_token=code_a, id="ord-a")
    await _insert(customer="Frutavor", po_number="PO-2026-10-02-000012", tracking_token=code_b, id="ord-b")
    async with _client() as client:
        opened = await client.get(f"/api/public/tracking/{code_a}")
        other = await client.get(f"/api/public/tracking/{code_b}")
    assert opened.json()["customer_name"] == "Café Niño & Co."
    assert opened.json()["order_number"] == "PO/A"
    assert other.json()["customer_name"] == "Frutavor"
    assert other.json()["customer_name"] != opened.json()["customer_name"]


@pytest.mark.anyio
async def test_invalid_and_cancelled_are_rejected_and_old_token_has_no_expiry(isolated):
    code = new_tracking_token()
    order = await _insert(tracking_token=code, tracking_token_created_at="2020-01-01T00:00:00+00:00")
    cancelled = await _insert(customer="Ditutup", tracking_token=new_tracking_token(), cancelled=True)
    async with _client() as client:
        still = await client.get(f"/api/public/tracking/{code}")
        missing = await client.get("/api/public/tracking/tidak-ada-token-xxxx")
        short = await client.get("/api/public/tracking/short")
        revoked = await client.get(f"/api/public/tracking/{cancelled['tracking_token']}")
    assert still.status_code == 200
    assert still.json()["order_number"] == order["po_number"]
    assert missing.status_code == short.status_code == revoked.status_code == 404


@pytest.mark.anyio
async def test_rename_keeps_the_code_and_get_stays_read_only(isolated):
    code = new_tracking_token()
    order = await _insert(customer="Frutavor", po_number="PO-2026-10-02-000012", tracking_token=code)
    headers = await _auth_headers()
    async with _client() as client:
        before_get = await client.get(f"/api/public/tracking/{code}")
    stored = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
    await server.db.orders.update_one({"id": order["id"]}, {"$set": {"customer": "Nama Baru", "po_number": "PO/2026/99"}})
    async with _client() as client:
        after_get = await client.get(f"/api/public/tracking/{code}")
        shared = await client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
    assert before_get.json()["customer_name"] == "Frutavor"
    assert after_get.json()["customer_name"] == "Nama Baru"
    assert after_get.json()["order_number"] == "PO/2026/99"
    body = shared.json()
    alias = _alias_of(body, code)
    assert body["public_path"] == f"/t/nama-baru/PO-2026-99/{alias}"
    assert not str(stored.get("tracking_public_code") or "").strip()
    fresh = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0, "tracking_token": 1, "tracking_token_created_at": 1, "tracking_public_code": 1})
    assert fresh["tracking_token"] == stored["tracking_token"] == code
    assert fresh["tracking_token_created_at"] == stored["tracking_token_created_at"]
    assert fresh["tracking_public_code"] == alias


@pytest.mark.anyio
async def test_get_does_not_create_a_code(isolated):
    order = await _insert()
    assert "tracking_token" not in order
    async with _client() as client:
        res = await client.get("/api/public/tracking/tidak-ada-kode-sama-sekali")
        res_order = await client.get(f"/api/public/tracking/{new_tracking_token()}")
    assert res.status_code == res_order.status_code == 404
    stored = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
    assert not str(stored.get("tracking_token") or "").strip()
    assert not str(stored.get("tracking_public_code") or "").strip()


@pytest.mark.anyio
async def test_post_creates_once_and_second_share_reuses_it(isolated):
    order = await _insert(customer="PT. Maju Jaya", po_number="PO 12/A")
    headers = await _auth_headers()
    async with _client() as client:
        first = await client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
        second = await client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json()["token"] == second.json()["token"]
    assert first.json()["code"] == second.json()["code"]
    long_token = first.json()["token"]
    alias = _alias_of(first.json(), long_token)
    assert first.json()["public_path"] == f"/t/pt-maju-jaya/PO%2012-A/{alias}"
    assert second.json()["public_path"] == first.json()["public_path"]
    stored = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0, "tracking_token": 1, "tracking_public_code": 1})
    assert stored["tracking_token"] == long_token
    assert stored["tracking_public_code"] == alias


@pytest.mark.anyio
async def test_missing_and_multiple_po_are_not_silently_chosen(isolated):
    headers = await _auth_headers()
    missing = await _insert(
        customer="Frutavor",
        po_number="",
        sj_number="SJ/2026/10/02/0001",
        tracking_token=new_tracking_token(),
    )
    multiple = await _insert(
        customer="Frutavor",
        po_number="PO-2026-10-02-000012",
        line_items=[
            {"name": "Cup", "quantity": 1, "po_number": "PO-LAIN-000099"},
        ],
        tracking_token=new_tracking_token(),
    )
    async with _client() as client:
        missing_share = await client.post(f"/api/orders/{missing['id']}/tracking-link", headers=headers)
        many_share = await client.post(f"/api/orders/{multiple['id']}/tracking-link", headers=headers)
        missing_open = await client.get(f"/api/public/tracking/{missing['tracking_token']}")
    assert missing_share.json()["po_label"] == "missing"
    assert "/tanpa-po/" in missing_share.json()["public_path"]
    assert "SJ" not in missing_share.json()["public_path"]
    assert missing_open.json()["order_number"] == ""
    assert many_share.json()["po_label"] == "multiple"
    many_path = many_share.json()["public_path"]
    assert "/beberapa-po/" in many_path
    assert "PO-2026-10-02-000012" not in many_path
    assert "PO-LAIN-000099" not in many_path
    assert many_path.endswith(f"/{many_share.json()['code']}")
    assert len(many_share.json()["code"]) == 22


@pytest.mark.anyio
async def test_alias_opens_same_order_and_old_token_still_works(isolated):
    long_token = new_tracking_token()
    order = await _insert(
        customer="Frutavor",
        po_number="PO-2026-10-02-000012",
        tracking_token=long_token,
        deadline="2026-10-02",
    )
    headers = await _auth_headers()
    async with _client() as client:
        shared = await client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
        body = shared.json()
        alias = _alias_of(body, long_token)
        by_alias = await client.get(f"/api/public/tracking/{alias}")
        by_token = await client.get(f"/api/public/tracking/{long_token}")
        again = await client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
    assert by_alias.status_code == by_token.status_code == 200
    assert by_alias.json()["customer_name"] == by_token.json()["customer_name"] == "Frutavor"
    assert by_alias.json()["order_number"] == by_token.json()["order_number"] == "PO-2026-10-02-000012"
    assert by_alias.json()["summary"]["deadline"] == "2026-10-02"
    assert again.json()["code"] == alias
    assert again.json()["token"] == long_token


@pytest.mark.anyio
async def test_unknown_alias_and_cancelled_order_stay_closed(isolated):
    long_token = new_tracking_token()
    alias = new_tracking_alias()
    await _insert(
        customer="Ditutup",
        tracking_token=long_token,
        tracking_public_code=alias,
        cancelled=True,
    )
    unknown = new_tracking_alias()
    async with _client() as client:
        by_alias = await client.get(f"/api/public/tracking/{alias}")
        by_token = await client.get(f"/api/public/tracking/{long_token}")
        missing = await client.get(f"/api/public/tracking/{unknown}")
    assert by_alias.status_code == by_token.status_code == missing.status_code == 404


@pytest.mark.anyio
async def test_concurrent_shares_store_one_alias(isolated):
    order = await _insert(customer="Frutavor", po_number="PO-2026-10-02-000012")
    headers = await _auth_headers()

    async with _client() as client:
        results = await asyncio.gather(*[
            client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
            for _ in range(8)
        ])
    assert [res.status_code for res in results] == [200] * 8
    codes = {res.json()["code"] for res in results}
    tokens = {res.json()["token"] for res in results}
    assert len(codes) == 1
    assert len(tokens) == 1
    alias = codes.pop()
    long_token = tokens.pop()
    assert len(alias) == 22
    assert alias != long_token[:22]
    stored = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0})
    assert stored["tracking_public_code"] == alias
    assert stored["tracking_token"] == long_token
    assert await server.db.orders.count_documents({"tracking_public_code": alias}) == 1


@pytest.mark.anyio
async def test_alias_collision_stores_a_different_code(isolated, monkeypatch):
    taken = new_tracking_alias()
    await _insert(id="other-order", tracking_token=new_tracking_token(), tracking_public_code=taken)
    fresh = new_tracking_alias()
    calls = {"n": 0}

    def sequenced():
        calls["n"] += 1
        return taken if calls["n"] == 1 else fresh

    monkeypatch.setattr("services.customer_tracking.new_tracking_alias", sequenced)
    order = await _insert(customer="Frutavor", po_number="PO-1")
    headers = await _auth_headers()
    async with _client() as client:
        shared = await client.post(f"/api/orders/{order['id']}/tracking-link", headers=headers)
    assert shared.status_code == 200
    assert shared.json()["code"] == fresh
    assert shared.json()["code"] != taken
    stored = await server.db.orders.find_one({"id": order["id"]}, {"_id": 0, "tracking_public_code": 1})
    assert stored["tracking_public_code"] == fresh
