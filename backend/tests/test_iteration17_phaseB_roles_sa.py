"""Iteration 17: Phase B - Role Management + Super Admin.

Tests:
  - Bootstrap: admin@eternalpackaging.com is Super Admin, has all 33 permissions.
  - Role CRUD (list/create/update/deactivate/activate/delete) + validation + gating.
  - Super Admin grant/revoke/transfer + invariants.
  - Audit log entries for each action.
  - Light regression on Req 5 (cancel/delete).
"""
import os
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://print-ops-hub-12.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

PW = "Eternal123!"
ADMIN_EMAIL = "admin@eternalpackaging.com"
DESIGNER_EMAIL = "designer@eternalpackaging.com"
HEAD_EMAIL = "production@eternalpackaging.com"


def _login(email: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": PW}, timeout=20)
    assert r.status_code == 200, f"login {email} failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN_EMAIL)


@pytest.fixture(scope="module")
def designer():
    return _login(DESIGNER_EMAIL)


@pytest.fixture(scope="module")
def head():
    return _login(HEAD_EMAIL)


@pytest.fixture(scope="module", autouse=True)
def cleanup(admin):
    """Ensure test roles/users are removed at start and end."""
    def _purge():
        # Try to delete test roles (ignore errors)
        for slug in ("qa_test_role", "qa_used", "ui_qa_role"):
            admin.delete(f"{API}/admin/roles/{slug}")
        # Deactivate / cleanup test user if exists
        r = admin.get(f"{API}/admin/users")
        if r.status_code == 200:
            for u in r.json().get("users", []):
                if u.get("email", "").startswith("TEST_qa_used_user"):
                    admin.post(f"{API}/admin/users/{u['id']}/deactivate")
    _purge()
    yield
    _purge()


# ---------------- Bootstrap ----------------
class TestBootstrap:
    def test_admin_is_super_admin_and_has_all_perms(self, admin):
        r = admin.get(f"{API}/auth/me")
        assert r.status_code == 200
        me = r.json()
        assert me.get("email") == ADMIN_EMAIL
        assert me.get("is_super_admin") is True, f"admin should be SA, got: {me}"
        perms = set(me.get("permissions") or [])
        # Sanity: super_admin.manage + dashboard.production keys present
        for k in ("super_admin.manage", "dashboard.production.view", "dashboard.production.export", "permissions.manage"):
            assert k in perms, f"missing perm {k} in /auth/me for admin. got={sorted(perms)}"
        # 33 permissions total
        r2 = admin.get(f"{API}/admin/permissions/catalog")
        assert r2.status_code == 200
        all_keys = r2.json().get("all_keys") or []
        assert len(all_keys) == 33, f"expected 33 permissions, got {len(all_keys)}"
        assert set(all_keys).issubset(perms), "admin should have every catalog perm"


# ---------------- ROLES LIST ----------------
class TestRoleList:
    def test_list_roles_returns_all_builtins(self, admin):
        r = admin.get(f"{API}/admin/roles")
        assert r.status_code == 200
        roles = r.json().get("roles", [])
        by_slug = {x["role"]: x for x in roles}
        expected = {"admin", "head_production", "head_operations", "designer",
                    "screener", "helper", "operator", "driver", "crm"}
        assert expected.issubset(by_slug.keys()), f"missing builtins: {expected - by_slug.keys()}"
        for slug in expected:
            row = by_slug[slug]
            assert row["is_builtin"] is True
            assert row.get("status") == "active"
            for f in ("label", "description", "permissions", "user_count",
                      "is_customized", "builtin_permissions"):
                assert f in row, f"role {slug} missing field {f}"
        # user_count for admin >= 1 (there is admin@)
        assert by_slug["admin"]["user_count"] >= 1


# ---------------- CREATE ROLE ----------------
class TestRoleCreate:
    def test_create_role_ok(self, admin):
        # Cleanup pre
        admin.delete(f"{API}/admin/roles/qa_test_role")
        payload = {
            "role": "qa_test_role",
            "label": "QA Test",
            "description": "from test",
            "permissions": ["po.view", "schedule.view"],
        }
        r = admin.post(f"{API}/admin/roles", json=payload)
        assert r.status_code == 200, r.text
        # verify list
        r2 = admin.get(f"{API}/admin/roles")
        found = [x for x in r2.json().get("roles", []) if x["role"] == "qa_test_role"]
        assert found, "role not in list"
        row = found[0]
        assert row["is_builtin"] is False
        assert row["user_count"] == 0
        assert set(row["permissions"]) == {"po.view", "schedule.view"}
        # audit
        a = admin.get(f"{API}/admin/audit-logs", params={"action": "role.create"})
        assert a.status_code == 200
        assert any(x.get("action") == "role.create" and "qa_test_role" in str(x)
                   for x in a.json().get("logs", a.json().get("entries", []) if isinstance(a.json(), dict) else []))

    def test_create_role_invalid_slug(self, admin):
        for bad in ("QA-Role", "1abc"):
            r = admin.post(f"{API}/admin/roles",
                           json={"role": bad, "label": "x", "description": "", "permissions": []})
            assert r.status_code == 400, f"expected 400 for slug='{bad}', got {r.status_code}"

    def test_create_role_duplicate(self, admin):
        # qa_test_role already exists from previous test
        r = admin.post(f"{API}/admin/roles",
                       json={"role": "qa_test_role", "label": "dup", "description": "", "permissions": []})
        assert r.status_code == 409, r.text

    def test_create_role_builtin_conflict(self, admin):
        r = admin.post(f"{API}/admin/roles",
                       json={"role": "admin", "label": "x", "description": "", "permissions": []})
        assert r.status_code == 409, r.text

    def test_create_role_unknown_permission(self, admin):
        r = admin.post(f"{API}/admin/roles",
                       json={"role": "qa_bogus_perm", "label": "x", "description": "",
                             "permissions": ["some.bogus.perm"]})
        assert r.status_code == 400, r.text
        # cleanup if somehow created
        admin.delete(f"{API}/admin/roles/qa_bogus_perm")


# ---------------- UPDATE ROLE ----------------
class TestRoleUpdate:
    def test_patch_role(self, admin):
        r = admin.patch(f"{API}/admin/roles/qa_test_role",
                        json={"label": "QA Test Updated",
                              "description": "updated",
                              "permissions": ["po.view"]})
        assert r.status_code == 200, r.text
        # verify
        r2 = admin.get(f"{API}/admin/roles")
        row = next(x for x in r2.json()["roles"] if x["role"] == "qa_test_role")
        assert row["label"] == "QA Test Updated"
        assert row["description"] == "updated"
        assert set(row["permissions"]) == {"po.view"}


# ---------------- DEACTIVATE/ACTIVATE ----------------
class TestRoleToggle:
    def test_deactivate(self, admin):
        r = admin.post(f"{API}/admin/roles/qa_test_role/deactivate")
        assert r.status_code == 200, r.text
        r2 = admin.get(f"{API}/admin/roles")
        row = next(x for x in r2.json()["roles"] if x["role"] == "qa_test_role")
        assert row["status"] == "inactive"

    def test_activate(self, admin):
        r = admin.post(f"{API}/admin/roles/qa_test_role/activate")
        assert r.status_code == 200, r.text
        r2 = admin.get(f"{API}/admin/roles")
        row = next(x for x in r2.json()["roles"] if x["role"] == "qa_test_role")
        assert row["status"] == "active"


# ---------------- DELETE ROLE ----------------
class TestRoleDelete:
    def test_delete_empty_ok(self, admin):
        r = admin.delete(f"{API}/admin/roles/qa_test_role")
        assert r.status_code == 200, r.text
        assert r.json().get("deleted") is True
        r2 = admin.get(f"{API}/admin/roles")
        assert not any(x["role"] == "qa_test_role" for x in r2.json()["roles"])

    def test_delete_in_use(self, admin):
        # Create qa_used role
        admin.delete(f"{API}/admin/roles/qa_used")
        r = admin.post(f"{API}/admin/roles",
                       json={"role": "qa_used", "label": "QA Used",
                             "description": "", "permissions": []})
        assert r.status_code == 200, r.text
        # BUG: POST /admin/users only accepts built-in roles (validates against
        # ROLE_DEFAULT_PERMISSIONS). So we can't create a user with a custom role
        # via API. Insert directly into Mongo to verify the delete-in-use guard.
        import asyncio, os as _os, uuid as _uuid
        from motor.motor_asyncio import AsyncIOMotorClient
        async def _seed():
            c = AsyncIOMotorClient(_os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
            db = c[_os.environ.get("DB_NAME", "test_database")]
            uid = str(_uuid.uuid4())
            await db.users.insert_one({
                "id": uid, "email": "TEST_qa_used_user@example.com",
                "name": "TEST QA Used", "role": "qa_used", "status": "active",
                "employee_id": "TEST-QA1", "permissions_override": {},
                "password_hash": "x",
            })
            return uid
        loop = asyncio.new_event_loop()
        try:
            user_id = loop.run_until_complete(_seed())
        finally:
            loop.close()
        try:
            r3 = admin.delete(f"{API}/admin/roles/qa_used")
            assert r3.status_code == 400, f"expected 400 in-use, got {r3.status_code} {r3.text}"
            body = r3.json()
            detail = body.get("detail", body)
            msg = str(detail).lower()
            assert "digunakan" in msg or "1 karyawan" in msg
            # Detail should include user_count
            if isinstance(detail, dict):
                assert detail.get("user_count") == 1
        finally:
            async def _cleanup():
                c = AsyncIOMotorClient(_os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
                db = c[_os.environ.get("DB_NAME", "test_database")]
                await db.users.delete_one({"id": user_id})
            loop2 = asyncio.new_event_loop()
            try:
                loop2.run_until_complete(_cleanup())
            finally:
                loop2.close()
            admin.delete(f"{API}/admin/roles/qa_used")

    def test_delete_builtin_forbidden(self, admin):
        r = admin.delete(f"{API}/admin/roles/designer")
        assert r.status_code == 400, r.text
        assert "built-in" in r.text.lower() or "builtin" in r.text.lower()


# ---------------- PERMISSION GATING ----------------
class TestRoleGating:
    def test_head_cannot_manage_roles(self, head):
        # head_production has users.view but NOT permissions.manage
        r1 = head.post(f"{API}/admin/roles",
                       json={"role": "qa_forbid", "label": "x", "description": "", "permissions": []})
        assert r1.status_code == 403, f"expected 403 create, got {r1.status_code}"
        r2 = head.patch(f"{API}/admin/roles/designer",
                        json={"label": "hack"})
        assert r2.status_code == 403
        r3 = head.post(f"{API}/admin/roles/designer/deactivate")
        assert r3.status_code == 403
        r4 = head.post(f"{API}/admin/roles/designer/activate")
        assert r4.status_code == 403
        r5 = head.delete(f"{API}/admin/roles/designer")
        assert r5.status_code == 403


# ---------------- SUPER ADMIN ----------------
class TestSuperAdmin:
    def _find(self, admin_s, email):
        r = admin_s.get(f"{API}/admin/users")
        for u in r.json().get("users", []):
            if u.get("email") == email:
                return u
        return None

    def test_grant_revoke_designer(self, admin):
        d = self._find(admin, DESIGNER_EMAIL)
        assert d, "designer not found"
        # Grant
        r = admin.post(f"{API}/admin/users/{d['id']}/super-admin/grant")
        assert r.status_code == 200, r.text
        after = self._find(admin, DESIGNER_EMAIL)
        assert after.get("is_super_admin") is True
        # Revoke (admin still SA -> allowed)
        r2 = admin.post(f"{API}/admin/users/{d['id']}/super-admin/revoke")
        assert r2.status_code == 200, r2.text
        after2 = self._find(admin, DESIGNER_EMAIL)
        assert after2.get("is_super_admin") is False
        # Audit
        a = admin.get(f"{API}/admin/audit-logs", params={"action": "super_admin.grant"})
        assert a.status_code == 200

    def test_revoke_last_super_admin_forbidden(self, admin):
        adm = self._find(admin, ADMIN_EMAIL)
        assert adm and adm.get("is_super_admin")
        r = admin.post(f"{API}/admin/users/{adm['id']}/super-admin/revoke")
        assert r.status_code == 400, f"expected 400 last-SA, got {r.status_code} {r.text}"
        assert "terakhir" in r.text.lower() or "last" in r.text.lower()

    def test_transfer_and_back(self, admin):
        d = self._find(admin, DESIGNER_EMAIL)
        adm = self._find(admin, ADMIN_EMAIL)
        assert d and adm
        # Transfer admin -> designer
        r = admin.post(f"{API}/admin/users/{adm['id']}/super-admin/transfer",
                       json={"to_user_id": d["id"], "reason": "test"})
        assert r.status_code == 200, r.text
        # Verify state
        adm_after = self._find(admin, ADMIN_EMAIL)
        d_after = self._find(admin, DESIGNER_EMAIL)
        assert adm_after.get("is_super_admin") is False
        assert d_after.get("is_super_admin") is True
        # admin /auth/me now not SA
        me = admin.get(f"{API}/auth/me").json()
        assert me.get("is_super_admin") is False
        # Login as designer, /me should be SA
        ds = _login(DESIGNER_EMAIL)
        me_d = ds.get(f"{API}/auth/me").json()
        assert me_d.get("is_super_admin") is True
        # Transfer back designer -> admin (as designer since only designer is SA now)
        r2 = ds.post(f"{API}/admin/users/{d['id']}/super-admin/transfer",
                     json={"to_user_id": adm['id'], "reason": "restore"})
        assert r2.status_code == 200, r2.text
        # Verify restored
        # Need fresh admin session because prior admin still had SA=False in DB → re-login
        adm_s2 = _login(ADMIN_EMAIL)
        me2 = adm_s2.get(f"{API}/auth/me").json()
        assert me2.get("is_super_admin") is True, f"admin SA not restored: {me2}"

    def test_non_sa_cannot_manage_sa(self, admin):
        # Ensure designer is not SA (should already have been restored)
        d = self._find(admin, DESIGNER_EMAIL)
        # Login as designer (non-SA, non-admin)
        ds = _login(DESIGNER_EMAIL)
        adm = self._find(admin, ADMIN_EMAIL)
        r = ds.post(f"{API}/admin/users/{adm['id']}/super-admin/revoke")
        assert r.status_code == 403, r.text
        r2 = ds.post(f"{API}/admin/users/{d['id']}/super-admin/grant")
        assert r2.status_code == 403


# ---------------- AUDIT ----------------
class TestAudit:
    def test_audit_actions_present(self, admin):
        r = admin.get(f"{API}/admin/audit-logs")
        assert r.status_code == 200
        payload = r.json()
        entries = payload.get("logs") or payload.get("entries") or payload
        if isinstance(entries, dict):
            entries = entries.get("items", [])
        actions = {e.get("action") for e in entries if isinstance(e, dict)}
        for a in ("role.create", "role.update", "role.deactivate", "role.activate",
                  "role.delete", "super_admin.grant", "super_admin.revoke", "super_admin.transfer"):
            assert a in actions, f"audit action '{a}' not found (found={sorted(x for x in actions if x)})"


# ---------------- REGRESSION Req 5 ----------------
class TestReq5Regression:
    def _real_order_id(self, admin):
        r = admin.get(f"{API}/orders")
        arr = r.json() if isinstance(r.json(), list) else r.json().get("orders", [])
        return arr[0]["id"] if arr else None

    def test_cancel_designer_forbidden(self, admin, designer):
        oid = self._real_order_id(admin)
        if not oid:
            pytest.skip("no orders present")
        r = designer.post(f"{API}/orders/{oid}/cancel", json={"reason": "x"})
        assert r.status_code == 403, f"expected 403 designer cancel, got {r.status_code}: {r.text}"

    def test_delete_designer_forbidden(self, admin, designer):
        oid = self._real_order_id(admin)
        if not oid:
            pytest.skip("no orders present")
        r = designer.delete(f"{API}/orders/{oid}", json={"reason": "x", "confirm": "HAPUS"})
        assert r.status_code == 403, f"expected 403 designer delete, got {r.status_code}: {r.text}"

    def test_bulk_endpoints_reachable(self, admin):
        # bulk-cancel with empty ids should 400 (validation) or 200 with 0
        r = admin.post(f"{API}/orders-bulk/cancel", json={"ids": [], "reason": "x"})
        assert r.status_code in (200, 400, 422), f"bulk-cancel got {r.status_code}"
