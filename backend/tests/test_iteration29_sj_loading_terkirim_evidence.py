"""Iteration 29: SJ / Loading / Terkirim evidence — no checkbox, no generic Lanjut.

Loading save bumps stage to 13 from qty evidence.
Successful bukti-kirim upload applies delivery qty (partial PO stays partial).
Failed upload does not change delivery status.
"""
import os
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")
PASS = "Eternal123!"
ADMIN_EMAIL = "admin@eternalpackaging.com"

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xd9"
)


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return s


@pytest.fixture()
def cleanup(db):
    ids = []
    yield ids
    if ids:
        db.orders.delete_many({"id": {"$in": ids}})
    db.orders.delete_many({"id": {"$regex": "^TEST-T29-"}})


def _mk_partial_po(db, *, packed_a=4000, packed_b=None, qty_a=10000, qty_b=5000):
    oid = f"TEST-T29-{uuid.uuid4().hex[:8]}"
    lines = [
        {"name": "Item A", "quantity": qty_a, "unit": "pcs", "production_process": "print", "item_code": "A"},
    ]
    packing_lines = [{"line_idx": 0, "packed_qty": packed_a}]
    packed_total = packed_a
    if packed_b is not None:
        lines.append(
            {"name": "Item B", "quantity": qty_b, "unit": "pcs", "production_process": "print", "item_code": "B"}
        )
        packing_lines.append({"line_idx": 1, "packed_qty": packed_b})
        packed_total += packed_b
    doc = {
        "id": oid,
        "po_number": oid,
        "customer": "TEST T29",
        "line_items": lines,
        "total_quantity": qty_a + (qty_b if packed_b is not None else 0),
        "current_stage": 12,
        "cancelled": False,
        "priority": "normal",
        "design_status": "Approved",
        "payment_status": "Approved",
        "sample_approved": True,
        "packing_info": {"lines": packing_lines, "qty_packed": packed_total},
        "qty_packed": packed_total,
        "helper_prep": {"qty_prepared": packed_total},
        "operator_splits": [
            {"line_idx": i, "quantity": row["packed_qty"], "qty_completed": row["packed_qty"]}
            for i, row in enumerate(packing_lines)
        ],
        "delivery_note_number": f"SJ-{oid}",
        "delivery_note_created_by": ADMIN_EMAIL,
        "delivery_note_created_by_name": "Admin",
        "delivery_note_created_at": "2026-01-02T00:00:00+00:00",
        "shipments": [{
            "id": f"shp-{oid}",
            "number": f"SJ-{oid}",
            "source": "manual",
            "created_at": "2026-01-02T00:00:00+00:00",
            "created_by": ADMIN_EMAIL,
            "lines": [{"line_idx": row["line_idx"], "qty": row["packed_qty"]} for row in packing_lines],
        }],
        "loading_info": {"lines": []},
        "delivery_proofs": [],
        "timeline": [],
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    db.orders.insert_one(doc)
    return oid


def _order(admin, oid):
    r = admin.get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    return body.get("order") or body


def test_loading_save_auto_stage_partial(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/loading",
        json={"lines": [{"line_idx": 0, "qty": 2500}]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    assert o["current_stage"] == 13
    assert o["loading_qty_status"] == "partial"
    assert o.get("loading_completed") is False
    assert o.get("delivery_status") in (None, "Belum", "Belum Terkirim", "")
    rows = o.get("shipment_lines") or []
    loaded = next(x for x in rows if int(x["line_idx"]) == 0)
    assert loaded["loaded"] == 2500
    assert loaded["available_loading"] == 1500


def test_second_loading_completes_eligible_not_po(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    admin.post(f"{BASE_URL}/api/orders/{oid}/loading", json={"lines": [{"line_idx": 0, "qty": 2500}]}, timeout=15)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/loading",
        json={"lines": [{"line_idx": 0, "qty": 1500}]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    loaded = next(x for x in o["shipment_lines"] if int(x["line_idx"]) == 0)
    assert loaded["loaded"] == 4000
    assert loaded["available_loading"] == 0
    assert o["loading_qty_status"] == "completed"
    assert o["current_stage"] == 13
    assert o.get("terkirim_confirmed") is False


def test_multi_item_overall_not_fully_loaded(db, admin, cleanup):
    oid = _mk_partial_po(db, packed_a=4000, packed_b=2000)
    cleanup.append(oid)
    admin.post(f"{BASE_URL}/api/orders/{oid}/loading", json={"lines": [{"line_idx": 0, "qty": 4000}]}, timeout=15)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/loading",
        json={"lines": [{"line_idx": 1, "qty": 500}]},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    o = r.json()["order"]
    by = {int(x["line_idx"]): x for x in o["shipment_lines"]}
    assert by[0]["loaded"] == 4000
    assert by[1]["loaded"] == 500
    assert o["loading_qty_status"] == "partial"
    assert o.get("loading_completed") is False
    assert o["current_stage"] == 13


def test_proof_upload_marks_partial_delivery(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    admin.post(f"{BASE_URL}/api/orders/{oid}/loading", json={"lines": [{"line_idx": 0, "qty": 4000}]}, timeout=15)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/delivery-proof",
        files={"file": ("proof.jpg", TINY_JPEG, "image/jpeg")},
        data={"note": "sj1"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    assert "EMERGENT_LLM_KEY" not in (r.text or "")
    o = r.json()["order"]
    assert o["qty_delivered"] == 4000
    assert o["delivery_status"] == "Sebagian Terkirim"
    assert o.get("terkirim_confirmed") is False
    assert o["current_stage"] == 13
    proof = r.json()["proof"]
    assert proof.get("shipment_id")
    assert proof.get("qty_delivered_at_upload") == 4000
    fr = admin.get(f"{BASE_URL}/api/files/{proof['storage_path']}", timeout=15)
    assert fr.status_code == 200, fr.text
    assert len(fr.content) > 0


def test_failed_proof_does_not_mark_delivered(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    admin.post(f"{BASE_URL}/api/orders/{oid}/loading", json={"lines": [{"line_idx": 0, "qty": 4000}]}, timeout=15)
    before = _order(admin, oid)
    r = admin.post(
        f"{BASE_URL}/api/orders/{oid}/delivery-proof",
        files={"file": ("hello.txt", b"hello world", "text/plain")},
        timeout=15,
    )
    assert r.status_code == 400
    after = _order(admin, oid)
    assert after.get("qty_delivered") == before.get("qty_delivered")
    assert after.get("delivery_status") == before.get("delivery_status")
    assert (after.get("delivery_proofs") or []) == (before.get("delivery_proofs") or [])


def test_reload_keeps_loading_evidence(db, admin, cleanup):
    oid = _mk_partial_po(db)
    cleanup.append(oid)
    admin.post(f"{BASE_URL}/api/orders/{oid}/loading", json={"lines": [{"line_idx": 0, "qty": 2500}]}, timeout=15)
    o = _order(admin, oid)
    assert o["loading_qty_status"] == "partial"
    assert o["current_stage"] == 13
    loaded = next(x for x in o["shipment_lines"] if int(x["line_idx"]) == 0)
    assert loaded["loaded"] == 2500
