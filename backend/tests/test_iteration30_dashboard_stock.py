"""Live API: dashboard today cards + current stock save/export/RBAC.

Uses local Mongo + running backend. Does not hit Accurate production.
"""
import os
import uuid
from datetime import datetime, timezone, timedelta
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
HELPER_EMAIL = "helper@eternalpackaging.com"
WIB = timezone(timedelta(hours=7))


def _today():
    return datetime.now(timezone.utc).astimezone(WIB).date().isoformat()


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


@pytest.fixture()
def cleanup(db):
    ids = []
    mids = []
    yield ids, mids
    if ids:
        db.orders.delete_many({"id": {"$in": ids}})
    db.orders.delete_many({"id": {"$regex": "^TEST-T30-"}})
    if mids:
        db.master_items.delete_many({"id": {"$in": mids}})
        db.item_stock_manual.delete_many({"master_item_id": {"$in": mids}})
    db.master_items.delete_many({"accurate_item_id": {"$regex": "^T30-"}})
    db.item_stock_manual.delete_many({"accurate_item_id": {"$regex": "^T30-"}})


def test_dashboard_today_work_only(db, admin, cleanup):
    ids, _mids = cleanup
    today = _today()
    oid = f"TEST-T30-W-{uuid.uuid4().hex[:8]}"
    ids.append(oid)
    db.orders.insert_one({
        "id": oid, "po_number": oid, "customer": "Cust W", "outlet": "Out W",
        "cancelled": False, "current_stage": 8, "work_date": today,
        "created_at": "2026-01-01T00:00:00+00:00",
        "line_items": [{"name": "Cup", "quantity": 100, "accurate_item_id": "T30-1"}],
        "operator_splits": [{"operator_name": "Op A", "work_date": today, "quantity": 100, "qty_completed": 10}],
    })
    r = admin.get(f"{BASE_URL}/api/dashboard/today", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    pos = [x["po_number"] for x in body["work_today"]["preview"]]
    assert oid in pos or body["work_today"]["total"] >= 1
    ship_pos = [x["po_number"] for x in body["shipments_today"]["preview"]]
    assert oid not in ship_pos


def test_dashboard_today_partial_shipment(db, admin, cleanup):
    ids, _mids = cleanup
    oid = f"TEST-T30-S-{uuid.uuid4().hex[:8]}"
    ids.append(oid)
    now = datetime.now(timezone.utc).isoformat()
    db.orders.insert_one({
        "id": oid, "po_number": oid, "customer": "Cust S", "outlet": "Out S",
        "cancelled": False, "current_stage": 12,
        "delivery_status": "Sebagian Terkirim",
        "qty_delivered": 4000,
        "line_items": [
            {"name": "A", "quantity": 10000, "accurate_item_id": "T30-A"},
            {"name": "B", "quantity": 5000, "accurate_item_id": "T30-B"},
        ],
        "shipments": [{
            "number": f"SJ-{oid}",
            "created_at": now,
            "lines": [{"line_idx": 0, "qty": 4000, "accurate_item_id": "T30-A"}],
        }],
        "packing_info": {"qty_packed": 4000, "lines": [{"line_idx": 0, "packed_qty": 4000}]},
        "created_at": "2026-01-01T00:00:00+00:00",
        "work_date": "2020-01-01",
    })
    r = admin.get(f"{BASE_URL}/api/dashboard/today", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    row = next((x for x in body["shipments_today"]["preview"] if x["po_number"] == oid), None)
    assert row, body["shipments_today"]
    assert row["status"] != "Terkirim"
    assert row["partial"] is True
    assert row["qty"] == 4000
    work_pos = [x["po_number"] for x in body["work_today"]["preview"]]
    assert oid not in work_pos


def test_current_stock_formula_and_save(db, admin, cleanup):
    ids, mids = cleanup
    aid = f"T30-{uuid.uuid4().hex[:6]}"
    mid = f"TEST-T30-M-{uuid.uuid4().hex[:8]}"
    oid = f"TEST-T30-O-{uuid.uuid4().hex[:8]}"
    mids.append(mid)
    ids.append(oid)
    db.master_items.insert_one({
        "id": mid, "accurate_item_id": aid, "item_code": "T30-CUP",
        "item_name": "Cup T30", "unit": "pcs", "is_active": True,
    })
    db.orders.insert_one({
        "id": oid, "po_number": oid, "cancelled": False, "current_stage": 8,
        "line_items": [{
            "name": "Cup T30", "quantity": 10000, "accurate_item_id": aid,
            "master_item_id": mid, "production_process": "print",
            "unit_price": 999999, "amount": 999999,
        }],
        "operator_splits": [{"line_idx": 0, "quantity": 10000, "qty_completed": 3000}],
    })
    r = admin.patch(
        f"{BASE_URL}/api/current-stock/{mid}",
        json={"manual_current_qty": 2000, "manual_purchase_qty": 1000},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["qty_po"] == 10000
    assert item["manual_current_qty"] == 2000
    assert item["purchase_qty"] == 1000
    assert item["operator_print_qty"] == 3000
    assert item["stock_current"] == 4000
    assert item["updated_by"] == ADMIN_EMAIL
    assert item["updated_at"]

    listed = admin.get(f"{BASE_URL}/api/current-stock", params={"q": "T30-CUP"}, timeout=15)
    assert listed.status_code == 200
    found = next(x for x in listed.json()["items"] if x["accurate_item_id"] == aid)
    assert found["stock_current"] == 4000

    # 10000 - (2000 + 8000) - 3000 = -3000, never clamped
    r3 = admin.patch(
        f"{BASE_URL}/api/current-stock/{mid}",
        json={"manual_purchase_qty": 8000},
        timeout=15,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["item"]["stock_current"] == -3000

    neg = admin.get(f"{BASE_URL}/api/current-stock", params={"stock_filter": "negative", "q": "T30-CUP"}, timeout=15)
    assert neg.status_code == 200
    codes = [x["item_code"] for x in neg.json()["items"]]
    assert "T30-CUP" in codes

    exp = admin.get(
        f"{BASE_URL}/api/current-stock/export",
        params={"stock_filter": "negative", "q": "T30-CUP"},
        timeout=15,
    )
    assert exp.status_code == 200, exp.text
    assert "spreadsheetml" in exp.headers.get("content-type", "")
    from io import BytesIO
    from openpyxl import load_workbook
    wb = load_workbook(BytesIO(exp.content))
    ws = wb.active
    headers = [c.value for c in ws[1]]
    assert "Nilai PO" not in headers
    assert headers == [
        "Kode", "Nama Item", "Satuan", "Qty PO", "Qty Saat Ini",
        "Pembelian Barang", "Qty Cetak Operator", "Stock Saat Ini", "Terakhir Update",
    ]
    assert ws.max_row >= 2
    stock_col = headers.index("Stock Saat Ini") + 1
    qty_po_col = headers.index("Qty PO") + 1
    assert isinstance(ws.cell(2, qty_po_col).value, (int, float))
    assert ws.cell(2, stock_col).value < 0


def test_helper_forbidden_current_stock():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": HELPER_EMAIL, "password": PASS}, timeout=15)
    if r.status_code != 200:
        pytest.skip("helper login unavailable")
    g = s.get(f"{BASE_URL}/api/current-stock", timeout=15)
    assert g.status_code == 403
