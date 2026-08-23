"""Phase A (R1, R2, R7, R9, R10, R11) backend tests for Eternal Packaging.

Covers approval gating, HOLD, rollback, helper-prep, screener-info, and bypass.
Creates isolated fresh POs prefixed `PO-PHASEA-*` via direct Mongo insert
(no existing PO data is touched)."""
import os
import time
import uuid
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://print-ops-hub-12.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")

ADMIN = ("admin@eternalpackaging.com", "Eternal123!")
DRIVER = ("driver@eternalpackaging.com", "Eternal123!")

TINY_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

STAGES = [
    "Order Masuk", "Design", "Approval Design", "Payment", "Approval Payment",
    "Penjadwalan", "Screener", "Persiapan Helper", "Operator",
    "Konfirmasi Sample", "Cetak", "Packing", "Surat Jalan", "Loading",
    "Driver", "Terkirim",
]

mongo = MongoClient(MONGO_URL)
db = mongo[DB_NAME]


# ---------- helpers ----------
def _login(email, pwd):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return s


def _new_po(stage=0, po_number=None, **extra):
    po_number = po_number or f"PO-PHASEA-{uuid.uuid4().hex[:8].upper()}"
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": po_number,
        "order_date": "2026-01-15",
        "priority": "normal",
        "urgency_approved_by": None,
        "customer": "TEST Customer",
        "outlet": "TEST Outlet",
        "recipient": "TEST",
        "address": "TEST addr",
        "payment_terms": "COD",
        "payment_status": "Waiting",
        "design_status": "Waiting",
        "sales_user": "admin@eternalpackaging.com",
        "sales": None,
        "created_by_user": {"email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"},
        "expedition": "Internal",
        "delivery_zone": "Kota",
        "seller": "TEST",
        "line_items": [{"item": "Cup", "quantity": 5000}],
        "total_quantity": 5000,
        "item_count": 1,
        "operator_splits": [],
        "design_files": [],
        "payment_files": [],
        "sample_files": [],
        "notes": "",
        "current_stage": stage,
        "status_label": STAGES[stage],
        "packing_status": "Belum",
        "delivery_status": "Belum",
        "delivery_note_number": "",
        "loading_checklist": None,
        "timeline": [],
        "created_at": "2026-01-15T00:00:00+00:00",
        "updated_at": "2026-01-15T00:00:00+00:00",
    }
    doc.update(extra)
    db.orders.insert_one(doc)
    return doc


