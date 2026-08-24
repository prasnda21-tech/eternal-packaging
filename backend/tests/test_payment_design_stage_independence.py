"""Tests A/B/C — Approval Payment must not mutate Design workflow data."""
import os
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8000").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "eternal_packaging")

ADMIN_EMAIL = "admin@eternalpackaging.com"
ADMIN_PASS = "Eternal123!"

PNG_1x1 = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
)
PO_PREFIX = "TEST-PAYDES-"


@pytest.fixture(scope="module")
def db():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client[DB_NAME].orders.delete_many({"po_number": {"$regex": f"^{PO_PREFIX}"}})
    client.close()


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASS},
        timeout=15,
    )
    assert r.status_code == 200, r.text
    yield s
    s.close()


def _file(name):
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "data_url": PNG_1x1,
        "uploaded_by": ADMIN_EMAIL,
        "uploaded_by_role": "admin",
        "uploaded_by_name": "Admin",
        "uploaded_at": "2026-01-15T00:00:00+00:00",
    }


def _seed_po(db, **overrides):
    po_number = f"{PO_PREFIX}{uuid.uuid4().hex[:8].upper()}"
    now = "2026-01-15T00:00:00+00:00"
    order = {
        "id": str(uuid.uuid4()),
        "po_number": po_number,
        "customer_name": "TEST CUST",
        "customer": "TEST CUST",
        "current_stage": 2,
        "line_items": [{"id": str(uuid.uuid4()), "item_code": "X", "name": "T", "quantity": 1, "unit": "pcs"}],
        "total_quantity": 1,
        "timeline": [],
        "design_files": [],
        "payment_files": [_file("pay.png")],
        "sample_files": [],
        "design_status": "Waiting",
        "payment_status": "Waiting",
        "design_approved_by": None,
        "design_approved_by_name": None,
        "design_approved_at": None,
        "payment_approved_by": None,
        "payment_approved_by_name": None,
        "payment_approved_at": None,
        "design_bypassed": False,
        "payment_bypassed": False,
        "design_approval_history": [],
        "payment_approval_history": [],
        "cancelled": False,
        "hold": False,
        "created_at": now,
        "updated_at": now,
    }
    order.update(overrides)
    db.orders.insert_one(order.copy())
    return order["id"]


def _get(session, oid):
    r = session.get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["order"]


class TestPaymentApprovalDesignIndependence:
    def test_a_design_not_done_payment_approved(self, db, session):
        """TEST A: Design belum dikerjakan; Approval Payment tidak mengisi Design."""
        oid = _seed_po(db, design_files=[], current_stage=3)
        before = _get(session, oid)
        r = session.post(
            f"{BASE_URL}/api/orders/{oid}/approve-payment",
            json={"approved": True, "note": "test A"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        after = _get(session, oid)

        assert after["payment_status"] == "Approved"
        assert after["payment_approved_by"] == ADMIN_EMAIL
        assert after["payment_approved_at"]

        assert after["design_status"] == "Waiting"
        assert not after.get("design_approved_by")
        assert not after.get("design_approved_at")
        assert after["design_files"] == []

        design_tl = [t for t in after.get("timeline", []) if t.get("stage") in (1, 2)]
        assert not design_tl, "timeline must not get design-stage entries from payment approve"

        assert after["hold"] is True

    def test_b_design_done_preserved_after_payment_approve(self, db, session):
        """TEST B: Design sudah selesai; Approval Payment tidak overwrite Design."""
        design_at = "2026-01-10T08:00:00+00:00"
        oid = _seed_po(
            db,
            design_files=[_file("design.png")],
            design_status="Approved",
            design_approved_by=ADMIN_EMAIL,
            design_approved_by_name="Admin",
            design_approved_at=design_at,
            current_stage=3,
        )
        before = _get(session, oid)
        r = session.post(
            f"{BASE_URL}/api/orders/{oid}/approve-payment",
            json={"approved": True, "note": "test B"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        after = _get(session, oid)

        assert after["design_status"] == before["design_status"]
        assert after["design_approved_by"] == before["design_approved_by"]
        assert after["design_approved_by_name"] == before["design_approved_by_name"]
        assert after["design_approved_at"] == before["design_approved_at"]
        assert len(after["design_files"]) == len(before["design_files"])
        assert after["payment_status"] == "Approved"

    def test_c_penjadwalan_gate_after_both_approvals(self, db, session):
        """TEST C: Setelah keduanya approved, HOLD cleared — siap Penjadwalan."""
        oid = _seed_po(
            db,
            design_files=[_file("design.png")],
            design_status="Approved",
            design_approved_by=ADMIN_EMAIL,
            design_approved_at="2026-01-10T08:00:00+00:00",
            current_stage=3,
        )
        r = session.post(
            f"{BASE_URL}/api/orders/{oid}/approve-payment",
            json={"approved": True},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        after = _get(session, oid)
        assert after["hold"] is False
        assert after["design_status"] == "Approved"
        assert after["payment_status"] == "Approved"
        assert (after.get("current_stage") or 0) < 6
        assert not after.get("work_date")
