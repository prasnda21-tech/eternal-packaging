"""Tests for Phase 2 features: HoP Dashboard, Reminders, Accurate mock sync,
Operator qty progress, Surat Jalan loading + delivered."""
import os
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
if not BASE_URL:
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
    "helper": "helper@eternalpackaging.com",
    "op1": "operator@eternalpackaging.com",
    "op2": "operator2@eternalpackaging.com",
    "driver": "driver@eternalpackaging.com",
    "crm": "crm@eternalpackaging.com",
}


def _login(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return s


@pytest.fixture(scope="module")
def sessions():
    return {k: _login(v) for k, v in EMAILS.items()}


@pytest.fixture(scope="module")
def orders(sessions):
    r = sessions["admin"].get(f"{BASE_URL}/api/orders", timeout=15)
    assert r.status_code == 200
    return {o["po_number"]: o for o in r.json()["orders"]}


# ---------- Accurate mock ----------
class TestAccurate:
    def test_accurate_status_mock(self, sessions):
        r = sessions["prod"].get(f"{BASE_URL}/api/accurate/status", timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "mock"
        assert "ACCURATE_CLIENT_ID" in body["message"]

    def test_accurate_status_forbidden_for_operator(self, sessions):
        r = sessions["op1"].get(f"{BASE_URL}/api/accurate/status", timeout=15)
        assert r.status_code == 403

    def test_accurate_sync_idempotent(self, sessions):
        # First call — might import or might already be updated depending on prior state
        r1 = sessions["prod"].post(f"{BASE_URL}/api/accurate/sync", timeout=20)
        assert r1.status_code == 200, r1.text
        b1 = r1.json()
        assert b1["mode"] == "mock"
        assert b1["imported"] + b1["updated"] == 2
        # Second call — everything should be "updated", nothing imported
        r2 = sessions["prod"].post(f"{BASE_URL}/api/accurate/sync", timeout=20)
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["imported"] == 0
        assert b2["updated"] == 2

    def test_accurate_sync_preserves_workflow_state(self, sessions):
        # Ensure PO-1049 exists
        sessions["prod"].post(f"{BASE_URL}/api/accurate/sync", timeout=20)
        r = sessions["prod"].get(f"{BASE_URL}/api/orders", timeout=15)
        pos = {o["po_number"]: o for o in r.json()["orders"]}
        assert "PO-1049" in pos
        oid = pos["PO-1049"]["id"]
        # Advance the stage from 0 -> 1
        cur = pos["PO-1049"]["current_stage"]
        r_adv = sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={"note": "TEST_move"}, timeout=15)
        assert r_adv.status_code == 200, r_adv.text
        new_stage = r_adv.json()["order"]["current_stage"]
        assert new_stage == cur + 1
        # Re-sync mock; workflow must be preserved
        sessions["prod"].post(f"{BASE_URL}/api/accurate/sync", timeout=20)
        r2 = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}", timeout=15)
        got = r2.json()["order"]
        assert got["current_stage"] == new_stage, "sync clobbered workflow state!"
        # timeline should still contain the TEST_move entry
        assert any("TEST_move" in (e.get("note") or "") for e in got["timeline"])

    def test_operator_cannot_sync(self, sessions):
        r = sessions["op1"].post(f"{BASE_URL}/api/accurate/sync", timeout=15)
        assert r.status_code == 403


