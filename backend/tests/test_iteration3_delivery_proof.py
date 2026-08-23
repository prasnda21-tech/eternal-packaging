"""Iteration 3 tests: delivery-proof upload (object storage), delete,
delivered gating, /files/{path} download+auth, and analytics/operators."""
import os
import base64
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
    "operator": "operator@eternalpackaging.com",
    "driver": "driver@eternalpackaging.com",
    "crm": "crm@eternalpackaging.com",
    "screener": "screener@eternalpackaging.com",
}

# Tiny 1x1 red JPEG (valid, PIL-generated)
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDi6KKK+ZP3E//Z"
)


def _login(email):
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.text}"
    return s


@pytest.fixture(scope="module")
def sessions():
    return {k: _login(v) for k, v in EMAILS.items()}


@pytest.fixture(scope="module")
def po_ready(sessions):
    """Return a PO advanced to stage >= 12 (Surat Jalan) via prod."""
    orders = sessions["prod"].get(f"{BASE_URL}/api/orders").json()["orders"]
    # Prefer a PO that's not yet delivered
    candidates = [o for o in orders if o["current_stage"] < 15]
    # Try PO-1047 (further from delivered so we can test gating cleanly),
    # else pick any active
    target = next((o for o in candidates if o["po_number"] == "PO-1047"), None) or candidates[0]
    oid = target["id"]
    # Ensure room (<5 proofs) — iteration 4 introduced a 5-cap; clear all proofs
    # so subsequent upload/type/size tests are not blocked by prior test state.
    full = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
    for p in list(full.get("delivery_proofs", [])):
        sessions["admin"].delete(f"{BASE_URL}/api/orders/{oid}/delivery-proof/{p['id']}", timeout=15)
    # Advance until stage >= 12
    for _ in range(20):
        r = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
        if r["current_stage"] >= 12:
            break
        # Auto-approve if needed
        if r.get("design_status") != "Approved":
            sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/approve-design", json={"approved": True})
        if r.get("payment_status") != "Approved":
            sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/approve-payment", json={"approved": True})
        sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={})
    final = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
    assert final["current_stage"] >= 12, f"could not advance PO to 12+, got {final['current_stage']}"
    return final


