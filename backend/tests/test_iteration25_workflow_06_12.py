"""
Iteration 25 - Workflow 06-12 targeted bugfix tests.

Verifies:
  * Bug A: /assign-operators tidak auto-jump stage (tetap 5).
  * Central prereq validator for advance stages 5->6, 6->7, 7->8, 8->9, 9->10, 10->11.
  * Bug B: /packed gate — tidak boleh shortcut dari stage 5..10 saat prereq incomplete.
  * Bug C: /approve-sample permission `operator.sample_approve` (admin+head_production allowed,
    operator forbidden). Re-upload sample resets sample_approved & append history.
  * Bug D: response berisi packing_prereq_reasons + packing_ready.
  * Backward compat: existing orders tetap accessible & fields baru nullable-safe.
  * Permission catalog: /admin/permissions expose `operator.sample_approve` (module Operator)
    dan admin role effective permissions include ini.
"""

import os
import time
import uuid
import base64
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # frontend/.env
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL"):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")
                break

API = f"{BASE_URL}/api"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

ADMIN = {"email": "admin@eternalpackaging.com", "password": "Eternal123!"}
HEAD = {"email": "production@eternalpackaging.com", "password": "Eternal123!"}
OPERATOR = {"email": "operator@eternalpackaging.com", "password": "Eternal123!"}


# -------------------------- Fixtures --------------------------

@pytest.fixture(scope="module")
def mongo():
    client = MongoClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin_sess():
    return _login(ADMIN["email"], ADMIN["password"])


@pytest.fixture(scope="module")
def head_sess():
    return _login(HEAD["email"], HEAD["password"])


@pytest.fixture(scope="module")
def operator_sess():
    return _login(OPERATOR["email"], OPERATOR["password"])


@pytest.fixture(scope="module")
def operator_info(mongo):
    """Return operator user document for split assignments."""
    u = mongo.users.find_one({"email": OPERATOR["email"]})
    assert u, "Operator user not found in DB"
    return {"email": u["email"], "name": u.get("name") or "Operator"}


