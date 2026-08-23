"""Iteration 9 — Granular RBAC (users, roles, permissions, audit) end-to-end tests.

Covers:
- /auth/me permissions payload for admin & helper roles
- /api/admin/permissions/catalog access control
- /api/admin/users CRUD (create/update/deactivate/activate) + login gate on inactive
- Per-user permissions_override affecting /orders/{id}/advance
- /api/admin/roles/{role}/permissions transient update with restore
- /api/admin/audit-logs entries
- Non-admin 403 on admin endpoints
"""
import os
import copy
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://print-ops-hub-12.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
PW = "Eternal123!"

ADMIN = "admin@eternalpackaging.com"
HELPER = "helper@eternalpackaging.com"
DRIVER = "driver@eternalpackaging.com"
PRODUCTION = "production@eternalpackaging.com"

TEST_EMAIL = "test.persiapan@eternalpackaging.com"


def _login(email, password=PW):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    return s, r


@pytest.fixture(scope="module")
def admin_session():
    s, r = _login(ADMIN)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def helper_session():
    s, r = _login(HELPER)
    assert r.status_code == 200
    return s


# ---------------- auth/me permissions ----------------
def test_admin_me_has_expected_permissions(admin_session):
    r = admin_session.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    data = r.json()
    perms = set(data.get("permissions") or [])
    for p in ["users.manage", "permissions.manage", "audit.view", "packing.update", "helper.prepare"]:
        assert p in perms, f"admin missing {p}; got {sorted(perms)}"


def test_helper_me_has_helper_and_packing(helper_session):
    r = helper_session.get(f"{API}/auth/me", timeout=15)
    assert r.status_code == 200
    perms = set(r.json().get("permissions") or [])
    assert "helper.prepare" in perms
    assert "packing.update" in perms