# ---------- HoP Dashboard summary ----------
class TestDashboardSummary:
    def test_hop_dashboard_shape(self, sessions):
        r = sessions["prod"].get(f"{BASE_URL}/api/dashboard/summary", timeout=15)
        assert r.status_code == 200, r.text
        b = r.json()
        for k in ("kpi", "stage_breakdown", "operator_load", "due_today", "overdue",
                  "waiting_design_approval", "waiting_payment_approval"):
            assert k in b, f"missing key {k}"
        kpi = b["kpi"]
        for k in ("total_active", "total_urgent", "due_today", "overdue",
                  "waiting_design_approval", "waiting_payment_approval", "completed"):
            assert k in kpi and isinstance(kpi[k], int)
        # 16 stages
        assert len(b["stage_breakdown"]) == 16
        assert b["stage_breakdown"][0]["label"] == "Order Masuk"
        assert b["stage_breakdown"][15]["label"] == "Terkirim"

    def test_operator_load_fields(self, sessions):
        r = sessions["prod"].get(f"{BASE_URL}/api/dashboard/summary", timeout=15)
        ol = r.json()["operator_load"]
        # At least one operator active
        if ol:
            row = ol[0]
            for k in ("email", "name", "qty_scheduled", "qty_completed", "qty_remaining",
                      "active_po_count", "pos"):
                assert k in row
            assert row["qty_remaining"] == max(0, row["qty_scheduled"] - row["qty_completed"])

    def test_operator_forbidden(self, sessions):
        r = sessions["op1"].get(f"{BASE_URL}/api/dashboard/summary", timeout=15)
        assert r.status_code == 403

    def test_driver_forbidden(self, sessions):
        r = sessions["driver"].get(f"{BASE_URL}/api/dashboard/summary", timeout=15)
        assert r.status_code == 403


# ---------- Reminders ----------
class TestReminders:
    def test_hop_sees_all(self, sessions):
        r = sessions["prod"].get(f"{BASE_URL}/api/reminders", timeout=15)
        assert r.status_code == 200
        b = r.json()
        assert b["role_scope"] == "all"
        assert "reminders" in b and "counts" in b
        counts = b["counts"]
        for k in ("overdue", "warn_6h", "warn_h1"):
            assert k in counts
        # sorted: overdue first
        buckets = [i["bucket"] for i in b["reminders"]]
        priority = {"overdue": 0, "warn_6h": 1, "warn_h1": 2}
        assert buckets == sorted(buckets, key=lambda x: priority.get(x, 9))
        for it in b["reminders"]:
            assert it["bucket"] in ("overdue", "warn_6h", "warn_h1")
            assert it["hours_left"] is None or isinstance(it["hours_left"], (int, float))

    def test_operator_scope_own(self, sessions):
        r = sessions["op1"].get(f"{BASE_URL}/api/reminders", timeout=15)
        assert r.status_code == 200
        b = r.json()
        assert b["role_scope"] == "own"
        # any items must have owner_role=operator
        for it in b["reminders"]:
            assert it["owner_role"] == "operator"


# ---------- Operator qty progress ----------
class TestOperatorProgress:
    def test_operator_updates_own_qty(self, sessions, orders):
        po = orders["PO-1048"]
        oid = po["id"]
        splits = po["operator_splits"]
        my = next(s for s in splits if s["operator_email"] == EMAILS["op1"])
        r = sessions["op1"].post(
            f"{BASE_URL}/api/orders/{oid}/operator-progress",
            json={"operator_id": my["id"], "qty_completed": 200}, timeout=15,
        )
        assert r.status_code == 200, r.text
        got = next(s for s in r.json()["order"]["operator_splits"] if s["id"] == my["id"])
        assert got["qty_completed"] == 200

    def test_qty_clamped_to_quantity(self, sessions, orders):
        po = orders["PO-1048"]
        oid = po["id"]
        # Refetch fresh splits
        splits = sessions["op1"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]["operator_splits"]
        my = next(s for s in splits if s["operator_email"] == EMAILS["op1"])
        r = sessions["op1"].post(
            f"{BASE_URL}/api/orders/{oid}/operator-progress",
            json={"operator_id": my["id"], "qty_completed": 999999}, timeout=15,
        )
        assert r.status_code == 200
        got = next(s for s in r.json()["order"]["operator_splits"] if s["id"] == my["id"])
        assert got["qty_completed"] == my["quantity"]

    def test_op1_cannot_update_op2(self, sessions, orders):
        po = orders["PO-1048"]
        oid = po["id"]
        splits = sessions["op1"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]["operator_splits"]
        other = next(s for s in splits if s["operator_email"] == EMAILS["op2"])
        r = sessions["op1"].post(
            f"{BASE_URL}/api/orders/{oid}/operator-progress",
            json={"operator_id": other["id"], "qty_completed": 5}, timeout=15,
        )
        assert r.status_code == 403

    def test_head_prod_updates_any_operator(self, sessions, orders):
        po = orders["PO-1048"]
        oid = po["id"]
        splits = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]["operator_splits"]
        other = next(s for s in splits if s["operator_email"] == EMAILS["op2"])
        r = sessions["prod"].post(
            f"{BASE_URL}/api/orders/{oid}/operator-progress",
            json={"operator_id": other["id"], "qty_completed": 50}, timeout=15,
        )
        assert r.status_code == 200

    def test_driver_cannot_update(self, sessions, orders):
        po = orders["PO-1048"]
        oid = po["id"]
        splits = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]["operator_splits"]
        target = splits[0]
        r = sessions["driver"].post(
            f"{BASE_URL}/api/orders/{oid}/operator-progress",
            json={"operator_id": target["id"], "qty_completed": 10}, timeout=15,
        )
        assert r.status_code == 403


