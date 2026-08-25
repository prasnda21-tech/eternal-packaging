"""Regression: Surat Jalan Accurate preview must not call client.is_live().

Uses local Mongo + running backend. Does not hit Accurate production.
"""
import os
from pathlib import Path

import pytest
import requests
from pymongo import MongoClient

_BACKEND_DIR = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(_BACKEND_DIR / ".env")
except Exception:
    pass

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    env_path = _BACKEND_DIR.parent / "frontend" / ".env"
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except OSError:
        BASE_URL = "http://localhost:8000"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "eternal_packaging")
PASS = os.environ.get("TRIAL_DEFAULT_PASSWORD") or "Eternal123!"
ADMIN_EMAIL = "admin@eternalpackaging.com"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": PASS}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"backend login unavailable: {r.status_code}")
    return s


def test_accurate_surat_jalan_preview_http_200(db, admin):
    """GET /orders/{id}/surat-jalan/accurate-preview must not 500 on is_live property."""
    order = db.orders.find_one({"po_number": "PO-PREVIEW-SJ"}, {"_id": 0, "id": 1, "po_number": 1})
    if not order:
        order = db.orders.find_one({"cancelled": {"$ne": True}}, {"_id": 0, "id": 1, "po_number": 1})
    if not order:
        pytest.skip("no local order")
    r = admin.get(f"{BASE_URL}/api/orders/{order['id']}/surat-jalan/accurate-preview", timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "bool' object is not callable" not in r.text
    assert body.get("blocked") is False or body.get("mode") in ("mock", "live")
    assert isinstance(body.get("notes"), list)
    if body.get("mode") == "mock":
        assert body.get("ok") is True
        assert body.get("blocked") is False


def test_accurate_import_preview_http_200(admin):
    """Import PO still uses is_live as a property (no TypeError)."""
    r = admin.post(f"{BASE_URL}/api/accurate/import/preview", json={"so_number": "PO-2001"}, timeout=20)
    assert r.status_code == 200, r.text
    assert "bool' object is not callable" not in r.text
    body = r.json()
    assert "preview" in body


def test_master_items_list_http_200(admin):
    r = admin.get(f"{BASE_URL}/api/master-items", timeout=20)
    assert r.status_code == 200, r.text
    assert "bool' object is not callable" not in r.text


def test_purchase_receipts_list_http_200(admin):
    r = admin.get(f"{BASE_URL}/api/purchase-receipts", timeout=20)
    assert r.status_code == 200, r.text
    assert "bool' object is not callable" not in r.text
