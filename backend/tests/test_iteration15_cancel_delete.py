"""
Iteration 15 — Requirement 5: Cancel PO vs Delete PO + Bulk Actions.

Verifies:
  * Login + list_orders status filtering (aktif/hold/selesai/cancelled)
  * Single Cancel PO (permissions, reason validation, double-cancel, audit)
  * _ensure_not_cancelled guard on ALL workflow endpoints
  * delete-impact + Delete PO (permissions, confirmation rules, audit snapshot)
  * Re-import after permanent delete is allowed; re-import while Cancelled is blocked
  * Bulk cancel + bulk delete
  * Phase A regression: advance / approve / rollback / helper-prep / screener-info

Uses fresh PO created via /api/import/file/confirm so seed data stays intact.
"""

import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = ("admin@eternalpackaging.com", "Eternal123!")
DESIGNER = ("designer@eternalpackaging.com", "Eternal123!")
HEAD = ("production@eternalpackaging.com", "Eternal123!")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=45)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text[:200]}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def designer():
    return _login(*DESIGNER)


def _make_po(admin_session, po_number=None, customer="TEST_QA Iter15"):
    po_number = po_number or f"PO-QA15-{uuid.uuid4().hex[:6].upper()}"
    preview = {
        "po_number": po_number,
        "accurate_id": None,
        "customer": customer,
        "outlet": None,
        "recipient": "QA Bot",
        "address": "Jl. Test",
        "payment_terms": "CBD",
        "sales": "QA",
        "created_by_user": "QA",
        "order_date": "2026-01-01",
        "estimated_delivery": "2026-01-30",
        "deadline": "2026-01-30",
        "line_items": [{"item_code": "TEST-1", "name": "Test Item", "quantity": 100, "unit": "pcs"}],
        "total_quantity": 100,
        "item_count": 1,
        "notes": "TEST_ QA iteration 15",
        "filename": "qa-iter15.csv",
    }
    r = admin_session.post(f"{API}/import/file/confirm", json={"preview": preview, "source": "file"})
    assert r.status_code == 200, f"could not create PO: {r.status_code} {r.text[:300]}"
    order = r.json()["order"]
    return order