# ---------- Loading checklist auto-advance to stage 13 ----------
class TestLoadingChecklist:
    def _get_fresh_po_for_loading(self, sessions):
        """Grab or create a PO around stage 12 (Surat Jalan) to load."""
        # Use PO-1050 (from Accurate mock), fully advance via prod to stage 12
        sessions["prod"].post(f"{BASE_URL}/api/accurate/sync", timeout=20)
        r = sessions["prod"].get(f"{BASE_URL}/api/orders", timeout=15)
        pos = {o["po_number"]: o for o in r.json()["orders"]}
        po = pos.get("PO-1050")
        assert po, "PO-1050 missing after sync"
        oid = po["id"]
        # Approve design + payment
        sessions["designer"].post(f"{BASE_URL}/api/orders/{oid}/approve-design", json={"approved": True})
        sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/approve-payment", json={"approved": True})
        # Advance to 12
        for _ in range(20):
            cur = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]["current_stage"]
            if cur >= 12:
                break
            sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={})
        return oid

    def test_loading_incomplete_does_not_advance(self, sessions):
        oid = self._get_fresh_po_for_loading(sessions)
        before = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]["current_stage"]
        r = sessions["driver"].post(
            f"{BASE_URL}/api/orders/{oid}/loading",
            json={"vehicle_plate": "B 1234 XX", "driver_name": "Budi", "items_loaded": True, "seal_ok": False},
            timeout=15,
        )
        assert r.status_code == 200
        got = r.json()["order"]
        assert got["current_stage"] == before  # not advanced

    def test_loading_complete_advances_to_13(self, sessions):
        oid = self._get_fresh_po_for_loading(sessions)
        r = sessions["driver"].post(
            f"{BASE_URL}/api/orders/{oid}/loading",
            json={"vehicle_plate": "B 1234 XX", "driver_name": "Budi", "items_loaded": True, "seal_ok": True, "notes": "TEST_ok"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        got = r.json()["order"]
        assert got["current_stage"] >= 13
        assert got["loading_checklist"]["items_loaded"] is True
        assert got["loading_checklist"]["seal_ok"] is True
        assert got["loading_checklist"]["vehicle_plate"] == "B 1234 XX"
        assert got["loading_checklist"]["driver_name"] == "Budi"

    def test_driver_marks_delivered_records_metadata(self, sessions):
        oid = self._get_fresh_po_for_loading(sessions)
        # ensure loading complete
        sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/loading",
            json={"vehicle_plate": "B 1", "driver_name": "Budi", "items_loaded": True, "seal_ok": True})
        r = sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/delivered", timeout=15)
        assert r.status_code == 200
        got = r.json()["order"]
        assert got["delivery_status"] == "Terkirim"
        assert got["current_stage"] == 15
        assert got.get("delivered_at")
        assert got.get("delivered_by") == EMAILS["driver"]
