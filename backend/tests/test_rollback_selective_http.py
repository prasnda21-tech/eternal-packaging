"""HTTP integration: selective rollback against local preview backend + Mongo."""
import os
import uuid

import bcrypt
import pytest
import requests
from pymongo import MongoClient

FRONTEND_ORIGIN = "http://localhost:3002"
API = "http://localhost:8000/api"
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "eternal_packaging")
PREFIX = "PO-RBSEL-HTTP-"
HTTP_ADMIN_PASSWORD = "rbsel-http-local-only"


def _live_or_skip():
    try:
        r = requests.get(f"{API}/system/health", timeout=5)
    except requests.RequestException:
        pytest.skip("local backend not running")
    if r.status_code != 200:
        pytest.skip("local backend health not 200")


def _login(mongo):
    email = f"rbsel.admin.{uuid.uuid4().hex[:8]}@example.com"
    mongo.users.insert_one({
        "id": str(uuid.uuid4()),
        "email": email,
        "name": "RBSEL Admin",
        "role": "admin",
        "status": "active",
        "password_hash": bcrypt.hashpw(HTTP_ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode(),
    })
    s = requests.Session()
    r = s.post(
        f"{API}/auth/login",
        json={"email": email, "password": HTTP_ADMIN_PASSWORD},
        headers={"Origin": FRONTEND_ORIGIN},
        timeout=15,
    )
    if r.status_code != 200:
        mongo.users.delete_many({"email": email})
        pytest.skip(f"login admin not available: {r.status_code} {r.text[:200]}")
    s._rbsel_email = email
    return s


def _split(sid, line_idx, qty, name):
    return {
        "id": sid,
        "line_idx": line_idx,
        "operator_email": f"{name.lower()}@x.com",
        "operator_name": name,
        "quantity": 10000,
        "qty_completed": qty,
        "work_date": "2026-08-20",
        "updated_at": "2026-08-21T08:00:00+00:00",
        "progress_entries": [{
            "id": f"e-{sid}",
            "qty": qty,
            "created_at": "2026-08-21T08:00:00+00:00",
            "created_by_name": name,
            "cancelled": False,
        }],
    }


@pytest.fixture
def mongo():
    client = MongoClient(MONGO_URL)
    db = client[DB_NAME]
    yield db
    db.orders.delete_many({"po_number": {"$regex": f"^{PREFIX}"}})
    db.users.delete_many({"email": {"$regex": r"^rbsel\.(admin|noperm)\."}})
    client.close()


@pytest.fixture
def three_row_po(mongo):
    _live_or_skip()
    oid = str(uuid.uuid4())
    doc = {
        "id": oid,
        "po_number": PREFIX + uuid.uuid4().hex[:8].upper(),
        "customer": "RBSEL HTTP",
        "current_stage": 14,
        "status_label": "Terkirim",
        "cancelled": False,
        "design_status": "Approved",
        "payment_status": "Approved",
        "line_items": [
            {"id": "line-a", "name": "Cup A", "quantity": 10000, "accurate_item_id": "rbsel-a", "production_process": "print"},
            {"id": "line-b", "name": "Cup B", "quantity": 8000, "accurate_item_id": "rbsel-b", "production_process": "print"},
            {"id": "line-c", "name": "Cup C", "quantity": 5000, "accurate_item_id": "rbsel-c", "production_process": "print"},
        ],
        "operator_splits": [
            _split("split-a", 0, 3000, "Budi"),
            _split("split-b", 1, 4000, "Andi"),
            _split("split-c", 2, 2000, "Cici"),
        ],
        "helper_prep": {
            "qty_prepared": 100,
            "items": [
                {"line_idx": 0, "qty_prepared": 40},
                {"line_idx": 1, "qty_prepared": 35},
                {"line_idx": 2, "qty_prepared": 25},
            ],
        },
        "packing_info": {
            "qty_packed": 60,
            "lines": [
                {"line_idx": 0, "packed_qty": 10},
                {"line_idx": 1, "packed_qty": 20},
                {"line_idx": 2, "packed_qty": 30},
            ],
        },
        "timeline": [],
        "rollback_history": [],
    }
    mongo.orders.insert_one(dict(doc))
    return doc


def _packed(order):
    lines = ((order.get("packing_info") or {}).get("lines") or [])
    by_idx = {int(r.get("line_idx")): int(r.get("packed_qty") or 0) for r in lines}
    return by_idx.get(0), by_idx.get(1), by_idx.get(2)


def _qtys(order):
    splits = order.get("operator_splits") or []
    by_id = {s.get("id"): int(s.get("qty_completed") or 0) for s in splits}
    return by_id.get("split-a"), by_id.get("split-b"), by_id.get("split-c")


def test_http_case_a_packing_only_b(mongo, three_row_po):
    admin = _login(mongo)
    oid = three_row_po["id"]
    body = {"ids": ["split-b"], "to_stage": 11, "reason": "rollback B ke Packing"}
    r = admin.post(f"{API}/orders/{oid}/rollback", json=body, timeout=15)
    assert r.status_code == 200, r.text[:400]
    hist = (r.json().get("order") or {}).get("rollback_history") or []
    assert hist[-1].get("ids") == ["split-b"]
    assert hist[-1].get("to_stage") == 11
    order = admin.get(f"{API}/orders/{oid}", timeout=15).json()["order"]
    assert _qtys(order) == (3000, 4000, 2000)
    assert _packed(order) == (10, 0, 30)
    assert order["current_stage"] == 14
    db_doc = mongo.orders.find_one({"id": oid}, {"_id": 0})
    assert _qtys(db_doc) == (3000, 4000, 2000)
    assert _packed(db_doc) == (10, 0, 30)
    assert db_doc["helper_prep"]["items"][1]["qty_prepared"] == 35
    assert db_doc["current_stage"] == 14


def test_http_case_b_operator_a_and_c(mongo, three_row_po):
    admin = _login(mongo)
    oid = three_row_po["id"]
    r = admin.post(
        f"{API}/orders/{oid}/rollback",
        json={"ids": ["split-a", "split-c"], "to_stage": 8, "reason": "rollback A dan C ke Operator"},
        timeout=15,
    )
    assert r.status_code == 200, r.text[:400]
    order = admin.get(f"{API}/orders/{oid}", timeout=15).json()["order"]
    assert _qtys(order) == (0, 4000, 0)
    assert _packed(order) == (0, 20, 0)
    assert order["current_stage"] == 14
    db_doc = mongo.orders.find_one({"id": oid}, {"_id": 0, "operator_splits": 1, "packing_info": 1})
    assert _qtys(db_doc) == (0, 4000, 0)
    assert _packed(db_doc) == (0, 20, 0)


def test_http_case_c_all_ids(mongo, three_row_po):
    admin = _login(mongo)
    r = admin.post(
        f"{API}/orders/{three_row_po['id']}/rollback",
        json={"ids": ["split-a", "split-b", "split-c"], "to_stage": 8, "reason": "semua"},
        timeout=15,
    )
    assert r.status_code == 200, r.text[:400]
    order = admin.get(f"{API}/orders/{three_row_po['id']}", timeout=15).json()["order"]
    assert _qtys(order) == (0, 0, 0)


def test_http_case_d_empty_ids_400(mongo, three_row_po):
    admin = _login(mongo)
    r = admin.post(
        f"{API}/orders/{three_row_po['id']}/rollback",
        json={"ids": [], "to_stage": 8, "reason": "x"},
        timeout=15,
    )
    assert r.status_code == 400
    db_doc = mongo.orders.find_one({"id": three_row_po["id"]}, {"_id": 0, "operator_splits": 1})
    assert _qtys(db_doc) == (3000, 4000, 2000)


def test_http_invalid_id_400(mongo, three_row_po):
    admin = _login(mongo)
    r = admin.post(
        f"{API}/orders/{three_row_po['id']}/rollback",
        json={"ids": ["not-on-this-po"], "to_stage": 8, "reason": "x"},
        timeout=15,
    )
    assert r.status_code == 400
    db_doc = mongo.orders.find_one({"id": three_row_po["id"]}, {"_id": 0, "operator_splits": 1, "packing_info": 1})
    assert _qtys(db_doc) == (3000, 4000, 2000)
    assert _packed(db_doc) == (10, 20, 30)


def test_http_ids_and_to_stage_accepted(mongo, three_row_po):
    admin = _login(mongo)
    r = admin.post(
        f"{API}/orders/{three_row_po['id']}/rollback",
        json={"ids": ["split-b"], "to_stage": 11, "reason": "kontrak frontend"},
        timeout=15,
    )
    assert r.status_code == 200, r.text[:400]
    assert r.status_code != 400
    db_doc = mongo.orders.find_one({"id": three_row_po["id"]}, {"_id": 0, "current_stage": 1, "packing_info": 1})
    assert db_doc["current_stage"] == 14
    assert _packed(db_doc) == (10, 0, 30)


def test_http_unauthorized_403(mongo, three_row_po):
    email = f"rbsel.noperm.{uuid.uuid4().hex[:8]}@example.com"
    raw = f"rb-{uuid.uuid4().hex}"
    mongo.users.insert_one({
        "id": str(uuid.uuid4()),
        "email": email,
        "name": "RBSEL NoPerm",
        "role": "driver",
        "status": "active",
        "password_hash": bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode(),
    })
    try:
        s = requests.Session()
        login = s.post(
            f"{API}/auth/login",
            json={"email": email, "password": raw},
            headers={"Origin": FRONTEND_ORIGIN},
            timeout=15,
        )
        assert login.status_code == 200, login.text[:200]
        r = s.post(
            f"{API}/orders/{three_row_po['id']}/rollback",
            json={"ids": ["split-b"], "to_stage": 11, "reason": "x"},
            timeout=15,
        )
        assert r.status_code == 403
        db_doc = mongo.orders.find_one({"id": three_row_po["id"]}, {"_id": 0, "operator_splits": 1, "packing_info": 1})
        assert _qtys(db_doc) == (3000, 4000, 2000)
        assert _packed(db_doc) == (10, 20, 30)
    finally:
        mongo.users.delete_many({"email": email})


def test_http_case_h_unselected_snapshot(mongo, three_row_po):
    admin = _login(mongo)
    oid = three_row_po["id"]
    before = mongo.orders.find_one({"id": oid}, {"_id": 0})
    split_a = next(s for s in before["operator_splits"] if s["id"] == "split-a")
    split_c = next(s for s in before["operator_splits"] if s["id"] == "split-c")
    r = admin.post(
        f"{API}/orders/{oid}/rollback",
        json={"ids": ["split-b"], "to_stage": 11, "reason": "snapshot H"},
        timeout=15,
    )
    assert r.status_code == 200, r.text[:400]
    after = mongo.orders.find_one({"id": oid}, {"_id": 0})
    assert next(s for s in after["operator_splits"] if s["id"] == "split-a") == split_a
    assert next(s for s in after["operator_splits"] if s["id"] == "split-c") == split_c
    assert after["packing_info"]["lines"][0] == before["packing_info"]["lines"][0]
    assert after["packing_info"]["lines"][2] == before["packing_info"]["lines"][2]
    assert after["helper_prep"]["items"][0] == before["helper_prep"]["items"][0]
    assert after["helper_prep"]["items"][2] == before["helper_prep"]["items"][2]
    assert after["current_stage"] == 14
    assert after["packing_info"]["lines"][1]["packed_qty"] == 0


def test_http_selective_surat_jalan_target_400(mongo, three_row_po):
    admin = _login(mongo)
    r = admin.post(
        f"{API}/orders/{three_row_po['id']}/rollback",
        json={"ids": ["split-b"], "to_stage": 12, "reason": "sj not selective"},
        timeout=15,
    )
    assert r.status_code == 400
    db_doc = mongo.orders.find_one({"id": three_row_po["id"]}, {"_id": 0, "operator_splits": 1, "packing_info": 1})
    assert _qtys(db_doc) == (3000, 4000, 2000)
    assert _packed(db_doc) == (10, 20, 30)


def test_http_legacy_to_stage_still_works(mongo):
    _live_or_skip()
    admin = _login(mongo)
    oid = str(uuid.uuid4())
    mongo.orders.insert_one({
        "id": oid,
        "po_number": PREFIX + "LEGACY",
        "customer": "RBSEL HTTP",
        "current_stage": 5,
        "status_label": "Penjadwalan",
        "cancelled": False,
        "design_status": "Approved",
        "payment_status": "Approved",
        "line_items": [{"id": "line-a", "name": "Cup A", "quantity": 1000}],
        "operator_splits": [],
        "timeline": [],
        "rollback_history": [],
    })
    r = admin.post(
        f"{API}/orders/{oid}/rollback",
        json={"to_stage": 0, "reason": "legacy stage"},
        timeout=15,
    )
    assert r.status_code == 200, r.text[:400]
    order = admin.get(f"{API}/orders/{oid}", timeout=15).json()["order"]
    assert order["current_stage"] == 0
