"""Iteration 10 RBAC regression: Helper Persiapan/Packing override + disable/enable user.

Scope (strictly): no code changes to server.py, permissions.py, Settings.jsx, App.js, AppShell.jsx.
Only exercises existing admin + auth + orders endpoints via /api routes.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://print-ops-hub-12.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "admin@eternalpackaging.com"
ADMIN_PASS = "Eternal123!"
TEST_EMAIL = "ui.persiapan@eternalpackaging.com"
TEST_PASS = "Eternal123!"


def _login(session: requests.Session, email: str, password: str):
    r = session.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    return r


def _fresh_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_session():
    s = _fresh_session()
    r = _login(s, ADMIN_EMAIL, ADMIN_PASS)
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def test_user(admin_session):
    """Ensure ui.persiapan user exists with helper role + deny packing.update override."""
    # Look up if exists
    r = admin_session.get(f"{API}/admin/users", timeout=20)
    assert r.status_code == 200, r.text
    users = r.json()["users"]
    existing = next((u for u in users if u["email"] == TEST_EMAIL), None)

    if existing:
        # Reset it: role=helper, override={packing.update: False}, active, password
        upd = admin_session.put(
            f"{API}/admin/users/{existing['id']}",
            json={"role": "helper", "permissions_override": {"packing.update": False}},
            timeout=20,
        )
        assert upd.status_code == 200, upd.text
        # Activate if inactive
        if existing.get("status") != "active":
            act = admin_session.post(f"{API}/admin/users/{existing['id']}/activate", timeout=20)
            assert act.status_code == 200, act.text
        # Reset password
        pw = admin_session.post(
            f"{API}/admin/users/{existing['id']}/reset-password",
            json={"new_password": TEST_PASS},
            timeout=20,
        )
        assert pw.status_code == 200, pw.text
        user = upd.json()["user"]
    else:
        r = admin_session.post(
            f"{API}/admin/users",
            json={
                "email": TEST_EMAIL,
                "name": "UI Persiapan Helper",
                "role": "helper",
                "password": TEST_PASS,
                "permissions_override": {"packing.update": False},
            },
            timeout=20,
        )
        assert r.status_code == 200, r.text
        user = r.json()["user"]

    assert user["role"] == "helper"
    return user


def test_01_helper_login_and_perms_deny_packing(test_user):
    """helper role default includes packing.update. Override deny should remove it, keep helper.prepare."""
    s = _fresh_session()
    r = _login(s, TEST_EMAIL, TEST_PASS)
    assert r.status_code == 200, f"login failed: {r.text}"
    me = s.get(f"{API}/auth/me", timeout=20)
    assert me.status_code == 200, me.text
    perms = set(me.json()["permissions"])
    assert "helper.prepare" in perms, f"missing helper.prepare: {perms}"
    assert "packing.update" not in perms, f"packing.update should be denied: {perms}"


def _find_or_create_stage11_order(admin_session) -> str:
    """Return id of an order sitting at stage 11 (Packing). Advance one via admin if needed."""
    r = admin_session.get(f"{API}/orders", timeout=20)
    assert r.status_code == 200, r.text
    orders = r.json()["orders"]
    # Try existing at stage 11 first
    hit = next((o for o in orders if o.get("current_stage") == 11), None)
    if hit:
        return hit["id"]
    # Else find order with stage < 11 that can be advanced. Simplest: pick highest stage < 11 and push.
    # Skip stage 2 gating (needs design_status Approved) and stage 4 (payment Approved).
    candidates = [o for o in orders if 0 < o.get("current_stage", 0) < 11]
    candidates.sort(key=lambda o: -o.get("current_stage", 0))
    for cand in candidates:
        oid = cand["id"]
        # Approve design and payment to bypass guardrails at stages 2 and 4.
        admin_session.post(f"{API}/orders/{oid}/approve-design", json={"note": "iter10 setup"}, timeout=20)
        admin_session.post(f"{API}/orders/{oid}/approve-payment", json={"note": "iter10 setup"}, timeout=20)
        # Try repeatedly advancing until stage 11 or failure
        for _ in range(25):
            cur = admin_session.get(f"{API}/orders/{oid}", timeout=20).json()["order"]
            cs = cur["current_stage"]
            if cs == 11:
                return oid
            if cs >= 11:
                break
            resp = admin_session.post(
                f"{API}/orders/{oid}/advance",
                json={"note": "iter10-setup advance to stage 11"},
                timeout=20,
            )
            if resp.status_code != 200:
                break
        cur = admin_session.get(f"{API}/orders/{oid}", timeout=20).json()["order"]
        if cur["current_stage"] == 11:
            return oid
    pytest.skip("No advanceable order could be moved to stage 11.")


@pytest.fixture(scope="module")
def stage11_order_id(admin_session, test_user):
    return _find_or_create_stage11_order(admin_session)


def test_02_advance_stage11_denied_when_packing_denied(stage11_order_id):
    s = _fresh_session()
    r = _login(s, TEST_EMAIL, TEST_PASS)
    assert r.status_code == 200
    adv = s.post(
        f"{API}/orders/{stage11_order_id}/advance",
        json={"note": "should be blocked"},
        timeout=20,
    )
    assert adv.status_code == 403, f"expected 403, got {adv.status_code}: {adv.text}"
    assert "packing.update" in adv.text, f"error should mention packing.update, got: {adv.text}"


def test_03_swap_override_restore_packing_deny_prepare(admin_session, test_user):
    """Admin updates override: restore packing.update, deny helper.prepare."""
    r = admin_session.put(
        f"{API}/admin/users/{test_user['id']}",
        json={"permissions_override": {"helper.prepare": False}},
        timeout=20,
    )
    assert r.status_code == 200, r.text
    saved = r.json()["user"]
    assert saved["permissions_override"] == {"helper.prepare": False}


def test_04_helper_login_perms_after_swap():
    s = _fresh_session()
    r = _login(s, TEST_EMAIL, TEST_PASS)
    assert r.status_code == 200
    me = s.get(f"{API}/auth/me", timeout=20).json()
    perms = set(me["permissions"])
    assert "packing.update" in perms, f"packing.update should now be allowed: {perms}"
    assert "helper.prepare" not in perms, f"helper.prepare should be denied: {perms}"


def test_05_advance_stage11_allowed_after_swap(stage11_order_id):
    s = _fresh_session()
    r = _login(s, TEST_EMAIL, TEST_PASS)
    assert r.status_code == 200
    adv = s.post(
        f"{API}/orders/{stage11_order_id}/advance",
        json={"note": "iter10: packing advance allowed"},
        timeout=20,
    )
    assert adv.status_code == 200, f"expected 200, got {adv.status_code}: {adv.text}"
    order = adv.json()["order"]
    assert order["current_stage"] >= 12, f"stage should have advanced past 11: {order.get('current_stage')}"


def test_06_deactivate_blocks_login(admin_session, test_user):
    r = admin_session.post(f"{API}/admin/users/{test_user['id']}/deactivate", timeout=20)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["status"] == "inactive"

    s = _fresh_session()
    login = _login(s, TEST_EMAIL, TEST_PASS)
    assert login.status_code == 403, f"expected 403, got {login.status_code}: {login.text}"
    assert "nonaktif" in login.text.lower(), f"error should mention nonaktif: {login.text}"


def test_07_activate_restores_login(admin_session, test_user):
    r = admin_session.post(f"{API}/admin/users/{test_user['id']}/activate", timeout=20)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["status"] == "active"

    s = _fresh_session()
    login = _login(s, TEST_EMAIL, TEST_PASS)
    assert login.status_code == 200, f"login after activate failed: {login.status_code} {login.text}"


def test_08_teardown_deactivate(admin_session, test_user):
    r = admin_session.post(f"{API}/admin/users/{test_user['id']}/deactivate", timeout=20)
    assert r.status_code == 200
    assert r.json()["user"]["status"] == "inactive"
