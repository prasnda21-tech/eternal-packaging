"""Iteration 20 — Payment permission separation (upload / approve / bypass) tests.

Scope (from review_request TEST 1c, 3-6):
- Verify default role permissions from /auth/me
- Direct API 403 for user w/o payment.upload
- Backend RBAC gating for approve, bypass, upload separation
- Re-upload after approved resets payment_status
"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://print-ops-hub-12.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_CRED = ("admin@eternalpackaging.com", "Eternal123!")
PROD_CRED = ("production@eternalpackaging.com", "Eternal123!")
DESIGNER_CRED = ("designer@eternalpackaging.com", "Eternal123!")

# 1x1 transparent PNG
DUMMY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
DATA_URL = f"data:image/png;base64,{DUMMY_PNG_B64}"

QA_PASSWORD = "Eternal123!"
QA_ROLES = {
    "qa_upload_only":   ["po.view", "payment.upload", "payment.view", "schedule.view"],
    "qa_approve_only":  ["po.view", "payment.approve", "payment.view", "schedule.view"],
    "qa_bypass_only":   ["po.view", "payment.bypass_approval", "payment.view", "schedule.view"],
}
QA_USERS = {
    "qa_upload_only":  "qa.uploader@example.com",
    "qa_approve_only": "qa.approver@example.com",
    "qa_bypass_only":  "qa.bypasser@example.com",
}


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed {email}: {r.status_code} {r.text[:200]}"
    return s


# ---------------- Setup: create QA roles + users ----------------
@pytest.fixture(scope="module")
def admin_sess():
    return _login(*ADMIN_CRED)


@pytest.fixture(scope="module")
def qa_env(admin_sess):
    """Create QA roles + users idempotently. Yields dict with sessions + created ids."""
    created_roles, created_users = [], []
    # Ensure roles exist
    for slug, perms in QA_ROLES.items():
        r = admin_sess.get(f"{API}/admin/roles", timeout=15)
        assert r.status_code == 200
        existing = {x["role"] for x in r.json().get("roles", [])}
        if slug not in existing:
            r2 = admin_sess.post(f"{API}/admin/roles", json={
                "role": slug, "label": slug, "description": "QA test role", "permissions": perms,
            }, timeout=15)
            assert r2.status_code == 200, f"role create {slug}: {r2.status_code} {r2.text[:200]}"
            created_roles.append(slug)
        else:
            # sync permissions
            r3 = admin_sess.put(f"{API}/admin/roles/{slug}/permissions", json={"permissions": perms}, timeout=15)
            assert r3.status_code == 200

    # Ensure users exist
    users_by_role = {}
    r = admin_sess.get(f"{API}/admin/users", timeout=15)
    assert r.status_code == 200
    existing_users_body = r.json()
    existing_users = existing_users_body if isinstance(existing_users_body, list) else existing_users_body.get("users", [])
    existing_emails = {u["email"]: u for u in existing_users}
    for slug, email in QA_USERS.items():
        if email in existing_emails:
            uid = existing_emails[email]["id"]
            # ensure role
            admin_sess.put(f"{API}/admin/users/{uid}", json={"role": slug}, timeout=15)
            # ensure active + password reset (in case previous run had stale pw)
            admin_sess.post(f"{API}/admin/users/{uid}/activate", timeout=15)
            admin_sess.post(f"{API}/admin/users/{uid}/reset-password", json={"new_password": QA_PASSWORD}, timeout=15)
            users_by_role[slug] = uid
        else:
            r2 = admin_sess.post(f"{API}/admin/users", json={
                "email": email, "name": f"QA {slug}", "role": slug, "password": QA_PASSWORD,
            }, timeout=15)
            assert r2.status_code == 200, f"create user {email}: {r2.status_code} {r2.text[:200]}"
            uid = r2.json()["user"]["id"]
            users_by_role[slug] = uid
            created_users.append(uid)

    # login sessions
    sess = {slug: _login(QA_USERS[slug], QA_PASSWORD) for slug in QA_ROLES}
    yield {"sess": sess, "user_ids": users_by_role, "created_roles": created_roles, "created_users": created_users}

    # ----- Cleanup -----
    for uid in list(users_by_role.values()):
        try:
            admin_sess.post(f"{API}/admin/users/{uid}/deactivate", timeout=15)
        except Exception:
            pass
    for slug in list(QA_ROLES.keys()):
        try:
            admin_sess.delete(f"{API}/admin/roles/{slug}", timeout=15)
        except Exception:
            pass
    # role qa_settings_role handled in its own test cleanup


# ---------------- Helpers ----------------
def _find_orders(admin_sess):
    r = admin_sess.get(f"{API}/orders", timeout=20)
    assert r.status_code == 200
    body = r.json()
    return body if isinstance(body, list) else body.get("orders", [])


def _get_order(admin_sess, oid):
    r = admin_sess.get(f"{API}/orders/{oid}", timeout=15)
    assert r.status_code == 200, r.text[:200]
    return r.json()


def _pick_order_with_payment_files(admin_sess):
    for o in _find_orders(admin_sess):
        if o.get("status") == "Cancelled":
            continue
        det = _get_order(admin_sess, o["id"])
        # /orders/{id} may return {order: ...} or the object directly
        d = det.get("order", det) if isinstance(det, dict) else det
        if d.get("payment_files"):
            return d
    return None


def _pick_order_without_payment_files(admin_sess):
    for o in _find_orders(admin_sess):
        if o.get("status") == "Cancelled":
            continue
        det = _get_order(admin_sess, o["id"])
        d = det.get("order", det) if isinstance(det, dict) else det
        if not d.get("payment_files"):
            return d
    return None


def _upload_payment(sess, oid, name="test_bukti.png"):
    return sess.post(f"{API}/orders/{oid}/attachment", json={
        "kind": "payment", "name": name, "data_url": DATA_URL,
    }, timeout=30)


# ---------------- Auth /me default perms ----------------
def test_admin_has_all_payment_perms(admin_sess):
    r = admin_sess.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    perms = r.json().get("permissions", [])
    for p in ("payment.upload", "payment.approve", "payment.bypass_approval"):
        assert p in perms, f"admin missing {p}"


def test_head_production_no_upload_but_approve_bypass():
    s = _login(*PROD_CRED)
    r = s.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    perms = r.json().get("permissions", [])
    assert "payment.upload" not in perms, "head_production should NOT have payment.upload"
    assert "payment.approve" in perms
    assert "payment.bypass_approval" in perms


def test_designer_no_payment_perms():
    s = _login(*DESIGNER_CRED)
    r = s.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    perms = r.json().get("permissions", [])
    for p in ("payment.upload", "payment.approve", "payment.bypass_approval"):
        assert p not in perms, f"designer should NOT have {p}"


# ---------------- TEST 1c — designer 403 on payment upload ----------------
def test_designer_upload_payment_403(admin_sess):
    order = _pick_order_without_payment_files(admin_sess) or _pick_order_with_payment_files(admin_sess)
    assert order, "no active PO found"
    s = _login(*DESIGNER_CRED)
    r = _upload_payment(s, order["id"])
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text[:200]}"
    assert "payment.upload" in r.text, f"error should mention payment.upload; got: {r.text[:300]}"


# ---------------- TEST 3a — qa_upload_only cannot approve ----------------
def test_qa_upload_only_cannot_approve(qa_env, admin_sess):
    order = _pick_order_with_payment_files(admin_sess) or _pick_order_without_payment_files(admin_sess)
    assert order
    s = qa_env["sess"]["qa_upload_only"]
    r = s.post(f"{API}/orders/{order['id']}/approve-payment", json={"approved": True}, timeout=15)
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text[:200]}"


def test_qa_upload_only_can_upload(qa_env, admin_sess):
    order = _pick_order_without_payment_files(admin_sess) or _pick_order_with_payment_files(admin_sess)
    assert order
    s = qa_env["sess"]["qa_upload_only"]
    r = _upload_payment(s, order["id"], name="uploader_bukti.png")
    assert r.status_code == 200, f"upload should succeed: {r.status_code} {r.text[:200]}"


# ---------------- TEST 3b — qa_approve_only cannot upload, can approve ----------------
def test_qa_approve_only_cannot_upload(qa_env, admin_sess):
    order = _pick_order_with_payment_files(admin_sess) or _pick_order_without_payment_files(admin_sess)
    assert order
    s = qa_env["sess"]["qa_approve_only"]
    r = _upload_payment(s, order["id"])
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text[:200]}"


def test_qa_approve_only_can_approve(qa_env, admin_sess):
    # Ensure there's an order with payment_files (create if needed via admin upload)
    order = _pick_order_with_payment_files(admin_sess)
    if not order:
        order = _pick_order_without_payment_files(admin_sess)
        assert order
        r = _upload_payment(admin_sess, order["id"], name="admin_seed_bukti.png")
        assert r.status_code == 200
        order = _get_order(admin_sess, order["id"])
        order = order.get("order", order)
    s = qa_env["sess"]["qa_approve_only"]
    r = s.post(f"{API}/orders/{order['id']}/approve-payment", json={"approved": True}, timeout=15)
    assert r.status_code == 200, f"approve failed: {r.status_code} {r.text[:200]}"
    # Verify persistence
    det = _get_order(admin_sess, order["id"])
    d = det.get("order", det)
    assert d.get("payment_status") == "Approved"


# ---------------- TEST 4a — approve requires evidence (400) ----------------
def test_approve_without_evidence_returns_400(qa_env, admin_sess):
    order = _pick_order_without_payment_files(admin_sess)
    assert order, "need a PO without payment_files"
    s = qa_env["sess"]["qa_approve_only"]
    r = s.post(f"{API}/orders/{order['id']}/approve-payment", json={"approved": True}, timeout=15)
    assert r.status_code == 400, f"expected 400 got {r.status_code}: {r.text[:200]}"
    body_text = r.text.lower()
    assert "bukti" in body_text or "payment" in body_text, f"unexpected error msg: {r.text[:200]}"


# ---------------- TEST 4b — upload then approve succeeds ----------------
def test_upload_then_approve(qa_env, admin_sess):
    order = _pick_order_without_payment_files(admin_sess)
    if not order:
        pytest.skip("no PO without payment_files to test 4b")
    # Upload via qa_upload_only
    up = qa_env["sess"]["qa_upload_only"]
    r_up = _upload_payment(up, order["id"], name="test4b.png")
    assert r_up.status_code == 200, f"upload: {r_up.text[:200]}"
    # Approve via qa_approve_only
    ap = qa_env["sess"]["qa_approve_only"]
    r_ap = ap.post(f"{API}/orders/{order['id']}/approve-payment", json={"approved": True}, timeout=15)
    assert r_ap.status_code == 200, f"approve: {r_ap.text[:200]}"


# ---------------- TEST 5 — Re-upload after approved resets ----------------
def test_reupload_after_approved_resets(qa_env, admin_sess):
    # find/prepare an Approved order
    order = None
    for o in _find_orders(admin_sess):
        if o.get("payment_status") == "Approved" and o.get("status") != "Cancelled":
            det = _get_order(admin_sess, o["id"])
            order = det.get("order", det)
            if order.get("payment_files"):
                break
            order = None
    if not order:
        pytest.skip("no Approved order found")
    hist_before = len(order.get("payment_approval_history", []))
    # Re-upload as qa_upload_only
    up = qa_env["sess"]["qa_upload_only"]
    r = _upload_payment(up, order["id"], name="reupload_bukti.png")
    assert r.status_code == 200, f"re-upload: {r.text[:200]}"
    # Verify state
    det = _get_order(admin_sess, order["id"])
    d = det.get("order", det)
    assert d.get("payment_status") == "Waiting", f"expected Waiting, got {d.get('payment_status')}"
    assert d.get("payment_approved_by") in (None, ""), f"payment_approved_by should reset"
    hist = d.get("payment_approval_history", [])
    assert len(hist) > hist_before, "history should have new entry"
    assert any(h.get("action") == "reset-on-new-upload" for h in hist), \
        f"missing reset-on-new-upload entry; history={hist}"


# ---------------- TEST 6a — qa_bypass_only ----------------
def test_qa_bypass_only_cannot_approve_can_bypass(qa_env, admin_sess):
    # find a PO not already approved / bypassed
    order = None
    for o in _find_orders(admin_sess):
        if o.get("status") == "Cancelled":
            continue
        det = _get_order(admin_sess, o["id"])
        d = det.get("order", det)
        if not d.get("payment_bypassed") and d.get("payment_status") != "Approved":
            order = d
            break
    if not order:
        # fall back to any non-cancelled order (bypass endpoint may still work)
        for o in _find_orders(admin_sess):
            if o.get("status") != "Cancelled":
                det = _get_order(admin_sess, o["id"])
                order = det.get("order", det)
                break
    assert order, "no candidate order"
    s = qa_env["sess"]["qa_bypass_only"]
    # cannot approve
    r_ap = s.post(f"{API}/orders/{order['id']}/approve-payment", json={"approved": True}, timeout=15)
    assert r_ap.status_code == 403
    # can bypass
    r_bp = s.post(f"{API}/orders/{order['id']}/payment/bypass",
                  json={"reason": "QA bypass test"}, timeout=15)
    assert r_bp.status_code == 200, f"bypass: {r_bp.status_code} {r_bp.text[:200]}"
    det = _get_order(admin_sess, order["id"])
    d = det.get("order", det)
    assert d.get("payment_bypassed") is True
    # bypass should NOT set payment_status='Approved' by itself
    # (unless PO was previously approved). Just record what actually happens.
    # Assert only the flag.


# ---------------- TEST 7 — Settings/catalog: payment.upload key present ----------------
def test_permission_catalog_has_payment_upload(admin_sess):
    r = admin_sess.get(f"{API}/admin/permissions/catalog", timeout=15)
    assert r.status_code == 200
    body = r.json()
    all_keys = set(body.get("all_keys", []))
    assert "payment.upload" in all_keys
    assert "payment.approve" in all_keys
    assert "payment.bypass_approval" in all_keys
    # verify grouping under Payment module
    modules = body.get("modules", {})
    pay_keys = {e["key"] for e in modules.get("Payment", [])}
    assert "payment.upload" in pay_keys, f"payment.upload not in Payment module; got {pay_keys}"


def test_create_role_with_payment_upload_persists(admin_sess):
    slug = "qa_settings_role"
    # cleanup pre-existing
    admin_sess.delete(f"{API}/admin/roles/{slug}", timeout=10)
    r = admin_sess.post(f"{API}/admin/roles", json={
        "role": slug, "label": "QA Settings Role", "description": "QA",
        "permissions": ["po.view", "payment.upload"],
    }, timeout=15)
    assert r.status_code == 200, r.text[:200]
    # Verify by listing role permissions
    r2 = admin_sess.get(f"{API}/admin/roles", timeout=15)
    assert r2.status_code == 200
    roles = r2.json().get("roles", [])
    match = [x for x in roles if x.get("role") == slug]
    assert match, f"role {slug} not returned in list"
    perms = match[0].get("permissions", []) or match[0].get("effective_permissions", [])
    assert "payment.upload" in perms, f"payment.upload missing from role perms: {perms}"
    # cleanup
    admin_sess.delete(f"{API}/admin/roles/{slug}", timeout=10)
