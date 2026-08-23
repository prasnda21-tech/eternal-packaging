"""
Iteration 23 — Verify Approval Design vs Approval Payment independence.

Focus areas:
  1) approve_payment must NOT mutate design_status (and vice-versa)
  2) HOLD gate: partial approval => order.hold True; both approved => False
  3) Re-upload of design (payment) only resets its own approval field
  4) Bypass sets *_bypassed flag independently
  5) Timeline resolver correctness is a frontend concern — validated separately
     via UI test. This backend test proves the underlying fields are stable.
"""
import os
import time
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or \
    "https://print-ops-hub-12.preview.emergentagent.com"
MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"

ADMIN_EMAIL = "admin@eternalpackaging.com"
ADMIN_PASS = "Eternal123!"

# Tiny 1x1 PNG as data URL (transparent)
PNG_1x1 = ("data:image/png;base64,"
           "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=")

PO_PREFIX = "TEST-APPRV-"


# ---------- fixtures ----------

@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    # Cleanup any TEST-APPRV-* PO
    client[DB_NAME].orders.delete_many({"po_number": {"$regex": f"^{PO_PREFIX}"}})
    client.close()


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    yield s
    s.close()


def _seed_po(db, *, design_files=True, payment_files=True, current_stage=2,
             design_status="Waiting", payment_status="Waiting"):
    po_number = f"{PO_PREFIX}{uuid.uuid4().hex[:8].upper()}"
    now = "2026-01-15T00:00:00+00:00"
    file_entry = lambda name: {
        "id": str(uuid.uuid4()),
        "name": name,
        "data_url": PNG_1x1,
        "uploaded_by": ADMIN_EMAIL,
        "uploaded_by_role": "admin",
        "uploaded_by_name": "Admin",
        "uploaded_at": now,
    }
    order = {
        "id": str(uuid.uuid4()),
        "po_number": po_number,
        "customer_name": "TEST CUST",
        "customer_id": "TEST",
        "salesperson": "TEST SALES",
        "salesperson_id": "TEST",
        "urgent": False,
        "urgent_note": "",
        "current_stage": current_stage,
        "line_items": [
            {"id": str(uuid.uuid4()), "item_code": "X", "name": "T", "quantity": 1,
             "unit": "pcs", "color": "-", "notes": ""}
        ],
        "total_quantity": 1,
        "item_count": 1,
        "timeline": [],
        "design_files": [file_entry("d.png")] if design_files else [],
        "payment_files": [file_entry("p.png")] if payment_files else [],
        "sample_files": [],
        "delivery_photos": [],
        "design_status": design_status,
        "payment_status": payment_status,
        "design_approved_by": None, "design_approved_by_name": None, "design_approved_at": None,
        "payment_approved_by": None, "payment_approved_by_name": None, "payment_approved_at": None,
        "design_bypassed": False, "payment_bypassed": False,
        "design_approval_history": [],
        "payment_approval_history": [],
        "cancelled": False,
        "hold": False,
        "notes": "",
        "created_at": now,
        "updated_at": now,
    }
    db.orders.insert_one(order.copy())
    return order["id"], po_number


def _get(session, oid):
    r = session.get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["order"]


# ---------- Primary bug: Approve payment must not touch design ----------

class TestApprovalIndependence:

    def test_approve_payment_does_not_approve_design(self, db, session):
        oid, _ = _seed_po(db)
        r = session.post(f"{BASE_URL}/api/orders/{oid}/approve-payment",
                         json={"approved": True, "note": "test"}, timeout=15)
        assert r.status_code == 200, r.text
        o = _get(session, oid)
        assert o["payment_status"] == "Approved"
        assert o["design_status"] == "Waiting", \
            f"BUG: design_status changed to {o['design_status']} after payment approve"
        # current_stage advances to 4 (existing design decision), but hold gate must be True
        assert o["current_stage"] == 4
        assert o["hold"] is True, "hold must be True while design not approved"

    def test_approve_design_does_not_approve_payment(self, db, session):
        oid, _ = _seed_po(db)
        r = session.post(f"{BASE_URL}/api/orders/{oid}/approve-design",
                         json={"approved": True, "note": "test"}, timeout=15)
        assert r.status_code == 200, r.text
        o = _get(session, oid)
        assert o["design_status"] == "Approved"
        assert o["payment_status"] == "Waiting", \
            f"BUG: payment_status changed to {o['payment_status']} after design approve"
        assert o["hold"] is True, "hold must be True while payment not approved"

    def test_both_approvals_clear_hold(self, db, session):
        oid, _ = _seed_po(db)
        r1 = session.post(f"{BASE_URL}/api/orders/{oid}/approve-design",
                          json={"approved": True}, timeout=15)
        assert r1.status_code == 200
        r2 = session.post(f"{BASE_URL}/api/orders/{oid}/approve-payment",
                          json={"approved": True}, timeout=15)
        assert r2.status_code == 200
        o = _get(session, oid)
        assert o["design_status"] == "Approved"
        assert o["payment_status"] == "Approved"
        assert o["hold"] is False


# ---------- Re-upload independence ----------

class TestReuploadIndependence:

    def test_reupload_design_only_resets_design(self, db, session):
        oid, _ = _seed_po(db, design_status="Approved", payment_status="Approved",
                          current_stage=5)
        r = session.post(f"{BASE_URL}/api/orders/{oid}/attachment",
                         json={"name": "d2.png", "data_url": PNG_1x1, "kind": "design"},
                         timeout=15)
        assert r.status_code == 200, r.text
        o = r.json()["order"]
        assert o["design_status"] == "Waiting"
        assert o["payment_status"] == "Approved", "payment must remain Approved"
        history = o.get("design_approval_history", [])
        assert any(h.get("action") == "reset-on-new-upload" for h in history), \
            "expected reset-on-new-upload history entry"

    def test_reupload_payment_only_resets_payment(self, db, session):
        oid, _ = _seed_po(db, design_status="Approved", payment_status="Approved",
                          current_stage=5)
        r = session.post(f"{BASE_URL}/api/orders/{oid}/attachment",
                         json={"name": "p2.png", "data_url": PNG_1x1, "kind": "payment"},
                         timeout=15)
        assert r.status_code == 200, r.text
        o = r.json()["order"]
        assert o["payment_status"] == "Waiting"
        assert o["design_status"] == "Approved", "design must remain Approved"


# ---------- Approve-design gate: needs design files ----------

class TestApproveDesignRequiresFiles:

    def test_cannot_approve_design_without_files(self, db, session):
        oid, _ = _seed_po(db, design_files=False)
        r = session.post(f"{BASE_URL}/api/orders/{oid}/approve-design",
                         json={"approved": True}, timeout=15)
        assert r.status_code == 400, r.text


# ---------- Bypass independence ----------

class TestBypass:

    def test_bypass_design_does_not_affect_payment(self, db, session):
        oid, _ = _seed_po(db)
        r = session.post(f"{BASE_URL}/api/orders/{oid}/design/bypass",
                         json={"reason": "bypass test"}, timeout=15)
        # bypass may require permission; skip if not permitted
        if r.status_code == 403:
            pytest.skip("admin lacks design.bypass_approval — permission model, not our bug")
        assert r.status_code == 200, r.text
        o = _get(session, oid)
        assert o.get("design_bypassed") is True
        assert o.get("payment_bypassed") in (False, None)
        assert o["design_status"] != "Approved"  # bypass is not approve
        assert o["payment_status"] == "Waiting"
