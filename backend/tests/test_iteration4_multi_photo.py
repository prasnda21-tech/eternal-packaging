"""Iteration 4 tests: multi-foto Bukti Kirim.
Focus: backend 5-photo cap, single-file endpoint contract unchanged,
sequential N uploads → N proofs with distinct notes/paths, CRM still 403,
storage still object-storage (no base64 in mongo)."""
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
    "driver": "driver@eternalpackaging.com",
    "crm": "crm@eternalpackaging.com",
    "operator": "operator@eternalpackaging.com",
}

# Tiny 1x1 red JPEG
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


def _ensure_room(admin_sess, prod_sess, exclude_ids=()):
    """Return an order at stage>=12,<15 with <5 proofs; delete a proof if needed."""
    orders = prod_sess.get(f"{BASE_URL}/api/orders").json()["orders"]
    for o in orders:
        if o["current_stage"] >= 15 or o["id"] in exclude_ids:
            continue
        full = prod_sess.get(f"{BASE_URL}/api/orders/{o['id']}").json()["order"]
        if full["current_stage"] < 12:
            # advance to 12
            for _ in range(20):
                r = prod_sess.get(f"{BASE_URL}/api/orders/{full['id']}").json()["order"]
                if r["current_stage"] >= 12:
                    full = r
                    break
                if r.get("design_status") != "Approved":
                    prod_sess.post(f"{BASE_URL}/api/orders/{full['id']}/approve-design", json={"approved": True})
                if r.get("payment_status") != "Approved":
                    prod_sess.post(f"{BASE_URL}/api/orders/{full['id']}/approve-payment", json={"approved": True})
                prod_sess.post(f"{BASE_URL}/api/orders/{full['id']}/advance", json={})
        proofs = full.get("delivery_proofs", [])
        if len(proofs) >= 5:
            # free a slot as admin
            admin_sess.delete(f"{BASE_URL}/api/orders/{full['id']}/delivery-proof/{proofs[0]['id']}", timeout=15)
        return prod_sess.get(f"{BASE_URL}/api/orders/{full['id']}").json()["order"]
    raise AssertionError("no non-delivered PO available")


def _pick_and_advance(session, exclude_ids=(), require_no_proofs=False, require_room=False):
    """Pick a PO with stage <15, advance to 12+, return the order.
    NOTE: /orders list excludes delivery_proofs → fetch each candidate
    individually when we need proof-count filters."""
    orders = session.get(f"{BASE_URL}/api/orders").json()["orders"]
    base = [o for o in orders if o["current_stage"] < 15 and o["id"] not in exclude_ids]
    candidates = []
    if require_no_proofs or require_room:
        for o in base:
            full = session.get(f"{BASE_URL}/api/orders/{o['id']}").json()["order"]
            proofs = full.get("delivery_proofs", [])
            if require_no_proofs and proofs:
                continue
            if require_room and len(proofs) >= 5:
                continue
            candidates.append(o)
    else:
        candidates = base
    assert candidates, "no eligible PO found"
    # If caller required room but all candidates were skipped, retry without filter and free a slot
    
    oid = candidates[0]["id"]
    for _ in range(20):
        r = session.get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
        if r["current_stage"] >= 12:
            return r
        if r.get("design_status") != "Approved":
            session.post(f"{BASE_URL}/api/orders/{oid}/approve-design", json={"approved": True})
        if r.get("payment_status") != "Approved":
            session.post(f"{BASE_URL}/api/orders/{oid}/approve-payment", json={"approved": True})
        session.post(f"{BASE_URL}/api/orders/{oid}/advance", json={})
    final = session.get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
    assert final["current_stage"] >= 12
    return final


@pytest.fixture(scope="module")
def po_multi(sessions):
    return _pick_and_advance(sessions["prod"])