class TestDeliveryProofUpload:
    def test_upload_by_driver_success(self, sessions, po_ready):
        oid = po_ready["id"]
        files = {"file": ("proof1.jpg", TINY_JPEG, "image/jpeg")}
        r = sessions["driver"].post(
            f"{BASE_URL}/api/orders/{oid}/delivery-proof",
            files=files, data={"note": "sampai di outlet"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        proof = body["proof"]
        assert proof["storage_path"].startswith("eternal-packaging/delivery-proof/")
        assert proof["uploaded_by"] == EMAILS["driver"]
        assert proof["uploaded_by_role"] == "driver"
        assert proof["uploaded_by_name"]
        assert proof["uploaded_at"]
        assert proof["size"] > 0
        assert proof["note"] == "sampai di outlet"

        # Verify persisted in order and timeline
        order = sessions["driver"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
        assert any(p["id"] == proof["id"] for p in order["delivery_proofs"])
        assert any("Upload bukti kirim" in (t.get("note") or "") for t in order["timeline"])
        # Save for downstream tests
        pytest.proof_ref = proof
        pytest.oid = oid

    def test_non_image_rejected(self, sessions, po_ready):
        oid = po_ready["id"]
        files = {"file": ("hello.txt", b"hello world", "text/plain")}
        r = sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/delivery-proof", files=files, timeout=15)
        assert r.status_code == 400
        assert "gambar" in r.text.lower()

    def test_size_cap(self, sessions, po_ready):
        oid = po_ready["id"]
        big = b"\xff" * (6 * 1024 * 1024)
        files = {"file": ("big.jpg", big, "image/jpeg")}
        r = sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/delivery-proof", files=files, timeout=60)
        assert r.status_code == 400

    @pytest.mark.parametrize("role", ["operator", "screener", "helper", "crm", "designer"])
    def test_permission_denied_roles(self, sessions, po_ready, role):
        oid = po_ready["id"]
        files = {"file": ("p.jpg", TINY_JPEG, "image/jpeg")}
        r = sessions[role].post(f"{BASE_URL}/api/orders/{oid}/delivery-proof", files=files, timeout=15)
        assert r.status_code == 403, f"role={role} unexpectedly {r.status_code}"


class TestFileDownload:
    def test_get_file_auth_required(self):
        # Unauthenticated
        r = requests.get(f"{BASE_URL}/api/files/eternal-packaging/delivery-proof/anything.jpg", timeout=15)
        assert r.status_code == 401

    def test_get_file_returns_bytes(self, sessions):
        proof = getattr(pytest, "proof_ref", None)
        assert proof, "upload test must run first"
        r = sessions["driver"].get(f"{BASE_URL}/api/files/{proof['storage_path']}", timeout=30)
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("image/")
        assert len(r.content) > 0

    def test_get_nonexistent_file_404(self, sessions):
        r = sessions["admin"].get(f"{BASE_URL}/api/files/eternal-packaging/delivery-proof/does-not-exist/xyz.jpg", timeout=15)
        assert r.status_code == 404


class TestDeliveredGating:
    def test_driver_delivered_requires_proof(self, sessions, po_ready):
        # Pick a fresh PO advanced to 12+ that has NO proof yet
        orders = sessions["prod"].get(f"{BASE_URL}/api/orders").json()["orders"]
        target = None
        for o in orders:
            if o["id"] == po_ready["id"]:
                continue  # already has proof from earlier test
            if o["current_stage"] < 15 and not o.get("delivery_proofs"):
                target = o
                break
        if not target:
            # Advance PO-1048 or another
            target = next(o for o in orders if o["current_stage"] < 15 and o["id"] != po_ready["id"])
        oid = target["id"]
        # advance to stage 12+
        for _ in range(20):
            r = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
            if r["current_stage"] >= 12:
                break
            if r.get("design_status") != "Approved":
                sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/approve-design", json={"approved": True})
            if r.get("payment_status") != "Approved":
                sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/approve-payment", json={"approved": True})
            sessions["prod"].post(f"{BASE_URL}/api/orders/{oid}/advance", json={})
        # Ensure no proof on this PO
        cur = sessions["prod"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
        if cur.get("delivery_proofs"):
            pytest.skip("target already has proofs")
        # Driver without proof: 400
        r = sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/delivered", timeout=15)
        assert r.status_code == 400
        assert "foto" in r.text.lower() or "bukti" in r.text.lower()
        # Upload proof
        files = {"file": ("p.jpg", TINY_JPEG, "image/jpeg")}
        up = sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/delivery-proof", files=files, timeout=30)
        assert up.status_code == 200
        # Now delivered succeeds
        r = sessions["driver"].post(f"{BASE_URL}/api/orders/{oid}/delivered", timeout=15)
        assert r.status_code == 200
        assert r.json()["order"]["current_stage"] == 15


class TestDeliveryProofDelete:
    def test_operator_cannot_delete(self, sessions):
        proof = getattr(pytest, "proof_ref", None)
        oid = getattr(pytest, "oid", None)
        assert proof and oid
        r = sessions["operator"].delete(f"{BASE_URL}/api/orders/{oid}/delivery-proof/{proof['id']}", timeout=15)
        assert r.status_code == 403

    def test_admin_deletes_and_soft_delete_flag_set(self, sessions):
        proof = getattr(pytest, "proof_ref", None)
        oid = getattr(pytest, "oid", None)
        assert proof and oid
        r = sessions["admin"].delete(f"{BASE_URL}/api/orders/{oid}/delivery-proof/{proof['id']}", timeout=15)
        assert r.status_code == 200
        order = r.json()["order"]
        assert not any(p["id"] == proof["id"] for p in order.get("delivery_proofs", []))
        # After deletion, /files/{path} should now 404 (soft-deleted)
        r2 = sessions["admin"].get(f"{BASE_URL}/api/files/{proof['storage_path']}", timeout=15)
        assert r2.status_code == 404


class TestAnalyticsOperators:
    def test_hop_can_read(self, sessions):
        r = sessions["prod"].get(f"{BASE_URL}/api/analytics/operators", timeout=15)
        assert r.status_code == 200
        body = r.json()
        assert "operators" in body
        assert "note" in body
        assert "generated_at" in body
        assert isinstance(body["operators"], list)
        # If there's at least one operator, verify shape
        if body["operators"]:
            op = body["operators"][0]
            for k in ["email", "name", "qty_scheduled", "qty_completed", "qty_remaining",
                      "active_po_count", "completed_po_count", "all_time_po_count", "po_history"]:
                assert k in op, f"missing key {k} in operator record"
            if op["po_history"]:
                p = op["po_history"][0]
                for k in ["po_id", "po_number", "outlet", "quantity", "qty_completed",
                          "qty_remaining", "stage", "stage_label", "priority",
                          "assigned_at", "first_progress_at", "last_progress_at",
                          "split_updated_at", "po_created_at", "po_delivered_at", "po_current_stage"]:
                    assert k in p, f"missing key {k} in po_history entry"

    @pytest.mark.parametrize("role", ["operator", "driver", "crm", "designer", "helper", "screener"])
    def test_non_hop_denied(self, sessions, role):
        r = sessions[role].get(f"{BASE_URL}/api/analytics/operators", timeout=15)
        assert r.status_code == 403


class TestRegressionAttachment:
    def test_design_attachment_still_base64(self, sessions):
        orders = sessions["prod"].get(f"{BASE_URL}/api/orders").json()["orders"]
        oid = next(o["id"] for o in orders if o["current_stage"] < 15)
        data_url = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        r = sessions["designer"].post(f"{BASE_URL}/api/orders/{oid}/attachment", json={
            "name": "TEST_iter3_design.png", "data_url": data_url, "kind": "design"
        }, timeout=15)
        assert r.status_code == 200
        order = r.json()["order"]
        assert any(f["name"] == "TEST_iter3_design.png" and f["data_url"].startswith("data:image") for f in order["design_files"])