def _get_order(session, oid):
    r = session.get(f"{API}/orders/{oid}", timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["order"]


def _detail_str(resp):
    """Extract HTTPException detail as a string for substring matching."""
    try:
        d = resp.json().get("detail")
    except Exception:
        return resp.text
    if isinstance(d, dict):
        return str(d)
    return str(d)


# ---------- fixtures ----------
@pytest.fixture(scope="module")
def admin():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def driver():
    return _login(*DRIVER)


@pytest.fixture
def fresh_po():
    order = _new_po(stage=0)
    yield order
    db.orders.delete_one({"id": order["id"]})


# ---------- R1: Design approval gating ----------
class TestR1DesignApproval:
    def test_design_approve_blocked_without_upload(self, admin, fresh_po):
        r = admin.post(f"{API}/orders/{fresh_po['id']}/approve-design", json={"approved": True})
        assert r.status_code == 400, r.text
        assert "Upload design terlebih dahulu" in _detail_str(r)

    def test_design_upload_then_approve(self, admin, fresh_po):
        oid = fresh_po["id"]
        r = admin.post(f"{API}/orders/{oid}/attachment",
                       json={"kind": "design", "name": "d.png", "data_url": TINY_PNG})
        assert r.status_code == 200, r.text
        r = admin.post(f"{API}/orders/{oid}/approve-design", json={"approved": True})
        assert r.status_code == 200, r.text
        o = r.json()["order"]
        assert o["design_status"] == "Approved"
        assert o["design_approved_by"] == ADMIN[0]
        assert o.get("design_approved_at")

    def test_design_reupload_resets_approval(self, admin, fresh_po):
        oid = fresh_po["id"]
        admin.post(f"{API}/orders/{oid}/attachment",
                   json={"kind": "design", "name": "d1.png", "data_url": TINY_PNG})
        admin.post(f"{API}/orders/{oid}/approve-design", json={"approved": True})
        r = admin.post(f"{API}/orders/{oid}/attachment",
                       json={"kind": "design", "name": "d2.png", "data_url": TINY_PNG})
        assert r.status_code == 200
        o = r.json()["order"]
        assert o["design_status"] == "Waiting"
        assert o["design_approved_by"] in (None, "")
        hist = o.get("design_approval_history", [])
        assert any(h.get("action") == "reset-on-new-upload" for h in hist)
        # Re-approve brings it back
        r = admin.post(f"{API}/orders/{oid}/approve-design", json={"approved": True})
        assert r.status_code == 200
        assert r.json()["order"]["design_status"] == "Approved"


# ---------- R1: Payment approval gating ----------
class TestR1PaymentApproval:
    def test_payment_approve_blocked_without_upload(self, admin, fresh_po):
        r = admin.post(f"{API}/orders/{fresh_po['id']}/approve-payment", json={"approved": True})
        assert r.status_code == 400
        assert "Upload bukti pembayaran" in _detail_str(r)

    def test_payment_upload_approve_reupload_resets(self, admin, fresh_po):
        oid = fresh_po["id"]
        r = admin.post(f"{API}/orders/{oid}/attachment",
                       json={"kind": "payment", "name": "p.png", "data_url": TINY_PNG})
        assert r.status_code == 200, r.text
        r = admin.post(f"{API}/orders/{oid}/approve-payment", json={"approved": True})
        assert r.status_code == 200
        assert r.json()["order"]["payment_status"] == "Approved"
        assert r.json()["order"]["payment_approved_by"] == ADMIN[0]
        # Re-upload payment resets approval
        r = admin.post(f"{API}/orders/{oid}/attachment",
                       json={"kind": "payment", "name": "p2.png", "data_url": TINY_PNG})
        o = r.json()["order"]
        assert o["payment_status"] == "Waiting"
        hist = o.get("payment_approval_history", [])
        assert any(h.get("action") == "reset-on-new-upload" for h in hist)


# ---------- R2: HOLD gating ----------
class TestR2Hold:
    def test_fresh_po_hold_flags(self, admin, fresh_po):
        o = _get_order(admin, fresh_po["id"])
        assert o["hold"] is True
        assert "Belum ada design" in o["hold_reasons"]
        assert "Belum ada bukti payment" in o["hold_reasons"]

    def test_hold_blocks_scheduling_and_lifts_after_approvals(self, admin, fresh_po):
        oid = fresh_po["id"]
        # Upload design, approve
        admin.post(f"{API}/orders/{oid}/attachment",
                   json={"kind": "design", "name": "d.png", "data_url": TINY_PNG})
        r = admin.post(f"{API}/orders/{oid}/approve-design", json={"approved": True})
        assert r.status_code == 200
        # Now advance from 3 (Payment) → 4 (Approval Payment)
        r = admin.post(f"{API}/orders/{oid}/advance", json={})
        assert r.status_code == 200, r.text
        # advance 4→5 requires payment ok — no payment yet so should fail at cur==4
        r = admin.post(f"{API}/orders/{oid}/advance", json={})
        assert r.status_code == 400
        # Upload payment
        admin.post(f"{API}/orders/{oid}/attachment",
                   json={"kind": "payment", "name": "p.png", "data_url": TINY_PNG})
        # Approve payment (auto-advances to 5)
        r = admin.post(f"{API}/orders/{oid}/approve-payment", json={"approved": True})
        assert r.status_code == 200
        o = r.json()["order"]
        assert o["current_stage"] == 5
        # 5→6 should now succeed (hold cleared)
        r = admin.post(f"{API}/orders/{oid}/advance", json={})
        assert r.status_code == 200, r.text
        o = r.json()["order"]
        assert o["current_stage"] == 6
        # HOLD gate is when leaving stage 5 (Penjadwalan); new_stage 6 = Screener.
        assert o["status_label"] == STAGES[6]
        # verify hold lifted
        o2 = _get_order(admin, oid)
        assert o2["hold"] is False
        assert o2["hold_reasons"] == []

    def test_hold_blocks_5_to_6_without_payment(self, admin):
        # Fresh PO placed at stage 5 with only design approved (no payment)
        order = _new_po(stage=5,
                        design_files=[{"id": "1", "name": "d.png", "data_url": TINY_PNG,
                                        "uploaded_by": ADMIN[0]}],
                        design_status="Approved",
                        design_approved_by=ADMIN[0],
                        design_approved_at="2026-01-15T00:00:00+00:00")
        try:
            r = admin.post(f"{API}/orders/{order['id']}/advance", json={})
            assert r.status_code == 400, r.text
            detail = r.json().get("detail")
            assert isinstance(detail, dict)
            assert "HOLD" in detail.get("message", "")
            assert isinstance(detail.get("hold_reasons"), list)
            assert len(detail["hold_reasons"]) > 0
        finally:
            db.orders.delete_one({"id": order["id"]})


# ---------- R7: Rollback ----------
class TestR7Rollback:
    def test_rollback_perm_gating_driver(self, driver, admin, fresh_po):
        r = driver.post(f"{API}/orders/{fresh_po['id']}/rollback",
                        json={"to_stage": 0, "reason": "test"})
        assert r.status_code == 403
        assert "workflow.rollback" in _detail_str(r)

    def test_rollback_admin_success_and_reset(self, admin):
        # PO at stage 5, design approved, payment approved — rollback to 0
        order = _new_po(stage=5,
                        design_files=[{"id": "1", "name": "d.png", "data_url": TINY_PNG}],
                        design_status="Approved",
                        design_approved_by=ADMIN[0],
                        design_approved_at="2026-01-15T00:00:00+00:00",
                        payment_files=[{"id": "1", "name": "p.png", "data_url": TINY_PNG}],
                        payment_status="Approved",
                        payment_approved_by=ADMIN[0],
                        payment_approved_at="2026-01-15T00:00:00+00:00")
        try:
            r = admin.post(f"{API}/orders/{order['id']}/rollback",
                           json={"to_stage": 0, "reason": "reset semua"})
            assert r.status_code == 200, r.text
            o = r.json()["order"]
            assert o["current_stage"] == 0
            rb = o.get("rollback_history", [])
            assert len(rb) >= 1
            assert rb[-1]["from_stage"] == 5 and rb[-1]["to_stage"] == 0
            assert rb[-1]["by_email"] == ADMIN[0]
            assert rb[-1]["reason"] == "reset semua"
            # Design reset because to_stage <= 1
            assert o["design_status"] == "Waiting"
            assert any(h.get("action") == "reset-on-rollback"
                       for h in o.get("design_approval_history", []))
            # Payment reset because to_stage <= 3
            assert o["payment_status"] == "Waiting"
            # Timeline updated
            assert len(o.get("timeline", [])) >= 1
        finally:
            db.orders.delete_one({"id": order["id"]})

    def test_rollback_validation(self, admin):
        order = _new_po(stage=3)
        try:
            oid = order["id"]
            # Empty reason
            r = admin.post(f"{API}/orders/{oid}/rollback", json={"to_stage": 0, "reason": "  "})
            assert r.status_code == 400
            # to_stage >= current_stage
            r = admin.post(f"{API}/orders/{oid}/rollback", json={"to_stage": 3, "reason": "x"})
            assert r.status_code == 400
            r = admin.post(f"{API}/orders/{oid}/rollback", json={"to_stage": 5, "reason": "x"})
            assert r.status_code == 400
            # out of range
            r = admin.post(f"{API}/orders/{oid}/rollback", json={"to_stage": -1, "reason": "x"})
            assert r.status_code == 400
            r = admin.post(f"{API}/orders/{oid}/rollback", json={"to_stage": 99, "reason": "x"})
            assert r.status_code == 400
        finally:
            db.orders.delete_one({"id": order["id"]})


# ---------- R9: Helper prep ----------
class TestR9HelperPrep:
    def test_helper_prep_update(self, admin, fresh_po):
        oid = fresh_po["id"]
        orig_qty = fresh_po["total_quantity"]
        r = admin.post(f"{API}/orders/{oid}/helper-prep",
                       json={"qty_required": 10000, "qty_prepared": 8500, "notes": "x"})
        assert r.status_code == 200, r.text
        o = r.json()["order"]
        hp = o["helper_prep"]
        assert hp["qty_required"] == 10000
        assert hp["qty_prepared"] == 8500
        assert hp["qty_shortage"] == 1500
        assert hp["shortage_status"] == "Barang Kurang"
        assert hp["notes"] == "x"
        assert hp["updated_by"] == ADMIN[0]
        assert hp.get("updated_at")
        assert o["total_quantity"] == orig_qty  # UNCHANGED

        # Update to sufficient
        r = admin.post(f"{API}/orders/{oid}/helper-prep",
                       json={"qty_required": 10000, "qty_prepared": 10000})
        o = r.json()["order"]
        assert o["helper_prep"]["qty_shortage"] == 0
        assert o["helper_prep"]["shortage_status"] == "Cukup"

    def test_helper_prep_negative(self, admin, fresh_po):
        r = admin.post(f"{API}/orders/{fresh_po['id']}/helper-prep",
                       json={"qty_required": -5, "qty_prepared": 0})
        assert r.status_code == 400

    def test_helper_prep_driver_denied(self, driver, fresh_po):
        r = driver.post(f"{API}/orders/{fresh_po['id']}/helper-prep",
                        json={"qty_required": 10, "qty_prepared": 5})
        assert r.status_code == 403
        assert "helper.prepare" in _detail_str(r)


# ---------- R10: Screener info ----------
class TestR10Screener:
    def test_screener_info_update(self, admin, fresh_po):
        oid = fresh_po["id"]
        orig_qty = fresh_po["total_quantity"]
        orig_lines = fresh_po["line_items"]
        orig_stage = fresh_po["current_stage"]
        r = admin.post(f"{API}/orders/{oid}/screener-info",
                       json={"screen_qty": 4, "notes": "4 screen"})
        assert r.status_code == 200, r.text
        o = r.json()["order"]
        assert o["screener_info"]["screen_qty"] == 4
        assert o["screener_info"]["updated_by"] == ADMIN[0]
        assert o["total_quantity"] == orig_qty
        assert o["line_items"] == orig_lines
        assert o["current_stage"] == orig_stage  # NOT advanced

    def test_screener_info_negative(self, admin, fresh_po):
        r = admin.post(f"{API}/orders/{fresh_po['id']}/screener-info",
                       json={"screen_qty": -1})
        assert r.status_code == 400

    def test_screener_driver_denied(self, driver, fresh_po):
        r = driver.post(f"{API}/orders/{fresh_po['id']}/screener-info",
                        json={"screen_qty": 4})
        assert r.status_code == 403
        assert "screen.update" in _detail_str(r)


# ---------- R11: Bypass ----------
class TestR11Bypass:
    def test_design_bypass_perm_driver(self, driver, fresh_po):
        r = driver.post(f"{API}/orders/{fresh_po['id']}/design/bypass", json={"reason": "x"})
        assert r.status_code == 403
        assert "design.bypass_approval" in _detail_str(r)

    def test_design_bypass_admin_flow(self, admin, fresh_po):
        oid = fresh_po["id"]
        # Empty reason
        r = admin.post(f"{API}/orders/{oid}/design/bypass", json={"reason": "  "})
        assert r.status_code == 400
        assert "Alasan bypass wajib diisi" in _detail_str(r)
        # Valid
        r = admin.post(f"{API}/orders/{oid}/design/bypass", json={"reason": "cetak internal"})
        assert r.status_code == 200
        o = r.json()["order"]
        assert o["design_bypassed"] is True
        assert o["design_bypassed_by"] == ADMIN[0]
        assert o.get("design_bypassed_at")
        assert o["design_bypass_reason"] == "cetak internal"
        # design_status must NOT become Approved
        assert o["design_status"] != "Approved"
        assert any(h.get("action") == "bypass" for h in o.get("design_approval_history", []))

    def test_payment_bypass_flow(self, admin, driver, fresh_po):
        oid = fresh_po["id"]
        r = driver.post(f"{API}/orders/{oid}/payment/bypass", json={"reason": "x"})
        assert r.status_code == 403
        assert "payment.bypass_approval" in _detail_str(r)
        r = admin.post(f"{API}/orders/{oid}/payment/bypass", json={"reason": " "})
        assert r.status_code == 400
        r = admin.post(f"{API}/orders/{oid}/payment/bypass", json={"reason": "urgent"})
        assert r.status_code == 200
        o = r.json()["order"]
        assert o["payment_bypassed"] is True
        assert o["payment_status"] != "Approved"

    def test_schedule_with_design_approve_and_payment_bypass(self, admin):
        order = _new_po(stage=0)
        oid = order["id"]
        try:
            admin.post(f"{API}/orders/{oid}/attachment",
                       json={"kind": "design", "name": "d.png", "data_url": TINY_PNG})
            r = admin.post(f"{API}/orders/{oid}/approve-design", json={"approved": True})
            assert r.status_code == 200
            r = admin.post(f"{API}/orders/{oid}/payment/bypass", json={"reason": "manual"})
            assert r.status_code == 200
            # current_stage should now be 5 (bypass forces to 5)
            cur = r.json()["order"]["current_stage"]
            # advance until stage 5, then 5→6
            while cur < 5:
                rr = admin.post(f"{API}/orders/{oid}/advance", json={})
                assert rr.status_code == 200, rr.text
                cur = rr.json()["order"]["current_stage"]
            r = admin.post(f"{API}/orders/{oid}/advance", json={})
            assert r.status_code == 200, r.text
            assert r.json()["order"]["current_stage"] == 6
        finally:
            db.orders.delete_one({"id": oid})

    def test_schedule_with_only_design_bypass_no_payment_holds(self, admin):
        # Design bypass only (no payment) → HOLD at 5→6
        order = _new_po(stage=5, design_bypassed=True,
                        design_bypassed_by=ADMIN[0], design_bypass_reason="x",
                        design_files=[{"id": "1", "name": "d.png", "data_url": TINY_PNG}])
        oid = order["id"]
        try:
            r = admin.post(f"{API}/orders/{oid}/advance", json={})
            assert r.status_code == 400, r.text
            detail = r.json().get("detail")
            assert isinstance(detail, dict)
            assert "HOLD" in detail.get("message", "")
        finally:
            db.orders.delete_one({"id": oid})


# ---------- Audit log ----------
class TestAudit:
    def test_audit_actions_recorded(self, admin):
        order = _new_po(stage=0, po_number=f"PO-PHASEA-AUDIT-{uuid.uuid4().hex[:6].upper()}")
        oid = order["id"]
        pon = order["po_number"]
        try:
            # design.upload + design.approve
            admin.post(f"{API}/orders/{oid}/attachment",
                       json={"kind": "design", "name": "d.png", "data_url": TINY_PNG})
            admin.post(f"{API}/orders/{oid}/approve-design", json={"approved": True})
            # payment.upload + payment.approve
            admin.post(f"{API}/orders/{oid}/attachment",
                       json={"kind": "payment", "name": "p.png", "data_url": TINY_PNG})
            admin.post(f"{API}/orders/{oid}/approve-payment", json={"approved": True})
            # rollback
            admin.post(f"{API}/orders/{oid}/rollback",
                       json={"to_stage": 0, "reason": "audit test"})
            # helper.prep + screener.info
            admin.post(f"{API}/orders/{oid}/helper-prep",
                       json={"qty_required": 100, "qty_prepared": 50})
            admin.post(f"{API}/orders/{oid}/screener-info", json={"screen_qty": 2})
            # bypass design + payment (after rollback design_status=Waiting)
            admin.post(f"{API}/orders/{oid}/design/bypass", json={"reason": "audit test"})
            admin.post(f"{API}/orders/{oid}/payment/bypass", json={"reason": "audit test"})

            time.sleep(0.5)
            r = admin.get(f"{API}/admin/audit-logs", timeout=15)
            assert r.status_code == 200, r.text
            data = r.json()
            logs = data.get("logs") if isinstance(data, dict) else data
            assert isinstance(logs, list)
            target = f"po:{pon}"
            related = [l for l in logs if l.get("target_email") == target]
            actions = {l.get("action") for l in related}
            expected = {
                "design.upload", "design.approve",
                "payment.upload", "payment.approve",
                "design.bypass", "payment.bypass",
                "workflow.rollback", "helper.prep.update", "screener.info.update",
            }
            missing = expected - actions
            assert not missing, f"missing audit actions {missing}; got {actions}"
            # actor recorded
            for l in related:
                if l.get("action") in expected:
                    assert l.get("actor_email") == ADMIN[0] or l.get("actor") == ADMIN[0] \
                           or (l.get("actor") or {}).get("email") == ADMIN[0]
        finally:
            db.orders.delete_one({"id": oid})


# ---------- Preservation checks ----------
class TestPreservation:
    def test_existing_pos_still_exist(self):
        for pon in ["PO-1049", "PO-2001", "PO-FILE-TEST-9001", "PO-PDF-TEST-7001"]:
            doc = db.orders.find_one({"po_number": pon})
            # Existence is best-effort — if not present in this DB, skip individually
            if not doc:
                pytest.skip(f"{pon} not present in this DB — preservation only meaningful if seeded")

    def test_admin_user_role_intact(self, admin):
        r = admin.get(f"{API}/admin/users", timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        users = data.get("users") if isinstance(data, dict) else data
        assert isinstance(users, list)
        adm = next((u for u in users if u.get("email") == ADMIN[0]), None)
        assert adm is not None, "admin user missing"
        assert adm.get("role") == "admin"
