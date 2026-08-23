"""Backend tests for Eternal Packaging orders API.

Covers auth, order retrieval, stage advance persistence, approvals, operator
splits (assign + per-operator advance), urgent toggle role gating, attachments,
notes, delivery, and timeline chronology.
"""
import os
import base64
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
    # Fallback for local since backend tests run inside container
    with open('/app/frontend/.env') as f:
        for line in f:
            if line.startswith('REACT_APP_BACKEND_URL='):
                BASE_URL = line.split('=', 1)[1].strip().rstrip('/')
                break

PASSWORD = "Eternal123!"
EMAILS = {
    "admin": "admin@eternalpackaging.com",
    "prod": "production@eternalpackaging.com",
    "designer": "designer@eternalpackaging.com",
    "screener": "screener@eternalpackaging.com",
    "helper": "helper@eternalpackaging.com",
    "op1": "operator@eternalpackaging.com",
    "op2": "operator2@eternalpackaging.com",
    "driver": "driver@eternalpackaging.com",
    "crm": "crm@eternalpackaging.com",
}


def client_for(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def sessions():
    return {k: client_for(v) for k, v in EMAILS.items()}


@pytest.fixture(scope="module")
def orders(sessions):
    r = sessions["admin"].get(f"{BASE_URL}/api/orders", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert "orders" in data and len(data["orders"]) >= 4
    return {o["po_number"]: o for o in data["orders"]}


# ---------- Auth ----------
class TestAuth:
    def test_login_invalid(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": EMAILS["admin"], "password": "wrong"}, timeout=15)
        assert r.status_code == 401

    def test_me_requires_cookie(self, sessions):
        r = sessions["admin"].get(f"{BASE_URL}/api/auth/me", timeout=15)
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_me_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/auth/me", timeout=15)
        assert r.status_code == 401


# ---------- Orders read ----------
class TestOrders:
    def test_list_orders_shape(self, sessions, orders):
        assert "PO-1046" in orders and "PO-1048" in orders
        o = orders["PO-1046"]
        # Ensure no mongo _id leaks
        assert "_id" not in o
        for k in ("id", "po_number", "current_stage", "line_items", "timeline", "operator_splits"):
            assert k in o

    def test_get_by_id(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["admin"].get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
        assert r.status_code == 200
        assert r.json()["order"]["po_number"] == "PO-1046"

    def test_get_not_found(self, sessions):
        r = sessions["admin"].get(f"{BASE_URL}/api/orders/does-not-exist", timeout=15)
        assert r.status_code == 404


# ---------- Approve design + persistence ----------
class TestApproveDesign:
    def test_designer_approves_design_persists(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["designer"].post(f"{BASE_URL}/api/orders/{oid}/approve-design", json={"approved": True, "note": "TEST_ok"}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()["order"]
        assert body["design_status"] == "Approved"
        assert body["current_stage"] >= 3
        # Reload from GET to verify persistence in mongo
        r2 = sessions["admin"].get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
        loaded = r2.json()["order"]
        assert loaded["design_status"] == "Approved"
        assert loaded["current_stage"] >= 3
        # Timeline entry with by_role=designer
        tl = loaded["timeline"]
        assert any(e.get("by_role") == "designer" for e in tl)

    def test_screener_cannot_approve_design(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["screener"].post(f"{BASE_URL}/api/orders/{oid}/approve-design", json={"approved": True}, timeout=15)
        assert r.status_code == 403


# ---------- Approve payment ----------
class TestApprovePayment:
    def test_head_prod_approves_payment(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/approve-payment", json={"approved": True}, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()["order"]
        assert body["payment_status"] == "Approved"
        assert body["current_stage"] >= 5

    def test_operator_cannot_approve_payment(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["op1"].post(f"{BASE_URL}/api/orders/{oid}/approve-payment", json={"approved": True}, timeout=15)
        assert r.status_code == 403


# ---------- Advance stage persistence (regression) ----------
class TestAdvancePersistence:
    def test_advance_persists_after_refresh(self, sessions, orders):
        # Use PO-1045 currently at 5
        oid = orders["PO-1045"]["id"]
        r0 = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
        start = r0.json()["order"]["current_stage"]
        r = sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": "TEST_advance"}, timeout=15)
        assert r.status_code == 200, r.text
        new_stage = r.json()["order"]["current_stage"]
        assert new_stage == start + 1
        # simulate refresh - new session
        s2 = client_for(EMAILS["prod"])
        r2 = s2.get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
        assert r2.json()["order"]["current_stage"] == new_stage


# ---------- Operator splits ----------
class TestOperatorSplits:
    def test_op1_advances_own_split(self, sessions, orders):
        po = orders["PO-1048"]
        oid = po["id"]
        splits = po["operator_splits"]
        my_split = next(s for s in splits if s["operator_email"] == EMAILS["op1"])
        before_stage = my_split["stage"]
        r = sessions["op1"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={"operator_id": my_split["id"]}, timeout=15)
        assert r.status_code == 200, r.text
        new_splits = r.json()["order"]["operator_splits"]
        updated = next(s for s in new_splits if s["id"] == my_split["id"])
        assert updated["stage"] == min(before_stage + 1, 11)
        # timeline entry by_role=operator
        tl = r.json()["order"]["timeline"]
        assert any(e.get("by_role") == "operator" for e in tl)

    def test_op1_cannot_advance_op2_split(self, sessions, orders):
        po = orders["PO-1048"]
        oid = po["id"]
        # refetch to get latest splits
        r0 = sessions["op1"].get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
        splits = r0.json()["order"]["operator_splits"]
        other = next(s for s in splits if s["operator_email"] == EMAILS["op2"])
        r = sessions["op1"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={"operator_id": other["id"]}, timeout=15)
        assert r.status_code == 403

    def test_assign_operators(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        payload = {"splits": [
            {"operator_email": EMAILS["op1"], "operator_name": "Rafi Operator", "quantity": 1500},
            {"operator_email": EMAILS["op2"], "operator_name": "Nanda", "quantity": 1700},
        ]}
        r = sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/assign-operators", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()["order"]
        assert len(body["operator_splits"]) == 2
        # current_stage should be >= 8 due to assignment (or higher if already advanced)
        assert body["current_stage"] >= 5  # 1046 previously approved payment so at 5+

    def test_assign_operators_exceeds_qty(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        payload = {"splits": [
            {"operator_email": EMAILS["op1"], "operator_name": "Rafi", "quantity": 99999},
        ]}
        r = sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/assign-operators", json=payload, timeout=15)
        assert r.status_code == 400

    def test_operator_cannot_assign(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["op1"].post(f"{BASE_URL}/api/orders/{oid}/assign-operators", json={"splits": []}, timeout=15)
        assert r.status_code == 403


# ---------- Urgent ----------
class TestUrgent:
    def test_head_prod_can_set_urgent(self, sessions, orders):
        oid = orders["PO-1047"]["id"]
        r = sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/urgent", json={"urgent": True}, timeout=15)
        assert r.status_code == 200
        body = r.json()["order"]
        assert body["priority"] == "urgent"
        assert body["urgency_approved_by"] == EMAILS["prod"]

    def test_operator_cannot_set_urgent(self, sessions, orders):
        oid = orders["PO-1047"]["id"]
        r = sessions["op1"].post(f"{BASE_URL}/api/orders/{oid}/urgent", json={"urgent": True}, timeout=15)
        assert r.status_code == 403


# ---------- Attachments ----------
class TestAttachments:
    def _make_data_url(self, size=200):
        raw = b"x" * size
        return "data:image/png;base64," + base64.b64encode(raw).decode()

    def test_designer_upload_design(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["designer"].post(f"{BASE_URL}/api/orders/{oid}/attachment",
                                       json={"name": "TEST_mock.png", "data_url": self._make_data_url(), "kind": "design"}, timeout=15)
        assert r.status_code == 200, r.text
        assert len(r.json()["order"]["design_files"]) >= 1

    def test_operator_upload_sample(self, sessions, orders):
        oid = orders["PO-1048"]["id"]
        r = sessions["op1"].post(f"{BASE_URL}/api/orders/{oid}/attachment",
                                  json={"name": "TEST_sample.png", "data_url": self._make_data_url(), "kind": "sample"}, timeout=15)
        assert r.status_code == 200, r.text
        assert len(r.json()["order"]["sample_files"]) >= 1

    def test_operator_cannot_upload_design(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["op1"].post(f"{BASE_URL}/api/orders/{oid}/attachment",
                                  json={"name": "x.png", "data_url": self._make_data_url(), "kind": "design"}, timeout=15)
        assert r.status_code == 403


# ---------- CRM read-only ----------
class TestCRMReadOnly:
    def test_crm_cannot_advance(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["crm"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={}, timeout=15)
        assert r.status_code == 403

    def test_crm_cannot_add_note(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["crm"].post(f"{BASE_URL}/api/orders/{oid}/note", json={"note": "hi"}, timeout=15)
        assert r.status_code == 403

    def test_crm_can_read(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["crm"].get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
        assert r.status_code == 200


# ---------- Delivery ----------
class TestDelivery:
    def test_driver_marks_delivered(self, sessions, orders):
        # Use PO-1048 which is far along; advance to loading first via prod
        oid = orders["PO-1048"]["id"]
        # Advance to stage 12+ via prod loop
        for _ in range(6):
            cur = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]["current_stage"]
            if cur >= 12:
                break
            sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={})
        # Iteration 3: driver must upload at least one proof before /delivered
        # Tiny valid 1x1 JPEG
        import base64
        jpeg = base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi6KKK+ZP3E//Z"
        )
        files = {"file": ("proof.jpg", jpeg, "image/jpeg")}
        pr = sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/delivery-proof", files=files, timeout=30)
        assert pr.status_code == 200, pr.text
        r = sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/delivered", timeout=15)
        assert r.status_code == 200
        body = r.json()["order"]
        assert body["delivery_status"] == "Terkirim"
        assert body["current_stage"] == 15


# ---------- Timeline chronology ----------
class TestTimeline:
    def test_timeline_chronological(self, sessions, orders):
        oid = orders["PO-1046"]["id"]
        r = sessions["admin"].get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
        tl = r.json()["order"]["timeline"]
        times = [e["at"] for e in tl]
        assert times == sorted(times), "timeline not chronological"
        # Contains multiple roles by now
        roles = {e.get("by_role") for e in tl}
        assert "designer" in roles or "head_production" in roles