# ---------------- catalog / roles ----------------
def test_permissions_catalog_admin(admin_session):
    r = admin_session.get(f"{API}/admin/permissions/catalog", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert "modules" in body and "all_keys" in body
    assert "users.manage" in body["all_keys"]


def test_helper_forbidden_on_admin_users(helper_session):
    r = helper_session.get(f"{API}/admin/users", timeout=15)
    assert r.status_code == 403


def test_admin_lists_users(admin_session):
    r = admin_session.get(f"{API}/admin/users", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    emails = {u["email"] for u in body["users"]}
    assert ADMIN in emails


def test_non_admin_403_on_admin_endpoints():
    s, r = _login(DRIVER)
    assert r.status_code == 200
    for path in ["/admin/users", "/admin/audit-logs", "/admin/permissions/catalog"]:
        rr = s.get(f"{API}{path}", timeout=15)
        assert rr.status_code == 403, f"{path} expected 403 got {rr.status_code}"


# ---------------- create/update/deactivate/activate flow ----------------
@pytest.fixture(scope="module")
def created_test_user(admin_session):
    # Clean up any leftover from a previous run
    existing = admin_session.get(f"{API}/admin/users", timeout=15).json()["users"]
    prior = next((u for u in existing if u["email"] == TEST_EMAIL), None)
    if prior:
        # deactivate is fine; we'll reuse by updating override & activating
        pass
    payload = {
        "email": TEST_EMAIL,
        "name": "Helper Test Persiapan",
        "role": "helper",
        "password": PW,
        "permissions_override": {"packing.update": False},
    }
    r = admin_session.post(f"{API}/admin/users", json=payload, timeout=15)
    if r.status_code == 409:
        # Reuse existing — update override & ensure active
        uid = prior["id"]
        admin_session.put(f"{API}/admin/users/{uid}", json={"permissions_override": {"packing.update": False}}, timeout=15)
        admin_session.post(f"{API}/admin/users/{uid}/activate", timeout=15)
        # reset password to be safe
        admin_session.post(f"{API}/admin/users/{uid}/reset-password", json={"new_password": PW}, timeout=15)
        u = admin_session.get(f"{API}/admin/users/{uid}", timeout=15).json()["user"]
        return u
    assert r.status_code == 200, f"create failed {r.status_code} {r.text}"
    u = r.json()["user"]
    assert u["email"] == TEST_EMAIL
    assert u.get("status") == "active"
    assert u.get("permissions_override", {}).get("packing.update") is False
    return u


def test_created_user_login_and_me(created_test_user):
    s, r = _login(TEST_EMAIL)
    assert r.status_code == 200
    perms = set(s.get(f"{API}/auth/me", timeout=15).json()["permissions"])
    assert "helper.prepare" in perms
    assert "packing.update" not in perms


def test_audit_log_has_create_entry(admin_session, created_test_user):
    r = admin_session.get(f"{API}/admin/audit-logs", params={"limit": 200}, timeout=15)
    assert r.status_code == 200
    actions = [(l["action"], l.get("target_email")) for l in r.json()["logs"]]
    assert any(a == "user.create" and t == TEST_EMAIL for a, t in actions) or \
           any(a == "user.update" and t == TEST_EMAIL for a, t in actions)


def test_update_switches_override(admin_session, created_test_user):
    uid = created_test_user["id"]
    r = admin_session.put(
        f"{API}/admin/users/{uid}",
        json={"permissions_override": {"helper.prepare": False, "packing.update": True}},
        timeout=15,
    )
    assert r.status_code == 200
    # Log in as that user and verify /auth/me
    s, lr = _login(TEST_EMAIL)
    assert lr.status_code == 200
    perms = set(s.get(f"{API}/auth/me", timeout=15).json()["permissions"])
    assert "packing.update" in perms
    assert "helper.prepare" not in perms


def _find_order_at_stage(session, stage_idx):
    r = session.get(f"{API}/orders", timeout=15)
    assert r.status_code == 200
    for o in r.json()["orders"]:
        if o.get("current_stage") == stage_idx:
            return o
    return None


def test_advance_permission_enforced_persiapan_then_packing(admin_session):
    """When user override grants packing.update but denies helper.prepare:
       - advancing an order at stage 7 (helper.prepare) => 403
       - advancing an order at stage 11 (packing.update) => 200 (if we can find one)
    """
    s, lr = _login(TEST_EMAIL)
    assert lr.status_code == 200

    o7 = _find_order_at_stage(admin_session, 7)
    if o7:
        r = s.post(f"{API}/orders/{o7['id']}/advance", json={"note": "test"}, timeout=15)
        assert r.status_code == 403, f"expected 403 at stage 7, got {r.status_code} {r.text}"
    else:
        pytest.skip("no order at stage 7 to check helper.prepare denial")

    o11 = _find_order_at_stage(admin_session, 11)
    if o11:
        r = s.post(f"{API}/orders/{o11['id']}/advance", json={"note": "test packing"}, timeout=15)
        # Might succeed (200) — packing.update is granted via override
        assert r.status_code == 200, f"expected 200 at stage 11, got {r.status_code} {r.text}"


def test_deactivate_blocks_login(admin_session, created_test_user):
    uid = created_test_user["id"]
    r = admin_session.post(f"{API}/admin/users/{uid}/deactivate", timeout=15)
    assert r.status_code == 200
    assert r.json()["user"]["status"] == "inactive"
    # Attempt login
    _, lr = _login(TEST_EMAIL)
    assert lr.status_code == 403
    detail = (lr.json().get("detail") or "").lower()
    assert "nonaktif" in detail, f"unexpected: {detail}"


def test_activate_restores_login(admin_session, created_test_user):
    uid = created_test_user["id"]
    r = admin_session.post(f"{API}/admin/users/{uid}/activate", timeout=15)
    assert r.status_code == 200
    _, lr = _login(TEST_EMAIL)
    assert lr.status_code == 200


def test_audit_log_has_activity(admin_session, created_test_user):
    r = admin_session.get(f"{API}/admin/audit-logs", params={"limit": 300}, timeout=15)
    assert r.status_code == 200
    actions = {l["action"] for l in r.json()["logs"] if l.get("target_email") == TEST_EMAIL}
    assert "user.deactivate" in actions
    assert "user.activate" in actions
    assert "user.update" in actions


# ---------------- role permissions update (transient) ----------------
def test_role_permissions_update_and_restore(admin_session):
    # Grab current helper role permissions from /admin/roles
    r = admin_session.get(f"{API}/admin/roles", timeout=15)
    assert r.status_code == 200
    roles = {x["role"]: x for x in r.json()["roles"]}
    helper = roles["helper"]
    original = list(helper["builtin_permissions"])
    # Modified set: drop packing.update
    modified = [p for p in original if p != "packing.update"]
    r = admin_session.put(
        f"{API}/admin/roles/helper/permissions",
        json={"permissions": modified},
        timeout=15,
    )
    assert r.status_code == 200
    r2 = admin_session.get(f"{API}/admin/roles", timeout=15)
    helper2 = {x["role"]: x for x in r2.json()["roles"]}["helper"]
    assert helper2["is_customized"] is True
    assert "packing.update" not in helper2["permissions"]
    # Restore
    r = admin_session.put(
        f"{API}/admin/roles/helper/permissions",
        json={"permissions": original},
        timeout=15,
    )
    assert r.status_code == 200


def test_audit_role_permissions_update_entry(admin_session):
    r = admin_session.get(f"{API}/admin/audit-logs", params={"limit": 200, "action": "role.permissions.update"}, timeout=15)
    assert r.status_code == 200
    assert r.json()["count"] >= 1


# ---------------- regression: existing flows still work ----------------
def test_regression_orders_list(admin_session):
    r = admin_session.get(f"{API}/orders", timeout=15)
    assert r.status_code == 200
    assert "orders" in r.json()


def test_regression_trial_logins_all_ok():
    for e in [ADMIN, PRODUCTION, HELPER, DRIVER, "designer@eternalpackaging.com",
              "screener@eternalpackaging.com", "operator@eternalpackaging.com",
              "crm@eternalpackaging.com"]:
        _, r = _login(e)
        assert r.status_code == 200, f"{e} login failed: {r.status_code} {r.text}"


def test_production_has_users_view_and_audit_view():
    s, r = _login(PRODUCTION)
    assert r.status_code == 200
    perms = set(s.get(f"{API}/auth/me", timeout=15).json()["permissions"])
    assert "users.view" in perms
    assert "audit.view" in perms
    assert "permissions.manage" not in perms  # confirms roles tab hidden
    # Can call /admin/users (read)
    assert s.get(f"{API}/admin/users", timeout=15).status_code == 200
    # Cannot create user
    r = s.post(f"{API}/admin/users", json={"email": "shouldfail@x.com", "name": "x", "role": "helper"}, timeout=15)
    assert r.status_code == 403


# ---------------- teardown: leave test user deactivated per instructions ----------------
def test_zzz_teardown_deactivate_test_user(admin_session):
    users = admin_session.get(f"{API}/admin/users", timeout=15).json()["users"]
    u = next((x for x in users if x["email"] == TEST_EMAIL), None)
    if u and u.get("status") == "active":
        r = admin_session.post(f"{API}/admin/users/{u['id']}/deactivate", timeout=15)
        assert r.status_code == 200