class TestIter4MultiPhoto:
    def test_upload_beyond_five_succeeds(self, sessions, po_multi):
        oid = po_multi["id"]
        existing = len(po_multi.get("delivery_proofs", []))
        # Fill up to 6 total — no business max on photo count
        to_upload = max(0, 6 - existing)
        proof_ids = []
        for i in range(to_upload):
            files = {"file": (f"proof_{i}.jpg", TINY_JPEG, "image/jpeg")}
            r = sessions["driver"].post(
                f"{BASE_URL}/api/orders/{oid}/delivery-proof",
                files=files, data={"note": f"foto ke-{i+1}"}, timeout=30,
            )
            assert r.status_code == 200, f"upload {i} failed: {r.text}"
            proof_ids.append(r.json()["proof"]["id"])

        order = sessions["driver"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
        assert len(order["delivery_proofs"]) >= 6, f"expected >=6 got {len(order['delivery_proofs'])}"

        # 7th still accepts (unlimited operational upload)
        files = {"file": ("proof_7.jpg", TINY_JPEG, "image/jpeg")}
        r = sessions["driver"].post(
            f"{BASE_URL}/api/orders/{oid}/delivery-proof",
            files=files, data={"note": "beyond five"}, timeout=15,
        )
        assert r.status_code == 200, r.text
        assert "maksimum" not in r.text.lower()
        pytest.iter4_oid = oid
        pytest.iter4_proof_ids = proof_ids

    def test_delete_one_then_upload_succeeds(self, sessions):
        oid = pytest.iter4_oid
        before = sessions["driver"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
        before_n = len(before.get("delivery_proofs") or [])
        # Admin deletes one
        first_id = pytest.iter4_proof_ids[0] if pytest.iter4_proof_ids else \
            before["delivery_proofs"][0]["id"]
        r = sessions["admin"].delete(f"{BASE_URL}/api/orders/{oid}/delivery-proof/{first_id}", timeout=15)
        assert r.status_code == 200
        assert len(r.json()["order"]["delivery_proofs"]) == before_n - 1

        # Now upload again — succeeds (append, no max refill gate)
        files = {"file": ("refill.jpg", TINY_JPEG, "image/jpeg")}
        r = sessions["driver"].post(
            f"{BASE_URL}/api/orders/{oid}/delivery-proof",
            files=files, data={"note": "refill after delete"}, timeout=30,
        )
        assert r.status_code == 200, r.text
        order = r.json()["order"]
        assert len(order["delivery_proofs"]) == before_n
        # Verify the new proof has the right note
        new_proof = order["delivery_proofs"][-1]
        assert new_proof["note"] == "refill after delete"

    # ---- Sequential upload contract (merged into same class to keep loadscope on one worker) ----

    def test_three_sequential_uploads_persist_distinctly(self, sessions):
        try:
            sessions["prod"].post(f"{BASE_URL}/api/accurate/sync", timeout=30)
        except Exception:
            pass
        try:
            po = _pick_and_advance(sessions["prod"], exclude_ids=[getattr(pytest, "iter4_oid", "")],
                                   require_no_proofs=True)
        except AssertionError:
            pytest.skip("No PO without proofs available in this environment run")
        oid = po["id"]
        notes = ["catatan_A", "catatan_B", "catatan_C"]
        proofs = []
        for i, note in enumerate(notes):
            files = {"file": (f"seq_{i}.jpg", TINY_JPEG, "image/jpeg")}
            r = sessions["driver"].post(
                f"{BASE_URL}/api/orders/{oid}/delivery-proof",
                files=files, data={"note": note}, timeout=30,
            )
            assert r.status_code == 200, r.text
            proofs.append(r.json()["proof"])
        ids = [p["id"] for p in proofs]
        paths = [p["storage_path"] for p in proofs]
        assert len(set(ids)) == 3
        assert len(set(paths)) == 3
        for i, p in enumerate(proofs):
            assert p["note"] == notes[i]
            assert p["storage_path"].startswith(f"eternal-packaging/delivery-proof/{po['po_number']}/")
        order = sessions["driver"].get(f"{BASE_URL}/api/orders/{oid}").json()["order"]
        assert len(order["delivery_proofs"]) == 3
        for i, p in enumerate(order["delivery_proofs"]):
            assert p["note"] == notes[i]
        for p in proofs:
            fr = sessions["driver"].get(f"{BASE_URL}/api/files/{p['storage_path']}", timeout=30)
            assert fr.status_code == 200
            assert fr.headers.get("content-type", "").startswith("image/")
            assert len(fr.content) > 0

    def test_single_photo_path_still_works(self, sessions):
        po = _ensure_room(sessions["admin"], sessions["prod"],
                          exclude_ids=[getattr(pytest, "iter4_oid", "")])
        oid = po["id"]
        files = {"file": ("only.jpg", TINY_JPEG, "image/jpeg")}
        r = sessions["driver"].post(
            f"{BASE_URL}/api/orders/{oid}/delivery-proof",
            files=files, data={"note": "single"}, timeout=30,
        )
        assert r.status_code == 200
        order = r.json()["order"]
        assert any(p["note"] == "single" for p in order["delivery_proofs"])

    # ---- Permissions unchanged ----

    def test_crm_cannot_upload(self, sessions, po_multi):
        oid = po_multi["id"]
        files = {"file": ("crm.jpg", TINY_JPEG, "image/jpeg")}
        r = sessions["crm"].post(f"{BASE_URL}/api/orders/{oid}/delivery-proof", files=files, timeout=15)
        assert r.status_code == 403

    def test_driver_cannot_delete_others_proof(self, sessions):
        po = _ensure_room(sessions["admin"], sessions["prod"],
                          exclude_ids=[getattr(pytest, "iter4_oid", "")])
        oid = po["id"]
        files = {"file": ("byadmin.jpg", TINY_JPEG, "image/jpeg")}
        r = sessions["admin"].post(
            f"{BASE_URL}/api/orders/{oid}/delivery-proof",
            files=files, data={"note": "admin uploaded"}, timeout=30,
        )
        assert r.status_code == 200
        pid = r.json()["proof"]["id"]
        rd = sessions["driver"].delete(f"{BASE_URL}/api/orders/{oid}/delivery-proof/{pid}", timeout=15)
        assert rd.status_code == 403

    # ---- No base64 in mongo ----

    def test_delivery_proofs_reference_storage_only(self, sessions):
        orders = sessions["admin"].get(f"{BASE_URL}/api/orders").json()["orders"]
        checked = 0
        for o in orders:
            full = sessions["admin"].get(f"{BASE_URL}/api/orders/{o['id']}").json()["order"]
            for p in full.get("delivery_proofs", []):
                assert "data_url" not in p
                assert p.get("storage_path", "").startswith("eternal-packaging/delivery-proof/")
                checked += 1
        assert checked > 0