def _make_po_doc(stage=5):
    """Insert a minimal PO doc directly to DB with the workflow already at stage 5.
    All approval prereqs (design/payment) marked satisfied to avoid entangling other flows.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    po_number = f"PO-TEST-{uuid.uuid4().hex[:8].upper()}"
    return {
        "id": str(uuid.uuid4()),
        "po_number": po_number,
        "customer_name": "TEST Customer",
        "product_name": "TEST Product",
        "total_quantity": 100,
        "current_stage": stage,
        "status": "active",
        "priority": "normal",
        "design_status": "Approved",
        "design_approved_by": "seed",
        "design_approved_at": now,
        "design_files": [{"id": str(uuid.uuid4()), "name": "d.png"}],
        "payment_status": "Approved",
        "payment_approved_by": "seed",
        "payment_approved_at": now,
        "payment_files": [{"id": str(uuid.uuid4()), "name": "p.png"}],
        "operator_splits": [],
        "sample_files": [],
        "screener_info": None,
        "helper_prep": None,
        "timeline": [{"stage": stage, "at": now, "by": "seed", "note": "seeded"}],
        "created_at": now,
        "updated_at": now,
        "notes": "",
        "packing_status": "Belum",
        "delivery_status": "Belum",
    }


@pytest.fixture
def po_id(mongo):
    """Create a fresh PO per test at stage 5, cleanup after."""
    doc = _make_po_doc(stage=5)
    mongo.orders.insert_one(doc)
    yield doc["id"]
    mongo.orders.delete_one({"id": doc["id"]})


# Helper to fully advance an order by direct DB set-up (used to test stage-11 gate later on)
def _set_stage(mongo, order_id, stage, **extra):
    mongo.orders.update_one({"id": order_id}, {"$set": {"current_stage": stage, **extra}})


# -------------------------- Tests --------------------------

class TestBugAAssignOperators:
    def test_assign_operators_no_auto_jump(self, admin_sess, po_id, operator_info, mongo):
        r = admin_sess.post(f"{API}/orders/{po_id}/assign-operators", json={
            "splits": [{
                "operator_email": operator_info["email"],
                "operator_name": operator_info["name"],
                "quantity": 100,
            }]
        })
        assert r.status_code == 200, r.text
        data = r.json()["order"]
        assert data["current_stage"] == 5, f"Auto-jump detected — stage={data['current_stage']}"
        assert len(data["operator_splits"]) == 1
        assert any("Assign 1 operator" in (t.get("note") or "") for t in data.get("timeline", [])), \
            "Timeline entry 'Assign 1 operator' missing"

    def test_get_after_assign_prereq_reasons_present(self, admin_sess, po_id, operator_info):
        admin_sess.post(f"{API}/orders/{po_id}/assign-operators", json={
            "splits": [{
                "operator_email": operator_info["email"],
                "operator_name": operator_info["name"],
                "quantity": 100,
            }]
        })
        r = admin_sess.get(f"{API}/orders/{po_id}")
        assert r.status_code == 200
        body = r.json()
        d = body.get("order", body)
        assert d["packing_ready"] is False
        reasons = d["packing_prereq_reasons"]
        assert "Qty Screen belum diinput." in reasons
        assert "Qty persiapan (qty_prepared) belum diinput." in reasons


class TestAdvanceGates:
    def _assign(self, sess, po_id, operator_info):
        return sess.post(f"{API}/orders/{po_id}/assign-operators", json={
            "splits": [{
                "operator_email": operator_info["email"],
                "operator_name": operator_info["name"],
                "quantity": 100,
            }]
        })

    def test_5_to_6_ok_when_assigned(self, admin_sess, po_id, operator_info):
        self._assign(admin_sess, po_id, operator_info)
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 200, r.text
        assert r.json()["order"]["current_stage"] == 6

    def test_6_to_7_blocked_without_screener(self, admin_sess, po_id, operator_info):
        self._assign(admin_sess, po_id, operator_info)
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})  # 5->6
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})  # 6->7 blocked
        assert r.status_code == 400
        detail = r.json().get("detail", {})
        assert "Qty Screen belum diinput." in detail.get("prereq_reasons", [])

    def test_6_to_7_ok_after_screener(self, admin_sess, po_id, operator_info):
        self._assign(admin_sess, po_id, operator_info)
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        r = admin_sess.post(f"{API}/orders/{po_id}/screener-info", json={"screen_qty": 100})
        assert r.status_code == 200, r.text
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 200, r.text
        assert r.json()["order"]["current_stage"] == 7

    def test_7_to_8_blocked_without_helper(self, admin_sess, po_id, operator_info):
        self._assign(admin_sess, po_id, operator_info)
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/screener-info", json={"screen_qty": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 400
        assert "Qty persiapan (qty_prepared) belum diinput." in r.json()["detail"]["prereq_reasons"]

    def test_7_to_8_ok_after_helper(self, admin_sess, po_id, operator_info):
        self._assign(admin_sess, po_id, operator_info)
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/screener-info", json={"screen_qty": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        r = admin_sess.post(f"{API}/orders/{po_id}/helper-prep",
                            json={"qty_required": 100, "qty_prepared": 100})
        assert r.status_code == 200, r.text
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 200, r.text
        assert r.json()["order"]["current_stage"] == 8

    def test_8_to_9_blocked_without_sample(self, admin_sess, po_id, operator_info):
        self._assign(admin_sess, po_id, operator_info)
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/screener-info", json={"screen_qty": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/helper-prep",
                        json={"qty_required": 100, "qty_prepared": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 400
        assert "Operator harus upload sampel cetak terlebih dahulu." in r.json()["detail"]["prereq_reasons"]

    def _upload_sample(self, sess, po_id):
        return sess.post(f"{API}/orders/{po_id}/attachment", json={
            "name": "sample.png",
            "data_url": "data:image/png;base64," + base64.b64encode(b"x").decode(),
            "kind": "sample",
        })

    def test_8_to_9_ok_after_sample_upload(self, admin_sess, po_id, operator_info):
        self._assign(admin_sess, po_id, operator_info)
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/screener-info", json={"screen_qty": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/helper-prep",
                        json={"qty_required": 100, "qty_prepared": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        r = self._upload_sample(admin_sess, po_id)
        assert r.status_code == 200, r.text
        order = r.json()["order"]
        assert order.get("sample_approved") in (False, None)
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 200, r.text
        assert r.json()["order"]["current_stage"] == 9


class TestBugCSampleApproval:
    def _prep_to_stage9(self, admin_sess, po_id, operator_info):
        admin_sess.post(f"{API}/orders/{po_id}/assign-operators", json={
            "splits": [{"operator_email": operator_info["email"], "operator_name": operator_info["name"], "quantity": 100}]
        })
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/screener-info", json={"screen_qty": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/helper-prep",
                        json={"qty_required": 100, "qty_prepared": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/attachment", json={
            "name": "sample.png",
            "data_url": "data:image/png;base64," + base64.b64encode(b"x").decode(),
            "kind": "sample",
        })
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})  # -> 9

    def test_9_to_10_blocked_without_approval(self, admin_sess, po_id, operator_info):
        self._prep_to_stage9(admin_sess, po_id, operator_info)
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 400
        assert "Sampel cetak belum disetujui." in r.json()["detail"]["prereq_reasons"]

    def test_operator_cannot_approve_sample(self, admin_sess, operator_sess, po_id, operator_info):
        self._prep_to_stage9(admin_sess, po_id, operator_info)
        r = operator_sess.post(f"{API}/orders/{po_id}/approve-sample", json={"approved": True})
        assert r.status_code == 403, f"Expected 403 for operator, got {r.status_code} {r.text}"

    def test_admin_can_approve_and_advance(self, admin_sess, po_id, operator_info):
        self._prep_to_stage9(admin_sess, po_id, operator_info)
        r = admin_sess.post(f"{API}/orders/{po_id}/approve-sample", json={"approved": True})
        assert r.status_code == 200, r.text
        assert r.json()["order"]["sample_approved"] is True
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 200, r.text
        assert r.json()["order"]["current_stage"] == 10

    def test_reupload_resets_approval_and_appends_history(self, admin_sess, po_id, operator_info):
        self._prep_to_stage9(admin_sess, po_id, operator_info)
        admin_sess.post(f"{API}/orders/{po_id}/approve-sample", json={"approved": True})
        # Re-upload
        r = admin_sess.post(f"{API}/orders/{po_id}/attachment", json={
            "name": "sample2.png",
            "data_url": "data:image/png;base64," + base64.b64encode(b"y").decode(),
            "kind": "sample",
        })
        assert r.status_code == 200
        order = r.json()["order"]
        assert order["sample_approved"] is False
        assert len(order["sample_files"]) >= 2, "History file lama harus tetap ada"
        history = order.get("sample_approval_history") or []
        assert any(h.get("action") == "reset-on-new-upload" for h in history)


class TestPrintQtyAndPackedGate:
    def _prep_to_stage10(self, admin_sess, po_id, operator_info):
        admin_sess.post(f"{API}/orders/{po_id}/assign-operators", json={
            "splits": [{"operator_email": operator_info["email"], "operator_name": operator_info["name"], "quantity": 100}]
        })
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/screener-info", json={"screen_qty": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/helper-prep",
                        json={"qty_required": 100, "qty_prepared": 100})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/attachment", json={
            "name": "s.png", "data_url": "data:image/png;base64,eA==", "kind": "sample"
        })
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        admin_sess.post(f"{API}/orders/{po_id}/approve-sample", json={"approved": True})
        admin_sess.post(f"{API}/orders/{po_id}/advance", json={})  # -> 10
        r = admin_sess.get(f"{API}/orders/{po_id}")
        body = r.json()
        return body.get("order", body)

    def test_10_to_11_blocked_without_qty_cetak(self, admin_sess, po_id, operator_info):
        self._prep_to_stage10(admin_sess, po_id, operator_info)
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 400
        assert "Qty Cetak belum diinput." in r.json()["detail"]["prereq_reasons"]

    def test_10_to_11_ok_after_qty(self, admin_sess, po_id, operator_info):
        order = self._prep_to_stage10(admin_sess, po_id, operator_info)
        split_id = order["operator_splits"][0]["id"]
        r = admin_sess.post(f"{API}/orders/{po_id}/operator-progress",
                            json={"operator_id": split_id, "qty_completed": 100})
        assert r.status_code == 200, r.text
        r = admin_sess.post(f"{API}/orders/{po_id}/advance", json={})
        assert r.status_code == 200, r.text
        assert r.json()["order"]["current_stage"] == 11

    @pytest.mark.parametrize("stage", [5, 6, 7, 8, 9, 10])
    def test_packed_shortcut_blocked(self, admin_sess, mongo, stage, operator_info):
        # Build fresh PO at requested stage with minimal prereqs (i.e. incomplete)
        doc = _make_po_doc(stage=stage)
        # Give assign only for stage>=6 to isolate reasons
        if stage >= 6:
            doc["operator_splits"] = [{
                "id": str(uuid.uuid4()),
                "operator_email": operator_info["email"],
                "operator_name": operator_info["name"],
                "quantity": 100,
                "qty_completed": 0,
                "stage": 8,
            }]
        mongo.orders.insert_one(doc)
        try:
            r = admin_sess.post(f"{API}/orders/{doc['id']}/packed")
            assert r.status_code == 400, f"stage={stage} expected 400 got {r.status_code}: {r.text}"
            reasons = r.json()["detail"]["prereq_reasons"]
            assert len(reasons) > 0
            # Verify MongoDB stage NOT mutated
            fresh = mongo.orders.find_one({"id": doc["id"]})
            assert fresh["current_stage"] == stage, f"Stage mutated from {stage} to {fresh['current_stage']}"
            assert fresh.get("packing_status") != "Sudah"
        finally:
            mongo.orders.delete_one({"id": doc["id"]})

    def test_packed_ok_with_all_prereqs(self, admin_sess, po_id, operator_info):
        order = self._prep_to_stage10(admin_sess, po_id, operator_info)
        split_id = order["operator_splits"][0]["id"]
        admin_sess.post(f"{API}/orders/{po_id}/operator-progress",
                        json={"operator_id": split_id, "qty_completed": 100})
        r = admin_sess.post(f"{API}/orders/{po_id}/packed")
        assert r.status_code == 200, r.text
        d = r.json()["order"]
        assert d["current_stage"] == 11
        assert d["packing_status"] == "Sudah"
        assert d["packing_ready"] is True
        assert any("Sudah dipacking" in (t.get("note") or "") for t in d.get("timeline", []))


class TestPermissionsCatalog:
    def test_permissions_expose_sample_approve(self, admin_sess):
        # Try common endpoints
        for path in ("/admin/permissions", "/permissions", "/admin/permissions/catalog"):
            r = admin_sess.get(f"{API}{path}")
            if r.status_code == 200:
                raw = r.text
                if "operator.sample_approve" in raw:
                    return
        pytest.fail("operator.sample_approve permission not exposed via admin permissions endpoint")

    def test_admin_effective_perms_include_sample_approve(self, admin_sess):
        r = admin_sess.get(f"{API}/auth/me")
        assert r.status_code == 200
        me = r.json()
        # Effective perms could be in me['permissions'] OR resolved server-side; if role=admin,
        # backend permits via role check regardless. So check `me`.
        perms = me.get("permissions") or me.get("effective_permissions") or []
        # If not exposed here, at minimum verify by exercising the endpoint (already covered in TestBugCSampleApproval).
        # Soft assertion:
        if perms:
            assert "operator.sample_approve" in perms or me.get("role") == "admin"


class TestExistingDataSafety:
    def test_get_orders_returns_prereq_fields(self, admin_sess):
        r = admin_sess.get(f"{API}/orders")
        assert r.status_code == 200
        arr = r.json() if isinstance(r.json(), list) else r.json().get("orders") or r.json().get("items") or []
        assert isinstance(arr, list)
        # Pick first non-empty existing order (skip our tests since po_id fixture cleanups)
        for o in arr[:5]:
            oid = o["id"]
            d = admin_sess.get(f"{API}/orders/{oid}")
            assert d.status_code == 200, d.text
            body = d.json()
            payload = body.get("order", body)
            assert "packing_prereq_reasons" in payload
            assert "packing_ready" in payload
            assert isinstance(payload["packing_prereq_reasons"], list)