# --------------------------------------------------------------- LOGIN + LIST
class TestLoginAndList:
    def test_login_admin(self, admin):
        r = admin.get(f"{API}/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN[0]
        assert "po.cancel" in r.json()["permissions"]
        assert "po.delete" in r.json()["permissions"]

    def test_list_default_aktif(self, admin):
        r = admin.get(f"{API}/orders?status=aktif")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "aktif"
        assert "counts" in body and set(body["counts"].keys()) >= {"aktif", "selesai", "cancelled"}
        for o in body["orders"]:
            assert not o.get("cancelled")
            assert o.get("current_stage", 0) < 15
            assert not o.get("hold")

    def test_list_cancelled(self, admin):
        r = admin.get(f"{API}/orders?status=cancelled")
        assert r.status_code == 200
        for o in r.json()["orders"]:
            assert o.get("cancelled") is True

    def test_list_selesai(self, admin):
        r = admin.get(f"{API}/orders?status=selesai")
        assert r.status_code == 200
        for o in r.json()["orders"]:
            assert not o.get("cancelled")
            assert o.get("current_stage", 0) >= 15

    def test_list_hold(self, admin):
        r = admin.get(f"{API}/orders?status=hold")
        assert r.status_code == 200
        for o in r.json()["orders"]:
            assert o.get("hold") is True
            assert not o.get("cancelled")


# ------------------------------------------------------------------- CANCEL
class TestCancelPO:
    def test_cancel_flow_and_guard(self, admin, designer):
        order = _make_po(admin)
        oid = order["id"]
        po_num = order["po_number"]

        # Designer without po.cancel → 403
        r = designer.post(f"{API}/orders/{oid}/cancel", json={"reason": "should fail"})
        assert r.status_code == 403, r.text

        # Empty reason → 400
        r = admin.post(f"{API}/orders/{oid}/cancel", json={"reason": "   "})
        assert r.status_code == 400
        assert "wajib" in r.text.lower()

        # Success
        r = admin.post(f"{API}/orders/{oid}/cancel", json={"reason": "Test cancel via QA"})
        assert r.status_code == 200, r.text
        cancelled_order = r.json()["order"]
        assert cancelled_order["cancelled"] is True
        assert cancelled_order["cancel_reason"] == "Test cancel via QA"
        assert cancelled_order["status_label"] == "Cancelled"
        assert any("Cancelled" in (t.get("note") or "") for t in cancelled_order.get("timeline", []))
        assert cancelled_order.get("cancelled_by") == ADMIN[0]
        assert cancelled_order.get("cancelled_at")

        # Double cancel → 400
        r = admin.post(f"{API}/orders/{oid}/cancel", json={"reason": "again"})
        assert r.status_code == 400
        assert "sudah" in r.text.lower()

        # Filter: appears in cancelled, not in aktif
        r = admin.get(f"{API}/orders?status=cancelled")
        assert any(o["id"] == oid for o in r.json()["orders"])
        r = admin.get(f"{API}/orders?status=aktif")
        assert not any(o["id"] == oid for o in r.json()["orders"])

        # GET still allowed (view for history)
        r = admin.get(f"{API}/orders/{oid}")
        assert r.status_code == 200
        assert r.json()["order"]["cancelled"] is True

        # Audit log
        r = admin.get(f"{API}/admin/audit-logs")
        assert r.status_code == 200
        logs = r.json().get("logs") or r.json().get("audit_logs") or r.json()
        if isinstance(logs, dict):
            logs = logs.get("logs", [])
        assert any(l.get("action") == "po.cancel" and po_num in (l.get("target_email") or "") for l in logs), \
            "po.cancel entry not found for this PO"

        # ------- GUARD: all workflow endpoints must 409 -------
        blocked = [
            ("POST", f"/orders/{oid}/advance", {}),
            ("POST", f"/orders/{oid}/approve-design", {"approved": True}),
            ("POST", f"/orders/{oid}/approve-payment", {"approved": True}),
            ("POST", f"/orders/{oid}/design/bypass", {"reason": "x"}),
            ("POST", f"/orders/{oid}/payment/bypass", {"reason": "x"}),
            ("POST", f"/orders/{oid}/rollback", {"to_stage": 0, "reason": "x"}),
            ("POST", f"/orders/{oid}/helper-prep", {"qty_required": 10, "qty_prepared": 5}),
            ("POST", f"/orders/{oid}/screener-info", {"screen_qty": 3}),
            ("POST", f"/orders/{oid}/note", {"note": "x"}),
            ("POST", f"/orders/{oid}/urgent", {"urgent": True}),
            ("POST", f"/orders/{oid}/packed", None),
            ("POST", f"/orders/{oid}/delivered", None),
        ]
        for method, path, body in blocked:
            r = admin.request(method, f"{API}{path}", json=body if body is not None else {})
            assert r.status_code == 409, f"{path} expected 409 got {r.status_code} {r.text[:120]}"
            assert "batalkan" in r.text.lower() or "cancel" in r.text.lower()

        # Verify unchanged
        r = admin.get(f"{API}/orders/{oid}")
        after = r.json()["order"]
        assert after["cancelled"] is True
        assert len(after.get("timeline", [])) == len(cancelled_order.get("timeline", []))


# ------------------------------------------------------------------- DELETE
class TestDeletePO:
    def test_delete_impact_and_delete(self, admin, designer):
        order = _make_po(admin)
        oid = order["id"]
        po_num = order["po_number"]

        # delete-impact permission
        r = designer.get(f"{API}/orders/{oid}/delete-impact")
        assert r.status_code == 403

        r = admin.get(f"{API}/orders/{oid}/delete-impact")
        assert r.status_code == 200, r.text
        imp = r.json()
        assert imp["po_number"] == po_num
        assert "impact" in imp
        assert "line_items" in imp["impact"]

        # DELETE without perm
        r = designer.delete(f"{API}/orders/{oid}", json={"reason": "x", "confirm": po_num})
        assert r.status_code == 403

        # Missing reason
        r = admin.delete(f"{API}/orders/{oid}", json={"reason": "  ", "confirm": po_num})
        assert r.status_code == 400

        # Wrong confirm
        r = admin.delete(f"{API}/orders/{oid}", json={"reason": "qa", "confirm": "SALAH"})
        assert r.status_code == 400
        detail = r.json().get("detail", {})
        if isinstance(detail, dict):
            assert detail.get("expected") == po_num

        # Correct via PO number
        r = admin.delete(f"{API}/orders/{oid}", json={"reason": "QA delete", "confirm": po_num})
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True

        # Gone
        r = admin.get(f"{API}/orders/{oid}")
        assert r.status_code == 404

        # Audit log has snapshot
        r = admin.get(f"{API}/admin/audit-logs")
        logs = r.json().get("logs", r.json())
        found = [l for l in logs if l.get("action") == "po.delete" and po_num in (l.get("target_email") or "")]
        assert found, "po.delete audit entry missing"
        before_snapshot = (found[0].get("before") or {})
        assert before_snapshot.get("po_number") == po_num
        assert "impact" in before_snapshot

    def test_delete_via_hapus_confirm(self, admin):
        order = _make_po(admin)
        oid = order["id"]
        r = admin.delete(f"{API}/orders/{oid}", json={"reason": "qa via HAPUS", "confirm": "HAPUS"})
        assert r.status_code == 200, r.text
        assert r.json()["deleted"] is True


# --------------------------------------------------------------- RE-IMPORT
class TestReimportBehaviour:
    def test_reimport_after_delete_allowed(self, admin):
        order = _make_po(admin)
        oid = order["id"]
        po_num = order["po_number"]
        # delete permanent
        r = admin.delete(f"{API}/orders/{oid}", json={"reason": "for reimport", "confirm": "HAPUS"})
        assert r.status_code == 200
        # re-import same po_number
        preview = {
            "po_number": po_num,
            "accurate_id": None,
            "customer": "TEST_QA reimport",
            "recipient": "QA",
            "address": "-",
            "order_date": "2026-01-01",
            "estimated_delivery": "2026-01-30",
            "deadline": "2026-01-30",
            "line_items": [{"item_code": "R-1", "name": "Reimport Item", "quantity": 10, "unit": "pcs"}],
            "total_quantity": 10,
            "item_count": 1,
        }
        r = admin.post(f"{API}/import/file/confirm", json={"preview": preview, "source": "file"})
        assert r.status_code == 200, f"reimport after delete should succeed: {r.status_code} {r.text[:200]}"

    def test_reimport_blocked_while_cancelled(self, admin):
        order = _make_po(admin)
        oid = order["id"]
        po_num = order["po_number"]
        r = admin.post(f"{API}/orders/{oid}/cancel", json={"reason": "block reimport"})
        assert r.status_code == 200
        # try re-import same po_number → must be duplicate/blocked
        preview = {
            "po_number": po_num,
            "accurate_id": None,
            "customer": "dup",
            "order_date": "2026-01-01",
            "estimated_delivery": "2026-01-30",
            "deadline": "2026-01-30",
            "line_items": [{"item_code": "D-1", "name": "Dup", "quantity": 1, "unit": "pcs"}],
        }
        r = admin.post(f"{API}/import/file/confirm", json={"preview": preview, "source": "file"})
        assert r.status_code == 409, f"cancelled PO must still block duplicate reimport, got {r.status_code} {r.text[:200]}"


# ------------------------------------------------------------------ BULK
class TestBulk:
    def test_bulk_cancel(self, admin, designer):
        o1 = _make_po(admin)
        o2 = _make_po(admin)
        o3 = _make_po(admin)
        # already-cancel o3 first, so bulk should skip it
        admin.post(f"{API}/orders/{o3['id']}/cancel", json={"reason": "pre-cancel"})

        # designer forbidden
        r = designer.post(f"{API}/orders-bulk/cancel", json={"ids": [o1["id"]], "reason": "x"})
        assert r.status_code == 403

        # empty ids
        r = admin.post(f"{API}/orders-bulk/cancel", json={"ids": [], "reason": "x"})
        assert r.status_code == 400

        # missing reason
        r = admin.post(f"{API}/orders-bulk/cancel", json={"ids": [o1["id"]], "reason": ""})
        assert r.status_code == 400

        # success
        r = admin.post(f"{API}/orders-bulk/cancel",
                       json={"ids": [o1["id"], o2["id"], o3["id"]], "reason": "QA bulk"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cancelled_count"] == 2  # o3 already cancelled — skipped
        assert set(body["po_numbers"]) == {o1["po_number"], o2["po_number"]}

    def test_bulk_delete(self, admin, designer):
        o1 = _make_po(admin)
        o2 = _make_po(admin)

        r = designer.post(f"{API}/orders-bulk/delete",
                          json={"ids": [o1["id"]], "reason": "x", "confirm": "HAPUS"})
        assert r.status_code == 403

        r = admin.post(f"{API}/orders-bulk/delete",
                       json={"ids": [o1["id"]], "reason": "x", "confirm": "wrong"})
        assert r.status_code == 400

        r = admin.post(f"{API}/orders-bulk/delete",
                       json={"ids": [o1["id"]], "reason": "", "confirm": "HAPUS"})
        assert r.status_code == 400

        r = admin.post(f"{API}/orders-bulk/delete",
                       json={"ids": [o1["id"], o2["id"]], "reason": "QA bulk del", "confirm": "HAPUS"})
        assert r.status_code == 200, r.text
        assert r.json()["deleted_count"] == 2

        # both gone
        assert admin.get(f"{API}/orders/{o1['id']}").status_code == 404
        assert admin.get(f"{API}/orders/{o2['id']}").status_code == 404

        # audit
        r = admin.get(f"{API}/admin/audit-logs")
        logs = r.json().get("logs", r.json())
        assert any(l.get("action") == "po.bulk_delete" for l in logs)


# --------------------------------------------------------- PHASE A REGRESSION
class TestPhaseARegression:
    def test_workflow_still_works(self, admin):
        order = _make_po(admin)
        oid = order["id"]

        # advance 0 → 1
        r = admin.post(f"{API}/orders/{oid}/advance", json={})
        assert r.status_code == 200, r.text
        assert r.json()["order"]["current_stage"] == 1

        # advance 1 → 2
        r = admin.post(f"{API}/orders/{oid}/advance", json={})
        assert r.status_code == 200
        assert r.json()["order"]["current_stage"] == 2

        # cannot advance past 2 without design approval
        r = admin.post(f"{API}/orders/{oid}/advance", json={})
        assert r.status_code == 400
        assert "design" in r.text.lower()

        # upload design
        tiny_png = ("data:image/png;base64,"
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
        r = admin.post(f"{API}/orders/{oid}/attachment",
                       json={"kind": "design", "name": "d.png", "data_url": tiny_png})
        assert r.status_code == 200, r.text

        # approve design
        r = admin.post(f"{API}/orders/{oid}/approve-design", json={"approved": True, "note": "ok"})
        assert r.status_code == 200
        assert r.json()["order"]["design_status"] == "Approved"
        assert r.json()["order"]["current_stage"] >= 3

        # HOLD at stage 4 without payment
        # first advance to 4
        r = admin.post(f"{API}/orders/{oid}/advance", json={})  # 3→4
        assert r.status_code == 200
        assert r.json()["order"]["current_stage"] == 4
        r = admin.post(f"{API}/orders/{oid}/advance", json={})  # 4→5 blocked
        assert r.status_code == 400
        assert "payment" in r.text.lower() or "hold" in r.text.lower()

        # rollback 4 → 1
        r = admin.post(f"{API}/orders/{oid}/rollback",
                       json={"to_stage": 1, "reason": "QA rollback"})
        assert r.status_code == 200, r.text
        rb = r.json()["order"]
        assert rb["current_stage"] == 1
        assert rb.get("rollback_history") and len(rb["rollback_history"]) >= 1

        # helper-prep
        r = admin.post(f"{API}/orders/{oid}/helper-prep",
                       json={"qty_required": 20, "qty_prepared": 15})
        assert r.status_code == 200
        assert r.json()["order"]["helper_prep"]["qty_shortage"] == 5

        # screener-info
        r = admin.post(f"{API}/orders/{oid}/screener-info", json={"screen_qty": 4})
        assert r.status_code == 200
        assert r.json()["order"]["screener_info"]["screen_qty"] == 4

        # cleanup — delete this test PO permanently
        admin.delete(f"{API}/orders/{oid}",
                     json={"reason": "cleanup", "confirm": "HAPUS"})
